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

    def fake_chat(messages, tools=None, on_token=None, **kw):
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



def test_an_identical_repeated_call_is_pointed_out(agent, monkeypatch):
    script(monkeypatch,
           reply("", tool_call("grep", pattern="timeout")),
           reply("", tool_call("grep", pattern="timeout")),
           reply("Not found."))
    agent.turn("find the timeout")
    results = [m["content"] for m in agent.messages if m["role"] == "tool"]
    assert "already made this exact call" not in results[0]
    assert "already made this exact call" in results[1]


def test_a_repeat_with_a_different_result_is_not_flagged(agent, monkeypatch, tmp_path):
    script(monkeypatch,
           reply("", tool_call("read_file", path="a.txt")),
           reply("", tool_call("write_file", path="a.txt", content="two")),
           reply("", tool_call("read_file", path="a.txt")),
           reply("Done."))
    (tmp_path / "a.txt").write_text("one")
    agent.turn("change a.txt")
    reads = [m["content"] for m in agent.messages if m.get("tool_name") == "read_file"]
    assert "two" in reads[1] and "already made this exact call" not in reads[1]


def test_repeats_are_counted_per_turn_and_can_be_switched_off(agent, monkeypatch):
    script(monkeypatch,
           reply("", tool_call("grep", pattern="x")), reply("no"),
           reply("", tool_call("grep", pattern="x")), reply("no"))
    agent.turn("one")
    agent.turn("two")                                    # a new turn: not a repeat
    assert not any("already made" in m["content"] for m in agent.messages if m["role"] == "tool")

    monkeypatch.setattr(config, "REPEAT_NOTE", False)
    script(monkeypatch, reply("", tool_call("grep", pattern="y")),
           reply("", tool_call("grep", pattern="y")), reply("no"))
    agent.turn("three")
    assert not any("already made" in m["content"] for m in agent.messages if m["role"] == "tool")


def test_review_sees_the_turns_diff_and_warns(agent, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "REVIEW", True)
    (tmp_path / "db.py").write_text("def ping():\n    return connect()\n")
    seen = []
    script(monkeypatch,
           reply("", tool_call("edit_file", path="db.py", old_string="return connect()",
                               new_string="return True")),
           reply("Tests pass now."),
           {"role": "assistant", "content": "FAKED: ping() always returns True."})
    real_chat = client.chat
    monkeypatch.setattr(client, "chat", lambda m, *a, **k: (seen.append(m), real_chat(m, *a, **k))[1])
    assert agent.turn("get the tests passing") == "Tests pass now."   # the answer is untouched
    assert "-    return connect()" in seen[-1][0]["content"] and "+    return True" in seen[-1][0]["content"]
    assert agent.verdict == (True, "ping() always returns True.")
    assert "⚠ review" in capsys.readouterr().out


def test_no_review_for_a_turn_that_wrote_nothing(agent, monkeypatch):
    monkeypatch.setattr(config, "REVIEW", True)
    script(monkeypatch, reply("", tool_call("glob", pattern="*")), reply("Nothing here."))
    agent.turn("what's here?")        # StopIteration if it made a third (review) call
    assert agent.verdict is None


def test_a_failed_review_keeps_the_turn(agent, monkeypatch):
    monkeypatch.setattr(config, "REVIEW", True)
    script(monkeypatch, reply("", tool_call("write_file", path="a.txt", content="x")),
           reply("Done."), ModelError("down"))
    assert agent.turn("write a.txt") == "Done."


def test_read_only_bash_turn_is_not_reviewed(agent, monkeypatch):
    """Both false alarms in the dd13360 baseline were on turns that changed nothing."""
    monkeypatch.setattr(config, "REVIEW", True)
    script(monkeypatch, reply("", tool_call("bash", command="ls")), reply("Nothing here."))
    agent.turn("what's here?")        # StopIteration if it made a review call
    assert agent.verdict is None


def test_review_sees_every_call_and_files_changed_by_commands(agent, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "REVIEW", True)
    seen = []
    script(monkeypatch,
           reply("", tool_call("read_file", path="missing.py"),
                 tool_call("bash", command="echo 'def ping(): return True' > db.py")),
           reply("Fixed."),
           {"role": "assistant", "content": "OK"})
    real_chat = client.chat
    monkeypatch.setattr(client, "chat", lambda m, *a, **k: (seen.append(m), real_chat(m, *a, **k))[1])
    agent.turn("fix the tests")
    prompt = seen[-1][0]["content"]
    assert "read_file(path='missing.py') -> Error" in prompt
    assert "Also changed by commands (contents not shown): db.py" in prompt


def test_interrupting_the_review_skips_it_and_keeps_the_turn(agent, monkeypatch, capsys):
    monkeypatch.setattr(config, "REVIEW", True)
    script(monkeypatch,
           reply("", tool_call("write_file", path="a.txt", content="x")),
           reply("Done."), KeyboardInterrupt())
    assert agent.turn("write a.txt") == "Done."
    assert agent.verdict is None and "review skipped" in capsys.readouterr().out


def test_a_turn_that_started_a_service_is_reviewed(agent, monkeypatch):
    """A stand-in kept up by start_service is as much a fake as one started with &."""
    monkeypatch.setattr(config, "REVIEW", True)
    from nanoharness import services
    monkeypatch.setattr(services, "start", lambda *a, **k: "Started service 'db'.")
    script(monkeypatch, reply("", tool_call("start_service", command="nc -l 5433", port=5433)),
           reply("Tests pass."), {"role": "assistant", "content": "FAKED: a stand-in database."})
    agent.turn("get the tests passing")
    assert agent.verdict == (True, "a stand-in database.")
