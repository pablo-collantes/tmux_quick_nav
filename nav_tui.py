#!/usr/bin/env python3
"""Tmux Folder Quick Nav — terminal UI for navigating code_folder tmux sessions.

Shows project cards in a grid. Active sessions are highlighted and sorted to top.
Click a card or type + Tab to autocomplete, then Enter to open. Right-click for
a menu, ^S for settings.

Naming convention, which everything else in this repo keys off:
  - Session name = folder name (e.g. "my-project")
  - CWD = $CODE_DIR/<folder>; CODE_DIR defaults to ~/code_folder

A folder that groups projects rather than being one (say "one-off-projects")
can be flagged as a container with ^F. Enter browses into a flagged folder
instead of opening a session there; Shift+Enter still opens the folder itself.
Flags are stored in $XDG_CONFIG_HOME/tmux-quick-nav/containers, which
notepad.sh and tmux-project-dir.sh read so they resolve nested projects too.
"""

import curses
import os
import re
import subprocess
import sys
import termios

CODE_DIR = os.path.expanduser(os.environ.get("CODE_DIR") or "~/code_folder")

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
    "tmux-quick-nav",
)
CONTAINERS_FILE = os.path.join(CONFIG_DIR, "containers")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings")

CONTAINERS_HEADER = """\
# Folders under $CODE_DIR that hold projects rather than being one.
#
# In nav_tui.py, Enter browses into these instead of opening a session, and
# Shift+Enter opens the folder itself. Toggle a folder with ^F; this file is
# rewritten when you do. One path per line, relative to $CODE_DIR.
"""

SETTINGS_HEADER = """\
# Settings for tmux-quick-nav, shared by nav_tui.py and notepad.sh.
#
# Press ^S in the navigator to change these; this file is rewritten when you
# do. Unknown keys and values are ignored, so a typo falls back to the default.
"""

# key, default, choices, label, what the setting does
SETTINGS_SPEC = (
    ("notepad", "all", ("all", "top-level", "off"),
     "Notepad", "Where Alt+n is allowed to open a NOTES.md"),
    ("hover", "on", ("on", "off"),
     "Mouse hover", "Moving the mouse over a card selects it"),
    ("hidden", "off", ("on", "off"),
     "Dotfolders", "List folders whose name starts with a dot"),
    ("enhanced_keys", "on", ("on", "off"),
     "Enhanced keys", "Ask the terminal to report Shift+Enter separately"),
)

SETTINGS_NOTES = {
    "notepad": "all: everywhere · top-level: not inside flagged folders · off: never",
    "hover": "Off keeps the selection where the keyboard left it",
    "hidden": "Off hides .config, .git and friends",
    "enhanced_keys": "Off if your terminal misbehaves; Shift+Enter then acts as Enter",
}

# Synthetic key codes for things getch() cannot report on its own.
KEY_SHIFT_ENTER = -1001
KEY_IGNORE = -1002

ENTER_KEYS = (10, 13, curses.KEY_ENTER)

# Final byte of a CSI/SS3 sequence -> the key it stands for. Needed because the
# kitty keyboard protocol turns off application cursor keys, so arrows arrive
# as CSI A..D while terminfo only lists the SS3 form (ESC O A) — ncurses then
# hands the sequence back one byte at a time and we decode it ourselves.
CSI_FINAL = {
    "A": curses.KEY_UP,
    "B": curses.KEY_DOWN,
    "C": curses.KEY_RIGHT,
    "D": curses.KEY_LEFT,
    "F": curses.KEY_END,
    "H": curses.KEY_HOME,
    "Z": curses.KEY_BTAB,
}

CSI_TILDE = {
    1: curses.KEY_HOME,
    2: curses.KEY_IC,
    3: curses.KEY_DC,
    4: curses.KEY_END,
    5: curses.KEY_PPAGE,
    6: curses.KEY_NPAGE,
    7: curses.KEY_HOME,
    8: curses.KEY_END,
}

CSI_RE = re.compile(r"\[([0-9;:]*)([A-Za-z~])")

BUTTON5_PRESSED = getattr(curses, "BUTTON5_PRESSED", 0)


# ── config files ─────────────────────────────────────────────────────────────

def load_containers():
    try:
        with open(CONTAINERS_FILE, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return set()
    return {
        line.strip() for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    }


def save_containers(containers):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONTAINERS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(CONTAINERS_HEADER)
        for rel in sorted(containers):
            fh.write(rel + "\n")
    os.replace(tmp, CONTAINERS_FILE)


def load_settings():
    values = {key: default for key, default, _, _, _ in SETTINGS_SPEC}
    choices = {key: valid for key, _, valid, _, _ in SETTINGS_SPEC}
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return values
    for line in lines:
        key, sep, value = line.split("#", 1)[0].partition("=")
        key, value = key.strip(), value.strip()
        if sep and key in choices and value in choices[key]:
            values[key] = value
    return values


def save_settings(values):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(SETTINGS_HEADER)
        for key, default, choices, _, about in SETTINGS_SPEC:
            fh.write(f"\n# {about}\n")
            fh.write(f"#   {' | '.join(choices)}  (default: {default})\n")
            fh.write(f"{key} = {values.get(key, default)}\n")
    os.replace(tmp, SETTINGS_FILE)


# ── projects and sessions ────────────────────────────────────────────────────

def get_projects(rel="", hidden=False):
    """Sorted sub-directories of $CODE_DIR/<rel>."""
    base = os.path.join(CODE_DIR, rel) if rel else CODE_DIR
    try:
        names = os.listdir(base)
    except OSError:
        return []
    return sorted(
        name for name in names
        if (hidden or not name.startswith("."))
        and os.path.isdir(os.path.join(base, name))
    )


def list_sessions():
    """{session name: start directory} for every live tmux session.

    One call answers both "is it open?" and "where is it rooted?". Asking per
    session with `display-message -t` does not work: in tmux 3.7 that prints an
    empty string and exits 0 whether or not the session exists, so a missing
    session looks like one rooted at "".
    """
    try:
        out = subprocess.check_output(
            ["tmux", "list-sessions", "-F", "#{session_name}\t#{session_path}"],
            stderr=subprocess.DEVNULL,
        ).decode()
    except (subprocess.CalledProcessError, OSError):
        return {}
    sessions = {}
    for line in out.splitlines():
        name, _, path = line.partition("\t")
        if name:
            sessions[name] = path
    return sessions


def session_target(name):
    """A tmux target matching exactly this session and nothing else.

    "=" turns off prefix matching, and the trailing ":" closes the session part
    of the target — without it a folder like "app1.2" is read as window "2" of
    session "app1" and never found, even though tmux happily created it.
    """
    return "=" + name + ":"


def same_dir(a, b):
    """True when a and b are the same directory, or a no longer exists.

    A session started before its project was moved into a flagged folder keeps
    the old location in #{session_path}. Counting a vanished path as a match
    reattaches you to the session you already have instead of opening a second
    one beside it.
    """
    if not os.path.exists(a):
        return True
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.realpath(a) == os.path.realpath(b)


def path_is(a, b):
    """True when a and b are the same directory, and it exists right now."""
    try:
        return bool(a) and os.path.samefile(a, b)
    except OSError:
        return False


def kill_session(name):
    """Close one tmux session. True when it is gone afterwards."""
    try:
        subprocess.check_call(
            ["tmux", "kill-session", "-t", session_target(name)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except (subprocess.CalledProcessError, OSError):
        return False


def current_session():
    """The session this navigator itself is running inside, if any."""
    if not os.environ.get("TMUX"):
        return None
    try:
        # No -t here, so this is the one display-message form that works.
        out = subprocess.check_output(
            ["tmux", "display-message", "-p", "#{session_name}"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except (subprocess.CalledProcessError, OSError):
        return None
    return out or None


# ── terminal modes ───────────────────────────────────────────────────────────

def term_write(seq):
    try:
        sys.stdout.write(seq)
        sys.stdout.flush()
    except (OSError, ValueError):
        pass


def enhanced_keys(enable):
    """Ask the terminal to report keys unambiguously (kitty keyboard protocol).

    Shift+Enter has no legacy encoding — terminals send plain CR for it — so
    without this there is nothing to distinguish it from Enter. Flag 1,
    "disambiguate escape codes", is enough to get CSI 13;2u back. Terminals
    that do not implement the protocol ignore both sequences, and a terminal
    whose own keybinding sends ESC CR is handled by decode_escape as well.
    """
    term_write("\x1b[>1u" if enable else "\x1b[<u")


def mouse_motion(enable):
    """Turn mouse motion reporting (mode 1003) on or off.

    ncurses asks for button events only, whatever mousemask() says, so hover
    needs this — and because ncurses never asked for it, its own teardown does
    not switch it off either.
    """
    term_write("\x1b[?1003h" if enable else "\x1b[?1003l")


def allow_ctrl_s():
    """Stop the tty eating ^S as XOFF so it can reach the app as a key.

    Called after initscr, so ncurses' saved shell mode (restored by endwin)
    still has flow control on and the terminal is left as we found it.
    """
    try:
        fd = sys.stdin.fileno()
        mode = termios.tcgetattr(fd)
        mode[0] &= ~termios.IXON
        termios.tcsetattr(fd, termios.TCSANOW, mode)
    except (termios.error, ValueError, OSError):
        pass


# ── key decoding ─────────────────────────────────────────────────────────────

def decode_escape(text):
    """Map the bytes following an ESC to a key code, KEY_IGNORE if unknown."""
    if text in ("\r", "\n"):
        return KEY_SHIFT_ENTER          # ESC CR: a terminal keybinding for Shift+Enter
    if len(text) == 2 and text[0] == "O":
        return CSI_FINAL.get(text[1], KEY_IGNORE)

    match = CSI_RE.fullmatch(text)
    if not match:
        return KEY_IGNORE
    params, final = match.group(1), match.group(2)
    # Sub-parameters (1:3) carry event types we do not care about.
    nums = [int(p.split(":")[0] or 0) for p in params.split(";")] if params else []

    if final == "u":
        code = nums[0] if nums else 0
        mods = nums[1] if len(nums) > 1 and nums[1] else 1
        return _decode_csi_u(code, mods)
    if final == "~":
        return CSI_TILDE.get(nums[0] if nums else 0, KEY_IGNORE)
    if final in CSI_FINAL:
        # "CSI 1 ; mods X" is the same key with modifiers held; none of them
        # mean anything different here.
        return CSI_FINAL[final]
    return KEY_IGNORE


def _decode_csi_u(code, mods):
    """One "CSI code ; modifiers u" key event, in the terms getch() uses."""
    bits = mods - 1
    shift, ctrl = bool(bits & 1), bool(bits & 4)
    if code == 13:
        # Any modifier on Enter means "open this one directly".
        return KEY_SHIFT_ENTER if mods > 1 else 10
    if code == 27:
        return 27
    if code == 9:
        return curses.KEY_BTAB if shift else 9
    if code in (8, 127):
        return curses.KEY_BACKSPACE
    if ctrl and 0x40 <= code <= 0x7E:
        return code & 0x1F              # ^F, ^N, ^S ...
    if mods <= 1 and 32 <= code <= 126:
        return code
    return KEY_IGNORE


# ── TUI ──────────────────────────────────────────────────────────────────────

class NavTUI:
    CARD_W = 28
    CARD_H = 5
    GAP_X = 2
    GAP_Y = 1
    HEADER_H = 3          # rows reserved above grid
    FOOTER_H = 3          # rows reserved below grid

    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.settings = load_settings()
        self.containers = load_containers()
        self.rel = ""                 # where we are, relative to CODE_DIR
        self.projects = self._list()
        self.own_session = current_session()
        self.sessions = {}            # name -> start directory
        self._by_path = {}            # realpath -> [session name]
        self._at = {}                 # rel -> sessions standing for that project
        self._index_sessions()
        self.selected = 0
        self.search = ""
        self.filtered = self._build_filtered()
        self.scroll_offset = 0
        self.status = ""
        self._counts = {}             # container rel -> number of projects inside

    # ── current location ─────────────────────────────────────────────────

    def _list(self, rel=None):
        return get_projects(
            self.rel if rel is None else rel,
            hidden=self.settings["hidden"] == "on",
        )

    def _rel_of(self, name):
        return f"{self.rel}/{name}" if self.rel else name

    def _is_container(self, name):
        return self._rel_of(name) in self.containers

    def _index_sessions(self):
        """Re-read the live sessions and the directories they started in."""
        self.sessions = list_sessions()
        self._by_path = {}
        for name, path in self.sessions.items():
            if path:
                self._by_path.setdefault(os.path.realpath(path), []).append(name)
        self._at.clear()

    def _sessions_at(self, rel):
        """Live sessions standing for the project at $CODE_DIR/<rel>."""
        if rel in self._at:
            return self._at[rel]
        path = os.path.join(CODE_DIR, rel)
        found = set(self._by_path.get(os.path.realpath(path), ()))
        # Nested projects normally keep the bare folder name as their session
        # name, but fall back to the qualified one when that would collide.
        for name in (rel, os.path.basename(rel)):
            where = self.sessions.get(name)
            if where is None:
                continue
            # A session named after the folder counts even when the path it
            # recorded has gone: that is what a project moved into a flagged
            # folder looks like from here.
            if not where or not os.path.isdir(where) or path_is(where, path):
                found.add(name)
        self._at[rel] = sorted(found)
        return self._at[rel]

    def _sessions_under(self, rel):
        """Every live session inside $CODE_DIR/<rel>, at any depth."""
        root = os.path.realpath(os.path.join(CODE_DIR, rel))
        found = set(self._sessions_at(rel))
        for name, path in self.sessions.items():
            if name == rel or name.startswith(rel + "/"):
                found.add(name)
            elif path:
                real = os.path.realpath(path)
                if real == root or real.startswith(root + os.sep):
                    found.add(name)
        for child in self._list(rel):
            crel = f"{rel}/{child}"
            if crel in self.containers:
                found.update(self._sessions_under(crel))
            else:
                found.update(self._sessions_at(crel))
        return sorted(found)

    def _card_sessions(self, name):
        """What "close" would close for this card."""
        rel = self._rel_of(name)
        return self._sessions_under(rel) if self._is_container(name) else self._sessions_at(rel)

    def _is_active(self, name):
        return bool(self._sessions_at(self._rel_of(name)))

    def _child_count(self, name):
        rel = self._rel_of(name)
        if rel not in self._counts:
            self._counts[rel] = len(self._list(rel))
        return self._counts[rel]

    def _target(self, name):
        """(folder name, absolute path) for the project to open."""
        return name, os.path.join(CODE_DIR, self.rel, name)

    def _reload(self, select_name=None):
        self.projects = self._list()
        self._index_sessions()
        self._counts.clear()
        self.search = ""
        self.scroll_offset = 0
        self.filtered = self._build_filtered()
        self.selected = (
            self.filtered.index(select_name)
            if select_name in self.filtered else 0
        )

    def enter_dir(self, name):
        self.rel = self._rel_of(name)
        self._reload()

    def leave_dir(self):
        """Go up one level. False when already at the top of $CODE_DIR."""
        if not self.rel:
            return False
        came_from = os.path.basename(self.rel)
        self.rel = os.path.dirname(self.rel)
        self._reload(select_name=came_from)
        return True

    def toggle_flag(self, name):
        rel = self._rel_of(name)
        if rel in self.containers:
            self.containers.discard(rel)
            self.status = f"{name} unflagged · Enter opens a session in it"
        else:
            self.containers.add(rel)
            self.status = f"{name} flagged · Enter browses in, Shift+Enter opens it"
        try:
            save_containers(self.containers)
        except OSError as e:
            self.status = f"Could not save flags: {e}"

    def close_sessions(self, names):
        """kill-session each of these, except the one we are running inside."""
        mine = [n for n in names if n == self.own_session]
        names = [n for n in names if n != self.own_session]
        closed = [n for n in names if kill_session(n)]
        failed = [n for n in names if n not in closed]

        here = self.filtered[self.selected] if self.filtered else None
        self._index_sessions()
        self.filtered = self._build_filtered()
        self.selected = (
            self.filtered.index(here) if here in self.filtered
            else min(self.selected, max(0, len(self.filtered) - 1))
        )

        said = []
        if closed:
            said.append("Closed " + (closed[0] if len(closed) == 1
                                     else f"{len(closed)} sessions"))
        if mine:
            said.append("kept the session you are in")
        if failed:
            said.append("could not close " + ", ".join(failed))
        self.status = " · ".join(said) or "Nothing to close"

    # ── filtering / completion ───────────────────────────────────────────

    def _build_filtered(self):
        """Return project list filtered by search, active sessions first."""
        if self.search:
            items = [p for p in self.projects if self.search.lower() in p.lower()]
        else:
            items = list(self.projects)
        # Sort: active sessions first, then alphabetical
        return sorted(items, key=lambda p: (not self._is_active(p), p.lower()))

    def update_filter(self):
        self.filtered = self._build_filtered()
        self.selected = min(self.selected, max(0, len(self.filtered) - 1))
        self.scroll_offset = 0

    def get_completion(self):
        if not self.search:
            return None
        lower = self.search.lower()
        matches = [p for p in self.projects if p.lower().startswith(lower)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            prefix = os.path.commonprefix([m.lower() for m in matches])
            if len(prefix) > len(self.search):
                return matches[0][:len(prefix)]
        return None

    # ── input ────────────────────────────────────────────────────────────

    def _read_key(self):
        """getch(), plus the escape sequences curses has no terminfo entry for.

        Arrow keys under the kitty keyboard protocol (CSI A) and both ways a
        terminal can report Shift+Enter (CSI 13;2u, or ESC CR from a terminal
        keybinding) all reach curses as a bare ESC followed by the rest of the
        sequence, so we collect and decode them here.
        """
        ch = self.stdscr.getch()
        if ch != 27:
            return ch
        tail = self._read_escape_tail()
        if not tail:
            return 27
        return decode_escape(tail)

    def _read_escape_tail(self):
        self.stdscr.timeout(50)        # nothing more within 50ms = a real Esc
        out = []
        try:
            while len(out) < 24:
                c = self.stdscr.getch()
                if c == -1:
                    break
                out.append(chr(c) if 0 <= c < 0x80 else "�")
                if len(out) == 1 and out[0] not in ("[", "O"):
                    break              # ESC CR, or Alt+key
                if len(out) == 2 and out[0] == "O":
                    break              # SS3 is one byte long
                if len(out) > 1 and out[0] == "[" and "@" <= out[-1] <= "~":
                    break              # CSI final byte
        finally:
            self.stdscr.timeout(-1)
        return "".join(out)

    # ── layout helpers ───────────────────────────────────────────────────

    def _grid_cols(self):
        _, max_x = self.stdscr.getmaxyx()
        usable = max_x - 4
        return max(1, (usable + self.GAP_X) // (self.CARD_W + self.GAP_X))

    def _visible_rows(self):
        max_y, _ = self.stdscr.getmaxyx()
        avail = max_y - self.HEADER_H - self.FOOTER_H
        return max(1, avail // (self.CARD_H + self.GAP_Y))

    def _ensure_visible(self):
        cols = self._grid_cols()
        vis = self._visible_rows()
        sel_row = self.selected // cols
        if sel_row < self.scroll_offset:
            self.scroll_offset = sel_row
        elif sel_row >= self.scroll_offset + vis:
            self.scroll_offset = sel_row - vis + 1

    def _card_rect(self, idx):
        """(y, x) of a card, or None when it is not on screen."""
        max_y, max_x = self.stdscr.getmaxyx()
        cols = self._grid_cols()
        drow = idx // cols - self.scroll_offset
        if drow < 0:
            return None
        y = self.HEADER_H + drow * (self.CARD_H + self.GAP_Y)
        x = 2 + (idx % cols) * (self.CARD_W + self.GAP_X)
        if y + self.CARD_H > max_y - self.FOOTER_H or x >= max_x:
            return None
        return y, x

    # ── drawing ──────────────────────────────────────────────────────────

    def draw(self):
        self.stdscr.erase()
        max_y, max_x = self.stdscr.getmaxyx()
        self._ensure_visible()

        # ── title bar / breadcrumb ───────────────────────────────────────
        title = " Projects"
        self.stdscr.addstr(1, 2, title, curses.color_pair(5) | curses.A_BOLD)
        if self.rel:
            crumb = " / " + self.rel
            if 2 + len(title) + len(crumb) < max_x - 2:
                self.stdscr.addstr(
                    1, 2 + len(title), crumb,
                    curses.color_pair(13) | curses.A_BOLD,
                )

        active_n = sum(1 for p in self.filtered if self._is_active(p))
        info = f"{active_n} active · {len(self.filtered)} total"
        if max_x > len(info) + 4:
            self.stdscr.addstr(1, max_x - len(info) - 3, info, curses.color_pair(7))

        # ── cards ────────────────────────────────────────────────────────
        for idx, name in enumerate(self.filtered):
            rect = self._card_rect(idx)
            if rect is None:
                if idx // self._grid_cols() >= self.scroll_offset:
                    break
                continue
            y, x = rect
            self._draw_card(y, x, name, idx == self.selected, max_y, max_x)

        # ── scroll indicator ─────────────────────────────────────────────
        cols = self._grid_cols()
        total_rows = (len(self.filtered) + cols - 1) // cols
        if total_rows > self._visible_rows():
            pct = (self.scroll_offset + 1) / max(1, total_rows - self._visible_rows() + 1)
            bar_h = max_y - self.HEADER_H - self.FOOTER_H
            pos = self.HEADER_H + int(pct * (bar_h - 1))
            for ry in range(self.HEADER_H, self.HEADER_H + bar_h):
                ch = "█" if ry == pos else "│"
                attr = curses.color_pair(5) if ry == pos else curses.color_pair(7)
                try:
                    self.stdscr.addstr(ry, max_x - 2, ch, attr)
                except curses.error:
                    pass

        # ── status line ──────────────────────────────────────────────────
        if self.status:
            try:
                self.stdscr.addstr(
                    max_y - 3, 2, self.status[:max_x - 4],
                    curses.color_pair(3) | curses.A_BOLD,
                )
            except curses.error:
                pass

        # ── search bar ───────────────────────────────────────────────────
        sy = max_y - 2
        try:
            self.stdscr.addstr(sy, 0, " " * (max_x - 1), curses.color_pair(6))
            prompt = f"  ❯ {self.search}"
            self.stdscr.addstr(sy, 0, prompt[:max_x - 2], curses.color_pair(6))
            # cursor block
            cx = len(prompt)
            if cx < max_x - 1:
                self.stdscr.addstr(sy, cx, "▌", curses.color_pair(8))
        except curses.error:
            pass

        self._draw_help(max_y, max_x)
        self.stdscr.refresh()

    def _draw_help(self, max_y, max_x):
        sel = self.filtered[self.selected] if self.filtered else None
        on_container = sel is not None and self._is_container(sel)

        # (key, action, how droppable it is when the bar does not fit)
        parts = [("↑↓←→", "Move", 3), ("Type", "Search", 4), ("Tab", "Complete", 4)]
        if on_container:
            parts += [("Enter", "Browse", 1), ("S-Enter", "Open", 2)]
        else:
            parts += [("Enter", "Open", 1)]
        if sel is not None:
            parts += [("^F", "Unflag" if on_container else "Flag", 1)]
        parts += [("^N", "New", 2), ("^S", "Settings", 3), ("Right", "Menu", 5)]
        parts += [("Esc", "Up" if self.rel else "Quit", 0)]

        self._draw_key_bar(parts, max_y, max_x)

    def _draw_key_bar(self, parts, max_y, max_x):
        """Draw key hints on the last row, dropping the least useful to fit."""
        parts = [(k, a, p) for k, a, *rest in parts for p in (rest or [0],)]
        width = lambda ps: sum(len(k) + len(a) + 4 for k, a, _ in ps)
        while parts and width(parts) >= max_x:
            drop = max(range(len(parts)), key=lambda i: parts[i][2])
            if parts[drop][2] == 0:
                break
            parts.pop(drop)

        hx, hy = 1, max_y - 1
        try:                            # overlays draw their own bar over ours
            self.stdscr.addstr(hy, 0, " " * (max_x - 1))
        except curses.error:
            pass
        for key, action, _ in parts:
            if hx + len(key) + len(action) + 3 >= max_x:
                break
            try:
                self.stdscr.addstr(hy, hx, f" {key}", curses.color_pair(6) | curses.A_BOLD)
                self.stdscr.addstr(hy, hx + len(key) + 1, f":{action} ", curses.color_pair(7))
            except curses.error:
                pass
            hx += len(key) + len(action) + 4

    def _draw_card(self, y, x, name, selected, max_y, max_x):
        is_container = self._is_container(name)
        is_active = self._is_active(name)
        w = self.CARD_W

        if selected:
            pair = curses.color_pair(2)
            # Accent the border: purple for a container, green for a project.
            bdr = curses.color_pair(11 if is_container else 9) | curses.A_BOLD
        else:
            pair = curses.color_pair(1)
            bdr = curses.color_pair(12) if is_container else curses.color_pair(1)

        # fill background
        for r in range(self.CARD_H):
            end = min(x + w, max_x - 1)
            if y + r < max_y and x < end:
                try:
                    self.stdscr.addstr(y + r, x, " " * (end - x), pair)
                except curses.error:
                    pass

        iw = w - 2  # inner width for border chars
        if iw < 1:
            return

        # border
        top = "╭" + "─" * iw + "╮"
        bot = "╰" + "─" * iw + "╯"
        try:
            self.stdscr.addstr(y, x, top[:max_x - x - 1], bdr)
            self.stdscr.addstr(y + self.CARD_H - 1, x, bot[:max_x - x - 1], bdr)
            for r in range(1, self.CARD_H - 1):
                if y + r < max_y:
                    self.stdscr.addstr(y + r, x, "│", bdr)
                    ex = x + w - 1
                    if ex < max_x:
                        self.stdscr.addstr(y + r, ex, "│", bdr)
        except curses.error:
            pass

        # flag marker, punched into the top-right of the border
        if is_container and x + w - 1 < max_x:
            try:
                self.stdscr.addstr(y, x + w - 4, " ★ ", curses.color_pair(11) | curses.A_BOLD)
            except curses.error:
                pass

        # project name (up to 2 lines if needed)
        max_name_w = w - 4
        if len(name) <= max_name_w:
            try:
                self.stdscr.addstr(y + 1, x + 2, name, pair | curses.A_BOLD)
            except curses.error:
                pass
        else:
            line1 = name[:max_name_w]
            line2 = name[max_name_w:max_name_w * 2]
            try:
                self.stdscr.addstr(y + 1, x + 2, line1, pair | curses.A_BOLD)
                if y + 2 < max_y:
                    self.stdscr.addstr(y + 2, x + 2, line2, pair | curses.A_BOLD)
            except curses.error:
                pass

        # status badges
        badge_y = y + self.CARD_H - 2
        if badge_y >= max_y:
            return
        if is_container:
            badges = [
                (" Browse ", curses.color_pair(14) | curses.A_BOLD),
                (f" {self._child_count(name)} ", curses.color_pair(4)),
            ]
        elif is_active:
            badges = [
                (" Open ", curses.color_pair(4)),
                (" Active ", curses.color_pair(3) | curses.A_BOLD),
            ]
        else:
            badges = [
                (" Open ", curses.color_pair(4)),
                (" New ", curses.color_pair(10)),
            ]
        bx = x + 2
        for text, attr in badges:
            if bx + len(text) > min(x + w - 1, max_x - 1):
                break
            try:
                self.stdscr.addstr(badge_y, bx, text, attr)
            except curses.error:
                pass
            bx += len(text) + 1

    def _draw_box(self, y, x, w, h, title=""):
        """A filled, bordered panel. Returns False if it does not fit."""
        max_y, max_x = self.stdscr.getmaxyx()
        if y < 0 or x < 0 or y + h > max_y or x + w > max_x or w < 4:
            return False
        fill = curses.color_pair(1)
        bdr = curses.color_pair(9) | curses.A_BOLD
        try:
            for r in range(h):
                self.stdscr.addstr(y + r, x, " " * w, fill)
            self.stdscr.addstr(y, x, "╭" + "─" * (w - 2) + "╮", bdr)
            self.stdscr.addstr(y + h - 1, x, "╰" + "─" * (w - 2) + "╯", bdr)
            for r in range(1, h - 1):
                self.stdscr.addstr(y + r, x, "│", bdr)
                self.stdscr.addstr(y + r, x + w - 1, "│", bdr)
            if title:
                label = f" {title} "[:w - 4]
                self.stdscr.addstr(y, x + 2, label, curses.color_pair(5) | curses.A_BOLD)
        except curses.error:
            return False
        return True

    # ── hit testing ──────────────────────────────────────────────────────

    def _card_at(self, my, mx):
        for idx in range(len(self.filtered)):
            rect = self._card_rect(idx)
            if rect is None:
                continue
            y, x = rect
            if y <= my < y + self.CARD_H and x <= mx < x + self.CARD_W:
                return idx
        return None

    # ── new directory prompt ─────────────────────────────────────────────

    def _draw_new_dir_prompt(self, name, error=""):
        max_y, max_x = self.stdscr.getmaxyx()
        # Draw over the search bar area
        sy = max_y - 2
        where = f"$CODE_DIR/{self.rel}/" if self.rel else ""
        try:
            self.stdscr.addstr(sy, 0, " " * (max_x - 1), curses.color_pair(6))
            prompt = f"  New folder: {where}{name}"
            self.stdscr.addstr(sy, 0, prompt[:max_x - 2], curses.color_pair(6) | curses.A_BOLD)
            cx = len(prompt)
            if cx < max_x - 1:
                self.stdscr.addstr(sy, cx, "▌", curses.color_pair(8))
        except curses.error:
            pass

        # Show error if any
        if error:
            ey = max_y - 3
            try:
                self.stdscr.addstr(ey, 2, error[:max_x - 4], curses.color_pair(3) | curses.A_BOLD)
            except curses.error:
                pass

        self._draw_key_bar([("Enter", "Create"), ("Esc", "Cancel")], max_y, max_x)

        self.stdscr.refresh()

    def new_dir_prompt(self):
        """Show input prompt for new directory name. Returns name or None."""
        name = ""
        error = ""
        while True:
            self.draw()
            self._draw_new_dir_prompt(name, error)
            key = self._read_key()

            if key in (KEY_IGNORE, curses.KEY_MOUSE):
                continue

            elif key == 27:  # Esc
                return None

            elif key in ENTER_KEYS or key == KEY_SHIFT_ENTER:
                if not name.strip():
                    error = "Name cannot be empty"
                    continue
                dir_path = os.path.join(CODE_DIR, self.rel, name.strip())
                if os.path.exists(dir_path):
                    error = "Directory already exists"
                    continue
                try:
                    os.makedirs(dir_path)
                    return name.strip()
                except OSError as e:
                    error = f"Error: {e}"
                    continue

            elif key in (curses.KEY_BACKSPACE, 127, 8):
                if name:
                    name = name[:-1]
                    error = ""

            elif key == curses.KEY_RESIZE:
                pass

            elif 32 <= key <= 126:
                name += chr(key)
                error = ""

    # ── settings panel ───────────────────────────────────────────────────

    def _settings_geom(self):
        max_y, max_x = self.stdscr.getmaxyx()
        w = min(76, max_x - 4)
        h = len(SETTINGS_SPEC) + 6
        return max(1, (max_y - h) // 2), max(0, (max_x - w) // 2), w, h

    def _draw_settings(self, sel):
        y, x, w, h = self._settings_geom()
        if not self._draw_box(y, x, w, h, "Settings"):
            return None
        label_w = max(len(s[3]) for s in SETTINGS_SPEC) + 2
        for i, (key, _, choices, label, _) in enumerate(SETTINGS_SPEC):
            ry = y + 2 + i
            row = curses.color_pair(4) if i == sel else curses.color_pair(1)
            value = self.settings[key]
            try:
                self.stdscr.addstr(ry, x + 2, " " * (w - 4), row)
                self.stdscr.addstr(ry, x + 2, label, row | curses.A_BOLD)
                chip = f" {value} "
                attr = curses.color_pair(3) if value != "off" else curses.color_pair(10)
                self.stdscr.addstr(ry, x + 2 + label_w, chip, attr | curses.A_BOLD)
                rest = " · ".join(c for c in choices if c != value)
                if rest:
                    cx = x + 2 + label_w + len(chip) + 2
                    if cx + len(rest) < x + w - 2:
                        self.stdscr.addstr(ry, cx, rest, curses.color_pair(12))
            except curses.error:
                pass
        note = SETTINGS_NOTES.get(SETTINGS_SPEC[sel][0], SETTINGS_SPEC[sel][4])
        try:
            self.stdscr.addstr(y + h - 2, x + 2, note[:w - 4],
                               curses.color_pair(1) | curses.A_DIM)
        except curses.error:
            pass
        max_y, max_x = self.stdscr.getmaxyx()
        self._draw_key_bar(
            [("↑↓", "Move", 1), ("←→", "Change", 1), ("Esc", "Close", 0)],
            max_y, max_x,
        )
        self.stdscr.refresh()
        return y, x, w, h

    def _cycle_setting(self, index, delta):
        key, _, choices, label, _ = SETTINGS_SPEC[index]
        value = choices[(choices.index(self.settings[key]) + delta) % len(choices)]
        self.settings[key] = value
        try:
            save_settings(self.settings)
        except OSError as e:
            self.status = f"Could not save settings: {e}"
        # Apply the ones that change how the app behaves right now.
        if key == "hidden":
            here = self.filtered[self.selected] if self.filtered else None
            self._reload(select_name=here)
        elif key == "enhanced_keys":
            enhanced_keys(value == "on")
        elif key == "hover":
            mouse_motion(value == "on")
        self.status = f"{label}: {value}"

    def settings_panel(self):
        sel = 0
        while True:
            self.draw()
            geom = self._draw_settings(sel)
            key = self._read_key()

            if key in (KEY_IGNORE, curses.KEY_RESIZE):
                continue
            if key in (27, 19):                      # Esc, ^S again
                return
            if key == curses.KEY_UP:
                sel = (sel - 1) % len(SETTINGS_SPEC)
            elif key == curses.KEY_DOWN:
                sel = (sel + 1) % len(SETTINGS_SPEC)
            elif key == curses.KEY_LEFT:
                self._cycle_setting(sel, -1)
            elif key in (curses.KEY_RIGHT, 32) or key in ENTER_KEYS:
                self._cycle_setting(sel, 1)
            elif key == curses.KEY_MOUSE and geom:
                try:
                    _, mx, my, _, bstate = curses.getmouse()
                except curses.error:
                    continue
                y, x, w, _ = geom
                row = my - (y + 2)
                inside = x <= mx < x + w and 0 <= row < len(SETTINGS_SPEC)
                if inside:
                    sel = row
                    if bstate & (curses.BUTTON1_CLICKED | curses.BUTTON1_RELEASED):
                        self._cycle_setting(sel, 1)
                elif bstate & (curses.BUTTON1_CLICKED | curses.BUTTON1_RELEASED
                               | curses.BUTTON3_RELEASED):
                    return
            else:
                return

    # ── right-click menu ─────────────────────────────────────────────────

    def _menu_items(self, idx):
        """(label, action, destructive) for a right-click on card `idx`."""
        items = []
        live = []
        if idx is not None:
            name = self.filtered[idx]
            short = name if len(name) <= 20 else name[:19] + "…"
            live = self._card_sessions(name)
            if self._is_container(name):
                items.append((f"Browse {short}", "browse", False))
                items.append(("Open a session here", "open", False))
                items.append(("Unflag as folder group", "flag", False))
            else:
                items.append((f"Open {short}", "open", False))
                items.append(("Flag as folder group", "flag", False))
        items.append(("New folder here...", "new", False))
        items.append(("Settings...", "settings", False))
        if live:
            # Last, and in red: a right-click is one slip away from a misclick.
            if idx is not None and self._is_container(self.filtered[idx]):
                plural = "" if len(live) == 1 else "s"
                items.append((f"Close {len(live)} session{plural} inside", "close", True))
            elif len(live) == 1:
                items.append((f"Close session {short}", "close", True))
            else:
                items.append((f"Close {len(live)} sessions for {short}", "close", True))
        return items

    def _draw_menu(self, items, sel, my, mx):
        max_y, max_x = self.stdscr.getmaxyx()
        w = max(len(label) for label, *_ in items) + 4
        h = len(items) + 2
        y = my if my + h <= max_y else max(0, my - h)
        x = mx if mx + w <= max_x else max(0, max_x - w)
        if not self._draw_box(y, x, w, h):
            return None
        for i, (label, _, danger) in enumerate(items):
            if i == sel:
                attr = curses.color_pair(16 if danger else 4) | curses.A_BOLD
            else:
                attr = curses.color_pair(15) if danger else curses.color_pair(1)
            try:
                self.stdscr.addstr(y + 1 + i, x + 1, f" {label.ljust(w - 3)}", attr)
            except curses.error:
                pass
        self._draw_key_bar(
            [("↑↓", "Move", 1), ("Enter", "Choose", 0), ("Esc", "Close", 0)],
            max_y, max_x,
        )
        self.stdscr.refresh()
        return y, x, w, h

    def context_menu(self, idx, my, mx):
        """Right-click menu. Returns a target to open, or None."""
        items = self._menu_items(idx)
        sel = 0
        while True:
            self.draw()
            geom = self._draw_menu(items, sel, my, mx)
            if geom is None:
                return None
            key = self._read_key()

            if key in (KEY_IGNORE, curses.KEY_RESIZE):
                continue
            if key == 27:
                return None
            if key == curses.KEY_UP:
                sel = (sel - 1) % len(items)
                continue
            if key == curses.KEY_DOWN:
                sel = (sel + 1) % len(items)
                continue
            if key in ENTER_KEYS or key == KEY_SHIFT_ENTER:
                return self._menu_action(items[sel][1], idx)
            if key == curses.KEY_MOUSE:
                try:
                    _, mcx, mcy, _, bstate = curses.getmouse()
                except curses.error:
                    continue
                y, x, w, _ = geom
                row = mcy - (y + 1)
                inside = x <= mcx < x + w and 0 <= row < len(items)
                if inside:
                    sel = row
                    if bstate & (curses.BUTTON1_CLICKED | curses.BUTTON1_RELEASED):
                        return self._menu_action(items[sel][1], idx)
                elif bstate & (curses.BUTTON1_CLICKED | curses.BUTTON1_RELEASED):
                    return None         # a click outside dismisses it
                continue
            return None                 # any other key closes the menu

    def _menu_action(self, action, idx):
        name = self.filtered[idx] if idx is not None else None
        if action == "close" and name:
            live = self._card_sessions(name)
            if len(live) > 1:
                where = "inside" if self._is_container(name) else "for"
                if not self._confirm(f"Close {len(live)} sessions {where} {name}?",
                                     f"Close {len(live)}"):
                    return None
            self.close_sessions(live)
            return None
        if action == "open" and name:
            return self._target(name)
        if action == "browse" and name:
            self.enter_dir(name)
        elif action == "flag" and name:
            self.toggle_flag(name)
        elif action == "new":
            created = self.new_dir_prompt()
            if created:
                return self._target(created)
        elif action == "settings":
            self.settings_panel()
        return None

    def _confirm(self, question, yes_label):
        """A small two-button box in the middle of the screen."""
        choices = [(yes_label, True, True), ("Cancel", False, False)]
        sel = 1                             # Cancel, so Enter is never the rash one
        while True:
            self.draw()
            max_y, max_x = self.stdscr.getmaxyx()
            w = min(max(len(question) + 8, 34), max_x - 4)
            h = 6
            y, x = max(0, (max_y - h) // 2), max(0, (max_x - w) // 2)
            if not self._draw_box(y, x, w, h, "Close sessions"):
                return False
            spans = []
            try:
                self.stdscr.addstr(y + 2, x + 3, question[:w - 6],
                                   curses.color_pair(1) | curses.A_BOLD)
                bx = x + 3
                for i, (label, _, danger) in enumerate(choices):
                    text = f" {label} "
                    if i == sel:
                        attr = curses.color_pair(16 if danger else 4) | curses.A_BOLD
                    else:
                        attr = curses.color_pair(15) if danger else curses.color_pair(1)
                    self.stdscr.addstr(y + 4, bx, text, attr)
                    spans.append((bx, bx + len(text), i))
                    bx += len(text) + 2
            except curses.error:
                pass
            self._draw_key_bar(
                [("←→", "Choose", 1), ("Enter", "Confirm", 0), ("Esc", "Cancel", 0)],
                max_y, max_x,
            )
            self.stdscr.refresh()

            key = self._read_key()
            if key in (KEY_IGNORE, curses.KEY_RESIZE):
                continue
            if key == 27:
                return False
            if key in (curses.KEY_LEFT, curses.KEY_RIGHT, 9, curses.KEY_BTAB):
                sel = 1 - sel
            elif key in ENTER_KEYS or key == KEY_SHIFT_ENTER:
                return choices[sel][1]
            elif key in (ord("y"), ord("Y")):
                return True
            elif key in (ord("n"), ord("N")):
                return False
            elif key == curses.KEY_MOUSE:
                try:
                    _, mx, my, _, bstate = curses.getmouse()
                except curses.error:
                    continue
                hit = next((i for a, b, i in spans if my == y + 4 and a <= mx < b), None)
                if hit is not None:
                    sel = hit
                    if bstate & (curses.BUTTON1_CLICKED | curses.BUTTON1_RELEASED):
                        return choices[sel][1]
                elif bstate & (curses.BUTTON1_CLICKED | curses.BUTTON1_RELEASED):
                    return False
            else:
                return False

    # ── main loop ────────────────────────────────────────────────────────

    def _open_or_browse(self, idx):
        """Enter on a card: browse into a container, open anything else."""
        name = self.filtered[idx]
        if self._is_container(name):
            self.enter_dir(name)
            return None
        return self._target(name)

    def _handle_mouse(self):
        try:
            _, mx, my, _, bstate = curses.getmouse()
        except curses.error:
            return None
        idx = self._card_at(my, mx)

        if bstate & (curses.BUTTON3_PRESSED | curses.BUTTON3_CLICKED):
            if idx is not None:
                self.selected = idx
            return self.context_menu(idx, my, mx)

        if bstate & (curses.BUTTON1_CLICKED | curses.BUTTON1_DOUBLE_CLICKED
                     | curses.BUTTON1_RELEASED):
            if idx is not None:
                self.selected = idx
                return self._open_or_browse(idx)
            return None

        if bstate & curses.BUTTON1_PRESSED:
            if idx is not None:
                self.selected = idx
            return None

        cols = self._grid_cols()
        if bstate & curses.BUTTON4_PRESSED:          # wheel up
            self.selected = max(0, self.selected - cols)
        elif BUTTON5_PRESSED and bstate & BUTTON5_PRESSED:
            if self.selected + cols < len(self.filtered):
                self.selected += cols
        elif bstate & curses.REPORT_MOUSE_POSITION:
            if idx is not None and self.settings["hover"] == "on":
                self.selected = idx
        return None

    def run(self):
        while True:
            self.draw()
            key = self._read_key()
            if key == KEY_IGNORE:
                continue
            keep_status = key == curses.KEY_MOUSE and self.status
            if not keep_status:
                self.status = ""

            if key == 27:  # Esc — out of a container, or out of the app
                if not self.leave_dir():
                    return None

            elif key == 6:  # Ctrl+F
                if self.filtered:
                    self.toggle_flag(self.filtered[self.selected])

            elif key == 14:  # Ctrl+N
                result = self.new_dir_prompt()
                if result:
                    return self._target(result)

            elif key == 19:  # Ctrl+S
                self.settings_panel()

            elif key == curses.KEY_MOUSE:
                target = self._handle_mouse()
                if target:
                    return target

            elif key == curses.KEY_UP:
                cols = self._grid_cols()
                if self.selected >= cols:
                    self.selected -= cols

            elif key == curses.KEY_DOWN:
                cols = self._grid_cols()
                if self.selected + cols < len(self.filtered):
                    self.selected += cols

            elif key == curses.KEY_LEFT:
                if self.selected > 0:
                    self.selected -= 1

            elif key == curses.KEY_RIGHT:
                if self.selected < len(self.filtered) - 1:
                    self.selected += 1

            elif key == curses.KEY_HOME:
                self.selected = 0

            elif key == curses.KEY_END:
                self.selected = max(0, len(self.filtered) - 1)

            elif key in ENTER_KEYS:
                if self.filtered:
                    target = self._open_or_browse(self.selected)
                    if target:
                        return target

            elif key == KEY_SHIFT_ENTER:
                # Open the folder itself, container flag or not.
                if self.filtered:
                    return self._target(self.filtered[self.selected])

            elif key == 9:  # Tab
                comp = self.get_completion()
                if comp:
                    self.search = comp
                    self.update_filter()
                    if len(self.filtered) == 1:
                        self.selected = 0

            elif key in (curses.KEY_BACKSPACE, 127, 8):
                if self.search:
                    self.search = self.search[:-1]
                    self.update_filter()
                else:
                    self.leave_dir()

            elif key == curses.KEY_RESIZE:
                pass  # redraws on next iteration

            elif 32 <= key <= 126:
                self.search += chr(key)
                self.update_filter()


# ── colour setup ─────────────────────────────────────────────────────────────

def _init_colors():
    curses.start_color()
    curses.use_default_colors()

    if curses.COLORS >= 256:
        #                            FG               BG
        curses.init_pair(1,  curses.COLOR_WHITE,  235)   # card normal
        curses.init_pair(2,  curses.COLOR_WHITE,  237)   # card selected
        curses.init_pair(3,  232,                  78)    # active badge  (green bg)
        curses.init_pair(4,  255,                  239)   # open badge    (gray bg)
        curses.init_pair(5,  78,                   -1)    # title text    (green)
        curses.init_pair(6,  255,                  236)   # search bar
        curses.init_pair(7,  245,                  -1)    # help / muted
        curses.init_pair(8,  78,                   236)   # cursor in search
        curses.init_pair(9,  78,                   235)   # green border (selected)
        curses.init_pair(10, 255,                  60)    # "new" badge (muted purple)
        curses.init_pair(11, 141,                  235)   # purple border + flag star
        curses.init_pair(12, 97,                   235)   # purple border (unselected)
        curses.init_pair(13, 141,                  -1)    # breadcrumb text
        curses.init_pair(14, 232,                  141)   # browse badge (purple bg)
        curses.init_pair(15, 203,                  235)   # destructive menu entry
        curses.init_pair(16, 210,                  239)   # destructive, selected
    else:
        curses.init_pair(1,  curses.COLOR_WHITE,   curses.COLOR_BLACK)
        curses.init_pair(2,  curses.COLOR_BLACK,   curses.COLOR_WHITE)
        curses.init_pair(3,  curses.COLOR_BLACK,   curses.COLOR_GREEN)
        curses.init_pair(4,  curses.COLOR_WHITE,   curses.COLOR_BLACK)
        curses.init_pair(5,  curses.COLOR_GREEN,   -1)
        curses.init_pair(6,  curses.COLOR_WHITE,   curses.COLOR_BLACK)
        curses.init_pair(7,  curses.COLOR_WHITE,   -1)
        curses.init_pair(8,  curses.COLOR_GREEN,   curses.COLOR_BLACK)
        curses.init_pair(9,  curses.COLOR_GREEN,   curses.COLOR_BLACK)
        curses.init_pair(10, curses.COLOR_WHITE,   curses.COLOR_BLUE)
        curses.init_pair(11, curses.COLOR_MAGENTA, curses.COLOR_BLACK)
        curses.init_pair(12, curses.COLOR_MAGENTA, curses.COLOR_BLACK)
        curses.init_pair(13, curses.COLOR_MAGENTA, -1)
        curses.init_pair(14, curses.COLOR_BLACK,   curses.COLOR_MAGENTA)
        curses.init_pair(15, curses.COLOR_RED,     curses.COLOR_BLACK)
        curses.init_pair(16, curses.COLOR_RED,     curses.COLOR_BLACK)


# ── entry point ──────────────────────────────────────────────────────────────

def app(stdscr):
    _init_colors()
    curses.curs_set(0)
    curses.set_escdelay(25)
    curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
    stdscr.keypad(True)
    allow_ctrl_s()

    tui = NavTUI(stdscr)
    enhanced_keys(tui.settings["enhanced_keys"] == "on")
    mouse_motion(tui.settings["hover"] == "on")
    try:
        return tui.run()
    finally:
        mouse_motion(False)
        enhanced_keys(False)


def main():
    result = curses.wrapper(app)
    if not result:
        sys.exit(0)

    name, cwd = result
    sessions = list_sessions()

    # Session name == folder name: attach if it exists, else create it. A
    # project inside a flagged container keeps the bare folder name too, unless
    # an unrelated session already holds it — then it takes the path-qualified
    # name, which still resolves as $CODE_DIR/<session>.
    session = name
    start = sessions.get(session)
    if start is not None and not same_dir(start, cwd):
        qualified = os.path.relpath(cwd, CODE_DIR)
        if qualified != session:
            session = qualified
            start = sessions.get(session)

    if start is not None:
        argv = ["tmux", "attach-session", "-t", session_target(session)]
    else:
        argv = ["tmux", "new-session", "-s", session, "-c", cwd]
    try:
        os.execvp("tmux", argv)
    except OSError as e:
        sys.exit(f"tmux-quick-nav: could not run tmux: {e}")


if __name__ == "__main__":
    main()
