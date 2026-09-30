"""Offline tests for session persistence (SESSION_DIR is a temp dir, see conftest.py)."""
import json
import stat

import pytest

from nanoharness import cli, client, config, session
from nanoharness.agent import Agent
from nanoharness.client import ModelError
from nanoharness.permissions import Permissions
from nanoharness.session import Session, SessionError


@pytest.fixture(autouse=True)
def workdir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKDIR", tmp_path)
    return tmp_path


def script(monkeypatch, *steps):
    """Scripted model; records every history it was sent."""
    steps, seen = iter(steps), []

    def fake_chat(messages, tools=None, on_token=None):
        seen.append([dict(m) for m in messages])
        step = next(steps)
        if isinstance(step, BaseException):
            raise step
        return step
    monkeypatch.setattr(client, "chat", fake_chat)
    return seen


def answer(text):
    return {"role": "assistant", "content": text}


def write_call(path):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": "write_file",
                                         "arguments": {"path": path, "content": "x"}}}]}


def saved(sess):
    return json.loads(sess.path.read_text())


def test_nothing_is_written_until_the_first_message():
    Session().save([{"role": "system", "content": "sys"}])
    assert list(config.SESSION_DIR.iterdir()) == []


def test_file_is_private_and_records_workdir(workdir):
    s = Session()
    s.save([{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}])
    assert stat.S_IMODE(s.path.stat().st_mode) == 0o600
    assert saved(s)["workdir"] == str(workdir)
    assert not list(config.SESSION_DIR.glob("*.tmp"))


def test_ids_do_not_collide():
    a = Session()
    a.save([{"role": "user", "content": "a"}])
    assert Session().id != a.id


def test_agent_saves_after_every_tool_result(monkeypatch):
    """A crash mid-turn must not lose the record of what already ran."""
    script(monkeypatch, write_call("a.txt"), ModelError("down"))
    agent = Agent(Permissions(yolo=True), session=Session())
    with pytest.raises(ModelError):
        agent.turn("make a.txt")
    assert [m["role"] for m in saved(agent.session)["messages"]] == ["system", "user", "assistant", "tool"]


def test_dropped_input_is_dropped_on_disk_too(monkeypatch):
    script(monkeypatch, answer("one"), ModelError("down"))
    agent = Agent(Permissions(yolo=True), session=Session())
    agent.turn("first")
    with pytest.raises(ModelError):
        agent.turn("second")
    assert all(m["content"] != "second" for m in saved(agent.session)["messages"])


def test_resume_restores_history_with_a_fresh_system_prompt(monkeypatch):
    script(monkeypatch, answer("noted"))
    first = Agent(Permissions(yolo=True), session=Session())
    first.turn("remember 42")

    sess, history = session.load(first.session.id)
    stale = [{"role": "system", "content": "OLD PROMPT"}] + history[1:]
    resumed = Agent(session=sess, messages=stale)
    assert resumed.messages[0]["content"].startswith(config.system_prompt())
    assert resumed.messages[1:] == first.messages[1:]


def test_resume_refuses_another_workdir(monkeypatch, tmp_path_factory):
    s = Session()
    s.save([{"role": "user", "content": "hi"}])
    monkeypatch.setattr(config, "WORKDIR", tmp_path_factory.mktemp("elsewhere"))
    with pytest.raises(SessionError, match="cd there"):
        session.load(s.id)
    assert session.list_sessions() == []


def test_unknown_or_corrupt_sessions(monkeypatch):
    with pytest.raises(SessionError, match="no session"):
        session.load("nope")
    config.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    (config.SESSION_DIR / "broken.json").write_text("{not json")
    with pytest.raises(SessionError, match="cannot read"):
        session.load("broken")
    assert session.list_sessions() == []      # skipped, not fatal


def test_list_is_newest_first_with_titles(monkeypatch):
    old, new = Session("20260101-000000"), Session("20260102-000000")
    old.save([{"role": "user", "content": "old   task\nwith newline"}])
    new.save([{"role": "user", "content": "new task"}])
    listed = session.list_sessions()
    assert [s["id"] for s in listed] == [new.id, old.id]
    assert listed[1]["title"] == "old task with newline"
    assert session.latest() == new.id


# --- CLI -------------------------------------------------------------------

def run_cli(monkeypatch, capsys, argv, lines):
    lines = iter(lines)

    def fake_input(*a):
        try:
            return next(lines)
        except StopIteration:
            raise EOFError from None
    monkeypatch.setattr("builtins.input", fake_input)
    code = cli.main(["--yolo", *argv])
    return code, capsys.readouterr().out


def test_continue_picks_up_where_it_left_off(monkeypatch, capsys):
    seen = script(monkeypatch, answer("noted"), answer("it was 42"))
    run_cli(monkeypatch, capsys, [], ["remember 42"])
    code, out = run_cli(monkeypatch, capsys, ["-c"], ["what was it?"])
    assert code == 0 and "resumed" in out and "remember 42" in out
    assert [m["content"] for m in seen[-1] if m["role"] == "user"] == ["remember 42", "what was it?"]


def test_continue_with_nothing_saved_starts_fresh(monkeypatch, capsys):
    script(monkeypatch)
    code, out = run_cli(monkeypatch, capsys, ["-c"], [])
    assert code == 0 and "starting a new one" in out


def test_resume_picker_by_number(monkeypatch, capsys):
    Session("20260101-000000").save([{"role": "user", "content": "older"}])
    Session("20260102-000000").save([{"role": "user", "content": "newer"}])
    script(monkeypatch)
    code, out = run_cli(monkeypatch, capsys, ["--resume"], ["2"])
    assert "resumed" in out and "'older'" in out


def test_resume_unknown_id_exits_nonzero(monkeypatch, capsys):
    code, out = run_cli(monkeypatch, capsys, ["--resume", "nope"], [])
    assert code == 1 and "no session" in out


def test_reset_starts_a_new_session_and_keeps_the_old(monkeypatch, capsys):
    script(monkeypatch, answer("a"), answer("b"))
    run_cli(monkeypatch, capsys, [], ["one", "/reset", "two"])
    assert len(session.list_sessions()) == 2


def test_the_archive_survives_a_resume():
    s = Session()
    archived = [{"role": "user", "content": "the port is 9310"}]
    s.save([{"role": "user", "content": "hi"}], archived)
    sess, _ = session.load(s.id)
    assert Agent(session=sess).archive == archived


def test_sessions_saved_without_an_archive_still_load():
    s = Session()
    s.save([{"role": "user", "content": "hi"}])
    assert "archive" not in saved(s)
    sess, _ = session.load(s.id)
    assert Agent(session=sess).archive == []
