# NanoHarness

A coding agent harness built from scratch. No SDKs, no dependencies — stdlib only,
so the wire format between you and the model stays visible.

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
```

Every conversation is saved after each message to `~/.local/share/nanoharness/sessions/`
(one `0600` JSON file each; outside the project so the agent's tools can't touch it). A
session resumes only in the directory it started in, with today's system prompt. `[a]lways`
approvals are deliberately not saved. `/sessions` lists them, `/reset` starts a new one.
Nothing cleans old sessions up; delete the files when you like.

No activation needed — the entry point's shebang points straight at `.venv/bin/python3`.
The install is editable, so source edits take effect immediately. The command is
`nanoharness`, never `nano`; that's the text editor.

Tests (offline, no model, no network):

```bash
.venv/bin/python3 -m pytest tests/ -q
```

Ollama runs on `generators` (tailnet `100.66.104.56`). The `10.50.0.33` LAN
address is not routable from here: no tailnet node advertises `10.50.0.0/x`
and an exit node is active, so that traffic is swallowed.

Config is all environment variables (see `nanoharness/config.py`):

| var | default | why you'd change it |
|---|---|---|
| `OLLAMA_HOST` |  `http://100.66.104.56:11434` | Ollama on another machine |
| `NANO_MODEL` | `aeroadvisor-agent:latest` | whatever `ollama list` shows |
| `NANO_NUM_CTX` | `49152` | matches the model's Modelfile; sending less silently truncates history |
| `NANO_THINK` | `0` | `1` turns on Qwen3 reasoning mode (streamed in grey) |
| `NANO_STREAM` | `1` | `0` prints each reply only once it is complete |
| `NANO_COMPACT_AT` | `0.75` | fraction of `NANO_NUM_CTX` at which history gets shrunk; the rest is room for the reply |
| `NANO_MAX_STEPS` | `25` | tool rounds per user turn before giving up |
| `NANO_SUBAGENT_MAX_STEPS` | `15` | tool rounds a `task` subagent gets before it must report |
| `NANO_WORKDIR` | cwd | the sandbox root; tools refuse to leave it |
| `NANO_SESSION_DIR` | `~/.local/share/nanoharness/sessions` | keep saved conversations elsewhere |

## Shape

| file | role |
|---|---|
| `config.py` | tunables and the system prompt |
| `client.py` | `POST /api/chat` — messages + tool schemas in, streamed fragments assembled into one assistant message out |
| `tools.py` | the registry: JSON schema (what the model sees) + function (what runs) |
| `permissions.py` | the gate between "the model asked" and "it ran" |
| `context.py` | keeps the history inside `num_ctx` |
| `session.py` | saves and resumes conversations |
| `subagent.py` | the `task` tool's fresh, read-only agent |
| `agent.py` | the loop |
| `cli.py` | REPL |

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
descriptions (and `bash`'s) steer the model toward them; it still reaches for `find` now and then.

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

The model rarely delegates on its own, so ask for it ("use task to find out ...", "use task
with write=true to ...").

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
and dies with the harness. The banner's `sandbox` line says what is in force.

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

## Not yet

An eval set: prompt changes (e.g. getting the model to delegate unprompted) are currently
judged by eye on a handful of live runs.
