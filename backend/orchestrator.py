"""Python routing with snapshot checks before committing teaching output."""
from copy import deepcopy
from sqlalchemy import select
from . import workflow, hypothesis, plans, activity, assessment, memory, observer, observer_review, planner, reflection, student_model, tools, tutor, utils
from .response import Response, response_for, content_key, has_content
from .logging_setup import event as log_event
from .db import Event, Session, append_event


def context_for(db, session_id: int, query: str = '', role: str = 'teaching') -> dict:
    from .context import for_session
    return for_session(db, session_id, query, role)


def persist_observation(db, row, state: dict, kind: str, data: dict):
    state['observer'] = observer.observe(state, kind, data)
    memory.update_short_term_memory(db, row.id, state)


def _ingest(db, session_id: int, event: utils.ClientEvent) -> dict:
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
        if event.rating is None or not pending or pending.get('feedback_received') or event.intervention_id != pending['event_id']:
            raise ValueError('Feedback must reference the currently pending intervention.')
    if event.type == 'feedback':
        state['observer']['pending_intervention']['feedback_received'] = True
    if event.type == 'preferences':
        if event.proactive_enabled is None:
            raise ValueError('Set proactive_enabled to true or false.')
        if event.proactive_enabled != state['proactive_enabled']:
            state['revision'] += 1
            state.get('observer', {}).pop('status', None)
        state['proactive_enabled'] = event.proactive_enabled
    submitted = event.response.model_dump() if event.response is not None else Response(text=event.content).model_dump()
    if event.type == 'problem_attempt' and not has_content(submitted):
        raise ValueError('Enter an answer before submitting.')
    if event.type == 'problem_attempt' and (state.get('assessment_pending') or state.get('current_problem', {}).get('solved')):
        raise ValueError('This answer is already being assessed or solved. Retry the next question instead.')
    if event.type == 'problem_attempt' and event.problem_id != state['current_problem'].get('id'):
        raise ValueError('This attempt belongs to an old problem. Refresh the workspace.')
    if (event.type in ('problem_attempt', 'student_message') or event.action == 'hint_request') and not state.get('current_problem', {}).get('id'):
        raise ValueError('Upload material or choose a question first.')
    explicit = event.type == 'student_message' or event.action == 'hint_request'
    draft_changed = False
    if explicit:
        o = state.get('observer', {})
        pending = o.pop('pending_intervention', None)
        if pending:
            append_event(db, session_id, 'intervention_closed', {'intervention_id': pending.get('event_id'),
                'reward': None, 'evidence': 'New explicit help request censors the previous outcome window.'})
    if event.action in ('edit', 'revert') or event.type == 'problem_attempt':
        previous = response_for(state)
        response = submitted if event.type == 'problem_attempt' or event.response is not None else {**previous, 'text': event.content}
        if previous != response:
            state['response_revision'] = state.get('response_revision', 0)+1
            if content_key(previous) != content_key(response):
                draft_changed = True
                state['draft_changed_at'] = utils.now()
            if state.get('hypothesis_state'):
                state['hypothesis_state']['status'] = 'stale'
        state.update(response=response, workspace=response['text'])
    if event.type == 'problem_attempt':
        state['assessment_pending'] = True
        state['submitted_response'] = deepcopy(submitted)
    if event.action in ('edit', 'revert', 'problem_opened') or explicit or event.type in ('problem_attempt', 'session_end'):
        state['revision'] += 1
    if event.type == 'session_end':
        state['ended'] = True
    received_at = utils.now()
    entry = append_event(db, session_id, event.type, {**data, 'revision': state['revision'], 'received_at': received_at})
    if draft_changed:
        state['last_draft_event_id'] = entry.id
    if event.type == 'problem_attempt':
        state['pending_assessment_event_id'] = entry.id
    persist_observation(db, row, state, event.type, data)
    return dict(event_id=entry.id, session_id=session_id, revision=state['revision'], explicit=explicit,
                received_at=received_at,
                requested=event.tool or ('hint' if event.action == 'hint_request' else None),
                kind=event.type, action=event.action, content=event.content,
                problem=deepcopy(state['current_problem']), concept=state['current_concept'],
                response=deepcopy(submitted) if event.type == 'problem_attempt' else None,
                unified=event.response is not None, response_revision=state.get('response_revision', 0),
                draft_changed=draft_changed,
                plan_version=state.get('plan_version', 0),
                assessment_mode=event.assessment_mode,
                replan=False)


def ingest(db, session_id: int, event: utils.ClientEvent) -> dict:
    row = db.get(Session, session_id)
    if row is None:
        raise ValueError('Session not found.')
    trace = workflow.create_run(row.user_id, 'Answer assessment' if event.type == 'problem_attempt' else
        'Student request' if event.type == 'student_message' or event.action == 'hint_request' else
        'Student activity' if event.type == 'student_action' else event.type.replace('_', ' ').capitalize(),
        session_id=session_id, trigger={'action': event.action, 'event_type': event.type}, db=db)
    token = workflow.CURRENT.set(trace)
    try:
        with workflow.local_span(db, 'Record student activity'):
            job = _ingest(db, session_id, event)
        if trace:
            job['workflow_run_id'] = trace.run_id
            workflow.update_run_data(db=db, trigger_event_id=job['event_id'], response_revision=job['response_revision'],
                plan_version=job['plan_version'], question_id=job['problem'].get('id'))
            queued = job['explicit'] or job['kind'] in ('problem_attempt', 'session_end', 'feedback')
            if not queued:
                workflow.finish(db=db)
        return job
    finally:
        workflow.CURRENT.reset(token)


async def _deliver_intervention(job: dict, explicit: bool) -> dict | None:
    sid = job['session_id']
    from .db import SessionLocal
    with SessionLocal.begin() as db:
        row = db.get(Session, sid)
        state = deepcopy(row.state)
        if state['ended'] or (not explicit and (state['revision'] != job['revision']
                or not observer_review.allowed(state) or state.get('assessment_pending'))):
            workflow.outcome('discarded', 'Question, response or context changed', db=db)
            utils.METRICS['stale_interventions'] += 1
            return None
        context = context_for(db, sid, job['content'])
        if job.get('observer_snapshot') and observer_review.snapshot(
                state, context['snapshot']['memory_revision']) != job['observer_snapshot']:
            workflow.outcome('discarded', 'Observer decision no longer matches the session', db=db)
            return None
        snapshot = state['revision']
        needs_plan = not state.get('plan', {}).get('steps')
        planning_context = context_for(db, sid, job['content'], role='planning') if needs_plan else None
    plan = await planner.create_learning_plan(planning_context) if needs_plan else context['plan']
    context['plan'] = plan
    from . import research, knowledge
    from . import operations
    if job.get('requested') == 'web_search' or (explicit and not job.get('requested')) or not context['knowledge']:
        pages = await research.investigate(context, required=job.get('requested') == 'web_search')
        if pages:
            with SessionLocal() as db:
                material_id = db.get(Session, sid).state.get('material_id')
            if material_id:
                knowledge.add_web_documents(material_id, pages, context['concept'])
                with SessionLocal() as db:
                    context = context_for(db, sid, job['content'])
            else:
                context['knowledge'] += [dict(id=p['url'],source=p['title'],text=p['text'][:2000]) for p in pages]
    with SessionLocal() as db:
        scores = planner.tool_scores(db, sid, job['problem']['id'], context['workspace'])
    selection = await planner.select_intervention(context, explicit,
        'explanation' if job.get('requested') == 'web_search' else job.get('requested'), scores=scores)
    result = await tutor.respond_to_student(context, selection)
    result['metadata']['retrieved_sources'] = [dict(id=c['id'], source=c['source'], url=c.get('url')) for c in context['knowledge']]
    with SessionLocal.begin() as db:
        row = db.get(Session, sid)
        current = deepcopy(row.state)
        # Read fresh after all awaits; new edits, problems and questions invalidate proactive output.
        changed_problem = current['current_problem'].get('id') != job['problem'].get('id')
        model=memory.get_long_term_memory(db,row.user_id)
        context_changed = current.get('plan_version',0)!=context.get('plan_version',0) or model.get('memory_revision',0)!=context.get('snapshot',{}).get('memory_revision',model.get('memory_revision',0))
        if current['ended'] or changed_problem or (not explicit and
                (current['revision'] != snapshot or not observer_review.allowed(current)
                 or current.get('assessment_pending') or context_changed)):
            workflow.outcome('discarded', 'Question, response or context changed', db=db)
            utils.METRICS['stale_interventions'] += 1
            return None
        if db.scalar(select(Event.id).where(Event.session_id == sid, Event.type == 'intervention',
                Event.data['trigger_event_id'].as_integer() == job['event_id'])):
            return None
        # Workspace highlight offsets must match delivery-time text even for explicit requests.
        if result['type'] == 'highlight' and response_for(current) != context['response']:
            raise ValueError('Response changed during review. Request a new review.')
        tools.validate_target(result, {**context, 'workspace': response_for(current)['text'], 'response': response_for(current)})
        append_event(db, sid, 'tool_call', {'name': result['type'], 'trigger_event_id': job['event_id'],
                                           'result': result})
        entry = append_event(db, sid, 'intervention', {**result, 'proactive': not explicit,
                             'trigger_event_id': job['event_id'], 'revision': current['revision'],
                             'problem_id': current['current_problem']['id']})
        o = current['observer']
        o['last_intervention_at'] = utils.now()
        previous = o.pop('pending_intervention', None)
        if previous:
            append_event(db, sid, 'intervention_closed', {'intervention_id': previous.get('event_id'),
                'reward': None, 'evidence': 'A new intervention superseded the previous outcome window.'})
        if not explicit:
            o['pending_intervention'] = {'action': 'WAKE', 'event_id': entry.id,
                'tool': result['type'], 'at': utils.now()}
        if needs_plan:
            current['plan'] = plan
            current['plan_version'] = current.get('plan_version', 0)+1
        memory.update_short_term_memory(db, sid, current)
        utils.METRICS['interventions'] += 1
        utils.METRICS['proactive_interventions' if not explicit else 'explicit_interventions'] += 1
        return {'type': 'intervention', 'event_id': entry.id, 'proactive': not explicit,
                'result': result, **{k: v for k, v in plans.view(current).items() if k != 'type'}, 'revision': current['revision']}


async def _assess(job):
    from .db import SessionLocal
    sid = job['session_id']
    with SessionLocal() as db:
        state = deepcopy(db.get(Session, sid).state)
        if state['ended'] or state['current_problem'].get('id') != job['problem'].get('id') or state.get('plan_version', 0) != job['plan_version']:
            workflow.outcome('discarded', 'Submission question or plan changed')
            return []
        context = context_for(db, sid, job['content'])
        # The submission snapshot is authoritative even if the student continues editing.
        context.update(response=job['response'], workspace=job['response']['text'])
        context['snapshot']['response_revision'] = job['response_revision']
        prior = hypothesis.eligible(state)
    result = await assessment.evaluate_attempt(job['problem'],
        job['response'] if job['unified'] else job['content'], context,
        job.get('assessment_mode') == 'reasoning')
    with SessionLocal.begin() as db:
        row = db.get(Session, sid)
        state = deepcopy(row.state)
        if state['ended'] or state['current_problem'].get('id') != job['problem'].get('id') or state.get('plan_version', 0) != job['plan_version']:
            workflow.outcome('discarded', 'Submission question or plan changed', db=db)
            return []
        entry = append_event(db, sid, 'assessment', {**result, 'trigger_event_id': job['event_id'],
            'response': job['response'], 'response_revision': job['response_revision']})
        evidence_id = entry.id
        student_model.update_mastery(db, row.user_id, job['concept'], result['score'], entry.id, result['verified'])
        student_model.update_misconceptions(db, row.user_id, job['concept'], assessment.diagnose_misconception(result), entry.id)
        outcome = reflection.reflect_on_intervention(db, sid, row.user_id, state,
            {**result, 'observed_at': job['received_at']}, entry.id)
        if result['correct'] and (result.get('method') == 'llm' or result['verified']):
            state['current_problem']['solved'] = True
        state.update(last_assessment={**result, 'event_id': entry.id},
            assessment_stages={'reflection': 'analyzing', 'hypotheses': 'waiting'})
        job['replan'] = bool(outcome and outcome['replan'])
        persist_observation(db, row, state, 'assessment', result)
        if not result['correct']:
            job['incorrect_assessment_id'] = evidence_id
        job['revision'] = state['revision']
    # Assessment is committed before either optional model stage can fail.
    summary = None
    with SessionLocal() as db:
        context = context_for(db,sid,job['content'])
        context.update(response=job['response'],workspace=job['response']['text'])
        context['snapshot']['response_revision'] = job['response_revision']
    try:
        summary = await reflection.reflect_on_answer(context, result)
    except Exception as exc:
        log_event('reflection.failed', error=type(exc).__name__)
        workflow.record('reflection.fallback', 'errors', 'warnings', message='Reflection unavailable; assessment retained.')
        activity.record('reflection', 'Reflection unavailable; assessment retained.')
    with SessionLocal.begin() as db:
        row = db.get(Session, sid)
        state = deepcopy(row.state)
        valid = not state['ended'] and state['current_problem'].get('id') == job['problem'].get('id') \
            and state.get('plan_version', 0) == job['plan_version']
        if not valid:
            return [{'type': 'assessment', 'event_id': evidence_id, 'result': result}]
        if summary:
            model = memory.get_long_term_memory(db, row.user_id)
            memory.record_concept_reflection(model, job['concept'], summary.get('concept_summary') or summary['summary'], summary['next_goal'],
                state.get('material_id'), evidence_id, summary.get('profile_note', ''))
            memory.update_long_term_memory(db, row.user_id, {'concept_memory': model['concept_memory'],
                'learner_profile': model['learner_profile']}, evidence_id=evidence_id)
            append_event(db, sid, 'reflection', {**summary, 'scope': 'answer', 'tentative': not result['verified'], 'evidence_event_id': evidence_id})
        state['assessment_stages'] = {'reflection': 'ready' if summary else 'unavailable', 'hypotheses': 'analyzing'}
        memory.update_short_term_memory(db, sid, state)
    with SessionLocal() as db:
        context=context_for(db,sid,job['content'])
        context.update(response=job['response'],workspace=job['response']['text'])
        context['snapshot']['response_revision'] = job['response_revision']
    log_event('hypothesis.start', session_id=sid, origin='submission', response_revision=job['response_revision'], plan_version=job['plan_version'])
    try:
        items = await hypothesis.analyze(context, prior, 'submission', f'event:{evidence_id}', result, summary)
        status, error = 'ready', None
    except Exception as exc:
        log_event('hypothesis.failed', session_id=sid, origin='submission', error=type(exc).__name__)
        workflow.record('hypothesis.fallback', 'errors', 'warnings', message='Submission hypothesis analysis unavailable; prior hypotheses retained')
        items, status, error = state.get('assessed_hypotheses', []), 'unavailable', 'Submission analysis unavailable; using prior hypotheses.'
    with SessionLocal.begin() as db:
        row = db.get(Session, sid)
        state = deepcopy(row.state)
        if state['ended'] or state['current_problem'].get('id') != job['problem'].get('id') \
                or state.get('plan_version', 0) != job['plan_version']:
            workflow.record('hypothesis.discarded', status='discarded', db=db, message='Submission hypothesis snapshot changed')
            log_event('hypothesis.discarded', session_id=sid, origin='submission')
            return [{'type': 'assessment', 'event_id': evidence_id, 'result': result}]
        snapshot = {**state, 'response_revision': job['response_revision']}
        panel = hypothesis.panel_for(snapshot, items, 'submission', status, error=error)
        state.update(hypothesis_state=panel, assessment_pending=False,
            assessment_stages={'reflection': 'ready' if summary else 'unavailable', 'hypotheses': status})
        if status == 'ready':
            state['assessed_hypotheses'] = items
            state['assessed_hypothesis_version'] = panel['version']
            workflow.version_artifact('hypotheses', items, panel['version'], 'produced', db=db)
            memory.update_long_term_memory(db, row.user_id, {'hypotheses': items})
            append_event(db, sid, 'hypotheses', {'items': items, 'evidence_event_id': evidence_id,
                'question_id': panel['question_id'], 'plan_version': panel['plan_version']})
        memory.update_short_term_memory(db, sid, state)
        panel = hypothesis.view(state)
    activity.record('short_term', 'Answer assessed: ' + ('correct.' if result['correct'] else 'incorrect; keep practicing this question.'))
    activity.record('reflection_summary', summary['summary'] if summary else 'Reflection unavailable; assessment retained.')
    activity.record('long_term', 'Updated provisional learning evidence and submission hypotheses.' if status == 'ready' else error)
    return [{'type': 'assessment', 'event_id': evidence_id, 'result': result,
             'stages': state['assessment_stages']}, {'type': 'hypotheses', 'state': panel}]


async def _handle(job: dict) -> list[dict]:
    """One background job; main.py serializes agent jobs, never event ingestion."""
    from .db import SessionLocal
    sid = job['session_id']
    outputs = []
    if job['kind'] == 'feedback':
        with SessionLocal.begin() as db:
            row = db.get(Session, sid)
            state = deepcopy(row.state)
            entry = db.get(Event, job['event_id'])
            pending = state.get('observer', {}).get('pending_intervention')
            if pending and pending['event_id'] == entry.data['intervention_id']:
                outcome = reflection.reflect_on_intervention(db, sid, row.user_id, state,
                    {'rating': entry.data['rating'], 'observed_at': job['received_at']}, entry.id)
                memory.update_short_term_memory(db, sid, state)
                job['replan'] = bool(outcome and outcome['replan'])
    if job['kind'] == 'problem_attempt':
        outputs.extend(await _assess(job))
    if job['replan']:
        with SessionLocal() as db:
            context = context_for(db, sid, role='planning')
            revision = db.get(Session, sid).state['revision']
        plan = await planner.revise_plan(context)
        with SessionLocal.begin() as db:
            state = memory.get_short_term_memory(db, sid)
            if state['revision'] == revision and not state['ended']:
                memory.update_short_term_memory(db, sid, {'plan_suggestion': plans.suggest(plan, state, 'Adjusting the approach after intervention feedback.')})
                append_event(db, sid, 'plan_suggested', {'plan': plan, 'plan_version': state.get('plan_version', 0), 'reason': 'unsuccessful outcome',
                                             'trigger_event_id': job['event_id']})
                outputs.append(plans.view(db.get(Session, sid).state))
    if job['kind'] == 'session_end':
        outputs.append({'type': 'session_summary', 'result': {'summary': 'Session ended. Answer reflections were saved during grading.', 'next_goal': 'Resume your learning when ready.'}})
    if job['kind'] != 'session_end' and (job['explicit'] or job.get('observer_approved')):
        result = await deliver_intervention(job, job['explicit'])
        if result:
            outputs.append(result)
    return outputs


async def deliver_intervention(job, explicit):
    with activity.collect() as items:
        result = await _deliver_intervention(job, explicit)
        if result:
            result['activity'] = activity.group(job, job.get('requested') or 'Teaching tool', items)
        return result


async def handle(job):
    with activity.collect(job['kind'] == 'problem_attempt') as items:
        outputs = await _handle(job)
        groups = []
        for output in outputs:
            group = output.pop('activity', None)
            if group:
                groups.append(group)
        if job['kind'] == 'problem_attempt' and any(o['type'] == 'assessment' for o in outputs):
            groups.insert(0, activity.group(job, 'Answer assessment', items))
        if groups:
            outputs.append({'type': 'memory_activity', 'items': groups})
        return outputs
