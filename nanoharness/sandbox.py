"""Run `bash` inside bubblewrap, so a command can only write to WORKDIR.

Allow-list, not deny-list: the sandbox starts as an empty root and gets only

    /usr /etc /opt (+ the /bin /lib /lib64 /sbin links)   read-only
    WORKDIR                                               read-write
    /tmp, $HOME                                           empty, gone after the command
    /dev                                                  bwrap's minimal one: no disks

so /home (keys, tokens, other projects), /run (docker.sock, keyrings, dbus), /media,
/mnt, /var and every other mount simply do not exist inside. That matters here: a user
in the docker or disk group is root-equivalent through /run/docker.sock or /dev/sd*.
The environment is cleared down to a few harmless variables so tokens do not leak in.

What it does NOT stop: writes inside WORKDIR (the permission prompt is still the gate
for those) and network access, unless NANO_SANDBOX_NET=0.
"""
import functools
import os
import shutil
import subprocess
from pathlib import Path

from . import config

READ_ONLY = ["/usr", "/etc", "/opt"]
MERGED_USR_LINKS = ["/bin", "/lib", "/lib64", "/sbin"]
KEEP_ENV = ["PATH", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "TERM", "TZ", "USER", "LOGNAME"]


class SandboxUnavailable(Exception):
    pass


@functools.lru_cache(maxsize=None)
def probe():
    """None if the sandbox works here, else the reason it does not. Runs `true` through
    the exact argv bash uses, so a probe cannot pass while the real thing is broken.
    (A cut-down probe once failed on a missing /lib64 loader link, and auto mode then
    silently ran bash unsandboxed.) Cached: it cannot change while we run."""
    if shutil.which("bwrap") is None:
        return "bwrap is not installed (apt install bubblewrap)"
    try:
        proc = subprocess.run(argv("true"), capture_output=True, text=True,
                              errors="replace", timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"bwrap failed to start: {e}"
    if proc.returncode != 0:
        return f"bwrap does not work here: {proc.stderr.strip()[:200]}"
    return None


def active() -> bool:
    """Should bash run sandboxed? Raises if the sandbox is required but unusable."""
    mode = config.SANDBOX
    if mode == "off":
        return False
    reason = probe()
    if reason is None:
        return True
    if mode == "on":
        raise SandboxUnavailable(f"NANO_SANDBOX=on but {reason}")
    return False   # auto: run unsandboxed; the banner has already warned


def status() -> str:
    """One line for the banner."""
    if config.SANDBOX == "off":
        return "off (NANO_SANDBOX=off): bash runs with your full privileges"
    reason = probe()
    if reason:
        prefix = "UNAVAILABLE, bash will refuse to run" if config.SANDBOX == "on" else \
                 "UNAVAILABLE, bash runs with your full privileges"
        return f"{prefix}: {reason}"
    net = "network on" if config.SANDBOX_NET else "no network"
    extra = f", +{len(config.SANDBOX_RO_PATHS)} read-only paths" if config.SANDBOX_RO_PATHS else ""
    return f"bwrap: only the workdir is writable, {net}{extra}"


def argv(command: str) -> list:
    """The bwrap command line that runs `command` with bash inside the sandbox."""
    home = str(Path.home())
    workdir = str(config.WORKDIR)
    args = [shutil.which("bwrap")]

    for path in READ_ONLY:
        args += ["--ro-bind-try", path, path]
    for link in MERGED_USR_LINKS:
        if os.path.islink(link):          # merged /usr: recreate the link
            args += ["--symlink", os.readlink(link), link]
        elif os.path.isdir(link):
            args += ["--ro-bind", link, link]
    args += ["--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "--dir", home]

    for path in config.SANDBOX_RO_PATHS:
        args += ["--ro-bind-try", path, path]
    args += ["--bind", workdir, workdir]  # last, so nothing above can shadow it

    args += ["--unshare-all"]
    if config.SANDBOX_NET:
        args += ["--share-net"]
    args += ["--die-with-parent", "--new-session", "--clearenv"]
    for name in KEEP_ENV:
        if name in os.environ:
            args += ["--setenv", name, os.environ[name]]
    args += ["--setenv", "HOME", home, "--chdir", workdir, "--", "/bin/bash", "-c", command]
    return args
