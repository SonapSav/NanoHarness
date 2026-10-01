"""Offline tests for the REPL: scripted input, scripted model."""
import re

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

        def fake_chat(messages, tools=None, on_token=None):
            step = next(steps)
            if isinstance(step, BaseException):
                raise step
            if on_token and step.get("content"):   # stream it, like the real client
                for word in step["content"].split(" "):
                    on_token("content", word + " ")
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


def test_streamed_answer_is_printed_once(run):
    out = run(["hi"], {"role": "assistant", "content": "hello there"})
    assert out.count("hello there") == 1


def test_answer_is_printed_when_streaming_is_off(run, monkeypatch):
    monkeypatch.setattr(config, "STREAM", False)
    out = run(["hi"], {"role": "assistant", "content": "hello there"})
    assert out.count("hello there") == 1


def test_stop_message_is_printed_even_when_streaming(run, monkeypatch):
    monkeypatch.setattr(config, "MAX_STEPS", 1)
    out = run(["loop"], {"role": "assistant", "content": "working",
                         "tool_calls": [{"function": {"name": "read_file",
                                                      "arguments": {"path": "x"}}}]})
    assert "stopped after 1 tool rounds" in out


def test_status_line_shows_only_while_a_reply_is_open_and_quiet(capsys):
    """Seen live: 14 s with nothing on screen while the model wrote a tool call."""
    p = cli.StreamPrinter(ticker=False)
    assert p.status_text() is None                       # no reply open
    p("start", "")
    t = p.quiet_since
    assert p.status_text(t + 0.5) is None                # not quiet long enough
    assert "waiting for the model · 3s" in p.status_text(t + 3.2)
    p("content", "I'll write it. ")
    t = p.quiet_since
    assert "working · 12s" in p.status_text(t + 12.1)    # text came, then silence
    p("end", "")
    assert p.status_text(t + 30) is None                 # closed: a permission prompt may follow
    assert p.last == "I'll write it."


def test_status_line_is_cleared_when_tokens_resume_or_the_turn_stops(capsys):
    p = cli.StreamPrinter(ticker=False)
    p("start", "")
    p.status = True                                      # as if the ticker had drawn it
    p("thinking", "hmm")
    assert not p.status and "\r\033[2K" in capsys.readouterr().out
    p.status = True
    p.stop()
    assert not p.status and not p.open and p.status_text() is None


def test_the_repl_stops_the_status_line_when_a_turn_fails(run, monkeypatch):
    stopped = []
    real = cli.StreamPrinter.stop
    monkeypatch.setattr(cli.StreamPrinter, "stop", lambda self: (stopped.append(1), real(self)))
    out = run(["hello"], ModelError("down"))
    assert stopped and "down" in out


def test_status_shows_what_the_banner_leaves_out(run):
    out = run(["/status"])
    for key in ("host", "sandbox", "context", "review", "yolo"):
        assert f"{key:<8}" in out
    assert config.OLLAMA_HOST in out and "tokens" in out


def test_rich_banner_panel_has_the_essentials_and_not_the_host():
    text = re.sub(r"\x1b\[[0-9;]*m", "", cli.rich_banner("20261001-000000", yolo=False, columns=120))
    assert config.MODEL in text and "20261001-000000" in text and "/help for commands" in text
    assert "NanoHarness v" in text.split("\n")[0]                   # title in the top border
    assert "files: read_file, write_file, edit_file" in text and "sandbox:" in text
    assert config.OLLAMA_HOST not in text


def test_rich_banner_fits_a_narrow_terminal(monkeypatch, tmp_path):
    deep = tmp_path / ("very-long-directory-name-" * 4) / "project"
    monkeypatch.setattr(config, "WORKDIR", deep)
    lines = [re.sub(r"\x1b\[[0-9;]*m", "", l) for l in cli.rich_banner("x", False, columns=60).split("\n")]
    box = lines[:lines.index(next(l for l in lines if l.startswith("╰"))) + 1]
    assert all(len(l) <= 60 for l in box)
    assert any("…" in l and l.rstrip(" │").endswith("/project") for l in lines)


def test_help_lists_commands_and_keys_only_with_the_box(run):
    out = run(["/help"])
    assert "/resume [n|id]" in out and "/status" in out and "Keys" not in out   # plain: no box


def test_footer_starts_with_the_directory(monkeypatch, tmp_path):
    from pathlib import Path
    monkeypatch.setattr(config, "WORKDIR", Path.home() / "Development" / "testllm")
    agent = cli.Agent()
    assert cli.footer(agent).startswith(f"~/Development/testllm · {config.MODEL} · ctx ")
    monkeypatch.setattr(config, "WORKDIR", tmp_path / ("x" * 60) / "proj")
    left = cli.footer(cli.Agent())
    assert left.startswith("…") and left.split(" · ")[0].endswith("/proj") and len(left.split(" · ")[0]) == 32


def test_note_appends_a_findable_record(run):
    import json
    out = run(["/note the spinner froze after Esc", "/note second one", "/note"])
    lines = cli.notes_path().read_text().splitlines()
    first = json.loads(lines[0])
    assert first["note"] == "the spinner froze after Esc" and first["session"] and first["messages"] >= 1
    assert first["workdir"] == str(config.WORKDIR) and len(lines) == 2
    assert "noted" in out and "2 notes in" in out


def test_footer_shows_the_last_turn_in_ms(run, monkeypatch):
    agent = cli.Agent()
    assert " ms" not in cli.footer(agent)                     # nothing before the first turn
    agent.last_turn_ms = 12345
    assert cli.footer(agent).endswith(" · 12,345 ms")
    monkeypatch.setattr(client, "chat", lambda *a, **k: {"role": "assistant", "content": "hi"})
    args = cli.parse_args([])
    agent = cli.step(cli.Agent(), None, None, args, "hello")
    assert isinstance(agent.last_turn_ms, int) and agent.last_turn_ms >= 0


def test_every_listed_command_is_handled_and_counted(monkeypatch):
    """The panel's count comes from COMMANDS; none of them may reach the model as a message."""
    def model(*a, **k):
        raise AssertionError("a command was sent to the model")
    monkeypatch.setattr(client, "chat", model)
    monkeypatch.setattr("builtins.input", lambda *a: "")         # /resume's picker: stay
    args = cli.parse_args([])
    for usage, _ in cli.COMMANDS:
        name = usage.split()[0]
        cli.step(cli.Agent(session=cli.Session()), None, None, args, name)   # as the REPL has
    text = re.sub(r"\x1b\[[0-9;]*m", "", cli.rich_banner("x", False, columns=120))
    assert f"8 tools · {len(cli.COMMANDS)} commands · /help for commands" in text


def test_footer_shows_the_last_turns_tokens(monkeypatch):
    agent = cli.Agent()
    agent.last_turn_ms, agent.last_turn_tokens = 900, (23410, 1191)
    assert cli.footer(agent).endswith(" · 900 ms · 23.4k in / 1.2k out")
    agent.last_turn_tokens = (512, 40)
    assert cli.footer(agent).endswith(" · 512 in / 40 out")


def test_a_turn_records_every_model_call_it_made(monkeypatch):
    def model(*a, **k):
        client.USAGE["in"] += 1000
        client.USAGE["out"] += 10
        return {"role": "assistant", "content": "hi"}
    monkeypatch.setattr(client, "chat", model)
    agent = cli.step(cli.Agent(session=cli.Session()), None, None, cli.parse_args([]), "hello")
    assert agent.last_turn_tokens == (1000, 10)


def test_expand_in_plain_mode_prints_the_last_tool_result(run):
    out = run(["do it", "/expand"],
              {"role": "assistant", "content": "", "tool_calls": [
                  {"function": {"name": "bash", "arguments": {"command": "seq 1 12"}}}]},
              {"role": "assistant", "content": "done"})
    assert "exit code: 0" in out and "\n12\n" in out
