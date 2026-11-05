# Studyroom — Agentic AI Tutor

A functional local MVP with FastAPI, a plain HTML/CSS/JavaScript interface,
six SQLite tables, OpenAI Responses API, and a conservative online LinUCB
intervention policy. Python agent modules are small cooperating functions;
there is no external agent framework.

## Run the offline demo

Use Python 3.11 or newer. From this directory:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Set `DEMO_MODE=true` in `.env`, then run:

```bash
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**. On Windows, activate with
`.venv\Scripts\Activate.ps1` and copy with `Copy-Item .env.example .env`.
No Node.js runtime or frontend build is needed to run the application.

The offline demo has scripted Binary Search teaching, a practice problem,
exact-answer grading, all five tool renderers, material ingestion/retrieval,
memory, reflection, and the online policy. Scripted teaching is visibly
labeled. Offline concept extraction uses Markdown headings and creates no
inferred prerequisite edges. It does not simulate arbitrary subject tutoring.

## Enable OpenAI

Edit `.env`:

```dotenv
OPENAI_API_KEY=your_key_here
OPENAI_MODEL=gpt-4.1-mini
DATABASE_URL=sqlite:///./tutor.db
DEMO_MODE=false
```

Choose a model available to your API account that supports Responses,
structured outputs, and function calling. Restart the server after changing
environment settings. A missing key produces a clear error when teaching is
requested; the application and its saved data remain accessible. The API key
is never returned to the browser. API errors do not silently switch to demo
answers.

`utils.call_llm` uses a lazily created `AsyncOpenAI` client, Responses
`create`/`parse`, Pydantic schemas, two SDK retries, an overall timeout,
`store=False`, an input character budget, and an output token budget.
The Tutor makes one forced, strict function call to the selected display
tool. The tool returns directly to the student, so no additional model turn
is needed to restate its output. Internal retrieval and verification run in
Python, without additional tool-selection model calls.

## Project files

```text
agentic-tutor/
├── backend/
│   ├── main.py             # HTTP, WebSockets, background tasks
│   ├── config.py           # Environment settings
│   ├── orchestrator.py     # Routing, snapshots, stale/duplicate checks
│   ├── observer.py         # Temporal features, guards, LinUCB
│   ├── planner.py          # Current plan, longer-term goals, action selection
│   ├── tutor.py            # Grounded teaching and function calls
│   ├── assessment.py       # Exact checks or provisional reasoning review
│   ├── reflection.py       # Evidence, rewards, session summaries
│   ├── memory.py           # Compact memory operations
│   ├── student_model.py    # Mastery and misconception hypotheses
│   ├── knowledge.py        # PDF/TXT/MD ingestion, BM25, prerequisite graph
│   ├── tools.py            # Five display tools and four internal tools
│   ├── db.py               # All six SQLAlchemy models
│   └── utils.py            # Shared schemas, OpenAI helper, metrics
├── frontend/
│   ├── index.html
│   ├── styles.css
│   └── app.js
├── data/materials/         # Original materials + chunk/index JSON at runtime
├── tests/
│   ├── conftest.py
│   ├── test_system.py
│   ├── test_openai_integration.py
│   ├── test_browser.py
│   └── frontend.test.cjs
├── requirements.txt
├── requirements-tested.txt
├── package.json           # Optional frontend test dependencies only
├── package-lock.json
├── pytest.ini
├── .env.example
├── .gitignore
├── TEST_RESULTS.md
└── README.md
```

There was no existing repository in the supplied workspace. This is a fresh
implementation; no existing `problem_generator.py` or frontend was available
to reuse. Problem generation is implemented in `tools.py`.

## Database schema

All tables are defined in `backend/db.py`; there are no auxiliary tables.

| Table | Columns |
|---|---|
| `users` | `id` integer PK; `email` unique string |
| `sessions` | `id` integer PK; `user_id` FK; `state` JSON; `created_at` datetime |
| `events` | `id` integer PK; `session_id` FK; `type` string; `data` JSON; `created_at` datetime |
| `student_state` | `user_id` PK and FK; `data` JSON; `updated_at` datetime |
| `concepts` | `id` integer PK; `name` unique string; `data` JSON |
| `concept_edges` | `from_id` and `to_id` FKs, composite PK |

`sessions.user_id` is indexed; events have a `(session_id, id)` index for
ordered retrieval. SQLite enables foreign keys, WAL, and a busy timeout.
Database triggers reject event updates and deletes. Related changes use
transactions. State writes assign newly validated JSON objects, avoiding
SQLAlchemy's nested-JSON mutation tracking pitfall.

Session JSON stores the active problem and private answer rubric, workspace,
current plan, observer window, revision, and preferences. Private answers and
rubrics are omitted from public session responses. Student JSON stores mastery,
goals, evidence-linked misconception hypotheses, up to 30 summary memories,
intervention aggregates, and the two LinUCB matrix/vector pairs. Event history
is queried with a bounded window instead of copied into memory JSON.

## End-to-end behavior

1. A session is created or resumed from the saved browser session ID. One
   development user owns local sessions. Course material is shared across this
   local user's sessions.
2. Workspace edits are debounced for one second; visible-tab inactivity events
   arrive every 20 seconds. Attempts and explicit questions are sent immediately.
   Chat remains editable while model work runs in a background asyncio task.
3. The Observer updates temporal features without an LLM. It defers during
   typing, applies user preferences/cooldowns, and aggregates decisions over
   configurable time windows. A WAIT or DEFER produces no proactive agent job.
4. Explicit questions bypass the proactive gate. A WAKE invokes the
   Orchestrator, which loads compact memory, course chunks, prerequisites, and
   relevant history. It creates a plan if needed, selects an intervention, and
   asks the Tutor to call the chosen tool.
5. Before saving/delivering proactive output, the Orchestrator rereads the
   session and checks its revision, current problem, preferences, and end state.
   Duplicate trigger IDs are rejected. Client-side unsent edits also suppress
   proactive cards. Highlight ranges must match the delivery-time source.
6. Exact-answer attempts use the server-side reference. `Check reasoning`
   requests provisional LLM review in live mode; it cannot create verified
   progress or policy rewards. Repeating a solved problem does not inflate
   mastery. `Record test run` records an unverified report from the student's
   own environment; it never executes their code.
7. A usable later outcome or linked feedback triggers deterministic Reflection.
   The session model, compact memory, and policy update in one transaction.
   A negative outcome can trigger a revised plan. Session completion performs
   one bounded synthesis call in live mode and saves a summary and next goal.

## Teaching tools

| Tool | Structured behavior |
|---|---|
| Hint | One small clue; live instructions forbid revealing the final answer |
| Explanation | Concept explanation using retrieved context |
| Highlight | Target, source ID, exact quote, Unicode start/end offsets |
| Visualization | Validated nodes, directed edges, and ordered trace steps |
| Guiding Question | A question asking for independent reasoning |

Highlighting marks the workspace preview or the selected material chunk.
Visualizations render safe SVG elements plus trace steps; text labels and
edge directions remain available as text. The UI never inserts model content
using `innerHTML`. Retrieved source names are displayed as context, which
does not claim every retrieved passage supports every model statement.

The four internal functions are `retrieve_knowledge`, `get_prerequisites`,
`verify_solution`, and `generate_problem`. No-intervention is a planner/policy
decision, not a sixth student-facing tool.

## Observer and online learning

Features: time since verified progress, time since activity, failure streak,
edit frequency, reverts, time on problem, recent hints, concept mastery, and
time since intervention. These are normalized and clipped, with an intercept.

Cold start uses `failure_streak >= 3` and `time_since_progress >= 90` seconds.
Before 12 usable feedback updates, the rule baseline is used. Afterwards,
LinUCB compares WAIT and WAKE scores with a conservative WAKE margin. The
same struggle thresholds, typing guard, user preference, pending-output guard,
and 120-second cooldown continue to apply. This deliberately limits exploration.

The default learning signal is +0.5 for an exact correct outcome, -0.25 for
an exact incorrect outcome, or the student's linked +1/-1 feedback. An eligible
WAIT decision can receive the same later exact-outcome proxy; easy/background
WAIT events do not train the policy. Each pending decision is consumed once.
No reward is assigned to provisional LLM review, expired windows, or attempts
submitted before the intervention. A new explicit help request censors the
old window. Unanswered decisions expire after ten minutes. Interrupted pending
wakes are cleared at application startup.

These rewards are noisy observational associations, not causal estimates.
Policy parameters persist across sessions and restarts in `student_state.data`.
`observe`, `vector`, `scores`, and `update_policy` are the replacement boundary
for a future trained estimator; no GRU or pretrained weights are required.

## Knowledge ingestion

Upload UTF-8 TXT, Markdown, or text-based PDF files up to 8 MiB. PDF extraction
is limited to 150 pages and text to 600,000 characters. Original bytes and
overlapping chunks are stored under `data/materials/` using generated IDs;
user filenames are display labels, never filesystem paths. Retrieval uses
BM25 over disk chunks. Live concept extraction produces a bounded graph;
unknown/self edges and directed cycles are rejected. Seed material and
reviewable prerequisites are provided for Binary Search.

## Tests

```bash
python -m pytest -q
```

Optional frontend DOM tests require Node.js 20 or newer:

```bash
npm ci
npm test
```

Optional real-browser test:

```bash
python -m pip install playwright
python -m playwright install chromium
RUN_BROWSER_TESTS=1 python -m pytest -q tests/test_browser.py
```

Set `BROWSER_EXECUTABLE` to use a particular installed Chromium binary.
On PowerShell set `$env:RUN_BROWSER_TESTS='1'` before running pytest.

Optional **paid** OpenAI smoke test:

```bash
RUN_OPENAI_INTEGRATION=1 python -m pytest -q tests/test_openai_integration.py
```

Normal tests mock the OpenAI boundary and never require paid calls. See
`TEST_RESULTS.md` for executed checks and environment limitations.

## Metrics and API

`GET /api/metrics` exposes process-lifetime Observer WAIT/DEFER/WAKE counts,
Observer total/mean processing latency, intervention counts, stale rejections,
LLM request counts, SDK-reported input/output token totals, errors, and summed
API wall latency. Subtract snapshots over a time interval to calculate
interventions per minute. API latency includes SDK retries; `llm_calls` counts
logical helper calls, not individual HTTP retry attempts. Metrics reset on
server restart; append-only events and the learned policy persist.

Core routes: `POST /api/sessions`, `GET /api/sessions/{id}`,
`POST /api/sessions/{id}/problem`, `GET /api/student`,
`GET/POST /api/materials`, `GET /api/concepts`, and `/ws/{id}`.
Interactive HTTP schemas are available at `/docs`.

## Practical limits

- This is a local, single-user MVP. Run **one Uvicorn worker** bound to localhost.
  Authentication, ownership isolation, durable background queues, and cross-worker
  coordination must be added before multi-user or public deployment.
- A browser refresh resumes saved work and recent chat. Events accepted before
  a process crash persist, but unfinished model jobs are not replayed.
- The policy implementation is functional; its effectiveness has not been
  established on student data. Thresholds and reward proxies need evaluation.
- Exact matching verifies agreement with a reference answer, not independent
  conceptual mastery. Generated reference answers can be wrong; review them for
  high-stakes use. LLM reasoning judgments and misconception hypotheses remain
  provisional. Mastery values are heuristic scores, not calibrated probabilities.
- The offline demo's teaching examples cover Binary Search only. Arbitrary
  concept tutoring, diagnostic review, graph extraction, problem generation,
  and session synthesis require a working API key in live mode.
- Scanned PDFs need OCR before upload; PPTX ingestion is not implemented.
  Disk BM25 is intended for a small course library, not a large corpus. Uploaded
  graph extraction uses the first 24,000 characters; extracted edges need review.
- Student code is never run. Reported tests remain unverified. A sandbox service
  can be integrated later without changing the five teaching tools.
- Input context is bounded and can be truncated for large histories. LLM output
  validation/refusal errors are reported instead of displaying malformed tools.
- Browser layout/end-to-end and live paid OpenAI checks remain unverified in
  the build environment; both optional test paths are included.

## API references used

- [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling)
- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
