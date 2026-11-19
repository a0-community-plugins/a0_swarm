from helpers.api import ApiHandler, Request
from usr.plugins.a0_swarm.helpers.registry import SwarmRegistry


class SwarmStatus(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict:
        parent = str((input or {}).get("parent_context_id") or "").strip()
        if not parent:
            return {"parent_context_id": "", "runs": [], "agents": []}
        snap = SwarmRegistry.get().snapshot(parent_ctx_id=parent)
        return {
            "parent_context_id": parent,
            "revision": snap.get("revision", 0),
            "generation": snap.get("generation", 0),
            "runs": snap["runs"],
            "agents": snap["agents"],
        }
