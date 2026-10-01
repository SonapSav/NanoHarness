"""Keyboard input for the terminal UI: raw bytes in, keys out; a line editor; a menu.

Pure logic, no terminal: tui.py reads the bytes and draws, these decide what they mean.
"""
import codecs
from dataclasses import dataclass

ESC = "\x1b"
PASTE_START, PASTE_END = "\x1b[200~", "\x1b[201~"   # bracketed paste

# Escape sequences -> key names. Several terminals send different ones for the same key.
SEQUENCES = {
    "\x1b[A": "up", "\x1b[B": "down", "\x1b[C": "right", "\x1b[D": "left",
    "\x1bOA": "up", "\x1bOB": "down", "\x1bOC": "right", "\x1bOD": "left",
    "\x1b[H": "home", "\x1b[F": "end", "\x1bOH": "home", "\x1bOF": "end",
    "\x1b[1~": "home", "\x1b[4~": "end", "\x1b[7~": "home", "\x1b[8~": "end",
    "\x1b[3~": "delete", "\x1b[1;5D": "word-left", "\x1b[1;5C": "word-right",
    "\x1b\r": "newline", "\x1b\n": "newline",           # Alt+Enter
    "\x1b\x7f": "word-backspace",
}
CONTROL = {
    "\r": "enter", "\n": "newline",                     # Ctrl-J inserts a line break
    "\x7f": "backspace", "\x08": "backspace", "\t": "tab",
    "\x01": "home", "\x05": "end", "\x02": "left", "\x06": "right",
    "\x04": "ctrl-d", "\x03": "ctrl-c", "\x15": "ctrl-u", "\x0b": "ctrl-k",
    "\x17": "word-backspace", "\x0c": "ctrl-l", "\x0f": "ctrl-o",
}


@dataclass
class Key:
    name: str           # "char", "paste", "esc", or a key name from the tables above
    text: str = ""      # the character(s) for "char" and "paste"


class KeyParser:
    """Feed it bytes as they arrive; it returns the keys they complete. An escape that might
    start a sequence is held until more bytes come, or until `flush()` (the reader calls it
    after a short wait with nothing more: then it was the Esc key)."""

    def __init__(self):
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.pending = ""
        self.pasting = None   # text of a paste in progress

    def feed(self, data: bytes):
        self.pending += self.decoder.decode(data)
        keys = []
        while self.pending:
            if self.pasting is not None:
                end = self.pending.find(PASTE_END)
                if end < 0:
                    self.pasting += self.pending
                    self.pending = ""
                    break
                keys.append(Key("paste", (self.pasting + self.pending[:end]).replace("\r\n", "\n")
                                .replace("\r", "\n")))
                self.pasting, self.pending = None, self.pending[end + len(PASTE_END):]
                continue
            if self.pending.startswith(PASTE_START):
                self.pasting, self.pending = "", self.pending[len(PASTE_START):]
                continue
            if self.pending[0] == ESC:
                key, used = self._escape()
                if key is None:          # incomplete: wait for more
                    break
                keys.append(key)
                self.pending = self.pending[used:]
                continue
            ch, self.pending = self.pending[0], self.pending[1:]
            keys.append(Key(CONTROL[ch]) if ch in CONTROL else
                        Key("char", ch) if ch >= " " else Key("ignored", ch))
        return keys

    def flush(self):
        """Nothing more came: a held escape was the Esc key."""
        if self.pending == ESC:
            self.pending = ""
            return [Key("esc")]
        return []

    def _escape(self):
        p = self.pending
        if len(p) == 1:
            return None, 0
        for seq, name in SEQUENCES.items():
            if p.startswith(seq):
                return Key(name), len(seq)
        if p[1] in "[O":
            # CSI: parameters, then a final byte in @..~. Unknown ones are skipped whole.
            for i in range(2, len(p)):
                if "@" <= p[i] <= "~":
                    return Key("ignored", p[:i + 1]), i + 1
            return None, 0       # no final byte yet
        return Key("esc"), 1     # Esc followed by an ordinary key: take the Esc alone


class LineEditor:
    """The input box's text: a list of lines and a cursor. `key()` returns "submit" when
    Enter is pressed on non-empty text, else None."""

    def __init__(self, history=None):
        self.history = history if history is not None else []
        self.clear()

    def clear(self):
        self.lines, self.row, self.col = [""], 0, 0
        self.browsing = None     # index into history while browsing with up/down
        self.draft = None        # what was typed before browsing started

    @property
    def text(self):
        return "\n".join(self.lines)

    def set_text(self, text):
        self.lines = text.split("\n") or [""]
        self.row = len(self.lines) - 1
        self.col = len(self.lines[-1])

    def insert(self, text):
        line = self.lines[self.row]
        before, after = line[:self.col], line[self.col:]
        parts = (before + text).split("\n")
        self.lines[self.row:self.row + 1] = parts[:-1] + [parts[-1] + after]
        self.row += len(parts) - 1
        self.col = len(parts[-1])

    def key(self, k: Key):
        line = self.lines[self.row]
        n = k.name
        if n == "char":
            self.insert(k.text)
        elif n == "paste":
            self.insert(k.text)
        elif n == "newline":
            self.insert("\n")
        elif n == "enter":
            if self.text.strip():
                return "submit"
        elif n == "backspace":
            if self.col:
                self.lines[self.row] = line[:self.col - 1] + line[self.col:]
                self.col -= 1
            elif self.row:
                prev = self.lines[self.row - 1]
                self.lines[self.row - 1:self.row + 1] = [prev + line]
                self.row, self.col = self.row - 1, len(prev)
        elif n == "delete":
            if self.col < len(line):
                self.lines[self.row] = line[:self.col] + line[self.col + 1:]
            elif self.row < len(self.lines) - 1:
                self.lines[self.row:self.row + 2] = [line + self.lines[self.row + 1]]
        elif n == "word-backspace":
            start = line[:self.col].rstrip(" ").rfind(" ") + 1   # start of the word before
            self.lines[self.row] = line[:start] + line[self.col:]
            self.col = start
        elif n == "left":
            if self.col:
                self.col -= 1
            elif self.row:
                self.row -= 1
                self.col = len(self.lines[self.row])
        elif n == "right":
            if self.col < len(line):
                self.col += 1
            elif self.row < len(self.lines) - 1:
                self.row, self.col = self.row + 1, 0
        elif n == "home":
            self.col = 0
        elif n == "end":
            self.col = len(line)
        elif n == "ctrl-u":
            self.lines[self.row] = line[self.col:]
            self.col = 0
        elif n == "ctrl-k":
            self.lines[self.row] = line[:self.col]
        elif n == "up":
            if self.row:
                self.row -= 1
                self.col = min(self.col, len(self.lines[self.row]))
            else:
                self._browse(-1)
        elif n == "down":
            if self.row < len(self.lines) - 1:
                self.row += 1
                self.col = min(self.col, len(self.lines[self.row]))
            else:
                self._browse(+1)
        return None

    def _browse(self, step):
        """Up on the first line / down on the last walks the input history."""
        if not self.history:
            return
        if self.browsing is None:
            if step > 0:
                return
            self.draft, self.browsing = self.text, len(self.history)
        self.browsing += step
        if self.browsing >= len(self.history):
            self.browsing, text = None, self.draft or ""
        else:
            self.browsing = max(0, self.browsing)
            text = self.history[self.browsing]
        self.set_text(text)
        if step < 0:
            self.row, self.col = 0, len(self.lines[0])

    def remember(self, text):
        if text.strip() and (not self.history or self.history[-1] != text):
            self.history.append(text)


class Menu:
    """A short list of options, picked with up/down and Enter, or a shortcut letter."""

    def __init__(self, options, shortcuts=None, cancel=None):
        self.options = options
        self.shortcuts = shortcuts or {}   # letter -> index
        self.cancel = cancel               # index Esc/Ctrl-C choose, or None
        self.index = 0

    def key(self, k: Key):
        """The chosen index, or None while still choosing."""
        if k.name == "up":
            self.index = (self.index - 1) % len(self.options)
        elif k.name == "down":
            self.index = (self.index + 1) % len(self.options)
        elif k.name == "enter":
            return self.index
        elif k.name in ("esc", "ctrl-c") and self.cancel is not None:
            return self.cancel
        elif k.name == "char" and k.text.lower() in self.shortcuts:
            return self.shortcuts[k.text.lower()]
        elif k.name == "char" and k.text.isdigit() and 1 <= int(k.text) <= len(self.options):
            return int(k.text) - 1
        return None
