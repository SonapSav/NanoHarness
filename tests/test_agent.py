"""Offline tests for the loop: the model is a scripted fake, no network."""
import pytest

from nanoharness import client, config
from nanoharness.agent import Agent
from nanoharness.client import ModelError
from nanoharness.permissions import Permissions


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKDIR", tmp_path)
    return Agent(Permissions(yolo=True))


def script(monkeypatch, *steps):
    """Make client.chat return (or raise) each step in order."""
    steps = iter(steps)

    def fake_chat(messages, tools=None, on_token=None):
        step = next(steps)
        if isinstance(step, BaseException):
            raise step
        return step
    monkeypatch.setattr(client, "chat", fake_chat)


def tool_call(name, **args):
    return {"function": {"name": name, "arguments": args}}


def reply(content="", *calls):
    return {"role": "assistant", "content": content, "tool_calls": list(calls)}


def assert_every_call_answered(messages):
    """Each assistant tool call must be followed by exactly one tool message."""
    for i, m in enumerate(messages):
        calls = m.get("tool_calls") or []
        results = messages[i + 1: i + 1 + len(calls)]
        assert [r["role"] for r in results] == ["tool"] * len(calls)


def test_model_error_before_any_reply_drops_the_user_message(agent, monkeypatch):
    """Regression: a failed request left the message behind, so a retry sent it twice."""
    script(monkeypatch, ModelError("down"))
    with pytest.raises(ModelError):
        agent.turn("hello")
    assert [m["role"] for m in agent.messages] == ["system"]


def test_interrupt_while_waiting_for_first_reply_drops_the_user_message(agent, monkeypatch):
    script(monkeypatch, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        agent.turn("hello")
    assert [m["role"] for m in agent.messages] == ["system"]


def test_model_error_after_a_tool_ran_keeps_the_history(agent, monkeypatch, tmp_path):
    """The file was written; the model must still see that it happened."""
    script(monkeypatch,
           reply("", tool_call("write_file", path="a.txt", content="x")),
           ModelError("down"))
    with pytest.raises(ModelError):
        agent.turn("make a.txt")
    assert (tmp_path / "a.txt").exists()
    assert [m["role"] for m in agent.messages] == ["system", "user", "assistant", "tool"]


def test_interrupt_at_permission_prompt_answers_every_call(agent, monkeypatch):
    """Regression: Ctrl-C at the prompt left tool calls with no result in the history."""
    def ctrl_c(*a):
        raise KeyboardInterrupt
    monkeypatch.setattr("builtins.input", ctrl_c)
    agent.permissions = Permissions(yolo=False)
    script(monkeypatch, reply("",
                              tool_call("write_file", path="a.txt", content="x"),
                              tool_call("bash", command="echo hi")))
    with pytest.raises(KeyboardInterrupt):
        agent.turn("do two things")

    assert_every_call_answered(agent.messages)
    first, second = agent.messages[-2:]
    assert "interrupted this call" in first["content"]
    assert "did NOT run" in second["content"] and second["tool_name"] == "bash"


def test_next_turn_after_interrupt_is_well_formed(agent, monkeypatch):
    def ctrl_c(*a):
        raise KeyboardInterrupt
    monkeypatch.setattr("builtins.input", ctrl_c)
    agent.permissions = Permissions(yolo=False)
    script(monkeypatch,
           reply("", tool_call("write_file", path="a.txt", content="x")),
           reply("ok, stopping"))
    with pytest.raises(KeyboardInterrupt):
        agent.turn("write it")
    assert agent.turn("never mind") == "ok, stopping"
    assert_every_call_answered(agent.messages)


def test_system_prompt_names_the_current_workdir(agent, tmp_path):
    """Regression: the prompt was frozen at import, so it named the wrong directory."""
    assert str(tmp_path) in agent.messages[0]["content"]
