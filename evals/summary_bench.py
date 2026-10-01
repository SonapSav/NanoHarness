"""Score compaction summaries directly, without running the agent.

The compaction evals sit at the ceiling (10/10, 9/10), so they catch regressions but can't
show a better summary. This calls context.summarize on exactly what fit() would hand it for
the two prepared histories, and checks each summary for the facts the session set and for
quoted log lines. Run it before and after changing SUMMARIZE_PROMPT.

    .venv/bin/python -m evals.summary_bench --hosts 100.66.104.56,100.76.19.74 --label before
"""
import argparse
import json
import multiprocessing
import re
import sys

from nanoharness import config, context
from .cases import STATUS_HISTORY, STATUS_HISTORY_LOST

HISTORIES = {"remember": STATUS_HISTORY, "lost": STATUS_HISTORY_LOST}

NEAR = r".{0,100}"
NO = r"(never|not|n't|no |read.only|untouch|vendored|unmodif|off.limits)"
FACTS = {
    "legacy read-only": re.compile(rf"legacy{NEAR}{NO}|{NO}{NEAR}legacy", re.I | re.S),
    "app.py not edited": re.compile(rf"app\.py{NEAR}{NO}|{NO}{NEAR}app\.py|service/routes", re.I | re.S),
    "port 9310": re.compile(r"9310"),
    "probe path": re.compile(r"/_probe/ready"),
}
# A line of the pasted log or e-mail thread, quoted back.
QUOTED = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}|\b(INFO|WARN|ERROR|DEBUG)\b|^\s*(From|Subject):",
                    re.M)


def what_fit_sends(history):
    """The `old` messages fit() would summarize for this history plus the case's next turn."""
    messages = [{"role": "system", "content": "system prompt"}] + history + [
        {"role": "user", "content": "Now add the readiness endpoint."}]
    cur = context.current_turn_start(messages)
    return context.elide(messages, range(1, cur))[1:cur]


def score(name, text):
    facts = {f: bool(rx.search(text)) for f, rx in FACTS.items()}
    quoted = sum(1 for line in text.splitlines() if QUOTED.search(line))
    return {"history": name, "facts": facts, "quoted_lines": quoted,
            "words": len(text.split()), "text": text}


def work(args):
    host, jobs = args
    config.OLLAMA_HOST = host
    out = []
    for name in jobs:
        try:
            r = score(name, context.summarize(what_fit_sends(HISTORIES[name])))
        except Exception as e:   # a failed summary is a result too
            r = {"history": name, "error": str(e)}
        print(f"  {name:<9} {r.get('facts', r.get('error'))} quoted={r.get('quoted_lines')} "
              f"words={r.get('words')}", flush=True)
        out.append(r)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--hosts", default=config.OLLAMA_HOST)
    p.add_argument("-n", type=int, default=10, help="summaries per history")
    p.add_argument("--label", default="current")
    args = p.parse_args()
    hosts = [h if h.startswith("http") else f"http://{h}:11434" for h in args.hosts.split(",")]
    jobs = [name for name in HISTORIES for _ in range(args.n)]
    with multiprocessing.Pool(len(hosts)) as pool:
        results = [r for part in pool.map(work, [(h, jobs[i::len(hosts)]) for i, h in enumerate(hosts)])
                   for r in part]

    print(f"\n{args.label}: {args.n} summaries per history")
    for name in HISTORIES:
        rs = [r for r in results if r["history"] == name and "facts" in r]
        errors = sum(r["history"] == name and "error" in r for r in results)
        kept = {f: sum(r["facts"][f] for r in rs) for f in FACTS}
        print(f"  {name:<9} " + "  ".join(f"{f} {k}/{len(rs)}" for f, k in kept.items())
              + f"  quoted lines {sum(r['quoted_lines'] for r in rs) / max(len(rs), 1):.1f}"
              + f"  words {sum(r['words'] for r in rs) / max(len(rs), 1):.0f}"
              + (f"  errors {errors}" if errors else ""))
    out = f"evals/results/summary-bench-{args.label}.json"
    json.dump({"prompt": context.SUMMARIZE_PROMPT, "results": results}, open(out, "w"), indent=1)
    print(f"results: {out}")


if __name__ == "__main__":
    sys.exit(main())
