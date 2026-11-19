# Durable orchestration validation

Date: 2026-11-18. Base: `f7b278dd9d107b0663393f1aa48e500618dc54fe`.
Plugin version: 2.1.0. This file records validation evidence; GitHub records publication status.

## Evidence

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

No live server deployment, remote provider job, or dependency installation was
part of this validation. The existing server was not restarted or
configured. Test copies ran in a separate temporary directory using its
framework interpreter.

Full local model/tool checkpoint replay is not implemented or claimed. Interrupted
local execution holds for review. A separate verified engine integration is
required to resume those turns. Remote tests use deterministic collaborators;
they establish submit/poll/uncertainty behavior, not acceptance by a real remote.

The browser's API and WebSocket collaborators are synthetic. Its checks establish
rendering and control payloads, not live transport integration. Runtime state and
workspaces are retained without automatic deletion; storage retention is manual.
