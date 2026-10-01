"""Everything the agent shows or asks goes through here, so the screen can change without
touching the loop. `current` is the active UI; the REPL swaps in a richer one in a terminal.

PlainUI is the original output, byte for byte: the evals read it (they count a subagent's
calls from its "↳ name" lines and script permission answers through input()), and so do
the tests.
"""


def indent(depth):
    return "  " + "    " * depth


class PlainUI:
    def note(self, text, depth=0):
        """A harness note, e.g. what compaction did."""
        print(f"\033[90m{indent(depth)}({text})\033[0m")

    def narration(self, text):
        """The model's text between tool calls, when it wasn't streamed."""
        print(f"\033[90m{text}\033[0m")

    def tool_call(self, name, args, depth=0):
        print(f"\033[36m{indent(depth)}{'↳' if depth else '→'} {name}\033[0m")

    def tool_result(self, name, args, result, depth=0):
        """Plain output shows no results: the model sees them, the user sees the answer."""

    def permission(self, who, preview, tool, args):
        """Ask before a tool that writes. Returns the answer as typed."""
        print(f"\n  \033[33m{who}{preview}\033[0m")
        return input("  allow? [y]es / [n]o / [a]lways for this tool: ")

    def review_start(self):
        """The reviewer is about to run (a model call after the answer: it can take a while)."""

    def review_skipped(self):
        print("\033[90m  (review skipped)\033[0m")

    def review(self, faked, reason):
        if faked:
            print(f"\033[33m  ⚠ review: this may make a check pass without fixing it: "
                  f"{reason}\033[0m")

    def review_failed(self, error):
        print(f"\033[90m  (review failed: {error})\033[0m")

    def warning(self, text):
        print(f"\033[31m  ({text})\033[0m")


current = PlainUI()


def use(ui):
    """Make `ui` the active UI; returns the one it replaced."""
    global current
    previous, current = current, ui
    return previous
