"""The loop. Everything else exists to serve these ~40 lines."""
import json

from . import client, config
from .permissions import Denied, Permissions
from .tools import REGISTRY, ToolError, schemas


class Agent:
    def __init__(self, permissions: Permissions = None):
        self.messages = [{"role": "system", "content": config.SYSTEM_PROMPT}]
        self.permissions = permissions or Permissions()

    def run_tool(self, call) -> str:
        """Execute one tool call. Every failure comes back as text, never as a crash:
        a small model can usually recover if you tell it what went wrong."""
        fn = call.get("function", {})
        name = fn.get("name", "")
        args = fn.get("arguments", {})

        # Small models sometimes send arguments as a JSON string instead of an object.
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                return f"Error: arguments for {name} were not valid JSON: {args[:200]}"
        if not isinstance(args, dict):
            return f"Error: arguments for {name} must be an object, got {type(args).__name__}."

        tool = REGISTRY.get(name)
        if tool is None:
            return f"Error: no such tool {name!r}. Available tools: {', '.join(REGISTRY)}."

        try:
            self.permissions.check(tool, args)
            return tool.fn(**args)
        except Denied as e:
            return f"Error: {e}"
        except ToolError as e:
            return f"Error: {e}"
        except TypeError as e:
            return f"Error: bad arguments for {name}: {e}"
        except Exception as e:  # never let a tool take the REPL down
            return f"Error: {name} failed unexpectedly: {type(e).__name__}: {e}"

    def add_tool_result(self, name: str, content: str):
        self.messages.append({"role": "tool", "tool_name": name, "content": content})

    def turn(self, user_input: str):
        """One user message in, one final assistant message out (via N tool rounds)."""
        start = len(self.messages)
        self.messages.append({"role": "user", "content": user_input})
        try:
            return self._loop()
        except BaseException:  # ModelError, KeyboardInterrupt
            # If the model never answered, drop the user message so a retry is not a duplicate.
            # Once it has, keep everything: tools may have changed the disk, and the model
            # needs that record.
            if len(self.messages) == start + 1:
                del self.messages[start:]
            raise

    def _loop(self):
        for step in range(config.MAX_STEPS):
            reply = client.chat(self.messages, schemas())
            self.messages.append(reply)

            calls = reply.get("tool_calls") or []
            if not calls:
                return reply.get("content", "").strip()

            if reply.get("content", "").strip():
                print(f"\033[90m{reply['content'].strip()}\033[0m")

            for i, call in enumerate(calls):
                name = call.get("function", {}).get("name", "?")
                print(f"\033[36m  → {name}\033[0m")
                try:
                    result = self.run_tool(call)
                except KeyboardInterrupt:
                    # Every tool call must get a result, or the next request sends Ollama
                    # a dangling call.
                    self.add_tool_result(name, (
                        "Error: the user interrupted this call. It may not have run, or may "
                        "have stopped partway. Do not assume it succeeded."
                    ))
                    for rest in calls[i + 1:]:
                        self.add_tool_result(rest.get("function", {}).get("name", "?"), (
                            "Error: skipped because the user interrupted an earlier call. "
                            "It did NOT run."
                        ))
                    raise
                self.add_tool_result(name, result)

        return f"(stopped after {config.MAX_STEPS} tool rounds — the model did not finish)"
