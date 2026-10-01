"""Offline tests for the rich terminal UI: rendered into a string, read back as a terminal would."""
import io
import re

from nanoharness import config, tools, tui


def screen(raw):
    """What a terminal would show: `\\r\\033[2K` clears the line, colours dropped."""
    lines = [""]
    for chunk in re.split(r"(\r\x1b\[2K|\n)", raw):
        if chunk == "\r\x1b[2K":
            lines[-1] = ""
        elif chunk == "\n":
            lines.append("")
        else:
            lines[-1] += chunk
    return re.sub(r"\x1b\[[0-9;]*m", "", "\n".join(lines)).rstrip("\n")


def make(width=60):
    out = io.StringIO()
    return tui.RichUI(out=out, width=width, ticker=False), out


def stream(u, text):
    for piece in re.findall(r"\S+|\s+", text):
        u("content", piece)


def test_titles_name_the_tool_and_what_it_is_about():
    assert tui.title("write_file", {"path": "a.py", "content": "x"}) == "Write(a.py)"
    assert tui.title("bash", {"command": "pytest  -q\n--tb=short"}) == "Bash(pytest -q --tb=short)"
    assert tui.title("bash", {"command": "x" * 100}).endswith("…)")
    assert tui.title("mystery", {}) == "mystery()"


def test_summaries():
    assert tui.summary("read_file", "     1\tfoo\n     2\tbar") == ["Read 2 lines"]
    assert tui.summary("bash", "exit code: 0\n\na\nb") == ["a", "b"]
    long = tui.summary("bash", "exit code: 1\n\n" + "\n".join(map(str, range(9))))
    assert "exit code: 1" in long[0] and "… +5 lines" in long[-1]
    assert "Error: nope" in tui.summary("grep", "Error: nope")[0]
    assert tui.summary("glob", "a.py\n\n[You already made this exact call ...]") == ["a.py"]


def test_markdown_lines():
    md = tui.Markdown()
    strip = lambda s: re.sub(r"\x1b\[[0-9;]*m", "", s)
    assert strip(md.line("## Plan")) == "Plan"
    assert strip(md.line("- **one** `x`")) == "• one x"
    md.line("```")
    assert md.line("- not a bullet in code").endswith("- not a bullet in code\x1b[0m")
    md.line("```")
    assert strip(md.line("- bullet again")) == "• bullet again"


def test_a_reply_renders_with_folded_thinking_and_one_bullet():
    u, out = make()
    u("start", "")
    u("thinking", "let me think")
    stream(u, "The output is:\n\n```\n1\n4\n```\nA line long enough that it has to wrap at sixty columns, not overflow.")
    u("end", "")
    lines = screen(out.getvalue()).split("\n")
    assert lines[:7] == ["✻ Thought for 1s", "⏺ The output is:", "  ", "  ```", "  1", "  4", "  ```"]
    wrapped = lines[7:]
    assert len(wrapped) == 2 and all(len(l) <= 60 and l.startswith("  ") for l in wrapped)
    assert " ".join(l.strip() for l in wrapped) == (
        "A line long enough that it has to wrap at sixty columns, not overflow.")
    assert u.last.startswith("The output is:")


def test_the_live_line_never_holds_a_line_break():
    u, out = make()
    drawn = []
    real = u._draw
    u._draw = lambda text, flush=True: (drawn.append(text), real(text, flush))[1]
    u("start", "")
    stream(u, "one\n\ntwo\n```\nthree\n```")
    u("end", "")
    assert drawn and not any("\n" in t for t in drawn)


def test_tool_call_and_result_with_a_diff_under_yolo(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKDIR", tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")
    u, out = make()
    args = {"path": "a.py", "old_string": "x = 1", "new_string": "x = 2"}
    u.tool_call("edit_file", args)
    tools.edit_file(**args)
    u.tool_result("edit_file", args, "Edited a.py.")
    shown = screen(out.getvalue())
    assert shown.startswith("⏺ Write(a.py)") is False and shown.startswith("⏺ Edit(a.py)")
    assert "⎿  Edited a.py." in shown and "-x = 1" in shown and "+x = 2" in shown


def test_a_diff_shown_at_the_prompt_is_not_repeated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKDIR", tmp_path)
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    u, out = make()
    args = {"path": "b.py", "content": "print(1)\n"}
    u.tool_call("write_file", args)
    tool = tools.REGISTRY["write_file"]
    assert u.permission("", tool.preview(**args), tool, args) == "y"
    u.tool_result("write_file", args, "Created b.py (9 chars).")
    shown = screen(out.getvalue())
    assert shown.count("+print(1)") == 1 and shown.count("Write(b.py)") == 1


def test_subagent_calls_are_nested_and_quiet():
    u, out = make()
    u.tool_call("task", {"prompt": "find the config"})
    u.tool_call("grep", {"pattern": "PORT"}, depth=1)
    u.tool_result("grep", {"pattern": "PORT"}, "app.py:1: PORT = 1", depth=1)
    u.tool_result("task", {"prompt": "find the config"}, "It is in app.py.")
    assert screen(out.getvalue()) == (
        "⏺ Task(find the config)\n"
        "  ⎿  ↳ Grep(PORT)\n"
        "  ⎿  Done: It is in app.py.")


def test_spinner_labels():
    u, _ = make()
    assert u.status_text() is None
    u("start", "")
    t = u.quiet_since
    assert u.status_text(t + 0.5) is None
    assert "Working… 3s" in u.status_text(t + 3.2)
    u("thinking", "hmm")
    assert "Thinking…" in u.status_text(u.thinking_since + 2)
    u("end", "")
    u.tool_call("bash", {"command": "sleep 5"})
    assert "Running Bash… 4s" in u.status_text(u.running[-1][1] + 4.1)
    u.tool_result("bash", {"command": "sleep 5"}, "exit code: 0\n\n")
    assert u.status_text() is None
