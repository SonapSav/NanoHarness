"""Deliberately dumb REPL. Read a line, run a turn, print the answer."""
import argparse
import sys
import threading
import time

from . import config, sandbox, session, tui, ui
from .agent import Agent
from .client import ModelError
from .permissions import Permissions
from .session import Session, SessionError

BANNER = """\033[1mNanoHarness\033[0m
  model    {model}
  host     {host}
  workdir  {workdir}
  ctx      {ctx}
  session  {session}
  sandbox  {sandbox}{yolo}

  /exit  quit      /reset  new session      /sessions  list saved      /resume [n|id]  switch to one
  /messages  dump raw history                 (at startup: -c resumes the latest, --resume picks)
"""


PROMPT = "\033[1m> \033[0m"
# With readline, colour codes in the prompt must be marked zero-width, or it miscounts the
# line and editing goes wrong; without it the markers would print.
READLINE_PROMPT = "\001\033[1m\002> \001\033[0m\002"


def parse_args(argv):
    p = argparse.ArgumentParser(prog="nanoharness", description="A coding agent harness, stdlib only.")
    p.add_argument("--yolo", action="store_true", help="skip permission prompts")
    p.add_argument("--plain", action="store_true",
                   help="plain output (the default when not in a terminal)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("-c", "--continue", dest="cont", action="store_true",
                   help="resume the latest session in this directory")
    g.add_argument("--resume", nargs="?", const="", metavar="ID",
                   help="resume a session; without ID, pick one from a list")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        sess, history = start_session(args)
    except SessionError as e:
        print(f"\033[31m{e}\033[0m")
        return 1

    rich = config.STREAM and sys.stdout.isatty() and not args.plain
    if rich:
        print(rich_banner(sess.id, args.yolo))
    else:
        print(BANNER.format(
            model=config.MODEL,
            host=config.OLLAMA_HOST,
            workdir=config.WORKDIR,
            ctx=config.NUM_CTX,
            session=sess.id,
            sandbox=sandbox_line(),
            yolo="\n  \033[31myolo     permissions disabled\033[0m" if args.yolo else "",
        ))
    if history:
        print(f"\033[90mresumed · {len(history)} messages · last request: "
              f"{last_request(history)!r}\033[0m\n")
    if sys.stdin.isatty():
        input_history()

    if rich:
        printer = tui.RichUI()
        ui.use(printer)
    else:
        printer = StreamPrinter() if config.STREAM else None
    agent = Agent(Permissions(yolo=args.yolo), on_token=printer, session=sess, messages=history)

    while True:
        try:
            user_input = input(PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not user_input:
            continue
        if user_input in ("/exit", "/quit"):
            return 0
        if user_input == "/reset":
            agent = Agent(agent.permissions, agent.on_token, session=Session())
            print(f"new session {agent.session.id} (the old one stays saved)")
            continue
        if user_input == "/sessions":
            print(format_sessions(session.list_sessions(), numbered=True, current=agent.session.id))
            continue
        if user_input == "/resume" or user_input.startswith("/resume "):
            agent = switch_session(agent, user_input[len("/resume"):].strip())
            continue
        if user_input == "/messages":
            import json
            print(json.dumps(agent.messages, indent=2)[:8000])
            continue

        if printer:
            printer.reset()
        try:
            try:
                answer = agent.turn(user_input)
            finally:
                if printer:
                    printer.stop()
            if printer is None or answer != printer.last:  # e.g. "(stopped after ...)"
                print(answer)
            print()
        except ModelError as e:
            if printer:
                printer.break_line()
            print(f"\033[31m{e}\033[0m")
            print(recovery_hint(agent))
        except KeyboardInterrupt:
            print("\n\033[31minterrupted\033[0m")
            print(recovery_hint(agent))


class StreamPrinter:
    """Shows a reply as it streams: thinking in italic grey, the answer in plain text.
    Remembers the last complete reply so the REPL does not print it twice.

    Ollama sends nothing while the model writes a tool call (measured: 14 s of silence for a
    60-line write_file, then the whole call at once), nor while it reads a long prompt. So
    while a reply is open and nothing has arrived for a second, a grey status line shows a
    spinner and the time; it goes as soon as anything arrives or the reply ends."""

    SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    QUIET = 1.0     # seconds of silence before the status line appears

    def __init__(self, ticker=None):
        self.lock = threading.RLock()
        self.reset()
        if sys.stdout.isatty() if ticker is None else ticker:
            threading.Thread(target=self._tick, daemon=True).start()

    def reset(self):
        with self.lock:
            self.kind = None   # what is mid-line right now: "thinking", "content" or None
            self.parts = []
            self.last = None
            self.open = False      # between "start" and "end": a reply is on its way
            self.quiet_since = None
            self.seen = False      # has this reply sent anything yet?
            self.status = False    # is the status line on screen?

    def break_line(self):
        with self.lock:
            self._clear_status()
            if self.kind:
                print()
            self.kind = None

    def stop(self):
        """The turn is over (answered, failed or interrupted): no status line from here."""
        with self.lock:
            self.open = False
            self._clear_status()

    def __call__(self, kind, text):
        with self.lock:
            if kind == "start":
                self.open, self.quiet_since, self.seen = True, time.monotonic(), False
                return
            self._clear_status()
            if kind == "end":
                self.open = False
                self.break_line()
                self.last = "".join(self.parts).strip()
                self.parts = []
                return
            self.quiet_since, self.seen = time.monotonic(), True
            if self.kind and kind != self.kind:
                print()   # thinking is over; the answer starts on its own line
            self.kind = kind
            if kind == "thinking":
                text = f"\033[3;90m{text}\033[0m"   # italic grey: apart from the harness's grey notes
            else:
                self.parts.append(text)
            sys.stdout.write(text)
            sys.stdout.flush()

    def status_text(self, now=None):
        """The status line for now, or None when it shouldn't show."""
        if not self.open or self.quiet_since is None:
            return None
        quiet = (now or time.monotonic()) - self.quiet_since
        if quiet < self.QUIET:
            return None
        spin = self.SPIN[int(quiet * 8) % len(self.SPIN)]
        return f"{spin} {'working' if self.seen else 'waiting for the model'} · {int(quiet)}s"

    def _tick(self):
        while True:
            time.sleep(0.125)
            with self.lock:
                line = self.status_text()
                if line is None:
                    continue
                if self.kind:          # mid-line: the status line goes on a line of its own
                    print()
                    self.kind = None
                sys.stdout.write(f"\r\033[2K\033[90m  {line}\033[0m")
                sys.stdout.flush()
                self.status = True

    def _clear_status(self):
        if self.status:
            sys.stdout.write("\r\033[2K")
            sys.stdout.flush()
            self.status = False


def rich_banner(session_id, yolo):
    rows = [("model", config.MODEL), ("host", config.OLLAMA_HOST), ("workdir", str(config.WORKDIR)),
            ("session", session_id), ("sandbox", strip_ansi(sandbox_line()))]
    if yolo:
        rows.append(("yolo", "permissions disabled"))
    inner = max(len(f"{k:<8} {v}") for k, v in rows) + 2
    top = f"\033[38;5;208m╭─ ✻ \033[1mNanoHarness\033[0m\033[38;5;208m {'─' * (inner - 15)}╮\033[0m"
    body = [f"\033[38;5;208m│\033[0m {k:<8} {v:<{inner - 11}} \033[38;5;208m│\033[0m"
            if k != "yolo" else
            f"\033[38;5;208m│\033[0m \033[31m{k:<8} {v:<{inner - 11}}\033[0m \033[38;5;208m│\033[0m"
            for k, v in rows]
    bottom = f"\033[38;5;208m╰{'─' * inner}╯\033[0m"
    help_line = ("\033[90m  /exit · /reset · /sessions · /resume [n|id] · /messages   "
                 "(startup: -c latest, --resume pick)\033[0m\n")
    return "\n".join([top, *body, bottom, help_line])


def strip_ansi(text):
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def input_history():
    """Up/down recall of earlier input, kept between runs. Best effort: no readline, no history."""
    try:
        import atexit
        import readline
    except ImportError:
        return
    path = config.SESSION_DIR.parent / "input_history"
    try:
        readline.read_history_file(path)
    except OSError:
        pass
    readline.set_history_length(1000)
    global PROMPT
    PROMPT = READLINE_PROMPT

    def save():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            readline.write_history_file(path)
        except OSError:
            pass
    atexit.register(save)


def sandbox_line():
    line = sandbox.status()
    return line if line.startswith("bwrap:") else f"\033[31m{line}\033[0m"


def start_session(args):
    """A fresh session, unless -c or --resume asked for a saved one."""
    id = None
    if args.cont:
        id = session.latest()
        if id is None:
            print("no saved sessions for this directory; starting a new one")
    elif args.resume is not None:
        id = args.resume or pick_session()
    if id:
        return session.load(id)
    return Session(), None


def pick_session(current=None, none_means="a new session"):
    sessions = session.list_sessions()
    if not sessions:
        print("no saved sessions for this directory" + ("; starting a new one" if current is None else ""))
        return None
    print(format_sessions(sessions, numbered=True, current=current))
    try:
        choice = input(f"resume which? [number or id, enter for {none_means}] ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    return chosen(choice, sessions)


def chosen(choice, sessions):
    """A number from the list (1 = newest) or a session id; None for nothing."""
    if choice.isdigit() and 1 <= int(choice) <= len(sessions):
        return sessions[int(choice) - 1]["id"]
    return choice or None


def switch_session(agent, choice):
    """/resume: the agent on another saved session, or the same agent if that fails.
    The current one is already saved (after every message), so leaving it loses nothing."""
    current = agent.session.id
    id = chosen(choice, session.list_sessions()) if choice else pick_session(current, "staying here")
    if not id or id == current:
        print(f"staying in session {current}")
        return agent
    try:
        sess, history = session.load(id)
    except SessionError as e:
        print(f"\033[31m{e}\033[0m")
        return agent
    # Same permissions, so an [a]lways answer and --yolo carry over.
    new = Agent(agent.permissions, agent.on_token, session=sess, messages=history)
    print(f"\033[90mresumed {sess.id} · {len(history)} messages · last request: "
          f"{last_request(history)!r}\033[0m\n")
    return new


def format_sessions(sessions, numbered=False, current=None):
    if not sessions:
        return "no saved sessions for this directory"
    rows = []
    for n, s in enumerate(sessions, start=1):
        star = "*" if s["id"] == current else " "
        mark = f"{n:>3}{star} " if numbered else f"  {star} "
        rows.append(f"{mark}{s['id']:<18} {s['updated'].replace('T', ' ')}  "
                    f"{s['messages']:>4} msgs  {s['title']}")
    return "\n".join(rows)


def last_request(messages) -> str:
    for m in reversed(messages):
        if m["role"] == "user":
            return " ".join(m["content"].split())[:70]
    return ""


def recovery_hint(agent):
    """After a failed turn, say whether the message survived. Agent.turn drops it
    only if the model never answered."""
    if agent.dropped_input:
        return "\033[90m(your message was not kept; send it again to retry)\033[0m\n"
    return "\033[90m(progress so far is kept; say \"continue\" to resume)\033[0m\n"


if __name__ == "__main__":
    raise SystemExit(main())
