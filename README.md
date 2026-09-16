# Tmux Quick Nav

A keyboard-driven project launcher for tmux, plus the desktop glue that makes
the rest of the system follow whichever project you are sitting in.

Hit a hotkey, type to filter, press Enter — a tmux session opens in a new
terminal window, or attaches if it already exists. Session name always equals
the project folder name, and that convention is what everything else here keys
off.

Built for **Omarchy / Hyprland** on Arch. The original GNOME version lives in
[`legacy/`](legacy/).

## What's in here

| File | Role |
|---|---|
| `nav_tui.py` | The launcher. A curses TUI showing project cards in a grid; active sessions are highlighted and sorted to the top. |
| `tmux-project-dir.sh` | Answers "which project is the focused window in?" from **outside** tmux. |
| `bin/omarchy-launch-files-project` | `SUPER + F` — Files, opened at the focused project. |
| `bin/omarchy-launch-code-project` | `SUPER + Z` — VSCode, opened at the focused project. |
| `notepad.sh` + `notepad.lua` | `Alt+n` inside tmux — a floating notepad anchored to the project root. |

## Project-aware app launching

The launchers open Files and VSCode at the project directory of whatever tmux
session you are currently focused on.

| Focused window | `SUPER + F` | `SUPER + Z` |
|---|---|---|
| tmux session in `~/code_folder/vet-os` | Files at `vet-os` | `code ~/code_folder/vet-os` |
| anything else | plain Files window | plain `code` (reopens last window) |

Subdirectories collapse to the project root, so sitting in
`vet-os/backend/src` still opens `vet-os` — which is what you want for a
VSCode workspace. Outside `~/code_folder` it falls back to the enclosing git
repo.

### Why not `omarchy-cmd-terminal-cwd`

Omarchy ships a helper for exactly this, and it cannot work inside tmux. It
reads the cwd of the terminal's child process and requires that child to be a
login shell listed in `/etc/shells`. Under tmux the child is `tmux: client`,
the shell check fails, and it returns `$HOME`.

The real cwd only exists in the tmux **server**, so `tmux-project-dir.sh` asks
tmux directly: walk the process tree down from the focused window's PID until
it finds a `tmux: client`, map that client to its session, then read the active
pane's path. It prints nothing and exits 1 when no tmux client is attached,
which is what drives the fallback column in the table above.

```bash
./tmux-project-dir.sh          # project root
./tmux-project-dir.sh --pane   # the active pane's exact cwd
```

`notepad.sh` solves the same problem the easy way — it runs *inside* tmux, so
it already knows its session name and just needs `~/code_folder/<session>`.
Only callers outside tmux have to do the process-tree walk.

## Install

Clone anywhere, then point the system at it.

**1. Symlink the launchers** so `~/.local/bin` and the repo stay in sync:

```bash
ln -sf "$PWD/bin/omarchy-launch-files-project" ~/.local/bin/
ln -sf "$PWD/bin/omarchy-launch-code-project"  ~/.local/bin/
```

The launchers resolve their own symlink to find the repo, so the clone can live
wherever you like.

**2. Hyprland binds** in `~/.config/hypr/bindings.lua`:

```lua
hl.unbind("SUPER + F")             -- Omarchy default: Full screen
hl.unbind("SUPER + CTRL + RETURN") -- Omarchy default: Herdr

o.bind("SUPER + CTRL + RETURN", "Tmux project navigator", "~/.local/bin/omarchy-launch-tmux-nav")
o.bind("SUPER + F", "File manager", "~/.local/bin/omarchy-launch-files-project")
o.bind("SUPER + Z", "VSCode", "~/.local/bin/omarchy-launch-code-project")
```

`SUPER + Z` is free on a stock Omarchy install; `SUPER + F` and
`SUPER + CTRL + RETURN` are not, hence the unbinds.

**3. The navigator launcher**, `~/.local/bin/omarchy-launch-tmux-nav`:

```bash
#!/bin/bash
exec setsid uwsm-app -- xdg-terminal-exec --app-id=org.omarchy.tmux-nav \
  -e python3 "$HOME/code_folder/tmux_folder_quick_nav/nav_tui.py"
```

**4. The notepad bind** in `~/.config/tmux/tmux.conf`:

```tmux
bind -n M-n display-popup -E -w 80% -h 85% -b rounded \
  "~/code_folder/tmux_folder_quick_nav/notepad.sh '#{session_name}' '#{pane_current_path}'"
```

## Keys

| Key | Where | Action |
|---|---|---|
| `SUPER + CTRL + ENTER` | anywhere | Open the project navigator |
| `SUPER + F` | anywhere | Files, at the focused project |
| `SUPER + Z` | anywhere | VSCode, at the focused project |
| `Alt+n` | in tmux | Project notepad |
| `Alt+x` | in the notepad | Toggle a `- [ ]` checkbox |
| `Enter` / `Tab` / `Esc` | in the navigator | Open / autocomplete / close |

## Conventions

- Session name **==** folder name under `~/code_folder`, matching
  [homelab_project](https://github.com/pablo-collantes/homelab_project) so its
  agents recognise sessions opened here.
- The notepad is `NOTES.md` at the project root (or an existing `notes.md` /
  `TODO.md`), gitignored so it never travels with the project.
