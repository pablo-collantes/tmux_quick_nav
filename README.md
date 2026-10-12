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

## Requirements

The navigator itself is deliberately thin:

- `tmux`
- `python3` — standard library only, `curses` included. No pip packages.

The desktop integration needs more, and each piece degrades on its own rather
than breaking the rest:

| For | Needs |
|---|---|
| Project-aware launchers | `hyprctl` (Hyprland), `jq`, `uwsm-app` + `xdg-terminal-exec` (Omarchy) |
| `SUPER + F` | `nautilus` |
| `SUPER + Z` | `code` |
| `Alt+n` notepad | `nvim` |

`tmux-project-dir.sh` exits 1 silently when `tmux`, `hyprctl` or `jq` is
missing, so the launchers simply fall back to opening without a project.

## Where your projects live

Everything resolves projects under one directory, `~/code_folder` by default.
Point it somewhere else with `CODE_DIR`:

```bash
export CODE_DIR="$HOME/src"
```

`nav_tui.py`, `tmux-project-dir.sh` and `notepad.sh` all read it. Set it in
your shell profile *and* in the Hyprland/tmux environment if you change it, so
the hotkey-launched processes see it too.

## Project-aware app launching

The launchers open Files and VSCode at the project directory of whatever tmux
session you are currently focused on.

| Focused window | `SUPER + F` | `SUPER + Z` |
|---|---|---|
| tmux session in `$CODE_DIR/my-project` | Files at `my-project` | `code $CODE_DIR/my-project` |
| anything else | plain Files window | plain `code` (reopens last window) |

Subdirectories collapse to the project root, so sitting in
`my-project/backend/src` still opens `my-project` — which is what you want for
a VSCode workspace. Outside `$CODE_DIR` it falls back to the enclosing git
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
it already knows its session name and just needs `$CODE_DIR/<session>`. Only
callers outside tmux have to do the process-tree walk.

## Install

Clone anywhere, then point the system at it. The commands below bake in the
clone's real location, so run them from inside the clone:

```bash
git clone https://github.com/pablo-collantes/tmux_quick_nav.git
cd tmux_quick_nav
```

**1. Symlink the launchers** so `~/.local/bin` and the repo stay in sync:

```bash
mkdir -p ~/.local/bin
ln -sf "$PWD/bin/omarchy-launch-files-project" ~/.local/bin/
ln -sf "$PWD/bin/omarchy-launch-code-project"  ~/.local/bin/
```

The launchers resolve their own symlink to find the repo, so the clone can live
wherever you like.

**2. The navigator launcher.** This writes it with the correct path already
filled in:

```bash
cat > ~/.local/bin/omarchy-launch-tmux-nav <<EOF
#!/bin/bash
exec setsid uwsm-app -- xdg-terminal-exec --app-id=org.omarchy.tmux-nav -e python3 "$PWD/nav_tui.py"
EOF
chmod +x ~/.local/bin/omarchy-launch-tmux-nav
```

**3. Hyprland binds** in `~/.config/hypr/bindings.lua`:

```lua
hl.unbind("SUPER + F")             -- Omarchy default: Full screen
hl.unbind("SUPER + CTRL + RETURN") -- Omarchy default: Herdr

o.bind("SUPER + CTRL + RETURN", "Tmux project navigator", "~/.local/bin/omarchy-launch-tmux-nav")
o.bind("SUPER + F", "File manager", "~/.local/bin/omarchy-launch-files-project")
o.bind("SUPER + Z", "VSCode", "~/.local/bin/omarchy-launch-code-project")
```

`SUPER + Z` is free on a stock Omarchy install; `SUPER + F` and
`SUPER + CTRL + RETURN` are not, hence the unbinds.

**4. The notepad bind** in `~/.config/tmux/tmux.conf`. This appends it with the
real path filled in (the `-T` title needs a Nerd Font; drop it if you'd rather
not):

```bash
cat >> ~/.config/tmux/tmux.conf <<EOF

# Project notepad
bind -n M-n display-popup -E -w 80% -h 85% -b rounded \
  -T " 󰠮 notes · #{session_name} " \
  "$PWD/notepad.sh '#{session_name}' '#{pane_current_path}'"
EOF
tmux source-file ~/.config/tmux/tmux.conf
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

In the navigator, `Ctrl+N` creates a new project folder under `$CODE_DIR` and
opens a session in it.

## Conventions

- Session name **==** folder name under `$CODE_DIR`. Keeping that one-to-one
  makes "which project am I in?" answerable from a session name alone, which is
  what the launchers and the notepad both rely on — and what any other tooling
  you point at tmux can rely on too.
- The notepad is `NOTES.md` at the project root (or an existing `notes.md` /
  `TODO.md`). On first creation, if the project already has a `.gitignore`, the
  notepad adds itself to it so your notes never travel with the project.

## License

[MIT](LICENSE).
