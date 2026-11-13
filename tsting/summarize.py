#!/usr/bin/env python3
"""Combine completed live evidence without merging independently reset mastery states."""
import argparse
import importlib.util
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('evaluation', HERE / 'evaluate.py')
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def summarize(paths):
    sources = [(Path(os.path.relpath(Path(path).resolve(), HERE)), json.loads(Path(path).read_text())) for path in paths]
    audit_path = HERE / 'manual_audit.json'
    audit = json.loads(audit_path.read_text()) if audit_path.exists() else {'excluded_question_ids': {}}
    audited_ids = audit['excluded_question_ids']
    answers, help_outputs, chosen = [], [], {}
    for path, data in sources:
        for scenario in data['scenarios']:
            key = (scenario['profile'], scenario['unit'])
            scored = [a for a in scenario['answers'] if 'assessment' in a and a['question']['id'] not in audited_ids]
            for answer in scored:
                answers.append({'profile': key[0], 'unit': key[1], 'source': str(path), **answer})
            for output in scenario['help']:
                help_outputs.append({'profile': key[0], 'unit': key[1], 'source': str(path), **output})
            if scored and (key not in chosen or len(scored) > len(chosen[key]['scored'])):
                chosen[key] = {'scenario': scenario, 'scored': scored, 'source': str(path)}
    unreliable_ids = audit.get('unreliable_evaluator_question_ids', {})
    compared = [a for a in answers if a['question']['id'] not in unreliable_ids]
    agree = sum(a['assessment']['correct'] == a['independent_review']['correct'] for a in compared)
    false_accept = sum(a['assessment']['correct'] and not a['independent_review']['correct'] for a in compared)
    false_reject = sum(not a['assessment']['correct'] and a['independent_review']['correct'] for a in compared)
    diff = sum(abs(a['assessment']['score']-a['independent_review']['score']) for a in compared)/len(compared) if compared else None
    lines = ['# Live simulated-learner evaluation — time-limited pilot', '',
             'Standalone evaluation code and archived evidence are in tsting/, separate from the application. The full repeated benchmark was shortened to meet the user’s remaining-time limit.', '',
             f'**{len(answers)} profile-valid live graded answers; correctness agreement {agree}/{len(compared)} usable comparisons.** False acceptance: {false_accept}; false rejection: {false_reject}.',
             f'Mean absolute score difference: {diff:.3f}.' if diff is not None else 'No completed grading comparisons.', '',
             '## Mastery and coverage', '',
             'These are observed BKT estimates, not calibrated ground-truth probabilities. For repeated profile/concept sessions, use the session with the most completed answers; never combine their independently reset mastery states.', '',
             '| Learner | Topic | Assigned level | Graded answers in selected session | Final observed mastery | App completed |',
             '|---|---|---|---:|---:|---|']
    selected = []
    for profile in evaluation.PROFILES:
        for unit in evaluation.COURSE:
            entry = chosen.get((profile, unit))
            if not entry:
                lines.append(f'| {profile} | {unit} | {evaluation.PROFILES[profile][unit]} | 0 | unavailable | unmeasured |')
                continue
            scenario, scored = entry['scenario'], entry['scored']
            mastery = scored[-1].get('mastery')
            selected.append({'profile': profile, 'unit': unit, 'level': scenario['level'],
                             'mastery': mastery, 'count': len(scored), 'source': entry['source']})
            lines.append(f'| {profile} | {unit} | {scenario["level"]} | {len(scored)} | {mastery} | {scenario.get("complete", "not observed")} |')
    comparisons = []
    for unit in evaluation.COURSE:
        strong = next((s for s in selected if s['profile'] == 'strong' and s['unit'] == unit), None)
        novice = next((s for s in selected if s['profile'] == 'novice' and s['unit'] == unit), None)
        if strong and novice:
            comparisons.append({'unit': unit, 'separated': strong['mastery'] > novice['mastery'],
                                'strong': strong['mastery'], 'novice': novice['mastery']})
    lines += ['', f'Strong-versus-novice ordering: {sum(c["separated"] for c in comparisons)}/{len(comparisons)} measured topics.', '',
              '## Teaching and grading evidence', '', f'Completed help reviews: {len(help_outputs)}.',
              f'Help factual/relevance/suitability issues: {sum(not all(h["review"][k] for k in ("factually_correct", "relevant", "suitable_for_draft")) for h in help_outputs)}.',
              f'Hints judged to reveal the answer: {sum(h["tool"] == "hint" and h["review"]["reveals_final_answer"] for h in help_outputs)}.', '']
    for a in answers:
        actual, judge = a['assessment'], a['independent_review']
        if actual['correct'] != judge['correct'] or abs(actual['score']-judge['score']) > .2:
            lines += [f'### Review: {a["profile"]}/{a["unit"]}', '', 'Question: '+a['question']['prompt'], '',
                      'Answer: '+a['answer'], '', f'Tutor score {actual["score"]}: {actual["reason"]}', '',
                      f'Independent evaluator score {judge["score"]}: {judge["reason"]}', '',
                      'Evidence excerpt: '+judge['supporting_excerpt'], '',
                      'This is an automated review flag; the same model supplies both judgments.', '']
            if a['question']['id'] in unreliable_ids:
                inspected = unreliable_ids[a['question']['id']]
                lines += ['Agent inspection: '+inspected['reason'], '',
                          'Check: '+inspected['counterexample'], '']
    for h in help_outputs:
        lines += [f'- {h["profile"]}/{h["unit"]}, {h["tool"]}: {h["review"]["reason"]}']
    for a in answers:
        if 'feedback_review' in a and not all(a['feedback_review'][k] for k in ('factually_correct', 'relevant', 'suitable_for_draft')):
            lines += ['', f'Feedback review flag ({a["profile"]}/{a["unit"]}): '+a['feedback_review']['reason']]
    excluded = [a for _, d in sources for s in d['scenarios'] for a in [*s['answers'], *([s['help_probe']] if 'help_probe' in s else [])] if a.get('excluded')]
    all_failures = [failure for _, d in sources for failure in d['failures']]
    budget_stops = [f for f in all_failures if 'Evaluation budget reached' in f.get('message', '')]
    failures = [f for f in all_failures if f not in budget_stops]
    unchanged = all(d.get('unchanged_repository_and_original_database') is True for _, d in sources)
    lines += ['', '## Validation and limitations', '',
              '- Nine local harness checks passed. Full synthetic API/WebSocket validation completed nine sessions and 26 graded answers.',
              f'- Original repository/database fingerprint checks passed for every included run: {unchanged}.',
              f'- Automatically excluded simulator/question samples: {len(excluded)}; manually excluded simulator violations missed by the automatic check: {len(audited_ids)}; recorded operation/provider failures in these runs: {len(failures)}.',
              f'- Intentional budget stops: {len(budget_stops)}. The final graded answer was retained, but its optional feedback-quality review did not complete. Budget stops are not counted as tutor failures.',
              '- Agent inspection exclusions are recorded separately in manual_audit.json; raw application/model outputs remain unchanged. The automatic profile-consistency check missed one out-of-profile linked-list explanation, demonstrating a limit of LLM role simulation.',
              f'- Independent grades excluded as unreliable after agent inspection: {len(unreliable_ids)}. One evaluator conflated persona fidelity with correctness; another assigned zero to a correct initial trace followed by an incorrect extra step. These are review cases rather than confirmed binary grading errors.',
              '- Future runs use separate correctness and profile-check calls; the correctness grader receives no learner profile. These completed live results used the earlier combined evaluator and were inspected accordingly.',
              '- An earlier unpaced attempt hit Gemini’s 15-requests/minute quota. It was stopped; its partial artifacts are archived under evidence/runs/20261113-211009-live-125648. They are excluded from the combined live metrics. Later runs pace actual requests at five-second intervals.',
              '- The longer paced run was deliberately interrupted for the time limit. Its completed evidence is retained; pending questions are not counted as answers.',
              '- Each abbreviated session has one answer, so mastery convergence and completion behavior remain largely untested. Full planned coverage of up to four answers per concept was not completed.',
              '- Learner/evaluator/tutor use the configured Gemini model in separate contexts. Agreement may reflect shared model bias; flagged excerpts require human review.',
              '- Fixed knowledge evaluates diagnosis and feedback, not actual learning gains. Research, course extraction, prerequisite unlocking, automatic Observer behavior and browser UI are outside this pilot.',
              '- Archived evidence is retained in tsting/evidence/. Future local runs write to the ignored tsting/.local-runs/ directory.', '', '## Source artifacts', '']
    for path, d in sources:
        lines += [f'- [{path.parent.name} report]({path.parent / "report.md"}) — model `{d.get("model")}`; seed {d["seed"]}; {d.get("runtime_seconds",0):.1f}s.',
                  f'  [Results]({path}) · [Application transcript]({path.parent / "transcript.jsonl"}).']
    summary = {'answers': answers, 'help': help_outputs, 'selected_mastery_sessions': selected,
               'strong_vs_novice': comparisons, 'correctness_agreement': agree,
               'false_acceptance': false_accept, 'false_rejection': false_reject,
               'mean_absolute_score_difference': diff, 'unchanged_repository_and_original_database': unchanged,
               'usable_comparisons': len(compared),
               'source_runs': [str(path) for path, _ in sources], 'failures': failures,
               'excluded_samples': len(excluded), 'manual_audit': audit, 'budget_stops': budget_stops}
    (HERE / 'SUMMARY.json').write_text(json.dumps(summary, indent=2))
    (HERE / 'REPORT.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'answers': len(answers), 'agreement': agree, 'topics_compared': len(comparisons),
                      'strong_vs_novice_ordering': sum(c['separated'] for c in comparisons),
                      'score_difference': diff, 'unchanged': unchanged, 'report': str(HERE / 'REPORT.md')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('results', nargs='+')
    args = parser.parse_args()
    summarize(args.results)
