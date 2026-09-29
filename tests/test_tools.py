"""Offline tests: no model, no network. Every one of these is a bug we actually hit."""
import pytest

from nanoharness import config, tools
from nanoharness.agent import Agent
from nanoharness.permissions import Permissions


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKDIR", tmp_path)
    return Agent(Permissions(yolo=True))


def call(agent, name, **args):
    return agent.run_tool({"function": {"name": name, "arguments": args}})


def test_write_read_edit_roundtrip(agent):
    call(agent, "write_file", path="a.py", content="print('hi')\n")
    assert "print('hi')" in call(agent, "read_file", path="a.py")
    call(agent, "edit_file", path="a.py", old_string="hi", new_string="bye")
    assert "bye" in call(agent, "read_file", path="a.py")


def test_edit_refuses_ambiguous_match(agent):
    call(agent, "write_file", path="a.py", content="x\nx\n")
    assert "appears 2 times" in call(agent, "edit_file", path="a.py", old_string="x", new_string="y")


def test_edit_refuses_missing_match(agent):
    call(agent, "write_file", path="a.py", content="x\n")
    assert "not found" in call(agent, "edit_file", path="a.py", old_string="zzz", new_string="y")


def test_file_tools_cannot_escape_workdir(agent):
    assert "outside the working directory" in call(agent, "read_file", path="../../../etc/passwd")


def test_unknown_tool_is_reported_not_raised(agent):
    assert "no such tool" in call(agent, "definitely_not_a_tool")


def test_bad_arguments_are_reported_not_raised(agent):
    assert "bad arguments" in call(agent, "read_file", wrong_kwarg=1)


def test_arguments_as_json_string_are_parsed(agent):
    """Small models sometimes send arguments as a string instead of an object."""
    out = agent.run_tool({"function": {"name": "write_file",
                                       "arguments": '{"path": "s.txt", "content": "ok"}'}})
    assert "Created" in out


def test_bash_reports_nonzero_exit(agent):
    assert "exit code: 3" in call(agent, "bash", command="exit 3")


def test_truncation_is_announced(agent):
    long = "y" * (config.MAX_TOOL_OUTPUT + 500)
    assert "[truncated:" in tools.truncate(long)


def test_denied_call_says_nothing_changed(agent, tmp_path, monkeypatch):
    """Regression: the model claimed success after a denial, so the wording matters."""
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    agent.permissions = Permissions(yolo=False)
    out = call(agent, "write_file", path="nope.txt", content="x")
    assert "DENIED" in out and "did NOT run" in out
    assert not (tmp_path / "nope.txt").exists()


def test_always_allow_prompts_only_once(agent, monkeypatch):
    answers = iter(["a"])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    agent.permissions = Permissions(yolo=False)
    call(agent, "write_file", path="one.txt", content="1")
    call(agent, "write_file", path="two.txt", content="2")   # StopIteration if it re-prompts


def test_reads_never_prompt(agent, monkeypatch):
    def boom(*a):
        raise AssertionError("a read should not ask for permission")
    call(agent, "write_file", path="r.txt", content="x")   # written while still yolo
    monkeypatch.setattr("builtins.input", boom)
    agent.permissions = Permissions(yolo=False)
    assert "x" in call(agent, "read_file", path="r.txt")
