# A0 Swarm

A0 Swarm gives Agent Zero a bounded parallel-delegation tool and a live,
chat-scoped mission-control panel. A parent agent can send independent tasks
to local background agents or configured Agent Zero remotes over A2A, collect
their results, exchange messages, and cancel work.

Version 2.0 hardens the original plugin around three operating rules:

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
as its own. The temporary checkout is removed after the task, but its branch
remains in the parent repository for review and merge. Selecting this mode
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

The registry is process memory, not durable chat history. Restarting Agent Zero
clears live swarm state. Remote tokens remain in local plugin configuration and
are never included in panel snapshots.

## Development

From the plugin repository:

```bash
python -m unittest discover -s tests -v
node --check webui/swarm-store.js
node --check extensions/webui/send_message_before/_10_swarm_mentions.js
python -m compileall -q .
```

The test suite covers finite fan-out, task validation, nested-orchestrator
guards, parent-scoped registry state, terminal-state behavior, lifecycle hooks,
and frontend source contracts.

## License

MIT. See [LICENSE](LICENSE).
