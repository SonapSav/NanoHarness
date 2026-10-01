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
    assert tui.title("start_service", {"command": "python3 -m http.server 8069", "port": 8069}) == \
        "Service(python3 -m http.server 8069)"


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


# --- phase 2: LiveUI ------------------------------------------------------------------

import threading

from nanoharness.keys import Key


def live(width=50):
    out = io.StringIO()
    return tui.LiveUI(out=out, width=width, ticker=False, history=[]), out


def bare(lines):
    return [re.sub(r"\x1b\[[0-9;]*m", "", l) for l in lines]


def typed(u, text):
    for ch in text:
        u.handle_key(Key("char", ch))


def in_thread(fn):
    box = {}
    t = threading.Thread(target=lambda: box.setdefault("v", _call(fn)))
    t.start()
    return t, box


def _call(fn):
    try:
        return fn()
    except BaseException as e:
        return e


def test_box_and_footer_fit_the_width_and_show_the_cursor():
    u, _ = live()
    u.footer = "model · ctx 3%"
    typed(u, "hello")
    lines = bare(u.region())
    assert lines[0].startswith("╭") and lines[-2].startswith("╰")
    assert all(len(l) <= 49 for l in lines)
    assert lines[1].startswith("│ > hello") and lines[1].endswith("│")
    assert "\x1b[7m" in u.region()[1]                       # the drawn cursor
    assert "model · ctx 3%" in lines[-1] and "enter send" in lines[-1]


def test_long_and_multiline_input_wraps_inside_the_box():
    u, _ = live(width=30)
    typed(u, "a" * 40)
    u.handle_key(Key("newline"))
    typed(u, "second")
    rows = bare(u.region())[1:-2]
    assert len(rows) == 3 and all(len(r) == 29 for r in rows)
    assert "second" in rows[-1]


def test_read_line_returns_the_text_and_echoes_it_into_scrollback():
    u, out = live()
    u.terminal = object()                     # pretend started, so commits render
    u.terminal = None
    t, box = in_thread(u.read_line)
    typed(u, "fix it")
    u.handle_key(Key("enter"))
    t.join(2)
    assert box["v"] == "fix it" and "> fix it" in screen(out.getvalue())
    assert u.editor.history == ["fix it"]


def test_ctrl_c_clears_then_twice_on_empty_exits_and_ctrl_d_exits():
    u, _ = live()
    t, box = in_thread(u.read_line)
    typed(u, "oops")
    u.handle_key(Key("ctrl-c"))
    assert u.editor.text == "" and t.is_alive()
    u.handle_key(Key("ctrl-c"))
    assert "again to exit" in bare(u.region())[-1]
    u.handle_key(Key("ctrl-c"))
    t.join(2)
    assert isinstance(box["v"], EOFError)
    t, box = in_thread(u.read_line)
    u.handle_key(Key("ctrl-d"))
    t.join(2)
    assert isinstance(box["v"], EOFError)


def test_enter_during_a_turn_queues_and_esc_interrupts(monkeypatch):
    u, _ = live()
    killed = []
    monkeypatch.setattr("os.kill", lambda pid, sig: killed.append(sig))
    u.set_busy(True)
    typed(u, "next one")
    u.handle_key(Key("enter"))
    assert u.queued == ["next one"] and "1 queued" in bare(u.region())[-1]
    u.handle_key(Key("esc"))
    import signal
    assert killed == [signal.SIGINT]
    u.set_busy(False)
    assert u.read_line() == "next one"                 # queued: no waiting


def test_permission_is_an_arrow_key_menu(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKDIR", tmp_path)
    u, out = live()
    args = {"command": "rm -rf build"}
    tool = tools.REGISTRY["bash"]
    t, box = in_thread(lambda: u.permission("", tool.preview(**args), tool, args))
    for _ in range(50):
        if u.menu:
            break
        threading.Event().wait(0.01)
    lines = bare(u.region())
    assert "Allow Bash(rm -rf build)?" in lines[1] and "❯ 1. Yes" in lines[2]
    u.handle_key(Key("down"))
    u.handle_key(Key("down"))
    u.handle_key(Key("enter"))
    t.join(2)
    assert box["v"] == "n" and "denied" in screen(out.getvalue()) and u.menu is None


def test_menu_escape_cancels_to_no(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKDIR", tmp_path)
    u, _ = live()
    args = {"path": "x.py", "content": "1"}
    tool = tools.REGISTRY["write_file"]
    t, box = in_thread(lambda: u.permission("", tool.preview(**args), tool, args))
    for _ in range(50):
        if u.menu:
            break
        threading.Event().wait(0.01)
    u.handle_key(Key("esc"))
    t.join(2)
    assert box["v"] == "n"


def test_each_permission_option_gives_its_own_answer(tmp_path, monkeypatch):
    """Regression: the options were mapped in the wrong order, so No meant always."""
    monkeypatch.setattr(config, "WORKDIR", tmp_path)
    tool = tools.REGISTRY["bash"]
    args = {"command": "ls"}
    for key, want in (("y", "y"), ("a", "a"), ("n", "n")):
        u, _ = live()
        t, box = in_thread(lambda: u.permission("", tool.preview(**args), tool, args))
        for _ in range(50):
            if u.menu:
                break
            threading.Event().wait(0.01)
        u.handle_key(Key("char", key))
        t.join(2)
        assert box["v"] == want


def test_footer_never_overflows_a_narrow_terminal():
    for width in (40, 60, 80, 120):
        u, _ = live(width=width)
        u.footer = "~/Development/some-project · aeroadvisor-agent:latest · ctx 3%"
        assert len(bare(u.region())[-1]) <= width - 1


def test_footer_is_amber():
    u, _ = live()
    u.footer = "~/p · model · ctx 1%"
    assert u.region()[-1].startswith(tui.ORANGE)


def test_diff_bars_fill_the_width_and_mark_only_the_changed_words():
    out = tui.render_diff(["@@ -1 +1 @@", " same", "-weather for Abu Dhabi.", "+weather for Doha."], 40)
    removed, added = out[2], out[3]
    assert tui.visible(removed) == tui.visible(added) == 40               # full-width bars
    assert removed.startswith(tui.DEL_BG) and added.startswith(tui.ADD_BG)
    assert f"{tui.DEL_WORD}Abu Dhabi{tui.DEL_BG}" in removed
    assert f"{tui.ADD_WORD}Doha{tui.ADD_BG}" in added
    assert tui.DEL_WORD + "weather" not in removed                        # unchanged words stay faded
    assert tui.DEL_BG not in out[1] and tui.ADD_BG not in out[1]          # context: no bar


def test_unpaired_lines_get_a_bar_without_word_marks():
    out = tui.render_diff(["-gone", "-also gone", "+new"], None)
    assert len(out) == 3 and tui.ADD_WORD in out[2] and tui.DEL_WORD in out[0]   # paired: gone/new
    assert tui.DEL_WORD not in out[1]                                     # nothing to pair with
    assert tui.visible(out[1]) == len("-also gone")                       # width None: text only


def test_diff_colours_fall_back_to_256_without_truecolor(monkeypatch):
    import importlib
    monkeypatch.setenv("COLORTERM", "")
    assert "48;5;" in importlib.reload(tui).DEL_BG
    monkeypatch.setenv("COLORTERM", "truecolor")
    assert "48;2;" in importlib.reload(tui).DEL_BG


def test_spinner_shows_while_the_reviewer_checks():
    """Seen live: 17 s with nothing on screen after the answer, while the review ran."""
    u, _ = make()
    u.review_start()
    assert "Checking the changes… 3s · esc to skip" in u.status_text(u.running[-1][1] + 3.2)
    u.review(False, "OK")
    assert u.status_text() is None and not u.running


WEATHER = ("exit code: 0\n\nFetching weather for Abu Dhabi...\n" +
           "\n".join(f"line {i}" for i in range(1, 13)))


def test_a_cut_short_result_says_how_to_expand_and_ctrl_o_shows_it_all():
    """From a /note: '(+9 lines) but I don't have the means to see them'."""
    u, out = live()
    u.terminal = None
    u.tool_call("bash", {"command": "python3 get_weather.py"})
    u.tool_result("bash", {"command": "python3 get_weather.py"}, WEATHER)
    assert "… +9 lines (ctrl+o to expand)" in screen(out.getvalue())
    assert "line 12" not in screen(out.getvalue())
    u.handle_key(Key("ctrl-o"))
    shown = screen(out.getvalue())
    assert "Bash(python3 get_weather.py), in full:" in shown and "line 12" in shown


def test_nothing_to_expand_after_a_short_result():
    u, out = make()
    u.tool_call("bash", {"command": "ls"})
    u.tool_result("bash", {"command": "ls"}, "exit code: 0\n\na.py")
    assert "ctrl+o" not in screen(out.getvalue())
    u.expand()
    assert "nothing cut short to expand" in screen(out.getvalue())


def test_an_unfinished_line_gives_way_to_the_spinner_after_a_quiet_second():
    """From a /note: the text ended without a newline, then silence while a big write_file was
    generated, and nothing said the model was still working."""
    u, out = make()
    u("start", "")
    stream(u, "Now let me create the shared CSS and JavaScript files:")
    t = u.quiet_since
    u.settle(t + 0.5)
    assert u.partial and u.status_text(t + 0.5) is None          # still writing: leave it
    u.settle(t + 1.2)
    assert not u.partial and "Working… 1s" in u.status_text(t + 1.2)
    assert "⏺ Now let me create the shared CSS and JavaScript files:" in screen(out.getvalue())
    stream(u, " more")                                           # it carries on: a new line
    u("end", "")
    assert screen(out.getvalue()).endswith("  more")
