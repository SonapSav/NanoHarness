"""Offline tests: no model, no network. Every one of these is a bug we actually hit."""
import time

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


def test_edit_prompt_shows_the_change(agent, tmp_path, monkeypatch, capsys):
    """A one-line 'edit tests/test_db.py' gave the user nothing to refuse a faked test on."""
    (tmp_path / "t.py").write_text("def test_db():\n    assert db.ping()\n")
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    agent.permissions = Permissions(yolo=False)
    call(agent, "edit_file", path="t.py", old_string="assert db.ping()", new_string="assert True")
    shown = capsys.readouterr().out
    assert "-    assert db.ping()" in shown and "+    assert True" in shown


def test_write_prompt_shows_new_file_and_caps_the_diff(agent, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    agent.permissions = Permissions(yolo=False)
    call(agent, "write_file", path="big.txt", content="\n".join(f"line {i}" for i in range(100)))
    shown = capsys.readouterr().out
    assert "+line 0" in shown and "+line 99" not in shown and "more diff lines" in shown


def test_preview_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKDIR", tmp_path)
    assert tools.diff_preview("write /etc/x", "/etc/x", lambda before: "y") == "write /etc/x"


def test_reads_never_prompt(agent, monkeypatch):
    def boom(*a):
        raise AssertionError("a read should not ask for permission")
    call(agent, "write_file", path="r.txt", content="x")   # written while still yolo
    monkeypatch.setattr("builtins.input", boom)
    agent.permissions = Permissions(yolo=False)
    assert "x" in call(agent, "read_file", path="r.txt")


def test_bash_timeout_kills_background_children(agent, monkeypatch, tmp_path):
    """Regression: the timeout killed only the shell, leaving its children running."""
    monkeypatch.setattr(config, "BASH_TIMEOUT", 1)
    marker = tmp_path / "survived"
    out = call(agent, "bash", command=f"(sleep 2; touch {marker}) & wait")
    assert "timed out" in out
    time.sleep(2.5)
    assert not marker.exists()


# --- glob / grep -----------------------------------------------------------

@pytest.fixture
def tree(agent, tmp_path):
    """A small project, plus the junk directories that must never show up."""
    for path, text in {
        "main.py": "import os\ndef main():\n    return 1\n",
        "src/app.py": "def handler():\n    # TODO: validate\n    pass\n",
        "src/deep/util.py": "def helper():\n    return 'Hello'\n",
        "README.md": "# todo list\n",
        ".venv/lib/site.py": "def main(): pass\n",
        "node_modules/x/index.js": "// TODO\n",
        "pkg.egg-info/PKG-INFO": "TODO\n",
    }.items():
        p = tmp_path / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return tmp_path


def test_glob_name_pattern_matches_at_any_depth(agent, tree):
    assert call(agent, "glob", pattern="*.py").splitlines() == [
        "main.py", "src/app.py", "src/deep/util.py"]


def test_glob_double_star_includes_zero_dirs(agent, tree):
    out = call(agent, "glob", pattern="src/**/*.py").splitlines()
    assert out == ["src/app.py", "src/deep/util.py"]


def test_glob_single_star_stays_in_one_dir(agent, tree):
    assert call(agent, "glob", pattern="src/*.py").splitlines() == ["src/app.py"]


def test_glob_relative_to_path(agent, tree):
    assert call(agent, "glob", pattern="deep/*.py", path="src").splitlines() == ["src/deep/util.py"]


def test_glob_no_match_says_so(agent, tree):
    assert "No files match" in call(agent, "glob", pattern="*.rs")


def test_glob_announces_truncation(agent, tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "MAX_GLOB_RESULTS", 2)
    for i in range(3):
        (tmp_path / f"f{i}.txt").write_text("x")
    assert "[showing 2 of 3 files" in call(agent, "glob", pattern="*.txt")


def test_grep_reports_path_and_line(agent, tree):
    assert call(agent, "grep", pattern=r"def \w+\(").splitlines() == [
        "main.py:2: def main():", "src/app.py:1: def handler():", "src/deep/util.py:1: def helper():"]


def test_grep_skips_junk_dirs(agent, tree):
    out = call(agent, "grep", pattern="TODO")
    assert out == "src/app.py:2: # TODO: validate"


def test_grep_ignore_case_and_glob_filter(agent, tree):
    assert call(agent, "grep", pattern="todo", ignore_case=True, glob="*.md") == "README.md:1: # todo list"


def test_grep_single_file(agent, tree):
    assert call(agent, "grep", pattern="Hello", path="src/deep/util.py") == \
        "src/deep/util.py:2: return 'Hello'"


def test_grep_bad_regex_is_explained(agent, tree):
    assert "Invalid regular expression" in call(agent, "grep", pattern="foo(")


def test_grep_skips_binary_files(agent, tmp_path):
    (tmp_path / "blob.bin").write_bytes(b"\0\1needle\0")
    (tmp_path / "t.txt").write_text("needle\n")
    assert call(agent, "grep", pattern="needle") == "t.txt:1: needle"


def test_grep_stops_at_the_match_cap(agent, tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "MAX_GREP_MATCHES", 3)
    (tmp_path / "many.txt").write_text("hit\n" * 10)
    out = call(agent, "grep", pattern="hit")
    assert out.count("many.txt:") == 3 and "stopped at 3 matches" in out


def test_search_cannot_escape_workdir(agent, tree):
    assert "outside the working directory" in call(agent, "grep", pattern="x", path="..")
    assert "outside the working directory" in call(agent, "glob", pattern="*", path="/etc")


def test_search_ignores_symlinks_pointing_outside(agent, tmp_path, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "secret.txt"
    outside.write_text("password=hunter2\n")
    (tmp_path / "link.txt").symlink_to(outside)
    assert "No matches" in call(agent, "grep", pattern="hunter2")
    assert "No files match" in call(agent, "glob", pattern="*.txt")


def test_search_tools_never_prompt(agent, tree, monkeypatch):
    def boom(*a):
        raise AssertionError("a search should not ask for permission")
    monkeypatch.setattr("builtins.input", boom)
    agent.permissions = Permissions(yolo=False)
    call(agent, "glob", pattern="*.py")
    call(agent, "grep", pattern="def")


def search(archive, pattern):
    token = tools.ARCHIVE.set(archive)
    try:
        return tools.search_history(pattern)
    finally:
        tools.ARCHIVE.reset(token)


def test_search_history_finds_a_detail_deep_inside_one_long_line():
    line = "filler " * 1000 + "the probe path is /_probe/ready ok " + "filler " * 1000
    out = search([{"role": "user", "content": line}], "probe path")
    assert "/_probe/ready" in out and out.startswith("[message 1, user] ...")
    assert len(out) < 600                                   # a snippet, not the line


def test_search_history_covers_tool_calls_and_says_when_empty():
    call = {"role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": "write_file", "arguments": {"path": "cfg.py"}}}]}
    assert "write_file" in search([call], "cfg\\.py")
    assert "Nothing has been summarized yet" in search([], "x")
    assert "No matches" in search([call], "nowhere")
    with pytest.raises(tools.ToolError):
        search([call], "(")


def test_search_history_caps_its_matches(monkeypatch):
    monkeypatch.setattr(tools, "MAX_HISTORY_HITS", 3)
    archive = [{"role": "user", "content": "hit"} for _ in range(10)]
    out = search(archive, "hit")
    assert out.count("[message") == 3 and "stopped at 3" in out


def test_one_noisy_message_cannot_crowd_out_the_rest():
    # Seen live: filler matching "probe" in one file read used every hit, hiding the real line.
    noisy = {"role": "tool", "tool_name": "read_file", "content": "\n".join(["probe noise"] * 200)}
    real = {"role": "user", "content": "the load balancer probes /_probe/ready"}
    out = search([noisy, real], "probe")
    assert "/_probe/ready" in out
    assert out.count("[message 1,") == 3 and "more matches in message 1" in out


def test_search_history_says_what_to_do_when_the_detail_is_not_there():
    archive = [{"role": "assistant", "content": "I've noted the readiness probe requirement."}]
    for out in (search(archive, "readiness"), search(archive, "nowhere")):
        assert "don't guess" in out
    assert "search again with other words" in search(archive, "readiness")
    assert "Try other words" in search(archive, "nowhere")


def test_rare_matches_are_shown_before_common_ones():
    # Seen live: "ops" matched every email's From: line and hid the one line that mattered.
    emails = "\n\n".join(f"From: ops{n}@example.com\n" + "body " * 80 for n in range(10))
    thread = emails + "\nthe load balancer probes GET /_probe/ready\n" + emails
    out = search([{"role": "user", "content": thread}], "ops|load balancer")
    assert "/_probe/ready" in out
    assert out.count("[message 1,") == 3 and "more matches in message 1" in out


def test_search_history_says_when_only_its_own_replies_matched():
    own = {"role": "assistant", "content": "I've noted the readiness probe requirement."}
    user = {"role": "user", "content": "the load balancer probes GET /_probe/ready"}
    assert "Only your own earlier replies matched" in search([own, user], "readiness")
    assert "Only your own" not in search([own, user], "probe")    # the user's message matched too


def test_bash_commands_get_no_terminal_input(agent):
    """A command that reads stdin must not wait on (or steal from) the user's terminal."""
    out = call(agent, "bash", command="read line; echo \"got:[$line] status:$?\"")
    assert "got:[] status:1" in out
