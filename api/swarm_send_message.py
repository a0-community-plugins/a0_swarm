from helpers.api import ApiHandler, Request
from usr.plugins.a0_swarm.helpers.registry import (
    SwarmRegistry, SwarmAgentStatus,
)
from usr.plugins.a0_swarm.helpers import delivery


class SwarmSendMessage(ApiHandler):
    async def process(self, input: dict, request: Request):
        data = input or {}
        agent_name = data.get("agent_name", "")
        content    = data.get("content", "")
        unblock    = bool(data.get("unblock", False))
        parent     = str(data.get("parent_context_id") or "").strip()

        if not agent_name or not content or not parent:
            return {
                "ok": False,
                "error": "parent_context_id, agent_name, and content are required",
            }

        reg = SwarmRegistry.get()
        entry = reg.get_agent(agent_name)
        if not entry or entry.parent_context_id != parent:
            return {"ok": False, "error": "Agent not found in the active chat"}

        try:
            msg = reg.create_message(entry.run_id, "orchestrator", agent_name, content)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        if unblock and entry.status == SwarmAgentStatus.BLOCKED:
            reg.update_status(agent_name, SwarmAgentStatus.WORKING, blocker="")

        result = await delivery.deliver_message(msg.message_id)
        return {
            "ok": result.ok,
            "message_id": msg.message_id,
            "delivery_state": result.state,
            "error": result.reason,
        }
