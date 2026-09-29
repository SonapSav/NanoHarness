"""The bash sandbox. The behavioural tests need a working bwrap; they are skipped where
it is not installed, but where it IS installed it must work: a broken sandbox in auto
mode silently means an unsandboxed bash, which is how the first version shipped."""
import shutil
from pathlib import Path

import pytest

from nanoharness import config, sandbox, tools

has_bwrap = shutil.which("bwrap") is not None
needs_bwrap = pytest.mark.skipif(not has_bwrap, reason="bwrap not installed")


@pytest.fixture(autouse=True)
def workdir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKDIR", tmp_path)
    monkeypatch.setattr(config, "SANDBOX", "auto")
    return tmp_path


def run(command):
    return tools.bash(command).split("\n\n", 1)[1]


@needs_bwrap
def test_installed_bwrap_actually_works():
    """Regression: the probe passed/failed on a different mount set than real runs."""
    assert sandbox.probe() is None
    assert sandbox.active()


@needs_bwrap
def test_home_is_empty_and_other_homes_are_gone():
    assert run("ls -A ~ | wc -l").strip() == "0"
    assert run("ls /home").split() == [Path.home().name]


@needs_bwrap
def test_run_and_mounts_are_hidden():
    assert run("ls /run /media /mnt /var 2>&1 | grep -c 'No such file'").strip() == "4"


@needs_bwrap
def test_only_workdir_is_writable(workdir):
    assert "Read-only" in run("touch /usr/nope 2>&1")
    run("echo hi > made.txt")
    assert (workdir / "made.txt").read_text() == "hi\n"


@needs_bwrap
def test_environment_is_cleared(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "leak-me")
    assert "leak-me" not in run("env")


@needs_bwrap
def test_tmp_does_not_persist_between_commands():
    run("echo x > /tmp/left-behind")
    assert "left-behind" not in run("ls /tmp")


def test_network_flag(monkeypatch):
    monkeypatch.setattr(config, "SANDBOX_NET", False)
    assert "--share-net" not in sandbox.argv("true")
    monkeypatch.setattr(config, "SANDBOX_NET", True)
    assert "--share-net" in sandbox.argv("true")


def test_workdir_is_bound_after_everything_else(monkeypatch, workdir):
    monkeypatch.setattr(config, "SANDBOX_RO_PATHS", [str(workdir.parent)])
    args = sandbox.argv("true")
    last_bind = max(i for i, a in enumerate(args) if a in ("--bind", "--ro-bind", "--ro-bind-try"))
    assert args[last_bind:last_bind + 3] == ["--bind", str(workdir), str(workdir)]


def test_required_but_unavailable_is_a_tool_error(monkeypatch, workdir):
    monkeypatch.setattr(config, "SANDBOX", "on")
    monkeypatch.setattr(sandbox, "probe", lambda: "no bwrap here")
    with pytest.raises(tools.ToolError, match="did NOT run"):
        tools.bash("touch should-not-exist")
    assert not (workdir / "should-not-exist").exists()


def test_off_runs_plain_bash(monkeypatch):
    monkeypatch.setattr(config, "SANDBOX", "off")
    assert not sandbox.active()
    assert run("echo $0").strip() == "/bin/bash"


def test_binary_output_does_not_crash():
    assert "exit code: 0" in tools.bash(r"printf '\x90\xff\n'")
