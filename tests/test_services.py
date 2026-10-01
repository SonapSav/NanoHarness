"""Services: real processes (a tiny web server), no model."""
import socket

import pytest

from nanoharness import config, sandbox, services
from nanoharness.tools import ToolError


@pytest.fixture(autouse=True)
def workdir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKDIR", tmp_path)
    (tmp_path / "index.html").write_text("<h1>hi</h1>")
    yield tmp_path
    services.stop_all()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a_server_really_starts_answers_and_stops():
    port = free_port()
    out = services.start(f"python3 -m http.server {port}", port, "web")
    assert "answering on http://localhost:" in out and "Checked: it is running" in out
    assert services.port_answers(port)
    assert "port " + str(port) + " answers" in services.status() and services.running()
    assert "Stopped service 'web'" in services.stop("web")
    assert not services.port_answers(port) and not services.running()


def test_a_port_already_in_use_is_refused_and_nothing_starts():
    """Seen: port 8000 held by Portainer; 'checking' it found someone else's server."""
    port = free_port()
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", port))
        taken.listen()
        with pytest.raises(ToolError, match="already in use by a program NanoHarness did not start"):
            services.start(f"python3 -m http.server {port}", port)
    assert not services.SERVICES


def test_one_of_ours_on_the_port_is_named():
    port = free_port()
    services.start(f"python3 -m http.server {port}", port, "first")
    with pytest.raises(ToolError, match="already in use by service 'first'"):
        services.start(f"python3 -m http.server {port}", port, "second")


def test_a_command_that_exits_reports_its_code_and_output():
    with pytest.raises(ToolError, match=r"(?s)did NOT start.*code 3.*boom"):
        services.start("echo boom; exit 3", None, "bad")
    assert "bad" not in services.SERVICES


def test_no_network_in_the_sandbox_refuses(monkeypatch):
    if not sandbox.active():
        pytest.skip("only matters under the sandbox")
    monkeypatch.setattr(config, "SANDBOX_NET", False)
    with pytest.raises(ToolError, match="no network"):
        services.start("python3 -m http.server 8999", 8999)


def test_stop_all_and_unknown_names():
    port = free_port()
    services.start(f"python3 -m http.server {port}", port)
    services.stop_all()
    assert not services.port_answers(port) and services.status() == "No services running."
    with pytest.raises(ToolError, match="No service named 'nope'"):
        services.stop("nope")
