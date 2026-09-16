-- notepad.lua — Neovim config for the tmux project notepad.
-- Loaded via:  nvim --clean -c 'luafile <this file>'   (see notepad.sh)
-- `--clean` skips the user's LazyVim config, so everything here is isolated and
-- nothing external can override these keys. Designed to be used entirely from
-- insert mode: every action is an Alt-chord, so you never need normal mode (and
-- therefore never reach for <Esc>, which discards).

local A = vim.api
local map = vim.keymap.set
local opt = vim.opt

-- ── look & feel ──────────────────────────────────────────────────────────────
opt.wrap = true
opt.linebreak = true       -- wrap on word boundaries, not mid-word
opt.breakindent = true     -- keep wrapped lines visually aligned
opt.number = false
opt.signcolumn = "no"

-- ── close actions ────────────────────────────────────────────────────────────
-- <Cmd> runs the command from any mode without first leaving insert.
map({ "n", "i" }, "<M-n>", "<Cmd>wqa!<CR>", { silent = true })  -- save & close
map({ "n", "i" }, "<Esc>", "<Cmd>qa!<CR>",  { silent = true })  -- discard & close

-- ── checkboxes ───────────────────────────────────────────────────────────────

-- Insert a fresh "- [ ] " item on a new line below, cursor ready to type.
local function new_checkbox()
  local cur = A.nvim_get_current_line()
  local indent = cur:match("^%s*") or ""
  local prefix = indent .. "- [ ] "
  local row = A.nvim_win_get_cursor(0)[1]
  A.nvim_buf_set_lines(0, row, row, true, { prefix })
  A.nvim_win_set_cursor(0, { row + 1, #prefix })
  vim.cmd("startinsert!")   -- ! = position at end of line
end

-- Flip the checkbox state on the current line: [ ] <-> [x]. ([ ] and [x] are the
-- same width, so the cursor stays put.) Returns false if the line has no box.
local function toggle_checkbox()
  local line = A.nvim_get_current_line()
  local out
  if line:find("%[ %]") then
    out = line:gsub("%[ %]", "[x]", 1)
  elseif line:find("%[[xX]%]") then
    out = line:gsub("%[[xX]%]", "[ ]", 1)
  else
    return false
  end
  A.nvim_set_current_line(out)
  return true
end

map({ "n", "i" }, "<M-c>", new_checkbox, { silent = true, desc = "New checkbox" })
map({ "n", "i" }, "<M-x>", function() toggle_checkbox() end,
  { silent = true, desc = "Toggle checkbox" })

-- "/c" typed in insert mode expands to a checkbox prefix (slash-command flavor).
-- nvim only allows a single keyword char after a non-keyword lead, so it must be
-- "/c", not "/cb". No trailing space: the char that triggers expansion adds it.
-- pcall so a bad abbrev can never abort the rest of this file (e.g. startinsert).
pcall(vim.cmd, "inoreabbrev <buffer> /c - [ ]")

-- Open at the end of the notepad, ready to append new notes below existing ones.
vim.cmd("normal! G")     -- jump to the last line
vim.cmd("startinsert!")  -- ! = enter insert at end of line (like `A`)
