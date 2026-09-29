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
```

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
| `NANO_THINK` | `0` | `1` turns on Qwen3 reasoning mode |
| `NANO_MAX_STEPS` | `25` | tool rounds per user turn before giving up |
| `NANO_WORKDIR` | cwd | the sandbox root; tools refuse to leave it |

## Shape

| file | role |
|---|---|
| `config.py` | tunables and the system prompt |
| `client.py` | `POST /api/chat` — messages + tool schemas in, assistant message out |
| `tools.py` | the registry: JSON schema (what the model sees) + function (what runs) |
| `permissions.py` | the gate between "the model asked" and "it ran" |
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

## Tools

`read_file` · `write_file` · `edit_file` (exact unique string replace) · `bash`

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

Streaming · context compaction · `grep`/`glob` · session persistence · subagents
