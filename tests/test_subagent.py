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
        who = "sub" if "You are a subagent" in messages[0]["content"] else "main"
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


# --- write=true ------------------------------------------------------------

def test_writer_gets_write_tools_but_still_cannot_recurse(agent, monkeypatch):
    seen = script(monkeypatch, calls("task", prompt="fix it", write=True), say("done"), say("ok"))
    agent.permissions = Permissions(yolo=True)
    agent.turn("go")
    assert seen[1][1] == ["read_file", "write_file", "edit_file", "bash", "glob", "grep"]


def test_writer_prompts_through_the_parents_gate(agent, monkeypatch, tmp_path, capsys):
    answers = iter(["y"])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    script(monkeypatch,
           calls("task", prompt="make a.txt", write=True),
           calls("write_file", path="a.txt", content="hi"),
           say("wrote a.txt"),
           say("ok"))
    agent.turn("go")
    assert (tmp_path / "a.txt").read_text() == "hi"
    assert "subagent: write a.txt" in capsys.readouterr().out


def test_writer_denied_changes_nothing(agent, monkeypatch, tmp_path):
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    script(monkeypatch,
           calls("task", prompt="make a.txt", write=True),
           calls("write_file", path="a.txt", content="hi"),
           say("the user denied it"),
           say("ok"))
    agent.turn("go")
    assert not (tmp_path / "a.txt").exists()
    assert "files changed by the subagent: none" in agent.messages[-2]["content"]


def test_parents_always_carries_into_the_subagent(agent, monkeypatch, tmp_path):
    agent.permissions.always.add("write_file")      # approved earlier in the session
    script(monkeypatch,
           calls("task", prompt="make a.txt", write=True),
           calls("write_file", path="a.txt", content="hi"),
           say("done"),
           say("ok"))
    agent.turn("go")                                 # no_prompts fixture: would raise
    assert (tmp_path / "a.txt").exists()


def test_harness_lists_real_changes_not_claimed_ones(agent, monkeypatch, tmp_path):
    """The subagent claims two files; only one write succeeded. The note says one."""
    agent.permissions = Permissions(yolo=True)
    script(monkeypatch,
           calls("task", prompt="edit", write=True),
           calls("write_file", path="real.txt", content="x"),
           calls("edit_file", path="missing.txt", old_string="a", new_string="b"),  # fails
           calls("bash", command="true"),
           say("I changed real.txt and missing.txt"),
           say("ok"))
    agent.turn("go")
    report = agent.messages[-2]["content"]
    assert report.startswith("I changed real.txt and missing.txt")
    assert "files changed by the subagent: real.txt; bash commands run: 1" in report


def test_research_subagent_has_no_change_note(agent, monkeypatch):
    script(monkeypatch, calls("task", prompt="look"), say("found it"), say("ok"))
    agent.turn("go")
    assert agent.messages[-2]["content"] == "found it"


def test_subagent_prompt_lists_the_project_files(agent, monkeypatch):
    """Without it the subagent opened with glob, or with bash it does not even have."""
    seen = script(monkeypatch, calls("task", prompt="look"), say("r"), say("ok"))
    agent.turn("go")
    sub_system = seen[1][2][0]["content"]
    assert "Files in the working directory" in sub_system and "big.txt" in sub_system
