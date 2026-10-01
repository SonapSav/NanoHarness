"""The loop. Everything else exists to serve these ~40 lines."""
import json

from . import client, config, context, review, ui
from .permissions import CURRENT, Denied, Permissions
from .tools import ARCHIVE, ON_DEMAND, REGISTRY, ToolError, file_tree, resolve, schemas

# Seen live: a search found the answer, the model overlooked it and ran the same grep over and
# over until it hit the step limit. The call still runs (a rerun after an edit can differ);
# only an identical result gets the note.
REPEAT_NOTE = ("\n\n[You already made this exact call earlier in this turn and got the same "
               "result. Repeating it won't change anything: try something different, or tell the "
               "user what you could not find.]")


def with_file_tree(prompt: str) -> str:
    return (prompt + "\nFiles in the working directory when this session started (skips .git, "
            ".venv and caches; it goes stale as files change):\n" + file_tree() + "\n")


class Agent:
    def __init__(self, permissions: Permissions = None, on_token=None, session=None, messages=None,
                 tools=None, system=None, max_steps=None, depth=0):
        # A resumed history gets today's system prompt: the code may have changed since.
        system = system or with_file_tree(config.system_prompt())
        self.messages = [{"role": "system", "content": system}] + (messages or [])[1:]
        self.tools = list(tools or [n for n in REGISTRY if n not in ON_DEMAND])   # a subagent gets fewer
        self.max_steps = max_steps or config.MAX_STEPS
        self.depth = depth                     # 0 for the REPL's agent, 1 for a subagent
        self.stopped = False                   # did the last turn run out of steps?
        self.permissions = permissions or Permissions()
        self.session = session   # saved after every message when set
        # Originals of what compaction summarized, for search_history; saved with the session.
        self.archive = list(getattr(session, "archive", None) or [])
        # on_token(kind, text) shows the reply as it streams; kind "start" opens each reply
        # (before the request is sent), "end" closes it.
        self.on_token = on_token
        self.dropped_input = False   # did the last failed turn discard the user's message?
        # For review.py, per turn: files as they were before this turn first wrote them (None =
        # didn't exist), the last bash command with its output, every call as a line, every
        # bash command, and a snapshot of the working directory at the start.
        self.before, self.last_command, self.steps, self.commands, self.start = {}, "", [], [], None
        self.verdict = None          # (faked, reason) from the last turn's review, if one ran

    def available(self):
        """Tool names offered right now: search_history once there is an archive to search."""
        return self.tools + (["search_history"] if self.archive and "search_history" not in self.tools else [])

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

        tool = REGISTRY.get(name) if name in self.available() else None
        if tool is None:
            return f"Error: no such tool {name!r}. Available tools: {', '.join(self.available())}."

        token = CURRENT.set(self.permissions)
        archive_token = ARCHIVE.set(self.archive)
        try:
            self.permissions.check(tool, args, who="subagent: " if self.depth else "")
            if name in ("write_file", "edit_file"):
                self.remember_before(args.get("path", ""))
            result = tool.fn(**args)
            if name == "bash":
                self.last_command = f"$ {args.get('command', '')}\n{result}"
                self.commands.append(str(args.get("command", "")))
            return result
        except Denied as e:
            return f"Error: {e}"
        except ToolError as e:
            return f"Error: {e}"
        except TypeError as e:
            return f"Error: bad arguments for {name}: {e}"
        except Exception as e:  # never let a tool take the REPL down
            return f"Error: {name} failed unexpectedly: {type(e).__name__}: {e}"
        finally:
            CURRENT.reset(token)
            ARCHIVE.reset(archive_token)

    def remember_before(self, path: str):
        key = str(resolve(path).relative_to(config.WORKDIR)) if path else ""
        if key and key not in self.before:
            p = config.WORKDIR / key
            self.before[key] = p.read_text(errors="replace") if p.is_file() else None

    def review_turn(self, request: str, answer: str):
        """Ask review.py whether this turn's changes fake a pass; warn the user if so.
        The model is not told: arguing with it made it spiral."""
        changed = review.changed(self.start)
        if not review.worth_reviewing(changed, self.commands):
            return
        after = {}
        for key in self.before:
            p = config.WORKDIR / key
            after[key] = p.read_text(errors="replace") if p.is_file() else ""
        changes = review.diff(self.before, after)
        by_commands = sorted(changed - self.before.keys() - {"?"})
        if by_commands:   # through bash: the old contents weren't kept, so no diff
            changes += "\n\nAlso changed by commands (contents not shown): " + ", ".join(by_commands)
        try:
            self.verdict = review.review(request, changes.strip(), self.last_command, answer,
                                         self.steps)
        except Exception as e:  # a failed review must not cost the user the turn
            ui.current.review_failed(e)
            return
        ui.current.review(*self.verdict)

    def add_tool_result(self, name: str, content: str):
        self.messages.append({"role": "tool", "tool_name": name, "content": content})
        self.save()

    def save(self):
        if self.session is None:
            return
        try:
            self.session.save(self.messages, self.archive)
        except OSError as e:  # a full disk should not kill the conversation
            ui.current.warning(f"could not save session: {e}")

    def turn(self, user_input: str):
        """One user message in, one final assistant message out (via N tool rounds)."""
        user_message = {"role": "user", "content": user_input}
        self.messages.append(user_message)
        self.dropped_input = False
        self.stopped = False
        self.before, self.last_command, self.steps, self.commands = {}, "", [], []
        self.verdict = None
        self.start = review.snapshot() if config.REVIEW and not self.depth else None
        self.save()
        try:
            answer = self._loop()
            if config.REVIEW and not self.depth:
                self.review_turn(user_input, answer)
            return answer
        except BaseException:  # ModelError, KeyboardInterrupt
            # If the model never answered, drop the user message so a retry is not a duplicate.
            # Once it has, keep everything: tools may have changed the disk, and the model
            # needs that record. (Compaction may have rebuilt the list, hence `is`, not indices.)
            if self.messages[-1] is user_message:
                self.messages.pop()
                self.dropped_input = True
            raise
        finally:
            self.save()

    def _loop(self):
        seen = {}   # (tool, arguments) -> last result, for this turn
        for step in range(self.max_steps):
            self.messages, note = context.fit(self.messages, schemas(self.available()),
                                              archive=self.archive)
            if note:
                ui.current.note(note, self.depth)
            if self.on_token:
                self.on_token("start", "")
            reply = client.chat(self.messages, schemas(self.available()), on_token=self.on_token)
            if self.on_token:
                self.on_token("end", "")
            self.messages.append(reply)
            self.save()

            calls = reply.get("tool_calls") or []
            if not calls:
                return reply.get("content", "").strip()

            # Narration between tool calls; a subagent's stays quiet, only its report matters.
            if reply.get("content", "").strip() and not self.on_token and not self.depth:
                ui.current.narration(reply["content"].strip())

            for i, call in enumerate(calls):
                name = call.get("function", {}).get("name", "?")
                ui.current.tool_call(name, call.get("function", {}).get("arguments"), self.depth)
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
                self.steps.append(review.step(name, call.get("function", {}).get("arguments"), result))
                if config.REPEAT_NOTE:
                    key = (name, json.dumps(call.get("function", {}).get("arguments"), sort_keys=True))
                    repeated = seen.get(key) == result
                    seen[key] = result
                    if repeated:
                        result += REPEAT_NOTE
                ui.current.tool_result(name, call.get("function", {}).get("arguments"), result,
                                       self.depth)
                self.add_tool_result(name, result)

        self.stopped = True
        return f"(stopped after {self.max_steps} tool rounds — the model did not finish)"
