from helpers.api import ApiHandler, Request
from usr.plugins.a0_swarm.helpers.registry import SwarmRegistry
from usr.plugins.a0_swarm.helpers import delivery


class SwarmRetryMessage(ApiHandler):
    async def process(self, input: dict, request: Request):
        data = input or {}
        message_id = data.get("message_id", "")
        parent = str(data.get("parent_context_id") or "").strip()
        if not message_id or not parent:
            return {
                "ok": False,
                "error": "parent_context_id and message_id are required",
            }

        reg = SwarmRegistry.get()
        msg = reg.get_message(message_id)
        if not msg:
            return {"ok": False, "error": "Message not found in the active chat"}
        target_name = msg.recipient if msg.recipient != "orchestrator" else msg.sender
        target = reg.get_agent(target_name)
        if not target or target.parent_context_id != parent:
            return {"ok": False, "error": "Message not found in the active chat"}
        if msg.delivery_state == "delivered":
            return {"ok": True, "message_id": message_id, "delivery_state": "delivered", "error": ""}

        if msg.delivery_state in ("uncertain", "sending") and data.get("acknowledge_duplicate_risk") is not True:
            return {"ok": False, "error": "Delivery is uncertain; explicit duplication-risk acknowledgement is required"}
        if msg.delivery_state == "sending":
            return {"ok": False, "error": "Delivery is still in progress"}
        reg.mark_message_queued(message_id)
        result = await delivery.deliver_message(message_id)
        return {
            "ok": result.ok,
            "message_id": message_id,
            "delivery_state": result.state,
            "error": result.reason,
        }
