#!/usr/bin/env python3
"""
Tmux Quick Nav - Fast project launcher for tmux sessions.

Opens a floating overlay where you can type to filter projects,
then launches or switches to the matching tmux session using
the same naming convention as the homelab project.

Keybindings:
  Enter     - Open selected project's tmux session
  Tab       - Autocomplete from selected item
  Escape    - Close
  Up/Down   - Navigate list
"""

import json
import os
import re
import signal
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

CONFIG_DIR = Path.home() / ".config" / "tmux_quick_nav"
CONFIG_FILE = CONFIG_DIR / "config.json"
PID_FILE = Path("/tmp/tmux_quick_nav.pid")
DEFAULT_KEYBINDING = "<Control><Super>Return"
GNOME_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
GNOME_BINDING_NAME = "Tmux Quick Nav"


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load config from disk, returning defaults if missing."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_config(cfg: dict) -> None:
    """Persist config to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2) + "\n")


def get_projects_dir() -> Path | None:
    """Return the configured projects directory, or None if not set."""
    cfg = load_config()
    raw = cfg.get("projects_dir")
    if raw:
        p = Path(raw).expanduser()
        if p.is_dir():
            return p
    return None


# ---------------------------------------------------------------------------
# Keybinding helpers
# ---------------------------------------------------------------------------

_MODIFIER_TO_GSETTINGS = {
    "ctrl": "<Control>", "control": "<Control>",
    "alt": "<Alt>", "shift": "<Shift>", "super": "<Super>",
}
_KEY_TO_DISPLAY = {"Return": "Enter", "space": "Space", "BackSpace": "Backspace"}
_KEY_TO_GSETTINGS = {v: k for k, v in _KEY_TO_DISPLAY.items()}


def keybind_to_display(gs: str) -> str:
    """Convert gsettings format to display: '<Control><Super>Return' -> 'Ctrl+Super+Enter'."""
    parts = []
    remaining = gs
    for tag, label in [("<Control>", "Ctrl"), ("<Alt>", "Alt"),
                        ("<Shift>", "Shift"), ("<Super>", "Super")]:
        if tag in remaining:
            parts.append(label)
            remaining = remaining.replace(tag, "")
    parts.append(_KEY_TO_DISPLAY.get(remaining, remaining))
    return "+".join(parts)


def keybind_from_display(display: str) -> str | None:
    """Convert display format to gsettings: 'Ctrl+Super+Enter' -> '<Control><Super>Return'.

    Returns None if the format is invalid.
    """
    parts = [p.strip() for p in display.split("+")]
    if len(parts) < 2:
        return None
    gs_parts = []
    for mod in parts[:-1]:
        gs_mod = _MODIFIER_TO_GSETTINGS.get(mod.lower())
        if gs_mod is None:
            return None
        gs_parts.append(gs_mod)
    key = parts[-1]
    gs_parts.append(_KEY_TO_GSETTINGS.get(key, key))
    return "".join(gs_parts)


def update_gnome_keybinding(binding: str) -> bool:
    """Update the GNOME keybinding for Tmux Quick Nav. Returns True on success."""
    for i in range(31):
        key_path = f"/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom{i}/"
        result = subprocess.run(
            ["gsettings", "get", f"{GNOME_SCHEMA}.custom-keybinding:{key_path}", "name"],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip() == f"'{GNOME_BINDING_NAME}'":
            subprocess.run([
                "gsettings", "set",
                f"{GNOME_SCHEMA}.custom-keybinding:{key_path}",
                "binding", binding,
            ])
            return True
    return False


# ---------------------------------------------------------------------------
# Tmux helpers (matches homelab_project naming convention)
# ---------------------------------------------------------------------------

def tmux_name(project_name: str) -> str:
    """Sanitize project name for tmux session name.

    Mirrors the homelab web/android tmuxName() function for the default
    "Shell 1" label, which returns just the sanitized project name.
    """
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", project_name)


def get_active_sessions() -> set[str]:
    """Return the set of currently running tmux session names."""
    result = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return set()
    return set(result.stdout.strip().splitlines())


def tmux_session_exists(name: str) -> bool:
    result = subprocess.run(
        ["tmux", "has-session", "-t", name], capture_output=True
    )
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Project helpers
# ---------------------------------------------------------------------------

def get_projects(projects_dir: Path) -> list[str]:
    """Return sorted list of project directory names."""
    if not projects_dir.is_dir():
        return []
    return sorted(
        d.name
        for d in projects_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )


# ---------------------------------------------------------------------------
# Launch logic
# ---------------------------------------------------------------------------

def launch_project(project_name: str, projects_dir: Path) -> None:
    """Create (if needed) and open the tmux session for a project.

    Always opens a NEW gnome-terminal attached to the session.  We never
    use ``tmux switch-client`` because that would hijack whichever tmux
    client happens to be the most-recently-active one — including the
    homelab agent's internal node-pty clients, which breaks the agent's
    session tracking.
    """
    session_name = tmux_name(project_name)
    project_dir = str(projects_dir / project_name)

    # Ensure the tmux session exists (create detached if not)
    if not tmux_session_exists(session_name):
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", session_name, "-c", project_dir]
        )

    # Open a new terminal attached to the session
    subprocess.Popen(
        ["gnome-terminal", "--", "tmux", "attach-session", "-t", session_name]
    )


# ---------------------------------------------------------------------------
# Single-instance toggle
# ---------------------------------------------------------------------------

def handle_single_instance() -> None:
    """If another instance is running, kill it and exit (toggle off)."""
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            if old_pid != os.getpid():
                os.kill(old_pid, signal.SIGTERM)
        except (ValueError, ProcessLookupError, PermissionError):
            pass
        PID_FILE.unlink(missing_ok=True)
        sys.exit(0)

    PID_FILE.write_text(str(os.getpid()))


def cleanup(*_args) -> None:
    PID_FILE.unlink(missing_ok=True)
    sys.exit(0)


# ---------------------------------------------------------------------------
# Settings dialog (shared by first-time setup + settings button)
# ---------------------------------------------------------------------------

class SettingsDialog:
    """Modal dialog for choosing the projects directory and keybinding."""

    BG = "#1e1e2e"
    FG = "#cdd6f4"
    FG_DIM = "#585b70"
    ENTRY_BG = "#313244"
    ACCENT = "#89b4fa"
    BORDER = "#585b70"
    BTN_BG = "#313244"

    def __init__(
        self,
        parent: tk.Tk | None = None,
        current_path: str = "",
        current_keybind: str = DEFAULT_KEYBINDING,
    ) -> None:
        self.result: str | None = None
        self.keybind_result: str | None = None

        if parent is None:
            self.root = tk.Tk()
            self.root.title("Tmux Quick Nav \u2014 Setup")
            self._owns_root = True
        else:
            self.root = tk.Toplevel(parent)
            self.root.title("Settings")
            self.root.transient(parent)
            self.root.grab_set()
            self._owns_root = False

        self.root.attributes("-type", "dialog")
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)
        self.root.configure(bg=self.BORDER)

        w, h = 480, 280
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 3}")

        main = tk.Frame(self.root, bg=self.BG)
        main.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        # -- projects directory --
        tk.Label(
            main, text="projects directory", font=("monospace", 10),
            bg=self.BG, fg=self.FG_DIM, anchor="w",
        ).pack(fill=tk.X, padx=16, pady=(16, 4))

        path_row = tk.Frame(main, bg=self.BG)
        path_row.pack(fill=tk.X, padx=16, pady=(0, 8))

        self.path_var = tk.StringVar(value=current_path)
        self.path_entry = tk.Entry(
            path_row, textvariable=self.path_var, font=("monospace", 13),
            bg=self.ENTRY_BG, fg=self.FG, insertbackground=self.ACCENT,
            relief=tk.FLAT, highlightthickness=1,
            highlightcolor=self.ACCENT, highlightbackground=self.BORDER,
        )
        self.path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.path_entry.focus_set()

        browse_btn = tk.Button(
            path_row, text="browse", font=("monospace", 10),
            bg=self.BTN_BG, fg=self.FG, activebackground=self.ACCENT,
            activeforeground=self.BG, relief=tk.FLAT, bd=0,
            highlightthickness=0, padx=8, command=self._browse,
        )
        browse_btn.pack(side=tk.LEFT, padx=(8, 0))

        # -- keybinding --
        tk.Label(
            main, text="keybinding  (e.g. Ctrl+Super+Enter)", font=("monospace", 10),
            bg=self.BG, fg=self.FG_DIM, anchor="w",
        ).pack(fill=tk.X, padx=16, pady=(4, 4))

        self._keybind_gsettings = current_keybind
        self.keybind_var = tk.StringVar(value=keybind_to_display(current_keybind))
        keybind_entry = tk.Entry(
            main, textvariable=self.keybind_var, font=("monospace", 13),
            bg=self.ENTRY_BG, fg=self.FG, insertbackground=self.ACCENT,
            relief=tk.FLAT, highlightthickness=1,
            highlightcolor=self.ACCENT, highlightbackground=self.BORDER,
        )
        keybind_entry.pack(fill=tk.X, padx=16, pady=(0, 8))

        # -- error label --
        self.error_label = tk.Label(
            main, text="", font=("monospace", 9),
            bg=self.BG, fg="#f38ba8", anchor="w",
        )
        self.error_label.pack(fill=tk.X, padx=16)

        # -- buttons --
        btn_row = tk.Frame(main, bg=self.BG)
        btn_row.pack(fill=tk.X, padx=16, pady=(8, 16))

        save_btn = tk.Button(
            btn_row, text="save", font=("monospace", 11),
            bg=self.ACCENT, fg=self.BG, activebackground="#b4d0fb",
            activeforeground=self.BG, relief=tk.FLAT, bd=0,
            highlightthickness=0, padx=16, pady=4, command=self._save,
        )
        save_btn.pack(side=tk.RIGHT)

        cancel_btn = tk.Button(
            btn_row, text="cancel", font=("monospace", 11),
            bg=self.BTN_BG, fg=self.FG, activebackground=self.ACCENT,
            activeforeground=self.BG, relief=tk.FLAT, bd=0,
            highlightthickness=0, padx=16, pady=4, command=self._cancel,
        )
        cancel_btn.pack(side=tk.RIGHT, padx=(0, 8))

        self.path_entry.bind("<Return>", lambda _: self._save())
        keybind_entry.bind("<Return>", lambda _: self._save())
        self.root.bind("<Escape>", lambda _: self._cancel())
        self.root.protocol("WM_DELETE_WINDOW", self._cancel)

    def _browse(self) -> None:
        start = self.path_var.get() or str(Path.home())
        chosen = filedialog.askdirectory(
            initialdir=start, title="Select projects directory",
            parent=self.root,
        )
        if chosen:
            self.path_var.set(chosen)

    def _save(self) -> None:
        # Validate path
        raw = self.path_var.get().strip()
        if not raw:
            self.error_label.config(text="path cannot be empty")
            return
        p = Path(raw).expanduser()
        if not p.is_dir():
            self.error_label.config(text="directory does not exist")
            return

        # Validate keybinding
        keybind_display = self.keybind_var.get().strip()
        if not keybind_display:
            self.error_label.config(text="keybinding cannot be empty")
            return
        gs_bind = keybind_from_display(keybind_display)
        if gs_bind is None:
            self.error_label.config(text="invalid keybinding format (use e.g. Ctrl+Super+Enter)")
            return

        # Save config
        self.result = str(p)
        self.keybind_result = gs_bind
        cfg = load_config()
        cfg["projects_dir"] = self.result
        cfg["keybinding"] = gs_bind
        save_config(cfg)

        # Update GNOME keybinding if it changed
        if gs_bind != self._keybind_gsettings:
            update_gnome_keybinding(gs_bind)

        self.root.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.keybind_result = None
        self.root.destroy()

    def run(self) -> str | None:
        """Run the dialog and return the chosen path (or None)."""
        self.root.mainloop()
        return self.result

    def wait(self) -> str | None:
        """Block until the Toplevel is closed (for use from QuickNav)."""
        self.root.wait_window()
        return self.result


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

class QuickNav:
    # -- colour palette (catppuccin-mocha-ish) --
    BG = "#1e1e2e"
    FG = "#cdd6f4" #cdd6f4
    FG_DIM = "#585b70"
    ENTRY_BG = "#313244"
    SELECT_BG = "#45475a"
    ACCENT = "#89b4fa"
    ACTIVE_DOT = "#a6e3a1"
    BORDER = "#585b70"

    def __init__(self, projects_dir: Path) -> None:
        self.projects_dir = projects_dir
        self.projects = get_projects(projects_dir)
        self.filtered: list[str] = list(self.projects)
        self.active_sessions = get_active_sessions()

        # -- root window --
        self.root = tk.Tk()
        self.root.title("Tmux Quick Nav")

        # Use the WM's own "dialog" window type instead of overrideredirect.
        # overrideredirect(True) makes the window completely invisible to the
        # WM, so Mutter/GNOME never assigns it keyboard focus — no amount of
        # focus_force() can fix that.  A "dialog" type window is WM-managed
        # (so it receives focus) but still floats above other windows.
        self.root.attributes("-type", "dialog")
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)
        self.root.configure(bg=self.BORDER)

        # -- geometry: centred, upper-third of screen --
        w, h = 520, 420
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 3
        self.root.geometry(f"{w}x{h}+{x}+{y}")

        # -- main frame (inside the 2px "border" frame) --
        main = tk.Frame(self.root, bg=self.BG)
        main.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        # -- header row (label + settings button) --
        header = tk.Frame(main, bg=self.BG)
        header.pack(fill=tk.X, padx=16, pady=(12, 4))

        tk.Label(
            header,
            text="tmux quick nav",
            font=("monospace", 10),
            bg=self.BG,
            fg=self.FG_DIM,
            anchor="w",
        ).pack(side=tk.LEFT)

        settings_btn = tk.Button(
            header,
            text="\u2699",
            font=("monospace", 20),
            bg=self.BG,
            fg=self.FG_DIM,
            activebackground=self.BG,
            activeforeground=self.ACCENT,
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0,
            padx=4,
            cursor="hand2",
            command=self._open_settings,
        )
        settings_btn.pack(side=tk.RIGHT)

        # -- search entry --
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self._on_search_changed)

        self.entry = tk.Entry(
            main,
            textvariable=self.search_var,
            font=("monospace", 16),
            bg=self.ENTRY_BG,
            fg=self.FG,
            insertbackground=self.ACCENT,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightcolor=self.ACCENT,
            highlightbackground=self.BORDER,
        )
        self.entry.pack(fill=tk.X, padx=16, pady=(4, 8))
        self.entry.focus_set()

        # -- results listbox --
        self.listbox = tk.Listbox(
            main,
            font=("monospace", 13),
            bg=self.BG,
            fg=self.FG,
            selectbackground=self.SELECT_BG,
            selectforeground=self.ACCENT,
            relief=tk.FLAT,
            highlightthickness=0,
            activestyle="none",
            bd=0,
        )
        self.listbox.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 8))

        # -- status bar --
        self.status_label = tk.Label(
            main,
            text="enter: open  |  tab: complete  |  esc: close  |  \u2699: settings",
            font=("monospace", 9),
            bg=self.BG,
            fg=self.FG_DIM,
            anchor="w",
        )
        self.status_label.pack(fill=tk.X, padx=16, pady=(0, 10))

        # -- populate initial list --
        self._update_listbox()

        # -- force keyboard focus after the window is mapped --
        self.root.after(50, self._grab_focus)

        # -- key bindings --
        self.entry.bind("<Return>", self._on_enter)
        self.entry.bind("<Tab>", self._on_tab)
        self.entry.bind("<Escape>", self._on_escape)
        self.entry.bind("<Up>", self._on_up)
        self.entry.bind("<Down>", self._on_down)
        self.listbox.bind("<Double-Button-1>", self._on_enter)
        self.root.protocol("WM_DELETE_WINDOW", self._on_escape)

        signal.signal(signal.SIGTERM, self._sigterm)

    # ---- focus ----

    def _grab_focus(self) -> None:
        """Force keyboard focus into the search entry."""
        self.root.lift()
        self.root.focus_force()
        self.entry.focus_set()

    # ---- settings ----

    def _open_settings(self) -> None:
        cfg = load_config()
        current_kb = cfg.get("keybinding", DEFAULT_KEYBINDING)
        dlg = SettingsDialog(
            parent=self.root,
            current_path=str(self.projects_dir),
            current_keybind=current_kb,
        )
        new_path = dlg.wait()
        if new_path and new_path != str(self.projects_dir):
            self.projects_dir = Path(new_path)
            self.projects = get_projects(self.projects_dir)
            self.search_var.set("")
            self._update_listbox()
        self.entry.focus_set()

    # ---- filtering ----

    def _on_search_changed(self, *_: object) -> None:
        self._update_listbox()

    def _update_listbox(self) -> None:
        query = self.search_var.get().lower()
        prefix = [p for p in self.projects if p.lower().startswith(query)]
        rest = [p for p in self.projects if not p.lower().startswith(query) and query in p.lower()]
        self.filtered = prefix + rest

        self.listbox.delete(0, tk.END)
        for p in self.filtered:
            dot = "\u25cf" if tmux_name(p) in self.active_sessions else "\u25cb"
            self.listbox.insert(tk.END, f" {dot}  {p}")

        if self.filtered:
            self.listbox.selection_set(0)

    # ---- selection helpers ----

    def _selected_project(self) -> str | None:
        sel = self.listbox.curselection()
        if sel:
            return self.filtered[sel[0]]
        return self.filtered[0] if self.filtered else None

    # ---- key handlers ----

    def _on_enter(self, _event: object = None) -> None:
        project = self._selected_project()
        if project:
            self._close_and_launch(project)

    def _close_and_launch(self, project: str) -> None:
        """Tear down the window cleanly, then launch the project."""
        self.root.destroy()
        PID_FILE.unlink(missing_ok=True)
        launch_project(project, self.projects_dir)
        sys.exit(0)

    def _on_tab(self, _event: object = None) -> str:
        project = self._selected_project()
        if project:
            self.search_var.set(project)
            self.entry.icursor(tk.END)
        return "break"

    def _on_escape(self, _event: object = None) -> None:
        self.root.destroy()
        cleanup()

    def _on_up(self, _event: object = None) -> str:
        sel = self.listbox.curselection()
        if sel and sel[0] > 0:
            self.listbox.selection_clear(sel[0])
            self.listbox.selection_set(sel[0] - 1)
            self.listbox.see(sel[0] - 1)
        return "break"

    def _on_down(self, _event: object = None) -> str:
        sel = self.listbox.curselection()
        if sel and sel[0] < len(self.filtered) - 1:
            self.listbox.selection_clear(sel[0])
            self.listbox.selection_set(sel[0] + 1)
            self.listbox.see(sel[0] + 1)
        return "break"

    def _sigterm(self, *_args: object) -> None:
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        cleanup()

    # ---- main loop ----

    def run(self) -> None:
        self.root.mainloop()


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------

def main() -> None:
    handle_single_instance()

    projects_dir = get_projects_dir()
    if projects_dir is None:
        # First-time setup — ask the user to pick a directory
        dlg = SettingsDialog(current_path=str(Path.home() / "code_folder"))
        chosen = dlg.run()
        if chosen is None:
            cleanup()
        projects_dir = Path(chosen)

    app = QuickNav(projects_dir)
    app.run()


if __name__ == "__main__":
    main()
