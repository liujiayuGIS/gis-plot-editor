"""Offline file-based request/reply transport; commands never contain executable code."""
from __future__ import annotations
import os
from pathlib import Path
import re
import time
import uuid
from scene import read_json, write_json


def dispatch(scene, command, folder, spec):
    allowed = {"op", "patches", "expected_revision", "path", "dpi", "overwrite"}
    if not isinstance(command, dict) or set(command) - allowed:
        raise ValueError("Unknown command fields")
    op = command.get("op")
    if op == "list": return {"objects": scene.list_objects(), "revision": scene.revision}
    if op == "audit": return scene.audit()
    if op == "patch": scene.edit(command.get("patches"), command.get("expected_revision"))
    elif op == "undo": scene.undo()
    elif op == "redo": scene.redo()
    elif op == "save": pass
    elif op == "export":
        path = Path(command["path"])
        if not path.is_absolute(): path = Path(folder) / "exports" / path
        # Agent exports are confined to this session; no arbitrary filesystem writes.
        path = path.resolve()
        if not path.is_relative_to(Path(folder).resolve()): raise ValueError("Agent export must be within the session folder")
        if not isinstance(command.get("overwrite", False), bool): raise ValueError("overwrite must be boolean")
        result = scene.export(path, command.get("dpi", 300), command.get("overwrite", False))
        scene.save_session(folder, spec)
        return result
    elif op == "close":
        scene.save_session(folder, spec)
        return {"close": True, "revision": scene.revision}
    else: raise ValueError("op must be list/audit/patch/undo/redo/save/export/close")
    scene.save_session(folder, spec)
    return {"revision": scene.revision, "audit": scene.audit()}


class Mailbox:
    def __init__(self, folder, scene, spec):
        self.folder, self.scene, self.spec = Path(folder), scene, spec
        self.commands = self.folder / "commands"
        self.responses = self.folder / "responses"
        self.commands.mkdir(parents=True, exist_ok=True)
        self.responses.mkdir(parents=True, exist_ok=True)
        self.heartbeat()

    def heartbeat(self):
        write_json(self.folder / "runtime.json", {"pid": os.getpid(), "heartbeat": time.time(), "state": "running", "revision": self.scene.revision})

    def process(self):
        close = False
        # Completed files stay as provenance; a response means never execute again.
        for path in sorted(self.commands.glob("*.json"), key=lambda p: p.name):
            if not re.fullmatch(r"[0-9a-f]{32}\.json", path.name): continue
            answer = self.responses / path.name
            if answer.exists(): continue
            try:
                if path.stat().st_size > 1024 * 1024: raise ValueError("Command exceeds 1 MB")
                result = dispatch(self.scene, read_json(path), self.folder, self.spec)
                write_json(answer, {"ok": True, "result": result})
                close |= bool(result.get("close"))
            except Exception as exc:
                write_json(answer, {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}, "revision": self.scene.revision})
        self.heartbeat()
        return close

    def finish(self):
        write_json(self.folder / "runtime.json", {"pid": os.getpid(), "heartbeat": time.time(), "state": "closed", "revision": self.scene.revision})


def send(folder, command, timeout=15):
    folder = Path(folder)
    status_path = folder / "runtime.json"
    if not status_path.exists(): raise RuntimeError("Editor not running; open the session before sending commands")
    status = read_json(status_path)
    if status["state"] != "running" or time.time() - status["heartbeat"] > 10:
        raise RuntimeError("Editor heartbeat is stale/closed; reopen the saved session")
    request_id = uuid.uuid4().hex
    write_json(folder / "commands" / (request_id + ".json"), command)
    response_path = folder / "responses" / (request_id + ".json")
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if response_path.exists(): return read_json(response_path)
        time.sleep(.1)
    return {"ok": False, "error": {"type": "Timeout", "message": "Queued command may still execute. Inspect response_path before retrying."}, "response_path": str(response_path)}
