"""Long-running processes the harness owns: the server you asked for, kept up.

`bash` tears its sandbox down when a command returns, so a server started there is gone at
once (seen: "the local web server is now running!" with nothing listening). A service runs in
the same kind of sandbox but stays up until it is stopped or the harness exits, and the
harness checks it really started before saying so:

- a port that already answers is refused (seen: port 8000 held by Portainer, so "checking"
  it found someone else's server),
- then it waits for the port to answer, or reports the exit code and the log's last lines.
"""
import os
import signal
import socket
import subprocess
import time
from dataclasses import dataclass, field

from . import config, sandbox
from .tools import ToolError, tool, truncate

READY_SECONDS = 15        # how long a port gets to start answering
NO_PORT_SECONDS = 2       # without a port: alive this long counts as started
LOG_TAIL = 15             # log lines shown on failure and in service_status


@dataclass
class Service:
    name: str
    command: str
    port: int | None
    proc: subprocess.Popen
    log: str
    started: float = field(default_factory=time.monotonic)

    def alive(self):
        return self.proc.poll() is None


SERVICES: dict[str, Service] = {}


def log_dir():
    return config.SESSION_DIR.parent / "services"


def port_answers(port) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.5):
            return True
    except OSError:
        return False


def tail(path, lines=LOG_TAIL):
    try:
        text = open(path, errors="replace").read().rstrip()
    except OSError:
        return "(no log)"
    return "\n".join(text.splitlines()[-lines:]) or "(log empty)"


def owner(port):
    return next((s for s in SERVICES.values() if s.port == port and s.alive()), None)


def start(command, port=None, name=None) -> str:
    port = int(port) if port not in (None, "") else None
    sandboxed = sandbox.active()
    if sandboxed and not config.SANDBOX_NET:
        raise ToolError("The sandbox has no network (NANO_SANDBOX_NET=0), so a server here could "
                        "not be reached. Nothing was started. Give the user the command instead.")
    name = name or (f"port-{port}" if port else f"service-{len(SERVICES) + 1}")
    if name in SERVICES and SERVICES[name].alive():
        raise ToolError(f"A service named {name!r} is already running ({SERVICES[name].command}). "
                        "Stop it first, or use another name.")
    if port is not None and port_answers(port):
        mine = owner(port)
        raise ToolError(
            f"Port {port} is already in use by {'service ' + repr(mine.name) if mine else 'a program NanoHarness did not start'}"
            ". Nothing was started. Choose another port (or stop that service first).")
    log_dir().mkdir(parents=True, exist_ok=True)
    log = str(log_dir() / f"{name}.log")
    argv = sandbox.argv(command) if sandboxed else ["/bin/bash", "-c", command]
    with open(log, "w") as out:
        proc = subprocess.Popen(argv, cwd=config.WORKDIR, stdin=subprocess.DEVNULL, stdout=out,
                                stderr=subprocess.STDOUT, start_new_session=True)
    service = Service(name, command, port, proc, log)
    try:
        ready = wait_ready(service)
    except BaseException:          # Esc while waiting: don't leave it half started
        kill(service)
        raise
    if not service.alive():
        raise ToolError(f"It did NOT start: `{command}` exited with code {proc.returncode}. "
                        f"Last lines of its output:\n{tail(log)}")
    SERVICES[name] = service
    where = f", answering on http://localhost:{port}" if ready and port else ""
    if port and not ready:
        return (f"Started service {name!r} (pid {proc.pid}): `{command}`, still running, but port "
                f"{port} did not answer within {READY_SECONDS}s. Do not tell the user it works; "
                "check with service_status.")
    return (f"Started service {name!r} (pid {proc.pid}): `{command}`{where}. Checked: it is "
            "running. It keeps running until stop_service or until the user quits NanoHarness.")


def wait_ready(service) -> bool:
    deadline = time.monotonic() + (READY_SECONDS if service.port else NO_PORT_SECONDS)
    while time.monotonic() < deadline:
        if not service.alive():
            return False
        if service.port and port_answers(service.port):
            return True
        time.sleep(0.2)
    return service.port is None


def kill(service, grace=3.0):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(service.proc.pid, sig)
        except ProcessLookupError:
            break
        try:
            service.proc.wait(timeout=grace)
            break
        except subprocess.TimeoutExpired:
            continue
    # The sandbox's own processes die just after bwrap (measured: ~0.1 s); "stopped" must be true.
    deadline = time.monotonic() + grace
    while service.port and port_answers(service.port) and time.monotonic() < deadline:
        time.sleep(0.05)


def stop(name) -> str:
    service = SERVICES.pop(name, None)
    if service is None:
        raise ToolError(f"No service named {name!r}. Running: {', '.join(SERVICES) or 'none'}.")
    kill(service)
    return f"Stopped service {name!r}."


def stop_all():
    for name in list(SERVICES):
        kill(SERVICES.pop(name))


def running():
    """Services still alive (a crashed one is reported once by status, then dropped)."""
    return [s for s in SERVICES.values() if s.alive()]


def describe(service) -> str:
    up = int(time.monotonic() - service.started)
    state = "running" if service.alive() else f"EXITED with code {service.proc.returncode}"
    port = ""
    if service.port:
        port = f", port {service.port} " + ("answers" if port_answers(service.port) else "does NOT answer")
    return f"{service.name}: `{service.command}`, {state}{port}, up {up}s"


def status(name=None) -> str:
    if name:
        service = SERVICES.get(name)
        if service is None:
            raise ToolError(f"No service named {name!r}. Running: {', '.join(SERVICES) or 'none'}.")
        return f"{describe(service)}\nLast lines of its output:\n{tail(service.log)}"
    if not SERVICES:
        return "No services running."
    return "\n".join(describe(s) for s in SERVICES.values())


# --- the tools ---------------------------------------------------------------------------

@tool(
    name="start_service",
    description="Start a long-running command (a web server, a watcher) that keeps running after "
                "this call, unlike bash. It checks it really started: give the port it listens on "
                "and it waits until that port answers, or reports the error. Stopped when the user "
                "quits NanoHarness or with stop_service.",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command, e.g. python3 -m http.server 8069"},
            "port": {"type": "integer", "description": "The port it listens on, if any."},
            "name": {"type": "string", "description": "A short name for it (optional)."},
        },
        "required": ["command"],
    },
    writes=True,
    preview=lambda command, port=None, **kw: f"start service: {command}" + (f" (port {port})" if port else ""),
)
def start_service(command, port=None, name=None):
    return start(command, port, name)


@tool(
    name="stop_service",
    description="Stop a service started with start_service.",
    parameters={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    writes=False,
    preview=lambda name, **kw: f"stop service {name}",
)
def stop_service(name):
    return stop(name)


@tool(
    name="service_status",
    description="Services started with start_service: running or not, whether the port answers. "
                "With a name: also the last lines of its output (its log).",
    parameters={"type": "object", "properties": {"name": {"type": "string"}}},
    writes=False,
    preview=lambda name=None, **kw: "service status",
)
def service_status(name=None):
    return truncate(status(name))
