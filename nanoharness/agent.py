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

    def turn(self, user_input: str):
        """One user message in, one final assistant message out (via N tool rounds)."""
        self.messages.append({"role": "user", "content": user_input})

        for step in range(config.MAX_STEPS):
            reply = client.chat(self.messages, schemas())
            self.messages.append(reply)

            calls = reply.get("tool_calls") or []
            if not calls:
                return reply.get("content", "").strip()

            if reply.get("content", "").strip():
                print(f"\033[90m{reply['content'].strip()}\033[0m")

            for call in calls:
                name = call.get("function", {}).get("name", "?")
                print(f"\033[36m  → {name}\033[0m")
                result = self.run_tool(call)
                self.messages.append({
                    "role": "tool",
                    "tool_name": name,
                    "content": result,
                })

        return f"(stopped after {config.MAX_STEPS} tool rounds — the model did not finish)"
