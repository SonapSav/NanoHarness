"""The gate between 'the model asked for X' and 'X ran'.

Reads run freely. Anything that writes to disk or runs a command asks first,
with a per-session 'always allow this tool' escape hatch.
"""
from .tools import Tool


class Denied(Exception):
    pass


class Permissions:
    def __init__(self, yolo=False):
        self.yolo = yolo              # --yolo: approve everything, for trusted loops
        self.always: set[str] = set()  # tool names approved for the rest of the session

    def check(self, tool: Tool, args: dict):
        if self.yolo or not tool.writes or tool.name in self.always:
            return

        try:
            preview = tool.preview(**args)
        except TypeError:
            preview = f"{tool.name}({args})"

        print(f"\n  \033[33m{preview}\033[0m")
        answer = input("  allow? [y]es / [n]o / [a]lways for this tool: ").strip().lower()

        if answer.startswith("a"):
            self.always.add(tool.name)
            return
        if answer.startswith("y") or answer == "":
            return
        raise Denied(
            "The user DENIED this call. It did NOT run and nothing changed on disk. "
            "Do not claim it succeeded. Stop, tell the user it was denied, and ask how to proceed."
        )
