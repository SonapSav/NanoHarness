"""Subagents: a fresh Agent with its own history, for work that would flood the main one.

The main agent calls the `task` tool with a self-contained instruction. The subagent
works in its own history, which is thrown away; only its final report comes back as the
tool result. With a small context window that is the point.

Two kinds:
- research (default): read_file, glob, grep. Never prompts, cannot change anything.
- write=true: also write_file, edit_file, bash. Its writes go through the SAME
  Permissions as the agent that started it (permissions.CURRENT), so they prompt,
  labelled "subagent:", unless that agent runs --yolo or has [a]lways for the tool.
  bash is sandboxed like any other. The harness appends the list of files it actually
  changed, so the main agent does not have to trust the subagent's own account.

Subagents cannot start subagents: `task` is never in their tool list.
"""
import json

from . import client, config
from .client import ModelError
from .permissions import CURRENT, Permissions
from .tools import ToolError

RESEARCH_TOOLS = ["read_file", "glob", "grep"]
WRITE_TOOLS = RESEARCH_TOOLS + ["write_file", "edit_file", "bash"]


def system_prompt(write: bool) -> str:
    if write:
        can = ("Complete it using read_file, glob, grep, write_file, edit_file and bash. "
               "Change only what the task asks for. Read a file before you edit it. "
               "bash is sandboxed: installs outside the working directory vanish after each "
               "command, so for Python packages create a venv first (python3 -m venv .venv) "
               "and use .venv/bin/pip and .venv/bin/python.")
    else:
        can = ("Complete it using read_file, glob and grep. You cannot change files or "
               "run commands.")
    return f"""You are a subagent working in the directory {config.WORKDIR}.

Another agent gave you a task. {can} You cannot see the other agent's
conversation: the task text is all you know.

Rules:
- Search first (glob for file names, grep for contents), then read only what you need.
- A tool result beginning with 'Error:' means it did NOT work. Never invent file contents,
  paths, line numbers or command output you did not see. If the user denies a call, stop
  and report that.
- When done, reply with no tool call. Your reply is the only thing the other agent will
  see, so make it a complete, concise report: the answer or outcome first, then the
  evidence with file paths and line numbers.
"""


OUT_OF_STEPS = ("You have run out of tool calls. Do not call any more tools. Report what "
                "you found or did so far, and say clearly what you did not get to.")


def run(task: str, write: bool = False) -> str:
    from .agent import Agent   # here, not at the top: agent imports tools imports us

    sub = Agent(
        # Research needs no gate (nothing it has can prompt); a writer shares its parent's.
        CURRENT.get(Permissions()) if write else Permissions(),
        tools=WRITE_TOOLS if write else RESEARCH_TOOLS,
        system=system_prompt(write),
        max_steps=config.SUBAGENT_MAX_STEPS,
        depth=1,
    )
    try:
        report = sub.turn(task)
        if sub.stopped:
            # Rather than hand back "(stopped after N rounds)", ask for what it has.
            sub.messages.append({"role": "user", "content": OUT_OF_STEPS})
            reply = client.chat(sub.messages)   # no tools offered, so it has to answer
            report = "(the subagent ran out of steps; partial report)\n\n" + (
                reply.get("content") or "").strip()
    except ModelError as e:
        raise ToolError(f"the subagent could not get a reply from the model: {e}"
                        + (changes_note(sub.messages) if write else "")) from None

    report = report or "(the subagent finished without saying anything)"
    return report + changes_note(sub.messages) if write else report


def changes(messages):
    """(files changed, bash commands run) according to the tool calls that succeeded.
    Read from the transcript, not from the subagent's report, which may be wrong."""
    files, commands = [], 0
    pending = []   # tool calls awaiting their results, in order
    for m in messages:
        if m["role"] == "assistant":
            pending = [c.get("function", {}) for c in m.get("tool_calls") or []]
        elif m["role"] == "tool" and pending:
            fn = pending.pop(0)
            if m["content"].startswith("Error:"):
                continue
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            if fn.get("name") in ("write_file", "edit_file") and args.get("path") not in files:
                files.append(args.get("path"))
            elif fn.get("name") == "bash":
                commands += 1
    return files, commands


def changes_note(messages) -> str:
    files, commands = changes(messages)
    listed = ", ".join(files) if files else "none"
    note = f"\n\n[harness: files changed by the subagent: {listed}"
    if commands:
        note += f"; bash commands run: {commands} (their effects are not tracked)"
    return note + "]"
