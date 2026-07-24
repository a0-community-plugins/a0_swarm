from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
import shutil
import sys


PLUGIN_PACKAGE_PREFIX = "usr.plugins.a0_swarm"
PLUGIN_ROOT = Path(__file__).resolve().parent
SWARM_WORKER_CONTEXT_KEY = "_a0_swarm.worker"


def install() -> None:
    """Finish install/update cleanup without a separate Execute step."""
    _stop_workers()
    _sweep_owned_workspaces()
    _clear_plugin_modules()
    _clear_plugin_bytecode()


def pre_update() -> None:
    _stop_workers()
    _sweep_owned_workspaces()
    _clear_plugin_modules()
    _clear_plugin_bytecode()


def uninstall() -> None:
    _stop_workers()
    _sweep_owned_workspaces()
    _clear_plugin_modules()
    _clear_plugin_bytecode()


def _stop_workers() -> None:
    try:
        from agent import AgentContext
        from helpers import persist_chat
        from usr.plugins.a0_swarm.helpers import a2a_runner
        from usr.plugins.a0_swarm.helpers.registry import (
            SwarmAgentStatus,
            SwarmRegistry,
        )
        from usr.plugins.a0_swarm.helpers.remotes import RemoteEndpoint
    except ImportError:
        return

    registry = SwarmRegistry.get()
    snapshot = registry.snapshot()
    for raw in snapshot["agents"]:
        entry = registry.get_agent(raw["agent_name"])
        if not entry or entry.status in {
            SwarmAgentStatus.DONE,
            SwarmAgentStatus.FAILED,
            SwarmAgentStatus.CANCELLED,
        }:
            continue
        registry.update_status(
            entry.agent_name,
            SwarmAgentStatus.CANCELLED,
            current_activity="",
            blocker="Plugin lifecycle cleanup stopped this worker.",
        )
        if entry.is_remote:
            remote = RemoteEndpoint(
                label=entry.remote_label,
                base_url=entry.remote_base_url,
                auth_token=entry.remote_auth_token,
            )
            coroutine = a2a_runner.cancel_task(remote, entry.remote_task_id)
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(coroutine)
            else:
                loop.create_task(coroutine)

    for context in list(AgentContext.all()):
        try:
            marker = context.get_data(
                SWARM_WORKER_CONTEXT_KEY,
                recursive=False,
            )
        except (AttributeError, TypeError):
            marker = (getattr(context, "data", {}) or {}).get(
                SWARM_WORKER_CONTEXT_KEY
            )
        if not marker:
            continue

        AgentContext.remove(context.id)
        try:
            persist_chat.remove_chat(context.id)
        except Exception:
            pass

    registry.clear_completed()


def _sweep_owned_workspaces() -> None:
    try:
        from agent import AgentContext
        from usr.plugins.a0_swarm.helpers import workspace

        workspace.sweep_orphans([context.id for context in AgentContext.all()])
    except Exception:
        pass


def _clear_plugin_modules() -> None:
    for module_name in list(sys.modules):
        if module_name == PLUGIN_PACKAGE_PREFIX or module_name.startswith(
            f"{PLUGIN_PACKAGE_PREFIX}."
        ):
            sys.modules.pop(module_name, None)
    importlib.invalidate_caches()


def _clear_plugin_bytecode() -> None:
    for cache_dir in PLUGIN_ROOT.rglob("__pycache__"):
        shutil.rmtree(cache_dir, ignore_errors=True)
