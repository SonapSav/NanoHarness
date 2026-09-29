"""Offline tests for subagents: one scripted model plays both the main agent and the
subagent. Calls happen in order (the subagent runs inside the main agent's tool call),
and each is tagged with who made it, by system prompt."""
import pytest

from nanoharness import client, config, subagent
from nanoharness.agent import Agent
from nanoharness.client import ModelError
from nanoharness.permissions import Permissions


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKDIR", tmp_path)
    (tmp_path / "big.txt").write_text("SECRET-CONTENT\n" * 50)
    return Agent(Permissions(yolo=False))   # not yolo: nothing here may prompt


@pytest.fixture(autouse=True)
def no_prompts(monkeypatch):
    def boom(*a):
        raise AssertionError("a subagent must never ask for permission")
    monkeypatch.setattr("builtins.input", boom)


def script(monkeypatch, *steps):
    steps, seen = iter(steps), []

    def fake_chat(messages, tools=None, on_token=None):
        who = "sub" if "research subagent" in messages[0]["content"] else "main"
        seen.append((who, [t["function"]["name"] for t in tools or []], list(messages)))
        step = next(steps)
        if isinstance(step, BaseException):
            raise step
        return step
    monkeypatch.setattr(client, "chat", fake_chat)
    return seen


def say(text):
    return {"role": "assistant", "content": text}


def calls(name, **args):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def test_only_the_report_reaches_the_main_history(agent, monkeypatch):
    seen = script(monkeypatch,
                  calls("task", prompt="What is in big.txt?"),
                  calls("read_file", path="big.txt"),            # subagent reads
                  say("big.txt repeats SECRET-CONTENT 50 times"),  # subagent reports
                  say("It repeats one line."))                   # main answers
    assert agent.turn("check big.txt") == "It repeats one line."

    task_result = agent.messages[-2]
    assert task_result["tool_name"] == "task"
    assert task_result["content"] == "big.txt repeats SECRET-CONTENT 50 times"
    # The file itself never entered the main history.
    assert sum(m["content"].count("SECRET-CONTENT") for m in agent.messages) == 1
    assert [who for who, _, _ in seen] == ["main", "sub", "sub", "main"]


def test_subagent_gets_read_only_tools_and_cannot_recurse(agent, monkeypatch):
    seen = script(monkeypatch, calls("task", prompt="look"), say("done"), say("ok"))
    agent.turn("go")
    main_tools, sub_tools = seen[0][1], seen[1][1]
    assert "task" in main_tools
    assert sub_tools == ["read_file", "glob", "grep"]


def test_subagent_is_refused_tools_it_was_not_given(agent, monkeypatch, tmp_path):
    script(monkeypatch,
           calls("task", prompt="write a file"),
           calls("write_file", path="pwned.txt", content="x"),   # the model tries anyway
           say("I could not write it"),
           say("ok"))
    agent.turn("go")
    assert not (tmp_path / "pwned.txt").exists()


def test_the_task_text_is_all_the_subagent_sees(agent, monkeypatch):
    seen = script(monkeypatch, calls("task", prompt="THE TASK"), say("r"), say("ok"))
    agent.turn("the user's private request")
    sub_messages = seen[1][2]
    assert [m["role"] for m in sub_messages] == ["system", "user"]
    assert sub_messages[1]["content"] == "THE TASK"


def test_out_of_steps_asks_for_a_partial_report(agent, monkeypatch):
    monkeypatch.setattr(config, "SUBAGENT_MAX_STEPS", 1)
    seen = script(monkeypatch,
                  calls("task", prompt="dig"),
                  calls("grep", pattern="x"),            # uses its one step
                  say("found nothing yet"),              # the no-tools wrap-up
                  say("ok"))
    agent.turn("go")
    result = agent.messages[-2]["content"]
    assert "ran out of steps" in result and "found nothing yet" in result
    assert seen[2][1] == []                              # wrap-up offered no tools
    assert subagent.OUT_OF_STEPS in seen[2][2][-1]["content"]


def test_model_error_in_subagent_is_a_tool_error(agent, monkeypatch):
    script(monkeypatch, calls("task", prompt="dig"), ModelError("down"), say("ok"))
    agent.turn("go")
    assert agent.messages[-2]["content"].startswith("Error: the subagent could not get a reply")


def test_interrupt_inside_subagent_is_recorded_in_main(agent, monkeypatch):
    script(monkeypatch, calls("task", prompt="dig"), KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        agent.turn("go")
    assert agent.messages[-1]["tool_name"] == "task"
    assert "interrupted" in agent.messages[-1]["content"]
