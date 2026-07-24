from __future__ import annotations
import asyncio

from agent import AgentContext, UserMessage
try:
    from agent import AgentContextType
except ImportError:
    class AgentContextType:
        BACKGROUND = "background"
from helpers import plugins, projects
from helpers.tool import Tool, Response
from initialize import initialize_agent
from usr.plugins.a0_swarm.helpers.registry import (
    SwarmRegistry, SwarmAgent, SwarmAgentStatus, MAX_RESULT_BYTES, utc_iso_now,
)
from usr.plugins.a0_swarm.helpers import a2a_runner, workspace
from usr.plugins.a0_swarm.helpers.remotes import RemoteEndpoint, resolve_endpoint
from usr.plugins.a0_swarm.helpers.task_specs import (
    SWARM_WORKER_CONTEXT_KEY,
    effective_max_parallel,
    normalize_tasks,
    normalize_workspace_mode,
    orchestration_conflict,
    result_byte_cap,
)


def _load_cfg(agent) -> dict:
    try:
        return plugins.get_plugin_config("a0_swarm", agent=agent) or {}
    except Exception:
        return {}


# Map A2A terminal state strings to our SwarmAgentStatus.
_A2A_TERMINAL_MAP = {
    "completed":  SwarmAgentStatus.DONE,
    "failed":     SwarmAgentStatus.FAILED,
    "canceled":   SwarmAgentStatus.CANCELLED,
    "cancelled":  SwarmAgentStatus.CANCELLED,
    "timeout":    SwarmAgentStatus.FAILED,
}


class DelegateParallel(Tool):
    async def execute(self, tasks: list | None = None, **kwargs):
        conflict = orchestration_conflict(self.agent.context)
        if conflict:
            return Response(
                message=(
                    f"delegate_parallel rejected: {conflict}. "
                    "Use one orchestration layer at a time."
                ),
                break_loop=False,
            )

        cfg = _load_cfg(self.agent)
        max_parallel = effective_max_parallel(cfg.get("max_parallel"))
        try:
            tasks = normalize_tasks(tasks, max_parallel=max_parallel)
        except ValueError as exc:
            return Response(
                message=f"delegate_parallel rejected: {exc}.",
                break_loop=False,
            )

        configured_profile = str(cfg.get("default_profile") or "").strip()
        parent_profile = str(getattr(self.agent.config, "profile", "") or "").strip()
        default_profile = configured_profile or parent_profile
        self._result_byte_cap = result_byte_cap(cfg.get("result_kb_cap"))

        # Where do local subs work? none (global workdir, default) | inherit (parent's project) |
        # isolated (own git worktree of the parent's repo). See helpers/workspace.py.
        workspace_mode = normalize_workspace_mode(cfg.get("subagent_workspace"))
        parent_project = projects.get_context_project_name(self.agent.context)
        if workspace_mode == "isolated":
            # reclaim isolated worktrees we own whose sub-context died before cleanup (crash-safe)
            try:
                workspace.sweep_orphans([c.id for c in AgentContext.all()])
            except Exception:
                pass

        registry = SwarmRegistry.get()
        parent_ctx_id = self.agent.context.id
        run = registry.create_run(
            parent_context_id=parent_ctx_id,
            parent_agent_name=getattr(self.agent, "agent_name", "orchestrator"),
            title=f"delegate_parallel: {len(tasks)} task(s)",
        )
        entries: list[SwarmAgent] = []
        coros = []

        for i, td in enumerate(tasks):
            label = td["label"]
            task_text = td["task"]
            profile = td["profile"] or default_profile
            endpoint = td["endpoint"]
            agent_name = (
                f"SA{self.agent.number + 1}_{i+1}-{run.run_id[-6:]}"
            )

            if endpoint:
                try:
                    remote = resolve_endpoint(endpoint, agent=self.agent)
                except Exception as exc:
                    remote = None
                    endpoint = f"{endpoint} ({str(exc)[:500]})"
                if remote is None:
                    entry = self._register_failed_entry(
                        registry=registry,
                        run_id=run.run_id,
                        parent_ctx_id=parent_ctx_id,
                        agent_name=agent_name,
                        label=label,
                        task_text=task_text,
                        blocker=f"Unknown remote endpoint: {endpoint}",
                        remote_label=endpoint,
                        delivery_mode="remote_a2a",
                    )
                    entries.append(entry)
                    coros.append(asyncio.sleep(0))
                    continue

                entry = SwarmAgent(
                    agent_name=agent_name, label=label, task=task_text,
                    context_id="", parent_context_id=parent_ctx_id,
                    status=SwarmAgentStatus.PENDING,
                    started_at=utc_iso_now(),
                    remote_label=remote.label,
                    remote_base_url=remote.base_url,
                    remote_auth_token=remote.auth_token,
                    run_id=run.run_id,
                    delivery_mode="remote_a2a",
                )
                registry.register(entry)
                entries.append(entry)
                coros.append(self._run_remote(remote, task_text, entry))
                continue

            # Local subagent path.
            sub_ctx = None
            ws = None
            try:
                config = initialize_agent()
                if profile:
                    config.profile = profile
                sub_ctx = AgentContext(
                    config=config,
                    type=AgentContextType.BACKGROUND,
                    name=f"Swarm: {label}",
                )
                sub_ctx.set_data(
                    SWARM_WORKER_CONTEXT_KEY,
                    {
                        "run_id": run.run_id,
                        "parent_context_id": parent_ctx_id,
                        "slot": i + 1,
                    },
                    recursive=False,
                )
                sub_agent = sub_ctx.agent0
                sub_agent.agent_name = agent_name

                # Give the sub a workspace before it runs; code execution binds
                # the working directory on first use.
                ws = workspace.setup(
                    workspace_mode,
                    parent_project,
                    sub_ctx,
                    run.run_id,
                    i,
                )
            except Exception as exc:
                self._cleanup_local_setup(sub_ctx, ws)
                entry = self._register_failed_entry(
                    registry=registry,
                    run_id=run.run_id,
                    parent_ctx_id=parent_ctx_id,
                    agent_name=agent_name,
                    label=label,
                    task_text=task_text,
                    blocker=f"Local subagent setup failed: {str(exc)[:1500]}",
                )
                entries.append(entry)
                coros.append(asyncio.sleep(0))
                continue

            entry = SwarmAgent(
                agent_name=agent_name, label=label, task=task_text,
                context_id=sub_ctx.id, parent_context_id=parent_ctx_id,
                status=SwarmAgentStatus.PENDING,
                started_at=utc_iso_now(),
                run_id=run.run_id,
                delivery_mode="local",
            )
            registry.register(entry)
            entries.append(entry)
            coros.append(self._run_subagent(sub_ctx, sub_agent, task_text, entry, ws))

        results = await asyncio.gather(*coros, return_exceptions=True)
        return Response(message=self._summarize(entries, results), break_loop=False)

    @staticmethod
    def _register_failed_entry(
        *,
        registry: SwarmRegistry,
        run_id: str,
        parent_ctx_id: str,
        agent_name: str,
        label: str,
        task_text: str,
        blocker: str,
        remote_label: str = "",
        delivery_mode: str = "local",
    ) -> SwarmAgent:
        timestamp = utc_iso_now()
        entry = SwarmAgent(
            agent_name=agent_name,
            label=label,
            task=task_text,
            context_id="",
            parent_context_id=parent_ctx_id,
            status=SwarmAgentStatus.FAILED,
            started_at=timestamp,
            finished_at=timestamp,
            blocker=blocker,
            remote_label=remote_label,
            run_id=run_id,
            delivery_mode=delivery_mode,
        )
        registry.register(entry)
        return entry

    @staticmethod
    def _cleanup_local_setup(sub_ctx, ws) -> None:
        try:
            if ws is not None:
                ws.teardown()
        except Exception:
            pass
        if sub_ctx is None:
            return
        try:
            AgentContext.remove(sub_ctx.id)
        except Exception:
            pass
        try:
            from helpers import persist_chat
            persist_chat.remove_chat(sub_ctx.id)
        except Exception:
            pass

    async def _run_subagent(self, sub_ctx, sub_agent, task_text, entry, ws=None):
        registry = SwarmRegistry.get()
        registry.update_status(entry.agent_name, SwarmAgentStatus.WORKING)
        try:
            sub_agent.hist_add_user_message(UserMessage(message=task_text))
            result = await sub_agent.monologue()
            out = str(result or "")
            # In isolated mode, tell the orchestrator which branch holds this sub's work, and make
            # clear it persists in the parent repo for merging (not an ephemeral throwaway dir).
            if ws is not None and getattr(ws, "mode", None) == "isolated" and getattr(ws, "branch", None):
                import os as _os
                _repo = _os.path.basename((getattr(ws, "repo_path", "") or "").rstrip("/")) or "the repo"
                out = (f"[workspace: committed to branch `{ws.branch}` in repo `{_repo}` — the worktree "
                       f"checkout is temporary but this branch persists in the repo; merge it to keep "
                       f"this work (it is NOT ephemeral).]\n") + out
            stored = self._cap_result(out)
            registry.update_status(
                entry.agent_name, SwarmAgentStatus.DONE,
                result=stored, current_activity="",
            )
            return out
        except Exception as e:
            registry.update_status(
                entry.agent_name, SwarmAgentStatus.FAILED,
                blocker=str(e)[:2000], current_activity="",
            )
            raise
        finally:
            self._cleanup_local_setup(sub_ctx, ws)
            
    async def _run_remote(self, remote: RemoteEndpoint, task_text: str, entry: SwarmAgent):
        registry = SwarmRegistry.get()
        if not a2a_runner.is_available():
            registry.update_status(
                entry.agent_name, SwarmAgentStatus.FAILED,
                blocker="FastA2A client not available in this Agent Zero build.",
            )
            raise RuntimeError("FastA2A client not available")

        registry.update_status(
            entry.agent_name, SwarmAgentStatus.WORKING,
            current_activity=f"Submitting to {remote.label}",
        )
        try:
            conn, task_id, remote_ctx_id = await a2a_runner.submit_task(remote, task_text)
        except Exception as e:
            registry.update_status(
                entry.agent_name, SwarmAgentStatus.FAILED,
                blocker=f"Could not reach {remote.label}: {e}",
            )
            raise

        # Persist task_id + remote context so cancel/intervene can target it.
        registry.update_status(
            entry.agent_name,
            SwarmAgentStatus.WORKING,
            current_activity=f"Running on {remote.label}",
            remote_task_id=task_id,
            context_id=remote_ctx_id,
        )

        try:
            state, result_text = await a2a_runner.wait_for_result(conn, task_id)
        finally:
            try:
                await conn.close()
            except Exception:
                pass

        mapped = _A2A_TERMINAL_MAP.get(state, SwarmAgentStatus.FAILED)
        if mapped is SwarmAgentStatus.DONE:
            result_text = str(result_text or "")
            stored = self._cap_result(result_text)
            registry.update_status(
                entry.agent_name, SwarmAgentStatus.DONE,
                result=stored, current_activity="",
            )
            return result_text
        else:
            registry.update_status(
                entry.agent_name, mapped,
                blocker=result_text[:2000] if result_text else f"Remote state: {state}",
                current_activity="",
            )
            if mapped is SwarmAgentStatus.FAILED:
                raise RuntimeError(result_text or f"Remote task ended in state {state}")
            return result_text

    def _cap_result(self, raw: str) -> str:
        encoded = raw.encode("utf-8")
        cap = getattr(self, "_result_byte_cap", MAX_RESULT_BYTES)
        if len(encoded) > cap:
            return encoded[:cap].decode("utf-8", errors="ignore") + "\n[...truncated]"
        return raw

    def _summarize(self, entries, results) -> str:
        parts = []
        for entry, result in zip(entries, results):
            origin = f" via {entry.remote_label}" if entry.is_remote else ""
            if entry.status is SwarmAgentStatus.FAILED or isinstance(result, Exception):
                detail = entry.blocker or str(result)
                parts.append(
                    f"### {entry.label} ({entry.agent_name}){origin}\n"
                    f"**Status:** FAILED\n**Error:** {detail}\n"
                )
            elif entry.status is SwarmAgentStatus.CANCELLED:
                parts.append(
                    f"### {entry.label} ({entry.agent_name}){origin}\n"
                    f"**Status:** CANCELLED\n"
                )
            else:
                summary_result = self._cap_result(str(result or ""))
                parts.append(
                    f"### {entry.label} ({entry.agent_name}){origin}\n"
                    f"**Status:** DONE\n**Result:**\n{summary_result}\n"
                )
        return (
            f"## Parallel Delegation Complete\n"
            f"{len(entries)} agents ran. Results:\n\n"
            + "\n---\n".join(parts)
        )

    def get_log_object(self):
        return self.agent.context.log.log(
            type="tool",
            heading=f"icon://group_work {self.agent.agent_name}: Delegating Parallel Tasks",
            content="",
            kvps=self.args,
        )
