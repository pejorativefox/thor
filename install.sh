#!/usr/bin/env bash
# Install Thor editor assets.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
BIN_DIR="${XDG_BIN_HOME:-${THOR_BIN_DIR:-$HOME/.local/bin}}"
STYLE_DIR="${THOR_STYLE_DIR:-$DATA_HOME/thor/styles}"
GSV4_STYLE_DIR="$DATA_HOME/gtksourceview-4/styles"
# gtksourceview version assumption: specs target the GtkSourceView 4 API;
# if Thor migrates to GtkSourceView 5, point this at gtksourceview-5/language-specs.
LANG_DIR="${THOR_LANG_DIR:-$DATA_HOME/gtksourceview-4/language-specs}"
DESKTOP_DIR="$DATA_HOME/applications"
ICON_SCALABLE_DIR="$DATA_HOME/icons/hicolor/scalable/apps"
ICON_256_DIR="$DATA_HOME/icons/hicolor/256x256/apps"
LOG_DIR="$STATE_HOME/thor/logs"

# 1. Styles
mkdir -p "$STYLE_DIR" "$GSV4_STYLE_DIR"
shopt -s nullglob
style_files=("$SRC_DIR"/styles/*.xml)
shopt -u nullglob
if [ "${#style_files[@]}" -gt 0 ]; then
    cp "${style_files[@]}" "$STYLE_DIR/"
    cp "${style_files[@]}" "$GSV4_STYLE_DIR/"
fi

# 2. Language specs
mkdir -p "$LANG_DIR"
shopt -s nullglob
lang_files=("$SRC_DIR"/lang/*.lang)
shopt -u nullglob
if [ "${#lang_files[@]}" -gt 0 ]; then
    cp "${lang_files[@]}" "$LANG_DIR/"
fi

# 3. Desktop entry
mkdir -p "$DESKTOP_DIR"
if [ -f "$SRC_DIR/data/dev.thor.Editor.desktop" ]; then
    cp "$SRC_DIR/data/dev.thor.Editor.desktop" "$DESKTOP_DIR/"
fi

# 4. Icons
mkdir -p "$ICON_SCALABLE_DIR" "$ICON_256_DIR"
if [ -f "$SRC_DIR/data/icons/dev.thor.Editor.svg" ]; then
    cp "$SRC_DIR/data/icons/dev.thor.Editor.svg" "$ICON_SCALABLE_DIR/"
fi
if [ -f "$SRC_DIR/data/icons/dev.thor.Editor.png" ]; then
    cp "$SRC_DIR/data/icons/dev.thor.Editor.png" "$ICON_256_DIR/"
fi

# 5. Launchers
mkdir -p "$BIN_DIR"
for bin in thor-open thor-code thor-cli; do
    if [ -f "$SRC_DIR/$bin" ]; then
        cp "$SRC_DIR/$bin" "$BIN_DIR/"
        chmod +x "$BIN_DIR/$bin"
        echo "Installed $bin to $BIN_DIR"
    fi
done
# `thor` itself comes from `pip install -e .` (console_script thor.app:main).
# Provide a PATH shim when it is missing so desktop Exec=thor and
# _spawn_argv fallback keep working without pip install.
if ! command -v thor >/dev/null 2>&1 && [ -f "$BIN_DIR/thor-cli" ]; then
    printf '#!/usr/bin/env bash\nexec python3 "%s" "$@"\n' "$BIN_DIR/thor-cli" > "$BIN_DIR/thor"
    chmod +x "$BIN_DIR/thor"
    echo "Installed thor shim to $BIN_DIR (thor-cli wrapper)"
fi

# 6. State / Logs directory
mkdir -p "$LOG_DIR"

# 7. Update caches if tools available
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t "$DATA_HOME/icons/hicolor" 2>/dev/null || true
fi

echo "Installed color schemes to $STYLE_DIR and $GSV4_STYLE_DIR"
echo "Installed language specs to $LANG_DIR"
echo "Installed desktop entry to $DESKTOP_DIR/dev.thor.Editor.desktop"
echo "Installed icons to $DATA_HOME/icons/hicolor/"
echo "Installed launchers to $BIN_DIR:"
echo "  thor-open 'file.cs[:line[:col]]'"
echo "  thor-code [folder]"
echo "Thor state and logs directory: $LOG_DIR"
echo "For Thor: install the editor (pip install -e . or use thor-cli) then run:"
echo "  THOR_DEBUG=1 thor   # or: THOR_DEBUG=1 python -m thor"
echo "  thor-code [folder]  # open folder like code ."
echo "  thor-open 'file.cs:line'  # open at location"
