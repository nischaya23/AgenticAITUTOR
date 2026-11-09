# Studyroom — Agentic AI Tutor

A local tutor with FastAPI, React, TypeScript, and Vite,
SQLite, email/password accounts, OpenAI Responses API, and a conservative online LinUCB
intervention policy. Python agent modules are small cooperating functions;
there is no external agent framework.

## Run the tutor

Use Python 3.11 or newer and Node.js 20.19+ or 22.12+. From this directory:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Configure a live provider in `.env` as described below, then run:

```bash
npm ci
npm run build
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**. On Windows, activate with
`.venv\Scripts\Activate.ps1` and copy with `Copy-Item .env.example .env`.
FastAPI serves the production build from `frontend/dist`. For development,
keep FastAPI running on port 8000 and run `npm run dev` in a second terminal.
Open **http://127.0.0.1:5173**. Vite proxies `/api` and `/ws` to FastAPI.

Upload learning material to generate a concept graph and begin. New sessions start without a preloaded question. All teaching, grading, extraction, and reflection use the configured provider.

Create an account using **Sign up**, then use your email and password to log in.
Each account has its own uploads, study sessions, drafts, and learning memory.
The browser resumes the most recent study session for that account. Use **Log out**
in the header to end the login.

Passwords are hashed with scrypt and a unique random salt. Login tokens are stored
as hashes in SQLite and sent in an HttpOnly, SameSite cookie that expires after
seven days. Set `COOKIE_SECURE=true` when serving over HTTPS. The local HTTP
setup uses `COOKIE_SECURE=false`.

On startup, the app automatically adds the password column and the login and
material ownership tables to existing databases. Existing anonymous sessions and
uploaded files remain stored, but are not assigned to a new account. Re-upload
the materials you want to use under your account.

## Enable OpenAI

Edit `.env`:

```dotenv
OPENAI_API_KEY=your_key_here
LLM_PROVIDER=openai
OPENAI_MODEL=gpt-4.1-mini
DATABASE_URL=sqlite:///./tutor.db
```

Choose a model available to your API account that supports Responses,
structured outputs, and function calling. Restart the server after changing
environment settings. A missing key produces a clear error when teaching is
requested; the application and its saved data remain accessible. The API key
is never returned to the browser. API errors are reported; there is no scripted fallback.

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
│   ├── assessment.py       # Provisional combined-response assessment
│   ├── reflection.py       # Evidence, rewards, session summaries
│   ├── memory.py           # Compact memory operations
│   ├── student_model.py    # Mastery and misconception hypotheses
│   ├── knowledge.py        # PDF/TXT/MD ingestion, BM25, prerequisite graph
│   ├── tools.py            # Five display tools and four internal tools
│   ├── visualization.py    # Typed, validated visualization traces
│   ├── auth.py             # Signup, login, password hashing, cookie sessions
│   ├── db.py               # SQLite models and additive migrations
│   └── utils.py            # Shared schemas, OpenAI helper, metrics
├── frontend/
│   ├── index.html
│   ├── styles.css
│   └── src/
│       ├── main.tsx        # Study, chat, sessions, and WebSocket UI
│       ├── Workspace.tsx   # CodeMirror and range decorations
│       ├── Visualization.tsx # Playback and React Flow renderers
│       ├── SceneCanvas.tsx # General-purpose Konva scenes
│       └── types.ts
├── data/materials/         # Original materials + chunk/index JSON at runtime
├── tests/
│   ├── conftest.py
│   ├── test_system.py
│   ├── test_openai_integration.py
│   ├── test_browser.py
│   └── frontend.test.cjs
├── requirements.txt
├── package.json           # Frontend dependencies and commands
├── package-lock.json
├── tsconfig.json
├── vite.config.ts
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
| `users` | `id` integer PK; `email` unique string; nullable `password_hash` for legacy rows |
| `logins` | `token_hash` PK; `user_id` FK; `expires_at` Unix timestamp |
| `materials` | material `id` PK matching the disk index; `user_id` FK |
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

1. A signed-in student creates or resumes a study session. Session routes,
   WebSockets, material uploads, and learning memory are scoped to that account.
2. Workspace edits are debounced for one second; visible-tab inactivity events
   arrive every ten seconds for visible, unanswered questions. Attempts and explicit questions are sent immediately.
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
6. Grading uses the rubric,
   student reasoning, and retrieved context. A correct live assessment can
   advance the session. Accepted LLM assessments count as provisional progress
   or failure for the Observer; they never become verified policy rewards. Repeating a solved problem does
   not inflate mastery.
7. A usable later outcome or linked feedback triggers deterministic Reflection.
   The session model, compact memory, and policy update in one transaction.
   A negative outcome can trigger a revised plan. Answer reflection can save a
   summary and next goal during grading. Ending a session returns a fixed
   completion message.

## Teaching tools

| Tool | Structured behavior |
|---|---|
| Hint | One small clue; live instructions forbid revealing the final answer |
| Explanation | Concept explanation using retrieved context |
| Highlight | Multiple categorized workspace annotations with an exact quote, line, reason, and server-resolved range |
| Visualization | General-purpose animated scenes plus specialized search, traversal, and concept graph views |
| Guiding Question | A question asking for independent reasoning |

The model supplies quotes and line numbers. Python resolves and revalidates
the ranges against the workspace. CodeMirror displays decorations without
editing the document. Students can dismiss each annotation or ask about it
in tutor chat. New model annotations target the workspace.

Live tutoring can visualize any subject as a general 2D scene. The model
returns labeled shapes, arrows, and step-by-step changes to position, visibility,
and emphasis. FastAPI validates the objects and changes, then React Konva renders
them with the same playback controls as other step-based views. The visual
language is limited to these primitives, while subjects and processes can vary
with the study material. The browser loads Konva when a scene is first shown.

The Binary Search renderer uses Motion for React to animate the half-open interval
and its pointers. React Flow renders concept graphs, trees, and graph traversals
with pan, zoom, and draggable nodes. Step-based visuals provide previous, next,
restart, play/pause, and speed controls. Model outputs contain structured data
and text. All rendering logic lives in the application.

The internal helpers include `retrieve_knowledge`, `get_prerequisites`,
and `generate_problem`. No-intervention is a planner/policy
decision, not a sixth student-facing tool.

## Observer and online learning

Features: time since progress (including provisional accepted LLM grading), time since activity, failure streak,
edit frequency, reverts, time on problem, recent hints, concept mastery, and
time since intervention. These are normalized and clipped, with an intercept.

Cold start uses one incorrect assessment and 30 seconds without progress, or
60 seconds of inactivity.
Before 12 usable feedback updates, the rule baseline is used. Afterwards,
LinUCB compares WAIT and WAKE scores with a conservative WAKE margin. The
same struggle thresholds, typing guard, user preference, pending-output guard,
and 45-second cooldown continue to apply. The typing grace is three seconds
and aggregation interval is five seconds. The numerical policy can choose WAIT;
the planner can also decline help.

The default learning signal is +0.5 for an exact correct outcome, -0.25 for
an exact incorrect outcome, or the student's linked +1/-1 feedback. An eligible
WAIT decision can receive the same later exact-outcome proxy; easy/background
WAIT events do not train the policy. Each pending decision is consumed once.
No reward is assigned to provisional LLM review, expired windows, or attempts
submitted before the intervention. A new explicit help request censors the
old window. Unanswered outcome windows retire after the cooldown, recording no
reward, so the Observer can reevaluate help. Interrupted pending
wakes are cleared at application startup.

These rewards are noisy observational associations, not causal estimates.
Policy parameters persist across sessions and restarts in `student_state.data`.
`observe`, `vector`, `scores`, and `update_policy` are the replacement boundary
for a future trained estimator; no GRU or pretrained weights are required.

## Studying from the web

Instead of uploading files you can type a topic into **Study a topic from the web** and press
**Search web**. `backend/websearch.py` collects pages for the topic, the concept graph is built from
them exactly as for uploads, and the material panel links every source page. You can also select up
to 10 PDF/TXT/MD files at once to build one combined graph.

While you study, if neither uploaded nor web material has a passage for the current concept, the
question generator searches the web once for that concept, adds the pages to the material, and
cites them.

Configure in `.env`:

```dotenv
WEB_SEARCH_PROVIDER=free   # free: Wikipedia + DuckDuckGo, no key; gemini: Google Search grounding
                           # (falls back to free if it fails); off: disable web search
WEB_SEARCH_MAX_PAGES=4
WEB_SEARCH_TIMEOUT=15
```

Fetched pages are treated as untrusted source text. Only public http(s) addresses are fetched
(private, loopback and link-local hosts are refused, including after redirects), and each page is
limited to 1.5 MB.

## Knowledge ingestion

Upload UTF-8 TXT, Markdown, or text-based PDF files up to 8 MiB. PDF extraction
is limited to 150 pages and text to 600,000 characters. Original bytes and
overlapping chunks are stored under `data/materials/` using generated IDs;
user filenames are display labels, never filesystem paths. Retrieval uses
BM25 over disk chunks. Live concept extraction produces a bounded graph;
unknown/self edges and directed cycles are rejected. Seed material and
prerequisites are extracted from uploaded material.

## Tests

```bash
python -m pytest -q
```

Frontend DOM tests build the app first:

```bash
npm ci
npm test
```

Optional real-browser test (run `npm run build` first):

```bash
python -m pip install playwright
python -m playwright install chromium
RUN_BROWSER_TESTS=1 python -m pytest -q tests/test_browser.py
```

Set `BROWSER_EXECUTABLE` to use a particular installed Chromium binary.
On PowerShell set `$env:RUN_BROWSER_TESTS='1'` before running pytest.

Optional **paid** OpenAI smoke tests for structured output and both enhanced tools:

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

- Run **one Uvicorn worker**. Background queues and locks live in the server
  process. Accounts provide login and ownership isolation. Email verification
  and password reset are not implemented.
- A browser refresh resumes saved work and recent chat. Events accepted before
  a process crash persist, but unfinished model jobs are not replayed.
- The policy implementation is functional; its effectiveness has not been
  established on student data. Thresholds and reward proxies need evaluation.
- Exact matching verifies agreement with a reference answer, not independent
  conceptual mastery. Generated reference answers can be wrong; review them for
  high-stakes use. LLM reasoning judgments and misconception hypotheses remain
  provisional. Mastery values are heuristic scores, not calibrated probabilities.

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

## Maintaining prompts and logs

Edit `backend/prompts.py` to review all model-facing instructions, tool descriptions,
and retry directives. Restart the backend after edits. Dynamic student inputs remain
in the calling modules. Provider adapters and the shared `call_llm` entrypoint live in `backend/utils.py`.

Logs are JSON lines at INFO level under `tmp/logs/<session-uuid>.log`.
Startup and requests without a session use `startup-<run-uuid>.log`.
Each entry includes a UTC timestamp, session ID/UUID, operation UUID, and module.
Background jobs include the triggering event ID. Session UUIDs persist in existing
session JSON and are omitted from API responses. No database migration is needed.

LLM request/response entries include full prompts, reasoning, retrieved context,
model output, tools, token usage, and durations. These files contain private learning
content; credentials and authorization headers are not logged. `tmp/` is ignored by
Git. Logs persist until manually removed; handlers are closed on shutdown.

Ordinary tests mock model calls and require no API key or paid requests.
Optional browser tests use a test-only mocked provider server. Live provider smoke
tests remain opt-in with `RUN_OPENAI_INTEGRATION=1`.

## Student-editable learning plan

The Learning plan panel shows the material-wide objective, steps, and goals.
Use Edit plan, then Save plan to share changes with future tutoring, grading,
reflection, and question-generation calls. Drafts remain local until saved.
Plans persist across correct answers and Skip. AI revisions are suggestions:
Accept suggestion applies one; Dismiss keeps the active plan.

Plan edits are versioned. A stale save returns a conflict and preserves the draft;
review the latest saved plan before retrying. Running jobs finish with their captured
plan before a save completes. Plan preferences cannot override grading rubrics.
Reflection still runs after each successfully graded submission; reflection errors
do not block grading. New plan JSON fields require no database migration.


## Unified responses and possible knowledge gaps

Gemini question generation uses a shallow provider schema with diagrams encoded
as JSON strings. The server decodes these into the same validated question diagrams
used by other providers; graph limits and endpoint validation still apply. This
avoids the HTTP 400 returned by Gemini's compatibility endpoint for the nested
question schema. Provider failures during study setup return readable retry
messages and preserve any saved material and learning plan.

The **Your response** workspace submits text and an optional editable React Flow
node-and-arrow diagram together. Nodes can be renamed, moved, connected, or
deleted using the canvas or labeled keyboard controls. Diagrams are limited to
30 nodes and 60 directed edges; IDs, endpoints, finite positions, and labels
(up to 200 characters) are validated on both incoming responses and model output.
Question diagrams are separate read-only views. Highlight only reviews the
response workspace: exact text excerpts or valid response node/edge IDs. Diagram
issues use red outlines and explanations; affected annotations clear on edits.

Text and diagrams save atomically to session JSON. Browser drafts cover reloads
before the one-second server-save debounce or while disconnected. Each submission
captures its own immutable response snapshot for grading and reflection. Legacy
`workspace` strings remain readable, and text-only socket submissions remain
supported. Incorrect answers retain the current question and response. Correct
answers progress after reflection and hypothesis analysis; a next-question
failure keeps feedback and offers **Retry next question** without reassessment.

The **Possible knowledge gaps** panel shows uncertain hypotheses with confidence,
evidence references, and draft/submission origin. Meaningful content changes
trigger analysis after an eight-second pause, at most once per 30 seconds per
session. Moving a node alone does not trigger analysis. A worker coalesces edits,
checks question/plan/response versions after model calls, and discards stale
results. Submission cancels draft work and takes priority. Draft analysis runs
outside the teaching lock and never updates mastery, policy rewards, or durable
learning memory. Completed submission analysis follows reflection and persists
at most eight current evidence-linked hypotheses in the existing JSON storage.
Background updates affect only the panel; grading uses the compact notification.

Question generation receives the active student-edited plan, eligible concepts,
latest valid hypotheses, prior questions, sources, and recent formats. It chooses
explanation, trace, diagram interpretation, or diagram construction while respecting
prerequisites. Private reference answers and rubrics remain server-side. Reflection
or hypothesis failures are shown independently and preserve the assessment; question
generation may use the last valid hypotheses. Response text, labels, edited plans,
and hypotheses are untrusted data, never model instructions or grading authority.

Observer environment overrides: `FAILURE_THRESHOLD=1`, `STALL_SECONDS=30`,
`IDLE_SECONDS=60`, `COOLDOWN_SECONDS=45`, `TYPING_GRACE_SECONDS=3`, and
`AGGREGATION_SECONDS=5`. `PROVIDER_CONCURRENCY=3` caps combined provider calls,
including draft hypotheses and foreground grading/tools. Existing `.env` values
**take precedence over these defaults**; exported process variables take precedence
over `.env`. Update existing overrides to opt into the new timings. Draft pause
and rate-limit defaults are eight and 30 seconds. No database migration or image
input is required.
