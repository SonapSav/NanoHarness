"""Offline tests for the fake-fix reviewer: the diff it sees and how its verdict is read."""
from nanoharness import review


def test_diff_covers_changed_and_new_files_only():
    text = review.diff({"a.py": "x = 1\n", "b.py": "same\n"},
                       {"a.py": "x = 2\n", "b.py": "same\n", "c.py": "new\n"})
    assert "-x = 1" in text and "+x = 2" in text and "+new" in text and "b.py" not in text


def fake_chat(content):
    return lambda messages, tools=None, on_token=None: {"role": "assistant", "content": content}


def test_faked_verdict_gives_the_reason(monkeypatch):
    monkeypatch.setattr(review.client, "chat", fake_chat("FAKED: ping() returns True on error.\n"))
    assert review.review("get tests passing", "diff", "", "") == (True, "ping() returns True on error.")


def test_anything_else_is_not_a_flag(monkeypatch):
    for content in ("OK", "", "Looks fine to me."):
        monkeypatch.setattr(review.client, "chat", fake_chat(content))
        assert review.review("r", "d", "", "")[0] is False
