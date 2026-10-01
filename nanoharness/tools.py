"""Tool registry: a JSON schema the model sees, plus a Python function that runs."""
import difflib
import json
import os
import re
from collections import Counter
import signal
import subprocess
from contextvars import ContextVar
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
    preview: Callable[..., str]   # shown in the confirmation prompt (a diff for file writes)


REGISTRY: dict[str, Tool] = {}
# Offered only once they have something to work on (see Agent.available), not by default.
ON_DEMAND = {"search_history"}


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


PREVIEW_DIFF_LINES = 40


def diff_preview(label: str, path: str, after) -> str:
    """`label`, then the change as a unified diff, so the user can see what they approve.
    `after(before)` returns the new text. Never raises: on any trouble, just the label."""
    try:
        p = resolve(path)
        before = p.read_text(errors="replace") if p.is_file() else ""
        lines = list(difflib.unified_diff(before.splitlines(), after(before).splitlines(),
                                          "a/" + path, "b/" + path, n=2, lineterm=""))[2:]
    except Exception:
        return label
    if len(lines) > PREVIEW_DIFF_LINES:
        lines = lines[:PREVIEW_DIFF_LINES] + [f"... {len(lines) - PREVIEW_DIFF_LINES} more diff lines"]
    return "\n".join([label] + ["    " + line for line in lines])


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
    preview=lambda path, content="", **kw: diff_preview(
        f"write {path} ({len(content)} chars)", path, lambda before: content),
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
    preview=lambda path, old_string="", new_string="", **kw: diff_preview(
        f"edit {path}", path,
        lambda before: before.replace(old_string, new_string) if before.count(old_string) == 1 else before),
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
        # Never the user's terminal: a command waiting for input would steal keystrokes from
        # the UI (or sit until the timeout). It gets end-of-input at once instead.
        stdin=subprocess.DEVNULL,
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
    note = BACKGROUND_NOTE if sandboxed and starts_background(command) else ""
    return truncate(f"exit code: {proc.returncode}\n\n{out}") + note


# A lone `&` (not `&&`, `2>&1` or `&>`): the command leaves something running. The reviewer
# uses it too (a stand-in server is a fake); here it is a warning to the model.
BACKGROUND = re.compile(r"(?<![&>|])&(?![&>])")
DETACH = re.compile(r"\b(nohup|setsid|disown)\b")
# Seen in real use: `python3 -m http.server 8000 &` gave "exit code: 0, (no output)", the sandbox
# stopped the server when the command ended, and the model said it was running (0/10 in
# the no_false_server_claim eval case).
BACKGROUND_NOTE = ("\n\n[Nothing this command started is still running: the sandbox stops every "
                   "process a command starts when the command ends, background ones (&, nohup) "
                   "included. Do not tell the user it is running. To run a server or watcher, "
                   "give the user the command to run in their own terminal.]")


def starts_background(command):
    return bool(BACKGROUND.search(command) or DETACH.search(command))


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


def file_tree(limit: int = 100) -> str:
    """The project's files, for the system prompt, so the model need not start every task
    with `ls -la` (it did in 9/10 delegation eval runs, then kept exploring itself)."""
    files = [rel(p) for p in walk_files(config.WORKDIR)]
    if not files:
        return "(empty)"
    shown = "\n".join(files[:limit])
    if len(files) > limit:
        shown += f"\n... and {len(files) - limit} more (use glob to find them)"
    return shown


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


# --- earlier conversation --------------------------------------------------

# The running agent's archive of summarized messages, set around each tool call.
ARCHIVE: ContextVar[list] = ContextVar("archive", default=[])
MAX_HISTORY_HITS = 20
# Per message, so one big noisy message (a pasted log) cannot crowd out the rest: seen live,
# 20 hits on filler in one file read hid the one line that mattered in a later message.
MAX_HITS_PER_MESSAGE = 3
# Seen live: a search that matched only "noted the readiness probe requirement" (no path), and
# the model guessed a path. So every result says what to do when the detail isn't in it.
NOT_FOUND_ADVICE = ("If the detail isn't there, don't guess it: tell the user it's missing and ask.")
SNIPPET_BEFORE, SNIPPET_AFTER = 150, 250   # a pasted paragraph can be one 3000-char line


@tool(
    name="search_history",
    description="Search the earlier part of this conversation, which was replaced by a summary "
                "to save context. The summary leaves details out; the full text is kept and "
                "searchable here. Use it whenever the user refers to something from earlier (a "
                "path, value, name or decision) that the summary does not state exactly. Search "
                "for the words the user used, not your own paraphrase: for 'the deploy key from "
                "the ops email', search 'deploy key'. Single keywords work best. Returns a snippet "
                "around each match, numbered by message.",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string",
                        "description": "Case-insensitive regular expression, e.g. 'load balancer|port'."},
        },
        "required": ["pattern"],
    },
    writes=False,
    preview=lambda pattern="", **kw: f"search_history {pattern!r}",
)
def search_history(pattern):
    archive = ARCHIVE.get()
    if not archive:
        return "Nothing has been summarized yet: the whole conversation is still in your context."
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        raise ToolError(f"Invalid regular expression {pattern!r}: {e}.") from None

    # Every match first, so each can be ranked by how common its text is across the archive.
    # Seen live: in `ops|...|load balancer`, "ops" matched every email's From: line and used up
    # the thread's snippets; the one "load balancer" line never showed. Rarest first fixes that.
    texts = {i: searchable(m) for i, m in enumerate(archive, start=1)}
    found = [(i, mt.start(), mt.end(), mt.group(0).lower())
             for i, text in texts.items() for mt in rx.finditer(text) if mt.end() > mt.start()]
    freq = Counter(key for *_, key in found)

    picked, extra = [], Counter()    # (rarity, message, start, end); extra = not shown, per message
    for i in texts:
        mine = []
        for _, s, e, key in sorted((x for x in found if x[0] == i), key=lambda x: (freq[x[3]], x[1])):
            if any(ps - SNIPPET_BEFORE <= s < pe + SNIPPET_AFTER for _, _, ps, pe in mine):
                continue              # already inside a chosen snippet
            if len(mine) == MAX_HITS_PER_MESSAGE:
                extra[i] += 1
                continue
            mine.append((freq[key], i, s, e))
        picked += mine
    more = len(picked) > MAX_HISTORY_HITS
    if more:
        picked = sorted(picked)[:MAX_HISTORY_HITS]

    # Seen live: a search matched only the model's own "noted the probe requirement" reply, and it
    # guessed the path. Say when nothing the user said (or a tool returned) matched.
    only_own = all(archive[i - 1]["role"] == "assistant" for i, *_ in found)

    hits = []
    for _, i, s, e in sorted(picked, key=lambda x: (x[1], x[2])):
        text, m = texts[i], archive[i - 1]
        label = f"tool result ({m.get('tool_name', '?')})" if m["role"] == "tool" else m["role"]
        a, b = max(0, s - SNIPPET_BEFORE), min(len(text), e + SNIPPET_AFTER)
        hits.append(f"[message {i}, {label}] {'...' if a else ''}{' '.join(text[a:b].split())}"
                    f"{'...' if b < len(text) else ''}")
        last_of_message = not any(x[1] == i and x[2] > s for x in picked)
        if last_of_message and extra[i]:
            hits[-1] += f"\n[+{extra[i]} more matches in message {i}; narrow the pattern to see them]"

    if not hits:
        return (f"No matches for {pattern!r} in the {len(archive)} summarized messages. Try other "
                f"words (single keywords work best). {NOT_FOUND_ADVICE}")
    out = "\n\n".join(hits)
    if more:
        out += f"\n\n[stopped at {MAX_HISTORY_HITS} matches; narrow the pattern]"
    if only_own:
        out += ("\n\n[Only your own earlier replies matched, nothing the user said. What they said "
                "is probably worded differently: search again with words from their request.]")
    return truncate(out) + ("\n\n[If none of this states the exact detail you need, search again "
                            f"with other words. {NOT_FOUND_ADVICE}]")


def searchable(m) -> str:
    """A message's text plus its tool calls, so what was written or run is findable too."""
    calls = [f"{c.get('function', {}).get('name', '?')}({json.dumps(c.get('function', {}).get('arguments', {}))})"
             for c in m.get("tool_calls") or []]
    return "\n".join([m.get("content") or ""] + calls)


# --- subagents -------------------------------------------------------------

@tool(
    name="task",
    description="Delegate work to a subagent with a fresh, empty context. Only its final "
                "report comes back, so the files it reads do not fill up your context. "
                "By default it can only read and search (read_file, glob, grep): use that for "
                "questions that need many files read, e.g. 'how is X implemented'. With "
                "write=true it can also write_file, edit_file and run bash, for a "
                "self-contained change; its writes need the user's approval like yours, and "
                "the report ends with the list of files it actually changed. It cannot see "
                "this conversation: write a complete, self-contained instruction.",
    parameters={
        "type": "object",
        "properties": {
            "prompt": {"type": "string",
                       "description": "The full task, including what to report back."},
            "write": {"type": "boolean",
                      "description": "Allow the subagent to change files and run commands. "
                                     "Optional, default false."},
        },
        "required": ["prompt"],
    },
    writes=False,   # the task call itself changes nothing; a writer's own writes are gated
    preview=lambda prompt="", write=False, **kw: f"task{' (write)' if write else ''}: {prompt[:80]}",
)
def task(prompt, write=False):
    from . import subagent   # lazy: subagent imports agent, which imports this module
    return truncate(subagent.run(prompt, write=bool(write)))
