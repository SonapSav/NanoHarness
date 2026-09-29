"""Tool registry: a JSON schema the model sees, plus a Python function that runs."""
import os
import re
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import config, sandbox


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


def schemas(names=None):
    """The `tools` array sent to Ollama, in OpenAI function-calling shape. `names`
    limits it to those tools (a subagent sees fewer)."""
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
        if names is None or t.name in names
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
    description="Run a bash command in the working directory. "
                "Returns combined stdout and stderr, and the exit code. "
                "Usually sandboxed: only the working directory is writable, the home "
                "directory and /tmp start empty and are wiped after each command. "
                "Do not use it to find files or search their contents: use glob and grep, "
                "which are faster and need no approval.",
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
    try:
        sandboxed = sandbox.active()
    except sandbox.SandboxUnavailable as e:
        raise ToolError(f"{e}. The command did NOT run.") from None
    argv = sandbox.argv(command) if sandboxed else ["/bin/bash", "-c", command]
    # Own process group, so a timeout or Ctrl-C kills everything the shell started,
    # not just the shell. subprocess.run's timeout would leave `sleep 999 &` running.
    proc = subprocess.Popen(
        argv,
        cwd=config.WORKDIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",   # binary output must not crash the tool
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


# --- search ----------------------------------------------------------------

# Never worth searching, and big enough (.venv alone is thousands of files) to drown results.
SKIP_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__",
             ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".idea", ".vscode"}
MAX_GLOB_RESULTS = 200
MAX_GREP_MATCHES = 100
MAX_GREP_FILE_BYTES = 2_000_000
MAX_LINE_CHARS = 200


def walk_files(root: Path):
    """Every file under root, skipping SKIP_DIRS and *.egg-info, never leaving WORKDIR.
    os.walk does not follow directory symlinks; file symlinks are checked by resolving."""
    if root.is_file():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in SKIP_DIRS and not d.endswith(".egg-info"))
        for name in sorted(filenames):
            p = Path(dirpath) / name
            real = p.resolve()
            if real == config.WORKDIR or config.WORKDIR in real.parents:
                yield p


def glob_regex(pattern: str) -> re.Pattern:
    """Translate a glob to a regex over /-separated relative paths. `**/` is zero or more
    directories, `*` and `?` stay within one path segment. (Path.match only learned `**`
    in 3.13.)"""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def glob_matcher(pattern: str):
    """A pattern with no '/' matches the file name at any depth, so `*.py` finds all of them."""
    rx = glob_regex(pattern)
    if "/" in pattern:
        return lambda rel: rx.match(rel) is not None
    return lambda rel: rx.match(rel.rsplit("/", 1)[-1]) is not None


def is_binary(p: Path) -> bool:
    try:
        with p.open("rb") as f:
            return b"\0" in f.read(8192)
    except OSError:
        return True


def rel(p: Path) -> str:
    return p.relative_to(config.WORKDIR).as_posix()


@tool(
    name="glob",
    description="Find files by NAME (not by what is inside them; use grep for that). "
                "`**` matches any number of directories, e.g. "
                "'src/**/*.py'. A pattern without '/' matches file names at any depth, so "
                "'*.py' finds every Python file. Skips .git, .venv, node_modules and caches.",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern, e.g. '*.py' or 'tests/**/test_*.py'."},
            "path": {"type": "string", "description": "Directory to search in. Optional, default the working directory."},
        },
        "required": ["pattern"],
    },
    writes=False,
    preview=lambda pattern, path=".", **kw: f"glob {pattern} in {path}",
)
def glob(pattern, path="."):
    root = resolve(path)
    if not root.is_dir():
        raise ToolError(f"No such directory: {path}")
    matches = glob_matcher(pattern)
    # Match against the path relative to `path`, so 'tests/*.py' means what it says from there.
    found = [rel(p) for p in walk_files(root) if matches(p.relative_to(root).as_posix())]
    if not found:
        return f"No files match {pattern!r} in {path}."
    shown = "\n".join(found[:MAX_GLOB_RESULTS])
    if len(found) > MAX_GLOB_RESULTS:
        shown += f"\n\n[showing {MAX_GLOB_RESULTS} of {len(found)} files; use a narrower pattern]"
    return shown


@tool(
    name="grep",
    description="Search INSIDE files for a regular expression (Python syntax), e.g. to "
                "find where a function or class is defined or used. Returns "
                "matching lines as path:line: text. Skips binary files, .git, .venv, "
                "node_modules and caches.",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regular expression, e.g. 'def \\w+' or 'TODO'."},
            "path": {"type": "string", "description": "File or directory to search. Optional, default the working directory."},
            "glob": {"type": "string", "description": "Only search files whose name matches this, e.g. '*.py'. Optional."},
            "ignore_case": {"type": "boolean", "description": "Case-insensitive match. Optional, default false."},
        },
        "required": ["pattern"],
    },
    writes=False,
    preview=lambda pattern, path=".", **kw: f"grep {pattern!r} in {path}",
)
def grep(pattern, path=".", glob=None, ignore_case=False):
    try:
        rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as e:
        raise ToolError(f"Invalid regular expression {pattern!r}: {e}. "
                        "Escape special characters like ( ) [ ] . * with a backslash.") from None
    root = resolve(path)
    if not root.exists():
        raise ToolError(f"No such file or directory: {path}")
    name_ok = glob_matcher(glob) if glob else (lambda r: True)

    hits, files_hit, more = [], set(), False
    for p in walk_files(root):
        r = rel(p)
        if not name_ok(r) or is_binary(p):
            continue
        try:
            if p.stat().st_size > MAX_GREP_FILE_BYTES:
                continue
            lines = p.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for n, line in enumerate(lines, start=1):
            if rx.search(line):
                if len(hits) == MAX_GREP_MATCHES:
                    more = True
                    break
                text = line.strip()
                if len(text) > MAX_LINE_CHARS:
                    text = text[:MAX_LINE_CHARS] + " [...]"
                hits.append(f"{r}:{n}: {text}")
                files_hit.add(r)
        if more:
            break

    if not hits:
        return f"No matches for {pattern!r} in {path}" + (f" (files matching {glob!r})." if glob else ".")
    out = "\n".join(hits)
    if more:
        out += (f"\n\n[stopped at {MAX_GREP_MATCHES} matches; narrow the pattern, "
                "or pass path or glob]")
    return truncate(out)


# --- subagents -------------------------------------------------------------

@tool(
    name="task",
    description="Delegate a research question to a subagent with a fresh, empty context. "
                "It can use read_file, glob and grep (it cannot change files or run commands) "
                "and returns only its final report, so the files it reads do not fill up your "
                "context. Use it for questions that need many files read, e.g. 'how is X "
                "implemented' or 'find every place that does Y'. It cannot see this "
                "conversation: write a complete, self-contained instruction.",
    parameters={
        "type": "object",
        "properties": {
            "prompt": {"type": "string",
                       "description": "The full task, including what to report back."},
        },
        "required": ["prompt"],
    },
    writes=False,   # its tools are read-only
    preview=lambda prompt="", **kw: f"task: {prompt[:80]}",
)
def task(prompt):
    from . import subagent   # lazy: subagent imports agent, which imports this module
    return truncate(subagent.run(prompt))
