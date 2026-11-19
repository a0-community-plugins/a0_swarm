"""One real-framework run, two synchronized kills, no provider calls or live data.

Use the framework interpreter with --framework /a0. Extension discovery, model
turn processing, tool dispatch, TASK persistence and command execution are real.
Only the model response and system-prompt content are deterministic substitutes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


async def child(root):
    sys.path.insert(0, str(root))
    os.chdir(root)
    from helpers import files
    files._base_dir = str(root)
    from agent import Agent, AgentConfig, AgentContext
    from helpers import persist_chat
    from helpers.llm_result import LLMResult, ResponseItem
    from usr.plugins.a0_swarm.helpers.registry import SwarmRegistry
    from usr.plugins.a0_swarm.helpers.execution import Coordinator
    import initialize

    class Model:
        kwargs = {"a0_api_mode": "responses"}
        model_name = "fixture/warm"
        def __init__(self, agent): self.agent = agent
        async def unified_turn(self, **kwargs):
            with (root / "model_calls").open("a") as handle: handle.write("call\n")
            received = any(isinstance(m.content, dict) and m.content.get("tool_name") == "swarm_execute"
                           and "tool_result" in m.content for m in self.agent.history.all_messages())
            if received:
                tool = {"tool_name": "response", "tool_args": {"text": "WARM_RECOVERED"}}
            else:
                code = "from pathlib import Path; import time; p=Path('effect'); p.write_text(p.read_text()+'x' if p.exists() else 'x'); time.sleep(4); print('COMMAND_RECEIPT')"
                tool = {"tool_name": "swarm_execute", "tool_args": {"runtime": "python", "code": code, "timeout": 20}}
            return LLMResult(mode="responses", state="local", output_items=[ResponseItem.from_any({
                "type": "function_call", "name": tool["tool_name"], "arguments": json.dumps(tool["tool_args"]),
                "call_id": "call_final" if received else "call_command", "id": "fixture_function",
            })])

    Agent.get_chat_model = lambda self: Model(self)
    async def prompt(self, loop_data): return ["Synthetic recovery fixture; no external provider."]
    Agent.get_system_prompt = prompt
    reg = SwarmRegistry.get()
    if not reg.snapshot()["agents"]:
        parent = AgentContext(config=AgentConfig(mcp_servers="", profile="default"), id="warm-parent")
        persist_chat.save_tmp_chat(parent)
        reg.admit(parent.id, "warm-request", [{"label": "Warm worker", "task": "Run a finite command then respond.",
                  "profile": "default", "project": None, "workspace_mode": "none", "slot": 0,
                  "result_cap": 4096, "endpoint": ""}])
    else:
        task = initialize.initialize_chats()  # actual synchronous startup hook
        await task.result()
    owner = Coordinator.get()
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        owner.tick()
        row = reg.snapshot()["agents"][0]
        if row["status"] == "done":
            entry = reg.get_agent(row["agent_name"])
            assert row["result"] == "WARM_RECOVERED", row
            assert entry.resume_count == 2, entry.resume_count
            assert len((root / "model_calls").read_text().splitlines()) == 2
            assert (root / "work/effect").read_text() == "x"
            assert "COMMAND_RECEIPT" in entry.checkpoint["context"]["agents"][0]["history"]
            (root / "verified.json").write_text(json.dumps({"context_id": row["context_id"], "attempt": row["attempt"],
                                                          "resume_count": entry.resume_count, "model_calls": 2, "effects": 1}))
            print("PASS: cached model result reused; original command reconnected; same worker completed after two process kills.")
            return
        if row["status"] == "needs_review":
            raise AssertionError(row["blocker"])
        await asyncio.sleep(0.1)
    raise TimeoutError("Fixture failed to complete")


def main(framework):
    candidate = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="swarm-warm-framework-") as temp:
        root = Path(temp)
        for name in ("agent.py", "models.py", "initialize.py", "helpers", "extensions", "agents", "prompts", "conf", "tools", "plugins", "skills", "knowledge"):
            (root / name).symlink_to(framework / name)
        (root / "usr/plugins").mkdir(parents=True)
        (root / "usr/__init__.py").write_text("")
        (root / "work").mkdir()
        (root / "usr/settings.json").write_text(json.dumps({"agent_profile": "default", "workdir_path": str(root / "work"), "mcp_servers": ""}))
        for plugin in (framework / "plugins").iterdir():
            if plugin.is_dir():
                toggle = root / "usr/plugins" / plugin.name
                toggle.mkdir()
                (toggle / ".toggle-0").touch()
        installed = root / "usr/plugins/a0_swarm"
        shutil.copytree(candidate, installed, ignore=shutil.ignore_patterns(".git", "__pycache__", "config.json", ".toggle-*"))
        # A synchronization hook exercises the real native extension dispatcher.
        hook = installed / "extensions/python/message_loop_result/_01_fixture_crash.py"
        hook.write_text('''from pathlib import Path
import asyncio
from helpers.extension import Extension
from helpers.files import get_abs_path
class FixtureCrash(Extension):
    async def execute(self, **kwargs):
        root=Path(get_abs_path(''))
        if not (root/'continue').exists():
            (root/'model_receipt_ready').touch()
            await asyncio.sleep(120)
''')
        log = (root / "fixture.log").open("w+")
        process = None
        try:
            for boundary in ("model_receipt_ready", "work/effect", "verified.json"):
                process = subprocess.Popen([sys.executable, str(installed / "scripts/verify_warm_recovery.py"), "--child", str(root)],
                                           stdout=log, stderr=log, env={**os.environ, "PYTHONPATH": str(root)})
                deadline = time.monotonic() + 50
                while not (root / boundary).exists() and time.monotonic() < deadline and process.poll() is None:
                    time.sleep(0.05)
                if not (root / boundary).exists():
                    log.flush(); log.seek(0)
                    raise RuntimeError(log.read()[-7000:])
                if boundary != "verified.json":
                    process.kill(); process.wait(timeout=5)
                    (root / "continue").touch()
                else:
                    process.wait(timeout=10)
                    if process.returncode:
                        log.seek(0); raise RuntimeError(log.read()[-7000:])
                    print((root / boundary).read_text())
        finally:
            if process and process.poll() is None:
                process.kill(); process.wait(timeout=5)
            log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--framework", type=Path, default=Path("/a0"))
    parser.add_argument("--child", type=Path)
    args = parser.parse_args()
    asyncio.run(child(args.child)) if args.child else main(args.framework)
