"""Offline file-based request/reply transport; commands never contain executable code."""
from __future__ import annotations
import os
from pathlib import Path
import re
import time
import uuid
from scene import read_json, write_json


def dispatch(scene, command, folder, spec, persist=True):
    allowed = {"op", "patches", "expected_revision", "path", "dpi", "overwrite", "jpeg_quality", "tiff_compression", "transparent", "style", "include_color_scale"}
    if not isinstance(command, dict) or set(command) - allowed:
        raise ValueError("Unknown command fields")
    op = command.get("op")
    if op in {"patch", "undo", "redo", "style_apply"}:
        revision = command.get("expected_revision")
        if not isinstance(revision, int) or isinstance(revision, bool): raise ValueError("Mutation requires integer expected_revision from list")
        if revision != scene.revision: raise ValueError(f"Revision conflict: expected {revision}, current {scene.revision}")
    if op == "list": return {"objects": scene.list_objects(), "revision": scene.revision}
    if op == "audit": return scene.audit()
    if op == "style_get": return scene.style_template(command.get("include_color_scale", False))
    if op == "patch": scene.edit(command.get("patches"), command.get("expected_revision"))
    elif op == "undo": scene.undo()
    elif op == "redo": scene.redo()
    elif op == "style_apply": scene.apply_style_template(command.get("style"), command["expected_revision"], command.get("include_color_scale", False))
    elif op == "save": pass
    elif op == "export":
        path = Path(command["path"])
        if not path.is_absolute(): path = Path(folder) / "exports" / path
        # Agent exports are confined to this session; no arbitrary filesystem writes.
        path = path.resolve()
        if not path.is_relative_to(Path(folder).resolve()): raise ValueError("Agent export must be within the session folder")
        if not isinstance(command.get("overwrite", False), bool): raise ValueError("overwrite must be boolean")
        result = scene.export(path, command.get("dpi", 300), command.get("overwrite", False),
                              command.get("jpeg_quality", 95), command.get("tiff_compression", "tiff_lzw"), command.get("transparent", False))
        if persist: scene.save_session(folder, spec)
        return result
    elif op == "close":
        if persist: scene.save_session(folder, spec)
        return {"close": True, "revision": scene.revision}
    else: raise ValueError("Unknown operation; use list/audit/patch/undo/redo/save/export/close/style_get/style_apply")
    if persist: scene.save_session(folder, spec)
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
        for path in sorted(self.commands.glob("*.json"), key=lambda p: (p.stat().st_mtime_ns, p.name)):
            if not re.fullmatch(r"[0-9a-f]{32}\.json", path.name): continue
            answer = self.responses / path.name
            if answer.exists(): continue
            if path.stem in self.scene.processed_requests:
                receipt = self.scene.processed_requests[path.stem]
                write_json(answer, receipt)
                close |= bool(receipt.get("result", {}).get("close"))
                continue
            try:
                if path.stat().st_size > 1024 * 1024: raise ValueError("Command exceeds 1 MB")
                before = self.scene.snapshot()
                history = (list(self.scene.undo_stack), list(self.scene.redo_stack), list(self.scene.log), self.scene.revision)
                result = dispatch(self.scene, read_json(path), self.folder, self.spec, persist=False)
                receipt = {"ok": True, "result": result}
                self.scene.processed_requests[path.stem] = receipt
                # State and receipt share one atomic session.json replacement. A crash
                # before the response is written cannot replay a committed mutation.
                try: self.scene.save_session(self.folder, self.spec)
                except Exception:
                    self.scene.restore(before)
                    self.scene.undo_stack, self.scene.redo_stack, self.scene.log, self.scene.revision = history
                    self.scene.processed_requests.pop(path.stem, None)
                    raise
            except Exception as exc:
                write_json(answer, {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}, "revision": self.scene.revision})
                continue
            # A response-write error must not turn a committed success into a failure.
            # The persisted receipt will recreate the missing response on the next poll.
            write_json(answer, receipt)
            close |= bool(result.get("close"))
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
