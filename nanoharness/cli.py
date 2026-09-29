"""Deliberately dumb REPL. Read a line, run a turn, print the answer."""
import argparse
import sys

from . import config, sandbox, session
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

  /exit  quit      /reset  new session      /sessions  list saved      /messages  dump raw history
"""


def parse_args(argv):
    p = argparse.ArgumentParser(prog="nanoharness", description="A coding agent harness, stdlib only.")
    p.add_argument("--yolo", action="store_true", help="skip permission prompts")
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

    printer = StreamPrinter() if config.STREAM else None
    agent = Agent(Permissions(yolo=args.yolo), on_token=printer, session=sess, messages=history)

    while True:
        try:
            user_input = input("\033[1m> \033[0m").strip()
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
            print(format_sessions(session.list_sessions(), current=agent.session.id))
            continue
        if user_input == "/messages":
            import json
            print(json.dumps(agent.messages, indent=2)[:8000])
            continue

        if printer:
            printer.reset()
        try:
            answer = agent.turn(user_input)
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
    """Shows a reply as it streams: thinking in grey, the answer in plain text.
    Remembers the last complete reply so the REPL does not print it twice."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.kind = None   # what is mid-line right now: "thinking", "content" or None
        self.parts = []
        self.last = None

    def break_line(self):
        if self.kind:
            print()
        self.kind = None

    def __call__(self, kind, text):
        if kind == "end":
            self.break_line()
            self.last = "".join(self.parts).strip()
            self.parts = []
            return
        if self.kind and kind != self.kind:
            print()   # thinking is over; the answer starts on its own line
        self.kind = kind
        if kind == "thinking":
            text = f"\033[90m{text}\033[0m"
        else:
            self.parts.append(text)
        sys.stdout.write(text)
        sys.stdout.flush()


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


def pick_session():
    sessions = session.list_sessions()
    if not sessions:
        print("no saved sessions for this directory; starting a new one")
        return None
    print(format_sessions(sessions, numbered=True))
    try:
        choice = input("resume which? [number or id, enter for a new session] ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if choice.isdigit() and 1 <= int(choice) <= len(sessions):
        return sessions[int(choice) - 1]["id"]
    return choice or None


def format_sessions(sessions, numbered=False, current=None):
    if not sessions:
        return "no saved sessions for this directory"
    rows = []
    for n, s in enumerate(sessions, start=1):
        mark = f"{n:>3}  " if numbered else ("  * " if s["id"] == current else "    ")
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
