"""Compact state plus a bounded view of the append-only history."""
from copy import deepcopy
from . import workflow
from .activity import record
from sqlalchemy import select
from .db import Session, StudentState, Event
from .utils import SessionData, StudentData, normalize_mastery


def get_observer_memory(db, user_id: int) -> dict:
    row = db.get(StudentState, user_id)
    data = row.data if row else {}
    return {'mastery': normalize_mastery(data.get('mastery', {})), 'policy': deepcopy(data.get('policy'))}


def get_short_term_memory(db, session_id: int) -> dict:
    row = db.get(Session, session_id)
    if row is None:
        raise ValueError('Session not found')
    record('memory_read', 'Retrieved current question, reasoning, and session context.')
    workflow.memory_read(db, 'session memory', row.state, revision=row.state.get('revision', 0), response_revision=row.state.get('response_revision', 0))
    return deepcopy(row.state)


def update_short_term_memory(db, session_id: int, updates: dict) -> dict:
    state = get_short_term_memory(db, session_id)
    before = deepcopy(state)
    state.update(deepcopy(updates))
    if 'workspace' in updates and 'response' not in updates:
        from .response import response_for
        response = response_for(state)
        state['response'] = {**response, 'text': updates['workspace']}
    from .response import response_for
    state['response'] = response_for(state)
    state = SessionData.model_validate(state).model_dump(mode='json')
    db.get(Session, session_id).state = state
    workflow.memory_write(db, 'session memory', before, state)
    if state.get('plan_version') != before.get('plan_version'):
        workflow.version_artifact('plan', state.get('plan', {}), state.get('plan_version', 0), 'produced', db=db)
    record('memory_update', 'Updated session memory: ' + ', '.join(sorted(updates)) + '.')
    return deepcopy(state)


def get_long_term_memory(db, user_id: int) -> dict:
    row = db.get(StudentState, user_id)
    record('memory_read', 'Retrieved learning memory: mastery, misconceptions, and prior summaries.')
    if not row:
        data = StudentData().model_dump(mode='json')
        workflow.memory_read(db, 'learning memory', data, memory_revision=0)
        return data
    data = {**StudentData().model_dump(mode='json'), **deepcopy(row.data)}
    workflow.memory_read(db, 'learning memory', data, memory_revision=data.get('memory_revision', 0))
    data['mastery'] = normalize_mastery(data.get('mastery', {}))
    return data


def update_long_term_memory(db, user_id: int, updates: dict, evidence_id: int | None = None) -> dict:
    data = get_long_term_memory(db, user_id)
    before = deepcopy(data)
    reconcile_items(db, user_id, data, legacy=True)
    data.update(deepcopy(updates))
    data['memories'] = data.get('memories', [])[-30:]
    data['memory_revision'] = get_long_term_memory(db, user_id).get('memory_revision', 0) + 1
    data = reconcile_items(db, user_id, data, evidence_id=evidence_id)
    data = StudentData.model_validate(data).model_dump(mode='json')
    row = db.get(StudentState, user_id)
    if row:
        row.data = data
    else:
        db.add(StudentState(user_id=user_id, data=data))
    workflow.memory_write(db, 'learning memory', before, data)
    record('memory_update', 'Updated learning memory: ' + ', '.join(sorted(updates)) + '.')
    return deepcopy(data)


def get_relevant_history(db, session_id: int, limit: int = 20) -> list[dict]:
    rows = db.scalars(select(Event).where(Event.session_id == session_id, Event.type.in_(['student_message', 'problem_attempt', 'intervention', 'assessment', 'feedback', 'memory_correction']))
                      .order_by(Event.id.desc()).limit(min(limit, 100))).all()
    record('memory_read', 'Retrieved relevant session history.')
    result = [dict(id=e.id, type=e.type, data=deepcopy(e.data)) for e in reversed(rows)]
    workflow.memory_read(db, 'session history', result)
    return result


def reconcile_items(db, user_id, data, evidence_id=None, legacy=False):
    """Maintain stable editable entries and apply existing student corrections."""
    from uuid import uuid4
    from .db import MemoryItem, utcnow
    items = list(db.scalars(select(MemoryItem).where(MemoryItem.user_id == user_id)))
    active = set()
    for kind, field in [('summary', 'memories'), ('agent_goal', 'learning_goals'),
                        ('student_goal', 'student_goals'), ('misconception', 'misconceptions')]:
        values = []
        for raw in data.get(field, []):
            text = raw.get('hypothesis', '') if isinstance(raw, dict) else raw
            concept = raw.get('concept') if isinstance(raw, dict) else None
            item = next((i for i in items if i.kind == kind and i.data.get('concept') == concept
                         and text in (i.data['original_text'], i.data['effective_text'])), None)
            if item is None:
                item = MemoryItem(id=uuid4().hex, user_id=user_id, kind=kind,
                    data=dict(original_text=text, effective_text=text, concept=concept,
                        source_event_id=raw.get('evidence_event_id') if isinstance(raw, dict) else evidence_id,
                        updated_at=None if legacy else utcnow().isoformat(),
                        disputed=False, student_note='', edits=[]))
                db.add(item)
                items.append(item)
            active.add(item.id)
            if isinstance(raw, dict):
                values.append({**raw, 'hypothesis': item.data['effective_text'],
                    'status': 'disputed' if item.data['disputed'] else raw.get('status', 'unconfirmed'),
                    'student_note': item.data['student_note']})
            else:
                values.append(item.data['effective_text'])
        data[field] = values
    for item in items:
        item.data = {**item.data, 'active': item.id in active}
    return data


def memory_snapshot(db, session_id):
    from .db import MemoryItem
    from .response import response_for
    row = db.get(Session, session_id)
    data = reconcile_items(db, row.user_id, get_long_term_memory(db, row.user_id), legacy=True)
    state = row.state
    return dict(revision=data.get('memory_revision', 0),
        short_term=dict(concept=state.get('current_concept'),
            question=state.get('current_problem', {}).get('prompt'), response=response_for(state),
            plan=state.get('plan', {}), assessment=state.get('last_assessment'),
            hypotheses=state.get('hypothesis_state'), assessment_pending=state.get('assessment_pending', False)),
        long_term={k: data.get(k) for k in ('mastery', 'learning_goals', 'student_goals', 'memories', 'hypotheses', 'misconceptions')},
        items=[dict(id=i.id, kind=i.kind, **i.data) for i in db.scalars(
            select(MemoryItem).where(MemoryItem.user_id == row.user_id)) if i.data.get('active')])


def correct_memory(db, session_id, correction):
    from .db import MemoryItem, append_event, utcnow
    from uuid import uuid4
    row = db.get(Session, session_id)
    data = get_long_term_memory(db, row.user_id)
    if data.get('memory_revision', 0) != correction.expected_revision:
        raise ValueError('Memory changed. Review the latest memory and retry.')
    reconcile_items(db, row.user_id, data, legacy=True)
    if correction.action == 'add_goal':
        data['student_goals'] = data.get('student_goals', []) + [correction.text]
    else:
        item = db.get(MemoryItem, correction.item_id)
        if item is None or item.user_id != row.user_id or not item.data.get('active'):
            raise ValueError('Memory item not found.')
        if item.kind not in ('summary', 'student_goal', 'agent_goal', 'misconception'):
            raise ValueError('This memory is read-only.')
        if item.kind == 'misconception' and correction.action != 'dispute':
            raise ValueError('Add a dispute explaining the misconception.')
        if item.kind != 'misconception' and correction.action != 'edit':
            raise ValueError('Use edit for goals and summaries.')
        before = item.data['effective_text']
        note = dict(at=utcnow().isoformat(), action=correction.action, before=before, text=correction.text)
        item.data = {**item.data, 'effective_text': before if correction.action == 'dispute' else correction.text,
            'disputed': correction.action == 'dispute', 'student_note': correction.text,
            'updated_at': note['at'], 'edits': item.data.get('edits', [])+[note]}
        field = {'summary': 'memories', 'agent_goal': 'learning_goals', 'student_goal': 'student_goals',
                 'misconception': 'misconceptions'}[item.kind]
        if item.kind != 'misconception':
            data[field] = [correction.text if x == before else x for x in data.get(field, [])]
    append_event(db, session_id, 'memory_correction', correction.model_dump())
    update_long_term_memory(db, row.user_id, data)
    db.flush()
    return memory_snapshot(db, session_id)
