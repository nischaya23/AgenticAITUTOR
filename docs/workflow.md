# Agentic workflow

The study panel combines preparation, independent background reviews, teaching,
assessment, memory changes, research, and video delivery. Teaching messages stay
visible with their activity cards collapsed. Older messages with no trace remain
readable and say that detailed activity was not recorded.

The default view groups repeated memory reads and adjacent unchanged numerical
Observer decisions. All, Agents, Tools, Memory, Context, and Errors filters apply
to recorded activity. Graph nodes and timeline steps share a public inspector.
Artifacts load when opened. Scrolling away from the latest activity preserves the
reading position and exposes a New activity button.

## Storage and lineage

SQLite has four additive tables: `workflow_runs`, `workflow_spans`,
`workflow_events`, and `workflow_artifacts`. Existing application events and
operation history remain intact. Workflow events and artifacts are immutable at
the database level. Artifact identities include the owning account, artifact kind,
and public contents, allowing repeated snapshots to share storage within that
account. Spans and event references retain artifacts with their session.

Each independent worker owns a run. An immutable ContextVar trace is explicitly
bound in queued background workers and operation jobs. Nested spans describe agent
stages, provider-slot waiting, model requests, tool execution and rendering. Runs
record queued/running work, completion, completion with warnings, failure,
cancellation, discarded stale work and restart interruption separately. Existing
teaching/grading locks, review debounce intervals and provider concurrency limits
continue to govern execution. Tracing does not call models.

Dependencies include structural parent edges, preceding stages in the same task,
and exact consumed plan/hypothesis artifact versions, including across runs.
An Observer reading version 3 links to version 3 even when version 4 completes
first. Background run triggers retain event/snapshot references. Video operation
runs remain active through rendering and delivery; a script-generation run can
finish while the video operation continues.

Memory changes and their before/after artifacts join the memory transaction;
rolled-back changes do not appear as committed updates. Read evidence is buffered
locally until a tracing boundary so read-only context sessions can close without
losing their evidence or unnecessarily acquiring SQLite write locks. Workflow and
memory inspection GETs create no workflow runs or memory activity.

Provider-bound input snapshots are captured after context compaction. The public
projection excludes grading references/rubrics, system instructions, policy,
credentials, provider envelopes/signatures and hidden reasoning. Context artifacts
retain response, plan, memory and hypothesis versions, included passages, budget
omissions and differences from the previous model input. Retrieval, model-input
inclusion and explicit output citations are separate records; retrieval alone is
never described as evidence of internal source use. Public payloads are bounded;
any storage-budget omission is explicit.

Preparation runs link to their material, including earlier clarification/retry
runs for the same operation. Sessions reference that material history without
copying it. Application event IDs link messages and videos to their workflow run
so delivered outputs appear once.

## Read APIs and delivery

All routes require the existing authenticated account and check ownership.

- `GET /api/sessions/{id}/workflow?before={cursor}&limit=20`: completed history,
  all active runs regardless of pagination, next cursor, stream watermark and
  tracking completeness.
- `GET /api/sessions/{id}/workflow/events?after={cursor}&limit=200`: ordered replay
  with a continuation cursor and `has_more`.
- `GET /api/workflow/runs/{id}`: spans, dependency edges, committed events and
  artifact summaries.
- `GET /api/workflow/artifacts/{id}`: immutable public artifact contents.
- `GET /api/operations/{id}/workflow`: preparation/rendering history and replay.

One dispatcher reads committed workflow events in cursor order and sends batches
as `workflow_events` on the authenticated WebSocket (`/ws/{id}?workflow=1`).
The query flag opts the migrated UI into activity batches while legacy socket
clients continue to receive their existing messages. Clients reconcile snapshots
and replay missed events on reconnection, using stable event/run IDs to deduplicate.
Operation SSE carries workflow batches with an independent workflow cursor; clients
can pass `workflow_after` alongside the existing `after`/Last-Event-ID cursor.
Legacy operation progress and socket messages remain compatible.

Tracking errors are fail-open. Tutoring continues, and the panel reports incomplete
activity rather than claiming successful tracking. No external observability
service or additional runtime dependencies are required.

## Verification

`tests/test_workflow.py` covers task isolation, provider-bound projections, queue
measurements, commit/rollback visibility, immutable snapshots, consumed-version
lineage, outcomes, pagination, account isolation, preparation attachment, ordered
streaming and fail-open tutoring. Frontend DOM tests cover concurrent run headers,
graph keyboard selection, artifact inspection, filters, grouping, scroll position,
new activity and single delivery of linked messages, alongside existing study UI
coverage.

Run `.venv/bin/pytest -q` and `npm test` from the repository root. Paid provider and
Chromium browser tests retain their existing opt-in environment flags.
