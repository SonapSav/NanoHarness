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
                line = self.status_text()
                if line is not None:
                    self._draw(line)
                elif self.live and not self.partial:
                    self._draw("")
