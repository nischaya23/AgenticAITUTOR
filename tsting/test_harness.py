"""Offline safety and failure-path checks for the standalone evaluation harness."""
import importlib.util
import hashlib
import json
from pathlib import Path
import re
import unittest

spec = importlib.util.spec_from_file_location('evaluation', Path(__file__).with_name('evaluate.py'))
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


class HarnessTests(unittest.TestCase):
    def test_archived_evidence_matches_export_hashes(self):
        root = Path(__file__).resolve().parent
        manifest = json.loads((root / 'EVIDENCE_MANIFEST.json').read_text())
        for run in manifest['runs']:
            for record in run['files']:
                self.assertEqual(hashlib.sha256((root / record['path']).read_bytes()).hexdigest(),
                                 record['sha256'], record['path'])

    def test_archived_denominators_and_inspection_exclusions(self):
        root = Path(__file__).resolve().parent
        summary = json.loads((root / 'SUMMARY.json').read_text())
        audit = json.loads((root / 'manual_audit.json').read_text())
        answers = [answer for source in summary['source_runs']
                   for scenario in json.loads((root / source).read_text())['scenarios']
                   for answer in scenario['answers'] if 'assessment' in answer]
        self.assertEqual(len(answers), 13)
        valid = [answer for answer in answers if answer['question']['id'] not in audit['excluded_question_ids']]
        self.assertEqual(len(valid), 11)
        compared = [answer for answer in valid if answer['question']['id'] not in audit['unreliable_evaluator_question_ids']]
        self.assertEqual(len(compared), 9)
        self.assertEqual(summary['usable_comparisons'], len(compared))
        self.assertEqual(summary['correctness_agreement'], sum(answer['assessment']['correct'] == answer['independent_review']['correct'] for answer in compared))

    def test_consolidated_report_links_are_portable(self):
        root = Path(__file__).resolve().parent
        for target in re.findall(r'\]\(([^)]+)\)', (root / 'REPORT.md').read_text()):
            self.assertFalse(Path(target).is_absolute(), target)
            self.assertTrue((root / target).exists(), target)

    def test_shared_pacer_reserves_nonoverlapping_attempt_slots(self):
        pacer = evaluation.RequestPacer(5.0)
        first, second, third = pacer.delay(), pacer.delay(), pacer.delay()
        self.assertLess(first, .1)
        self.assertGreater(second, 4.9)
        self.assertGreater(third, 9.9)
    def test_learner_cannot_see_private_reference_or_teacher_memory(self):
        problem = {'id': 'p', 'concept': 'Binary Search', 'prompt': 'Find x.',
                   'expected_answer': 'SECRET_REFERENCE', 'rubric': 'SECRET_RUBRIC',
                   'assessment': 'SECRET_GRADE', 'material': 'SECRET_COURSE', 'student': 'SECRET_MEMORY'}
        payload = evaluation.learner_payload('novice', 'boundaries', problem)
        self.assertNotIn('SECRET_', str(payload))
        self.assertNotIn(evaluation.COURSE['boundaries']['text'], str(payload))
        self.assertIn('left=mid', payload['fixed_knowledge'])

    def test_scrubber_removes_nested_references(self):
        self.assertEqual(evaluation.clean({'expected_answer': 'secret', 'items': [{'rubric': 'secret', 'content': 'public'}]}),
                         {'items': [{'content': 'public'}]})

    def test_deterministic_checker_requires_unambiguous_convention(self):
        prompt = 'Give the zero-based index of the first occurrence in [1,2,2,5], target=2.'
        self.assertTrue(evaluation.deterministic_answer(prompt, '1')['correct'])
        self.assertFalse(evaluation.deterministic_answer(prompt, '2')['correct'])
        self.assertIsNone(evaluation.deterministic_answer(prompt.replace('zero-based', ''), '1'))
        self.assertIsNone(evaluation.deterministic_answer(prompt.replace('target=2', 'target=9'), '-1'))
        self.assertIsNone(evaluation.deterministic_answer(prompt, 'The index is 1.'))

    def test_timeout_does_not_replay_submission(self):
        class SilentSocket:
            def __init__(self): self.sent = []
            def send_json(self, message): self.sent.append(message)
            def receive_json(self):
                # Supply an unrelated event then end. Still must never resubmit.
                raise EOFError()
        ws = SilentSocket()
        socket = evaluation.Socket(ws, lambda *args: None, .05)
        with self.assertRaises(RuntimeError):
            socket.event({'type': 'problem_attempt', 'problem_id': 'p'}, 'assessment')
        self.assertEqual(len(ws.sent), 1)

    def test_correlate_result_then_wait_for_job_completion(self):
        import queue
        class FakeSocket:
            def send_json(self, message): pass
        socket = object.__new__(evaluation.Socket)
        socket.ws, socket.trace, socket.timeout = FakeSocket(), lambda *args: None, .1
        socket.queue = queue.Queue()
        for message in [
            {'type': 'assessment', 'event_id': 999},  # stale result before our ack
            {'type': 'ack', 'event_type': 'problem_attempt', 'event_id': 12},
            {'type': 'assessment', 'event_id': 13, 'result': {'correct': False}},
            {'type': 'study_state', 'state': {}},
            {'type': 'busy', 'active': False},
        ]: socket.queue.put(message)
        result = socket.event({'type': 'problem_attempt', 'problem_id': 'p'}, 'assessment')
        self.assertEqual(result['ack']['event_id'], 12)
        self.assertEqual(result['output']['event_id'], 13)

    def test_timeout_without_messages_never_resubmits(self):
        import queue
        class FakeSocket:
            sent = 0
            def send_json(self, message): self.sent += 1
        socket = object.__new__(evaluation.Socket)
        socket.ws, socket.trace, socket.timeout = FakeSocket(), lambda *args: None, .01
        socket.queue = queue.Queue()
        with self.assertRaises(TimeoutError):
            socket.event({'type': 'problem_attempt', 'problem_id': 'p'}, 'assessment')
        self.assertEqual(socket.ws.sent, 1)

    def test_simulator_violation_excluded_without_submission(self):
        class FakeModels:
            def call(self, role, payload, schema):
                if schema is evaluation.LearnerAnswer: return schema(text='violates profile')
                if schema is evaluation.ProfileReview:
                    return schema(profile_consistent=False, profile_reason='Knows forbidden skill.')
                assert 'fixed_knowledge' not in payload, 'Correctness grader must be blind to learner persona'
                return schema(question_valid=True, question_issue='', correct=True, score=1,
                              reason='Correct but invalid simulator.', supporting_excerpt='violates profile')
        obj = object.__new__(evaluation.Evaluation)
        obj.models, obj.check_budget, obj.save = FakeModels(), lambda: None, lambda: None
        record = {}
        answer, _ = obj.make_answer('novice', 'boundaries', {'prompt': 'question'}, record)
        self.assertIsNone(answer)
        self.assertEqual(record['excluded'], 'simulator_profile_violation')
        self.assertEqual(len(record['generation_attempts']), 3)

    def test_partial_report_survives_provider_failure(self):
        data = {'mode': 'live', 'model': 'test', 'seed': 23, 'revision': 'test', 'scenarios': [],
                'failures': [{'category': 'provider', 'type': 'APIConnectionError'}]}
        report = evaluation.render_report(data)
        self.assertIn('Graded answers: 0', report)
        self.assertIn('APIConnectionError', report)
        self.assertIn('unavailable', report)


if __name__ == '__main__':
    unittest.main()
