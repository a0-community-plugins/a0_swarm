from __future__ import annotations

import asyncio
import uuid

from helpers import plugins, projects
from helpers.tool import Tool, Response
from usr.plugins.a0_swarm.helpers.registry import SwarmRegistry
from usr.plugins.a0_swarm.helpers.execution import Coordinator, safe_endpoint
from usr.plugins.a0_swarm.helpers.remotes import resolve_endpoint
from usr.plugins.a0_swarm.helpers.task_specs import (
    normalize_tasks, effective_max_parallel, normalize_workspace_mode,
    orchestration_conflict, result_byte_cap,
)


class DelegateParallel(Tool):
    async def execute(self, tasks=None, request_id="", run_id="", **kwargs):
        reg = SwarmRegistry.get()
        parent = self.agent.context.id
        if run_id:
            run = reg.get_run(run_id)
            if run is None or run.parent_context_id != parent:
                return Response("Run not found in this chat.", break_loop=False)
        else:
            conflict = orchestration_conflict(self.agent.context)
            if conflict:
                return Response(f"delegate_parallel rejected: {conflict}", break_loop=False)
            cfg = plugins.get_plugin_config("a0_swarm", agent=self.agent) or {}
            try:
                specs = normalize_tasks(tasks, max_parallel=effective_max_parallel(cfg.get("max_parallel")))
                for index, spec in enumerate(specs):
                    spec.update(profile=spec["profile"] or cfg.get("default_profile") or self.agent.config.profile,
                                project=projects.get_context_project_name(self.agent.context),
                                workspace_mode=normalize_workspace_mode(cfg.get("subagent_workspace")),
                                result_cap=result_byte_cap(cfg.get("result_kb_cap")), slot=index)
                    if spec["endpoint"]:
                        safe_endpoint(spec["endpoint"])
                        remote = resolve_endpoint(spec["endpoint"], agent=self.agent)
                        if remote is None:
                            raise ValueError("Remote endpoint is not configured")
                        spec["remote_url"] = safe_endpoint(remote.base_url)
                # Explicit identity survives caller retries. The framework tool
                # log identifies a single invocation when none was supplied.
                identity = request_id or getattr(getattr(self, "log", None), "id", "") or str(uuid.uuid4())
                run = reg.admit(parent, str(identity), specs, self.agent.agent_name)
            except ValueError as exc:
                return Response(f"delegate_parallel rejected: {exc}", break_loop=False)
        coordinator = Coordinator.get()
        coordinator.tick()
        while True:
            run = reg.get_run(run.run_id)
            if run.result:
                return Response(message=reg.collect(run.run_id), break_loop=False)
            entries = [a for a in reg.snapshot(parent)["agents"] if a["run_id"] == run.run_id]
            if any(a["status"] in ("needs_review", "cancel_requested") for a in entries):
                return Response(message=f"Swarm {run.run_id} needs review in the Swarm panel. Completed results and workspaces are retained. Use delegate_parallel with run_id to collect later; do not resubmit these tasks.", break_loop=False)
            await asyncio.sleep(1)
            coordinator.tick()

    def get_log_object(self):
        return self.agent.context.log.log(type="tool", heading=f"icon://group_work {self.agent.agent_name}: Delegating Parallel Tasks", content="", kvps=self.args)
