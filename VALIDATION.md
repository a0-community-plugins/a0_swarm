# Swarm recovery validation

Date: 2026-11-18. Base: `f7b278dd9d107b0663393f1aa48e500618dc54fe`.
Plugin version: 2.2.0. This file records validation evidence; GitHub records publication status.

## Warm recovery (2.2.0)

- Existing suite plus the first three recovery cases: 27 passed. After refining
  error handling, the four focused recovery cases passed (1.5 seconds).
- `scripts/verify_warm_recovery.py --framework /a0` passed with the real Python
  3.12.4 framework, enabled extension discovery, native Responses function calls,
  actual tool execution and chat restoration. One worker survived synchronized
  SIGKILLs after its model receipt and during its command: same context, attempt
  1, recovery count 2, two model calls total, one command effect. Model output and
  prompt text were deterministic; this check made no external provider calls.
- The initial fixture exposed an incorrect native workdir translation. It was
  corrected before the passing run. No core files or dependencies were changed.
- The focused cases cover bounded recovery, stale-attempt rejection, duplicate
  command prevention, missing runner outcomes, cancellation before dispatch, and
  exiting the framework retry loop when tool effects are unknown.

The first live worker declined the worker-only command because its generic Agent
Zero prompt described a parent role. No command ran. The plugin now supplies an
explicit runtime worker role; acceptance after that correction is recorded below.

The corrected candidate was installed through the normal Plugin Hub update API
in the launcher instance at `http://127.0.0.1:60030/`, Agent Zero v2.16 at core
`2b0b3a416d80638379dc575798a4174ab4d3e1d9`. Its configured provider-backed worker
started one `swarm_execute` Python command, wrote a marker, and waited. The normal
restart API changed the server PID while the original detached command stayed
alive. Startup restored the worker automatically; it consumed the original
command receipt and returned `SWARM_WARM_RECOVERED_20261118`.

- Same worker context `EhFYcgz2`, attempt **1**, recovery count **1**.
- Command completed with exit code **0**; marker content remained exactly **x**.
- The aggregate result was ready to collect; all contexts were idle afterward.
- Native Responses history and provider state were retained in the checkpoint.
  Provider cache hits and latency savings were not measured.
- After browser reload, the panel showed **Done / Attempt 1 / Recovered 1**.
  Swarm was the only visible canvas surface; the console had no errors.
- CI passed on Python 3.11, 3.12 and 3.13. No remote A2A provider, machine power
  loss, storage loss or interactive-session restoration was tested.

The one corrected live run is the restart acceptance check. It was not repeated
after passing. The first declined command and initial fixture path correction
remain documented above rather than being counted as successful checks.


## Live integration correction (2.1.1)

On November 18, 2026, the published plugin was installed through the Plugin Hub
in a running launcher-managed Agent Zero v2.16 instance, core commit
`2b0b3a416d80638379dc575798a4174ab4d3e1d9`. One real provider-backed run admitted
two local workers: an immediate response and a worker that wrote one marker
before waiting. The normal restart API exposed a startup failure: `SwarmRestore`
returned an awaitable to synchronous extension dispatch. Both function lifecycle
hooks (`initialize_chats` and `toggle_plugin`) now use synchronous `execute`.

After applying that correction, normal startup and chat restoration succeeded.
The same completed result, worker context IDs and attempt numbers survived;
the interrupted worker became `needs_review`. Its marker remained exactly one
line, no worker was running, and no retry was issued. The live Swarm panel
reconnected without console errors. This is local-worker acceptance; no remote
A2A provider was exercised.

The integrated UI also exposed an overlapping Code Intelligence panel caused by
its `display: contents` wrapper. That independent plugin now preserves the canvas
surface box. It is not a Swarm CSS change.

## Pre-publication evidence (2.1.0)

- Plugin unittest discovery: **24 passed**, including six durability cases and the existing registry/task/repository checks.
- After lifecycle/ownership refinements: the six durability cases passed again. After fixing hide-before-aggregation, the affected admission/result receipt case passed individually.
- `scripts/verify_framework.py`: **passed** real `AgentContext.run_task` process-kill/reopen with a synthetic monologue, retained chat/work, and a separate local-cancellation termination receipt check.
- Framework interpreter: Python 3.12.4 in an existing Agent Zero image. Framework `agent.py` SHA-256: `fd7fa584f2cdf7bf483a10542735853ffe65cacf9e2a701009f853d488b40b2e`.
- Browser: actual panel/store and Alpine rendered in an isolated synthetic page. Recovery retry acknowledgement, known-remote status check, result collection, cancellation request payloads, chat-switch isolation, reload state, and stale-revision rejection passed.
- Pre-publication review: `scripts/verify_framework.py --delivery` passed an actual framework task handoff, worker acknowledgement, and a stale handoff after termination without starting another turn.
- Changed store JavaScript syntax, all plugin Python AST parsing, and Git whitespace checks passed.

## Corrections established by those checks

Core excludes `BACKGROUND` contexts from persistence. Workers now use inertly
restored `TASK` contexts. Cancellation waits for the worker wrapper to finish,
not merely for its Future to report cancellation. Clearing a completed run
commits its aggregate result before hiding it, preserving replay receipts.

The framework fixture suppresses extension discovery and prompt rendering,
uses temporary state, and substitutes the model turn with synthetic work. It
exercises real framework task/context ownership and chat persistence. It does
not establish live enabled-plugin discovery or provider acceptance.

## Limits

The pre-publication checks used separate temporary copies with the framework
interpreter. The live integration correction above subsequently installed the
plugin and restarted the named server. No dependencies were installed.

Version 2.2 supports the worker checkpoints and finite batch-command adapter
described in README. Arbitrary in-flight tools, volatile interactive sessions,
unknown command outcomes and incompatible core changes remain held. The 2.1
evidence above predates this adapter. Remote tests use deterministic collaborators;
they establish submit/poll/uncertainty behavior, not acceptance by a real remote.

The pre-publication browser's API and WebSocket collaborators are synthetic. Those checks establish
rendering and control payloads, not live transport integration. Runtime state and
workspaces are retained without automatic deletion; storage retention is manual.
