from helpers.tool import Tool, Response
from helpers import files, projects, settings
from usr.plugins.a0_swarm.helpers import checkpoints, command_jobs


class SwarmExecute(Tool):
    async def execute(self, runtime="terminal", code="", timeout=600, **kwargs):
        owner = checkpoints.owned(self.agent)
        if not owner or not getattr(self, "_swarm_job_id", ""):
            return Response("swarm_execute requires an owned local Swarm worker and a single tool call per model turn.", break_loop=False)
        reg, entry = owner
        checkpoint = reg.get_agent(entry.agent_name).checkpoint
        if checkpoint.get("stage") != "durable_tool":
            return Response("A portable checkpoint is unavailable; command was not dispatched.", break_loop=False)
        project = projects.get_context_project_name(self.agent.context)
        cwd = projects.get_project_folder(project) if project else settings.get_settings().get("workdir_path")
        # This runner executes on the framework host, not a remote execution
        # shell. Preserve native paths, translating /a0 only in development.
        cwd = files.fix_dev_path(cwd or files.get_abs_path("usr/workdir"))
        from pathlib import Path
        Path(cwd).mkdir(parents=True, exist_ok=True)
        checkpoint["cwd"] = str(Path(cwd).resolve())
        reg.save_checkpoint(entry.agent_name, entry.attempt, checkpoint)
        try:
            command_jobs.prepare(self._swarm_job_id, runtime=runtime, code=code, cwd=checkpoint["cwd"], timeout=timeout)
        except ValueError as exc:
            return Response(f"Command was not dispatched: {exc}", break_loop=False)
        self._swarm_defer_intervention = True
        result = await command_jobs.wait(self._swarm_job_id)
        return Response(command_jobs.format_result(result), break_loop=False)
