"""Run the eval cases against the real model and report pass rates.

    .venv/bin/python -m evals                     # every case, 3 runs each
    .venv/bin/python -m evals -k honesty -n 5     # cases whose name or group contains 'honesty'
    .venv/bin/python -m evals --baseline evals/results/<earlier>.json

Each run gets a fresh temp workdir, no session file, and its own approvals. Results
(checks, answers, full transcripts) go to evals/results/<timestamp>.json.
"""
import argparse
import builtins
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

from nanoharness import config, context, tools
from nanoharness.agent import Agent
from nanoharness.client import ModelError
from nanoharness.permissions import Permissions

from .cases import CASES
from .checks import Run

RESULTS = Path(__file__).parent / "results"


def run_case(case, keep=False):
    """One run of one case. Returns a JSON-able dict."""
    workdir = Path(tempfile.mkdtemp(prefix=f"nanoeval-{case.name}-")).resolve()
    for rel, text in case.files.items():
        p = workdir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    saved_workdir, saved_input = config.WORKDIR, builtins.input
    config.WORKDIR = workdir
    prompts = []
    answers = iter([] if case.approve == "all" else case.approve)

    def scripted_input(prompt=""):
        prompts.append(prompt)
        return next(answers, "n")

    builtins.input = scripted_input
    agent = Agent(Permissions(yolo=case.approve == "all"))
    out, error, answer = io.StringIO(), None, ""
    start = time.monotonic()
    try:
        with redirect_stdout(out):
            answer = agent.turn(case.prompt)
    except ModelError as e:
        error = str(e)
    finally:
        builtins.input = saved_input
    seconds = time.monotonic() - start

    tool_counts = Counter(c.get("function", {}).get("name", "?")
                          for m in agent.messages for c in m.get("tool_calls") or [])
    # A subagent's history is thrown away; its calls only survive as "↳ name" lines.
    plain = re.sub(r"\x1b\[[0-9;]*m", "", out.getvalue())
    sub_counts = Counter(line.split("↳", 1)[1].strip()
                         for line in plain.splitlines() if "↳" in line)
    run = Run(workdir, agent.messages, answer, tool_counts, prompts)
    try:
        results = [check(run) for check in case.checks]   # command checks need WORKDIR
    finally:
        config.WORKDIR = saved_workdir
        if not keep:
            shutil.rmtree(workdir, ignore_errors=True)

    return {
        "case": case.name,
        "group": case.group,
        "passed": error is None and all(r["ok"] for r in results),
        "checks": results,
        "error": error,
        "answer": answer,
        "tools": dict(tool_counts),
        "sub_tools": dict(sub_counts),
        "prompts": len(prompts),
        "rounds": sum(1 for m in agent.messages if m["role"] == "assistant"),
        "tokens": context.estimate_tokens(agent.messages, tools.schemas()),
        "seconds": round(seconds, 1),
        "stopped": agent.stopped,
        "workdir": str(workdir) if keep else None,
        "stdout": out.getvalue()[-4000:],
        "messages": agent.messages,
    }


def summarize(runs):
    """Per-case aggregates, in case order."""
    by_case = {}
    for r in runs:
        by_case.setdefault(r["case"], []).append(r)
    summary = {}
    for name, rs in by_case.items():
        n = len(rs)
        summary[name] = {
            "group": rs[0]["group"],
            "passed": sum(r["passed"] for r in rs),
            "runs": n,
            "tools": round(sum(sum(r["tools"].values()) for r in rs) / n, 1),
            "bash": round(sum(r["tools"].get("bash", 0) for r in rs) / n, 1),
            "sub": round(sum(sum(r.get("sub_tools", {}).values()) for r in rs) / n, 1),
            "tokens": round(sum(r["tokens"] for r in rs) / n),
            "seconds": round(sum(r["seconds"] for r in rs) / n, 1),
        }
    return summary


def report(summary, runs, baseline=None):
    base = (baseline or {}).get("summary", {})
    head = f"{'case':<24}{'group':<10}{'pass':>7}"
    head += f"{'was':>7}" if base else ""
    head += f"{'tools':>7}{'bash':>6}{'sub':>6}{'tokens':>8}{'secs':>7}"
    lines = [head, "-" * len(head)]
    total = total_runs = 0
    for name, s in summary.items():
        row = f"{name:<24}{s['group']:<10}{s['passed']:>4}/{s['runs']:<2}"
        if base:
            b = base.get(name)
            if b:
                delta = s["passed"] / s["runs"] - b["passed"] / b["runs"]
                mark = "+" if delta > 0 else "-" if delta < 0 else " "
                row += f"{b['passed']:>4}/{b['runs']:<1}{mark}"
            else:
                row += f"{'new':>7}"
        row += f"{s['tools']:>7}{s['bash']:>6}{s.get('sub', 0):>6}{s['tokens']:>8}{s['seconds']:>7}"
        lines.append(row)
        total += s["passed"]
        total_runs += s["runs"]
    lines += ["-" * len(head), f"{'total':<34}{total:>4}/{total_runs}  ({100 * total // max(total_runs, 1)}%)"]

    failures = [r for r in runs if not r["passed"]]
    if failures:
        lines += ["", "failures:"]
        for r in failures:
            why = r["error"] or "; ".join(f"{c['check']} ({c['detail']})" for c in r["checks"] if not c["ok"])
            lines.append(f"  {r['case']}: {why}"[:240])
    return "\n".join(lines)


def git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, cwd=Path(__file__).parent).stdout.strip() or None
    except OSError:
        return None


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m evals", description="Run NanoHarness evals against the live model.")
    p.add_argument("-n", "--repeat", type=int, default=3, help="runs per case (default 3)")
    p.add_argument("-k", "--filter", default="", help="only cases whose name or group contains this")
    p.add_argument("--baseline", type=Path, help="an earlier results file to compare against")
    p.add_argument("--out", type=Path, help="where to write results (default evals/results/<timestamp>.json)")
    p.add_argument("--keep", action="store_true", help="keep each run's workdir for inspection")
    args = p.parse_args(argv)

    cases = [c for c in CASES if args.filter in c.name or args.filter in c.group]
    if not cases:
        print(f"no cases match {args.filter!r}")
        return 1
    baseline = json.loads(args.baseline.read_text()) if args.baseline else None

    total = len(cases) * args.repeat
    print(f"{len(cases)} cases x {args.repeat} runs against {config.MODEL} at {config.OLLAMA_HOST}")
    runs = []
    for case in cases:
        for i in range(args.repeat):
            print(f"  [{len(runs) + 1}/{total}] {case.name} #{i + 1} ... ", end="", flush=True)
            r = run_case(case, keep=args.keep)
            runs.append(r)
            print(("pass" if r["passed"] else "FAIL") + f"  ({r['seconds']}s)", flush=True)

    summary = summarize(runs)
    result = {
        "when": datetime.now().isoformat(timespec="seconds"),
        "commit": git_commit(),
        "config": {"model": config.MODEL, "num_ctx": config.NUM_CTX, "temperature": config.TEMPERATURE,
                   "think": config.THINK, "max_steps": config.MAX_STEPS, "sandbox": config.SANDBOX},
        "summary": summary,
        "runs": runs,
    }
    out = args.out or RESULTS / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1))

    print()
    print(report(summary, runs, baseline))
    print(f"\nresults: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
