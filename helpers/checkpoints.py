"""Worker-only continuation points. A saved conversation is never proof of a tool outcome."""
from __future__ import annotations

from dataclasses import asdict
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import uuid

from .registry import SwarmRegistry

VERSION = 1
MAX_BYTES = 8 * 1024 * 1024
SAFE_STAGES = {"ready", "model", "response_ready", "durable_tool", "final"}


class WorkerInterrupted(BaseException):
    """Leave the framework's generic retry loop; Swarm owns continuation."""


def control_exception(agent, data):
    owner = owned(agent)
    if not owner:
        return
    from helpers.errors import InterventionException, RepairableException, HandledException
    args, kwargs = data.get("args", ()), data.get("kwargs", {})
    error = kwargs.get("exception", args[2] if len(args) > 2 else None)
    reg, entry = owner
    if isinstance(error, InterventionException):
        return
    if isinstance(error, (RepairableException, HandledException)) and entry.checkpoint.get("stage") == "model":
        return
    from .execution import RecoveryHold
    if isinstance(error, RecoveryHold):
        reg.save_checkpoint(entry.agent_name, entry.attempt, {**entry.checkpoint, "stage": "uncertain"})
    if error is not None:
        raise WorkerInterrupted(type(error).__name__) from None


@lru_cache(maxsize=1)
def host_fingerprint():
    from helpers.files import get_abs_path
    paths = ("agent.py", "helpers/persist_chat.py", "helpers/history.py", "helpers/tool.py")
    return hashlib.sha256(b"".join(Path(get_abs_path(p)).read_bytes() for p in paths)).hexdigest()


def owned(agent):
    if agent is None:
        return None
    reg = SwarmRegistry.get()
    row = reg.get_agent_by_context(agent.context.id)
    if row is None or row.is_remote or row.phase != "local_started":
        return None
    return reg, reg.get_agent(row.agent_name)


async def settle_history(agent):
    task = agent.get_data("_organize_history_task")
    if task is not None:
        await task.result()


def snapshot(agent):
    from helpers import persist_chat, projects
    data = persist_chat._serialize_context(agent.context)
    if agent.number != 0 or len(data["agents"]) != 1:
        raise ValueError("Nested agent stacks need a separate recovery adapter")
    loop = agent.loop_data
    state = {
        "version": VERSION, "host": host_fingerprint(), "context": data,
        "project": projects.get_context_project_name(agent.context),
        "loop": {key: getattr(loop, key) for key in ("last_response", "protocol_persistent", "extras_persistent")},
        "user_message": agent.last_user_message.to_dict() if agent.last_user_message else None,
        "intervention": asdict(agent.intervention) if agent.intervention else None,
    }
    # Unlike chat export, do not silently discard nonportable extension state.
    encoded = json.dumps(state, ensure_ascii=False)
    if len(encoded.encode()) > MAX_BYTES:
        raise ValueError("Checkpoint exceeds the recovery size limit")
    return json.loads(encoded)


async def capture(agent, stage, **extra):
    owner = owned(agent)
    if not owner:
        return
    reg, entry = owner
    try:
        await settle_history(agent)
        checkpoint = {**snapshot(agent), "stage": stage, **extra}
        if len(json.dumps(checkpoint).encode()) > MAX_BYTES:
            raise ValueError("Checkpoint exceeds the recovery size limit")
    except (TypeError, ValueError):
        checkpoint = {"version": VERSION, "stage": "unsupported"}
    reg.save_checkpoint(entry.agent_name, entry.attempt, checkpoint)


def invalidate(agent):
    owner = owned(agent)
    if owner:
        reg, entry = owner
        # Called before assistant history/tool dispatch, including tool.before_execution.
        reg.save_checkpoint(entry.agent_name, entry.attempt, {**entry.checkpoint, "stage": "dispatch"})


async def before_model(agent, call_data):
    if not owned(agent) or call_data.get("background") or getattr(agent.loop_data, "current_tool", None):
        return
    model = call_data.get("model")
    options = getattr(model, "kwargs", {}) or {}
    # Inference can be repeated; provider-executed tools may have unknown effects.
    builtins = (call_data.get("responses_builtin_tools") or options.get("responses_builtin_tools")
                or options.get("tools") or (options.get("extra_body") or {}).get("tools"))
    await capture(agent, "provider_effect" if builtins else "model")


async def model_result(agent, result_data):
    if owned(agent):
        result = result_data.get("llm_result")
        if result is not None:
            await capture(agent, "response_ready", llm_result=result.to_dict())


async def before_tool(agent, tool_name, tool_args):
    if tool_name != "swarm_execute" or not owned(agent):
        return
    from helpers.llm_result import result_from_metadata
    last = list(agent.history.all_messages())[-1]
    result = result_from_metadata(last.metadata)
    calls = result.function_calls if result else []
    # Partial native batches need a dispatch cursor. Do not skip their remaining calls.
    if len(calls) > 1:
        return
    tool = agent.loop_data.current_tool
    job_id = uuid.uuid4().hex
    tool._swarm_job_id = job_id
    await capture(agent, "durable_tool", job_id=job_id, tool_args=dict(tool_args),
                  call_id=calls[0].call_id if calls else "", tool_log_id=getattr(getattr(tool, "log", None), "id", ""))


async def after_tool(agent, response, tool_name):
    if tool_name == "response" and response is not None and response.break_loop and owned(agent):
        await capture(agent, "final", result=str(response.message))


def restore(agent, checkpoint):
    from agent import UserMessage
    from helpers import history, projects
    if checkpoint.get("version") != VERSION or checkpoint.get("host") != host_fingerprint():
        raise ValueError("Host or checkpoint format changed; continuation requires review")
    data = checkpoint["context"]
    if data["id"] != agent.context.id or checkpoint.get("project") != projects.get_context_project_name(agent.context):
        raise ValueError("Worker context or workspace changed")
    saved = data["agents"][0]
    if saved.get("agent_profile", "") != agent.config.profile:
        raise ValueError("Worker profile changed")
    agent.history = history.deserialize_history(saved["history"], agent=agent)
    agent.data.update(saved["data"])
    agent.context.data.update(data["data"])
    agent.context.output_data.update(data["output_data"])
    user = checkpoint.get("user_message")
    agent.last_user_message = history.Message.from_dict(user, agent.history) if user else None
    intervention = checkpoint.get("intervention")
    agent.intervention = UserMessage(**intervention) if intervention else None
    agent._swarm_resume_loop = checkpoint.get("loop", {})
    if checkpoint.get("stage") == "response_ready":
        agent._swarm_cached_llm = checkpoint["llm_result"]


def restore_loop(agent):
    saved = vars(agent).pop("_swarm_resume_loop", None)
    if saved is not None:
        for key, value in saved.items():
            setattr(agent.loop_data, key, value)
        agent.loop_data.extras_temporary["swarm_recovery"] = (
            "Continued from committed progress in this same worker. Reuse recorded results; "
            "do not repeat completed steps. Transient interactive shell/browser sessions may "
            "have reset: re-establish session state before relying on it."
        )


def cached_model(agent, data):
    cached = vars(agent).pop("_swarm_cached_llm", None)
    if cached is not None:
        from helpers.llm_result import LLMResult
        data["result"] = LLMResult.from_dict(cached)
