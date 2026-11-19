"""Private command receipts reconnect a worker without repeating its command."""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading

from .command_runner import atomic_json


def directory(job_id):
    from helpers.files import get_abs_path
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise ValueError("Invalid command receipt ID")
    return Path(get_abs_path("usr/plugin_data/a0_swarm/commands", job_id))


def prepare(job_id, *, runtime, code, cwd, timeout=600):
    if runtime not in ("terminal", "python", "nodejs") or not isinstance(code, str) or not code.strip() or len(code) > 100_000:
        raise ValueError("Use terminal, python or nodejs with a nonempty code string (up to 100000 characters)")
    timeout = max(1, min(1800, int(timeout)))
    root = directory(job_id)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    cwd = str(Path(cwd).resolve(strict=True))
    python = "/opt/venv/bin/python" if Path("/opt/venv/bin/python").exists() else sys.executable
    executable = {"terminal": shutil.which("bash"), "python": python, "nodejs": shutil.which("node")}[runtime]
    if not executable:
        raise ValueError("Requested command runtime is unavailable")
    digest = hashlib.sha256(json.dumps([runtime, code, cwd, timeout]).encode()).hexdigest()
    # Serialize admission, including a second coordinator trying to reconnect.
    with (root / "admission.lock").open("a+b") as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest_path = root / "input.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            if manifest["digest"] != digest:
                raise ValueError("Command receipt already belongs to different input")
            return root
        script = root / {"terminal": "code.sh", "python": "code.py", "nodejs": "code.js"}[runtime]
        if script.exists():
            if script.read_text() != code:
                raise ValueError("Interrupted command admission has different code")
        else:
            fd = os.open(script, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(code)
                handle.flush()
                os.fsync(handle.fileno())
        atomic_json(manifest_path, {"digest": digest, "argv": [executable, str(script)], "cwd": cwd, "timeout": timeout})
    return root


def launch(root):
    # launch is safe to repeat: the runner lock and committed started marker
    # prevent duplicate dispatch, even when a spawn acknowledgement is lost.
    if (root / "result.json").exists():
        return
    with (root / "runner.lock").open("a+b") as lock:
        os.chmod(lock.name, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
    runner = Path(__file__).with_name("command_runner.py")
    process = subprocess.Popen([sys.executable, str(runner), str(root)], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True, close_fds=True)
    # Reap while this process is alive; the detached runner survives our exit.
    threading.Thread(target=process.wait, name="SwarmCommandReaper", daemon=True).start()


async def wait(job_id):
    from .execution import RecoveryHold
    root = directory(job_id)
    if not (root / "input.json").exists():
        raise RecoveryHold("Command admission receipt is unavailable")
    launch(root)
    for check in range(9100):  # bounded beyond the maximum command lifetime
        result = root / "result.json"
        if result.exists():
            data = json.loads(result.read_text())
            if data["state"] == "unknown":
                raise RecoveryHold(data["message"])
            return data
        if check and check % 25 == 0:
            with (root / "runner.lock").open("a+b") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    fcntl.flock(lock, fcntl.LOCK_UN)
                    launch(root)
        await asyncio.sleep(0.2)
    raise RecoveryHold("Command monitoring limit reached; inspect the original receipt")


async def cancel(job_id):
    root = directory(job_id)
    if not (root / "input.json").exists():
        # Dispatch is impossible until the immutable admission file exists.
        return True
    fd = os.open(root / "cancel", os.O_WRONLY | os.O_CREAT, 0o600)
    os.close(fd)
    try:
        result = await asyncio.wait_for(wait(job_id), timeout=8)
        return result["state"] in ("cancelled", "completed")
    except (TimeoutError, ValueError):
        return False


def format_result(result):
    text = result.get("output", "")
    text = text.encode()[:65536].decode(errors="ignore")
    suffix = "\n[Output truncated]" if result.get("truncated") or len(result.get("output", "").encode()) > 65536 else ""
    return f"Command {result['state']}; exit code {result.get('exit_code')}; timed out: {bool(result.get('timed_out'))}\n{text}{suffix}"
