# Tmux Quick Nav

A fast, keyboard-driven project launcher that creates and switches between tmux sessions. Designed as a companion to the [homelab project](https://github.com/pablof036/homelab_project), using the same tmux session naming convention so sessions opened here are recognized by the homelab agents.

Press a hotkey, type to filter your projects, hit Enter — a tmux session opens in a new terminal window. If the session already exists, it attaches to it instead.

![Ubuntu 22.04 / 24.04](https://img.shields.io/badge/Ubuntu-22.04%20%7C%2024.04-E95420?logo=ubuntu&logoColor=white)
![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)

## Quick Start

```bash
git clone https://github.com/pablof036/tmux_quick_nav.git
cd tmux_quick_nav
bash install.sh
```

The install script will:

1. Check for dependencies (`tmux`, `python3`, `python3-tk`) and offer to install any that are missing
2. Register a **Ctrl+Super+Enter** keyboard shortcut in GNOME

The first time you launch, you'll be prompted to choose your projects directory (e.g. `~/code_folder`, `~/projects`, `~/code`). This is saved to `~/.config/tmux_quick_nav/config.json` and can be changed at any time from within the app.

## Usage

| Key | Action |
|---|---|
| **Ctrl+Super+Enter** | Open the launcher (press again to dismiss) |
| **Enter** | Open the selected project in a tmux session |
| **Tab** | Autocomplete the search field from the selected item |
| **Up / Down** | Navigate the project list |
| **Escape** | Close the launcher |

Typing in the search field filters projects in real time. Matches at the start of the folder name are prioritized, similar to terminal tab completion.

Active tmux sessions are marked with a filled circle, inactive ones with an empty circle.

## Settings

Click the gear icon in the top-right corner of the launcher to open settings. From there you can change:

- **Projects directory** — the folder containing your project subdirectories
- **Keybinding** — the keyboard shortcut to open the launcher (format: `Ctrl+Super+Enter`)

Changes are saved immediately. The projects list refreshes without restarting, and the GNOME keybinding is updated in place.

Configuration is stored at:

```
~/.config/tmux_quick_nav/config.json
```

## How It Works

- Reads all subdirectories from your configured projects folder
- Sanitizes the folder name into a tmux-compatible session name (matching the homelab project's `tmuxName()` convention)
- Creates a detached tmux session if one doesn't already exist for that project
- Opens a new `gnome-terminal` window attached to the session

The launcher runs as a single instance — the keyboard shortcut toggles it on and off using a PID file at `/tmp/tmux_quick_nav.pid`.

## Requirements

- **Ubuntu 22.04 or 24.04** (GNOME desktop)
- **Python 3.10+** (ships with Ubuntu 22.04+)
- **python3-tk** — Tkinter for the overlay UI
- **tmux** — terminal multiplexer
- **gnome-terminal** — ships with Ubuntu desktop

## License

MIT
