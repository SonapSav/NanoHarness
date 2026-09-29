import pytest

from nanoharness import config


@pytest.fixture(autouse=True)
def isolated_sessions(tmp_path_factory, monkeypatch):
    """Never let a test write into the real ~/.local/share/nanoharness."""
    monkeypatch.setattr(config, "SESSION_DIR", tmp_path_factory.mktemp("sessions"))
