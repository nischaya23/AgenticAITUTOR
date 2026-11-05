# Executed validation — 5 November 2026

## Python

Command: `python -m pytest -q`

**25 passed, 2 skipped, 2 dependency deprecation warnings; 1.51 seconds.**

The two skipped tests are explicitly opt-in: a paid OpenAI call and a Chromium
browser check. The warnings originate in installed AnyIO and Starlette test
dependencies; the application tests passed.

Coverage includes:

- Exactly six database tables, foreign keys, append-only triggers, JSON
  persistence, and reopening the database through a fresh engine.
- All five validated intervention tools, structured highlights/graphs,
  strict function definitions, and invalid tool metadata rejection.
- Explicit WebSocket tutoring with proactive help disabled, saved plans/goals,
  and reloaded history.
- WAIT without invoking agents or LLMs; WAKE through the Orchestrator.
- Rule fallback, typing deferral, cooldowns, preferences, expired windows,
  learned WAIT selection, and persisted LinUCB updates.
- Stale and duplicate intervention suppression.
- Exact assessments, heuristic mastery, no repeated-correct-answer inflation,
  evidence-linked reflection, one-time feedback, and negative-outcome replanning.
- No bandit credit for provisional judgments or pre-intervention attempts.
- Material ingestion, BM25 retrieval, graph storage and cycle prevention.
- Input validation, private-answer filtering, no execution of student code,
  problem generation, and session summary persistence.
- The production OpenAI helper with mocked SDK-shaped parse/function outputs,
  model configuration, `store=False`, and token/call metrics. No paid calls.

## Frontend DOM tests

Command: `npm test`

**4 passed, 0 failed; 1.49 seconds.**

Checks cover all five intervention renderers, SVG graph/trace output, Unicode
highlight offsets, flushing edits before explicit requests, problem IDs on
attempts, reasoning-assessment messages, safe text rendering, and suppression
of proactive cards when unsent local edits exist. Transport is mocked using
JSDOM; this does not establish browser layout correctness.

JavaScript syntax and Python compilation checks also passed.

## Browser and live API limits

The optional Chromium check was attempted. The environment prevented Chromium
from creating its required socket (`Operation not permitted`). Browser download
was also unavailable, and a separate browser could not reach the isolated local
server. Therefore no full-browser pass or visual layout verification is claimed.
`tests/test_browser.py` remains available to run in a normal local environment.

No user OpenAI key was supplied. The paid live integration test was not run.
`tests/test_openai_integration.py` provides an opt-in structured Responses smoke
test. Mocked integration verifies the application's SDK request/response wiring,
not external API availability or live model teaching quality.

## Observer microbenchmark

10,000 independent synthetic observations exercised the three branches with a
fixed timestamp, using the production feature extraction and cold-start policy.
This excludes database/network work and is not a student behavior simulation.

| Measure | Result |
|---|---:|
| Mean Observer processing time | 0.0320 ms |
| p95 Observer processing time | 0.0660 ms |
| WAIT observations | 3,334 |
| DEFER observations | 3,333 |
| WAKE observations | 3,333 |
| LLM calls | 0 |

The synthetic distribution is chosen by the benchmark, not a measured production
intervention rate. For live runs, `/api/metrics` reports action counts, intervention
counts, LLM helper calls, token usage, API latency, and Observer mean latency.
No live API latency, actual token cost, or learning-effectiveness result is claimed.
