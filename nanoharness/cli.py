"""Deliberately dumb REPL. Read a line, run a turn, print the answer."""
import sys

from . import config
from .agent import Agent
from .client import ModelError
from .permissions import Permissions

BANNER = """\033[1mNanoHarness\033[0m
  model    {model}
  host     {host}
  workdir  {workdir}
  ctx      {ctx}{yolo}

  /exit  quit      /reset  clear history      /messages  dump raw history
"""


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    yolo = "--yolo" in argv

    print(BANNER.format(
        model=config.MODEL,
        host=config.OLLAMA_HOST,
        workdir=config.WORKDIR,
        ctx=config.NUM_CTX,
        yolo="\n  \033[31myolo     permissions disabled\033[0m" if yolo else "",
    ))

    printer = StreamPrinter() if config.STREAM else None
    agent = Agent(Permissions(yolo=yolo), on_token=printer)

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
            agent = Agent(agent.permissions, agent.on_token)
            print("history cleared")
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


def recovery_hint(agent):
    """After a failed turn, say whether the message survived. Agent.turn drops it
    only if the model never answered."""
    if agent.dropped_input:
        return "\033[90m(your message was not kept; send it again to retry)\033[0m\n"
    return "\033[90m(progress so far is kept; say \"continue\" to resume)\033[0m\n"


if __name__ == "__main__":
    raise SystemExit(main())
