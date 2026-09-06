#!/usr/bin/env bash
# Install Thor editor assets.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STYLE_DIR="${XED_STYLE_DIR:-$HOME/.local/share/xed/styles}"
# Thor uses same XDG dirs for styles/lang as xed; keep override via env
STYLE_DIR="${THOR_STYLE_DIR:-$STYLE_DIR}"
LANG_DIR="${XED_LANG_DIR:-$HOME/.local/share/gtksourceview-4/language-specs}"
LANG_DIR="${THOR_LANG_DIR:-$LANG_DIR}"
BIN_DIR="${XED_BIN_DIR:-$HOME/.local/bin}"
BIN_DIR="${THOR_BIN_DIR:-$BIN_DIR}"

mkdir -p "$STYLE_DIR"
cp "$SRC_DIR"/styles/*.xml "$STYLE_DIR/"

mkdir -p "$LANG_DIR"
cp "$SRC_DIR"/lang/*.lang "$LANG_DIR/"

mkdir -p "$BIN_DIR"
for bin in thor-open thor-code thor-cli; do
    if [ -f "$SRC_DIR/$bin" ]; then
        cp "$SRC_DIR/$bin" "$BIN_DIR/"
        chmod +x "$BIN_DIR/$bin"
        echo "Installed $bin to $BIN_DIR"
    fi
done

# Keep gtksourceview-4/language-specs and lang/*.lang check for tests
echo "Installed color schemes to $STYLE_DIR"
echo "Installed language specs to $LANG_DIR"
echo "Installed launchers to $BIN_DIR:"
echo "  thor-open 'file.cs[:line[:col]]'"
echo "  thor-code [folder]"
echo "For Thor: install the editor (pip install -e . or use thor-cli) then run:"
echo "  THOR_DEBUG=1 thor   # or: THOR_DEBUG=1 python -m thor"
echo "  thor-code [folder]  # open folder like code ."
echo "  thor-open 'file.cs:line'  # open at location"
echo "Thor marker log: /tmp/thor-csharp-$(id -u).log"
