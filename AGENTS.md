# A0 Swarm

## Purpose

Own bounded local/A2A delegation and durable swarm orchestration.

## Ownership

- `helpers/durable_store.py` owns the SQLite schema, process lock and generation.
- `helpers/durable_registry.py` owns transactional admission, receipts and control transitions; `registry.py` is the compatibility/UI projection.
- `helpers/execution.py` owns worker scheduling and bounded same-attempt recovery. The framework owns each local task.
- `helpers/checkpoints.py` and worker-only extension hooks own versioned continuation snapshots, model/final receipts, host fingerprint checks, and dispatch invalidation. Never infer tool success from chat history.
- `helpers/command_jobs.py` owns immutable batch admission and reconnect; the standalone `command_runner.py` owns its OS lock, dispatch marker, output, cancellation and terminal receipt.
- Workers use persisted `AgentContextType.TASK`; core deliberately excludes `BACKGROUND` contexts from chat persistence. Restored chats remain inert until the journal permits work.
- `helpers/recovery.py` and plugin extensions restore context ownership after chat restoration and reconcile from the job loop.
- Function hooks for synchronous `initialize_chats` and `toggle_plugin` use synchronous `execute`; chat restoration attaches its callback to the returned deferred task.
- `helpers/workspace.py` owns only explicitly marked workspaces. Recovery custody survives lifecycle changes.
- `api/` and `webui/` expose chat-scoped controls and committed state.

## Local Contracts

- Persist admission and dispatch intent before starting work. Publish control changes only after commit; journal failure must never downgrade to memory.
- Default state lives outside the checkout in `usr/plugin_data/a0_swarm/`; update/uninstall preserve it.
- A single OS-locked coordinator owns a database. Generation/attempt checks reject stale writes; they cannot fence external side effects.
- Resume local workers only from supported portable checkpoints (maximum 8 MiB, three automatic recoveries per attempt). Reuse recorded model results and durable command receipts. Unknown ordinary tools, partial native batches, missing command outcomes and unknown remote submissions require review. Only a known remote task ID permits remote reconnect.
- Save no remote tokens or credential-bearing endpoint URLs. Resolve current scoped credentials and verify endpoint identity before reconnecting.
- Workspace cleanup is explicit; neither a missing live context nor a retained branch proves dirty files are disposable.
- Hide completed runs without deleting admission receipts. Keep unresolved messages visible.
- Local message delivery runs inside the existing owned task and writes its intervention without calling `communicate`, which could start a replacement turn. Recheck attempt, context, phase, cancellation and any pending intervention at handoff.
- One orchestrator per chat; preserve finite fan-out, auth/CSRF and parent/run scope checks.

## Work Guidance

- The built-in adapter supports current single-agent framework loops and finite `swarm_execute` commands. Preserve native Responses metadata and cache eligibility; never promise a provider cache hit or restore volatile interactive sessions.
- Checkpoints contain private conversation data; expose only stage/count in the panel. Keep command files and receipts private. Parent result collection remains explicit after a parent restart.
- Keep control updates transactional. Per-token previews and usage counters may be transient.
- Update README and tool prompts with user-visible behavior. Do not add packages for SQLite.

## Verification

- Run plugin unittest discovery using the CI namespace layout; run framework lifecycle checks with the framework interpreter.
- Use synchronized process-kill fixtures and deterministic remote fixtures for crash/ambiguity boundaries.
- Check changed JavaScript syntax and one browser recovery/control pass.
- After passing these checks, stop. Broaden or repeat only for a concrete failure or unresolved risk.

## Child DOX Index

No child DOX files.
