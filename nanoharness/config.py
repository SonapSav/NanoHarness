"""All tunables in one place. Everything is env-overridable."""
import os
from pathlib import Path

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://100.66.104.56:11434")  # generators, over tailscale
MODEL = os.environ.get("NANO_MODEL", "aeroadvisor-agent:latest")

# Ollama silently truncates history past num_ctx, so always set it explicitly.
NUM_CTX = int(os.environ.get("NANO_NUM_CTX", "49152"))  # matches the Modelfile; do not send less
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

# Saved conversations. Outside WORKDIR on purpose: the agent's tools cannot touch them.
SESSION_DIR = Path(os.environ.get(
    "NANO_SESSION_DIR",
    Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "nanoharness/sessions",
)).expanduser()


def system_prompt() -> str:
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
  Never pip install --user or apt install; they cannot work here.
- To answer a question about how the code works, if it would take reading more than
  two files, call task instead of reading them yourself. Your context is small; the
  subagent's report is short.
- When the task is done, reply with a short plain-text summary and no tool call.
"""
