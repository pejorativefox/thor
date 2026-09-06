#!/usr/bin/env bash
# Install Thor editor assets.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
STYLE_DIR="${THOR_STYLE_DIR:-$DATA_HOME/thor/styles}"
# gtksourceview version assumption: specs target the GtkSourceView 4 API;
# if Thor migrates to GtkSourceView 5, point this at gtksourceview-5/language-specs.
LANG_DIR="${THOR_LANG_DIR:-$DATA_HOME/gtksourceview-4/language-specs}"
BIN_DIR="${THOR_BIN_DIR:-$HOME/.local/bin}"

mkdir -p "$STYLE_DIR"
shopt -s nullglob
style_files=("$SRC_DIR"/styles/*.xml)
shopt -u nullglob
if [ "${#style_files[@]}" -gt 0 ]; then
    cp "${style_files[@]}" "$STYLE_DIR/"
fi

mkdir -p "$LANG_DIR"
shopt -s nullglob
lang_files=("$SRC_DIR"/lang/*.lang)
shopt -u nullglob
if [ "${#lang_files[@]}" -gt 0 ]; then
    cp "${lang_files[@]}" "$LANG_DIR/"
fi

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
