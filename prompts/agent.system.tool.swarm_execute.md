### swarm_execute

Run a finite local command as a Swarm worker with a durable execution receipt.
Use this instead of code_execution_tool for independent batch commands when
restart recovery matters. It runs in the worker's current project/workdir on the
Agent Zero host/container. It does not use SSH, an interactive shell, or persistent
Python variables. Files and installed dependencies in the workspace persist.

Arguments: `runtime` (`terminal`, `python`, or `nodejs`), `code` (string), and
`timeout` (seconds, default 600, maximum 1800). Issue it alone in a model turn.
Only local Swarm workers can use it. Ordinary parent chats must use their normal tools.

Agent Zero process restarts reconnect to the original command and reuse its
output. A crashed command runner with no receipt is held for review, never silently
rerun. Cancellation or timeout kills its process group but cannot undo external
effects. Do not launch daemons, detached background processes or interactive tasks
through this tool. Do not issue the same command again merely because the UI disconnected.
