"""Language-intelligence glue: diagnostics + LSP request/response helpers.

Rendering: diagnostics are surfaced as (a) editor underline tags + gutter
marks (GtkSource.Buffer APIs, guarded), (b) a clickable Problems list, and
(c) a status summary. The pure helpers in this module (positions, parsing,
edit computation) are headless-testable; GTK application lives in views.py
and __init__.py.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from .roslyn import file_uri

@dataclass
class Diagnostic:
    uri: str
    path: str
    line: int  # 0-based
    character: int
    severity: int  # 1=error 2=warning 3=info 4=hint
    message: str
    code: str = ""


SEVERITY_LABEL = {1: "error", 2: "warning", 3: "info", 4: "hint"}


def normalize_diagnostics(uri: str, raw: list) -> List[Diagnostic]:
    out: List[Diagnostic] = []
    path = uri[7:] if uri.startswith("file://") else uri
    for item in raw:
        try:
            rng = item.get("range", {}).get("start", {})
            line = max(0, int(rng.get("line", 0)))
            char = max(0, int(rng.get("character", 0)))
            severity = int(item.get("severity", 3))
            message = str(item.get("message", "")).strip().splitlines()[0][:500]
            code = item.get("code", "")
            if isinstance(code, dict):
                code = str(code.get("value", ""))
            out.append(Diagnostic(uri, path, line, char, severity, message, str(code)))
        except Exception as e:
            logger.debug(f"normalize_diagnostics skip: {e!r}")
            continue
    return out


def summarize(diagnostics: List[Diagnostic]) -> str:
    errors = sum(1 for d in diagnostics if d.severity == 1)
    warnings = sum(1 for d in diagnostics if d.severity == 2)
    if errors == 0 and warnings == 0:
        return f"{len(diagnostics)} diagnostics"
    return f"{errors} errors, {warnings} warnings"


def position_params(path: str, line: int, character: int) -> dict:
    """Build textDocumentPositionParams (lines are 0-based like LSP)."""
    return {
        "textDocument": {"uri": file_uri(path)},
        "position": {"line": max(0, line), "character": max(0, character)},
    }


def diagnostics_by_file(store: Dict[str, List[Diagnostic]]) -> Dict[str, int]:
    return {uri: len(items) for uri, items in store.items()}


def xy_of(result) -> Tuple[int, int]:
    """Extract (x, y) from GI coordinate calls with unstable arity.

    Gtk coordinate methods disagree: most return (x, y), but
    Gdk.Window.get_origin() returns (ok, x, y). Take the last two so both
    shapes (and any future success-flag prefix) work.
    """
    try:
        seq = tuple(result)
    except TypeError as e:
        raise ValueError(f"not a coordinate tuple: {result!r}") from e
    if len(seq) < 2:
        raise ValueError(f"not a coordinate tuple: {result!r}")
    return int(seq[-2]), int(seq[-1])


# ---------------------------------------------------------------------------
# Positions / offsets (LSP positions are 0-based UTF-16 code units; buffer
# iters count unicode characters. The two agree for BMP text and differ
# only for astral characters (emoji etc.) — convert properly so Roslyn
# gets the right column, like VSCode does.)
# ---------------------------------------------------------------------------

def _utf16_len(text: str) -> int:
    try:
        return len(text.encode("utf-16-le")) // 2
    except Exception:
        return len(text)


def _utf16_slice(text: str, units: int) -> str:
    """First ``units`` UTF-16 code units of ``text`` as a str."""
    if units <= 0:
        return ""
    try:
        raw = text.encode("utf-16-le")
        cut = max(0, min(units * 2, len(raw)))
        # Avoid splitting a surrogate pair.
        while cut >= 2 and cut < len(raw):
            lo = int.from_bytes(raw[cut - 2 : cut], "little")
            if 0xD800 <= lo <= 0xDBFF:
                cut -= 2
                break
            break
        return raw[:cut].decode("utf-16-le", errors="ignore")
    except Exception:
        return text[:units]


def offset_to_position(text: str, offset: int) -> Tuple[int, int]:
    """Convert a char offset into an (line, character) pair (both 0-based).

    ``character`` is in UTF-16 code units per LSP (matches plain length
    for ASCII/BMP text).
    """
    offset = max(0, min(offset, len(text)))
    line = text.count("\n", 0, offset)
    line_start = text.rfind("\n", 0, offset) + 1
    return line, _utf16_len(text[line_start:offset])


def position_to_offset(text: str, line: int, character: int) -> int:
    """Convert a 0-based (line, character) pair into a char offset.

    ``character`` is interpreted as UTF-16 code units per LSP.
    """
    lines = text.split("\n")
    if not lines:
        return 0
    line = max(0, min(line, len(lines) - 1))
    prefix = _utf16_slice(lines[line], max(0, character))
    character = len(prefix)
    return sum(len(lines[i]) + 1 for i in range(line)) + character


_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def word_range_at(text: str, offset: int) -> Tuple[int, int]:
    """Return (start, end) offsets of the identifier surrounding offset."""
    for match in _WORD_RE.finditer(text):
        if match.start() <= offset <= match.end():
            return match.start(), match.end()
    return offset, offset


def line_start_offset(text: str, line: int) -> int:
    return position_to_offset(text, line, 0)


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------

#: VSCode-style commit characters for C#. GtkSource4 has no native
#: commit-char API, so the plugin best-effort accepts on these in the
#: fallback popup; the GtkSource path documents the gap (see gscompletion).
COMMIT_CHARACTERS = (".", "(", "[", "<", ";", ",")

#: LSP CompletionItemKind -> Gtk icon-name. Names follow the GNOME icon
#: theme / GtkSource convention; unknown kinds fall back to no icon.
COMPLETION_KIND_ICONS = {
    1: "completion-text",  # Text
    2: "completion-method",  # Method
    3: "completion-function",  # Function
    4: "completion-constructor",  # Constructor
    5: "completion-field",  # Field
    6: "completion-variable",  # Variable
    7: "completion-class",  # Class
    8: "completion-interface",  # Interface
    9: "completion-module",  # Module
    10: "completion-property",  # Property
    11: "completion-unit",  # Unit
    12: "completion-value",  # Value
    13: "completion-enum",  # Enum
    14: "completion-keyword",  # Keyword
    15: "completion-snippet",  # Snippet
    16: "completion-color",  # Color
    17: "completion-file",  # File
    18: "completion-reference",  # Reference
    19: "completion-folder",  # Folder
    20: "completion-enum-member",  # EnumMember
    21: "completion-constant",  # Constant
    22: "completion-struct",  # Struct
    23: "completion-event",  # Event
    24: "completion-operator",  # Operator
    25: "completion-type",  # TypeParameter
}


@dataclass
class CompletionItem:
    label: str
    detail: str = ""
    documentation: str = ""
    insert_text: str = ""
    replace_start: int = 0
    replace_end: int = 0
    kind: int = 0
    # VSCode-parity fields (all optional; older servers omit them).
    filter_text: str = ""
    sort_text: str = ""
    insert_format: int = 1  # 1=PlainText, 2=Snippet
    insert_start: int = 0
    insert_end: int = 0
    additional_edits: list = field(default_factory=list)
    commit_chars: list = field(default_factory=list)
    preselect: bool = False


def _markdown_to_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("value", ""))
    if isinstance(value, list):
        return "\n".join(_markdown_to_text(v) for v in value)
    return str(value)


def _strip_fences(text: str) -> str:
    """Strip markdown code fences for plain-text proposal info."""
    text = re.sub(r"```[a-zA-Z#]*\n?", "", text)
    return text.strip()


_SNIPPET_PLACEHOLDER_RE = re.compile(r"\$\{(\d+):([^}]*)\}")
_SNIPPET_TABSTOP_RE = re.compile(r"\$(\d+)")
_SNIPPET_BRACE_RE = re.compile(r"\$\{(\d+)\}")


def snippet_to_plain(snippet: str) -> str:
    """Best-effort Snippet -> plain text (VSCode tabstops removed).

    ``${1:foo}`` -> ``foo``, ``$1``/``$0`` -> ````. Escapes (``\\$``)
    are left as-is; full snippet expansion with placeholders is out of
    scope for the GtkSource provider.
    """
    if not snippet:
        return ""
    text = _SNIPPET_PLACEHOLDER_RE.sub(lambda m: m.group(2), snippet)
    text = _SNIPPET_BRACE_RE.sub("", text)
    text = _SNIPPET_TABSTOP_RE.sub("", text)
    return text


def completion_is_incomplete(response: dict) -> bool:
    """True when the server sent ``isIncomplete`` (VSCode re-queries)."""
    try:
        result = (response or {}).get("result")
        if isinstance(result, dict):
            return bool(result.get("isIncomplete"))
    except Exception:
        pass
    return False


def parse_completion(
    response: dict, text: str, offset: int, max_items: int = 500
) -> List[CompletionItem]:
    """Parse a textDocument/completion response into UI items.

    The replace range defaults to the identifier at the cursor; an LSP
    textEdit (if present and intersecting the cursor) overrides it.
    Both ``textEdit.range`` and ``textEdit.insert/replace`` shapes are
    understood. Items keep Roslyn's relevance order unless ``sortText``
    is present, in which case they are stably sorted by it (VSCode).
    """
    result = response.get("result") if isinstance(response, dict) else None
    if result is None:
        return []
    raw_items = result.get("items", result) if isinstance(result, dict) else result
    if not isinstance(raw_items, list):
        return []
    fallback_start, fallback_end = word_range_at(text, offset)
    items: List[CompletionItem] = []
    indexed: List[tuple[int, CompletionItem]] = []
    for index, raw in enumerate(raw_items[:max_items]):
        if not isinstance(raw, dict):
            continue
        label = str(raw.get("label", ""))
        if not label:
            # labelDetails-only items still need a label; skip empties.
            continue
        detail = str(raw.get("detail", "") or "")
        documentation = _strip_fences(_markdown_to_text(raw.get("documentation")))[:1000]
        try:
            insert_format = int(raw.get("insertTextFormat", 1))
        except (TypeError, ValueError):
            insert_format = 1
        raw_insert = str(
            raw.get("insertText", "")
            or raw.get("textEdit", {}).get("newText", "")
            or label
        )
        insert_text = snippet_to_plain(raw_insert) if insert_format == 2 else raw_insert
        start, end = fallback_start, fallback_end
        insert_start, insert_end = fallback_start, fallback_end
        text_edit = raw.get("textEdit") or {}
        single_range = text_edit.get("range") if isinstance(text_edit, dict) else None
        insert_range = (text_edit.get("insert") or {}).get("range") if isinstance(text_edit, dict) else None
        replace_range = (text_edit.get("replace") or {}).get("range") if isinstance(text_edit, dict) else None
        try:
            if isinstance(replace_range, dict) and isinstance(insert_range, dict):
                s = insert_range.get("start", {})
                e = insert_range.get("end", {})
                insert_start = position_to_offset(text, int(s.get("line", 0)), int(s.get("character", 0)))
                insert_end = position_to_offset(text, int(e.get("line", 0)), int(e.get("character", 0)))
                s = replace_range.get("start", {})
                e = replace_range.get("end", {})
                start = position_to_offset(text, int(s.get("line", 0)), int(s.get("character", 0)))
                end = position_to_offset(text, int(e.get("line", 0)), int(e.get("character", 0)))
            elif isinstance(single_range, dict):
                s = single_range.get("start", {})
                e = single_range.get("end", {})
                start = position_to_offset(text, int(s.get("line", 0)), int(s.get("character", 0)))
                end = position_to_offset(text, int(e.get("line", 0)), int(e.get("character", 0)))
                insert_start, insert_end = start, end
        except (TypeError, ValueError):
            pass
        try:
            kind = int(raw.get("kind", 0))
        except (TypeError, ValueError):
            kind = 0
        filter_text = str(raw.get("filterText", "") or "")
        sort_text = str(raw.get("sortText", "") or "")
        additional = raw.get("additionalTextEdits") or []
        if not isinstance(additional, list):
            additional = []
        commits = raw.get("commitCharacters") or []
        if not isinstance(commits, list):
            commits = []
        indexed.append(
            (
                index,
                CompletionItem(
                    label=label,
                    detail=detail,
                    documentation=documentation,
                    insert_text=insert_text,
                    replace_start=start,
                    replace_end=end,
                    kind=kind,
                    filter_text=filter_text,
                    sort_text=sort_text,
                    insert_format=insert_format,
                    insert_start=insert_start,
                    insert_end=insert_end,
                    additional_edits=list(additional),
                    commit_chars=[str(c) for c in commits if c],
                    preselect=bool(raw.get("preselect")),
                ),
            )
        )
    # VSCode sorts by sortText when the server provides it; otherwise the
    # server order is already relevance order — keep it stable. The
    # locals-first heuristic only applies when the server sent no
    # sortText at all (older/partial responses); otherwise it would fight
    # Roslyn's own relevance order.
    if any(item.sort_text for _, item in indexed):
        indexed.sort(key=lambda pair: (pair[1].sort_text or "\uffff", pair[0]))
        items = [item for _, item in indexed]
    elif any(item.kind in LOCAL_KINDS for _, item in indexed):
        items = locals_first([item for _, item in indexed])
    else:
        items = [item for _, item in indexed]
    return items


#: LSP kinds treated as locals (variables, parameters, constants): these
#: sort to the top of the list, stable, so `var |` offers locals first.
LOCAL_KINDS = frozenset({6, 21})


def locals_first(items: list["CompletionItem"]) -> list["CompletionItem"]:
    """Stable-partition locals (Variable/Constant kinds) to the front."""
    return [i for i in items if i.kind in LOCAL_KINDS] + [
        i for i in items if i.kind not in LOCAL_KINDS
    ]


def _match_text(item: "CompletionItem") -> str:
    return (item.filter_text or item.label or "").lower()


def fuzzy_score(candidate: str, query: str) -> tuple | None:
    """VSCode-like fuzzy score (lower is better), None when no match.

    ``candidate``/``query`` are already lowered. Tiers: prefix match (0),
    substring (1), subsequence (2). Within a tier, earlier + tighter
    matches win. ``None`` means the query's chars don't appear in order.
    """
    if not query:
        return (3, 0, 0)
    cand = candidate or ""
    if cand.startswith(query):
        return (0, 0, 0)
    pos = cand.find(query)
    if pos >= 0:
        return (1, pos, 0)
    # Subsequence scan (e.g. ``wrLn`` -> ``WriteLine``, ``cw`` -> ``Console``).
    ci = 0
    first = -1
    gaps = 0
    qi = 0
    qlen = len(query)
    clen = len(cand)
    while qi < qlen and ci < clen:
        if cand[ci] == query[qi]:
            if first < 0:
                first = ci
            qi += 1
        elif first >= 0:
            gaps += 1
        ci += 1
    if qi < qlen:
        return None
    last = ci - 1
    span = last - first if first >= 0 else clen
    return (2, first, gaps + span)


#: LSP kinds that take `(` on accept (callables).
PAREN_KINDS = frozenset({2, 3, 4})

#: LSP kinds that take `.` on accept (values/namespaces worth chaining off).
DOT_KINDS = frozenset({5, 6, 7, 8, 9, 10, 13, 22})


def completion_suffix(kind: int, insert_text: str) -> str:
    """Chained char to append after accepting a completion (`(`/`.`/`""`).

    Skipped when the inserted text already carries its own terminator
    (snippets like ``WriteLine($1)``, ``foo()``) so nothing duplicates.
    """
    try:
        kind = int(kind)
    except (TypeError, ValueError):
        return ""
    text = insert_text or ""
    if not text or text.endswith(("(", ")", ".", ";", ",")):
        return ""
    if "(" in text or ")" in text:
        return ""
    if kind in PAREN_KINDS:
        return "("
    if kind in DOT_KINDS:
        return "."
    return ""


def rank_for_prefix(items: list["CompletionItem"], prefix: str) -> list["CompletionItem"]:
    """Cull non-matches and rank by typed prefix (VSCode-like fuzzy).

    Tiers: prefix match, substring, subsequence (``wrLn`` -> ``WriteLine``).
    Matches on ``filterText`` (fallback: label), case-insensitive. Ties
    keep Roslyn's server order (sortText order from parse_completion).
    Empty prefix keeps server order when sortText is present, else the
    locals-first fallback for servers that send no relevance order.
    """
    if not prefix:
        if any(getattr(i, "sort_text", "") for i in items):
            return list(items)
        return locals_first(items)
    return filter_completion(items, prefix)


def completion_filter_text(item: CompletionItem) -> str:
    """Text the framework filters on (VSCode: filterText or label)."""
    return item.filter_text or item.label


def completion_info_text(item: CompletionItem, max_len: int = 1000) -> str:
    """Combined detail + documentation for the proposal info pane."""
    detail = (item.detail or "").strip()
    doc = (item.documentation or "").strip()
    if detail and doc:
        return f"{detail}\n{doc}"[:max_len]
    return (detail or doc)[:max_len]


def completion_icon_name(kind: int) -> str:
    """Gtk icon-name for an LSP CompletionItemKind ('' if unknown)."""
    try:
        return COMPLETION_KIND_ICONS.get(int(kind), "")
    except (TypeError, ValueError):
        return ""


def should_trigger_completion(typed_char: str) -> bool:
    return typed_char in (".", "(", "<")


def is_identifier_char(char: str) -> bool:
    """True for chars that extend a C# identifier (letters, digits, _)."""
    return bool(char) and (char == "_" or char.isalnum())


def prefix_at(text: str, offset: int) -> tuple[str, int]:
    """Return (prefix, start_offset) of the identifier ending at offset.

    The prefix is the trailing [A-Za-z0-9_] run immediately before the
    cursor. Non-identifier chars (including '.') terminate it, so after
    ``Console.Wri`` with the cursor at the end the prefix is ``Wri``.
    """
    offset = max(0, min(offset, len(text)))
    start = offset
    while start > 0 and (text[start - 1] == "_" or text[start - 1].isalnum()):
        start -= 1
    return text[start:offset], start


def filter_completion(
    items: list["CompletionItem"], prefix: str
) -> list["CompletionItem"]:
    """Filter items by what the user has typed (VSCode-like fuzzy).

    Matches ``filterText`` (fallback: label), case-insensitive:
      1. labels starting with the prefix
      2. labels containing it as a substring
      3. labels matching it as a subsequence (``wrLn`` -> ``WriteLine``)
    Ties keep Roslyn's relevance order. Empty prefix returns every item.
    """
    if not prefix:
        return list(items)
    lowered = prefix.lower()
    scored: list[tuple[tuple, int, CompletionItem]] = []
    for index, item in enumerate(items):
        score = fuzzy_score(_match_text(item), lowered)
        if score is not None:
            scored.append((score, index, item))
    scored.sort(key=lambda triple: (triple[0], triple[1]))
    return [item for _, _, item in scored]


def best_initial_index(items: list["CompletionItem"]) -> int:
    """Index the popup should select first (VSCode preselect wins)."""
    for index, item in enumerate(items):
        try:
            if bool(getattr(item, "preselect", False)):
                return index
        except Exception:
            continue
    return 0


def completion_commit_chars(item: "CompletionItem") -> list[str]:
    """Effective commit chars: per-item LSP list, else the C# defaults."""
    try:
        own = list(getattr(item, "commit_chars", None) or [])
    except Exception:
        own = []
    own = [c for c in own if c]
    if own:
        return own
    return list(COMMIT_CHARACTERS)


# ---------------------------------------------------------------------------
# Hover / locations
# ---------------------------------------------------------------------------

def parse_hover(response: dict, max_len: int = 1500) -> str:
    """Extract plain text from a textDocument/hover response."""
    result = (response or {}).get("result") or {}
    contents = result.get("contents")
    if contents is None:
        return ""
    if isinstance(contents, dict) and "value" in contents:
        text = _markdown_to_text(contents)
    elif isinstance(contents, list):
        text = "\n".join(_markdown_to_text(c) for c in contents)
    else:
        text = _markdown_to_text(contents)
    # Strip markdown fences for tooltip display.
    text = re.sub(r"```[a-zA-Z#]*\n?", "", text)
    return text.strip()[:max_len]


@dataclass
class NavTarget:
    uri: str
    path: str
    line: int  # 0-based
    character: int = 0


def _nav_from_lsp_range(uri: str, loc_range: dict) -> NavTarget:
    path = uri[7:] if uri.startswith("file://") else uri
    start = (loc_range or {}).get("start", {})
    try:
        line = max(0, int(start.get("line", 0)))
        char = max(0, int(start.get("character", 0)))
    except (TypeError, ValueError):
        line, char = 0, 0
    return NavTarget(uri=uri, path=path, line=line, character=char)


def parse_locations(response: dict) -> List[NavTarget]:
    """Parse definition/references responses (Location | Location[] | LocationLink[])."""
    result = (response or {}).get("result")
    if result is None:
        return []
    raw_list = result if isinstance(result, list) else [result]
    targets: List[NavTarget] = []
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        uri = raw.get("uri") or raw.get("targetUri", "")
        loc_range = raw.get("range") or raw.get("targetRange") or {}
        # LocationLink points at targetSelectionRange for the symbol itself.
        sel = raw.get("targetSelectionRange")
        if sel:
            loc_range = sel
        if uri:
            targets.append(_nav_from_lsp_range(uri, loc_range))
    return targets


# ---------------------------------------------------------------------------
# Edits (formatting, code actions)
# ---------------------------------------------------------------------------

@dataclass
class EditOp:
    """A single text replacement computed from LSP edits (offsets into the
    original document text)."""

    start: int
    end: int
    new_text: str


def text_edits_to_ops(text_edits: list, text: str) -> List[EditOp]:
    """Convert LSP TextEdit[] into offset ops sorted for safe application."""
    ops: List[EditOp] = []
    for edit in text_edits or []:
        try:
            rng = edit["range"]
            start = position_to_offset(text, int(rng["start"]["line"]), int(rng["start"]["character"]))
            end = position_to_offset(text, int(rng["end"]["line"]), int(rng["end"]["character"]))
            ops.append(EditOp(start, end, str(edit.get("newText", ""))))
        except (KeyError, TypeError, ValueError) as e:
            logger.debug(f"text_edits_to_ops skip: {e!r}")
            continue
    # Apply from the end so earlier offsets stay valid.
    ops.sort(key=lambda op: (op.start, op.end), reverse=True)
    return ops


def apply_ops_to_text(text: str, ops: List[EditOp]) -> str:
    for op in ops:
        text = text[: op.start] + op.new_text + text[op.end :]
    return text


def workspace_edit_to_ops(workspace_edit: dict, current_text_by_uri: dict) -> Dict[str, List[EditOp]]:
    """Convert an LSP WorkspaceEdit into per-uri op lists.

    Supports both `changes` (uri -> TextEdit[]) and `documentChanges`
    (TextDocumentEdit[]). Unknown shapes are skipped with a debug log.
    """
    out: Dict[str, List[EditOp]] = {}
    if not isinstance(workspace_edit, dict):
        return out
    changes = workspace_edit.get("changes") or {}
    for uri, edits in changes.items():
        text = current_text_by_uri.get(uri, "")
        out[uri] = text_edits_to_ops(edits, text)
    for doc_change in workspace_edit.get("documentChanges") or []:
        if not isinstance(doc_change, dict):
            continue
        if doc_change.get("kind") == "rename":
            logger.debug("workspace_edit rename not supported, skipping")
            continue
        doc = doc_change.get("textDocument", {})
        uri = doc.get("uri", "")
        if not uri:
            continue
        text = current_text_by_uri.get(uri, "")
        out[uri] = text_edits_to_ops(doc_change.get("edits", []), text)
    return out


@dataclass
class CodeAction:
    title: str
    kind: str = ""
    edit: Optional[dict] = None
    command: Optional[dict] = None
    data: Optional[dict] = None
    needs_resolve: bool = False


def parse_code_actions(response: dict) -> List[CodeAction]:
    result = (response or {}).get("result") or []
    actions: List[CodeAction] = []
    for raw in result:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title", ""))
        if not title:
            continue
        actions.append(
            CodeAction(
                title=title,
                kind=str(raw.get("kind", "") or ""),
                edit=raw.get("edit"),
                command=raw.get("command"),
                data=raw.get("data"),
                needs_resolve=bool(raw.get("data") and not raw.get("edit")),
            )
        )
    # Prefer quickfixes/refactors over informational actions.
    order = {"quickfix": 0, "refactor": 1}
    actions.sort(key=lambda a: order.get((a.kind or "").split(".")[0], 9))
    return actions
