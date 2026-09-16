#!/usr/bin/env python3
"""Tmux Folder Quick Nav — terminal UI for navigating code_folder tmux sessions.

Shows project cards in a grid. Active sessions are highlighted and sorted to top.
Click a card or type + Tab to autocomplete, then Enter to open.

Tmux naming convention matches homelab_project:
  - Session name = folder name (e.g. "homelab_project")
  - CWD = ~/code_folder/<folder>
"""

import curses
import os
import subprocess
import sys

CODE_DIR = os.path.expanduser("~/code_folder")


# ── helpers ──────────────────────────────────────────────────────────────────

def get_projects():
    try:
        return sorted(
            name for name in os.listdir(CODE_DIR)
            if os.path.isdir(os.path.join(CODE_DIR, name))
        )
    except OSError:
        return []


def get_active_sessions():
    try:
        out = subprocess.check_output(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        return set(out.split("\n")) if out else set()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return set()


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
        self.projects = get_projects()
        self.active = get_active_sessions()
        self.selected = 0
        self.search = ""
        self.filtered = self._build_filtered()
        self.scroll_offset = 0

    # ── filtering / completion ───────────────────────────────────────────

    def _build_filtered(self):
        """Return project list filtered by search, active sessions first."""
        if self.search:
            items = [p for p in self.projects if self.search.lower() in p.lower()]
        else:
            items = list(self.projects)
        # Sort: active sessions first, then alphabetical
        return sorted(items, key=lambda p: (p not in self.active, p.lower()))

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

    # ── drawing ──────────────────────────────────────────────────────────

    def draw(self):
        self.stdscr.erase()
        max_y, max_x = self.stdscr.getmaxyx()
        cols = self._grid_cols()
        self._ensure_visible()

        # ── title bar ────────────────────────────────────────────────────
        title = " Projects"
        self.stdscr.addstr(1, 2, title, curses.color_pair(5) | curses.A_BOLD)

        active_n = sum(1 for p in self.filtered if p in self.active)
        info = f"{active_n} active · {len(self.filtered)} total"
        if max_x > len(info) + 4:
            self.stdscr.addstr(1, max_x - len(info) - 3, info, curses.color_pair(7))

        # ── cards ────────────────────────────────────────────────────────
        for idx, name in enumerate(self.filtered):
            row = idx // cols
            col = idx % cols
            drow = row - self.scroll_offset
            if drow < 0:
                continue
            y = self.HEADER_H + drow * (self.CARD_H + self.GAP_Y)
            x = 2 + col * (self.CARD_W + self.GAP_X)
            if y + self.CARD_H > max_y - self.FOOTER_H:
                break
            self._draw_card(y, x, name, idx == self.selected, max_y, max_x)

        # ── scroll indicator ─────────────────────────────────────────────
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

        # ── help bar ─────────────────────────────────────────────────────
        help_parts = [
            ("↑↓←→", "Navigate"),
            ("Type", "Search"),
            ("Tab", "Complete"),
            ("Enter", "Open"),
            ("^N", "New"),
            ("Esc", "Quit"),
        ]
        hx = 1
        hy = max_y - 1
        for key, action in help_parts:
            if hx + len(key) + len(action) + 3 >= max_x:
                break
            try:
                self.stdscr.addstr(hy, hx, f" {key}", curses.color_pair(6) | curses.A_BOLD)
                self.stdscr.addstr(hy, hx + len(key) + 1, f":{action} ", curses.color_pair(7))
            except curses.error:
                pass
            hx += len(key) + len(action) + 4

        self.stdscr.refresh()

    def _draw_card(self, y, x, name, selected, max_y, max_x):
        is_active = name in self.active
        w = self.CARD_W

        if selected:
            pair = curses.color_pair(2)
            bdr = curses.color_pair(9) | curses.A_BOLD   # green border
        else:
            pair = curses.color_pair(1)
            bdr = curses.color_pair(1)

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
        if badge_y < max_y:
            if is_active:
                try:
                    self.stdscr.addstr(badge_y, x + 2, " Open ", curses.color_pair(4))
                    self.stdscr.addstr(badge_y, x + 9, " Active ", curses.color_pair(3) | curses.A_BOLD)
                except curses.error:
                    pass
            else:
                try:
                    self.stdscr.addstr(badge_y, x + 2, " Open ", curses.color_pair(4))
                    self.stdscr.addstr(badge_y, x + 9, " New ", curses.color_pair(10))
                except curses.error:
                    pass

    # ── hit testing ──────────────────────────────────────────────────────

    def _card_at(self, my, mx):
        cols = self._grid_cols()
        for idx in range(len(self.filtered)):
            row = idx // cols - self.scroll_offset
            col = idx % cols
            y = self.HEADER_H + row * (self.CARD_H + self.GAP_Y)
            x = 2 + col * (self.CARD_W + self.GAP_X)
            if y <= my < y + self.CARD_H and x <= mx < x + self.CARD_W:
                return idx
        return None

    # ── new directory prompt ─────────────────────────────────────────────

    def _draw_new_dir_prompt(self, name, error=""):
        max_y, max_x = self.stdscr.getmaxyx()
        # Draw over the search bar area
        sy = max_y - 2
        try:
            self.stdscr.addstr(sy, 0, " " * (max_x - 1), curses.color_pair(6))
            prompt = f"  New folder: {name}"
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

        # Update help bar
        hy = max_y - 1
        try:
            self.stdscr.addstr(hy, 0, " " * (max_x - 1), curses.color_pair(7))
        except curses.error:
            pass
        help_parts = [("Enter", "Create"), ("Esc", "Cancel")]
        hx = 1
        for key_label, action in help_parts:
            if hx + len(key_label) + len(action) + 3 >= max_x:
                break
            try:
                self.stdscr.addstr(hy, hx, f" {key_label}", curses.color_pair(6) | curses.A_BOLD)
                self.stdscr.addstr(hy, hx + len(key_label) + 1, f":{action} ", curses.color_pair(7))
            except curses.error:
                pass
            hx += len(key_label) + len(action) + 4

        self.stdscr.refresh()

    def new_dir_prompt(self):
        """Show input prompt for new directory name. Returns name or None."""
        name = ""
        error = ""
        while True:
            self.draw()
            self._draw_new_dir_prompt(name, error)
            key = self.stdscr.getch()

            if key == 27:  # Esc
                return None

            elif key in (10, 13, curses.KEY_ENTER):
                if not name.strip():
                    error = "Name cannot be empty"
                    continue
                dir_path = os.path.join(CODE_DIR, name.strip())
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

    # ── main loop ────────────────────────────────────────────────────────

    def run(self):
        while True:
            self.draw()
            key = self.stdscr.getch()

            if key == 27:  # Esc
                return None

            elif key == 14:  # Ctrl+N
                result = self.new_dir_prompt()
                if result:
                    return result

            elif key == curses.KEY_MOUSE:
                try:
                    _, mx, my, _, bstate = curses.getmouse()
                    idx = self._card_at(my, mx)
                    if idx is not None:
                        if bstate & (curses.BUTTON1_CLICKED
                                     | curses.BUTTON1_DOUBLE_CLICKED
                                     | curses.BUTTON1_RELEASED):
                            self.selected = idx
                            return self.filtered[idx]
                        elif bstate & curses.BUTTON1_PRESSED:
                            self.selected = idx
                    # scroll wheel
                    if bstate & curses.BUTTON4_PRESSED:  # scroll up
                        cols = self._grid_cols()
                        if self.selected >= cols:
                            self.selected -= cols
                    elif bstate & curses.BUTTON5_PRESSED:  # scroll down
                        cols = self._grid_cols()
                        if self.selected + cols < len(self.filtered):
                            self.selected += cols
                except curses.error:
                    pass

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

            elif key in (10, 13, curses.KEY_ENTER):
                if self.filtered:
                    return self.filtered[self.selected]

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
    else:
        curses.init_pair(1,  curses.COLOR_WHITE,  curses.COLOR_BLACK)
        curses.init_pair(2,  curses.COLOR_BLACK,  curses.COLOR_WHITE)
        curses.init_pair(3,  curses.COLOR_BLACK,  curses.COLOR_GREEN)
        curses.init_pair(4,  curses.COLOR_WHITE,  curses.COLOR_BLACK)
        curses.init_pair(5,  curses.COLOR_GREEN,  -1)
        curses.init_pair(6,  curses.COLOR_WHITE,  curses.COLOR_BLACK)
        curses.init_pair(7,  curses.COLOR_WHITE,  -1)
        curses.init_pair(8,  curses.COLOR_GREEN,  curses.COLOR_BLACK)
        curses.init_pair(9,  curses.COLOR_GREEN,  curses.COLOR_BLACK)
        curses.init_pair(10, curses.COLOR_WHITE,  curses.COLOR_BLUE)


# ── entry point ──────────────────────────────────────────────────────────────

def app(stdscr):
    _init_colors()
    curses.curs_set(0)
    curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
    stdscr.keypad(True)

    nav = NavTUI(stdscr)
    return nav.run()


def main():
    result = curses.wrapper(app)
    if not result:
        sys.exit(0)

    cwd = os.path.join(CODE_DIR, result)

    # Match homelab_project convention: attach if exists, else create new
    try:
        subprocess.check_call(
            ["tmux", "has-session", "-t", result],
            stderr=subprocess.DEVNULL,
        )
        os.execvp("tmux", ["tmux", "attach-session", "-t", result])
    except subprocess.CalledProcessError:
        os.execvp("tmux", ["tmux", "new-session", "-s", result, "-c", cwd])


if __name__ == "__main__":
    main()
