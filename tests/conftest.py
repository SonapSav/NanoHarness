import pytest

from nanoharness import config


@pytest.fixture(autouse=True)
def isolated_sessions(tmp_path_factory, monkeypatch):
    """Never let a test write into the real ~/.local/share/nanoharness."""
    monkeypatch.setattr(config, "SESSION_DIR", tmp_path_factory.mktemp("sessions"))


@pytest.fixture(autouse=True)
def no_review(monkeypatch):
    """The review is a model call; tests that want it turn it on and script its reply."""
    monkeypatch.setattr(config, "REVIEW", False)
