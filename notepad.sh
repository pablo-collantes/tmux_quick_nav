#!/usr/bin/env sh
# notepad.sh — open (or create) the notepad for the current tmux project.
#
# Args: <session_name> <pane_current_path>
#
# Resolves the project root by preferring ~/code_folder/<session> (the naming
# convention used by nav_tui.py: session name == folder name). Falls back to the
# pane's current path for ad-hoc sessions created outside code_folder.
#
# Invoked from tmux via display-popup; see the "Project notepad" bind in
# ~/.config/tmux/tmux.conf.

session="$1"
pane_path="$2"
code="$HOME/code_folder"

if [ -n "$session" ] && [ -d "$code/$session" ]; then
    root="$code/$session"
elif [ -n "$pane_path" ] && [ -d "$pane_path" ]; then
    root="$pane_path"
else
    root="$PWD"
fi

# Prefer an existing notepad at the project root; otherwise default to NOTES.md.
note=""
for cand in NOTES.md notes.md TODO.md; do
    if [ -f "$root/$cand" ]; then
        note="$root/$cand"
        break
    fi
done
note="${note:-$root/NOTES.md}"

# Seed a fresh notepad with a title so it's never an empty, unsaved buffer.
if [ ! -f "$note" ]; then
    printf '# %s — notes\n\n' "$(basename "$root")" > "$note"

    # First-time creation: if the project already keeps a .gitignore, add the
    # notepad to it so it stays out of version control. (A per-repo entry travels
    # with the project, complementing the machine-wide ~/.config/git/ignore.)
    # We only touch an existing .gitignore — we don't create one.
    note_name=$(basename "$note")
    gitignore="$root/.gitignore"
    if [ -f "$gitignore" ] && ! grep -qxF "$note_name" "$gitignore"; then
        # Ensure a trailing newline before appending so we don't glue onto the
        # last existing entry.
        [ -s "$gitignore" ] && [ -n "$(tail -c1 "$gitignore")" ] && printf '\n' >> "$gitignore"
        printf '%s\n' "$note_name" >> "$gitignore"
    fi
fi

cd "$root" 2>/dev/null || true

# Open nvim as a plain notepad. `--clean` skips your LazyVim config entirely, so
# nothing external can override the notepad keys (LazyVim maps <Esc> to
# clear-search on the VeryLazy event, which would otherwise clobber our discard
# binding) and startup stays fast. Your normal nvim setup is unaffected.
#
# All notepad behaviour (keymaps, checkbox helpers) lives in notepad.lua next to
# this script; it's loaded by absolute path so cwd doesn't matter.
#   Alt+n → save and close        Alt+c → new checkbox
#   Esc   → discard and close     Alt+x → toggle checkbox  (/c also inserts one)
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec nvim --clean -c "luafile $script_dir/notepad.lua" "$note"
