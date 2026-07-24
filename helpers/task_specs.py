from __future__ import annotations

from typing import Any


DEFAULT_MAX_PARALLEL = 4
ABSOLUTE_MAX_PARALLEL = 16
DEFAULT_RESULT_KB = 64
MIN_RESULT_KB = 1
MAX_RESULT_KB = 1024
MAX_TASK_CHARS = 100_000
VALID_WORKSPACE_MODES = {"none", "inherit", "isolated"}

SWARM_WORKER_CONTEXT_KEY = "_a0_swarm.worker"
HARNESS_RUN_CONTEXT_KEY = "agent_harness.current_run"
HARNESS_WORKER_CONTEXT_KEY = "agent_harness.parallel_worker"


def bounded_int(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed < minimum:
        return default
    return min(parsed, maximum)


def effective_max_parallel(value: Any) -> int:
    """Resolve legacy/untrusted settings to a safe, finite fan-out."""
    return bounded_int(
        value,
        default=DEFAULT_MAX_PARALLEL,
        minimum=1,
        maximum=ABSOLUTE_MAX_PARALLEL,
    )


def result_byte_cap(value: Any) -> int:
    kb = bounded_int(
        value,
        default=DEFAULT_RESULT_KB,
        minimum=MIN_RESULT_KB,
        maximum=MAX_RESULT_KB,
    )
    return kb * 1024


def normalize_workspace_mode(value: Any) -> str:
    mode = str(value or "none").strip().lower()
    return mode if mode in VALID_WORKSPACE_MODES else "none"


def normalize_tasks(tasks: Any, *, max_parallel: int) -> list[dict[str, str]]:
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("tasks must be a non-empty list")
    if len(tasks) > max_parallel:
        raise ValueError(
            f"requested {len(tasks)} tasks, but this plugin allows at most "
            f"{max_parallel} per delegation"
        )

    normalized: list[dict[str, str]] = []
    for index, raw in enumerate(tasks, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"task {index} must be an object")

        task = str(raw.get("task") or "").strip()
        if not task:
            raise ValueError(f"task {index} is missing a non-empty task")
        if len(task) > MAX_TASK_CHARS:
            raise ValueError(
                f"task {index} exceeds the {MAX_TASK_CHARS:,}-character limit"
            )

        label = str(raw.get("label") or f"Agent-{index}").strip()
        profile = str(raw.get("profile") or "").strip()
        endpoint = str(raw.get("endpoint") or "").strip()
        if len(label) > 120:
            raise ValueError(f"task {index} label exceeds 120 characters")
        if len(profile) > 200:
            raise ValueError(f"task {index} profile exceeds 200 characters")
        if len(endpoint) > 2048:
            raise ValueError(f"task {index} endpoint exceeds 2,048 characters")

        normalized.append(
            {
                "label": label or f"Agent-{index}",
                "task": task,
                "profile": profile,
                "endpoint": endpoint,
            }
        )
    return normalized


def orchestration_conflict(context: Any) -> str:
    """Prevent nested orchestrators from multiplying workers recursively."""

    def get_data(key: str) -> Any:
        try:
            return context.get_data(key, recursive=False)
        except (AttributeError, TypeError):
            data = getattr(context, "data", {}) or {}
            return data.get(key) if isinstance(data, dict) else None

    if get_data(SWARM_WORKER_CONTEXT_KEY):
        return "A0 Swarm workers cannot start another swarm"
    if get_data(HARNESS_WORKER_CONTEXT_KEY):
        return "Agent Harness workers cannot start an A0 Swarm run"

    harness_run = get_data(HARNESS_RUN_CONTEXT_KEY)
    if isinstance(harness_run, dict) and harness_run.get("status") in {
        "active",
        "blocked",
    }:
        return (
            "an Agent Harness run is active in this chat; finish or stop it "
            "before using A0 Swarm"
        )
    return ""
