from helpers.api import ApiHandler, Request
from usr.plugins.a0_swarm.helpers.registry import SwarmRegistry
from usr.plugins.a0_swarm.helpers.execution import Coordinator


class SwarmCancel(ApiHandler):
    async def process(self, input: dict, request: Request):
        data = input or {}
        parent = str(data.get("parent_context_id") or "")
        reg = SwarmRegistry.get()
        entry = reg.get_agent(data.get("agent_name", ""))
        if not parent or not entry or entry.parent_context_id != parent:
            return {"ok": False, "error": "Worker not found in the active chat"}
        reg.request_cancel(entry.agent_name)
        Coordinator.get().tick()
        return {"ok": True, "status": "cancel_requested"}
