"""All tunables in one place. Everything is env-overridable."""
import os
from pathlib import Path

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://100.66.104.56:11434")  # generators, over tailscale
MODEL = os.environ.get("NANO_MODEL", "aeroadvisor-agent:latest")

# Ollama silently truncates history past num_ctx, so always set it explicitly.
NUM_CTX = int(os.environ.get("NANO_NUM_CTX", "49152"))  # matches the Modelfile; do not send less
TEMPERATURE = float(os.environ.get("NANO_TEMPERATURE", "0.6"))
THINK = os.environ.get("NANO_THINK", "0") == "1"   # Qwen3 reasoning mode
STREAM = os.environ.get("NANO_STREAM", "1") == "1"  # print the reply as it is generated
# Shrink the history once it is estimated past this fraction of NUM_CTX; the rest is room
# for the reply. See context.py.
COMPACT_AT = float(os.environ.get("NANO_COMPACT_AT", "0.75"))

MAX_STEPS = int(os.environ.get("NANO_MAX_STEPS", "25"))   # tool rounds per user turn
BASH_TIMEOUT = int(os.environ.get("NANO_BASH_TIMEOUT", "60"))
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
contents (grep), and run shell commands. Use them instead of guessing: read a file
before you edit it. Prefer glob and grep over find and grep in bash.

Rules:
- Paths may be relative to the working directory. Never go outside it.
- edit_file replaces an exact string. The old_string must appear exactly once,
  so include enough surrounding lines to make it unique.
- Take one step at a time. After a tool returns, look at the result before deciding
  what to do next.
- A tool result beginning with 'Error:' means the action did NOT happen. Never report
  success for a tool call that returned an error, and never invent output you did not see.
- When the task is done, reply with a short plain-text summary and no tool call.
"""
