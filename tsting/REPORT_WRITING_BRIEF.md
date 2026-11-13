# Instructions for the report-writing Codex

Use this folder as evidence for an **exploratory, time-limited simulated-learner pilot**.
Read `README.md`, `REPORT.md`, `SUMMARY.json`, `manual_audit.json`, and
`EVIDENCE_MANIFEST.json`, then consult the linked source `results.json` files and
transcripts for examples. Do not launch paid experiments unless separately requested.

## Facts and denominators to preserve

- Three fixed profiles: novice, mixed and strong. Three binary-search units: interval
  boundaries, lower bound/duplicates and complexity. Learner knowledge did not improve.
- The combined live analysis uses only the two paced live runs identified in
  `SUMMARY.json`. Their model was `gemini-3.5-flash-lite`, with seed 23.
- Those runs produced **13 graded answers**. Agent inspection excluded **two simulator
  profile violations**, leaving **11 profile-valid answers**.
- **Two independent evaluator judgments** on retained answers were unsuitable for binary
  correctness comparison. Thus **nine usable comparisons** remained, with **9/9
  agreement**, zero observed false acceptances/rejections in that filtered subset,
  and mean absolute score difference about **0.033**. This is post-inspection agreement
  on a small, selected sample, **not 100% system accuracy** or independent verification.
- Do not subtract the evaluator exclusions from the mastery sample: they invalidate
  reference-grade comparisons, while the learner answers and observed BKT updates remain.
- Strong-versus-novice mastery ordering held in all three measured topics. Selected
  observed values were strong `0.6337` in each unit; novice boundaries `0.1711`, lower
  bound `0.3099`, complexity `0.1621`.
- Most selected sessions had one answer; novice complexity had four. The report selects
  the more complete session when a profile/unit was run twice and never merges the
  independently reset BKT states. Mixed lower-bound coverage is unavailable after its
  simulator exclusion. The mixed/partial boundaries estimate equaled the strong
  estimate after one answer; this pilot does not establish fine-grained mastery accuracy.
- Only **two help outputs** were evaluated: a novice-complexity hint and explanation.
  Both were judged accurate/relevant/suitable, and the hint did not reveal the answer.
  Do not generalize this to every tool or subject.
- Nine harness checks passed during the original experiment; the full synthetic
  API/WebSocket validation completed nine sessions and 26 answers. These synthetic
  outcomes validate the harness paths, not live teaching accuracy.
- Repository/database fingerprints were unchanged during the original live evaluation.
  This `tsting/` folder was added afterward at the user's explicit request.

## Findings to explain with evidence

1. **Tutor feedback review issue:** a learner correctly gave initial values
   `left=0, right=5, mid=2` for `[2,4,4,4,6]` and target `4`, but then incorrectly
   proposed `right=mid-1`. The tutor awarded full credit and ignored that extra error.
   The independent evaluator's zero score was also unsuitable: it claimed `right=mid`
   was required, although this unit's ordinary exact search returns `mid` on equality.
   Present this as feedback failing to address contradictory reasoning; do not claim
   that zero was the verified correct grade. See the `mixed/boundaries` review and its
   question ID in `manual_audit.json`.
2. **Partial-credit disagreement:** for a correct halving sequence paired with an
   incorrect linear-time explanation, tutor score was `0.5` versus evaluator `0.2`.
   Both rejected the overall answer. The score difference alone does not establish
   which numerical score was correct.
3. **Evaluation-system limitations:** one evaluator confused fidelity to a misconception
   with factual correctness. Simulated learners also introduced knowledge outside
   their assigned profiles. The current harness now separates correctness and profile
   checks, but this improvement was **not** used in the archived live runs.

## Methodology, limits and exclusions

Explain the reference materials, targeted sessions, real application HTTP/WebSocket
path, hidden learner profiles, fixed knowledge and separate ungraded help probe. Accounts
were separated by profile. Inputs were text; prepared graphs avoided course-generation
variability. No production code was patched for the evaluation.

The same configured Gemini model supplied tutor, learner and evaluator roles in separate
contexts. Shared model bias remains. Exclusions were made through Codex/agent inspection,
not a human panel or independent expert adjudication. Label interpretations accordingly.

An earlier unpaced run hit a reported 15-requests/minute quota and is archived for
provenance, **not included in the combined results**. Later runs spaced model requests
by five seconds. One run was deliberately interrupted; the shortened run hit its soft
time budget after retaining the final grade but before its optional feedback review.
An intentional budget stop is not an application/provider failure. Do not claim all
planned sessions completed cleanly or that the full repeated benchmark was finished.

Do not claim calibrated mastery probabilities, mastery convergence, statistical
reliability, learning gains, independent factual verification, production readiness,
or end-to-end validation of the browser, Observer, research or prerequisite system.
No confidence intervals or significance tests are supported by this sample.

## Suggested report structure

1. Purpose and research questions.
2. Experimental setup and fixed learner profiles.
3. Results, with sample/exclusion flow and mastery table.
4. Qualitative cases separating tutor issues from simulator/evaluator issues.
5. Threats to validity, scope and time/quota constraints.
6. Future work: repeated sessions, an independently configured evaluator or expert
   review, stronger simulator checks, and a separate learning-progress experiment.

Cite repository-relative evidence paths and question IDs for examples. Preserve the
distinction between observed behavior, agent interpretation, and proposed improvements.
Do not invent results, costs, participants, extra experiments or validation claims.
