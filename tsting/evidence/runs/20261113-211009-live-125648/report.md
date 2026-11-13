# Studyroom simulated-learner pilot

Mode: **live**. Model: `gemini-3.5-flash-lite`. Seed: 23.
Revision: `3fc8f0f085065403d5af029e7045fc46669ea28c`. Runtime: 90.8s.

Fixed knowledge, isolated concept sessions, no research or automatic help. Help-probe questions are skipped without grading.

## Grading

- Graded answers: 1; agreement: 1/1.
- False acceptance: 0; false rejection: 0.
- Mean absolute score difference: 0.200.
- Excluded questions/probes: 0; operation failures: 2.

## Mastery

| Profile | Concept | Level | Answers | Final mastery | Complete | Status |
|---|---|---|---:|---:|---|---|
| novice | complexity | weak | 1 | — | — | failed |
| novice | boundaries | weak | 0 | — | — | failed |
| novice | lower_bound | weak | 0 | — | — | running |

Weak-profile completion flags: 0. This is a review flag, not proof of a grading error.
Expected mastery ordering satisfied: 0/0 cross-level pairs (ties do not count as separation).
Mastery is a hand-set BKT progression heuristic. No numeric calibration or learning-gain claim is made.

## Teaching

- Reviewed outputs: 4.
- Factual issues: 0; relevance issues: 0; draft suitability issues: 0.
- Hints judged to reveal the final answer: 0.

- Assessment feedback factual issues: 0 / 0 reviewed.

## Review evidence

- {"scenario": "novice/complexity", "category": "provider", "type": "RateLimitError", "message": "Error code: 429 - [{'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://aistudio.google.com/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \\n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 15, model: gemini-3.5-flash-lite, description: Generate content requests per minute limit exceeded on", "provider_status": 429}
- {"scenario": "novice/boundaries", "category": "operation", "type": "RuntimeError", "message": "HTTP 503 /api/sessions/2/study: {\"detail\":\"Gemini rate or quota limit reached (HTTP 429). Please retry later.\"}", "provider_status": null}

Exclusions: {"invalid_question": 0, "simulator_profile_violation": 0}.
Failure categories: {"provider": 1, "operation": 1}.

## Reproducibility and limits

- Same-model evaluator judgments are provisional and may share the tutor’s biases. Review the saved excerpts.
- Each concept has at most four answers; early app completion reduces the sample further. Unmeasured concepts remain inconclusive.
- Seeds fix scenario ordering, not model sampling. No statistical reliability claim is made.
- All private profiles and the full source course are in results.json; transcript.jsonl contains public application traffic.
- Repository and original database unchanged: True.
- Usage: `{"external_calls": 11, "external_input_tokens": 5882, "external_output_tokens": 1485, "tutor": {"llm_calls": 21, "input_tokens": 26145, "output_tokens": 2756, "api_latency_ms": 55144.35, "interventions": 4, "explicit_interventions": 4, "llm_errors": 6}}`.
