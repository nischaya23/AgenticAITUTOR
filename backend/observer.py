"""Temporal observation and conservative LinUCB. This module never calls an LLM."""
import time
import numpy as np
from . import config
from .utils import METRICS, now

ACTIONS = ['WAIT', 'WAKE']
FEATURES = ['time_since_progress', 'time_since_action', 'failure_streak',
            'edit_frequency', 'revert_count', 'time_on_problem',
            'recent_hint_count', 'concept_mastery', 'time_since_intervention']
SCALES = np.array([180, 60, 5, 10, 5, 600, 3, 1, 300])


def new_policy() -> dict:
    return {'A': [np.eye(10).tolist() for _ in ACTIONS],
            'b': [np.zeros(10).tolist() for _ in ACTIONS], 'updates': 0}


def vector(features: dict) -> np.ndarray:
    return np.r_[1., np.clip([features[k] for k in FEATURES] / SCALES, 0, 3)]


def scores(policy: dict, features: dict) -> list[float]:
    x = vector(features)
    result = []
    for a, b in zip(policy['A'], policy['b']):
        inv_x = np.linalg.solve(np.asarray(a), x)
        result.append(float(np.asarray(b) @ inv_x + .1 * np.sqrt(x @ inv_x)))
    return result


def update_policy(policy: dict, action: str, features: dict, reward: float) -> dict:
    """Rewards are observational proxies; never interpreted as causal effects."""
    p = {**policy, 'A': list(policy['A']), 'b': list(policy['b'])}
    i, x = ACTIONS.index(action), vector(features)
    p['A'][i] = (np.array(p['A'][i]) + np.outer(x, x)).tolist()
    p['b'][i] = (np.array(p['b'][i]) + np.clip(reward, -1, 1) * x).tolist()
    p['updates'] += 1
    return p


def observe(state: dict, kind: str, data: dict, mastery: float, policy: dict,
            timestamp: float | None = None) -> tuple[str, dict, dict]:
    started = time.perf_counter()
    t = timestamp if timestamp is not None else now()
    o = dict(state.get('observer', {}))
    o.setdefault('problem_started_at', t)
    o.setdefault('last_progress_at', t)
    o.setdefault('last_action_at', t)
    # Unanswered interventions must not suppress help forever after a restart.
    for key in ('pending_intervention', 'pending_decision'):
        if key in o and t-o[key]['at'] >= config.COOLDOWN_SECONDS:
            pending = o.pop(key)
            o['retired_outcome'] = {'intervention_id': pending.get('event_id'), 'action': pending.get('action'),
                'reward': None, 'evidence': 'Cooldown elapsed without an outcome; retired for reevaluation.'}
    if data.get('action') == 'problem_opened':
        o.update(problem_started_at=t, last_progress_at=t, failure_streak=0,
                 revert_count=0, edit_times=[])
    if kind == 'assessment' and (data.get('verified') or data.get('method') == 'llm'):
        o['grading_signal'] = 'verified' if data.get('verified') else 'provisional LLM grading'
        if data['correct']:
            o.update(last_progress_at=t, failure_streak=0)
        else:
            o['failure_streak'] = o.get('failure_streak', 0) + 1
            o['last_failure_at'] = t
    if kind in ('problem_attempt', 'student_message', 'code_execution') or data.get('action') in ('edit', 'revert'):
        o['last_action_at'] = t
    if data.get('action') in ('edit', 'revert'):
        o['last_edit_at'] = t
        o['edit_times'] = [v for v in o.get('edit_times', []) if t - v < 60][-29:] + [t]
    if data.get('action') == 'revert':
        o['revert_count'] = o.get('revert_count', 0) + 1
    o['hint_times'] = [v for v in o.get('hint_times', []) if t - v < 600]
    f = dict(time_since_progress=max(0, t-o['last_progress_at']),
             time_since_action=max(0, t-o['last_action_at']),
             failure_streak=o.get('failure_streak', 0),
             edit_frequency=sum(t-v < 60 for v in o.get('edit_times', [])),
             revert_count=o.get('revert_count', 0),
             time_on_problem=max(0, t-o['problem_started_at']),
             recent_hint_count=len(o['hint_times']), concept_mastery=mastery,
             time_since_intervention=max(0, t-o.get('last_intervention_at', t-3600)))
    action = 'WAIT'
    reason = 'Waiting for more evidence of difficulty'
    evaluated_scores = None
    idle = f['time_since_action'] >= config.IDLE_SECONDS
    eligible = (f['failure_streak'] >= config.FAILURE_THRESHOLD and f['time_since_progress'] >= config.STALL_SECONDS) or idle
    if (state.get('ended') or state.get('study_complete') or state.get('current_problem', {}).get('solved') or not state.get('proactive_enabled', True)
            or o.get('pending_wake') or o.get('pending_intervention')
            or f['time_since_intervention'] < config.COOLDOWN_SECONDS):
        reason = 'Inactive or answered question' if state.get('ended') or state.get('study_complete') or state.get('current_problem', {}).get('solved') or not state.get('proactive_enabled', True) else 'Help is pending' if o.get('pending_wake') else 'Help cooldown'
        action = 'WAIT'
    elif t-o.get('last_edit_at', 0) < config.TYPING_GRACE_SECONDS:
        reason = 'You are still typing'
        action = 'DEFER'
    elif t-o.get('last_decision_at', 0) < config.AGGREGATION_SECONDS:
        reason = 'Gathering recent activity'
        action = 'DEFER'
    else:
        o['last_decision_at'] = t
        if eligible:
            s = scores(policy, f)
            evaluated_scores = dict(zip(ACTIONS, s))
            action = 'WAKE' if policy['updates'] < config.BANDIT_WARMUP or s[1] > s[0]+.05 else 'WAIT'
            signal = 'Inactivity eligible' if idle else 'Difficulty eligible (' + o.get('grading_signal', 'assessment signals') + ')'
            reason = signal + ('; policy WAIT' if action == 'WAIT' else '; evaluating whether to offer help')
        # Snapshot a WAIT only at an eligible decision, for comparable feedback.
        if eligible and action == 'WAIT' and not o.get('pending_decision'):
            o['pending_decision'] = {'action': 'WAIT', 'features': f, 'at': t}
    if action == 'WAKE':
        o['pending_wake'] = True
        o['wake_features'] = f
    o['status'] = dict(decision=action, reason=reason, timestamp=t, features=f, scores=evaluated_scores)
    METRICS[f'observer_{action.lower()}'] += 1
    METRICS['observer_events'] += 1
    METRICS['observer_latency_ms'] += (time.perf_counter()-started)*1000
    return action, o, f
