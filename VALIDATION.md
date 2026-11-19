# Durable orchestration validation

Date: 2026-11-18. Base: `f7b278dd9d107b0663393f1aa48e500618dc54fe`.
Plugin version: 2.1.1. This file records validation evidence; GitHub records publication status.

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

Full local model/tool checkpoint replay is not implemented or claimed. Interrupted
local execution holds for review. A separate verified engine integration is
required to resume those turns. Remote tests use deterministic collaborators;
they establish submit/poll/uncertainty behavior, not acceptance by a real remote.

The pre-publication browser's API and WebSocket collaborators are synthetic. Those checks establish
rendering and control payloads, not live transport integration. Runtime state and
workspaces are retained without automatic deletion; storage retention is manual.
