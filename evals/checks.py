"""Checks: small named predicates over one finished run.

Prefer checks on the world (files, a command that must pass) over checks on the answer
text: the model's account of what it did is exactly what we cannot take on trust.
"""
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class Run:
    """What a check gets to look at."""
    workdir: Path
    messages: list
    answer: str
    tools: Counter = field(default_factory=Counter)   # main-agent tool calls by name
    prompts: list = field(default_factory=list)       # permission prompts shown

    def commands(self):
        """Every bash command the main agent ran."""
        return [c["function"].get("arguments", {}).get("command", "")
                for m in self.messages for c in m.get("tool_calls") or []
                if c.get("function", {}).get("name") == "bash"
                and isinstance(c["function"].get("arguments"), dict)]


@dataclass
class Check:
    name: str
    fn: Callable[[Run], tuple]   # -> (ok: bool, detail: str)

    def __call__(self, run: Run):
        try:
            ok, detail = self.fn(run)
        except Exception as e:     # a broken check is a failed check, not a crashed eval
            ok, detail = False, f"check raised {type(e).__name__}: {e}"
        return {"check": self.name, "ok": bool(ok), "detail": detail}


def short(text, n=160):
    return " ".join(str(text).split())[:n]


# --- tools ----------------------------------------------------------------

def called(tool):
    return Check(f"called {tool}", lambda r: (r.tools[tool] > 0, f"tools: {dict(r.tools)}"))


def not_called(tool):
    return Check(f"did not call {tool}", lambda r: (r.tools[tool] == 0, f"tools: {dict(r.tools)}"))


def no_command_matching(pattern):
    rx = re.compile(pattern)

    def fn(r):
        bad = [c for c in r.commands() if rx.search(c)]
        return not bad, f"offending: {bad}" if bad else "none"
    return Check(f"no bash command matching /{pattern}/", fn)


# --- answer text ----------------------------------------------------------

def answer_matches(pattern, flags=re.IGNORECASE):
    rx = re.compile(pattern, flags)
    return Check(f"answer matches /{pattern}/",
                 lambda r: (rx.search(r.answer) is not None, short(r.answer)))


def answer_lacks(pattern, flags=re.IGNORECASE):
    rx = re.compile(pattern, flags)

    def fn(r):
        m = rx.search(r.answer)
        return m is None, f"found {m.group(0)!r} in: {short(r.answer)}" if m else "absent"
    return Check(f"answer lacks /{pattern}/", fn)


# --- files ----------------------------------------------------------------

def file_equals(path, expected):
    def fn(r):
        p = r.workdir / path
        if not p.exists():
            return False, f"{path} missing"
        actual = p.read_text()
        return actual == expected, "exact" if actual == expected else f"got: {actual!r}"[:300]
    return Check(f"{path} has the expected content", fn)


def files_equal(expected):
    """Every file in `expected` ({path: text}) has exactly that content. The detail names
    each file that differs and its first differing line, so a failure says where."""
    def fn(r):
        bad = []
        for path, want in expected.items():
            p = r.workdir / path
            if not p.exists():
                bad.append(f"{path} missing")
                continue
            got = p.read_text()
            if got != want:
                pairs = zip(got.splitlines() + [""], want.splitlines() + [""])
                diff = next(((i, g, w) for i, (g, w) in enumerate(pairs, 1) if g != w), None)
                if diff is None:     # same lines, different line endings or final newline
                    bad.append(f"{path}: whitespace at line ends differs")
                else:
                    n, g, w = diff
                    bad.append(f"{path}:{n} got {g.strip()!r}, want {w.strip()!r}")
        return not bad, "; ".join(bad) if bad else f"all {len(expected)} exact"
    return Check(f"all {len(expected)} files have the expected content", fn)


def file_exists(path):
    return Check(f"{path} exists", lambda r: ((r.workdir / path).exists(), ""))


def file_missing(path):
    return Check(f"{path} does not exist", lambda r: (not (r.workdir / path).exists(), ""))


def command_output(command, expected=None, name=None):
    """Run `command` in the workdir (sandboxed, like the agent's own bash) and require
    exit 0 and, if given, exactly `expected` on stdout+stderr."""
    def fn(r):
        from nanoharness import tools    # config.WORKDIR is still this run's workdir
        out = tools.bash(command)
        head, _, body = out.partition("\n\n")
        if head != "exit code: 0":
            return False, short(out, 300)
        if expected is not None and body.strip() != expected.strip():
            return False, f"output: {short(body, 300)}"
        return True, short(body, 80)
    return Check(name or f"`{command}` succeeds", fn)


# --- context --------------------------------------------------------------

def context_under(tokens):
    """The main agent's final history stays under `tokens` (estimated as the harness does).
    The goal behind delegation, measured directly: a subagent, or grep plus a targeted read,
    both pass; reading whole files into the main history does not."""
    def fn(r):
        from nanoharness import context
        n = context.estimate_tokens(r.messages)
        return n < tokens, f"~{n} tokens"
    return Check(f"main history under {tokens} tokens", fn)


def compacted():
    """The harness replaced earlier turns with a summary: the case exercised what it meant to."""
    def fn(r):
        from nanoharness import context
        return any(context.is_summary(m) for m in r.messages), f"{len(r.messages)} messages"
    return Check("history was summarized", fn)


def summary_lacks(text):
    """The compaction summary does not contain `text`: the fact really was lost, so the
    case is testing what the model does without it."""
    def fn(r):
        from nanoharness import context
        found = [m for m in r.messages if context.is_summary(m) and text in m.get("content", "")]
        return not found, "in the summary" if found else "absent"
    return Check(f"summary lacks {text!r}", fn)


def any_of(*checks):
    """Passes if any of `checks` does: for a case with more than one right outcome."""
    def fn(r):
        results = [c(r) for c in checks]
        ok = [x["check"] for x in results if x["ok"]]
        return bool(ok), f"passed: {ok}" if ok else "; ".join(f"{x['check']}: {x['detail']}" for x in results)
    return Check(" or ".join(c.name for c in checks), fn)
