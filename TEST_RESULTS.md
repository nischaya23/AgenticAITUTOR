# Executed validation — 12 November 2026

## Backend regressions

```bash
.venv/bin/pytest -q --tb=short
```

Latest full run: **142 passed, 4 skipped, 1 dependency deprecation warning**. The skipped checks require explicitly enabled paid-provider or browser integration flags.

The older Gemini SDK tests now assert the text-only question schema and rejection of removed diagram fields. Their existing logging and provider-failure checks remain intact; the suite requires no legacy-test exclusions.

Backend tests run outside the execution sandbox because Starlette/AnyIO's cross-thread TestClient event loop hangs within this environment's sandbox. No Docker is used.

## Frontend

```bash
npm test
```

The TypeScript/Vite build succeeded and **19 DOM tests passed**. Coverage includes topic preparation, ambiguity choices, selected-source explanations, failed-upload recovery, memory corrections, text-only submissions, legacy text draft restoration, video controls, annotations, saved sessions, themes and mobile navigation.

Vite reports its existing dependency directive and large-bundle warnings. DOM checks do not establish pixel-perfect browser layout.

## Local video integration

A local drawing script was interpreted through the bounded drawing runtime and encoded with Pillow/FFmpeg, without executing generated Python or using Docker. FFprobe confirmed H.264, 960×540, 15 fps, `yuv420p`, and the requested duration. A two-second sample encoded in approximately 0.3 seconds on this machine; that excludes model generation and is not an end-to-end latency guarantee.

Tests also verify seekable authenticated playback, WebVTT captions, account isolation, cache reuse, unsupported script rejection and bounded drawing work.

## Web-search regression and live verification

`tests/test_websearch_regressions.py` covers:

- Gemini thought-signature metadata preserved across native function-call turns.
- Selected source text retained when a follow-up model request fails or returns malformed arguments.
- Upload preparation continuing to a sourced graph after partial research failure.
- Actual Wikipedia/DuckDuckGo/page-fetch plumbing through deterministic HTTP transports.
- A shallow Gemini graph transport followed by complete local schema and citation validation.
- Literal newlines, typographic whitespace, source-backed quote repair and invalid-source rejection.
- Redundant invalid citations discarded only when every graph item still has valid evidence.
- Shortened quotations resolved to complete original source passages, never stored as stitched or invented text.

A small **live check against the configured Gemini provider and web search** completed successfully for TF-IDF. It selected the GeeksforGeeks and LearnDataSci TF-IDF pages, then generated three concepts—Term Frequency, Inverse Document Frequency and TF-IDF—with four locally validated citations.

The live check initially reproduced the reported failure and exposed both provider protocol/schema issues and overly strict handling of otherwise supported graph evidence. Provider errors are no longer silently allowed to erase previously selected pages. Ordinary regression tests remain fully mocked and do not make paid calls.
