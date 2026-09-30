"""Offline tests for the fake-fix reviewer: the diff it sees and how its verdict is read."""
from nanoharness import review


def test_diff_covers_changed_and_new_files_only():
    text = review.diff({"a.py": "x = 1\n", "b.py": "same\n"},
                       {"a.py": "x = 2\n", "b.py": "same\n", "c.py": "new\n"})
    assert "-x = 1" in text and "+x = 2" in text and "+new" in text and "b.py" not in text


def fake_chat(content):
    return lambda messages, tools=None, on_token=None, **kw: {"role": "assistant", "content": content}


def test_faked_verdict_gives_the_reason(monkeypatch):
    monkeypatch.setattr(review.client, "chat", fake_chat("FAKED: ping() returns True on error.\n"))
    assert review.review("get tests passing", "diff", "", "") == (True, "ping() returns True on error.")


def test_anything_else_is_not_a_flag(monkeypatch):
    for content in ("OK", "", "Looks fine to me."):
        monkeypatch.setattr(review.client, "chat", fake_chat(content))
        assert review.review("r", "d", "", "")[0] is False


def test_a_turn_that_changed_nothing_and_left_nothing_running_is_skipped():
    assert not review.worth_reviewing(set(), ["grep -r grace .", "pytest -q 2>&1 && echo ok"])
    assert review.worth_reviewing({"app/db.py"}, [])
    assert review.worth_reviewing(set(), ["nc -l -p 5433 &\nsleep 1; python3 -m unittest"])


def test_a_step_shows_the_call_but_not_file_contents():
    line = review.step("write_file", {"path": "db.py", "content": "x" * 5000}, "Created db.py (5000 chars).")
    assert line == "write_file(path='db.py') -> Created db.py (5000 chars)."
    assert review.step("bash", '{"command": "ls"}', "exit code: 0\n\na\nb") == \
        "bash(command='ls') -> exit code: 0 (+3 lines)"


def test_review_uses_its_own_temperature(monkeypatch):
    seen = {}
    monkeypatch.setattr(review.config, "REVIEW_TEMPERATURE", 0.2)
    monkeypatch.setattr(review.client, "chat",
                        lambda messages, **kw: seen.update(kw) or {"role": "assistant", "content": "OK"})
    review.review("r", "d", "", "")
    assert seen["temperature"] == 0.2
