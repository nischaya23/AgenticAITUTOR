"""Outcome summaries, evidence-linked feedback, and optional session synthesis."""
from . import workflow, prompts, utils
from .activity import record
from .db import append_event
from .memory import get_long_term_memory, update_long_term_memory

def reflect_on_intervention(db, session_id: int, user_id: int, state: dict, evidence: dict, evidence_id: int) -> dict | None:
    o = dict(state.get('observer', {}))
    pending = o.get('pending_intervention')
    if not pending:
        record('reflection', 'Intervention reflection skipped: no pending intervention to review.')
        return None
    if evidence.get('observed_at', utils.now()) < pending['at']:
        reward, reason = (None, 'Attempt was submitted before the intervention; no reward assigned.')
    elif utils.now() - pending['at'] > 600:
        reward, reason = (None, 'Outcome window expired; no reward assigned.')
    elif evidence.get('rating') in (-1, 1):
        reward, reason = (float(evidence['rating']), 'Explicit student feedback; subjective interruption/utility signal.')
    elif evidence.get('verified'):
        reward = 0.5 if evidence['correct'] else -0.25
        reason = 'Verified exact-answer outcome; temporal association does not establish causality.'
    else:
        record('reflection', 'Intervention reflection deferred: LLM grading is provisional; no verified outcome or explicit feedback.')
        return None
    record('reflection', 'Reviewing intervention outcome: ' + reason)
    data = get_long_term_memory(db, user_id)
    if reward is not None:
        successes = data['effective_interventions']
        kind = pending.get('tool', pending['action'])
        bucket = dict(successes.get(kind, {'observations': 0, 'reward_sum': 0}))
        bucket['observations'] += 1
        bucket['reward_sum'] += reward
        successes[kind] = bucket
        update_long_term_memory(db, user_id, {'effective_interventions': successes, 'memories': data['memories'] + [f'{kind}: {reason} Reward proxy {reward}; evidence event {evidence_id}.']})
    entry = append_event(db, session_id, 'reflection', dict(intervention_id=pending.get('event_id'), evidence_event_id=evidence_id, reward=reward, evidence=reason, causal_claim=False, action=pending['action']))
    o.pop('pending_intervention', None)
    state['observer'] = o
    return {'event_id': entry.id, 'reward': reward, 'replan': reward is not None and reward < 0}

class SessionReflection(utils.Schema):
    summary: str
    next_goal: str

@workflow.stage('Reflection · Review assessment', 'agents')
async def reflect_on_answer(context: dict, assessment: dict) -> dict:
    record('reflection', 'Reflecting on the answer and prior help; LLM-based conclusions remain tentative.')
    return (await utils.call_llm(prompts.REFLECT_ON_ANSWER, {'context': context, 'assessment': assessment}, schema=SessionReflection, operation='REFLECT_ON_ANSWER')).model_dump()
