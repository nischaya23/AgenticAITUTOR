# Studyroom — Agentic AI Tutor

Describe what you want to learn or upload course notes. Studyroom researches the subject, explains which websites it selects, builds a sourced knowledge graph, and tutors you through text questions. Learning memory is visible and correctable. Visual explanations are generated drawing scripts rendered locally into video.

The application uses FastAPI, React/TypeScript, SQLite, and the configured OpenAI or Gemini provider. Agents are cooperating Python modules; no external agent framework is required.

## Run

Use Python 3.11+ and Node.js 20.19+ or 22.12+. Install FFmpeg and DejaVu fonts on the machine for local video rendering. Pillow is included in the Python requirements. The video worker uses POSIX resource limits; Docker is not used.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
npm ci
npm run build
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Configure one provider in `.env`:

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=your_key
OPENAI_MODEL=your_model
```

Or set `LLM_PROVIDER=gemini`, `GEMINI_API_KEY` and `GEMINI_MODEL`. Use a model your account can access. Keys stay on the backend. The configured provider handles extraction, planning, tutoring, grading and reflection; failures are reported rather than replaced with scripted teaching.

Open http://127.0.0.1:8000. For development, keep FastAPI running and use `npm run dev`, then open http://127.0.0.1:5173. Vite proxies `/api` and `/ws`.

Create an email/password account. Accounts own their materials, sessions, operations, learning memory and rendered videos. Passwords use scrypt; revocable login cookies are HttpOnly. Set `COOKIE_SECURE=true` for HTTPS and configure `CORS_ORIGINS` for additional development origins.

## Study and memory

- Enter a topic description, level and goals; no upload is required. Ambiguous topics ask which meaning you intend.
- Alternatively, upload up to ten text-based PDF, TXT or Markdown files. Each file is limited to 8 MiB; combined extracted text is limited to 600,000 characters. Scanned PDFs require OCR.
- Research progress shows queries, candidate pages, selection/rejection reasons and supporting quotations. Accepted evidence is retained if a later research request fails.
- The learning map shows concepts, prerequisites and evidence. Click an unlocked concept to focus on it.
- Questions and student responses are text-only. Saved legacy diagrams are not editable; active diagram questions are replaced on resume without mastery credit.
- Request hints, explanations, text review, guiding questions, videos or web search. Automatic help can be disabled in the workspace.
- The Memory tab shows session context, long-term mastery, goals, reflections, hypotheses and misconceptions. Correct summaries/goals or dispute misconceptions without changing grades or deleting evidence.
- New visualizations use local Pillow frames and FFmpeg MP4 encoding, with captions and chapters. The renderer interprets a bounded Python drawing subset rather than executing unrestricted model code.

See [the complete numbered agent flow and architecture](docs/ARCHITECTURE.md).

## Observer

The numerical Observer uses activity features, struggle thresholds and LinUCB as an advisory baseline. The Observer LLM can override WAIT or WAKE and explains its decision. Routine reviews wait for an eight-second pause and are spaced thirty seconds apart. Completed assessments can trigger priority review.

User preferences, typing, inactive/solved questions and in-flight work remain enforced. Repeat automatic help has a fifteen-second minimum; before the normal forty-five-second cooldown expires it also requires new response content or a new incorrect assessment. Approved help goes to the Planner for tool selection.

```dotenv
OBSERVER_LLM_ENABLED=true
FAILURE_THRESHOLD=1
STALL_SECONDS=30
IDLE_SECONDS=60
COOLDOWN_SECONDS=45
TYPING_GRACE_SECONDS=3
AGGREGATION_SECONDS=5
BANDIT_WARMUP=12
PROVIDER_CONCURRENCY=3
```

LLM grading is provisional. It updates progress and heuristic mastery but is not a verified LinUCB reward. Hypothesis confidence and mastery are estimates, not calibrated probabilities. Helpful/unhelpful feedback can train the executed policy action. These are observational associations, not causal estimates.

## Web research

```dotenv
WEB_SEARCH_PROVIDER=free
WEB_SEARCH_MAX_PAGES=4
WEB_SEARCH_TIMEOUT=15
```

`free` uses Wikipedia and DuckDuckGo. `gemini` uses Google grounding links, then fetches the actual linked pages; it falls back to free search when necessary. `off` disables research. Uploads still work from their own evidence; topic-only preparation needs usable sources.

A shared native `web_search` function tool is available during preparation and study. Research is bounded to three searches per preparation and two per study request, with up to four fetched candidates per search. Existing accepted web passages are reused. Gemini tool-call metadata is retained across turns, and its graph transport is kept shallow before full local validation.

Fetched content is untrusted data. Private/loopback addresses and unsafe redirects are refused, pages have size limits, and supporting quotes must match supplied text. Selection establishes relevance, not guaranteed factual correctness.

## Persistence and APIs

SQLite stores account/session state, append-only events, student memory, editable memory entries and preparation/video operations. Original material and retrieval chunks live under `data/materials`; account-scoped videos and scripts live under `data/videos`. Summaries retain the latest thirty entries. The app adds new tables on startup and preserves existing saved material and event history.

Important endpoints:

- `POST /api/material-jobs` — prepare uploaded files.
- `POST /api/material-jobs/topic` — prepare a topic description.
- `GET /api/operations/{id}` and `/events` — owned progress snapshots and resumable SSE.
- `POST /api/material-jobs/{id}/meaning` and `/retry` — clarify or retry a topic.
- `GET /api/sessions/{id}/memory` and `POST .../memory/corrections` — memory display and versioned corrections.
- `GET /api/videos/{key}` and `/captions` — owned, seekable video and captions.
- Existing session, material, student, study and `/ws/{id}` routes remain available. Schemas are at `/docs`.

Run one Uvicorn worker: task queues and locks live in the process. Refreshing restores saved work and operation progress. Interrupted preparation jobs can be retried; unfinished model calls are not replayed. No student-submitted code is executed.

## Validation

```bash
python -m pytest -q
npm test
```

The ordinary suite mocks providers and search. It includes a local FFmpeg renderer integration check when FFmpeg is installed. Optional browser and paid model tests use their existing `RUN_BROWSER_TESTS` and `RUN_OPENAI_INTEGRATION` flags. See [executed validation](TEST_RESULTS.md) for the exact commands and any local legacy-test exclusions.

All model-facing instructions are in `backend/prompts.py` and the specialized research/Observer/video modules. Restart the backend after changing code or configuration. Logs are JSON lines under `tmp/logs`; do not publish them because model request logs contain student context.
