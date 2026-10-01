"""The terminal look, after Claude Code and Pi: inline, so the conversation stays in the
terminal's own scrollback and only the last line (the line being written, or a spinner) is
redrawn in place.

    ⏺ Write(weather.py)
      ⎿  Wrote 60 lines
    ✻ Running… 4s

RichUI is both the UI (ui.py's interface) and the REPL's on_token stream printer. Stdlib only.
"""
import re
import shutil
import sys
import threading
import time

from . import tools, ui

DIM, BOLD, ITALIC, RESET = "\033[2m", "\033[1m", "\033[3m", "\033[0m"
GREY, RED, GREEN, YELLOW, CYAN = "\033[90m", "\033[31m", "\033[32m", "\033[33m", "\033[36m"
ORANGE = "\033[38;5;208m"
SPIN = "·✢✳✶✻✽✻✶✳✢"
ANSI = re.compile(r"\x1b\[[0-9;]*m")

RESULT_LINES = 4        # output lines shown under a tool call
DIFF_LINES = 20         # diff lines shown under an edit
QUIET = 1.0             # seconds without output before the spinner appears

TITLES = {"read_file": "Read", "write_file": "Write", "edit_file": "Edit", "bash": "Bash",
          "glob": "Glob", "grep": "Grep", "task": "Task", "search_history": "SearchHistory"}
MAIN_ARG = {"read_file": "path", "write_file": "path", "edit_file": "path", "bash": "command",
            "glob": "pattern", "grep": "pattern", "task": "prompt", "search_history": "pattern"}


def visible(text):
    return len(ANSI.sub("", text))


def inline(text):
    """**bold** and `code` inside a line."""
    text = re.sub(r"`([^`]+)`", lambda m: f"{CYAN}{m.group(1)}\033[39m", text)
    return re.sub(r"\*\*([^*]+)\*\*", lambda m: f"{BOLD}{m.group(1)}\033[22m", text)


class Markdown:
    """Line-at-a-time markdown: enough for a model's answers (headings, lists, code)."""

    def __init__(self):
        self.code = False

    def line(self, text):
        if text.strip().startswith("```"):
            self.code = not self.code
            return f"{GREY}{text.strip()}{RESET}"
        if self.code:
            return f"{CYAN}{text}{RESET}"
        if m := re.match(r"#{1,6}\s+(.*)", text):
            return f"{BOLD}{inline(m.group(1))}{RESET}"
        if m := re.match(r"(\s*)[-*]\s+(.*)", text):
            return f"{m.group(1)}• {inline(m.group(2))}"
        if m := re.match(r">\s?(.*)", text):
            return f"{GREY}│ {inline(m.group(1))}{RESET}"
        return inline(text)


def title(name, args):
    """`Write(weather.py)`: the tool, and its one argument that says what it's about."""
    if isinstance(args, str):
        args = {"arguments": args}
    main = str((args or {}).get(MAIN_ARG.get(name, ""), "") or "")
    main = " ".join(main.split())
    if len(main) > 70:
        main = main[:67] + "…"
    return f"{TITLES.get(name, name)}({main})"


def colour_diff(lines):
    out = []
    for line in lines:
        bare = line.strip()
        if bare.startswith("+") and not bare.startswith("+++"):
            out.append(f"{GREEN}{line}{RESET}")
        elif bare.startswith("-") and not bare.startswith("---"):
            out.append(f"{RED}{line}{RESET}")
        elif bare.startswith("@@"):
            out.append(f"{CYAN}{line}{RESET}")
        else:
            out.append(f"{GREY}{line}{RESET}")
    return out


def summary(name, result):
    """The lines shown under a finished tool call."""
    text = str(result).split("\n\n[You already made this exact call", 1)[0]
    lines = text.rstrip().splitlines() or [""]
    if text.startswith("Error"):
        return [f"{RED}{lines[0][:200]}{RESET}"]
    if name == "read_file":
        n = sum(1 for l in lines if re.match(r"\s*\d+\t", l))
        return [f"Read {n} lines" if n else lines[0]]
    if name in ("write_file", "edit_file"):
        return [lines[0]]
    if name == "bash":
        code, _, body = text.partition("\n\n")
        body = [l for l in body.splitlines() if l.strip()] or ["(no output)"]
        if len(body) <= RESULT_LINES + 1:     # "+1 lines" takes as much room as the line
            shown = body
        else:
            shown = body[:RESULT_LINES] + [f"{GREY}… +{len(body) - RESULT_LINES} lines{RESET}"]
        if code.strip() != "exit code: 0":
            shown.insert(0, f"{RED}{code.strip()}{RESET}")
        return shown
    if name == "task":
        return ["Done" + (f": {lines[0][:150]}" if lines[0] else "")]
    return [lines[0][:200]] + ([f"{GREY}… +{len(lines) - 1} lines{RESET}"] if len(lines) > 1 else [])


class RichUI(ui.PlainUI):
    """See the module docstring. `out`, `width` and `ticker` are for tests."""

    def __init__(self, out=None, width=None, ticker=None):
        self.out = out or sys.stdout
        self._width = width
        self.lock = threading.RLock()
        self.live = ""          # what is on the bottom line right now (redrawn in place)
        self.running = []       # tool calls in progress, innermost last
        self.diffs = {}         # depth -> diff of the edit in flight, until shown
        self.reset()
        if self.out.isatty() if ticker is None else ticker:
            threading.Thread(target=self._tick, daemon=True).start()

    # --- the REPL's stream printer interface (see cli.StreamPrinter) -------------------

    def reset(self):
        with self.lock:
            self.open = False         # a model reply is on its way
            self.quiet_since = None
            self.thinking_since = None
            self.partial = ""         # the answer line being written
            self.block_started = False
            self.md = Markdown()
            self.parts = []
            self.last = None

    def stop(self):
        with self.lock:
            self.open = False
            self.running.clear()
            self._flush_partial()
            self._draw("")

    def break_line(self):
        self.stop()

    def __call__(self, kind, text):
        with self.lock:
            now = time.monotonic()
            if kind == "start":
                self.open, self.quiet_since, self.thinking_since = True, now, None
                self.block_started, self.md = False, Markdown()
                return
            if kind == "end":
                self._end_thinking(now)
                self._flush_partial()
                self.open = False
                self.last = "".join(self.parts).strip()
                self.parts = []
                self._draw("")
                return
            self.quiet_since = now
            if kind == "thinking":
                if self.thinking_since is None:
                    self.thinking_since = now
                return
            self._end_thinking(now)
            self.parts.append(text)
            self.partial += text
            while "\n" in self.partial:
                line, self.partial = self.partial.split("\n", 1)
                self._answer_line(line)
            self._wrap_partial()
            self._draw(self._partial_text())

    # --- ui.py's interface ----------------------------------------------------------

    def note(self, text, depth=0):
        self._commit(f"{GREY}  ⎿  {text}{RESET}")

    def narration(self, text):
        for line in text.splitlines():
            self._answer_line(line)

    def tool_call(self, name, args, depth=0):
        with self.lock:
            self._flush_partial()
            if isinstance(args, dict) and name in ("write_file", "edit_file"):
                try:   # computed before the call runs, so it shows what is about to change
                    self.diffs[depth] = tools.REGISTRY[name].preview(**args).splitlines()[1:]
                except Exception:
                    self.diffs.pop(depth, None)
            if depth:
                self._commit(f"{GREY}  ⎿  {'    ' * (depth - 1)}↳ {title(name, args)}{RESET}")
            else:
                self._commit(f"{GREEN}⏺{RESET} {BOLD}{title(name, args)}{RESET}")
            self.running.append((name, time.monotonic()))

    def tool_result(self, name, args, result, depth=0):
        with self.lock:
            if self.running:
                self.running.pop()
            if depth:
                if str(result).startswith("Error"):
                    self._commit(f"{GREY}  ⎿      {RESET}{RED}{str(result).splitlines()[0][:150]}{RESET}")
                return
            lines = summary(name, result)
            diff = self.diffs.pop(depth, None)
            if diff and not str(result).startswith("Error"):
                shown = [l[4:] if l.startswith("    ") else l for l in diff][:DIFF_LINES]
                lines += colour_diff(shown)
                if len(diff) > DIFF_LINES:
                    lines.append(f"{GREY}… +{len(diff) - DIFF_LINES} diff lines{RESET}")
            for i, line in enumerate(lines):
                self._commit(("  ⎿  " if i == 0 else "     ") + line)
            self._draw("")

    def permission(self, who, preview, tool, args):
        with self.lock:
            self._draw("")
            head, *diff = preview.splitlines()
            if who:   # the main agent's header is just above; a subagent's call needs naming
                self._commit(f"{YELLOW}  ? {who}{title(tool.name, args)}{RESET}")
            for line in colour_diff([l[4:] if l.startswith("    ") else l for l in diff]):
                self._commit("     " + line)
            self.diffs.pop(1 if who else 0, None)    # shown here; not again under the result
            paused = self.running
            self.running = []                         # no spinner over the question
        try:
            # \001..\002 mark colour codes as zero-width for readline's line editing.
            z = lambda code: f"\001{code}\002"
            return input(f"  {z(YELLOW)}⎿{z(RESET)}  {z(BOLD)}allow?{z(RESET)} "
                         "[y]es / [n]o / [a]lways for this tool: ")
        finally:
            with self.lock:
                self.running = paused
                self.quiet_since = time.monotonic()

    def review(self, faked, reason):
        if faked:
            self._commit(f"{YELLOW}⚠ Review: this may make a check pass without fixing it: "
                         f"{reason}{RESET}")

    def review_failed(self, error):
        self._commit(f"{GREY}  (review failed: {error}){RESET}")

    def warning(self, text):
        self._commit(f"{RED}  ({text}){RESET}")

    # --- drawing -------------------------------------------------------------------

    def width(self):
        return self._width or shutil.get_terminal_size((100, 24)).columns

    def _answer_line(self, line):
        prefix = "  " if self.block_started else f"{BOLD}⏺{RESET} "
        if line.strip() or self.block_started:
            self.block_started = True      # before the commit, which redraws the next line
            self._commit(prefix + self.md.line(line))

    def _partial_text(self):
        if not self.partial or "\n" in self.partial:   # mid-split: drawn once it is one line
            return ""
        return ("  " if self.block_started else f"{BOLD}⏺{RESET} ") + inline(self.partial)

    def _wrap_partial(self):
        """Commit words that would overflow the line, so the live line never wraps."""
        room = self.width() - 4
        while len(self.partial) > room:
            cut = self.partial.rfind(" ", 0, room)
            cut = cut if cut > 0 else room
            head, self.partial = self.partial[:cut], self.partial[cut:].lstrip(" ")
            self._answer_line(head)

    def _flush_partial(self):
        rest, self.partial = self.partial, ""
        if rest.strip():
            self._answer_line(rest)

    def _end_thinking(self, now):
        if self.thinking_since is not None:
            secs = max(1, round(now - self.thinking_since))
            self.thinking_since = None
            self._commit(f"{GREY}✻ Thought for {secs}s{RESET}")

    def _commit(self, line):
        """Write a finished line into scrollback, above the live line."""
        with self.lock:
            self._draw("", flush=False)
            self.out.write(line + "\n")
            self._draw(self._partial_text())

    def _draw(self, text, flush=True):
        """Replace the live line with `text` (may be empty)."""
        if text == self.live and flush:
            return
        if self.live or text:
            self.out.write("\r\033[2K" + text)
        self.live = text
        if flush:
            self.out.flush()

    def status_text(self, now=None):
        """The spinner line for now, or None when it shouldn't show."""
        now = now or time.monotonic()
        frame = SPIN[int(now * 6) % len(SPIN)]
        if self.thinking_since is not None:
            return f"{ORANGE}{frame} Thinking… {int(now - self.thinking_since)}s{RESET}"
        if self.running:
            name, since = self.running[-1]
            return f"{ORANGE}{frame} Running {TITLES.get(name, name)}… {int(now - since)}s{RESET}"
        if self.open and self.quiet_since is not None and not self.partial:
            quiet = now - self.quiet_since
            if quiet >= QUIET:
                return f"{ORANGE}{frame} Working… {int(quiet)}s{RESET}"
        return None

    def _tick(self):
        while True:
            time.sleep(0.15)
            with self.lock:
                if getattr(self, "resized", False):
                    self._render()
                line = self.status_text()
                if line is not None:
                    self._draw(line)
                elif self.live and not self.partial:
                    self._draw("")


# --- phase 2: the live bottom area ------------------------------------------------------

class Terminal:
    """Key-at-a-time input with no echo and no signal keys, so Ctrl-C and Esc arrive as keys
    (the UI decides what they mean), bracketed paste, and a hidden cursor (the input box draws
    its own). `restore()` puts everything back; it is safe to call twice."""

    def __init__(self, fd, out):
        import termios
        self.termios, self.fd, self.out = termios, fd, out
        self.saved = termios.tcgetattr(fd)
        mode = termios.tcgetattr(fd)
        mode[0] &= ~(termios.ICRNL | termios.IXON)            # Enter stays \r; Ctrl-S/Q are keys
        mode[3] &= ~(termios.ICANON | termios.ECHO | termios.ISIG | termios.IEXTEN)
        mode[6][termios.VMIN], mode[6][termios.VTIME] = 1, 0
        termios.tcsetattr(fd, termios.TCSADRAIN, mode)
        out.write("\033[?2004h\033[?25l")
        out.flush()
        self.active = True

    def restore(self):
        if self.active:
            self.active = False
            self.termios.tcsetattr(self.fd, self.termios.TCSADRAIN, self.saved)
            self.out.write("\033[?2004l\033[?25h")
            self.out.flush()


class Stdout:
    """Stands in for sys.stdout while the bottom area is on screen: whatever else prints (the
    REPL's errors, /sessions, /status) goes into scrollback above it, not over it."""

    def __init__(self, ui, real):
        self.ui, self.real, self.buffer = ui, real, ""
        self.encoding = getattr(real, "encoding", "utf-8")

    def write(self, text):
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            self.ui._commit(line)
        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return True

    def fileno(self):
        return self.real.fileno()


class LiveUI(RichUI):
    """RichUI with a bottom area that stays put while the conversation scrolls above it:

        ✻ Working… 12s
        ╭──────────────────────────────────────╮
        │ > fix the failing test█              │
        ╰──────────────────────────────────────╯
          aeroadvisor-agent · ctx 3%          esc to interrupt

    The REPL reads input with `read_line()`, permission prompts and pickers become arrow-key
    menus, Esc or Ctrl-C interrupt a turn, and Enter during a turn queues the message.
    `start()` takes over the terminal, `close()` hands it back."""

    BOX_LINES = 8          # input lines shown before the box scrolls

    def __init__(self, out=None, width=None, ticker=None, history=None):
        from .keys import KeyParser, LineEditor
        self.drawn = 0             # lines of the bottom area on screen now
        self.top = ""              # the line above the box: spinner or the answer being written
        self.editor = LineEditor(history)
        self.parser = KeyParser()
        self.menu = None           # (title, options, Menu) while a menu is up
        self.busy = False          # a turn is running
        self.queued = []           # messages sent with Enter during a turn
        self.footer = ""           # left side of the footer, set by the REPL
        self.exit_armed = 0.0      # when Ctrl-C was pressed on an empty box
        self.terminal = None
        self.real_stdout = None
        self.resized = False
        super().__init__(out=out, width=width, ticker=ticker)
        import threading as _t
        self.submitted = _t.Condition(self.lock)
        self.result = None

    # --- terminal ownership ------------------------------------------------------------

    def start(self, fd):
        """Take the terminal: key-at-a-time input, stdout through us, a key-reader thread."""
        self.terminal = Terminal(fd, self.out)
        self.real_stdout, sys.stdout = sys.stdout, Stdout(self, self.out)
        import signal
        try:   # only a flag: a redraw inside a signal handler could cut another one in half
            signal.signal(signal.SIGWINCH, lambda *a: setattr(self, "resized", True))
        except (ValueError, OSError):
            pass
        threading.Thread(target=self._read_keys, args=(fd,), daemon=True).start()
        self._render()

    def close(self):
        with self.lock:
            self._clear_region()
            self.drawn = 0
            self.out.flush()
        if self.real_stdout is not None:
            sys.stdout, self.real_stdout = self.real_stdout, None
        if self.terminal:
            self.terminal.restore()

    # --- what the REPL calls -------------------------------------------------------------

    def read_line(self):
        """The next message: a queued one, or what is typed. EOFError on Ctrl-D (or Ctrl-C
        twice) on an empty box."""
        with self.lock:
            if self.queued:
                result = self.queued.pop(0)
            else:
                self.result = None
                self._render()
                while self.result is None:
                    self.submitted.wait()
                result, self.result = self.result, None
            if result is EOFError:
                raise EOFError
            for i, line in enumerate(result.split("\n")):   # the message, into the conversation
                self._commit((f"{BOLD}>{RESET} " if i == 0 else "  ") + line)
        return result

    def set_busy(self, busy):
        with self.lock:
            self.busy = busy
            self._render()

    def choose(self, title, options, shortcuts=None, cancel=None):
        """An arrow-key menu in place of the input box. Returns the chosen index."""
        from .keys import Menu
        with self.lock:
            self._draw_top("")
            self.menu = (title, options, Menu(options, shortcuts, cancel))
            self.result = None
            self._render()
            while self.result is None:
                self.submitted.wait()
            chosen, self.result, self.menu = self.result, None, None
            self._render()
            return chosen

    def permission(self, who, preview, tool, args):
        with self.lock:
            head, *diff = preview.splitlines()
            if who:
                self._commit(f"{YELLOW}  ? {who}{title(tool.name, args)}{RESET}")
            for line in colour_diff([l[4:] if l.startswith("    ") else l for l in diff]):
                self._commit("     " + line)
            self.diffs.pop(1 if who else 0, None)
            paused, self.running = self.running, []
        try:
            name = TITLES.get(tool.name, tool.name)
            index = self.choose(f"Allow {who}{title(tool.name, args)}?",
                                ["Yes", f"Yes, and don't ask again for {name} this session",
                                 "No, and tell the agent"],
                                shortcuts={"y": 0, "a": 1, "n": 2}, cancel=2)
            answer = "yan"[index]                  # in the order of the options above
            self._commit(f"  {YELLOW}⎿{RESET}  {['allowed', 'allowed always', 'denied'][index]}")
            return answer
        finally:
            with self.lock:
                self.running = paused
                self.quiet_since = time.monotonic()

    # --- drawing -------------------------------------------------------------------------

    def _draw(self, text, flush=True):
        self._draw_top(text)

    def _draw_top(self, text):
        self.live = text            # RichUI's ticker reads `live` to know a spinner is up
        if text != self.top:
            self.top = text
            self._render()

    def _commit(self, line):
        with self.lock:
            self._clear_region()
            self.out.write(line + "\n")
            self.drawn = 0
            self._render()

    def _clear_region(self):
        if self.drawn:
            self.out.write("\r" + "\033[1A" * (self.drawn - 1) + "\033[J")
        self.drawn = 0

    def _render(self):
        with self.lock:
            self.resized = False
            if self.terminal is None:          # not started (tests drive region() directly)
                return
            lines = self.region()
            self._clear_region()
            self.out.write("\n".join(lines))
            self.drawn = len(lines)
            self.out.flush()

    def region(self):
        """The bottom area's lines, top to bottom."""
        width = max(20, self.width() - 1)      # never the last column: it would auto-wrap
        inner = width - 4
        lines = [self.top] if self.top else []
        lines.append(f"{GREY}╭{'─' * (width - 2)}╮{RESET}")
        if self.menu:
            heading, options, menu = self.menu
            lines.append(self._boxed(f"{BOLD}{heading}{RESET}", inner))
            for i, option in enumerate(options):
                mark = f"{ORANGE}❯{RESET} " if i == menu.index else "  "
                lines.append(self._boxed(f"{mark}{i + 1}. {option}", inner))
        else:
            lines += [self._boxed(l, inner) for l in self._input_lines(inner)]
        lines.append(f"{GREY}╰{'─' * (width - 2)}╯{RESET}")
        lines.append(self._footer(width))
        return lines

    def _boxed(self, text, inner):
        pad = max(0, inner - visible(text))
        return f"{GREY}│{RESET} {text}{' ' * pad} {GREY}│{RESET}"

    def _input_lines(self, inner):
        """The editor's text, wrapped to the box, with a drawn cursor; scrolled to it."""
        rows, cursor_row = [], 0
        room = inner - 2
        for i, line in enumerate(self.editor.lines):
            prefix = "> " if i == 0 else "  "
            chunks = [line[j:j + room] for j in range(0, max(len(line), 1), room)] or [""]
            if len(line) and len(line) % room == 0 and i == self.editor.row and self.editor.col == len(line):
                chunks.append("")
            for c, chunk in enumerate(chunks):
                start = c * room
                text = chunk
                if i == self.editor.row and start <= self.editor.col <= start + len(chunk) \
                        and (self.editor.col < start + room or c == len(chunks) - 1):
                    at = self.editor.col - start
                    under = text[at] if at < len(text) else " "
                    text = f"{text[:at]}\033[7m{under}\033[27m{text[at + 1:]}"
                    cursor_row = len(rows)
                rows.append((prefix if c == 0 else "  ") + text)
        if not self.editor.text and not self.busy:
            rows = [f"> \033[7m \033[27m{GREY}Ask anything. Alt+Enter or Ctrl+J for a new line{RESET}"]
        top = max(0, min(cursor_row - self.BOX_LINES + 1, len(rows) - self.BOX_LINES))
        return rows[top:top + self.BOX_LINES]

    def _footer(self, width):
        if self.menu:
            hint = "↑/↓ choose · enter select · esc cancel"
        elif self.busy:
            hint = "esc to interrupt" + (f" · {len(self.queued)} queued" if self.queued else
                                         " · enter queues a message")
        elif time.monotonic() - self.exit_armed < 2:
            hint = "press ctrl-c again to exit"
        else:
            hint = "enter send · ctrl-d exit"
        left = f"  {self.footer}"
        gap = max(2, width - visible(left) - len(hint) - 1)
        return f"{GREY}{left}{' ' * gap}{hint}{RESET}"

    # --- keys ----------------------------------------------------------------------------

    def _read_keys(self, fd):
        import os
        import select
        while self.terminal and self.terminal.active:
            wait = 0.05 if self.parser.pending else 0.5
            ready, _, _ = select.select([fd], [], [], wait)
            keys = self.parser.feed(os.read(fd, 4096)) if ready else self.parser.flush()
            for key in keys:
                self.handle_key(key)

    def handle_key(self, key):
        """One key, from the reader thread (or a test)."""
        import os
        import signal
        with self.lock:
            if self.menu:
                chosen = self.menu[2].key(key)
                if chosen is not None:
                    self.result = chosen
                    self.submitted.notify_all()
                self._render()
                return
            if key.name in ("esc", "ctrl-c") and self.busy:
                os.kill(os.getpid(), signal.SIGINT)   # a real signal: it breaks a blocked read
                return
            if key.name == "ctrl-c":
                if self.editor.text:
                    self.editor.clear()
                elif time.monotonic() - self.exit_armed < 2:
                    self._finish(EOFError)
                else:
                    self.exit_armed = time.monotonic()
                self._render()
                return
            if key.name == "ctrl-d" and not self.editor.text and not self.busy:
                self._finish(EOFError)
                return
            if key.name == "ctrl-l":
                self._render()
                return
            if self.editor.key(key) == "submit":
                text = self.editor.text
                self.editor.remember(text)
                self.editor.clear()
                if self.busy:
                    self.queued.append(text)
                else:
                    self._finish(text)
            self._render()

    def _finish(self, result):
        self.result = result
        self.submitted.notify_all()
