"""The coding tasks are fair: their hidden tests pass with a reference solution and fail on the
starting files, through the same check the evals use. Real python, no model."""
from pathlib import Path

import pytest

from evals.checks import Run, command_output, hidden_tests
from evals.coding import TASKS
from nanoharness import config


def build(tmp_path, files):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return Run(tmp_path, [], "")


@pytest.mark.parametrize("name", list(TASKS))
def test_hidden_tests_fail_on_the_starting_files(name, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKDIR", tmp_path)
    files, _, hidden, _, _ = TASKS[name]
    result = hidden_tests(name, hidden)(build(tmp_path, files))
    assert not result["ok"], result
    assert not (tmp_path / f"hidden_test_{name}.py").exists()          # removed again


@pytest.mark.parametrize("name", list(TASKS))
def test_reference_solution_passes_every_check(name, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKDIR", tmp_path)
    files, _, hidden, extra, ref = TASKS[name]
    run = build(tmp_path, {**files, **ref})
    assert hidden_tests(name, hidden)(run)["ok"], hidden_tests(name, hidden)(run)
    for cmd, label in extra:
        assert command_output(cmd, name=label)(run)["ok"], label


@pytest.mark.parametrize("name", [n for n in TASKS if any(r.startswith("test_") for r in TASKS[n][0])])
def test_the_visible_tests_pass_with_the_reference(name, tmp_path, monkeypatch):
    """The model is told to run the tests: they must be passable."""
    monkeypatch.setattr(config, "WORKDIR", tmp_path)
    files, _, _, _, ref = TASKS[name]
    build(tmp_path, {**files, **ref})
    from nanoharness import tools
    out = tools.bash("python3 -m unittest -q 2>&1")
    assert out.startswith("exit code: 0"), out


def test_patching_only_the_symptom_fails_the_two_file_bug(tmp_path, monkeypatch):
    """Making orders.py divide by 100 passes the visible test but leaves loyalty.py broken."""
    monkeypatch.setattr(config, "WORKDIR", tmp_path)
    files, _, hidden, _, _ = TASKS["code_two_file_bug"]
    patched = files["orders.py"].replace("percent)", "percent / 100)")
    result = hidden_tests("code_two_file_bug", hidden)(build(tmp_path, {**files, "orders.py": patched}))
    assert not result["ok"] and "test_the_other_caller" in result["detail"]
