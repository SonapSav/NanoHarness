"""Tool registry: a JSON schema the model sees, plus a Python function that runs."""
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import config


class ToolError(Exception):
    """An expected failure. The message goes back to the model so it can retry."""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., str]
    writes: bool          # does it change the world? drives the permission gate
    preview: Callable[..., str]   # one line shown in the confirmation prompt


REGISTRY: dict[str, Tool] = {}


def tool(name, description, parameters, writes, preview):
    def register(fn):
        REGISTRY[name] = Tool(name, description, parameters, fn, writes, preview)
        return fn
    return register


def schemas():
    """The `tools` array sent to Ollama, in OpenAI function-calling shape."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in REGISTRY.values()
    ]


# --- helpers ---------------------------------------------------------------

def resolve(path: str) -> Path:
    """Resolve a user/model-supplied path and refuse anything outside WORKDIR."""
    p = (config.WORKDIR / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    if p != config.WORKDIR and config.WORKDIR not in p.parents:
        raise ToolError(f"Path {p} is outside the working directory {config.WORKDIR}.")
    return p


def truncate(text: str, limit: int = None) -> str:
    """Cap tool output so one `cat` of a big file cannot eat the context window."""
    limit = limit or config.MAX_TOOL_OUTPUT
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[truncated: showing {limit} of {len(text)} chars]"


# --- the tools -------------------------------------------------------------

@tool(
    name="read_file",
    description="Read a text file. Returns the contents with line numbers.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path, relative to the working directory."},
            "offset": {"type": "integer", "description": "First line to read (1-based). Optional."},
            "limit": {"type": "integer", "description": "How many lines to read. Optional, default 400."},
        },
        "required": ["path"],
    },
    writes=False,
    preview=lambda path, **kw: f"read {path}",
)
def read_file(path, offset=1, limit=400):
    p = resolve(path)
    if not p.is_file():
        raise ToolError(f"No such file: {path}")
    try:
        lines = p.read_text(errors="replace").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        raise ToolError(f"Cannot read {path}: {e}") from e

    offset = max(1, int(offset))
    chunk = lines[offset - 1: offset - 1 + int(limit)]
    if not chunk:
        return f"{path} has {len(lines)} lines; nothing at offset {offset}."
    numbered = "\n".join(f"{i:>6}\t{line}" for i, line in enumerate(chunk, start=offset))
    tail = "" if offset - 1 + len(chunk) >= len(lines) else f"\n\n[file continues to line {len(lines)}]"
    return truncate(numbered) + tail


@tool(
    name="write_file",
    description="Write a file, creating it or overwriting it completely. "
                "For a small change to an existing file prefer edit_file.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string", "description": "The full new contents of the file."},
        },
        "required": ["path", "content"],
    },
    writes=True,
    preview=lambda path, content="", **kw: f"write {path} ({len(content)} chars)",
)
def write_file(path, content):
    p = resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    existed = p.exists()
    try:
        p.write_text(content)
    except OSError as e:
        raise ToolError(f"Cannot write {path}: {e}") from e
    return f"{'Overwrote' if existed else 'Created'} {path} ({len(content)} chars)."


@tool(
    name="edit_file",
    description="Replace an exact string in a file. old_string must occur exactly once, "
                "so include surrounding lines to make it unique.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string", "description": "Exact text to replace, including whitespace."},
            "new_string": {"type": "string", "description": "Replacement text."},
        },
        "required": ["path", "old_string", "new_string"],
    },
    writes=True,
    preview=lambda path, **kw: f"edit {path}",
)
def edit_file(path, old_string, new_string):
    p = resolve(path)
    if not p.is_file():
        raise ToolError(f"No such file: {path}")
    text = p.read_text(errors="replace")

    count = text.count(old_string)
    if count == 0:
        raise ToolError(
            f"old_string not found in {path}. It must match exactly, including indentation "
            "and line breaks. Read the file again and copy the text verbatim."
        )
    if count > 1:
        raise ToolError(
            f"old_string appears {count} times in {path}. Add surrounding lines to make it unique."
        )

    p.write_text(text.replace(old_string, new_string))
    return f"Edited {path}."


@tool(
    name="bash",
    description="Run a shell command in the working directory. "
                "Returns combined stdout and stderr, and the exit code.",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to run."},
        },
        "required": ["command"],
    },
    writes=True,
    preview=lambda command, **kw: f"run: {command}",
)
def bash(command):
    # Own process group, so a timeout or Ctrl-C kills everything the shell started,
    # not just the shell. subprocess.run's timeout would leave `sleep 999 &` running.
    proc = subprocess.Popen(
        command,
        shell=True,
        cwd=config.WORKDIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=config.BASH_TIMEOUT)
    except subprocess.TimeoutExpired:
        kill_group(proc)
        raise ToolError(
            f"Command timed out after {config.BASH_TIMEOUT}s and was killed, "
            "along with anything it started."
        ) from None
    except BaseException:  # KeyboardInterrupt: the new group no longer gets the terminal's SIGINT
        kill_group(proc)
        raise

    out = (stdout + stderr).strip() or "(no output)"
    return truncate(f"exit code: {proc.returncode}\n\n{out}")


def kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()
    # Not communicate(): a process that escaped the group could hold the pipes open forever.
    proc.stdout.close()
    proc.stderr.close()
