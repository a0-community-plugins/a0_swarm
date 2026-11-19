from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import support
from usr.plugins.a0_swarm.helpers.durable_store import DurableStore
from usr.plugins.a0_swarm.helpers.durable_registry import DurableRegistry
from usr.plugins.a0_swarm.helpers.registry import SwarmAgentStatus as Status
from usr.plugins.a0_swarm.helpers.execution import Coordinator
from usr.plugins.a0_swarm.helpers import a2a_runner


def spec(remote=False):
    return {"label": "Synthetic", "task": "Synthetic task", "profile": "", "project": None,
            "workspace_mode": "none", "result_cap": 1024, "slot": 0,
            "endpoint": "fixture" if remote else "", **({"remote_url": "http://fixture.test"} if remote else {})}


class DurabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "swarm.sqlite"
        self.reg = DurableRegistry(DurableStore(self.path))

    def tearDown(self):
        self.reg.store.close()
        self.temp.cleanup()

    def admit(self, remote=False):
        run = self.reg.admit("parent", "request", [spec(remote)])
        return run, self.reg.get_agent(self.reg.snapshot()["agents"][0]["agent_name"])

    def reopen(self):
        self.reg.store.close()
        self.reg = DurableRegistry(DurableStore(self.path))

    def test_admission_result_receipts_survive_reopen_and_hide(self):
        run, entry = self.admit()
        self.reg.transition(entry.agent_name, 1, Status.DONE, result="saved result")
        self.reg.clear_completed("parent")
        result = self.reg.get_run(run.run_id).result
        self.assertIn("saved result", result)
        self.reopen()
        again = self.reg.admit("parent", "request", [spec()])
        self.assertEqual(again.run_id, run.run_id)
        self.assertEqual(again.result, result)
        self.assertEqual(self.reg.snapshot("parent")["runs"], [])
        with self.assertRaisesRegex(ValueError, "different tasks"):
            self.reg.admit("parent", "request", [{**spec(), "task": "different"}])

    def test_exclusive_owner_stale_attempt_and_cancel_receipt(self):
        _, entry = self.admit()
        with self.assertRaisesRegex(RuntimeError, "Another Swarm"):
            DurableStore(self.path)
        self.assertFalse(self.reg.transition(entry.agent_name, 99, Status.DONE))
        self.reg.request_cancel(entry.agent_name)
        self.assertFalse(self.reg.transition(entry.agent_name, 1, Status.DONE, result="late result"))
        current = self.reg.get_agent(entry.agent_name)
        self.assertEqual(current.status, Status.CANCEL_REQUESTED)
        self.assertEqual(current.result, "late result")
        self.reg.store.db.execute("UPDATE meta SET generation=generation+1")
        self.reg.store.db.commit()
        with self.assertRaisesRegex(RuntimeError, "Stale"):
            self.reg.transition(entry.agent_name, 1, Status.CANCELLED)
        self.assertEqual(self.reg.get_agent(entry.agent_name).status, Status.CANCEL_REQUESTED)

    def test_send_claim_recovery_and_private_projection(self):
        run, entry = self.admit(True)
        msg = self.reg.create_message(run.run_id, "orchestrator", entry.agent_name, "synthetic message")
        self.assertTrue(self.reg.claim_message(msg.message_id))
        self.assertFalse(self.reg.claim_message(msg.message_id))
        self.reg.update_status(entry.agent_name, Status.WORKING, phase="remote_sending", remote_auth_token="DO-NOT-PERSIST")
        self.reopen()
        self.assertEqual(self.reg.get_message(msg.message_id).delivery_state, "uncertain")
        self.assertEqual(self.reg.get_agent(entry.agent_name).status, Status.REVIEW)
        self.assertNotIn("DO-NOT-PERSIST", repr(self.reg.store.load()))
        self.assertEqual(self.reg.snapshot("other")["agents"], [])
        self.reg.clear_completed("parent")
        self.assertEqual(len(self.reg.snapshot()["agents"]), 1)

    def test_remote_known_id_polls_without_submission_and_timeout_is_not_failure(self):
        _, entry = self.admit(True)
        self.reg.update_status(entry.agent_name, Status.WORKING, phase="remote_wait", remote_task_id="remote-1", deadline=time.time()+200)
        self.reopen()
        owner = Coordinator(self.reg)
        calls = []
        async def connect(_):
            async def close(): pass
            return SimpleNamespace(close=close)
        async def poll(conn, task_id, **kwargs):
            calls.append(task_id)
            return "timeout", ""
        with patch("usr.plugins.a0_swarm.helpers.execution.resolve_remote", return_value=object()), patch.object(a2a_runner, "open_connection", connect), patch.object(a2a_runner, "wait_for_result", poll), patch.object(a2a_runner, "submit_task") as submit:
            asyncio.run(owner.drive(self.reg.get_agent(entry.agent_name)))
            submit.assert_not_called()
        self.assertEqual(calls, ["remote-1"])
        current = self.reg.get_agent(entry.agent_name)
        self.assertEqual(current.status, Status.RECOVERING)
        self.assertEqual(current.retries, 1)

    def test_lost_remote_submit_response_is_never_retried(self):
        _, entry = self.admit(True)
        calls = []
        async def lost(*args):
            calls.append("accepted-by-remote")
            raise TimeoutError()
        with patch("usr.plugins.a0_swarm.helpers.execution.resolve_remote", return_value=object()), patch.object(a2a_runner, "submit_task", lost):
            asyncio.run(Coordinator(self.reg).drive(entry))
            self.reopen()
            asyncio.run(Coordinator(self.reg).drive(self.reg.get_agent(entry.agent_name)))
        self.assertEqual(calls, ["accepted-by-remote"])
        self.assertEqual(self.reg.get_agent(entry.agent_name).status, Status.REVIEW)

    def test_process_kill_at_committed_boundaries(self):
        self.reg.store.close()
        for phase, expected in (("queued", Status.PENDING), ("local_started", Status.REVIEW), ("completed", Status.DONE)):
            with self.subTest(phase=phase):
                path = Path(self.temp.name) / f"{phase}.sqlite"
                code = '''import sys, time
from usr.plugins.a0_swarm.helpers.durable_store import DurableStore
from usr.plugins.a0_swarm.helpers.durable_registry import DurableRegistry
from usr.plugins.a0_swarm.helpers.registry import SwarmAgentStatus as S
r=DurableRegistry(DurableStore(sys.argv[1]))
r.admit('parent','request',[{'label':'Synthetic','task':'synthetic','endpoint':''}])
a=r.snapshot()['agents'][0]
phase=sys.argv[2]
if phase != 'queued': r.transition(a['agent_name'],1,S.DONE if phase=='completed' else S.WORKING,phase=phase,result='saved' if phase=='completed' else '',workspace={'key':'retained-dirty-worktree'})
print('committed',flush=True)
time.sleep(60)
'''
                child = subprocess.Popen([sys.executable, "-c", code, str(path), phase], stdout=subprocess.PIPE, text=True)
                try:
                    self.assertEqual(child.stdout.readline().strip(), "committed")
                    child.kill()
                    child.wait(timeout=5)
                    reopened = DurableRegistry(DurableStore(path))
                    try:
                        entry = reopened.snapshot()["agents"][0]
                        self.assertEqual(entry["status"], expected)
                        if phase != "queued":
                            self.assertEqual(entry["workspace"]["key"], "retained-dirty-worktree")
                        if phase == "completed":
                            self.assertEqual(entry["result"], "saved")
                    finally:
                        reopened.store.close()
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.wait(timeout=5)
                    child.stdout.close()


if __name__ == "__main__":
    unittest.main()
