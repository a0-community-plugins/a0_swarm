from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from types import ModuleType

import support
from usr.plugins.a0_swarm.helpers import command_jobs, checkpoints
from usr.plugins.a0_swarm.helpers.execution import RecoveryHold
from usr.plugins.a0_swarm.helpers.durable_store import DurableStore
from usr.plugins.a0_swarm.helpers.durable_registry import DurableRegistry
from usr.plugins.a0_swarm.helpers.registry import SwarmAgentStatus as Status


class WarmRecoveryTests(unittest.TestCase):
    def test_unknown_tool_error_exits_generic_retry_and_lost_receipt_holds(self):
        errors = ModuleType("helpers.errors")
        for name in ("InterventionException", "RepairableException", "HandledException"):
            setattr(errors, name, type(name, (Exception,), {}))
        with tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules, {"helpers.errors": errors}):
            reg = DurableRegistry(DurableStore(Path(temp) / "journal.sqlite"))
            reg.admit("parent", "request", [{"label": "worker", "task": "task", "endpoint": ""}])
            name = reg.snapshot()["agents"][0]["agent_name"]
            reg.transition(name, 1, Status.WORKING, phase="local_started")
            for stage, error in (("dispatch", errors.RepairableException()), ("durable_tool", RecoveryHold("Lost outcome"))):
                reg.save_checkpoint(name, 1, {"stage": stage})
                with patch.object(checkpoints, "owned", lambda agent: (reg, reg.get_agent(name))):
                    with self.assertRaises(checkpoints.WorkerInterrupted):
                        checkpoints.control_exception(object(), {"args": (None, "message_loop", error)})
                reg.recover_after_error(name, 1)
                self.assertEqual(reg.get_agent(name).status, Status.REVIEW)
            self.assertEqual(reg.get_agent(name).checkpoint["stage"], "uncertain")
            reg.store.close()

    def test_recovery_admission_is_bounded_and_unknown_tools_stay_held(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "journal.sqlite"
            reg = DurableRegistry(DurableStore(path))
            reg.admit("parent", "request", [{"label": "worker", "task": "task", "endpoint": ""}])
            row = reg.snapshot()["agents"][0]
            name = row["agent_name"]
            reg.transition(name, 1, Status.WORKING, phase="local_started")
            reg.save_checkpoint(name, 1, {"stage": "model"})
            for count in range(3):
                reg.store.close()
                reg = DurableRegistry(DurableStore(path))
                self.assertEqual(reg.get_agent(name).status, Status.RECOVERING)
                reg.begin_resume(name, 1)
                self.assertEqual(reg.get_agent(name).resume_count, count + 1)
            reg.store.close()
            reg = DurableRegistry(DurableStore(path))
            self.assertEqual(reg.get_agent(name).status, Status.REVIEW)
            reg.save_checkpoint(name, 1, {"stage": "dispatch"})
            reg.retry(name)
            self.assertEqual(reg.get_agent(name).checkpoint, {})
            with self.assertRaisesRegex(RuntimeError, "ownership"):
                reg.save_checkpoint(name, 1, {"stage": "model"})
            reg.transition(name, 2, Status.WORKING, phase="local_started")
            reg.save_checkpoint(name, 2, {"stage": "dispatch"})
            reg.store.close()
            reg = DurableRegistry(DurableStore(path))
            self.assertEqual(reg.get_agent(name).status, Status.REVIEW)
            reg.store.close()

    def test_detached_command_survives_caller_kill_and_receipt_is_reused(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(command_jobs, "directory", lambda job: Path(temp) / job):
            root = Path(temp)
            job = "a" * 32
            code = "from pathlib import Path; import time; p=Path('count'); p.write_text(p.read_text()+'x' if p.exists() else 'x'); print('completed once',flush=True); time.sleep(0.8)"
            receipt = command_jobs.prepare(job, runtime="python", code=code, cwd=temp, timeout=10)
            caller = subprocess.Popen([sys.executable, "-c",
                "import subprocess,sys,time; subprocess.Popen([sys.executable,sys.argv[1],sys.argv[2]],start_new_session=True); time.sleep(60)",
                str(Path(command_jobs.__file__).with_name("command_runner.py")), str(receipt)])
            try:
                deadline = time.monotonic() + 10
                while not (root / "count").exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue((root / "count").exists())
                caller.kill()
                caller.wait(timeout=3)
                result = asyncio.run(command_jobs.wait(job))
                self.assertEqual(result["state"], "completed")
                self.assertEqual(result["exit_code"], 0)
                again = asyncio.run(command_jobs.wait(job))
                self.assertEqual(again, result)
                self.assertEqual((root / "count").read_text(), "x")
            finally:
                if caller.poll() is None:
                    caller.kill()
                    caller.wait(timeout=3)

    def test_lost_runner_receipt_never_reexecutes_and_cancel_is_confirmed(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(command_jobs, "directory", lambda job: Path(temp) / job):
            job = "b" * 32
            root = command_jobs.prepare(job, runtime="python", code="open('should-not-exist','w').write('bad')", cwd=temp)
            (root / "started.json").write_text(json.dumps({"pid": -1}))
            with self.assertRaisesRegex(ValueError, "without a confirmed outcome"):
                asyncio.run(command_jobs.wait(job))
            self.assertFalse((Path(temp) / "should-not-exist").exists())
            other = "c" * 32
            command_jobs.prepare(other, runtime="python", code="import time; time.sleep(60)", cwd=temp)
            self.assertTrue(asyncio.run(command_jobs.cancel(other)))
            self.assertEqual(asyncio.run(command_jobs.wait(other))["state"], "cancelled")


if __name__ == "__main__":
    unittest.main()
