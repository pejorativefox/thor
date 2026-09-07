# Thor — lightweight GtkSourceView editor

A standalone `GtkSourceView` editor that bakes modern comforts in-process (no libpeas). Thor is a fast, native Linux editor with project browser, fuzzy finder, git gutter, C# support, terminal, and more — all via `thor/host.py`.

## What you get

**Project folder browser**
- Open any folder and browse its files in the side panel.
- Files are colored by git state, like in VS Code: green means new,
  tan means changed, red means deleted.
- Open a folder with `Ctrl+Shift+O`, or straight from the terminal:
  `thor-code [folder]` works like `code .` (focuses the live window when
  the folder is already open; `--new-window` forces a duplicate).

**Quick file opener**
- Press `Ctrl+P`, start typing any part of a file name, and jump to it.
  It understands capitals (`mc` finds `MyClass.cs`) and multiple words.

**Git change markers**
- The left edge of the editor shows what changed compared to git:
  green for added lines, tan for changed lines, red where lines were
  deleted. Untracked new files show all green. Marks refresh every
  time you save.

**C# support**
- Solution explorer, build / run / test per project, a test list with
  pass/fail marks, clickable error list, code completions, hover help,
   go-to-definition (`F12`), find references (`Shift+F12`), formatting
   (`Shift+Alt+F`) and quick fixes (`Alt+Enter`).

**Built-in terminal**
- A terminal in the bottom panel, with tabs: `Ctrl+Shift+T` opens a
  new terminal tab, `Ctrl+Shift+W` closes one, `` Ctrl+` `` jumps focus
  to the terminal and back.

**Less clutter**
- `Ctrl+B` hides side and bottom panes for distraction-free editing,
  `Ctrl+J` / `Ctrl+E` toggle bottom / side pane alone.

**Tab switching**
- `Ctrl+PageDown` jumps to the next tab, `Ctrl+PageUp` to the previous
  one, wrapping around at either end.

**Auto-reload**
- Files changed by another program (a build, a git checkout) reload on
  their own — but only when you have no unsaved edits, so your work is
  never overwritten.

**Word highlighting**
- Every occurrence of the word under your cursor lights up in the
  editor, with matching ticks in the ruler for quick scanning.

**Small helpers**
- `thor-open 'file.cs:line:col'` opens a file at an exact position —
  handy for terminal links (`thor-open` understands `file:line` and `file(line)`).
- One extra dark-friendly color scheme (`styles/atom-one-dark.xml`) is installed automatically
  and used by default (gutter-friendly, via `GtkSource.StyleSchemeManager`).

## Install

You need **python3-gi**, **gir1.2-gtk-3.0**, **gir1.2-gtksource-4** and for C# the
**dotnet SDK** plus Roslyn:

```bash
dotnet tool install --global roslyn-language-server
pip install -e .          # provides `thor` console script
# or run without install:
python -m thor
./thor-cli
```

Launch:

```bash
thor [folder|file ...]           # like `thor .`
thor-code [folder]               # `code .` equivalent (own process per window)
thor-open 'file.cs:line:col'     # open at location
THOR_DEBUG=1 thor                # verbose [thor:*] traces + marker log
```

Styles, `csharp.lang`, icons, desktop entry, and launchers are installed
via `./install.sh` to standard XDG directories:

```bash
./install.sh  # installs assets to ~/.local/share, logs to ~/.local/state, and launchers to ~/.local/bin
```

### XDG Directory Layout

- **Desktop Entry**: `~/.local/share/applications/dev.thor.Editor.desktop` (`$XDG_DATA_HOME/applications/`)
- **Icons**: `~/.local/share/icons/hicolor/{scalable,256x256}/apps/dev.thor.Editor.{svg,png}`
- **Styles**: `~/.local/share/gtksourceview-4/styles/` and `~/.local/share/thor/styles/`
- **Language Specs**: `~/.local/share/gtksourceview-4/language-specs/`
- **Configuration**: `~/.config/thor/` (`$XDG_CONFIG_HOME/thor/`)
- **Cache**: `~/.cache/thor/project-mode/` (`$XDG_CACHE_HOME/thor/`)
- **Logs & State**: `~/.local/state/thor/logs/` (`$XDG_STATE_HOME/thor/logs/`)
- **Launchers**: `~/.local/bin/` (`$XDG_BIN_HOME`)

## Everyday shortcuts

| Keys | What it does |
| ---- | ------------ |
| `Ctrl+Shift+O` | Open a project folder in a new window |
| `Ctrl+Shift+P` | Command palette (`Edit Settings file` opens `~/.config/thor/settings.toml`) |
| `Ctrl+B` | Hide/show all panes (focus mode) |
| `Ctrl+J` / `Ctrl+E` | Toggle bottom / side pane |
| `Ctrl+PageUp` / `Ctrl+PageDown` | Previous / next tab |
| `Ctrl+Shift+T` / `Ctrl+Shift+W` | New / close terminal tab |
| `` Ctrl+` `` | Jump focus to the terminal and back |
| `Ctrl+Space` | Code completions (C#) |
| `F12` / `Shift+F12` | Go to definition / find references (C#) |
| `Alt+Enter` | Quick fix for the error at the cursor (C#) |
| `Shift+Alt+F` | Format the file (C#) |
| `Ctrl+F` / `F3` / `Shift+F3` | Find bar / next / previous |
| `Ctrl+R` | Toggle word wrap (not in the terminal — that's reverse-search) |

## Something not working?

Run the self-check first:

```bash
python3 doctor.py          # checks Thor
python3 doctor.py --help   # filtered
```

The usual culprits:

1. **One editor per process, one window per folder.** Every window is its own
   process (`NON_UNIQUE` application, one window each). A second
   `thor-code <path>` for an already-open folder focuses the live window
   via the per-root IPC socket (no duplicate); `thor-open file:line` inside
   a live root forwards into it. `thor --new-window <folder>` forces a
   duplicate. `Ctrl+Q` and the window-manager close run the same per-window
   exit path, so one quit can never take down a sibling window. Global
   `~/.config/thor/state.toml` is last-writer-wins by design.
2. **Panes are hidden.** New panels live in the side/bottom panes; turn
   them on via View → Side Pane / Bottom Pane (or `Ctrl+B` / `Ctrl+E` / `Ctrl+J`).
3. **C# completions missing.** Make sure the dotnet SDK and
   `roslyn-language-server` are installed (see above). The C# Output
   panel at the bottom shows what the language server is doing.

If you report a problem, run with debug and include traces:
`THOR_DEBUG=1 thor` (`[thor:*]`). Marker log: `~/.local/state/thor/logs/thor-csharp.log`,
server stderr: `~/.local/state/thor/logs/roslyn-stderr.log`.

## For developers

The test suite runs under `pytest` — `pytest-xvfb` auto-creates a virtual display:

```bash
python3 -m pytest -q
```

Thor headless modules (`thor/csharp/*`, `thor/project`, etc.) import without a display.
