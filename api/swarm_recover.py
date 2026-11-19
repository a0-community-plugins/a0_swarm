import time
from helpers.api import ApiHandler, Request
from usr.plugins.a0_swarm.helpers.registry import SwarmRegistry, SwarmAgentStatus
from usr.plugins.a0_swarm.helpers.execution import Coordinator


class SwarmRecover(ApiHandler):
    async def process(self, input: dict, request: Request):
        data = input or {}
        parent = str(data.get("parent_context_id") or "")
        reg = SwarmRegistry.get()
        run = reg.get_run(str(data.get("run_id") or ""))
        if not parent or not run or run.parent_context_id != parent:
            return {"ok": False, "error": "Run not found in the active chat"}
        action = data.get("action")
        if action == "collect":
            try:
                return {"ok": True, "result": reg.collect(run.run_id)}
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
        entry = reg.get_agent(str(data.get("agent_name") or ""))
        if not entry or entry.run_id != run.run_id:
            return {"ok": False, "error": "Worker not found in this run"}
        try:
            if action == "retry" and data.get("acknowledge_duplicate_risk") is True:
                from agent import AgentContext
                ctx = AgentContext.get(entry.context_id) if not entry.is_remote else None
                if ctx and ctx.is_running():
                    raise ValueError("Worker is still running; cancel it before retrying")
                reg.retry(entry.agent_name)
            elif action == "check" and entry.remote_task_id and not entry.cancel_requested:
                reg.transition(entry.agent_name, entry.attempt, SwarmAgentStatus.RECOVERING, retries=0, next_wake=0, deadline=time.time()+1800)
            else:
                raise ValueError("Action is unavailable; retry requires explicit acknowledgement")
            Coordinator.get().tick()
            return {"ok": True}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
