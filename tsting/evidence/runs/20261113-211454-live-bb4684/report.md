# Studyroom simulated-learner pilot

Mode: **live**. Model: `gemini-3.5-flash-lite`. Seed: 23.
Revision: `3fc8f0f085065403d5af029e7045fc46669ea28c`. Runtime: 325.5s.

Fixed knowledge, isolated concept sessions, no research or automatic help. Help-probe questions are skipped without grading.

## Grading

- Graded answers: 4; agreement: 4/4.
- False acceptance: 0; false rejection: 0.
- Mean absolute score difference: 0.075.
- Excluded questions/probes: 0; operation failures: 0.

## Mastery

| Profile | Concept | Level | Answers | Final mastery | Complete | Status |
|---|---|---|---:|---:|---|---|
| novice | complexity | weak | 4 | 0.1621 | False | finished |
| novice | boundaries | weak | 0 | — | — | running |

Weak-profile completion flags: 0. This is a review flag, not proof of a grading error.
Expected mastery ordering satisfied: 0/0 cross-level pairs (ties do not count as separation).
Mastery is a hand-set BKT progression heuristic. No numeric calibration or learning-gain claim is made.

## Teaching

- Reviewed outputs: 2.
- Factual issues: 0; relevance issues: 0; draft suitability issues: 0.
- Hints judged to reveal the final answer: 0.

- Assessment feedback factual issues: 0 / 4 reviewed.

## Review evidence

### novice/complexity — grading disagreement

Question: Trace how a starting search interval of 16 elements decreases after each comparison in binary search when the target is not in the first checked midpoint. List the remaining interval sizes step by step until 1 element is left, and explain why this pattern of repeated division means binary search does not examine every element like linear search.

Answer: With a starting search interval of 16 elements, the size decreases step by step to 8, 4, 2, and finally 1 element. Even though the interval is halved at each step, binary search actually examines every single item in the list just like linear search does, which is why it takes O(n) time. The repeated halving just divides the runtime by two once overall, but because it still checks all elements, it isn't fundamentally faster in terms of big-O complexity from what I understand.

Tutor (0.5): You correctly listed the step-by-step reduction of the search interval from 16 down to 8, 4, 2, and 1 elements. However, you incorrectly claimed that binary search examines every item in the list just like linear search and takes O(n) time. Binary search does not check every element; by repeatedly halving the interval, it discards half of the remaining items at each step, resulting in a logarithmic O(log n) time complexity rather than a linear O(n) scan.

Evaluator (0.2): The learner correctly lists the interval sizes decreasing from 16 to 8, 4, 2, and 1, but then provides an incorrect explanation due to the assigned misconception, claiming that binary search examines every element like linear search and takes O(n) time.

Evidence: With a starting search interval of 16 elements, the size decreases step by step to 8, 4, 2, and finally 1 element. Even though the interval is halved at each step, binary search actually examines every single item in the list just like linear search does, which is why it takes O(n) time.


Exclusions: {"invalid_question": 0, "simulator_profile_violation": 0}.
Failure categories: {"provider": 0, "operation": 0}.

## Reproducibility and limits

- Same-model evaluator judgments are provisional and may share the tutor’s biases. Review the saved excerpts.
- Each concept has at most four answers; early app completion reduces the sample further. Unmeasured concepts remain inconclusive.
- Seeds fix scenario ordering, not model sampling. No statistical reliability claim is made.
- All private profiles and the full source course are in results.json; transcript.jsonl contains public application traffic.
- Repository and original database unchanged: True.
- Usage: `{"external_calls": 16, "external_input_tokens": 8847, "external_output_tokens": 2378, "tutor": {"llm_calls": 25, "input_tokens": 84234, "output_tokens": 5153, "api_latency_ms": 168886.43999999997, "interventions": 2, "explicit_interventions": 2, "llm_errors": 1}}`.
