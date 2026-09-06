"""Roslyn completion via GtkSource's built-in framework (the wordcompletion way).

The bundled wordcompletion plugin builds no popup at all: it registers a
GtkSourceCompletionProvider and the editor's own completion window handles
focus, filtering, navigation and commit. This module does the same for
Roslyn items, which fixes the focus-stealing class of bugs structurally:
our code never creates, maps or focuses a window.

VSCode-parity notes (GtkSource4 constraints):

* ``do_populate`` never blocks: it returns the cached list immediately
  (or an empty unfinished list) and completes the in-flight request via
  ``context.add_proposals(..., True)`` when Roslyn answers.
* Trigger ownership belongs to the framework (interactive + user
  requested). The plugin must NOT prefetch on every keystroke; it only
  forces ``completion.start()`` on explicit ``Ctrl+Space``.
* Filtering while typing is done by the framework against each
  proposal's ``text`` (``filterText`` or label). The cache is reused
  while the user extends the same prefix (VSCode behaviour for complete
  lists) and re-queried after trigger characters or when the server
  reported ``isIncomplete``.
* Replacement uses the LSP ``textEdit`` range carried per proposal,
  not a generic word scan.

Custom CompletionPopup (completion.py) remains as a frozen fallback for
builds without GtkSource.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("GtkSource", "4")
    from gi.repository import GObject, GtkSource  # type: ignore

    _AVAILABLE = True
except Exception:  # headless unit tests / missing typelib
    GObject = None  # type: ignore
    GtkSource = None  # type: ignore
    _AVAILABLE = False

from . import intelligence as intel

#: GtkSource4 ``populate`` is synchronous, so this module never blocks it:
#: a fast local Roslyn usually answers in milliseconds and the async
#: completion lands while the popup is open. No main-loop wait here.
FETCH_TIMEOUT_S = 0.35  # legacy ``fetch_sync`` shim only; populate is async.

#: Cap proposals per populate so huge namespaces stay snappy; the
#: framework filters as the user types. Large enough to keep Roslyn's
#: relevance order intact (VSCode shows hundreds virtualized).
MAX_PROPOSALS = 300

#: How long a complete list may be reused while the user extends the same
#: prefix on the same line (framework filters locally, like VSCode).
CACHE_TTL_S = 10.0

#: Interactive delay (ms). VSCode feels instant; the previous 150ms felt
#: laggy. The framework still throttles while typing.
INTERACTIVE_DELAY_MS = 75

#: Characters that request a fresh member list (LSP TriggerCharacter).
TRIGGER_CHARACTERS = (".", "(", "<")


class _RoslynProposalBase:
    """Proposal logic; mixed into the GtkSource type when available."""

    def __init__(
        self,
        label: str,
        insert_text: str,
        info: str = "",
        filter_text: str = "",
        markup: str = "",
        icon_name: str = "",
        replace_start: int = 0,
        replace_end: int = 0,
        insert_start: int = 0,
        insert_end: int = 0,
        additional_edits: list | None = None,
        commit_chars: list | None = None,
        preselect: bool = False,
        kind: int = 0,
    ) -> None:
        # GtkSource filters on ``text`` and shows ``label``/``markup``.
        text = filter_text or label
        try:
            super().__init__(label=label, text=text, info=info or "")  # type: ignore[call-arg]
        except TypeError:
            # Fallback base (no GtkSource): plain object init.
            try:
                super().__init__()  # type: ignore[call-arg]
            except TypeError:
                pass
            self.label_text = label
            self.filter_text_value = text
            self.info_text = info or ""
        if markup:
            try:
                self.set_property("markup", markup)  # type: ignore[attr-defined]
            except Exception:
                pass
        if icon_name:
            try:
                self.set_property("icon-name", icon_name)  # type: ignore[attr-defined]
            except Exception:
                pass
        self.insert_text = insert_text
        self.filter_text = filter_text or label
        self.replace_start = replace_start
        self.replace_end = replace_end
        self.insert_start = insert_start
        self.insert_end = insert_end
        self.additional_edits = list(additional_edits or [])
        self.commit_chars = list(commit_chars or [])
        self.preselect = bool(preselect)
        self.kind = kind


if _AVAILABLE:
    # Mixin first: its __init__ must win MRO over the Gtk/GObject ones,
    # and its super().__init__() chains into them correctly.
    class RoslynProposal(_RoslynProposalBase, GtkSource.CompletionItem):  # type: ignore[misc]
        """A framework proposal carrying the exact text to insert."""
else:
    # Import-safe fallback: never instantiated without GtkSource, but the
    # module must load (a conditional base of `object` would crash with
    # "duplicate base class" if written inline in the class statement).
    class RoslynProposal(_RoslynProposalBase):  # type: ignore[no-redef]
        """A framework proposal carrying the exact text to insert."""


def _is_word_char(char: str) -> bool:
    return bool(char) and (char == "_" or char.isalnum())


def word_start_offset(text: str, offset: int) -> int:
    """Pure helper: start offset of the identifier ending at offset."""
    _prefix, start = intel.prefix_at(text, offset)
    return start


def _escape_markup(text: str) -> str:
    try:
        from gi.repository import GLib  # type: ignore

        return GLib.markup_escape_text(text or "")
    except Exception:
        return (text or "").replace("&", "&amp;").replace("<", "&lt;")


def proposal_markup(label: str, detail: str) -> str:
    """VSCode-like row: ``label`` + muted ``detail`` (framework aligns)."""
    if detail:
        return f"{_escape_markup(label)}  <span alpha=\"55%\" size=\"smaller\">{_escape_markup(detail.strip()[:120])}</span>"
    return _escape_markup(label)


def trigger_context(
    text: str, offset: int, user_requested: bool, is_retrigger: bool = False
) -> tuple[int, str | None]:
    """LSP completion context for a populate (pure, testable).

    Returns ``(triggerKind, triggerCharacter)``: 1=Invoked, 2=
    TriggerCharacter, 3=TriggerForIncompleteCompletions (VSCode
    re-query while the user extends an incomplete list).
    """
    if is_retrigger:
        return 3, None
    if user_requested:
        return 1, None
    if 0 < offset <= len(text):
        char = text[offset - 1]
        if char in TRIGGER_CHARACTERS:
            return 2, char
    return 1, None


@dataclass
class _CacheEntry:
    items: list = field(default_factory=list)
    is_incomplete: bool = False
    path: str = ""
    line: int = 0
    char: int = 0
    offset: int = 0
    prefix: str = ""
    time: float = 0.0


def cache_valid(
    entry: _CacheEntry | None,
    path: str,
    line: int,
    prefix: str,
    char: int | None = None,
    offset: int | None = None,
    now: float | None = None,
) -> bool:
    """Whether a cached list may back a populate without re-querying.

    VSCode reuses a *complete* list while the user extends the same
    prefix on the same line (local fuzzy filter). Anything else —
    line/column change, trigger character (prefix reset), expiry, or
    ``isIncomplete`` — needs a fresh request. ``char``/``offset`` are
    optional so older callers keep working; when given they must match
    the entry, otherwise a same-prefix edit elsewhere on the line would
    reuse a stale list.
    """
    if entry is None or not entry.items:
        return False
    if entry.path != path or entry.line != line:
        return False
    if char is not None and entry.char != char:
        return False
    if offset is not None and entry.offset != offset:
        return False
    if entry.is_incomplete:
        return False
    try:
        age = (time.monotonic() if now is None else now) - entry.time
    except Exception:
        age = 0.0
    if age > CACHE_TTL_S:
        return False
    # Extending the same prefix reuses; anything else re-queries.
    # Empty current prefix right after '.' must NOT reuse the previous
    # word list (member list differs).
    if prefix and entry.prefix and not prefix.startswith(entry.prefix):
        return False
    if not prefix and entry.prefix:
        return False
    return True


class _RoslynCompletionProviderBase:
    """Provider logic; mixed into the GObject/iface types when available."""

    __gsignals__ = {}  # type: ignore[misc]

    """GtkSource completion provider backed by Roslyn textDocument/completion.

    Callbacks (wired by the plugin, kept off GTK so they stay testable):
      is_ready() -> bool
      resolve_path(buffer) -> str | None (None for non-C# buffers)
      send_request(method, params, callback) -> id | None
      flush_doc(path) -> None (best-effort synchronous didChange flush)
    """

    def __init__(self, is_ready, resolve_path, send_request, flush_doc=None) -> None:
        super().__init__()
        self._is_ready = is_ready
        self._resolve_path = resolve_path
        self._send_request = send_request
        self._flush_doc = flush_doc
        self._lock = threading.Lock()
        self._cache: dict[str, _CacheEntry] = {}
        self._seq = 0
        self._match_warned: set = set()
        # Legacy compat (fetch_sync/note_response tests + prefetch path).
        self._legacy_items: list = []
        self._legacy_key = None
        self._legacy_time = 0.0

    # -- GtkSource interface -----------------------------------------
    def do_get_name(self):  # noqa: N802
        return "thor-csharp-roslyn"

    def do_get_activation(self):  # noqa: N802
        return (
            GtkSource.CompletionActivation.INTERACTIVE  # type: ignore[union-attr]
            | GtkSource.CompletionActivation.USER_REQUESTED  # type: ignore[union-attr]
        )

    def do_get_interactive_delay(self):  # noqa: N802
        return INTERACTIVE_DELAY_MS

    def do_get_priority(self):  # noqa: N802
        # Rank above the plain word-completion provider.
        return 10

    def _buffer_path(self, context):
        try:
            _ok, it = context.get_iter()
        except Exception:
            return None, None
        try:
            buf = it.get_buffer()
        except Exception:
            return None, None
        try:
            path = self._resolve_path(buf)
        except Exception:
            return None, None
        return buf, path

    def _is_user_requested(self, context) -> bool:
        try:
            activation = context.get_activation()
            return bool(activation & GtkSource.CompletionActivation.USER_REQUESTED)  # type: ignore[union-attr]
        except Exception:
            return False

    def _warn_once(self, key: str, message: str) -> None:
        if key in self._match_warned:
            return
        self._match_warned.add(key)
        logger.debug(message)

    def do_match(self, context):  # noqa: N802
        # VSCode shows completion while typing anywhere in C#; the
        # framework already throttles via the interactive delay, so stay
        # permissive here. Gating on the previous char (old behaviour)
        # suppressed the popup on first chars and after keywords.
        try:
            if not self._is_ready():
                self._warn_once("not-ready", "completion do_match: Roslyn not ready")
                return False
            _buf, path = self._buffer_path(context)
            if not path:
                self._warn_once("no-path", "completion do_match: buffer is not a C# path")
                return False
            self._match_warned.clear()
            return True
        except Exception:
            return False

    def _proposals_for(self, items: list) -> list:
        # VSCode highlights the preselected item; the framework selects
        # the first row, so the preselected item leads (stable). Sort the
        # FULL list first, then truncate: truncating first could drop the
        # preselected item (or better matches) past MAX_PROPOSALS.
        try:
            ordered = sorted(
                list(items),
                key=lambda it: (0 if bool(getattr(it, "preselect", False)) else 1),
            )[:MAX_PROPOSALS]
        except Exception:
            ordered = list(items[:MAX_PROPOSALS])
        out = []
        for item in ordered:
            try:
                out.append(
                    RoslynProposal(
                        label=item.label,
                        insert_text=item.insert_text or item.label,
                        info=intel.completion_info_text(item),
                        filter_text=intel.completion_filter_text(item),
                        markup=proposal_markup(item.label, item.detail),
                        icon_name=intel.completion_icon_name(item.kind),
                        replace_start=item.replace_start,
                        replace_end=item.replace_end,
                        insert_start=item.insert_start,
                        insert_end=item.insert_end,
                        additional_edits=item.additional_edits,
                        commit_chars=item.commit_chars,
                        preselect=item.preselect,
                        kind=item.kind,
                    )
                )
            except Exception as e:
                logger.debug(f"roslyn proposal build failed: {e!r}")
                continue
        return out

    def do_populate(self, context):  # noqa: N802
        # Never block the main loop (VSCode is fully async). Serve the
        # cache synchronously; complete the request on this same context
        # when Roslyn answers.
        try:
            _ok, it = context.get_iter()
            buf = it.get_buffer()
            path = self._resolve_path(buf)
            if not path or not self._is_ready():
                self._warn_once(
                    f"populate-{bool(path)}-{self._is_ready()}",
                    f"completion do_populate: path={path} ready={self._is_ready()}",
                )
                context.add_proposals(self, [], True)
                return
            text = self._buffer_text(buf)
            offset = it.get_offset()
            line, char = intel.offset_to_position(text, offset)
            prefix, _start = intel.prefix_at(text, offset)
            user_requested = self._is_user_requested(context)
            logger.debug(f"completion populate: path={path} line={line} char={char} "
                  f"prefix={prefix!r} user={user_requested}")
            with self._lock:
                entry = self._cache.get(path)
                hit = cache_valid(entry, path, line, prefix, char=char, offset=offset)
                items = list(entry.items) if (hit and entry is not None) else []
                retrigger = bool(entry is not None and entry.is_incomplete)
                self._seq += 1
                seq = self._seq
            if hit:
                proposals = self._proposals_for(items)
                logger.debug(f"completion populate: cache hit, {len(proposals)} proposals finished=True")
                context.add_proposals(self, proposals, True)
                if retrigger:
                    # Incomplete list: VSCode re-queries in the background
                    # while keeping the current rows visible.
                    logger.debug("completion populate: cache incomplete, re-querying")
                    self._request_async(
                        context, seq, path, line, char, text, offset,
                        user_requested, is_retrigger=True,
                    )
                return
            logger.debug(f"completion populate: cache miss (have_entry={entry is not None}), "
                  f"flushing doc and requesting")
            if self._flush_doc is not None:
                try:
                    self._flush_doc(path)
                except Exception as e:
                    logger.debug(f"completion populate: flush failed: {e!r}")
            # No usable cache: show nothing yet but keep the context open;
            # the async answer completes it (no freeze, no stale flash).
            context.add_proposals(self, [], False)
            self._request_async(
                context, seq, path, line, char, text, offset,
                user_requested, is_retrigger=retrigger,
            )
        except Exception as e:
            logger.debug(f"roslyn populate failed: {e!r}")
            try:
                context.add_proposals(self, [], True)
            except Exception:
                pass

    def _request_async(
        self, context, seq: int, path: str, line: int, char: int,
        text: str, offset: int, user_requested: bool, is_retrigger: bool = False,
    ) -> None:
        kind, trigger_char = trigger_context(text, offset, user_requested, is_retrigger)
        params = intel.position_params(path, line, char)
        params["context"] = {"triggerKind": kind, "triggerCharacter": trigger_char}
        prefix, _s = intel.prefix_at(text, offset)
        logger.debug(f"completion request: {path}:{line}:{char} triggerKind={kind} "
              f"triggerChar={trigger_char!r} prefix={prefix!r} retrigger={is_retrigger}")

        def _cb(message: dict) -> None:
            try:
                items = intel.parse_completion(message or {}, text, offset)
                items = intel.rank_for_prefix(items, prefix)
                incomplete = intel.completion_is_incomplete(message or {})
            except Exception:
                items, incomplete = [], False
            try:
                err = (message or {}).get("error")
                logger.debug(f"completion response: {len(items)} items incomplete={incomplete} "
                      f"error={err} path={path} line={line}")
            except Exception:
                pass
            with self._lock:
                if seq < self._seq and not is_retrigger:
                    # Superseded by a newer populate; keep cache fresh but
                    # don't touch the stale context.
                    self._cache[path] = _CacheEntry(
                        items=list(items), is_incomplete=incomplete,
                        path=path, line=line, char=char, offset=offset,
                        prefix=prefix, time=time.monotonic(),
                    )
                    logger.debug(f"completion response: superseded seq={seq}, cache updated, "
                          f"context untouched")
                    return
                self._cache[path] = _CacheEntry(
                    items=list(items), is_incomplete=incomplete,
                    path=path, line=line, char=char, offset=offset,
                    prefix=prefix, time=time.monotonic(),
                )
                # Legacy shim state for fetch_sync callers.
                self._legacy_items = list(items)
                self._legacy_key = (path, line)
                self._legacy_time = time.monotonic()
            # Complete the still-open context, unless the cursor has since
            # moved to another line (then the framework already populated
            # anew and this delivery would be stale).
            try:
                try:
                    _ok, cur = context.get_iter()
                    cur_text = self._buffer_text(cur.get_buffer())
                    cur_line, _c = intel.offset_to_position(cur_text, cur.get_offset())
                    if cur_line != line:
                        logger.debug(f"completion response: cursor moved to line {cur_line}, "
                              f"dropping delivery for line {line}")
                        return
                except Exception:
                    pass
                proposals = self._proposals_for(items)
                logger.debug(f"completion deliver: {len(proposals)} proposals finished=True")
                context.add_proposals(self, proposals, True)
            except Exception as e:
                logger.debug(f"roslyn async complete failed: {e!r}")

        try:
            request_id = self._send_request("textDocument/completion", params, _cb)
        except Exception as e:
            logger.debug(f"roslyn completion send failed: {e!r}")
            request_id = None
        if request_id is None:
            try:
                logger.error("completion request not sent: Roslyn server not running.")
            except Exception:
                pass
            try:
                context.add_proposals(self, [], True)
            except Exception:
                pass

    def _stored_range_start(self, proposal, text: str, cur: int) -> int | None:
        """Stored edit start when plausible, else None (word-scan fallback).

        Manually built proposals carry the (0, 0) default — never trust an
        empty range, or ``Console.Wri`` would collapse to the file start.
        """
        try:
            start_off = getattr(proposal, "replace_start", None)
            end_off = getattr(proposal, "replace_end", None)
            if not isinstance(start_off, int) or not isinstance(end_off, int):
                return None
            if end_off <= start_off:
                return None
            if 0 <= start_off <= cur <= len(text):
                return start_off
        except Exception:
            pass
        return None

    def do_get_start_iter(self, context, proposal):  # noqa: N802
        """Per-proposal start iter (VSCode textEdit-aware).

        Note the PyGObject shape: ``iter`` is an out-param in C, so this
        takes ``(context, proposal)`` and returns ``(ok, iter)``.
        """
        try:
            cur = None
            try:
                _ok, cur = context.get_iter()
            except Exception:
                cur = None
            if proposal is not None and cur is not None:
                try:
                    buf = cur.get_buffer()
                    text = self._buffer_text(buf)
                    cur_off = cur.get_offset()
                    start_off = self._stored_range_start(proposal, text, cur_off)
                    if start_off is None:
                        # Fall back to the insert range when it is set.
                        ins_s = getattr(proposal, "insert_start", None)
                        ins_e = getattr(proposal, "insert_end", None)
                        if (
                            isinstance(ins_s, int)
                            and isinstance(ins_e, int)
                            and ins_e > ins_s
                            and 0 <= ins_s <= cur_off <= len(text)
                        ):
                            between = text[ins_s:cur_off]
                            if not between or all(
                                c == "_" or c.isalnum() for c in between
                            ):
                                start_off = ins_s
                    if start_off is not None:
                        return True, buf.get_iter_at_offset(start_off)
                except Exception:
                    pass
            if cur is not None:
                start = cur.copy()
                while start.backward_char():
                    if not _is_word_char(start.get_char()):
                        start.forward_char()
                        break
                return True, start
        except Exception as e:
            logger.debug(f"roslyn start-iter failed: {e!r}")
        return False, None

    def do_activate_proposal(self, proposal, it):  # noqa: N802
        """Apply the LSP textEdit range exactly (VSCode semantics)."""
        try:
            insert = getattr(proposal, "insert_text", "") or ""
            if not insert:
                return False
            buf = it.get_buffer()
            end = it.copy()
            try:
                text = self._buffer_text(buf)
                cur = end.get_offset()
                start_off = self._stored_range_start(proposal, text, cur)
                if start_off is not None:
                    start = buf.get_iter_at_offset(start_off)
                else:
                    raise ValueError("no stored range")
            except Exception:
                start = end.copy()
                while start.backward_char():
                    if not _is_word_char(start.get_char()):
                        start.forward_char()
                        break
            buf.begin_user_action()
            try:
                buf.delete(start, end)
                buf.insert(start, insert)
                # Chain the next char (VSCode-style): `count` -> `count.`,
                # `WriteLine` -> `WriteLine(`. Skipped when already present
                # (snippet terminators, lookahead char) to avoid doubling.
                suffix = intel.completion_suffix(getattr(proposal, "kind", 0), insert)
                if suffix:
                    try:
                        ahead = start.copy()
                        ahead.forward_char()
                        if ahead.get_offset() > start.get_offset():
                            existing = buf.get_text(start, ahead, True)
                        else:
                            existing = ""
                    except Exception:
                        existing = ""
                    if existing != suffix:
                        try:
                            buf.insert(start, suffix)
                            if suffix == "(":
                                buf.insert(start, ")")
                                buf.place_cursor(
                                    buf.get_iter_at_offset(start.get_offset() - 1)
                                )
                        except Exception as e:
                            logger.debug(f"roslyn suffix insert failed: {e!r}")
                # Same-document additionalTextEdits (e.g. brace
                # adjustments). The main insert above shifted every offset
                # after it, so ranges are re-anchored against the CURRENT
                # buffer text and verified before applying: out-of-bounds
                # ranges, reversed ranges, and edits overlapping the
                # just-applied region are dropped with a debug log rather
                # than corrupting the buffer.
                try:
                    applied_start = start.get_offset()
                    applied_end = applied_start + len(insert)
                except Exception:
                    applied_start = applied_end = -1
                for edit in getattr(proposal, "additional_edits", None) or []:
                    try:
                        rng = (edit or {}).get("range") or {}
                        s = rng.get("start", {})
                        e = rng.get("end", {})
                        cur_text = self._buffer_text(buf)
                        try:
                            s_off = intel.position_to_offset(
                                cur_text, max(0, int(s.get("line", 0))),
                                max(0, int(s.get("character", 0))))
                            e_off = intel.position_to_offset(
                                cur_text, max(0, int(e.get("line", 0))),
                                max(0, int(e.get("character", 0))))
                        except (TypeError, ValueError) as conv_e:
                            logger.debug(f"roslyn additional edit dropped (bad range): {conv_e!r}")
                            continue
                        if not (0 <= s_off <= e_off <= len(cur_text)):
                            logger.debug(
                                "roslyn additional edit dropped "
                                f"(range {s_off}-{e_off} outside 0-{len(cur_text)})")
                            continue
                        if applied_start >= 0 and s_off < applied_end and e_off > applied_start:
                            logger.debug("roslyn additional edit dropped (overlaps main edit)")
                            continue
                        s_it = buf.get_iter_at_offset(s_off)
                        e_it = buf.get_iter_at_offset(e_off)
                        if e_it.compare(s_it) < 0:
                            continue
                        # Skip edits overlapping the just-applied region.
                        buf.delete(s_it, e_it)
                        buf.insert(
                            buf.get_iter_at_offset(s_off),
                            str((edit or {}).get("newText", "")),
                        )
                    except Exception as edit_e:
                        logger.debug(f"roslyn additional edit failed: {edit_e!r}")
                        continue
            finally:
                buf.end_user_action()
            return True
        except Exception as e:
            logger.debug(f"roslyn activate failed: {e!r}")
            return False

    # -- data ----------------------------------------------------------
    @staticmethod
    def _buffer_text(buf) -> str:
        try:
            start, end = buf.get_bounds()
            return buf.get_text(start, end, True)
        except Exception:
            return ""

    def note_response(self, key, message: dict, text: str, offset: int) -> None:
        """Parse + cache a completion response (GTK-free, any thread)."""
        try:
            items = intel.parse_completion(message or {}, text, offset)
            incomplete = intel.completion_is_incomplete(message or {})
        except Exception:
            items, incomplete = [], False
        prefix, _s = intel.prefix_at(text, offset)
        try:
            line, char = intel.offset_to_position(text, offset)
        except Exception:
            line, char = 0, 0
        path = key[0] if isinstance(key, tuple) and key else str(key or "")
        with self._lock:
            self._cache[path] = _CacheEntry(
                items=list(items), is_incomplete=incomplete, path=path,
                line=line, char=char, offset=offset, prefix=prefix,
                time=time.monotonic(),
            )
            self._legacy_items = list(items)
            self._legacy_key = key
            self._legacy_time = time.monotonic()

    def fetch_sync(
        self, path: str, line: int, char: int, text: str, offset: int,
        timeout: float = FETCH_TIMEOUT_S,
    ) -> list:
        """Legacy blocking fetch (deprecated: populate is async now).

        Kept for unit tests and the non-framework fallback path. New
        code should rely on ``do_populate`` + cache instead. Must NOT run
        on the GTK main thread: ``event.wait`` would freeze the UI.
        """
        prefix, _s = intel.prefix_at(text, offset)
        try:
            on_main = threading.current_thread() is threading.main_thread()
        except Exception:
            on_main = False
        if on_main:
            # Blocking wait on the UI thread freezes the editor; production
            # code must use do_populate + async request instead. Warn and
            # fall through: this legacy shim is only kept for unit tests
            # and the headless fallback path (both main-thread).
            logger.warning(
                "fetch_sync on the main thread blocks the UI; "
                "use do_populate + async request instead.")
        with self._lock:
            entry = self._cache.get(path)
            if cache_valid(entry, path, line, prefix, char=char, offset=offset) and entry is not None:
                return list(entry.items)
            if self._legacy_key == (path, line) and self._legacy_items:
                return list(self._legacy_items)

        params = intel.position_params(path, line, char)
        params["context"] = {"triggerKind": 1, "triggerCharacter": None}

        event = threading.Event()
        box: dict = {}

        def _cb(message: dict) -> None:
            try:
                items = intel.parse_completion(message or {}, text, offset)
                incomplete = intel.completion_is_incomplete(message or {})
            except Exception:
                items, incomplete = [], False
            with self._lock:
                # Late (post-timeout) answers land in the cache too: the next
                # keystroke on the same line picks them up with no new request.
                box["items"] = items
                self._cache[path] = _CacheEntry(
                    items=list(items), is_incomplete=incomplete, path=path,
                    line=line, char=char, offset=offset, prefix=prefix,
                    time=time.monotonic(),
                )
                self._legacy_items = list(items)
                self._legacy_key = (path, line)
                self._legacy_time = time.monotonic()
            event.set()

        try:
            request_id = self._send_request("textDocument/completion", params, _cb)
        except Exception as e:
            logger.debug(f"roslyn completion send failed: {e!r}")
            request_id = None
        if request_id is None:
            with self._lock:
                return list(self._legacy_items)

        if event.wait(timeout):
            return list(box.get("items", []))

        with self._lock:
            box["expired"] = True
            return list(self._legacy_items)


if _AVAILABLE:
    class RoslynCompletionProvider(  # type: ignore[misc,no-redef,attr-defined]
        _RoslynCompletionProviderBase, GObject.Object, GtkSource.CompletionProvider  # type: ignore[attr-defined]
    ):
        """GtkSource provider; see _RoslynCompletionProviderBase for logic.

        The do_* virtuals MUST be (re)declared on this GType itself:
        PyGObject does not install trampolines for overrides inherited
        from a plain-Python mixin, so without these the framework silently
        falls back to the C defaults (match=True, empty proposals) and no
        completion ever appears. Each wrapper delegates to the base logic.
        """

        def do_get_name(self):  # noqa: N802
            return super().do_get_name()

        def do_get_activation(self):  # noqa: N802
            return super().do_get_activation()

        def do_get_interactive_delay(self):  # noqa: N802
            return super().do_get_interactive_delay()

        def do_get_priority(self):  # noqa: N802
            return super().do_get_priority()

        def do_match(self, context):  # noqa: N802
            return super().do_match(context)

        def do_populate(self, context):  # noqa: N802
            return super().do_populate(context)

        def do_get_start_iter(self, context, proposal):  # noqa: N802
            return super().do_get_start_iter(context, proposal)

        def do_activate_proposal(self, proposal, it):  # noqa: N802
            return super().do_activate_proposal(proposal, it)
else:
    # Same duplicate-base-class hazard as RoslynProposal above: with no
    # GtkSource typelib the GObject/iface bases don't exist, so fall back
    # to the plain logic base (never instantiated in this configuration).
    class RoslynCompletionProvider(_RoslynCompletionProviderBase):  # type: ignore[no-redef]
        """GtkSource provider; see _RoslynCompletionProviderBase for logic."""


def show_completion(view, provider=None) -> bool:
    """Force the framework popup open (VSCode Ctrl+Space).

    Mirrors GtkSourceView's own show-completion handler: the context is
    created without an explicit position (the framework anchors it at the
    cursor) and start() receives every registered provider. Hand-made
    contexts are rejected by the framework (add_proposals asserts the
    context is the completion's own), so this exact shape matters.
    Returns True when the request was issued; the provider's populate
    supplies the rows asynchronously.
    """
    try:
        completion = view.get_completion()
    except Exception as e:
        logger.debug(f"completion show: no completion object: {e!r}")
        return False
    if completion is None:
        logger.debug("completion show: view.get_completion() is None")
        return False
    try:
        context = completion.create_context()
    except Exception as e:
        logger.debug(f"completion show: create_context failed: {e!r}")
        return False
    try:
        registered = list(completion.get_providers() or [])
        providers = registered or ([provider] if provider is not None else [])
        logger.debug(f"completion show: start providers={len(providers)} "
              f"registered={[type(p).__name__ for p in registered]}")
        started = bool(completion.start(providers, context))
        logger.debug(f"completion show: start returned {started}")
        return started
    except TypeError:
        # Older bindings: start() takes no explicit provider list.
        try:
            return bool(completion.start(context))
        except Exception as e:
            logger.debug(f"completion show failed: {e!r}")
            return False
    except Exception as e:
        logger.debug(f"completion show failed: {e!r}")
        return False


def attach_to_views(window, provider, attached: set) -> int:
    """Register the provider on every view once. Returns new attachments."""
    count = 0
    try:
        views = window.get_views()
    except Exception as e:
        logger.debug(f"completion attach: get_views failed: {e!r}")
        return 0
    logger.debug(f"completion attach: {len(views or [])} views, {len(attached)} already attached")
    for view in views or []:
        try:
            key = hash(view)
        except Exception:
            continue
        if key in attached:
            continue
        try:
            completion = view.get_completion()
        except Exception:
            continue
        if completion is None:
            continue
        try:
            completion.add_provider(provider)
            attached.add(key)
            count += 1
        except Exception as e:
            logger.debug(f"completion provider attach failed: {e!r}")
    return count


def detach_from_views(window, provider, attached: set) -> None:
    """Remove the provider wherever it was attached."""
    try:
        views = window.get_views()
    except Exception:
        views = []
    for view in views or []:
        try:
            if hash(view) not in attached:
                continue
            view.get_completion().remove_provider(provider)
        except Exception:
            pass
    attached.clear()
