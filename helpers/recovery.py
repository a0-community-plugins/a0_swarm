"""Plugin startup restores ownership; the job loop owns bounded reconciliation."""
from .registry import SwarmRegistry
import sys


def restore():
    from agent import AgentContext
    from .task_specs import SWARM_WORKER_CONTEXT_KEY
    reg = SwarmRegistry.get()
    for row in reg.snapshot()["agents"]:
        if row["is_remote"]:
            continue
        ctx = AgentContext.get(row["context_id"])
        if ctx:
            ctx.set_data(SWARM_WORKER_CONTEXT_KEY, {"run_id": row["run_id"], "parent_context_id": row["parent_context_id"], "attempt": row["attempt"]}, recursive=False)
            ctx.agent0.agent_name = row["agent_name"]


def tick():
    if getattr(sys, "_a0_swarm_chats_restoring", False):
        return
    restore()
    from .execution import Coordinator
    Coordinator.get().tick()
