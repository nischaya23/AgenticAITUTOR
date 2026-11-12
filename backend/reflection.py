"""Outcome summaries, evidence-linked feedback, and optional session synthesis."""
from . import workflow, prompts, utils
from .activity import record
from .db import append_event
from .memory import add_concept_note, get_long_term_memory, update_long_term_memory

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
        updates = {'effective_interventions': successes}
        if state.get('current_concept'):
            # Help outcomes are remembered on the concept they happened in, not in a global list.
            add_concept_note(data, state['current_concept'], f'{kind}: {reason} Reward proxy {reward}; evidence event {evidence_id}.')
            updates['concept_memory'] = data['concept_memory']
        update_long_term_memory(db, user_id, updates)
    entry = append_event(db, session_id, 'reflection', dict(intervention_id=pending.get('event_id'), evidence_event_id=evidence_id, reward=reward, evidence=reason, causal_claim=False, action=pending['action']))
    o.pop('pending_intervention', None)
    state['observer'] = o
    return {'event_id': entry.id, 'reward': reward, 'replan': reward is not None and reward < 0}

class SessionReflection(utils.Schema):
    summary: str
    next_goal: str
    concept_summary: str = ''
    profile_note: str = ''

@workflow.stage('Reflection · Review assessment', 'agents')
async def reflect_on_answer(context: dict, assessment: dict) -> dict:
    record('reflection', 'Reflecting on the answer and prior help; LLM-based conclusions remain tentative.')
    student = context.get('student', {})
    prior = student.get('concept_memory', {}).get(utils.normalize_concept(context.get('concept')), {})
    payload = {'context': context, 'assessment': assessment,
               'prior_concept_summary': prior.get('summary', ''), 'learner_profile': student.get('learner_profile', '')}
    result = (await utils.call_llm(prompts.REFLECT_ON_ANSWER, payload, schema=SessionReflection, operation='REFLECT_ON_ANSWER')).model_dump()
    # Never lose the concept summary if the model leaves it empty.
    result['concept_summary'] = result['concept_summary'].strip() or ' '.join(filter(None, [prior.get('summary', ''), result['summary']]))
    return result
