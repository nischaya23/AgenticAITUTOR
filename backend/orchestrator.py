"""Python routing with snapshot checks before committing teaching output."""
from copy import deepcopy
from sqlalchemy import select
from . import assessment, config, knowledge, memory, observer, planner, reflection, student_model, tools, tutor, utils
from .db import Event, Session, append_event


def context_for(db, session_id: int, query: str = '') -> dict:
    row = db.get(Session, session_id)
    state = memory.get_short_term_memory(db, session_id)
    problem = state.get('current_problem', {})
    public_problem = {k: v for k, v in problem.items() if k not in ('expected_answer', 'rubric')}
    history = memory.get_relevant_history(db, session_id)
    # Avoid sending policy matrices and the internal event stream to the model.
    history = [e for e in history if e['type'] in ('student_message', 'problem_attempt', 'intervention', 'assessment')][-12:]
    model = student_model.get_student_model(db, row.user_id)
    return dict(concept=state['current_concept'], problem=public_problem,
        workspace=state.get('workspace', ''), plan=state.get('plan', {}),
        student={k: v for k, v in model.items() if k != 'policy'}, history=history,
        knowledge=tools.retrieve_knowledge(f'{state["current_concept"]} {query[:1000]}',
                                           material_id=state.get('material_id')),
        prerequisites=tools.get_prerequisites(db, state['current_concept'],
                                             material_id=state.get('material_id')), query=query)


def persist_observation(db, row, state: dict, kind: str, data: dict):
    model = memory.get_long_term_memory(db, row.user_id)
    policy = model['policy'] or observer.new_policy()
    if not model['policy']:
        memory.update_long_term_memory(db, row.user_id, {'policy': policy})
    pending = state.get('observer', {}).get('pending_intervention') or state.get('observer', {}).get('pending_decision')
    if pending and utils.now()-pending['at'] > 600:
        append_event(db, row.id, 'reflection', {'intervention_id': pending.get('event_id'),
            'reward': None, 'evidence': 'Outcome window expired; no reward assigned.'})
    action, o, features = observer.observe(state, kind, data,
        model['mastery'].get(state['current_concept'], .5), policy)
    state['observer'] = o
    if action == 'WAKE':
        append_event(db, row.id, 'observer_trigger', {'decision': action, 'features': features,
                                                     'revision': state['revision']})
    memory.update_short_term_memory(db, row.id, state)
    return action


def ingest(db, session_id: int, event: utils.ClientEvent) -> dict:
    row = db.get(Session, session_id)
    if row is None:
        raise ValueError('Session not found.')
    state = deepcopy(row.state)
    if state.get('ended'):
        raise ValueError('Session has ended. Start a new session.')
    data = event.model_dump(mode='json')
    if event.type == 'student_message' and not event.content.strip():
        raise ValueError('A message cannot be empty.')
    if event.type == 'feedback':
        pending = state.get('observer', {}).get('pending_intervention')
        if event.rating is None or not pending or event.intervention_id != pending['event_id']:
            raise ValueError('Feedback must reference the currently pending intervention.')
    if event.type == 'preferences':
        if event.proactive_enabled is None:
            raise ValueError('Set proactive_enabled to true or false.')
        state['proactive_enabled'] = event.proactive_enabled
    if event.type == 'problem_attempt' and not event.content.strip():
        raise ValueError('Enter an answer before submitting.')
    if event.type == 'problem_attempt' and event.problem_id != state['current_problem'].get('id'):
        raise ValueError('This attempt belongs to an old problem. Refresh the workspace.')
    explicit = event.type == 'student_message' or event.action == 'hint_request'
    if explicit:
        o = state.get('observer', {})
        pending = o.pop('pending_intervention', None) or o.pop('pending_decision', None)
        o.pop('pending_decision', None)
        if pending:
            append_event(db, session_id, 'reflection', {'intervention_id': pending.get('event_id'),
                'reward': None, 'evidence': 'New explicit help request censors the previous outcome window.'})
    if event.action in ('edit', 'revert'):
        state['workspace'] = event.content
    if event.action in ('edit', 'revert', 'problem_opened') or explicit or event.type in ('problem_attempt', 'session_end'):
        state['revision'] += 1
    if event.type == 'session_end':
        state['ended'] = True
    received_at = utils.now()
    entry = append_event(db, session_id, event.type, {**data, 'revision': state['revision'], 'received_at': received_at})
    reflection_result = None
    if event.type == 'feedback':
        reflection_result = reflection.reflect_on_intervention(db, session_id, row.user_id,
                                                               state, data, entry.id)
    decision = persist_observation(db, row, state, event.type, data)
    return dict(event_id=entry.id, session_id=session_id, revision=state['revision'], explicit=explicit,
                received_at=received_at,
                requested=event.tool or ('hint' if event.action == 'hint_request' else None),
                kind=event.type, content=event.content, decision=decision,
                problem=deepcopy(state['current_problem']), concept=state['current_concept'],
                assessment_mode=event.assessment_mode,
                replan=bool(reflection_result and reflection_result['replan']))


def clear_pending_wake(db, session_id: int):
    state = memory.get_short_term_memory(db, session_id)
    state['observer'].pop('pending_wake', None)
    memory.update_short_term_memory(db, session_id, state)


async def deliver_intervention(job: dict, explicit: bool) -> dict | None:
    sid = job['session_id']
    from .db import SessionLocal
    with SessionLocal.begin() as db:
        row = db.get(Session, sid)
        state = deepcopy(row.state)
        if state['ended'] or (not explicit and (state['revision'] != job['revision'] or not state['proactive_enabled'])):
            clear_pending_wake(db, sid)
            utils.METRICS['stale_interventions'] += 1
            return None
        context = context_for(db, sid, job['content'])
        snapshot = state['revision']
        needs_plan = not state.get('plan', {}).get('steps')
    plan = await planner.create_learning_plan(context) if needs_plan else context['plan']
    context['plan'] = plan
    selection = await planner.select_intervention(context, explicit, job.get('requested'))
    if selection['action'] == 'none':
        if explicit:
            selection = {'action': 'explanation', 'strategy': 'Answer the explicit question.'}
        else:
            with SessionLocal.begin() as db:
                clear_pending_wake(db, sid)
                append_event(db, sid, 'plan', {'decision': 'none', 'trigger_event_id': job['event_id']})
            return None
    result = await tutor.respond_to_student(context, selection)
    result['metadata']['retrieved_sources'] = [dict(id=c['id'], source=c['source']) for c in context['knowledge']]
    with SessionLocal.begin() as db:
        row = db.get(Session, sid)
        current = deepcopy(row.state)
        # Read fresh after all awaits; new edits, problems and questions invalidate proactive output.
        changed_problem = current['current_problem'].get('id') != job['problem'].get('id')
        if current['ended'] or changed_problem or (not explicit and
                (current['revision'] != snapshot or not current['proactive_enabled'])):
            clear_pending_wake(db, sid)
            utils.METRICS['stale_interventions'] += 1
            return None
        if db.scalar(select(Event.id).where(Event.session_id == sid, Event.type == 'intervention',
                Event.data['trigger_event_id'].as_integer() == job['event_id'])):
            clear_pending_wake(db, sid)
            return None
        # Workspace highlight offsets must match delivery-time text even for explicit requests.
        tools.validate_target(result, {**context, 'workspace': current.get('workspace', '')})
        append_event(db, sid, 'tool_call', {'name': result['type'], 'trigger_event_id': job['event_id'],
                                           'result': result})
        entry = append_event(db, sid, 'intervention', {**result, 'proactive': not explicit,
                             'trigger_event_id': job['event_id'], 'revision': current['revision']})
        o = current['observer']
        o.pop('pending_wake', None)
        o['last_intervention_at'] = utils.now()
        if result['type'] == 'hint':
            o['hint_times'] = o.get('hint_times', [])[-9:] + [utils.now()]
        if not explicit:
            o.pop('pending_decision', None)
            o['pending_intervention'] = {'action': 'WAKE', 'event_id': entry.id,
                'features': o.get('wake_features', {}), 'tool': result['type'], 'at': utils.now()}
        current['plan'] = plan
        memory.update_short_term_memory(db, sid, current)
        if needs_plan:
            model = memory.get_long_term_memory(db, row.user_id)
            memory.update_long_term_memory(db, row.user_id,
                {'learning_goals': list(dict.fromkeys(model['learning_goals'] + plan['learning_goals']))[-10:]})
        utils.METRICS['interventions'] += 1
        utils.METRICS['proactive_interventions' if not explicit else 'explicit_interventions'] += 1
        return {'type': 'intervention', 'event_id': entry.id, 'proactive': not explicit,
                'result': result, 'plan': plan}


async def handle(job: dict) -> list[dict]:
    """One background job; main.py serializes agent jobs, never event ingestion."""
    from .db import SessionLocal
    sid = job['session_id']
    outputs = []
    if job['kind'] == 'problem_attempt':
        with SessionLocal() as db:
            context = context_for(db, sid)
        result = await assessment.evaluate_attempt(job['problem'], job['content'], context,
                                                   job.get('assessment_mode') == 'reasoning')
        with SessionLocal.begin() as db:
            row = db.get(Session, sid)
            state = deepcopy(row.state)
            if state['current_problem'].get('id') != job['problem'].get('id') or state['ended']:
                return []
            entry = append_event(db, sid, 'assessment', {**result, 'trigger_event_id': job['event_id']})
            already_solved = bool(state['current_problem'].get('solved'))
            if not already_solved:
                student_model.update_mastery(db, row.user_id, job['concept'], result['score'], entry.id, result['verified'])
                student_model.update_misconceptions(db, row.user_id, job['concept'],
                    assessment.diagnose_misconception(result), entry.id)
            evidence = {**result, 'observed_at': job['received_at']}
            outcome = reflection.reflect_on_intervention(db, sid, row.user_id, state, evidence, entry.id) if not already_solved else None
            if result['verified'] and result['correct']:
                state['current_problem']['solved'] = True
            job['replan'] = bool(outcome and outcome['replan'])
            job['decision'] = persist_observation(db, row, state, 'assessment',
                result if not already_solved else {**result, 'verified': False})
            job['revision'] = state['revision']
            outputs.append({'type': 'assessment', 'event_id': entry.id, 'result': result})
    if job['replan']:
        with SessionLocal() as db:
            context = context_for(db, sid)
            revision = db.get(Session, sid).state['revision']
        plan = await planner.revise_plan(context)
        with SessionLocal.begin() as db:
            state = memory.get_short_term_memory(db, sid)
            if state['revision'] == revision and not state['ended']:
                memory.update_short_term_memory(db, sid, {'plan': plan})
                append_event(db, sid, 'plan', {'plan': plan, 'reason': 'unsuccessful outcome',
                                             'trigger_event_id': job['event_id']})
                outputs.append({'type': 'plan', 'plan': plan})
    if job['kind'] == 'session_end':
        with SessionLocal() as db:
            context = context_for(db, sid)
        result = await reflection.reflect_on_session(context)
        with SessionLocal.begin() as db:
            row = db.get(Session, sid)
            model = memory.get_long_term_memory(db, row.user_id)
            append_event(db, sid, 'reflection', {**result, 'scope': 'session'})
            memory.update_long_term_memory(db, row.user_id, {'memories': model['memories']+[result['summary']],
                                                            'learning_goals': [result['next_goal']]})
        outputs.append({'type': 'session_summary', 'result': result})
    elif job['explicit'] or job['decision'] == 'WAKE':
        try:
            result = await deliver_intervention(job, job['explicit'])
            if result:
                outputs.append(result)
        finally:
            with SessionLocal.begin() as db:
                clear_pending_wake(db, sid)
    return outputs
