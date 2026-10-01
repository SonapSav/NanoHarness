"""Does a side call (review, subagent, summary) push the main conversation out of Ollama's cache?

Sends a long "main" prompt, then several unrelated review-sized prompts, then the main prompt
plus one more turn, and prints how long Ollama spent processing each prompt. If the last one
is fast, the main prompt survived in the cache. Replies are one token with reasoning off:
caching concerns prompt processing only. Rerun after changing the servers' Ollama setup.

    .venv/bin/python -m evals.cache_probe --hosts 100.66.104.56,100.76.19.74
"""
import argparse
import json
import sys
import urllib.request

from nanoharness import config


def processing_seconds(host, messages):
    body = {"model": config.MODEL, "messages": messages, "stream": False, "think": False,
            "options": {"num_ctx": config.NUM_CTX, "num_predict": 1,
                        "temperature": config.TEMPERATURE}}
    req = urllib.request.Request(f"{host}/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    reply = json.load(urllib.request.urlopen(req, timeout=600))
    # prompt_eval_count is always the full prompt; the duration is what shows a cache hit.
    return reply.get("prompt_eval_count"), round(reply.get("prompt_eval_duration", 0) / 1e9, 2)


def probe(host, main_words, side_words, sides):
    main = [{"role": "system", "content": "You are a coding agent."},
            {"role": "user", "content": " ".join(f"main{i}" for i in range(main_words)) + "\nGo."}]
    cold = processing_seconds(host, main)
    side = [processing_seconds(host, [{"role": "user", "content": f"Side call {k}: "
                                       + " ".join(f"s{k}_{i}" for i in range(side_words))}])
            for k in range(sides)]
    after = processing_seconds(host, main + [{"role": "assistant", "content": "ok"},
                                             {"role": "user", "content": "next"}])
    kept = after[1] < cold[1] / 5
    print(f"{host}: main cold {cold[0]} tokens {cold[1]}s | {sides} side calls "
          f"{side[0][0]} tokens ~{side[0][1]}s each | next main turn {after[1]}s -> "
          + ("main prompt still cached" if kept else "MAIN PROMPT WAS EVICTED"))
    return kept


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--hosts", default=config.OLLAMA_HOST)
    p.add_argument("--main-words", type=int, default=5200, help="~25k tokens")
    p.add_argument("--side-words", type=int, default=1700, help="~11k tokens")
    p.add_argument("--sides", type=int, default=3)
    args = p.parse_args()
    hosts = [h if h.startswith("http") else f"http://{h}:11434" for h in args.hosts.split(",")]
    return 0 if all([probe(h, args.main_words, args.side_words, args.sides) for h in hosts]) else 1


if __name__ == "__main__":
    sys.exit(main())
