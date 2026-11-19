"""Detached, single-admission batch runner. Stdlib only; no framework imports."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

MAX_OUTPUT = 1024 * 1024


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(".next")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def run(root):
    os.umask(0o077)
    root = Path(root)
    with (root / "runner.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        if (root / "result.json").exists():
            return
        if (root / "started.json").exists():
            # A previous runner disappeared after dispatch intent. Its child may
            # have acted or may still be alive; never start a replacement.
            atomic_json(root / "result.json", {"state": "unknown", "message": "Command runner stopped without a confirmed outcome; inspect retained output."})
            return
        manifest = json.loads((root / "input.json").read_text())
        if (root / "cancel").exists():
            atomic_json(root / "result.json", {"state": "cancelled", "output": "", "exit_code": None})
            return
        atomic_json(root / "started.json", {"pid": os.getpid(), "started_at": time.time()})
        child = None
        try:
            child = subprocess.Popen(manifest["argv"], cwd=manifest["cwd"], stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
            atomic_json(root / "child.json", {"pid": child.pid})
            deadline = time.monotonic() + manifest["timeout"]
            captured = 0
            cancelled = False
            timed_out = False
            selector = selectors.DefaultSelector()
            selector.register(child.stdout, selectors.EVENT_READ)
            with (root / "output.txt").open("wb") as output:
                while selector.get_map():
                    if (root / "cancel").exists() or time.monotonic() >= deadline:
                        cancelled = (root / "cancel").exists()
                        timed_out = not cancelled
                        try:
                            os.killpg(child.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    for key, _ in selector.select(timeout=0.2):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        kept = chunk[:max(0, MAX_OUTPUT-captured)]
                        output.write(kept)
                        captured += len(kept)
                    if cancelled or timed_out:
                        # A daemon may keep a pipe open outside our process group.
                        child.wait(timeout=5)
                        break
                output.flush()
                os.fsync(output.fileno())
            selector.close()
            child.stdout.close()
            code = child.wait(timeout=5)
            # Batch execution must not leave ordinary background children behind.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            atomic_json(root / "result.json", {
                "state": "cancelled" if cancelled else "completed", "exit_code": code,
                "timed_out": timed_out, "truncated": captured >= MAX_OUTPUT,
                "output": (root / "output.txt").read_text(errors="replace"),
            })
        except BaseException:
            if child is not None:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            atomic_json(root / "result.json", {"state": "unknown", "message": "Command outcome could not be confirmed; inspect retained output."})
            raise


if __name__ == "__main__":
    run(sys.argv[1])
