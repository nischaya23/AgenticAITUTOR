"""Outcome summaries, evidence-linked rewards, and optional session synthesis."""
from . import config, utils
from .db import append_event
from .memory import get_long_term_memory, update_long_term_memory
from .observer import update_policy, new_policy


def reflect_on_intervention(db, session_id: int, user_id: int, state: dict,
                            evidence: dict, evidence_id: int) -> dict | None:
    o = dict(state.get('observer', {}))
    pending = o.get('pending_intervention') or o.get('pending_decision')
    if not pending:
        return None
    if evidence.get('observed_at', utils.now()) < pending['at']:
        reward, reason = None, 'Attempt was submitted before the intervention; no reward assigned.'
    elif utils.now()-pending['at'] > 600:
        reward, reason = None, 'Outcome window expired; no reward assigned.'
    elif evidence.get('rating') in (-1, 1):
        reward, reason = float(evidence['rating']), 'Explicit student feedback; subjective interruption/utility signal.'
    elif evidence.get('verified'):
        reward = .5 if evidence['correct'] else -.25
        reason = 'Verified exact-answer outcome; temporal association does not establish causality.'
    else:
        return None
    data = get_long_term_memory(db, user_id)
    if reward is not None:
        policy = update_policy(data['policy'] or new_policy(), pending['action'], pending['features'], reward)
        successes = data['effective_interventions']
        kind = pending.get('tool', pending['action'])
        bucket = dict(successes.get(kind, {'observations': 0, 'reward_sum': 0}))
        bucket['observations'] += 1
        bucket['reward_sum'] += reward
        successes[kind] = bucket
        update_long_term_memory(db, user_id, {'policy': policy, 'effective_interventions': successes,
            'memories': data['memories'] + [f'{kind}: {reason} Reward proxy {reward}; evidence event {evidence_id}.']})
    entry = append_event(db, session_id, 'reflection', dict(
        intervention_id=pending.get('event_id'), evidence_event_id=evidence_id,
        reward=reward, evidence=reason, causal_claim=False, action=pending['action']))
    o.pop('pending_intervention', None)
    o.pop('pending_decision', None)
    state['observer'] = o
    return {'event_id': entry.id, 'reward': reward, 'replan': reward is not None and reward < 0}


class SessionReflection(utils.Schema):
    summary: str
    next_goal: str


async def reflect_on_session(context: dict) -> dict:
    if config.DEMO_MODE:
        return dict(summary=f'Practiced {context["concept"]}; retain assessment evidence for the next session.',
                    next_goal=f'Demonstrate {context["concept"]} independently')
    return (await utils.call_llm(
        'Summarize this completed study session in at most 120 words and choose one next goal. '
        'Separate verified outcomes from tentative judgments; do not claim intervention causality.',
        context, schema=SessionReflection)).model_dump()
