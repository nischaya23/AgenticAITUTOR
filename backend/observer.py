"""Collect the activity signals supplied to the Observer Agent."""
from . import utils


def observe(state: dict, kind: str, data: dict, timestamp: float | None = None) -> dict:
    t = utils.now() if timestamp is None else timestamp
    o = dict(state.get('observer', {}))
    for key in ('pending_decision', 'pending_wake', 'wake_features', 'intervention_response_key',
                'last_decision_at', 'retired_outcome'):
        o.pop(key, None)
    if o.get('status', {}).get('decision_source') != 'llm':
        o.pop('status', None)
    o.setdefault('problem_started_at', t)
    o.setdefault('last_action_at', t)

    if data.get('action') == 'problem_opened':
        o.update(problem_started_at=t, last_action_at=t, failure_streak=0, revert_count=0, edit_times=[])
        o.pop('last_edit_at', None)
    if kind == 'assessment':
        o['failure_streak'] = 0 if data['correct'] else o.get('failure_streak', 0) + 1
    action = data.get('action')
    if kind in ('problem_attempt', 'student_message', 'code_execution') or action in ('edit', 'revert', 'hint_request'):
        o['last_action_at'] = t
    if action in ('edit', 'revert'):
        o['last_edit_at'] = t
        o['edit_times'] = [v for v in o.get('edit_times', []) if t - v < 60][-29:] + [t]
    if action == 'revert':
        o['revert_count'] = o.get('revert_count', 0) + 1
    return o


def activity_context(state: dict, now: float) -> dict:
    o = state.get('observer', {})
    last_help = o.get('last_intervention_at')
    return {
        'seconds_since_activity': max(0, now - o.get('last_action_at', now)),
        'seconds_on_question': max(0, now - o.get('problem_started_at', now)),
        'seconds_since_help': max(0, now - last_help) if last_help is not None else None,
        'edits_in_last_minute': sum(now - t < 60 for t in o.get('edit_times', [])),
        'reverts': o.get('revert_count', 0),
        'incorrect_attempts_in_a_row': o.get('failure_streak', 0),
        'last_assessment': state.get('last_assessment'),
    }
