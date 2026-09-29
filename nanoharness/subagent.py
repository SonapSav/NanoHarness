"""Subagents: a fresh Agent with its own history, for work that would flood the main one.

The main agent calls the `task` tool with a self-contained instruction. A subagent
reads and searches (read-only tools, so it never prompts and cannot change anything),
and only its final report comes back as the tool result. Everything it read stays in
its own history, which is thrown away. With a small context window that is the point.

Subagents cannot start subagents: `task` is not in their tool list.
"""
from . import client, config
from .client import ModelError
from .permissions import Permissions
from .tools import ToolError

TOOLS = ["read_file", "glob", "grep"]


def system_prompt() -> str:
    return f"""You are a research subagent working in the directory {config.WORKDIR}.

Another agent gave you a task. Complete it using read_file, glob and grep. You cannot
change files or run commands. You cannot see the other agent's conversation: the task
text is all you know.

Rules:
- Search first (glob for file names, grep for contents), then read only what you need.
- A tool result beginning with 'Error:' means it did NOT work. Never invent file contents,
  paths or line numbers you did not see.
- When done, reply with no tool call. Your reply is the only thing the other agent will
  see, so make it a complete, concise report: the answer first, then the evidence with
  file paths and line numbers.
"""


OUT_OF_STEPS = ("You have run out of tool calls. Do not call any more tools. Report what "
                "you found so far, and say clearly what you did not get to.")


def run(task: str) -> str:
    from .agent import Agent   # here, not at the top: agent imports tools imports us

    sub = Agent(
        Permissions(),
        tools=TOOLS,
        system=system_prompt(),
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
        raise ToolError(f"the subagent could not get a reply from the model: {e}") from None

    return report or "(the subagent finished without saying anything)"
