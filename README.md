# NanoHarness

A coding agent harness built from scratch. No SDKs, no dependencies — stdlib only,
so the wire format between you and the model stays visible.

Streaming, context compaction, `grep`/`glob`, saved sessions, subagents (read-only or
write-capable) and a bubblewrap sandbox for `bash`. 228 offline tests; eval baseline 188/210
against the live model. What was planned, what's done and what's pending:
[`docs/project.md`](docs/project.md).

## Run

One-time install:

```bash
cd ~/Development/NanoHarness
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
ln -s ~/Development/NanoHarness/.venv/bin/nanoharness ~/.local/bin/
```

Then, from whatever directory you want the agent to work in:

```bash
cd ~/some/scratch/dir
nanoharness              # that directory becomes the sandbox root
nanoharness --yolo       # skip permission prompts
nanoharness -c           # continue the latest session in this directory
nanoharness --resume     # pick a saved session from a list (or --resume ID)
nanoharness --plain      # the plain line-by-line output (the default when not in a terminal)
```

In a terminal the output looks like Claude Code's or Pi's, inline so it stays in the
terminal's scrollback: the model's reasoning folds to `✻ Thought for 4s`, each tool call is a
`⏺ Write(weather.py)` line with a short `⎿` result (edits as a diff on faded red and green bars, the changed words in a stronger tint), answers get light
markdown, and a spinner with a timer shows whenever the model or a tool is busy. Input is a box
that stays at the bottom while the conversation scrolls above it, with a footer (directory, model, context used, the last turn's time in ms and tokens ↑ in / ↓ out,
key hints):

| key | does |
|---|---|
| Enter | send (during a turn: queue it for when the turn ends) |
| Alt+Enter, Ctrl+J | new line; pasted text keeps its lines and goes as one message |
| ↑ / ↓ | move between lines; on the first/last line, earlier input (kept across runs) |
| Ctrl+O | the last result shown cut short (`… +9 lines`), in full; also `/expand` |
| Esc, Ctrl+C | during a turn: interrupt it |
| Ctrl+C | otherwise: clear the box; twice on an empty box: exit |
| Ctrl+D | exit (empty box) |

Permission prompts and the `/resume` picker are menus: ↑/↓ and Enter, or `y`/`a`/`n`, Esc for no.

The startup panel shows a logo slot with the model, directory and session on the left, and the
tools and safety settings on the right; `/status` adds the host and how full the context is,
`/help` lists commands and keys. `/undo` takes back the file changes of the last turn that made
any (edited files restored, new ones deleted; files changed only through `bash` are listed, not
restored), and tells the agent. `/note <text>` keeps a remark about the session (what went wrong,
what felt clumsy) in `~/.local/share/nanoharness/notes.jsonl`, with the session id and how far
in it was, so the moment can be found again. Stdlib only.

**Servers.** A `bash` command can't leave anything running: the sandbox stops whatever it
started when it returns. For a web server or a watcher the agent uses `start_service`, which
keeps it running in the same sandbox and checks it really started: a port that already answers
is refused (someone else's server), and it waits for the port to answer or reports the error and
log. `/services` lists them (`/services stop <name>`), the footer shows `● 1 service`, and they all
stop when you quit.

**Your own logo:** put one-colour text art in `~/.config/nanoharness/logo.txt` (or point `NANO_LOGO`
at a file). About 30 columns by 10-12 rows fits best; up to 36 by 16 is shown, larger is trimmed.
Braille (`⣿⡇⠛`) or half blocks (`▀▄█`) give the most detail at that size.

Every conversation is saved after each message to `~/.local/share/nanoharness/sessions/`
(one `0600` JSON file each; outside the project so the agent's tools can't touch it). A
session resumes only in the directory it started in, with today's system prompt. `[a]lways`
approvals are deliberately not saved: they last for the running REPL (across `/reset` and
`/resume`), never into a later one. In the REPL, `/sessions` lists them numbered, `/resume 2` (or
`/resume <id>`, or `/resume` for a picker) switches to one, and `/reset` starts a new one.
Nothing cleans old sessions up; delete the files when you like. When compaction summarizes
earlier turns, the originals are kept in the session file too, and the agent gets a
`search_history` tool to look details up in them.

No activation needed — the entry point's shebang points straight at `.venv/bin/python3`.
The install is editable, so source edits take effect immediately. The command is
`nanoharness`, never `nano`; that's the text editor.

Tests (offline, no model, no network):

```bash
.venv/bin/python3 -m pytest tests/ -q
```

Ollama runs on two servers over the tailnet, each a 12 GB RTX 3060. The default is server 2
(`100.76.19.74`, 32 GB RAM) with `nano-35b` (`qwen3.5:35b-a3b`, a 35B mixture-of-experts with 3B
active, split between GPU and RAM, 49k context). Server 1 (`generators`, `100.66.104.56`) has the
earlier `aeroadvisor-agent` (`qwen3.5:9b`); to use it:

```bash
OLLAMA_HOST=http://100.66.104.56:11434 NANO_MODEL=aeroadvisor-agent:latest NANO_NUM_CTX=49152 nanoharness
```

The `10.50.0.33` LAN
address is not routable from here: no tailnet node advertises `10.50.0.0/x`
and an exit node is active, so that traffic is swallowed.

Config is all environment variables (see `nanoharness/config.py`):

| var | default | why you'd change it |
|---|---|---|
| `OLLAMA_HOST` |  `http://100.76.19.74:11434` | Ollama on another machine |
| `NANO_MODEL` | `nano-35b` | whatever `ollama list` shows |
| `NANO_NUM_CTX` | `49152` | matches the model's Modelfile; sending less silently truncates history |
| `NANO_THINK` | `1` | Qwen3 reasoning mode, streamed in italic grey. `0` is ~1.7x faster but, in evals, shipped broken code as working far more often (FizzBuzz 9/20 vs 20/20) |
| `NANO_STREAM` | `1` | `0` prints each reply only once it is complete |
| `NANO_NUM_PREDICT` | `16384` | tokens one reply may generate, reasoning included; hitting it is an error (`-1` = no cap) |
| `NANO_REVIEW` | `1` | after a turn that wrote, a separate call checks whether the change makes a check pass without fixing it, and warns you |
| `NANO_REVIEW_TEMPERATURE` | `0.6` | the review call's own temperature (the agent keeps `NANO_TEMPERATURE`); lower made it loop until cut off |
| `NANO_COMPACT_AT` | `0.75` | fraction of `NANO_NUM_CTX` at which history gets shrunk; the rest is room for the reply |
| `NANO_MAX_STEPS` | `25` | tool rounds per user turn before giving up |
| `NANO_SUBAGENT_MAX_STEPS` | `15` | tool rounds a `task` subagent gets before it must report |
| `NANO_WORKDIR` | cwd | the sandbox root; tools refuse to leave it |
| `NANO_SESSION_DIR` | `~/.local/share/nanoharness/sessions` | keep saved conversations elsewhere |

## Shape

| file | role |
|---|---|
| `config.py` | tunables and the system prompt (the agent and its subagents append a listing of up to 100 project files, so the model doesn't open every task with `ls -la`) |
| `client.py` | `POST /api/chat` — messages + tool schemas in, streamed fragments assembled into one assistant message out |
| `tools.py` | the registry: JSON schema (what the model sees) + function (what runs) |
| `permissions.py` | the gate between "the model asked" and "it ran" |
| `context.py` | keeps the history inside `num_ctx` |
| `session.py` | saves and resumes conversations |
| `subagent.py` | the `task` tool's fresh agent: read-only by default, write-capable on request |
| `sandbox.py` | the bubblewrap command line `bash` runs in |
| `review.py` | optional second look after a turn that wrote: does the change fake a pass? (on by default, `NANO_REVIEW=0` to skip) |
| `agent.py` | the loop |
| `ui.py` | what the agent shows or asks goes through here; `PlainUI` is the original output, which the evals read |
| `banner.py` | the startup panel: logo slot, model, tools, safety |
| `services.py` | long-running processes (servers) the agent starts with `start_service`, checked and stopped on exit |
| `tui.py` | the terminal look: `⏺` tool lines, folded reasoning, diffs, markdown, spinner, the input box |
| `keys.py` | keyboard input for the box: key parsing, the line editor, menus |
| `commands.py` | the user's slash commands: one list for `/help`, the panel and the system prompt |
| `cli.py` | REPL |
| `evals/` | live-model eval cases and runner (`python -m evals`); `python -m evals.review_replay` runs `review.py` over recorded runs |
| `docs/project.md` | status against the original plan, and what's pending |

The whole idea is `agent.py`:

```
append user message
repeat up to MAX_STEPS:
    reply = model.chat(history, tools)
    append reply
    if no tool calls: return reply.content
    for each call: check permission, run it, append the result as a tool message
```

## Context

Past `num_ctx`, Ollama silently drops the oldest tokens, system prompt first. So before
every request `context.py` estimates the size (chars / 3, measured on this model) and, once
it passes `NANO_COMPACT_AT`, shrinks the history, cheapest step first:

1. shorten long tool outputs from earlier turns
2. replace earlier turns with a model-written summary
3. shorten older tool outputs in the current turn, keeping the latest two

The system prompt and the current request are never touched. A grey line says what happened.

## Tools

`read_file` · `write_file` · `edit_file` (exact unique string replace) · `glob` · `grep` · `bash` · `task`

`glob` and `grep` are pure Python (no ripgrep), read-only so they never prompt, skip
`.git`/`.venv`/`node_modules`/caches and binary files, and ignore symlinks that point outside
`WORKDIR`. A glob without `/` matches file names at any depth, so `*.py` finds them all. Their
descriptions (and `bash`'s) steer the model toward them, and with the file listing in the prompt
it no longer reaches for `ls`/`find` in the evals.

Every tool failure is returned to the model as `Error: ...` text rather than raised.
Small models usually self-correct when told what went wrong; a crash teaches them nothing.
The wording matters: terse errors let this model narrate intent as fact, so denials and
failures say explicitly that nothing changed.

`task` hands work to a **subagent**: a fresh agent with its own empty history. Only its final
report comes back; the files it read stay out of the main history (in a live run: ~2k tokens
with `task` against ~12k reading the same files directly). It cannot see the conversation and
cannot start subagents of its own. It gets `NANO_SUBAGENT_MAX_STEPS` rounds, then is asked
for a partial report.

- **Research** (default): only `read_file`/`glob`/`grep`, so it never prompts and cannot
  change anything.
- **`write=true`**: also `write_file`/`edit_file`/`bash`. Its writes go through the *same*
  permission gate as the main agent: they prompt, labelled `subagent:`, unless you run
  `--yolo` or already chose `[a]lways` for that tool. The harness, not the subagent, appends
  `[harness: files changed by the subagent: ...]` from the calls that actually succeeded, so
  the main agent doesn't have to take its word. `bash` effects are counted, not tracked.

The model doesn't delegate on its own, so ask for it ("use task to find out ...", "use task
with write=true to ..."); when asked it does, reliably (15/15 in evals). Unprompted, on a
project 3.5x the context window, it used `grep` and partial reads and kept the main history
small, so it hasn't needed to.

### The `bash` sandbox

`bash` runs inside [bubblewrap](https://github.com/containers/bubblewrap) (`apt install
bubblewrap`). It is an allow-list: the sandbox starts empty and gets only

| inside | access |
|---|---|
| `/usr`, `/etc`, `/opt` (+ the `/bin` `/lib` `/lib64` `/sbin` links) | read-only |
| `WORKDIR` | read-write |
| `/tmp`, `$HOME` | empty, wiped after every command |
| `/dev` | minimal: `null`, `random`, `tty`, ... no disks |

Everything else (`/home` with your keys and tokens, `/run` with `docker.sock` and keyrings,
`/media`, `/mnt`, `/var`, other mounts) does not exist inside. The environment is cleared to
`PATH`, locale and `TERM`, so tokens don't leak in. Each command gets its own PID namespace
and dies with the harness. The startup panel's `sandbox` line says what is in force.

It does **not** stop writes inside `WORKDIR` (`rm -rf .` still works; the permission prompt
is the gate for that), or network access unless `NANO_SANDBOX_NET=0`. Tools that live in
your home directory (nvm, pyenv) are hidden too; expose them read-only with
`NANO_SANDBOX_RO_PATHS=~/.nvm:~/.pyenv`.

| var | default | |
|---|---|---|
| `NANO_SANDBOX` | `auto` | `auto`: sandbox if bwrap works, else warn in red and run unsandboxed · `on`: refuse to run `bash` without it · `off`: full user privileges |
| `NANO_SANDBOX_NET` | `1` | `0` cuts the network inside the sandbox |
| `NANO_SANDBOX_RO_PATHS` | | extra `:`-separated read-only paths |

For unattended use, set `NANO_SANDBOX=on` so a broken bwrap fails closed.

Anything installed during a command is gone by the next one (`$HOME` is wiped, `/usr` is
read-only). Keep a project's dev tools in a venv inside it (`python3 -m venv .venv`), where
the sandbox can see and keep them.

## Evals

`tests/` is offline and proves the harness does what it says. `evals/` asks a different
question: how well does *this model* behave in it? Each case is a fresh temp workdir, one
prompt to the live model, and checks on the outcome, preferring the world (files, a command
that must pass) over the answer text:

```bash
.venv/bin/python -m evals                          # all 17 cases, 3 runs each (~20 min on one server)
.venv/bin/python -m evals -k honesty -n 5          # by name or group
.venv/bin/python -m evals -k coding -n 10          # the coding group: opt-in, ~3-4 min a run
.venv/bin/python -m evals --with-coding            # everything, the coding group included
.venv/bin/python -m evals --baseline evals/results/baseline-ccc4df4.json
.venv/bin/python -m evals --hosts 100.66.104.56,100.76.19.74   # split runs across servers
# (both servers must have the model: nano-35b is only on server 2; the 188/210 baseline is
#  aeroadvisor-agent's, so run it with NANO_MODEL=aeroadvisor-agent:latest NANO_NUM_CTX=49152)
```

With `--hosts` (or `NANO_EVAL_HOSTS`), each server gets a worker process that takes the next
run from a shared queue; two servers halve the time. They must serve the same model build
with the same settings. Each result records its server, and the report adds a per-server line.

| group | what it checks |
|---|---|
| search | finds code with `grep`/`glob`, not `bash` |
| delegate | uses `task` when asked; answers a question about a project 3.5x the context window while keeping the main history under 10k tokens (however it gets there) |
| honesty | actually runs the command; respects a denial; doesn't invent a missing file's contents, a missing setting, or what compaction lost; doesn't fake tests it can't pass |
| edit | fixes a failing test without touching it; a precise edit; creates and runs a file; renames a function across files and nothing else |
| context | after a forced summary, still acts on facts set early in the session |
| sandbox | installs into a project venv |

Results (every check, answer and full transcript) go to `evals/results/` (gitignored). With
temperature 0.6 one run proves little: judge a prompt change by pass rates over several
runs, against a baseline taken just before it. Add a case whenever the model does something
worth never seeing again (`evals/cases.py`).

**Baseline** at `ccc4df4`, 10 runs per case over two servers, reviewer on: **188/210**. 19 cases
are at 187/190 and catch regressions; the two fake cases (`stops_when_blocked`,
`stops_when_data_missing`) fail on purpose because the model fakes tests it can't pass, and the
reviewer flagged all 19 fakes. Per-case numbers and what's pending:
[`docs/project.md`](docs/project.md#eval-baseline).

