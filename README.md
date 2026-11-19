# A0 Swarm

A0 Swarm gives Agent Zero a bounded parallel-delegation tool and a live,
chat-scoped mission-control panel. A parent agent can send independent tasks
to local managed workers or configured Agent Zero remotes over A2A, collect
their results, exchange messages, and cancel work.

Version 2.1 adds durable orchestration while preserving three operating rules:

- fan-out is finite: four workers by default and 16 at the absolute ceiling;
- every panel snapshot and control action is scoped to the active chat;
- completed runs remain visible until the user clears them.

Agent Zero core files are not modified.

## Install

Place this repository at:

```text
usr/plugins/a0_swarm
```

Enable **A0 Swarm** in Agent Zero's plugin settings and restart Agent Zero when
the UI asks. The install hook performs lifecycle cleanup automatically.
There is no separate Execute step.

Use 2.1.1 or later: it fixes startup and disable hooks that incorrectly returned
coroutines to synchronous framework lifecycle dispatch in 2.1.0.

Runtime settings belong in the local `config.json`, which is ignored and must
not be committed.

## Use

The parent agent receives `delegate_parallel`. One call creates one swarm run:

```json
{
  "tasks": [
    {
      "label": "Research",
      "task": "Inspect the relevant primary documentation and report evidence."
    },
    {
      "label": "Review",
      "task": "Review the current implementation and list concrete risks."
    }
  ]
}
```

Each task supports:

| Field | Required | Meaning |
| --- | --- | --- |
| `task` | yes | Complete, independent assignment for the subagent. |
| `label` | no | Human-readable panel label. |
| `profile` | no | Local Agent Zero profile. The parent profile is inherited by default. |
| `endpoint` | no | Configured remote label or full A2A URL. Omit for a local worker. |

Subagents can call `swarm_message` to message the orchestrator or another agent
in the same run. The right-canvas **Swarm** surface shows live activity,
streamed output, tool counts, token usage, messages, completion state, and
errors. WebSocket updates are backed by a scoped HTTP poll, so a reconnect does
not make the panel go blank. Switching chats immediately clears stale state and
loads only the selected chat's runs.

## Workspace modes

Local workers have separate Agent Zero chat contexts. That does not
automatically mean separate filesystems.

| Mode | Filesystem behavior | Best use |
| --- | --- | --- |
| `none` | Workers use the global work directory. | Independent non-project tasks. |
| `inherit` | Workers share the parent's active project folder. | Parallel reading and analysis only. |
| `isolated` | Each worker receives a temporary Git worktree and persistent `swarm/...` branch. | Parallel repository edits. |

`isolated` falls back to `inherit` when the active project is not a Git
repository. It uses the `a0_worktree` plugin when that plugin exposes its
versioned ownership contract; otherwise A0 Swarm manages only worktrees marked
as its own. The checkout and its branch are retained after the task, including
dirty and untracked files. Review them before merging or explicitly removing the
owned worktree. Selecting this mode
therefore authorizes those worktree and branch mutations for swarm tasks.

## Remote Agent Zero workers

Add a remote in plugin settings with a label, its A2A URL, and an optional
token. Use **A2A Discovery** to verify the Agent Card before delegating work.
Follow-up messages reuse the returned remote context; cancellation uses the
remote A2A task identifier.

Docker discovery is optional. The easiest setup is usually to enter a known A2A
URL directly, which requires no Docker socket. If you want automatic discovery
of sibling containers, the settings UI reports Docker access readiness and
shows the exact Compose or `docker run` socket mount. A container cannot add a
new mount to itself, so applying that mount requires recreating or restarting
the Agent Zero container from the host. Treat Docker-socket access as
high-privilege host access.

When Agent Zero itself runs in Docker, browser-style `localhost` remote URLs
are translated to `host.docker.internal` for the runtime connection.

## A0 Swarm or Agent Harness?

Use one orchestrator per chat.

- **Agent Harness Pro** is the default for careful, structured single-agent
  coding work.
- **Agent Harness Ultra** owns a coding task graph and coordinates one to four
  bounded workers with verification and adoption gates.
- **A0 Swarm** is the general delegation surface when tasks are independent,
  may use different profiles, need interactive messaging, or need to reach
  remote Agent Zero instances.

A0 Swarm rejects calls from an active Agent Harness run or from another swarm
worker. Nesting orchestration layers makes worker count, ownership, approvals,
and completion state ambiguous.

## Configuration

| Setting | Default | Contract |
| --- | --- | --- |
| `max_parallel` | `4` | Effective range 1–16. Legacy `0` resolves to 4. |
| `default_profile` | parent profile | Optional local worker profile override. |
| `result_kb_cap` | `64` | Per-agent registry, panel, and parent-summary cap; range 1–1024 KB. |
| `subagent_workspace` | `none` | `none`, `inherit`, or `isolated`. |
| `remotes` | `[]` | A2A label, base URL, and optional token records. |

Swarm control state is committed to SQLite under
`usr/plugin_data/a0_swarm/swarm.sqlite`. This directory must be on persistent,
local storage. One Agent Zero process owns it; shared/network filesystems and
multiple coordinators are not supported. No additional Python package is needed.
Remote tokens stay in scoped plugin configuration and are resolved at use time;
credential-bearing URLs are rejected. Per-token previews remain transient.

## Restart recovery

Admission, dispatch intent, task IDs, cancellation requests, messages, results,
and workspace references survive a process restart. Queued work resumes after
ownership is restored. Known remote task IDs reconnect to the same task, with
persisted deadlines and bounded retries. Completed workers are never rerun to
reconstruct a result.

Interrupted local workers and remote sends without a recorded task ID show
**Needs review**. A timeout does not prove an action failed. This release provides
durable orchestration, not arbitrary local model/tool checkpoint replay. It does
not install or activate a separate durability engine. Full local turn resumption
requires a separately verified backend integration.

Use the panel to check a known remote task, collect a completed result, or request
cancellation. Cancellation stays **Cancel requested** until confirmed. If checks
are exhausted, inspect the retained worker and request cancellation again as
needed. An explicit retry of uncertain work warns that previous effects may be
duplicated; it creates a new attempt and preserves the previous attempt's context
and workspace reference in the timeline.

Supply a stable `request_id` to `delegate_parallel` and reuse it for an admission
retry. A conflicting task payload rejects. Without one, the framework invocation
log ID identifies that call only; a new invocation is a new request. To reattach
or collect later, call with `run_id` alone. Result collection is repeatable; it
does not guarantee exactly-once insertion into a parent's chat history.

Messages are journaled before delivery. An interrupted send becomes **Delivery
uncertain** and is never automatically resent. Local handoff awaits a worker reply
as acknowledgement because ordinary chat interventions have no durable admission
receipt. Remote receipt acknowledges transport acceptance, not completion of the
requested work. Review before retrying an uncertain message.

Clear-completed hides terminal runs while retaining deduplication receipts; runs
with unresolved messages remain visible. Update, disable, and uninstall require
active workers to stop and preserve the journal, chats, and workspaces. Missing
live contexts are never grounds for automatic worktree deletion. Retention is
explicit in this release; monitor storage usage and remove data only after its
parent chats and external work are no longer resumable.

## Design references

The admission/intent/outcome boundaries were informed by
[Alessandro's Durable Execution plugin](https://github.com/3clyp50/a0-durable-execution/tree/93097e75706e4b90b003f18c5606faea0d98c2cf).
Versioned ownership and durable task-state concepts were informed by
[Pi durable](https://github.com/earendil-works/pi/tree/5a14e69c067d40dfbff4f2fff7d3b34c897e2dc9/packages/durable).
Neither runtime nor its core overrides are bundled here.

## Development

From the plugin repository:

```bash
python -m unittest discover -s tests -v
node --check webui/swarm-store.js
node --check extensions/webui/send_message_before/_10_swarm_mentions.js
python -m compileall -q .
```

Run `scripts/verify_framework.py` with the framework interpreter and an isolated
plugin namespace for the synthetic process-kill lifecycle check. It disables
extension discovery inside its child fixture and never calls a model provider.
Use `--delivery` for the focused live-task/stopped-task message-handoff check.

The test suite covers finite fan-out, task validation, nested-orchestrator
guards, parent-scoped registry state, terminal-state behavior, lifecycle hooks,
and frontend source contracts.

## License

MIT. See [LICENSE](LICENSE).
