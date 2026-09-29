"""Conversations on disk, so a session survives the REPL exiting.

One JSON file per session in SESSION_DIR, outside WORKDIR (so the agent's own tools
cannot read or rewrite it), rewritten atomically after every message: a crash mid-turn
loses nothing, and a file is never half-written. Files are 0600; history holds whatever
the agent read.
"""
import json
import os
from datetime import datetime
from pathlib import Path

from . import config, context


class SessionError(Exception):
    pass


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Session:
    def __init__(self, id: str = None, created: str = None):
        self.id = id or new_id()
        self.created = created or now()

    @property
    def path(self) -> Path:
        return config.SESSION_DIR / f"{self.id}.json"

    def save(self, messages):
        # No file until there is something to resume: launching and quitting leaves no trace.
        if not self.path.exists() and not any(m["role"] == "user" for m in messages):
            return
        data = {
            "id": self.id,
            "workdir": str(config.WORKDIR),
            "model": config.MODEL,
            "created": self.created,
            "updated": now(),
            "messages": messages,
        }
        config.SESSION_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, self.path)


def new_id() -> str:
    """Readable and sortable, since people type it: 20260929-154012, -2 on a collision."""
    base = datetime.now().strftime("%Y%m%d-%H%M%S")
    id, n = base, 1
    while (config.SESSION_DIR / f"{id}.json").exists():
        n += 1
        id = f"{base}-{n}"
    return id


def read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise SessionError(f"cannot read session {path.name}: {e}") from e


def load(id: str):
    """Return (Session, messages) for a session started in the current WORKDIR."""
    path = config.SESSION_DIR / f"{id}.json"
    if not path.exists():
        raise SessionError(f"no session {id!r} in {config.SESSION_DIR}")
    data = read(path)
    if data.get("workdir") != str(config.WORKDIR):
        # Tool paths in the history are relative to where it ran; replaying them elsewhere
        # would point the model at the wrong files.
        raise SessionError(
            f"session {id} was started in {data.get('workdir')}, not {config.WORKDIR}; "
            "cd there to resume it"
        )
    return Session(data["id"], data.get("created")), data["messages"]


def list_sessions():
    """Sessions for the current WORKDIR, newest first, as dicts with id, updated,
    messages (count) and title (the first request). Unreadable files are skipped."""
    if not config.SESSION_DIR.is_dir():
        return []
    found = []
    for path in config.SESSION_DIR.glob("*.json"):
        try:
            data = read(path)
        except SessionError:
            continue
        if data.get("workdir") != str(config.WORKDIR):
            continue
        messages = data.get("messages", [])
        first = next((m["content"] for m in messages
                      if m["role"] == "user" and not context.is_summary(m)), "")
        found.append({
            "id": data["id"],
            "updated": data.get("updated", ""),
            "messages": len(messages),
            "title": " ".join(first.split())[:70],
        })
    return sorted(found, key=lambda s: (s["updated"], s["id"]), reverse=True)


def latest():
    sessions = list_sessions()
    return sessions[0]["id"] if sessions else None
