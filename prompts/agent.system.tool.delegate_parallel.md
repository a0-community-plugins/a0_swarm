### delegate_parallel
Spawn multiple subagents to work on independent tasks in parallel. Each agent runs concurrently in a separate Agent Zero chat context and returns its result when done. Filesystem isolation depends on the configured workspace mode. Use this when a task decomposes into independent subtasks with no sequential dependency between them.

All tasks in one `delegate_parallel` call share a single swarm run. Agents in the same run can message each other and the orchestrator with `swarm_message`. Use meaningful `label` values — the swarm panel uses them for observability.

Use one orchestration layer at a time. Do not call this tool from an A0 Swarm worker or while Agent Harness is active in the same chat. Fan-out is bounded by plugin settings (four tasks by default, never more than 16).

Args:
- `request_id` (string): stable unique identity for a new run; reuse exactly when retrying the same admission.
- `run_id` (string): reattach to an existing run or collect its results; omit tasks in this mode.
- `tasks` (list, required for a new run): array of task objects. Each object:
  - `label` (string, required): human-readable name shown in the swarm panel (e.g. "Researcher", "Code Writer").
  - `task` (string, required): full task description for that agent.
  - `profile` (string, optional): Agent Zero profile for a local subagent.
  - `endpoint` (string, optional): route this task to a configured remote A2A Agent Zero instance by its settings `label` or a full `http(s)://host:port` URL. Test remotes in Plugin Settings before relying on them.

Returns: a structured markdown summary of all agent results once every agent completes.

When `subagent_workspace` is `inherit` or `isolated`, each sub **already starts in the correct directory** (its activated project / worktree). Do NOT put an absolute `cd /a0/usr/projects/...` into a sub's `task` — phrase the work to run in the sub's **current** directory. A hardcoded `cd` makes the sub leave its workspace and write into the shared checkout, defeating isolation.

When `subagent_workspace=isolated`, the worker's worktree and branch remain for review, including uncommitted files. Do not claim all work is committed or remove the checkout automatically.

If the tool reports Needs review, inspect that run in the Swarm panel. Do not create a replacement run or silently retry uncertain actions. Use `run_id` to reattach. Completed results survive restart. Local workers warm-resume from supported checkpoints and reuse saved model/tool results. Prefer swarm_execute for finite local commands; it reconnects to the original command after a server restart. Unknown ordinary-tool outcomes remain held for review. A restarted parent collects with run_id rather than admitting a replacement run.

Example:
~~~json
{
  "thoughts": ["I'll split this into research and implementation tasks."],
  "tool_name": "delegate_parallel",
  "tool_args": {
    "request_id": "research-implementation-1",
    "tasks": [
      {"label": "Researcher", "task": "Research the top 5 Python async frameworks and summarize their tradeoffs."},
      {"label": "Implementer", "task": "Write a working FastAPI hello-world server with JWT auth."}
    ]
  }
}
~~~
