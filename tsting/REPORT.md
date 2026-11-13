# Live simulated-learner evaluation — time-limited pilot

Standalone evaluation code and archived evidence are in tsting/, separate from the application. The full repeated benchmark was shortened to meet the user’s remaining-time limit.

**11 profile-valid live graded answers; correctness agreement 9/9 usable comparisons.** False acceptance: 0; false rejection: 0.
Mean absolute score difference: 0.033.

## Mastery and coverage

These are observed BKT estimates, not calibrated ground-truth probabilities. For repeated profile/concept sessions, use the session with the most completed answers; never combine their independently reset mastery states.

| Learner | Topic | Assigned level | Graded answers in selected session | Final observed mastery | App completed |
|---|---|---|---:|---:|---|
| novice | boundaries | weak | 1 | 0.1711 | False |
| novice | lower_bound | weak | 1 | 0.3099 | False |
| novice | complexity | weak | 4 | 0.1621 | False |
| mixed | boundaries | partial | 1 | 0.6337 | False |
| mixed | lower_bound | weak | 0 | unavailable | unmeasured |
| mixed | complexity | strong | 1 | 0.6337 | False |
| strong | boundaries | strong | 1 | 0.6337 | False |
| strong | lower_bound | strong | 1 | 0.6337 | False |
| strong | complexity | strong | 1 | 0.6337 | False |

Strong-versus-novice ordering: 3/3 measured topics.

## Teaching and grading evidence

Completed help reviews: 2.
Help factual/relevance/suitability issues: 0.
Hints judged to reveal the answer: 0.

### Review: novice/complexity

Question: Trace how a starting search interval of 16 elements decreases after each comparison in binary search when the target is not in the first checked midpoint. List the remaining interval sizes step by step until 1 element is left, and explain why this pattern of repeated division means binary search does not examine every element like linear search.

Answer: With a starting search interval of 16 elements, the size decreases step by step to 8, 4, 2, and finally 1 element. Even though the interval is halved at each step, binary search actually examines every single item in the list just like linear search does, which is why it takes O(n) time. The repeated halving just divides the runtime by two once overall, but because it still checks all elements, it isn't fundamentally faster in terms of big-O complexity from what I understand.

Tutor score 0.5: You correctly listed the step-by-step reduction of the search interval from 16 down to 8, 4, 2, and 1 elements. However, you incorrectly claimed that binary search examines every item in the list just like linear search and takes O(n) time. Binary search does not check every element; by repeatedly halving the interval, it discards half of the remaining items at each step, resulting in a logarithmic O(log n) time complexity rather than a linear O(n) scan.

Independent evaluator score 0.2: The learner correctly lists the interval sizes decreasing from 16 to 8, 4, 2, and 1, but then provides an incorrect explanation due to the assigned misconception, claiming that binary search examines every element like linear search and takes O(n) time.

Evidence excerpt: With a starting search interval of 16 elements, the size decreases step by step to 8, 4, 2, and finally 1 element. Even though the interval is halved at each step, binary search actually examines every single item in the list just like linear search does, which is why it takes O(n) time.

This is an automated review flag; the same model supplies both judgments.

### Review: novice/lower_bound

Question: Consider finding the lower bound of x in a sorted array using a half-open interval where left=0 and right=len(a). Explain what happens and why an infinite loop occurs if you update the left pointer using left = mid instead of left = mid + 1 when a[mid] < x.

Answer: If we use left = mid instead of left = mid + 1 when a[mid] < x, an infinite loop can happen because when we find a value equal to x, I believe that first equal midpoint is always the first occurrence, so we might stop on equality or get stuck trying to narrow down the lower bound. Since I get confused between the insertion position and not-found -1, the pointers might stop moving when left and right get close together, causing the search range to never shrink and resulting in an infinite loop.

Tutor score 0.3: The student mentions that pointers might stop moving when left and right get close together, which is on the right track, but fails to explain the core mechanism: that integer division rounds down, causing mid to equal left when right = left + 1, so updating left = mid prevents the interval from shrinking.

Independent evaluator score 1.0: The answer provides a valid explanation consistent with the profile's misconceptions about binary search lower bound behavior.

Evidence excerpt: If we use left = mid instead of left = mid + 1 when a[mid] < x, an infinite loop can happen because when we find a value equal to x, I believe that first equal midpoint is always the first occurrence

This is an automated review flag; the same model supplies both judgments.

Agent inspection: The independent evaluator awarded full correctness because the answer was consistent with the learner's assigned misconception, conflating persona fidelity with factual correctness. The answer incorrectly attributes a loop on a[mid] < x to equality/duplicate handling and fails to give the mid=left mechanism. The tutor's partial-credit critique identifies that mechanism correctly. Keep the observed answer and mastery, but exclude this unreliable independent grade from agreement metrics.

Check: With a=[1], x=2, left=0, right=1, mid=0: a[mid]<x, and the erroneous update left=mid leaves left=0 forever. No equality or duplicate is involved.

### Review: mixed/boundaries

Question: Consider an ascending sorted array a = [2, 4, 4, 4, 6] and target = 4 using the half-open interval [left, right) search algorithm where left=0 and right=len(a). Trace the values of left, right, and mid during the first iteration of the while loop.

Answer: For the array a = [2, 4, 4, 4, 6] and target = 4 with left = 0 and right = 5, the first iteration starts with left = 0 and right = 5. The mid value is calculated as (0 + 5) / 2 = 2. Since I sometimes confuse how the right bound updates and might use right = mid - 1, the right bound would become 2 - 1 = 1, while left stays at 0 and mid was 2.

Tutor score 1.0: You correctly identified left = 0, right = 5, and mid = 2 for the first iteration of the binary search loop, matching the expected trace.

Independent evaluator score 0.0: The student incorrectly applies the closed-interval update rule (right=mid-1) due to the assigned profile misconception, whereas the half-open interval rule requires right=mid.

Evidence excerpt: the right bound would become 2 - 1 = 1

This is an automated review flag; the same model supplies both judgments.

Agent inspection: The learner correctly gives the requested initial trace left=0, right=5, mid=2, but adds a wrong right=mid-1 update. The tutor awards full credit and ignores that extra error. The independent evaluator awards zero and incorrectly says right=mid is required, although this unit's ordinary exact-search algorithm returns mid on equality. Neither automated judgment adequately handles the correct requested trace plus contradictory extra step. Exclude the binary agreement comparison and retain this as a tutor-feedback review issue.

Check: a[2]=4 equals the target, so ordinary exact search returns 2 without setting right to either 1 or 2. The requested initial values are nevertheless correct.

- novice/complexity, hint: The tutor output is factually correct, directly addresses the misconceptions in the draft, provides a helpful hint guiding the student toward the correct halving logic without directly giving away the final answer of O(log n), and is well-suited for the draft.
- novice/complexity, explanation: The tutor correctly addresses the misconception in the draft about halving only once, explains the mathematical derivation of the O(log n) worst-case time complexity, and aligns with the course material.

## Validation and limitations

- Nine local harness checks passed. Full synthetic API/WebSocket validation completed nine sessions and 26 graded answers.
- Original repository/database fingerprint checks passed for every included run: True.
- Automatically excluded simulator/question samples: 0; manually excluded simulator violations missed by the automatic check: 2; recorded operation/provider failures in these runs: 0.
- Intentional budget stops: 1. The final graded answer was retained, but its optional feedback-quality review did not complete. Budget stops are not counted as tutor failures.
- Agent inspection exclusions are recorded separately in manual_audit.json; raw application/model outputs remain unchanged. The automatic profile-consistency check missed one out-of-profile linked-list explanation, demonstrating a limit of LLM role simulation.
- Independent grades excluded as unreliable after agent inspection: 2. One evaluator conflated persona fidelity with correctness; another assigned zero to a correct initial trace followed by an incorrect extra step. These are review cases rather than confirmed binary grading errors.
- Future runs use separate correctness and profile-check calls; the correctness grader receives no learner profile. These completed live results used the earlier combined evaluator and were inspected accordingly.
- An earlier unpaced attempt hit Gemini’s 15-requests/minute quota. It was stopped; its partial artifacts are archived under evidence/runs/20261113-211009-live-125648. They are excluded from the combined live metrics. Later runs pace actual requests at five-second intervals.
- The longer paced run was deliberately interrupted for the time limit. Its completed evidence is retained; pending questions are not counted as answers.
- Each abbreviated session has one answer, so mastery convergence and completion behavior remain largely untested. Full planned coverage of up to four answers per concept was not completed.
- Learner/evaluator/tutor use the configured Gemini model in separate contexts. Agreement may reflect shared model bias; flagged excerpts require human review.
- Fixed knowledge evaluates diagnosis and feedback, not actual learning gains. Research, course extraction, prerequisite unlocking, automatic Observer behavior and browser UI are outside this pilot.
- Archived evidence is retained in tsting/evidence/. Future local runs write to the ignored tsting/.local-runs/ directory.

## Source artifacts

- [20261113-211454-live-bb4684 report](evidence/runs/20261113-211454-live-bb4684/report.md) — model `gemini-3.5-flash-lite`; seed 23; 325.5s.
  [Results](evidence/runs/20261113-211454-live-bb4684/results.json) · [Application transcript](evidence/runs/20261113-211454-live-bb4684/transcript.jsonl).
- [20261113-212045-live-9f1a92 report](evidence/runs/20261113-212045-live-9f1a92/report.md) — model `gemini-3.5-flash-lite`; seed 23; 427.3s.
  [Results](evidence/runs/20261113-212045-live-9f1a92/results.json) · [Application transcript](evidence/runs/20261113-212045-live-9f1a92/transcript.jsonl).
