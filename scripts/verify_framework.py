"""Synthetic kill/reopen check using real framework AgentContext/DeferredTask.

Run with the framework interpreter and PYTHONPATH including an isolated plugin
namespace plus framework source. No live server, providers, or user state.
"""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from usr.plugins.a0_swarm.helpers.durable_store import DurableStore
from usr.plugins.a0_swarm.helpers.durable_registry import DurableRegistry
from usr.plugins.a0_swarm.helpers.registry import SwarmRegistry


def child(root, cancel=False, delivery=False):
    from helpers import extension
    async def no_extensions(*args, **kwargs): pass
    extension.call_extensions_async = no_extensions
    extension.call_extensions_sync = lambda *args, **kwargs: None
    from agent import AgentContext, AgentConfig, Agent
    from helpers import files
    import initialize
    from usr.plugins.a0_swarm.helpers.execution import Coordinator
    files._base_dir = str(root)
    config = AgentConfig(mcp_servers="")
    initialize.initialize_agent = lambda: config
    parent = AgentContext(config=config, id="synthetic-parent")
    reg = DurableRegistry(DurableStore(root / "swarm.sqlite"))
    SwarmRegistry._instance = reg
    run = reg.admit(parent.id, "synthetic-request", [{"label":"Synthetic", "task":"No provider calls", "profile":"", "project":None, "workspace_mode":"none", "slot":0, "result_cap":1024, "endpoint":""}])
    async def synthetic_turn(self):
        (root / "uncommitted-work.txt").write_text("Synthetic work must survive")
        (root / "ready").write_text(self.context.id)
        await asyncio.sleep(120)
    Agent.hist_add_user_message = lambda *args, **kwargs: None  # no prompt assets in the isolated fixture
    Agent.monologue = synthetic_turn
    entry = reg.get_agent(reg.snapshot()["agents"][0]["agent_name"])
    async def run_fixture():
        work = asyncio.create_task(Coordinator(reg).local(entry))
        if cancel or delivery:
            while not (root / "ready").exists():
                if work.done():
                    await work
                    raise RuntimeError("worker did not start")
                await asyncio.sleep(0.05)
            if delivery:
                from usr.plugins.a0_swarm.helpers import delivery as messages
                live = reg.get_agent(entry.agent_name)
                ctx = AgentContext.get(live.context_id)
                original_task = ctx.task
                def never_start_turn(*args, **kwargs):
                    raise AssertionError("message delivery attempted to start a turn")
                ctx.communicate = never_start_turn
                msg = reg.create_message(run.run_id, "orchestrator", entry.agent_name, "synthetic handoff")
                outcome = await messages.deliver_message(msg.message_id)
                assert outcome.state == "uncertain" and outcome.ok
                assert "synthetic handoff" in ctx.agent0.intervention.message
                reply = messages.capture_pending_reply(ctx.agent0, "synthetic acknowledgement")
                assert reply and reg.get_message(msg.message_id).delivery_state == "delivered"
            reg.request_cancel(entry.agent_name)
        await asyncio.wait_for(work, timeout=15) if cancel or delivery else await work
        if delivery:
            stale = reg.create_message(run.run_id, "orchestrator", entry.agent_name, "arrived after stop")
            reg.claim_message(stale.message_id)
            outcome = await messages._deliver_local(reg, stale, live)
            assert outcome.state == "queued"
            assert ctx.task is original_task and not original_task.is_alive()
            print("PASS: live handoff and acknowledgement; stale handoff after termination did not start another turn")
        if cancel:
            assert reg.get_agent(entry.agent_name).status.value == "cancelled"
            assert (root / "usr/chats" / (root / "ready").read_text() / "chat.json").exists()
            print("PASS: actual local cancellation waited for worker termination and preserved chat")
    asyncio.run(run_fixture())


def main():
    with tempfile.TemporaryDirectory(prefix="swarm-framework-") as directory:
        root = Path(directory)
        child_process = subprocess.Popen([sys.executable, __file__, "--child", str(root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic()+40
            while not (root / "ready").exists() and time.monotonic() < deadline:
                if child_process.poll() is not None:
                    stdout, stderr = child_process.communicate()
                    raise RuntimeError(f"Framework fixture failed: {stdout}\n{stderr}")
                time.sleep(0.1)
            assert (root / "ready").exists(), "synthetic worker did not start"
            child_process.kill()
            child_process.communicate(timeout=5)
            reg = DurableRegistry(DurableStore(root / "swarm.sqlite"))
            try:
                entry = reg.snapshot()["agents"][0]
                assert entry["status"] == "needs_review", entry
                assert entry["context_id"] == (root / "ready").read_text()
                assert (root / "uncommitted-work.txt").read_text() == "Synthetic work must survive"
                saved = root / "usr" / "chats" / entry["context_id"] / "chat.json"
                assert saved.exists(), f"worker chat not retained: {saved}"
                print("PASS: actual AgentContext.run_task killed mid synthetic monologue; reopen held execution and retained chat/work")
            finally:
                reg.store.close()
            cancel_root = root / "cancel"
            cancel_root.mkdir()
            result = subprocess.run([sys.executable, __file__, "--cancel", str(cancel_root)], capture_output=True, text=True, timeout=40)
            assert result.returncode == 0, result.stdout + result.stderr
            print(result.stdout.strip())
        finally:
            if child_process.poll() is None:
                child_process.kill()
                child_process.communicate(timeout=5)


if __name__ == "__main__":
    if "--delivery" in sys.argv:
        with tempfile.TemporaryDirectory(prefix="swarm-delivery-") as directory:
            child(Path(directory), delivery=True)
    elif "--child" in sys.argv or "--cancel" in sys.argv:
        child(Path(sys.argv[2]), cancel="--cancel" in sys.argv)
    else:
        main()
