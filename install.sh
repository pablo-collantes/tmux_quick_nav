#!/bin/bash
# Install Tmux Quick Nav
# Tested on Ubuntu 22.04 and 24.04 (GNOME desktop)
#
# What this script does:
#   1. Checks / installs dependencies (tmux, python3-tk)
#   2. Sets up the Ctrl+Super+Enter keybinding in GNOME
#   3. On first launch the app will ask you to pick your projects directory

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_PATH="$SCRIPT_DIR/tmux_quick_nav.py"

# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------

MISSING=()

if ! command -v tmux &>/dev/null; then
    MISSING+=("tmux")
fi

if ! command -v python3 &>/dev/null; then
    MISSING+=("python3")
fi

# Check for tkinter (python3-tk)
if ! python3 -c "import tkinter" &>/dev/null; then
    MISSING+=("python3-tk")
fi

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "Missing dependencies: ${MISSING[*]}"
    read -rp "Install them now with apt? [Y/n] " answer
    answer="${answer:-Y}"
    if [[ "$answer" =~ ^[Yy] ]]; then
        sudo apt update && sudo apt install -y "${MISSING[@]}"
    else
        echo "Please install the missing packages and re-run this script."
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# Make launcher executable
# ---------------------------------------------------------------------------

chmod +x "$SCRIPT_PATH"

# ---------------------------------------------------------------------------
# GNOME keybinding setup
# ---------------------------------------------------------------------------

echo "Setting up Ctrl+Super+Enter keybinding..."

SCHEMA="org.gnome.settings-daemon.plugins.media-keys"
BINDING_NAME="Tmux Quick Nav"
COMMAND="python3 $SCRIPT_PATH"
BINDING="<Control><Super>Return"

# Read existing custom keybindings
EXISTING=$(gsettings get "$SCHEMA" custom-keybindings)

# Find existing slot for our binding, or the first free slot
SLOT=""
for i in $(seq 0 30); do
    KEY_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom${i}/"
    NAME=$(gsettings get "$SCHEMA.custom-keybinding:$KEY_PATH" name 2>/dev/null || echo "")

    if [ "$NAME" = "'$BINDING_NAME'" ]; then
        SLOT="custom${i}"
        echo "  Found existing slot: $SLOT (updating)"
        break
    fi

    if [ -z "$SLOT" ] && { [ -z "$NAME" ] || [ "$NAME" = "''" ]; }; then
        SLOT="custom${i}"
    fi
done

if [ -z "$SLOT" ]; then
    echo "Error: no free custom-keybinding slot found" >&2
    exit 1
fi

KEY_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/${SLOT}/"

# Add our slot to the list if it's not already there
if [[ "$EXISTING" != *"$SLOT"* ]]; then
    if [ "$EXISTING" = "@as []" ]; then
        NEW_LIST="['$KEY_PATH']"
    else
        NEW_LIST="${EXISTING%]},'$KEY_PATH']"
        # normalise whitespace (gsettings is picky)
        NEW_LIST=$(echo "$NEW_LIST" | sed "s/\['/['/; s/', *'/', '/g")
    fi
    gsettings set "$SCHEMA" custom-keybindings "$NEW_LIST"
fi

# Write the keybinding properties
gsettings set "$SCHEMA.custom-keybinding:$KEY_PATH" name "$BINDING_NAME"
gsettings set "$SCHEMA.custom-keybinding:$KEY_PATH" command "$COMMAND"
gsettings set "$SCHEMA.custom-keybinding:$KEY_PATH" binding "$BINDING"

echo ""
echo "Done!  Installation complete."
echo ""
echo "  Keybinding:  Ctrl+Super+Enter"
echo "  Script:      $SCRIPT_PATH"
echo ""
echo "The first time you press Ctrl+Super+Enter, you'll be asked"
echo "to choose your projects directory (e.g. ~/code_folder, ~/projects, ~/code)."
echo ""
echo "You can change it later using the \u2699 button inside the launcher."
