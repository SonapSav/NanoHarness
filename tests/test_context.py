"""Offline tests for history compaction: tiny num_ctx, scripted summarizer."""
import pytest

from nanoharness import client, config, context
from nanoharness.agent import Agent
from nanoharness.client import ModelError
from nanoharness.permissions import Permissions

BIG = "z" * 3000   # ~1000 tokens by the estimate


@pytest.fixture(autouse=True)
def small_ctx(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "WORKDIR", tmp_path)
    monkeypatch.setattr(config, "NUM_CTX", 2000)      # budget: 1500 tokens, ~4500 chars
    monkeypatch.setattr(config, "COMPACT_AT", 0.75)


def summarizer(monkeypatch, text="the user wanted a.txt; it was written"):
    """Replace the model with one that only writes summaries, and record the requests."""
    seen = []

    def fake_chat(messages, tools=None):
        seen.append(messages)
        if isinstance(text, BaseException):
            raise text
        return {"role": "assistant", "content": text}
    monkeypatch.setattr(client, "chat", fake_chat)
    return seen


def no_model(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("the model should not be called")
    monkeypatch.setattr(client, "chat", fail)


def sys_():
    return {"role": "system", "content": "sys"}


def user(text):
    return {"role": "user", "content": text}


def calls(*names):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": n, "arguments": {}}} for n in names]}


def result(content, name="bash"):
    return {"role": "tool", "tool_name": name, "content": content}


def test_under_budget_is_left_alone(monkeypatch):
    no_model(monkeypatch)
    msgs = [sys_(), user("hi")]
    out, note = context.fit(msgs)
    assert out is msgs and note is None


def test_old_tool_outputs_are_elided_first(monkeypatch):
    no_model(monkeypatch)
    msgs = [sys_(), user("one"), calls("bash"), result(BIG * 2), {"role": "assistant", "content": "done"},
            user("two"), calls("bash"), result("x" * 1000)]
    out, note = context.fit(msgs)
    assert context.ELIDED in out[3]["content"] and len(out[3]["content"]) < 500
    assert out[-1]["content"] == "x" * 1000          # current turn untouched
    assert msgs[3]["content"] == BIG * 2              # input not mutated
    assert "shortened old tool outputs" in note


def test_elision_is_idempotent():
    once = context.elide([result(BIG)], [0])
    assert context.elide(once, [0]) == once


def test_earlier_turns_are_summarized_when_elision_is_not_enough(monkeypatch):
    seen = summarizer(monkeypatch)
    old = [item for i in range(8) for item in (user(f"req {i} " + "q" * 400),
                                               {"role": "assistant", "content": "a" * 400})]
    msgs = [sys_(), *old, user("current"), calls("bash"), result("r")]
    out, note = context.fit(msgs)

    assert out[0] == msgs[0]
    assert out[1]["content"].startswith(context.SUMMARY_HEADER)
    assert "a.txt" in out[1]["content"]
    assert out[2:] == msgs[-3:]                        # current turn kept verbatim
    assert "req 7" in seen[0][1]["content"]            # the summarizer saw the transcript
    assert "summarized" in note


def test_a_previous_summary_is_not_mistaken_for_the_current_turn(monkeypatch):
    summarizer(monkeypatch)
    msgs = [sys_(), user(context.SUMMARY_HEADER + "s" * 3000), calls("bash"), result("r"),
            user("current")]
    assert context.current_turn_start(msgs) == 4


def test_failed_summary_falls_back_instead_of_raising(monkeypatch):
    summarizer(monkeypatch, ModelError("down"))
    msgs = [sys_(), user("q" * 6000), {"role": "assistant", "content": "ok"},
            user("current"), calls("bash", "bash", "bash"),
            result(BIG), result(BIG), result("last")]
    out, note = context.fit(msgs)
    assert "summary failed" in note
    assert context.ELIDED in out[5]["content"]         # oldest current-turn output elided
    assert out[6]["content"] == BIG and out[7]["content"] == "last"   # latest two kept


def test_still_over_budget_is_reported(monkeypatch):
    no_model(monkeypatch)
    msgs = [sys_(), user("q" * 9000)]
    out, note = context.fit(msgs)
    assert "STILL OVER BUDGET" in note


def test_dropped_input_survives_compaction(monkeypatch):
    """Compaction rebuilds the list; a failed turn must still drop only the user message."""
    responses = iter([{"role": "assistant", "content": "summary"}, ModelError("down")])

    def fake_chat(messages, tools=None):
        r = next(responses)
        if isinstance(r, BaseException):
            raise r
        return r
    monkeypatch.setattr(client, "chat", fake_chat)

    agent = Agent(Permissions(yolo=True))
    agent.messages += [user("q" * 6000), {"role": "assistant", "content": "ok"}]
    with pytest.raises(ModelError):
        agent.turn("new request")
    assert agent.dropped_input
    assert all(m["content"] != "new request" for m in agent.messages)
    assert agent.messages[1]["content"].startswith(context.SUMMARY_HEADER)


def test_a_lone_summary_is_not_summarized_again(monkeypatch):
    """Regression (seen live): re-summarizing the summary cost a call and grew it."""
    no_model(monkeypatch)
    msgs = [sys_(), user(context.SUMMARY_HEADER + "s" * 300), user("current"),
            calls("bash", "bash", "bash"), result(BIG), result(BIG), result("last")]
    out, note = context.fit(msgs)
    assert "summarized" not in note and out[1] == msgs[1]


def test_a_summary_that_is_not_smaller_is_discarded(monkeypatch):
    summarizer(monkeypatch, text="w" * 9000)
    msgs = [sys_(), user("q" * 5000), user("current")]
    out, note = context.fit(msgs)
    assert "not smaller" in note and out[1] == msgs[1]
