"""Own workers independently of a parent wait and resume committed progress."""
from __future__ import annotations

import asyncio
import threading
import sys
import os
import time
from urllib.parse import urlsplit

from .registry import SwarmRegistry, SwarmAgentStatus as Status, TERMINAL


class RecoveryHold(ValueError):
    """A safe, caller-visible reason emitted only by this module."""


def safe_endpoint(url):
    parsed = urlsplit(url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RecoveryHold("Use a credential-free remote URL and the separate token setting")
    return url.rstrip("/")


def resolve_remote(entry):
    from agent import AgentContext
    from .remotes import resolve_endpoint
    parent = AgentContext.get(entry.parent_context_id)
    if parent is None:
        raise RecoveryHold("Parent chat is unavailable; recovery held")
    remote = resolve_endpoint(entry.spec["endpoint"], agent=parent.agent0)
    if remote is None or safe_endpoint(remote.base_url) != entry.spec["remote_url"]:
        raise RecoveryHold("Remote configuration changed or is missing; recovery held")
    return remote


class Coordinator:
    _instance = None
    _lock = vars(sys).setdefault("_a0_swarm_coordinator_lock", threading.RLock())

    def __init__(self, registry):
        self.registry = registry
        self.active = {}
        self.stopped = False

    @classmethod
    def get(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = getattr(sys, "_a0_swarm_coordinator", None) or cls(SwarmRegistry.get())
                sys._a0_swarm_coordinator = cls._instance
            return cls._instance

    def tick(self):
        from helpers.defer import EventLoopThread
        with self._lock:
            if self.stopped or self.registry.store.failed:
                return
            self.active = {k: v for k, v in self.active.items() if not v.done()}
            snap = self.registry.snapshot()
            for run in snap["runs"]:
                if not run["result"] and run["agents"] and all(a["status"] in ("done", "failed", "cancelled") for a in run["agents"]):
                    self.registry.finish_run(run["run_id"])
            for row in snap["agents"]:
                entry = self.registry.get_agent(row["agent_name"])
                if entry.agent_name in self.active or entry.status in TERMINAL or entry.next_wake > time.time():
                    continue
                if entry.status == Status.REVIEW:
                    continue
                future = EventLoopThread("SwarmCoordinator").run_coroutine(self.drive(entry))
                self.active[entry.agent_name] = future

    async def drive(self, entry):
        reg = self.registry
        try:
            if entry.cancel_requested:
                await self.cancel(entry)
            elif entry.is_remote:
                await self.remote(entry)
            elif entry.phase == "queued":
                await self.local(entry)
            else:
                await self.resume(entry)
        except asyncio.CancelledError:
            raise
        except RecoveryHold as exc:
            reg.transition(entry.agent_name, entry.attempt, Status.REVIEW, blocker=str(exc))
        except Exception as exc:
            # Transport exceptions can contain credential-bearing URLs or tokens.
            reg.transition(entry.agent_name, entry.attempt, Status.REVIEW,
                           blocker="Worker operation could not be confirmed. Check configuration and inspect its context before retrying.", last_error=type(exc).__name__)
        finally:
            reg.finish_run(entry.run_id)

    async def local(self, entry):
        from agent import AgentContext, AgentContextType
        from initialize import initialize_agent
        from helpers import persist_chat, projects, subagents
        from . import workspace
        from .task_specs import SWARM_WORKER_CONTEXT_KEY

        reg = self.registry
        if AgentContext.get(entry.parent_context_id) is None:
            raise RecoveryHold("Parent chat unavailable")
        project = entry.spec.get("project")
        if project and not os.path.isdir(projects.get_project_folder(project)):
            raise RecoveryHold("Project is unavailable")
        profile = entry.spec.get("profile")
        if profile:
            definition = subagents.get_agents_dict(project).get(profile)
            if definition is None or not definition.enabled:
                raise RecoveryHold("Profile is unavailable")
        config = initialize_agent()
        if entry.spec.get("profile"):
            config.profile = entry.spec["profile"]
        # Commit the setup boundary before creating resources. A partial setup
        # is held on restart, never blindly repeated or swept away.
        if not reg.transition(entry.agent_name, entry.attempt, Status.WORKING, phase="setup"):
            return
        # Core intentionally never saves BACKGROUND contexts. TASK contexts are
        # restored inertly, so the journal remains the execution authority.
        ctx = AgentContext(config=config, type=AgentContextType.TASK, name=f"Swarm: {entry.label}")
        ctx.set_data(SWARM_WORKER_CONTEXT_KEY, {"run_id": entry.run_id, "parent_context_id": entry.parent_context_id, "attempt": entry.attempt}, recursive=False)
        ctx.agent0.agent_name = entry.agent_name
        if not reg.transition(entry.agent_name, entry.attempt, Status.WORKING, context_id=ctx.id):
            return
        def ownership(value):
            if not reg.transition(entry.agent_name, entry.attempt, Status.WORKING, workspace=value):
                raise RuntimeError("Workspace ownership changed")
        workspace.setup(entry.spec["workspace_mode"], entry.spec.get("project"), ctx, entry.run_id, entry.spec["slot"], ownership=ownership)
        persist_chat.save_tmp_chat(ctx)
        if not reg.transition(entry.agent_name, entry.attempt, Status.WORKING, phase="local_started"):
            return

        await self.run_local(entry, ctx, initial=True)

    async def resume(self, entry):
        from agent import AgentContext
        from .checkpoints import SAFE_STAGES
        if entry.checkpoint.get("stage") not in SAFE_STAGES:
            raise RecoveryHold("No confirmed continuation point is available; inspect the interrupted action.")
        ctx = AgentContext.get(entry.context_id)
        if ctx is None or AgentContext.get(entry.parent_context_id) is None:
            raise RecoveryHold("Saved worker or parent chat is unavailable")
        if ctx.is_running():
            raise RecoveryHold("Worker context is already running outside this coordinator")
        self.registry.begin_resume(entry.agent_name, entry.attempt)
        await self.run_local(self.registry.get_agent(entry.agent_name), ctx, initial=False)

    async def run_local(self, entry, ctx, *, initial):
        from agent import LoopData, UserMessage
        from helpers import persist_chat
        from . import checkpoints, command_jobs
        reg = self.registry
        started = threading.Event()
        exited = threading.Event()

        async def run_owned():
            started.set()
            try:
                if reg.get_agent(entry.agent_name).cancel_requested:
                    checkpoint = reg.get_agent(entry.agent_name).checkpoint
                    confirmed = not checkpoint.get("job_id") or await command_jobs.cancel(checkpoint["job_id"])
                    reg.transition(entry.agent_name, entry.attempt, Status.CANCELLED if confirmed else Status.CANCEL_REQUESTED,
                                   phase="cancelled" if confirmed else "local_started")
                    return
                if initial:
                    ctx.agent0.hist_add_user_message(UserMessage(message=(
                        entry.task + "\n\n[Swarm execution: prefer swarm_execute for finite local batch commands. "
                        "It preserves command receipts across Agent Zero restarts. Use normal tools for interactive or remote sessions.]"
                    )))
                else:
                    checkpoint = entry.checkpoint
                    checkpoints.restore(ctx.agent0, checkpoint)
                    if checkpoint["stage"] == "durable_tool":
                        ctx.agent0.loop_data = LoopData(user_message=ctx.agent0.last_user_message)
                        for key, value in checkpoint.get("loop", {}).items():
                            setattr(ctx.agent0.loop_data, key, value)
                        args = checkpoint["tool_args"]
                        cwd = checkpoint.get("cwd")
                        if not cwd:
                            raise RecoveryHold("Command was interrupted before its workspace was recorded")
                        command_jobs.prepare(checkpoint["job_id"], runtime=args.get("runtime", "terminal"),
                                             code=args.get("code", ""), cwd=cwd, timeout=args.get("timeout", 600))
                        receipt = await command_jobs.wait(checkpoint["job_id"])
                        extra = {}
                        if checkpoint.get("call_id"):
                            from helpers.llm_result import function_call_output_item
                            extra["_responses_output_item"] = function_call_output_item(checkpoint["call_id"], command_jobs.format_result(receipt))
                        ctx.agent0.hist_add_tool_result("swarm_execute", command_jobs.format_result(receipt),
                                                       id=checkpoint.get("tool_log_id", ""), **extra)
                        await checkpoints.capture(ctx.agent0, "ready")
                reg.transition(entry.agent_name, entry.attempt, Status.WORKING, phase="local_started",
                               current_activity="Continuing saved progress" if not initial else "Thinking...", blocker="")
                result = entry.checkpoint.get("result", "") if not initial and entry.checkpoint.get("stage") == "final" else await ctx.agent0.monologue()
                text = str(result or "")
                workspace = reg.get_agent(entry.agent_name).workspace
                if workspace.get("mode") == "isolated":
                    text = f"[Workspace retained: {workspace.get('project_name')}; branch {workspace.get('branch')}. Inspect uncommitted changes before merging.]\n" + text
                cap = entry.spec["result_cap"]
                text = text.encode()[:cap].decode(errors="ignore")
                reg.transition(entry.agent_name, entry.attempt, Status.DONE, phase="completed", result=text, current_activity="")
            except asyncio.CancelledError:
                checkpoint = reg.get_agent(entry.agent_name).checkpoint
                confirmed = not checkpoint.get("job_id") or await command_jobs.cancel(checkpoint["job_id"])
                reg.transition(entry.agent_name, entry.attempt, Status.CANCELLED if confirmed else Status.CANCEL_REQUESTED,
                               phase="cancelled" if confirmed else "local_started",
                               blocker="Local worker stopped. External effects and workspace changes are retained." if confirmed else "Command cancellation is not confirmed; retained work requires inspection.")
                raise
            except RecoveryHold as exc:
                checkpoint = reg.get_agent(entry.agent_name).checkpoint
                reg.save_checkpoint(entry.agent_name, entry.attempt, {**checkpoint, "stage": "uncertain"})
                reg.transition(entry.agent_name, entry.attempt, Status.REVIEW, blocker=str(exc), current_activity="")
            except checkpoints.WorkerInterrupted:
                reg.recover_after_error(entry.agent_name, entry.attempt)
            except Exception:
                reg.recover_after_error(entry.agent_name, entry.attempt)
            finally:
                try:
                    persist_chat.save_tmp_chat(ctx)
                    reg.finish_run(entry.run_id)
                finally:
                    exited.set()
        task = ctx.run_task(run_owned)
        # Polling the owned task avoids coupling cancellation of this waiter to
        # the framework task. Its run_owned wrapper owns the terminal receipt.
        kill_sent = False
        while not exited.is_set():
            current = reg.get_agent(entry.agent_name)
            if current.cancel_requested and started.is_set() and not kill_sent:
                ctx.kill_process()
                kill_sent = True
            await asyncio.sleep(0.2)

    async def remote(self, entry):
        from . import a2a_runner
        reg = self.registry
        remote = resolve_remote(entry)
        conn = None
        try:
            if not entry.remote_task_id:
                if entry.phase != "queued":
                    reg.transition(entry.agent_name, entry.attempt, Status.REVIEW, blocker="Remote submission outcome unknown; do not resubmit automatically.")
                    return
                if not reg.transition(entry.agent_name, entry.attempt, Status.WORKING, phase="remote_sending", deadline=time.time()+1800):
                    return
                conn, task_id, context_id = await a2a_runner.submit_task(remote, entry.task)
                if not task_id:
                    raise RecoveryHold("Remote did not return a task ID")
                # Receipt must be retained even when cancellation raced submit.
                reg.record_remote(entry.agent_name, entry.attempt, task_id, context_id)
                entry = reg.get_agent(entry.agent_name)
            if entry.cancel_requested:
                await self.cancel(entry)
                return
            if time.time() >= entry.deadline or entry.retries >= 5:
                reg.transition(entry.agent_name, entry.attempt, Status.REVIEW, blocker="Remote monitoring limit reached; task may still be running. Check status or cancel.")
                return
            if conn is None:
                conn = await a2a_runner.open_connection(remote)
            state, result = await a2a_runner.wait_for_result(conn, entry.remote_task_id, max_wait_s=min(20, max(1, int(entry.deadline-time.time()))))
            terminal = {"completed": Status.DONE, "failed": Status.FAILED, "canceled": Status.CANCELLED, "cancelled": Status.CANCELLED}
            if state in terminal:
                reg.transition(entry.agent_name, entry.attempt, terminal[state], phase="completed",
                               result=str(result).encode()[:entry.spec["result_cap"]].decode(errors="ignore"), current_activity="")
            else:
                reg.transition(entry.agent_name, entry.attempt, Status.RECOVERING, phase="remote_wait", retries=entry.retries+1,
                               next_wake=time.time()+min(60, 2**entry.retries), blocker="Remote outcome not yet confirmed; reconnecting to the same task.")
        finally:
            if conn:
                await conn.close()

    async def cancel(self, entry):
        reg = self.registry
        if entry.retries >= 5:
            reg.transition(entry.agent_name, entry.attempt, Status.REVIEW,
                           blocker="Cancellation remains unconfirmed after five checks. Inspect the worker or request cancellation again.")
            return
        if entry.is_remote:
            from . import a2a_runner
            if entry.remote_task_id:
                state = await a2a_runner.cancel_task(resolve_remote(entry), entry.remote_task_id)
                if state:
                    reg.transition(entry.agent_name, entry.attempt, Status.CANCELLED, phase="cancelled")
                    return
            elif entry.phase == "queued":
                reg.transition(entry.agent_name, entry.attempt, Status.CANCELLED, phase="cancelled")
                return
        else:
            from agent import AgentContext
            ctx = AgentContext.get(entry.context_id)
            if entry.checkpoint.get("job_id"):
                from .command_jobs import cancel
                if await cancel(entry.checkpoint["job_id"]):
                    reg.transition(entry.agent_name, entry.attempt, Status.CANCELLED, phase="cancelled")
                    return
            if ctx and ctx.is_running():
                ctx.kill_process()
            elif entry.phase == "queued":
                reg.transition(entry.agent_name, entry.attempt, Status.CANCELLED, phase="cancelled")
                return
        reg.transition(entry.agent_name, entry.attempt, Status.CANCEL_REQUESTED,
                       retries=entry.retries+1, next_wake=time.time()+60, blocker="Cancellation is not confirmed. Retained work may have external effects.")

    def close(self):
        with self._lock:
            if any(not future.done() for future in self.active.values()):
                raise RuntimeError("Stop active Swarm workers before updating or disabling the plugin; recovery data is retained")
            self.stopped = True
            self.registry.store.close()
