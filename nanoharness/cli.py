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

    agent = Agent(Permissions(yolo=yolo))

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
            agent = Agent(agent.permissions)
            print("history cleared")
            continue
        if user_input == "/messages":
            import json
            print(json.dumps(agent.messages, indent=2)[:8000])
            continue

        before = len(agent.messages)
        try:
            print(agent.turn(user_input) + "\n")
        except ModelError as e:
            print(f"\033[31m{e}\033[0m")
            print(recovery_hint(agent, before))
        except KeyboardInterrupt:
            print("\n\033[31minterrupted\033[0m")
            print(recovery_hint(agent, before))


def recovery_hint(agent, before):
    """After a failed turn, say whether the message survived. Agent.turn drops it
    only if the model never answered."""
    if len(agent.messages) == before:
        return "\033[90m(your message was not kept; send it again to retry)\033[0m\n"
    return "\033[90m(progress so far is kept; say \"continue\" to resume)\033[0m\n"


if __name__ == "__main__":
    raise SystemExit(main())
