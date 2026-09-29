"""Offline tests for the REPL: scripted input, scripted model."""
import pytest

from nanoharness import cli, client, config
from nanoharness.client import ModelError


@pytest.fixture
def run(tmp_path, monkeypatch, capsys):
    """Feed lines to the REPL, script the model, return what was printed."""
    monkeypatch.setattr(config, "WORKDIR", tmp_path)

    def go(lines, *steps):
        lines, steps = iter(lines), iter(steps)

        def fake_input(*a):
            try:
                return next(lines)
            except StopIteration:
                raise EOFError from None

        def fake_chat(messages, tools=None):
            step = next(steps)
            if isinstance(step, BaseException):
                raise step
            return step

        monkeypatch.setattr("builtins.input", fake_input)
        monkeypatch.setattr(client, "chat", fake_chat)
        cli.main(["--yolo"])
        return capsys.readouterr().out
    return go


def test_error_before_reply_says_resend(run):
    out = run(["hello"], ModelError("down"))
    assert "send it again" in out


def test_error_after_tool_ran_says_continue(run):
    out = run(["make a.txt"],
              {"role": "assistant", "content": "",
               "tool_calls": [{"function": {"name": "write_file",
                                            "arguments": {"path": "a.txt", "content": "x"}}}]},
              ModelError("down"))
    assert '"continue"' in out


def test_interrupt_before_reply_says_resend(run):
    out = run(["hello"], KeyboardInterrupt())
    assert "interrupted" in out and "send it again" in out
