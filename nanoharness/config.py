"""All tunables in one place. Everything is env-overridable."""
import os
from pathlib import Path

# Server 2 (12 GB RTX 3060, 32 GB RAM) runs nano-35b: qwen3.5:35b-a3b, a 35B mixture-of-experts
# with 3B active, 45% on the GPU and the rest in system RAM; ~41 tok/s writing. Server 1
# (100.66.104.56) still has the earlier aeroadvisor-agent (qwen3.5:9b, 49152 context).
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://100.76.19.74:11434")  # over tailscale
MODEL = os.environ.get("NANO_MODEL", "nano-35b")

# Ollama silently truncates history past num_ctx, so always set it explicitly.
NUM_CTX = int(os.environ.get("NANO_NUM_CTX", "32768"))  # matches the Modelfile; do not send less
TEMPERATURE = float(os.environ.get("NANO_TEMPERATURE", "0.6"))
# Qwen3 reasoning mode. On by default: in evals it took FizzBuzz from 9/20 to 20/20 (the
# model stopped shipping swapped Fizz/Buzz as "success") for ~1.7x the time per task.
THINK = os.environ.get("NANO_THINK", "1") == "1"
# Cap on tokens generated per reply, reasoning included (Ollama's num_predict; -1 = no cap).
# Recorded replies top out near 2k tokens; the two that spiralled ran ~50k tokens of
# reasoning for ~17 min and ended empty. Hitting the cap is an error the user sees.
NUM_PREDICT = int(os.environ.get("NANO_NUM_PREDICT", "16384"))
STREAM = os.environ.get("NANO_STREAM", "1") == "1"  # print the reply as it is generated
# Shrink the history once it is estimated past this fraction of NUM_CTX; the rest is room
# for the reply. See context.py.
COMPACT_AT = float(os.environ.get("NANO_COMPACT_AT", "0.75"))

MAX_STEPS = int(os.environ.get("NANO_MAX_STEPS", "25"))   # tool rounds per user turn
SUBAGENT_MAX_STEPS = int(os.environ.get("NANO_SUBAGENT_MAX_STEPS", "15"))
# Tell the model when a tool call repeats an earlier one in the turn with the same result.
REPEAT_NOTE = os.environ.get("NANO_REPEAT_NOTE", "1") == "1"
# After a turn that wrote, ask a separate call whether the changes make a check pass without
# fixing it, and warn the user if so (review.py). Live: 9/9 fakes flagged, 0 of 23 correct
# runs; costs ~10-30 s per turn that wrote (reasoning on). NANO_REVIEW=0 turns it off.
REVIEW = os.environ.get("NANO_REVIEW", "1") == "1"
# The review call's own temperature. Same as the agent's: at 0.2 (to stop verdicts flipping
# between passes) 9 of 75 honest reviews looped until num_predict cut them off, no verdict.
REVIEW_TEMPERATURE = float(os.environ.get("NANO_REVIEW_TEMPERATURE", "0.6"))
BASH_TIMEOUT = int(os.environ.get("NANO_BASH_TIMEOUT", "60"))

# bash sandbox (see sandbox.py). auto: use bwrap if it works, warn if not. on: refuse to
# run bash without it. off: full user privileges, as before.
SANDBOX = os.environ.get("NANO_SANDBOX", "auto").lower()
if SANDBOX not in ("auto", "on", "off"):
    raise SystemExit(f"NANO_SANDBOX must be auto, on or off, not {SANDBOX!r}")
SANDBOX_NET = os.environ.get("NANO_SANDBOX_NET", "1") == "1"
# Extra read-only paths inside the sandbox, ':'-separated, e.g. ~/.nvm:~/.pyenv for tools
# that live in your home directory (which is otherwise hidden).
SANDBOX_RO_PATHS = [str(Path(p).expanduser().resolve())
                    for p in os.environ.get("NANO_SANDBOX_RO_PATHS", "").split(":") if p]
MAX_TOOL_OUTPUT = int(os.environ.get("NANO_MAX_TOOL_OUTPUT", "8000"))  # chars
HTTP_TIMEOUT = int(os.environ.get("NANO_HTTP_TIMEOUT", "300"))

# The sandbox root. Every path a tool touches must resolve inside this.
WORKDIR = Path(os.environ.get("NANO_WORKDIR", os.getcwd())).resolve()

# The startup panel's logo: one-colour text art, best about 30 columns by 10-12 rows (up to
# 36 by 16 is shown; bigger is trimmed). No file: the space is kept with a small mark.
LOGO = Path(os.environ.get(
    "NANO_LOGO",
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "nanoharness/logo.txt",
)).expanduser()

# Saved conversations. Outside WORKDIR on purpose: the agent's tools cannot touch them.
SESSION_DIR = Path(os.environ.get(
    "NANO_SESSION_DIR",
    Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "nanoharness/sessions",
)).expanduser()


def about(yolo=False) -> str:
    """What the agent is and where it runs, from the live settings so it can't go stale.
    Without it, "which model are you?" got a guess and harness questions made-up answers
    (knows_itself: 0/10). The Ollama host is left out on purpose: /status shows it."""
    from importlib.metadata import PackageNotFoundError, version
    from . import sandbox
    from .commands import COMMANDS
    try:
        name = f"NanoHarness v{version('nanoharness')}"
    except PackageNotFoundError:
        name = "NanoHarness"
    box = sandbox.status()
    commands = ", ".join(usage.split()[0] for usage, _ in COMMANDS)
    return f"""About this environment (answer questions about yourself or the harness from this; if it
doesn't say, say you don't know):
- You are the agent in {name}, a small coding agent harness (Python, stdlib only)
  running locally. The model answering is {MODEL}, served by Ollama.
- Context window: {NUM_CTX:,} tokens. Past {COMPACT_AT:.0%} of it, older turns are replaced by
  a summary; search_history finds the originals.
- bash sandbox: {box}. Permissions: {"none asked (--yolo)" if yolo else "the user is asked before anything that writes"}.
- You cannot see images, and you have no web search tool.
- The user's own commands (typed by them, not your tools): {commands}. Sessions are saved
  after every message; the user goes back to one with /resume (or starts with
  `nanoharness -c` for the latest, `nanoharness --resume` to pick).
"""


def system_prompt(yolo=False) -> str:
    """Built on demand so it always names the current WORKDIR."""
    return f"""You are a coding agent working in the directory {WORKDIR}.

You have tools to read, write and edit files, find files (glob), search their
contents (grep), run shell commands, and delegate research to a subagent (task).
Use them instead of guessing: read a file before you edit it. Prefer glob and grep
over find and grep in bash.

Rules:
- Paths may be relative to the working directory. Never go outside it.
- edit_file replaces an exact string. The old_string must appear exactly once,
  so include enough surrounding lines to make it unique.
- Take one step at a time. After a tool returns, look at the result before deciding
  what to do next.
- A tool result beginning with 'Error:' means the action did NOT happen. Never report
  success for a tool call that returned an error, and never invent output you did not see.
- bash runs in a sandbox: anything installed outside the working directory is gone
  after the command. To install Python packages, first create a venv in the working
  directory (python3 -m venv .venv), then use .venv/bin/pip and .venv/bin/python.
  Never pip install --user or apt install; they cannot work here. Nothing a bash command
  starts keeps running after it returns, background (&, nohup) included. To run a server
  or watcher, use start_service (with its port): it keeps it running and checks it started.
  Never tell the user something is running unless a tool result said so.
- To answer a question about how the code works, if it would take reading more than
  two files, call task instead of reading them yourself. Your context is small; the
  subagent's report is short.
- When the task is done, reply with a short plain-text summary and no tool call.

{about(yolo)}"""
