from agent import AgentContext
from helpers.api import ApiHandler, Request
from usr.plugins.a0_swarm.helpers.registry import (
    SwarmRegistry, SwarmAgentStatus,
)
from usr.plugins.a0_swarm.helpers import a2a_runner
from usr.plugins.a0_swarm.helpers.remotes import RemoteEndpoint


class SwarmCancel(ApiHandler):
    async def process(self, input: dict, request: Request):
        data = input or {}
        agent_name = data.get("agent_name", "")
        parent = str(data.get("parent_context_id") or "").strip()
        if not agent_name or not parent:
            return {
                "ok": False,
                "error": "parent_context_id and agent_name are required",
            }

        reg = SwarmRegistry.get()
        entry = reg.get_agent(agent_name)
        if not entry or entry.parent_context_id != parent:
            return {"ok": False, "error": "Agent not found in the active chat"}

        # Order matters: terminal status FIRST so the subagent's later
        # DONE/FAILED write is absorbed by terminal-state in update_status.
        reg.update_status(agent_name, SwarmAgentStatus.CANCELLED, current_activity="")
        if entry.run_id:
            reg.add_event(entry.run_id, agent_name, "status", "Cancelled by orchestrator")

        if entry.is_remote:
            remote = RemoteEndpoint(
                label=entry.remote_label,
                base_url=entry.remote_base_url,
                auth_token=getattr(entry, "remote_auth_token", ""),
            )
            await a2a_runner.cancel_task(remote, entry.remote_task_id)
        else:
            ctx = AgentContext.get(entry.context_id)
            if ctx:
                ctx.kill_process()
        return {"ok": True}
