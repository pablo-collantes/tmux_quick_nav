#!/usr/bin/env sh
# notepad.sh — open (or create) the notepad for the current tmux project.
#
# Args: <session_name> <pane_current_path>
#
# Resolves the project root by preferring $CODE_DIR/<session> (the naming
# convention used by nav_tui.py: session name == folder name), where CODE_DIR
# defaults to ~/code_folder. Projects nested inside a folder flagged as a
# container in nav_tui.py are looked up there too, at any depth. Falls back to
# the pane's current path for ad-hoc sessions created outside it.
#
# Honours the `notepad` setting in $XDG_CONFIG_HOME/tmux-quick-nav/settings
# (press ^S in nav_tui.py): `all`, `top-level` — never inside a flagged folder
# — or `off`.
#
# Invoked from tmux via display-popup; see the "Project notepad" bind in
# ~/.config/tmux/tmux.conf.

# Resolve this script's directory up front: $0 may be relative to the caller's
# cwd (and we cd into the project below), or may be a symlink into ~/.local/bin.
script_dir=$(CDPATH= cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)

session="$1"
pane_path="$2"
code="${CODE_DIR:-$HOME/code_folder}"
config="${XDG_CONFIG_HOME:-$HOME/.config}/tmux-quick-nav"
containers="$config/containers"
settings="$config/settings"

# A folder flagged in nav_tui.py (^F) groups projects rather than being one.
# One path per line in $containers, relative to $CODE_DIR, so a flagged folder
# nested inside another is listed by its full relative path.
is_container() {
    [ -f "$containers" ] || return 1
    while IFS= read -r line; do
        case "$line" in '' | '#'*) continue ;; esac
        [ "$line" = "$1" ] && return 0
    done < "$containers"
    return 1
}

# True when $1 sits inside a flagged folder, however deep.
nested_in_container() {
    case "$1" in "$code"/*) ;; *) return 1 ;; esac
    rest=${1#"$code"/}
    prefix=""
    while [ "$rest" != "${rest#*/}" ]; do
        prefix="${prefix:+$prefix/}${rest%%/*}"
        rest=${rest#*/}
        is_container "$prefix" && return 0
    done
    return 1
}

setting() {   # setting <key> <default>
    value=""
    [ -f "$settings" ] && value=$(sed -n \
        "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*\([^#[:space:]]*\).*/\1/p" \
        "$settings" | tail -n 1)
    printf '%s' "${value:-$2}"
}

# tmux tears a `display-popup -E` down the moment we exit, so hold it open.
refuse() {
    printf '\n  %s\n\n  Press ^S in the navigator to change it, or edit\n  %s\n\n  Any key closes this.' \
        "$1" "$settings"
    saved=$(stty -g 2>/dev/null)
    [ -n "$saved" ] && stty raw -echo 2>/dev/null
    dd bs=1 count=1 >/dev/null 2>&1
    [ -n "$saved" ] && stty "$saved" 2>/dev/null
    exit 0
}

root=""
if [ -n "$session" ]; then
    if [ -d "$code/$session" ]; then
        root="$code/$session"
    elif [ -f "$containers" ]; then
        # Not directly under $CODE_DIR, so it may be a project inside a flagged
        # folder — those keep the leaf folder name as their session name.
        while IFS= read -r c; do
            case "$c" in '' | '#'*) continue ;; esac
            if [ -d "$code/$c/$session" ]; then
                root="$code/$c/$session"
                break
            fi
        done < "$containers"
    fi
fi

if [ -z "$root" ]; then
    if [ -n "$pane_path" ] && [ -d "$pane_path" ]; then
        root="$pane_path"
    else
        root="$PWD"
    fi
fi

case "$(setting notepad all)" in
    off)
        refuse "The notepad is switched off (notepad = off)."
        ;;
    top-level)
        if nested_in_container "$root"; then
            refuse "The notepad is limited to top-level projects (notepad = top-level)."
        fi
        ;;
esac

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
# this script, loaded by the absolute path resolved above.
#   Alt+n → save and close        Alt+c → new checkbox
#   Esc   → discard and close     Alt+x → toggle checkbox  (/c also inserts one)
exec nvim --clean -c "luafile $script_dir/notepad.lua" "$note"
