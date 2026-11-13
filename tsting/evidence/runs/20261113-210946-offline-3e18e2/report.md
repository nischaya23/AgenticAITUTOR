# Studyroom simulated-learner pilot

Mode: **offline**. Model: `gpt-4o`. Seed: 23.
Revision: `3fc8f0f085065403d5af029e7045fc46669ea28c`. Runtime: 22.3s.

Fixed knowledge, isolated concept sessions, no research or automatic help. Help-probe questions are skipped without grading.

## Grading

- Graded answers: 26; agreement: 26/26.
- False acceptance: 0; false rejection: 0.
- Mean absolute score difference: 0.000.
- Excluded questions/probes: 0; operation failures: 0.

## Mastery

| Profile | Concept | Level | Answers | Final mastery | Complete | Status |
|---|---|---|---:|---:|---|---|
| novice | complexity | weak | 2 | 0.8692 | True | finished |
| novice | boundaries | weak | 4 | 0.1255 | False | finished |
| novice | lower_bound | weak | 4 | 0.1255 | False | finished |
| mixed | complexity | strong | 2 | 0.8692 | True | finished |
| mixed | boundaries | partial | 4 | 0.1255 | False | finished |
| mixed | lower_bound | weak | 4 | 0.1255 | False | finished |
| strong | complexity | strong | 2 | 0.8692 | True | finished |
| strong | boundaries | strong | 2 | 0.8692 | True | finished |
| strong | lower_bound | strong | 2 | 0.8692 | True | finished |

Weak-profile completion flags: 1. This is a review flag, not proof of a grading error.
Expected mastery ordering satisfied: 4/7 cross-level pairs (ties do not count as separation).
Mastery is a hand-set BKT progression heuristic. No numeric calibration or learning-gain claim is made.

## Teaching

- Reviewed outputs: 18.
- Factual issues: 0; relevance issues: 0; draft suitability issues: 0.
- Hints judged to reveal the final answer: 0.

- Assessment feedback factual issues: 0 / 26 reviewed.

## Review evidence


Exclusions: {"invalid_question": 0, "simulator_profile_violation": 0}.
Failure categories: {"provider": 0, "operation": 0}.

## Reproducibility and limits

- Same-model evaluator judgments are provisional and may share the tutor’s biases. Review the saved excerpts.
- Each concept has at most four answers; early app completion reduces the sample further. Unmeasured concepts remain inconclusive.
- Seeds fix scenario ordering, not model sampling. No statistical reliability claim is made.
- All private profiles and the full source course are in results.json; transcript.jsonl contains public application traffic.
- Repository and original database unchanged: True.
- Usage: `{"external_calls": 114, "external_input_tokens": 0, "external_output_tokens": 0, "tutor": {"interventions": 18, "explicit_interventions": 18}}`.
