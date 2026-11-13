# Simulated-learner evaluation

This folder contains a standalone evaluation harness and the evidence from the
November 13, 2026 time-limited pilot. It is separate from application runtime code.
Nothing in this folder is imported by the tutor. No production code was changed to
perform the evaluation.

Start with [REPORT.md](REPORT.md) for findings and
[REPORT_WRITING_BRIEF.md](REPORT_WRITING_BRIEF.md) for instructions for a report writer.

## Included files

- `evaluate.py`: real API/WebSocket evaluation, fixed learner profiles and reference course.
- `test_harness.py`: offline checks of isolation boundaries, grading/profile separation,
  request pacing, event handling, timeouts, and reporting.
- `summarize.py`: combines selected source runs and applies documented inspection exclusions.
- `manual_audit.json`: two simulator exclusions and two unreliable-reference judgments,
  identified through agent inspection rather than human adjudication.
- `REPORT.md` and `SUMMARY.json`: combined, inspected live results.
- `evidence/runs/`: original structured results, historical per-run reports, and live
  application transcripts. Per-run reports precede inspection; use the combined report
  for final metrics. The offline run is validation evidence, not live tutor performance.
- `EVIDENCE_MANIFEST.json`: file hashes, run purposes and export provenance.

Databases, credentials, original material storage, raw provider logs and Python caches
are omitted. Source results and included transcripts are preserved byte for byte.
All learners are synthetic. New runs and their private logs are ignored under `.local-runs/`.

## Reproduce the archived combined report

From the repository root, after installing its existing Python requirements:

```bash
python tsting/test_harness.py
python tsting/summarize.py \
  tsting/evidence/runs/20261113-211454-live-bb4684/results.json \
  tsting/evidence/runs/20261113-212045-live-9f1a92/results.json
```

This regenerates `REPORT.md` and `SUMMARY.json` without model calls. It does not
replay the experiment. The archived report of nine harness checks and 26 synthetic
answers records what was tested during the original pilot. Packaging checks may
add further tests without changing that historical count.

## Run a new experiment

Use the repository virtual environment (for example `.venv/bin/python` instead of
`python` below) and configure the provider through the existing root `.env` file.

```bash
python tsting/evaluate.py --offline
python tsting/evaluate.py --live
```

`--live` uses the configured provider/model and may incur API costs. It creates its
own database, materials and logs; it does not use the application's existing database.
The learner sees its assigned knowledge and public question only. A blinded grader
sees the reference course, question and answer without the learner profile. A separate
profile checker sees the profile and answer without reference-course or grading data.

**Version distinction:** archived live runs used the earlier combined grading/profile
check. The current harness separates those calls after inspection exposed evaluator
errors. The improved variant was not used to produce the archived live evidence;
do not claim it was validated by those live runs.

The default pilot uses three profiles (novice, mixed, strong), three concepts
(boundaries, lower bound/duplicates, complexity), and up to four diagnostic answers per
profile/concept. Each session first requests a hint and an explanation on an unanswered
draft, then skips that probe. Diagnostic answers do not receive that help or earlier
feedback. Learner knowledge stays fixed; this does not measure learning gains.

A shorter diagnostic experiment can use:

```bash
python tsting/evaluate.py --live --answers 1 --profiles strong novice mixed \
  --skip-help-probes --max-runtime 420
```

`--units`, `--profiles`, `--answers`, `--seed`, `--timeout`, `--max-calls`,
`--request-interval`, and `--initial-cooldown` control coverage and limits.
Default bounds are 45 minutes, 350 calls checked between operations, and a 240-second
WebSocket wait. In-flight backend calls/retries can exceed these soft bounds; the
historical shortened run lasted 427.3 seconds against a 420-second soft budget.
On systems providing GNU `timeout`, wrap a run with `timeout --signal=INT 450` for an
additional external stop. Interrupted assessments are never automatically resubmitted.

A shared transport pacer spaces actual tutor/learner/evaluator attempts and SDK retries
by five seconds. It was added after the provider reported a 15-requests/minute quota;
provider quotas may differ by account/model. Latency includes evaluation pacing and
should not be reported as ordinary application responsiveness. The full default pilot
can take tens of minutes. Seeds reproduce scenario ordering, not model sampling.

The real tutor handles planning, question generation, help, grading, reflection,
hypotheses, persistence and progression. Fixed material graphs are seeded in the
isolated environment. Browser UI, live research, course extraction, prerequisite
unlocking and automatic Observer behavior are outside this experiment. Offline mode
replaces model calls with the repository's synthetic fixture and makes no paid calls.

Inside the restricted Codex sandbox, even a minimal FastAPI `TestClient` hung. Full
integration/live runs were performed with external execution permission. Ordinary
unit checks and report regeneration need no network access.

When regenerating a report for new source runs, inspect the raw evidence before adding
new exclusions. Historical audit entries are keyed to specific question IDs and should
not be applied to unrelated data. Retain raw outputs and explain every exclusion.
