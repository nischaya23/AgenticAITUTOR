# Workflow tracing

The frontend shows preparation stages and up to four recent tutor steps, with the responsible agent or component on each step. Tutor replies stay in the conversation. Slideshows arrive as ordinary teaching replies and render in React Flow. The read APIs below expose detailed traces.

## What is recorded

- SQLite stores runs, spans, events and artifacts in `workflow_runs`, `workflow_spans`, `workflow_events` and `workflow_artifacts`. Workflow events and artifacts are immutable. Identical public artifacts can share storage within an account.
- Independent jobs have separate runs. Nested spans capture agent stages, provider waits and attempts, and tool execution. Dependencies link a stage to the plan or hypothesis version it actually used. Run outcomes include completion, warnings, failure, cancellation, stale work and restart interruption.
- Memory changes and their before/after artifacts commit together. The trace captures compacted model inputs with protected fields removed, including credentials, provider signatures, system instructions, private answers and rubrics. Retrieved passages, passages included in context and explicit citations are tracked separately.
- Preparation runs attach to their material, including runs for clarification or retry. Sessions can reference that history. Application event IDs link saved teaching replies to their workflow runs.

## Read APIs

All routes require an authenticated account and enforce ownership.

| Endpoint | Returns |
| --- | --- |
| `GET /api/sessions/{id}/workflow?before={cursor}&limit=20` | Paginated history, active runs, stream watermark and tracking status |
| `GET /api/sessions/{id}/workflow/events?after={cursor}&limit=200` | Ordered replay, next cursor and `has_more` |
| `GET /api/workflow/runs/{id}` | Spans, dependency edges, events and artifact summaries |
| `GET /api/workflow/artifacts/{id}` | Immutable, redacted artifact contents |
| `GET /api/operations/{id}/workflow` | Preparation run history and replay |

## Live delivery

The backend sends committed `workflow_events` batches on `/ws/{id}?workflow=1`. The frontend uses selected events for the tutor progress flow and reloads the saved conversation and study state on reconnect. Clients that need detailed replay can use the session events API.

Preparation progress streams from `/api/operations/{id}/events`. Operation events use `after` or `Last-Event-ID`, and trace batches use a separate `workflow_after` cursor. The preparation panel displays stages from operation events. If tracking fails, tutoring continues and the backend exposes tracking status separately.

## Verification

Run `python -m pytest -q tests/test_workflow.py` and `npm test` from the repository root. Backend tests cover trace privacy, version lineage, transactions, pagination, ownership and delivery. Frontend tests cover the progress flow and saved conversation.
