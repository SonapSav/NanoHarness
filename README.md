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

`read_file` · `write_file` · `edit_file` (exact unique string replace) · `glob` · `grep` · `bash`

`glob` and `grep` are pure Python (no ripgrep), read-only so they never prompt, skip
`.git`/`.venv`/`node_modules`/caches and binary files, and ignore symlinks that point outside
`WORKDIR`. A glob without `/` matches file names at any depth, so `*.py` finds them all. Their
descriptions (and `bash`'s) steer the model toward them; it still reaches for `find` now and then.

Every tool failure is returned to the model as `Error: ...` text rather than raised.
Small models usually self-correct when told what went wrong; a crash teaches them nothing.
The wording matters: terse errors let this model narrate intent as fact, so denials and
failures say explicitly that nothing changed.

**`bash` is not sandboxed.** The file tools refuse to resolve outside `WORKDIR`, but
`bash` runs with your full user privileges and only starts in `WORKDIR` — `cat > ~/.bashrc`
would go straight through. The permission prompt is the only thing standing there, so read
the command before approving, and be sparing with `[a]lways` on `bash`. Real containment
(`bwrap`, bind-mounting only `WORKDIR`) is a stage-3 job and required before unattended use.

## Not yet

subagents
