# NanoHarness: project status

*Last updated 2026-09-30.*

NanoHarness is a coding agent harness built from scratch: stdlib-only Python, no SDKs, talking to a
local model (`aeroadvisor-agent`, a Qwen3 fine-tune) on Ollama over the tailnet. The point is to keep
every moving part visible: the wire format, the loop, the tools, the permission gate.

This document records what the original plan asked for, what has been built, how it was verified,
and what is still open.

## The original plan

The first commit (`e587fe7`) was stage 1: a REPL, the agent loop, four tools (`read_file`,
`write_file`, `edit_file`, `bash`) and a permission prompt. Its README listed what was missing:

> **Not yet:** Streaming · context compaction · `grep`/`glob` · session persistence · subagents

It also named one requirement for anything unattended:

> Real containment (`bwrap`, bind-mounting only `WORKDIR`) is a stage-3 job and required before
> unattended use.

## Status at a glance

| Planned item | Status | Commit |
|---|---|---|
| Streaming | ✅ Done | `778937d` |
| Context compaction | ✅ Done | `1f61953` |
| `grep` / `glob` | ✅ Done | `87da0d1` |
| Session persistence | ✅ Done | `1a2c4d9` |
| Subagents | ✅ Done: read-only `5602321`, write-capable `accba09` | |
| `bwrap` sandbox for `bash` | ✅ Done | `73081c9` |

Beyond the plan: an eval set for the live model (`93a2bfe`), reasoning mode on by default
(`2d5302b`), a project file listing in the main and subagent prompts (`914b5bb`, `4295af3`), and five
fixes to the stage-1 code found while reading it.

Current state: **105 offline tests pass** (`tests/`, no model or network), and the **eval baseline
is 60/60** (12 cases × 5 runs against the live model). A 13th, harder case, `rename_across_files`,
is measured separately: 18/19 (see [Harder eval cases](#harder-eval-cases)).

## What was built

### Fixes to the stage-1 code

Found by reading the code before adding anything:

| Problem | Fix | Commit |
|---|---|---|
| Ctrl-C at a permission prompt left tool calls with no result, so the next request was malformed | Every tool call now always gets a result (interrupted, or skipped) | `938cc00` |
| A failed request left the user's message in history, so a retry sent it twice | Dropped if the model never answered; kept once tools have run (they changed the disk) | `938cc00` |
| The user couldn't tell which of those two happened | The REPL says "send it again" or "say continue" | `c6f7cfb` |
| The system prompt was frozen at import and could name the wrong directory | Built on demand | `be93fb7` |
| A `bash` timeout killed only the shell; its children kept running | Each command gets its own process group, killed on timeout or Ctrl-C | `b59ad2d` |

### Planned features

**Context compaction** (`context.py`). Past `num_ctx`, Ollama silently drops the oldest tokens,
system prompt first. Before every request the harness estimates the size (3 chars/token, measured on
this model) and, past 75% of the window (`NANO_COMPACT_AT`), shrinks the history cheapest step first:
shorten old tool outputs, then summarize earlier turns with the model, then shorten older outputs in
the current turn. Verified live: an earlier turn became an accurate 170-word summary and the model
carried on correctly.

**Streaming** (`client.py`). Replies print as they are generated; reasoning streams in grey. Measured
live: first text after 0.18 s on a reply that took 26 s in total. Mid-stream errors, truncated
streams and dropped connections all surface as the usual model error.

**`grep` / `glob`** (`tools.py`). Pure Python, read-only (no prompts), skipping `.git`, `.venv`,
`node_modules`, caches and binary files, and never following a symlink out of `WORKDIR`. Tool
descriptions steer the model to them: in live runs, content searches went through `grep` 4/4 and
`bash` use halved.

**Session persistence** (`session.py`). Every message is saved atomically to a `0600` JSON file in
`~/.local/share/nanoharness/sessions/`, outside the project so the agent's own tools can't touch it.
`nanoharness -c` continues the latest session for the current directory; `--resume` picks from a list.
`[a]lways` approvals are deliberately not saved. Verified live: a resumed session recalled a codeword
stored in the previous one.

**Subagents** (`subagent.py`, the `task` tool). A fresh agent with its own history; only its final
report returns, so the files it reads stay out of the main context (live: ~2k tokens with `task` vs
~12k reading the same files directly). Two kinds:

- *Research* (default): `read_file`/`glob`/`grep` only, never prompts, cannot change anything.
- *`write=true`*: also `write_file`/`edit_file`/`bash`, through the **same** permission gate as the
  main agent (prompts labelled `subagent:`). The harness appends the list of files it actually changed,
  taken from the calls that succeeded rather than from the subagent's own account.

Subagents cannot start subagents, and get 15 rounds before being asked for a partial report.

**`bash` sandbox** (`sandbox.py`). An allow-list in bubblewrap: `/usr`, `/etc`, `/opt` read-only;
`WORKDIR` read-write; `/tmp` and `$HOME` empty and wiped after every command; a minimal `/dev`. `/home`,
`/run` (including `docker.sock`), `/media`, `/mnt` and `/var` don't exist inside, which matters because
this user is in the `docker` and `disk` groups. The environment is cleared so tokens can't leak in.
Verified by trying to escape it: the SSH key, `docker.sock`, other mounts and disks were all hidden,
writes to `/usr` and `/etc` failed, and a planted `GH_TOKEN` didn't get in.

One lesson from building it: the first version's availability check used a cut-down mount set, failed
on a missing loader link, and `auto` mode then silently ran `bash` unsandboxed. The check now runs the
exact command `bash` uses, and a test fails if an installed `bwrap` doesn't work.

### Beyond the plan

**Eval set** (`evals/`). `tests/` proves the harness does what it says; `evals/` measures how well
this model behaves in it. Each case is a fresh temp directory, one prompt to the live model, and
checks on the outcome, preferring the world (files, a command that must pass) over the answer text.
`python -m evals` reports pass rates and tool counts, optionally against a baseline.

**Reasoning mode on by default** (`NANO_THINK=1`). Measured over 20 runs, it took the FizzBuzz case
from 9/20 to 20/20: without it, the model kept swapping Fizz and Buzz, running the file, seeing the
wrong output and reporting success. It costs about 1.7× the time per task. A system-prompt rule telling
the model to check its output was tried first and made no difference (9/20 vs 8/20), so it was
dropped.

**Project file listing in the prompts.** The model opened 9 of 10 delegation runs with `ls -la` and
then kept exploring itself, even when told to use `task`. Listing up to 100 project files in the
system prompt removed that: `delegate_when_asked` went from 3/5 to 10/10 and `list_test_files` from
3/5 to 10/10. In subagents, refused `bash` calls dropped from 18 to 1.

**Venv rule for the sandbox.** Installs outside `WORKDIR` vanish between commands, and a subagent spent
11 calls failing to install pytest. The prompts now say to install into a project `.venv`; live runs
then went straight to venv, install, test.

## Eval baseline

At commit `3dbdeef`, 5 runs per case: **60/60**.

| Group | Cases | What they check |
|---|---|---|
| search | `find_definition`, `list_test_files` | finds code with `grep`/`glob`, not `bash` |
| delegate | `delegate_when_asked`, `delegate_big_project`, `big_project_question` | uses `task` when asked; on a project 3.5× the context window, keeps the main history under 10k tokens |
| honesty | `actually_runs_command`, `respects_denial`, `no_invented_contents` | runs what it is asked to run; respects a denial; doesn't invent a missing file's contents |
| edit | `fix_failing_test`, `precise_edit`, `create_and_run`, `rename_across_files`* | fixes a bug without touching the tests; a precise edit; writes and runs a correct file; renames a function across files and nothing else |
| sandbox | `venv_install` | installs into a project venv |

\* Added after the baseline, so not in it; its own numbers are under [Harder eval cases](#harder-eval-cases).

Results are saved in `evals/results/` (gitignored, so they exist only on this machine). The baseline
file is `evals/results/baseline-3dbdeef.json`. Compare with:

```bash
.venv/bin/python -m evals --baseline evals/results/baseline-3dbdeef.json
```

## Pending

### Harder eval cases

At 100%, the eval can catch regressions but can't show whether a change is an improvement. It needs
cases the model currently fails some of the time.

**Done: `rename_across_files`.** Rename `calc_total` to `order_total` in a small package, tests and
README included. It passes only if the tests pass *and* every file equals the original with exactly
`\bcalc_total\b` replaced (the new `files_equal` check names the first differing line). Traps: a
lookalike, `calc_total_weight`, that a blind substring replace also renames (and the tests still
pass, so only the exact check sees it); references by string (`__all__`, a `getattr` table) that
following imports misses; a README mention.

Live, over two batches: **18/19 finished runs passed**. The one real failure renamed the lookalike
too. A 20th run was cut off by the runner being killed, and one more "failed" only because Ollama
stalled for 300 s after the work was done (both checks pass on its files). The usual approach is
careful: `grep`, read every file, one `edit_file` per file, run the tests, `grep` again (~20 tool
calls, ~60 s). So the case is harder than the baseline but still near the ceiling.

Remaining candidates:

- **Long sessions that trigger compaction**: plant facts early, push the history past the budget,
  then check what the model still remembers and whether it still acts correctly.
- **Write-capable subagent**: check that the harness's change list matches what is on disk, and that
  a denial inside the subagent is reported honestly.
- **Recovering from a failed command**: e.g. a missing dependency, checking that the model recovers
  without looping through the same failing command.

### Open behaviour problems

- **The model doesn't delegate on its own.** When asked it now does so reliably (15/15), but unprompted
  it never has. So far that hasn't mattered: on the big-project case it used `grep` and partial reads
  and kept the main history small. A case where that strategy isn't enough would show whether it
  matters.
- **It doesn't verify its own output.** Reasoning mode fixed the FizzBuzz failures by getting the code
  right first time, not by making the model check what it ran (tool calls stayed at exactly 2: write,
  run). A task where the first attempt is usually wrong would expose this again.
- **Made-up command output is rare but real**: about 1 in 20 runs without reasoning mode.

### Known limitations

- **The sandbox guards `bash` only.** It doesn't stop `rm -rf .` inside `WORKDIR`; the permission
  prompt is the gate for that. For unattended use, set `NANO_SANDBOX=on` so a broken `bwrap` refuses
  to run instead of falling back.
- **The network is on inside the sandbox by default** (`NANO_SANDBOX_NET=0` to cut it).
- **Tools installed in your home directory** (nvm, pyenv) are hidden in the sandbox unless exposed with
  `NANO_SANDBOX_RO_PATHS`.
- **Two REPLs resuming the same session** overwrite each other; whichever saves last wins.
- **Old sessions are never cleaned up.**
- **Compaction's token count is an estimate** (3 chars/token); code-heavy sessions compact a bit early.
- **The file listing is a snapshot** from session start and goes stale as files change (the prompt
  says so).
- **Eval samples are small** (5–10 runs per case). Early on, 3 runs of FizzBuzz looked like 2/3 and
  turned out to be 45% over 20; judge changes with `-n 10` or more on the cases they target.
- **The eval runner counts an Ollama stall as a failed run**, even when the work on disk passes every
  check. Read the failure line before trusting a lower pass rate.
