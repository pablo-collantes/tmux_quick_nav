#!/bin/bash
#
# Print the project directory of the tmux session in the focused Hyprland
# window.
#
#   tmux-project-dir.sh          project root  (default)
#   tmux-project-dir.sh --pane   the active pane's exact cwd
#
# Prints nothing and exits 1 when the focused window has no tmux client
# attached, so callers can fall back to their normal "no project" behaviour.
#
# Why this exists: omarchy-cmd-terminal-cwd reads the cwd of the terminal's
# child process and requires that child to be a login shell (/etc/shells).
# Inside tmux the child is `tmux: client`, the shell check fails, and it
# returns $HOME. The real cwd lives in the tmux *server*, which is what we ask
# here.

set -uo pipefail

CODE_DIR="${CODE_DIR:-$HOME/code_folder}"
CONTAINERS="${XDG_CONFIG_HOME:-$HOME/.config}/tmux-quick-nav/containers"

mode="root"
[[ ${1:-} == "--pane" ]] && mode="pane"

for dep in tmux hyprctl jq; do command -v "$dep" >/dev/null || exit 1; done

win_pid=$(hyprctl activewindow -j 2>/dev/null | jq -r '.pid // empty')
[[ $win_pid =~ ^[0-9]+$ ]] && ((win_pid > 1)) || exit 1

# A tmux client runs as a descendant of the terminal emulator, and the
# emulator's pid is the one Hyprland reports for the window.
descends_from_window() {
  local pid=$1 hops=0
  while [[ $pid =~ ^[0-9]+$ ]] && ((pid > 1 && hops < 16)); do
    ((pid == win_pid)) && return 0
    pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d '[:space:]')
    ((hops++))
  done
  return 1
}

session=""
while read -r client_pid client_session; do
  [[ -n ${client_pid:-} ]] || continue
  if descends_from_window "$client_pid"; then
    session=$client_session
    break
  fi
done < <(tmux list-clients -F '#{client_pid} #{client_session}' 2>/dev/null)

[[ -n $session ]] || exit 1

# Matched by exact field rather than a `-t` target string, which tmux parses
# (a session name is not a safe target literal).
path=$(tmux list-panes -a -F $'#{session_name}\t#{window_active}\t#{pane_active}\t#{pane_current_path}' 2>/dev/null |
  awk -F'\t' -v s="$session" '$1 == s && $2 == "1" && $3 == "1" { print $4; exit }')

[[ -d $path ]] || exit 1

if [[ $mode == "pane" ]]; then
  printf '%s\n' "$path"
  exit 0
fi

# A folder flagged as a container in nav_tui.py (^F) groups projects rather
# than being one, so the project root sits a level deeper inside it.
is_container() {
  local line
  [[ -f $CONTAINERS ]] || return 1
  while IFS= read -r line; do
    [[ -z $line || $line == \#* ]] && continue
    [[ $line == "$1" ]] && return 0
  done < "$CONTAINERS"
  return 1
}

# Inside ~/code_folder the top-level folder is the project -- that is the
# convention nav_tui.py creates sessions with (session name == folder name) --
# unless it is a container, in which case descend until it is not.
if [[ $path == "$CODE_DIR"/* ]]; then
  rest=${path#"$CODE_DIR"/}
  proj=""
  while :; do
    proj="${proj:+$proj/}${rest%%/*}"
    if [[ $rest == */* ]] && is_container "$proj"; then
      rest=${rest#*/}
    else
      break
    fi
  done
  printf '%s/%s\n' "$CODE_DIR" "$proj"
  exit 0
fi

# Anywhere else, the enclosing git repo is the best guess at a project root.
if root=$(git -C "$path" rev-parse --show-toplevel 2>/dev/null) && [[ -d $root ]]; then
  printf '%s\n' "$root"
  exit 0
fi

printf '%s\n' "$path"
