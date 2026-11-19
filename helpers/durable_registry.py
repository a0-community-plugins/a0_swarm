"""Transactional control state with the existing registry as its UI projection."""
from __future__ import annotations

import copy
from dataclasses import asdict
import functools
import hashlib
import json
import time

from .durable_store import DurableStore
from .registry import (SwarmRegistry, SwarmAgent, SwarmAgentStatus as Status,
                       SwarmRun, SwarmRunStatus, SwarmMessage, SwarmTimelineEvent,
                       TERMINAL, utc_iso_now)


def transaction(method):
    @functools.wraps(method)
    def call(self, *args, **kwargs):
        with self._rlock:
            if self._in_transaction:
                return method(self, *args, **kwargs)
            if self.store.failed or self.store.closed:
                raise RuntimeError("Swarm journal unavailable; execution held")
            before = self._dump()
            self._in_transaction = True
            try:
                result = method(self, *args, **kwargs)
                self.store.save(self._dump())
            except BaseException:
                self._load(before)
                raise
            finally:
                self._in_transaction = False
            subscribers = list(self._subscribers)
        super(DurableRegistry, self)._fire(subscribers)
        return result
    return call


class DurableRegistry(SwarmRegistry):
    def __init__(self, store):
        super().__init__()
        self.store = store
        self._in_transaction = False
        self._load(store.load())
        self.reconcile_restart()

    @classmethod
    def open(cls):
        from helpers.files import get_abs_path
        return cls(DurableStore(get_abs_path("usr/plugin_data/a0_swarm/swarm.sqlite")))

    def _dump(self):
        agents = {}
        for key, agent in self._agents.items():
            value = asdict(agent)
            value.pop("remote_auth_token", None)
            value.pop("messages", None)
            value["live_output"] = value["live_reasoning"] = ""
            agents[key] = value
        return {"runs": {k: asdict(v) for k, v in self._runs.items()}, "agents": agents,
                "messages": {k: v.to_dict() for k, v in self._messages.items()},
                "events": {k: v.to_dict() for k, v in self._events.items()}}

    def _load(self, data):
        self._runs = {k: SwarmRun(**{**v, "status": SwarmRunStatus(v["status"])}) for k, v in data["runs"].items()}
        self._agents = {k: SwarmAgent(**{**v, "status": Status(v["status"])}) for k, v in data["agents"].items()}
        self._messages = {k: SwarmMessage(**v) for k, v in data["messages"].items()}
        self._events = {k: SwarmTimelineEvent(**v) for k, v in data["events"].items()}
        for msg in self._messages.values():
            target = msg.recipient if msg.recipient != "orchestrator" else msg.sender
            if target in self._agents:
                self._agents[target].messages.append(msg)

    def _fire(self, subs):
        if not self._in_transaction:
            super()._fire(subs)

    def get_agent(self, name):
        with self._rlock:
            return copy.deepcopy(self._agents.get(name))

    @transaction
    def admit(self, parent, request_id, specs, parent_name="orchestrator"):
        digest = hashlib.sha256(json.dumps(specs, sort_keys=True).encode()).hexdigest()
        for run in self._runs.values():
            if run.parent_context_id == parent and run.request_id == request_id:
                if run.spec_digest != digest:
                    raise ValueError("Request ID already belongs to different tasks")
                return copy.deepcopy(run)
        run = super().create_run(parent, parent_name, f"delegate_parallel: {len(specs)} task(s)")
        self._runs[run.run_id].request_id = request_id
        self._runs[run.run_id].spec_digest = digest
        for index, spec in enumerate(specs):
            entry = SwarmAgent(
                agent_name=f"SA1_{index+1}-{run.run_id[4:]}", label=spec["label"], task=spec["task"],
                context_id="", parent_context_id=parent, status=Status.PENDING,
                started_at=utc_iso_now(), run_id=run.run_id, spec=spec,
                remote_label=spec.get("endpoint", ""), remote_base_url=spec.get("remote_url", ""),
                delivery_mode="remote_a2a" if spec.get("endpoint") else "local",
                generation=self.store.generation)
            super().register(entry)
        return copy.deepcopy(self._runs[run.run_id])

    @transaction
    def transition(self, name, attempt, status, **fields):
        agent = self._agents.get(name)
        if not agent or agent.attempt != attempt or agent.generation != self.store.generation:
            return False
        if agent.status in TERMINAL:
            return False
        if agent.cancel_requested and status not in (Status.CANCELLED, Status.REVIEW, Status.CANCEL_REQUESTED):
            # Preserve a late result without presenting it as successful cancellation.
            if fields.get("result"):
                agent.result = fields["result"]
            return False
        super().update_status(name, status, **fields)
        return True

    @transaction
    def reconcile_restart(self):
        for agent in self._agents.values():
            agent.generation = self.store.generation
            if agent.status in TERMINAL:
                continue
            if agent.cancel_requested:
                agent.status = Status.CANCEL_REQUESTED
            elif agent.phase == "queued":
                agent.status = Status.PENDING
            elif agent.remote_task_id:
                agent.status = Status.RECOVERING
            elif agent.checkpoint.get("stage") in ("ready", "model", "response_ready", "durable_tool", "final") and agent.resume_count < 3:
                agent.status = Status.RECOVERING
                agent.current_activity = "Recovering saved progress"
                agent.blocker = ""
            else:
                agent.status = Status.REVIEW
                agent.blocker = "Execution was interrupted; no verified resumable outcome is available."
        for msg in self._messages.values():
            if msg.delivery_state == "sending":
                msg.delivery_state = "uncertain"
                msg.failure_reason = "Delivery was interrupted before its receipt was committed."

    @transaction
    def save_checkpoint(self, name, attempt, checkpoint):
        agent = self._agents[name]
        if agent.attempt != attempt or agent.generation != self.store.generation or agent.status in TERMINAL:
            raise RuntimeError("Checkpoint ownership changed")
        agent.checkpoint = copy.deepcopy(checkpoint)

    @transaction
    def begin_resume(self, name, attempt):
        agent = self._agents[name]
        if agent.attempt != attempt or agent.cancel_requested or agent.resume_count >= 3:
            raise ValueError("Automatic recovery limit reached or cancellation requested")
        agent.resume_count += 1
        agent.status = Status.RECOVERING
        agent.blocker = ""
        self.add_event(agent.run_id, name, "recovery", f"Warm recovery {agent.resume_count}: reusing saved progress and results.")

    @transaction
    def recover_after_error(self, name, attempt):
        agent = self._agents[name]
        if agent.attempt != attempt or agent.cancel_requested or agent.status in TERMINAL:
            return
        safe = agent.checkpoint.get("stage") in ("ready", "model", "response_ready", "durable_tool", "final")
        agent.status = Status.RECOVERING if safe and agent.resume_count < 3 else Status.REVIEW
        agent.next_wake = time.time() + min(30, 2 ** agent.resume_count)
        agent.current_activity = "Recovering saved progress" if agent.status == Status.RECOVERING else ""
        agent.blocker = "" if agent.status == Status.RECOVERING else "No confirmed continuation point is available, or automatic recovery is exhausted. Inspect retained work."

    @transaction
    def claim_message(self, message_id):
        msg = self._messages[message_id]
        if msg.delivery_state not in ("queued", "failed"):
            return False
        self._mark_message_state(message_id, "sending")
        return True

    @transaction
    def record_remote(self, name, attempt, task_id, context_id):
        agent = self._agents[name]
        if agent.attempt != attempt or agent.generation != self.store.generation:
            raise RuntimeError("Stale remote receipt")
        agent.remote_task_id = task_id
        agent.context_id = context_id
        agent.phase = "remote_wait"

    @transaction
    def request_cancel(self, name):
        agent = self._agents[name]
        if agent.status in TERMINAL:
            return
        agent.cancel_requested = True
        agent.retries = 0
        agent.next_wake = 0
        super().update_status(name, Status.CANCEL_REQUESTED, current_activity="Cancellation requested")

    @transaction
    def retry(self, name):
        old = self._agents[name]
        if old.status != Status.REVIEW or old.cancel_requested or old.remote_task_id:
            raise ValueError("This worker cannot be retried; reconcile or cancel it first")
        self.add_event(old.run_id, name, "recovery", f"User authorized attempt {old.attempt+1}; prior outcome remains uncertain.",
                       ref_id=json.dumps({"attempt": old.attempt, "context_id": old.context_id, "workspace": old.workspace}))
        old.attempt += 1
        old.status = Status.PENDING
        old.phase = "queued"
        old.context_id = ""
        old.workspace = {}
        old.checkpoint = {}
        old.resume_count = 0
        old.blocker = ""
        old.retries = 0
        old.next_wake = old.deadline = 0

    @transaction
    def finish_run(self, run_id):
        run = self._runs[run_id]
        agents = [a for a in self._agents.values() if a.run_id == run_id]
        if run.result or not agents or any(a.status not in TERMINAL for a in agents):
            return run.result
        run.status = self._derive_run_status(run.status, agents)
        run.finished_at = utc_iso_now()
        run.result = "## Parallel Delegation Complete\nRun: " + run_id + "\n\n" + "\n\n".join(
            f"### {a.label}\nStatus: {a.status.value}\n{a.result or a.blocker}" for a in agents)
        return run.result

    @transaction
    def collect(self, run_id):
        run = self._runs[run_id]
        if not run.result:
            raise ValueError("This run has no final result yet")
        run.collected = True
        return run.result

    @transaction
    def clear_completed(self, parent_ctx_id=None):
        for run in self._runs.values():
            agents = [a for a in self._agents.values() if a.run_id == run.run_id]
            unresolved = any(m.run_id == run.run_id and m.delivery_state in ("queued", "sending", "uncertain") for m in self._messages.values())
            if agents and all(a.status in TERMINAL for a in agents) and not unresolved and (parent_ctx_id is None or run.parent_context_id == parent_ctx_id):
                self.finish_run(run.run_id)
                run.hidden = True

    def clear_for_parent(self, parent_ctx_id):
        raise ValueError("Journal deletion requires explicit data removal; use clear completed to hide results")

    def snapshot(self, parent_ctx_id=None):
        with self._rlock:
            snap = super().snapshot(parent_ctx_id)
            snap["runs"] = [r for r in snap["runs"] if not r["hidden"]]
            visible = {r["run_id"] for r in snap["runs"]}
            snap["agents"] = [a for a in snap["agents"] if a["run_id"] in visible]
            snap["revision"] = self.store.revision
            snap["generation"] = self.store.generation
            for run in snap["runs"]:
                if any(a["status"] == "needs_review" for a in run["agents"]):
                    run["status"] = "needs_review"
                run["result_ready"] = bool(run["result"])
            return snap


# The legacy facade's control mutations share one commit/rollback boundary.
# Telemetry/stream updates deliberately remain transient.
for _name in ("register", "create_run", "add_event", "create_message", "_mark_message_state", "update_status", "add_message", "remove"):
    setattr(DurableRegistry, _name, transaction(getattr(SwarmRegistry, _name)))
