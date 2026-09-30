"""Keep the history inside num_ctx.

Past num_ctx, Ollama silently drops the oldest tokens, system prompt first. So before
each request we estimate the size and, if it is too big, shrink the history ourselves,
cheapest step first:

    1. elide long tool outputs from earlier turns
    2. replace earlier turns with a model-written summary
    3. elide tool outputs in the current turn, except the latest few

The system prompt and the current user message are never touched. What step 2 replaces is
not thrown away: it goes to the agent's archive, which the search_history tool searches.
"""
import json

from . import client, config
from .client import ModelError

# Measured on aeroadvisor-agent: ~3.1 chars/token for JSON-heavy prompts, ~4.1 for code.
# Rounding down overestimates tokens, which errs on the safe side.
CHARS_PER_TOKEN = 3
KEEP_CHARS = 300          # what an elided tool output keeps
KEEP_RECENT_TOOLS = 2     # step 3 leaves this many of the latest tool outputs whole
# Per message, when rendering the transcript to summarize: the start and the end. A note after
# a pasted log, or the conclusion of a long reply, is at the end.
SUMMARY_HEAD_CHARS = 1000
SUMMARY_TAIL_CHARS = 500

SUMMARIZE_PROMPT = """You are compressing the history of a coding session so it can continue \
in a smaller context. Write a concise summary (under 300 words) of the transcript you are given:
- what the user asked for
- what was done: files created or changed, commands run and their outcomes
- decisions made, and anything still unfinished or failing
Use plain text. Keep file names, paths and exact error messages. Do not invent anything."""

SUMMARY_MARK = "[Summary of the earlier conversation, written to save context"   # also in old sessions
# Telling the model not to guess, here or in the system prompt, did not stop it (0/30 on
# admits_what_compaction_lost). So the details stay findable, and the header says where.
SUMMARY_HEADER = (SUMMARY_MARK + ". It leaves details out; the full earlier conversation is "
                  "kept, and search_history searches it. Before relying on a detail from earlier "
                  "that is not stated exactly below (a path, value, name), look it up, searching "
                  "for the words the user used for it.]\n\n")
ELIDED = "more chars elided to save context"


def budget() -> int:
    return int(config.NUM_CTX * config.COMPACT_AT)


def estimate_tokens(messages, tools=None) -> int:
    return (len(json.dumps(messages)) + len(json.dumps(tools or []))) // CHARS_PER_TOKEN


def fit(messages, tools=None, archive=None):
    """Return (messages, note). The list is new if anything was shrunk; note says what
    happened and is None when the history already fit. Messages replaced by a summary
    are appended to `archive`, as they were before this call shortened anything."""
    before = estimate_tokens(messages, tools)
    if before <= budget():
        return messages, None

    cur = current_turn_start(messages)
    steps = []

    original = messages
    messages = elide(messages, range(1, cur))
    if estimate_tokens(messages, tools) <= budget():
        return messages, note(before, messages, tools, "shortened old tool outputs")

    old = messages[1:cur]
    # A lone earlier summary is already as small as it gets; re-summarizing it only
    # costs a model call (and in practice came back longer).
    if old and not (len(old) == 1 and is_summary(old[0])):
        try:
            summary = {"role": "user", "content": SUMMARY_HEADER + summarize(old)}
        except ModelError as e:
            steps.append(f"summary failed: {e}")
        else:
            if len(json.dumps(summary)) < len(json.dumps(old)):
                if archive is not None:   # an earlier summary is a copy, not an original
                    archive.extend(m for m in original[1:cur] if not is_summary(m))
                messages = [messages[0], summary] + messages[cur:]
                cur = 2
                steps.append("summarized earlier turns")
                if estimate_tokens(messages, tools) <= budget():
                    return messages, note(before, messages, tools, *steps)
            else:
                steps.append("summary was not smaller, kept the original")

    tool_idx = [i for i in range(cur, len(messages)) if messages[i]["role"] == "tool"]
    messages = elide(messages, tool_idx[:-KEEP_RECENT_TOOLS] if KEEP_RECENT_TOOLS else tool_idx)
    steps.append("shortened tool outputs in this turn")
    if estimate_tokens(messages, tools) > budget():
        steps.append("STILL OVER BUDGET, Ollama may drop the oldest context")
    return messages, note(before, messages, tools, *steps)


def current_turn_start(messages) -> int:
    """Index of the latest user message: everything from there on is the current turn."""
    for i in range(len(messages) - 1, 0, -1):
        if messages[i]["role"] == "user" and not is_summary(messages[i]):
            return i
    return 1


def is_summary(m) -> bool:
    return m["role"] == "user" and m.get("content", "").startswith(SUMMARY_MARK)


def elide(messages, indices):
    """Copy of messages with the tool outputs at `indices` cut down to KEEP_CHARS."""
    out = list(messages)
    for i in indices:
        m = out[i]
        content = m.get("content", "")
        if m["role"] == "tool" and len(content) > KEEP_CHARS and ELIDED not in content:
            out[i] = {**m, "content": (
                content[:KEEP_CHARS]
                + f"\n[... {len(content) - KEEP_CHARS} {ELIDED}. "
                  "Run the tool again if you need the full output.]"
            )}
    return out


def summarize(old) -> str:
    reply = client.chat([
        {"role": "system", "content": SUMMARIZE_PROMPT},
        {"role": "user", "content": transcript(old)},
    ])
    text = (reply.get("content") or "").strip()
    if not text:
        raise ModelError("the model returned an empty summary")
    return text


def transcript(old) -> str:
    """Render messages as plain text for the summarizer, capped so the request itself fits.
    The first message is kept first (it may be an earlier summary); if over the cap, the
    oldest of the rest are dropped."""
    lines = [render(m) for m in old]
    cap = budget() * CHARS_PER_TOKEN - len(SUMMARIZE_PROMPT) - 1000
    head, rest = lines[:1], lines[1:]
    total = sum(map(len, head))
    kept = []
    for line in reversed(rest):
        if total + len(line) > cap:
            kept.append("[... older messages omitted ...]")
            break
        kept.append(line)
        total += len(line)
    return "\n\n".join(head + kept[::-1])


def clip(text) -> str:
    """The head and tail of a long message, with the middle marked as cut."""
    if len(text) <= SUMMARY_HEAD_CHARS + SUMMARY_TAIL_CHARS:
        return text
    cut = len(text) - SUMMARY_HEAD_CHARS - SUMMARY_TAIL_CHARS
    return f"{text[:SUMMARY_HEAD_CHARS]}\n[... {cut} chars cut ...]\n{text[-SUMMARY_TAIL_CHARS:]}"


def render(m) -> str:
    content = clip(m.get("content") or "")
    if m["role"] == "tool":
        return f"TOOL RESULT ({m.get('tool_name', '?')}): {content}"
    calls = [
        f"{c.get('function', {}).get('name', '?')}({json.dumps(c.get('function', {}).get('arguments', {}))[:300]})"
        for c in m.get("tool_calls") or []
    ]
    suffix = f"\n  [called: {'; '.join(calls)}]" if calls else ""
    return f"{m['role'].upper()}: {content}{suffix}"


def note(before, messages, tools, *steps) -> str:
    after = estimate_tokens(messages, tools)
    return f"context ~{before} → ~{after} tokens (budget {budget()}): {', '.join(steps)}"
