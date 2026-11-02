"""Cross-platform process lock. The lock file persists; OS releases ownership on exit."""
import os
from pathlib import Path


class SessionLock:
    def __init__(self, folder):
        self.path = Path(folder) / "editor.lock"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        if os.fstat(self.stream.fileno()).st_size == 0:
            self.stream.write(b" "); self.stream.flush()
        try:
            if os.name == "nt":
                import msvcrt
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            raise RuntimeError("Session is already open; use send or choose another session") from exc

    def release(self):
        if self.stream.closed: return
        if os.name == "nt":
            import msvcrt
            self.stream.seek(0)
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        self.stream.close()

    def __enter__(self): return self
    def __exit__(self, *args): self.release()
