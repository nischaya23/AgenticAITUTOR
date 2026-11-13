"""Compact state plus a bounded view of the append-only history."""
from copy import deepcopy
from . import workflow
from .activity import record
from sqlalchemy import select
from .db import Session, StudentState, Event
from .utils import SessionData, StudentData, normalize_mastery, normalize_concept

CONCEPT_NOTES = 5
CONCEPT_EVIDENCE = 10
ARCHIVED_NOTES = 50
PROFILE_CHARS = 600
HYPOTHESES_PER_CONCEPT = 8


def hypotheses_for_concepts(items, concepts) -> list[dict]:
    wanted = {normalize_concept(name) for name in concepts if name}
    return [item for item in items if normalize_concept(item.get('concept')) in wanted]


def merge_assessed_hypotheses(existing, concept, assessed) -> list[dict]:
    groups = {normalize_concept(concept): []}
    for item in assessed:
        groups.setdefault(normalize_concept(item['concept']), []).append(item)
    retained = [item for item in existing if normalize_concept(item.get('concept')) not in groups]
    return retained + [item for group in groups.values() for item in group[:HYPOTHESES_PER_CONCEPT]]


def migrate_legacy(data: dict) -> dict:
    """Old flat memory had untagged `memories` and transient long-term `learning_goals`."""
    legacy = data.pop('memories', None)
    if legacy:
        data['archived_notes'] = (list(data.get('archived_notes') or []) + [m for m in legacy if isinstance(m, str)])[-ARCHIVED_NOTES:]
    data.pop('learning_goals', None)
    return data


def concept_entry(data: dict, name: str) -> dict:
    """The mutable concept-memory entry for `name`, created when missing."""
    key = normalize_concept(name)
    entry = data.setdefault('concept_memory', {}).setdefault(key, {'name': name})
    entry.setdefault('name', name)
    return entry


def record_concept_reflection(data: dict, concept: str, summary: str, next_goal: str,
                              material_id: str | None, evidence_id: int | None, profile_note: str = '') -> dict:
    """Replace (not append) the concept's rolling summary from one reflection."""
    from .db import utcnow
    entry = concept_entry(data, concept)
    entry.update(summary=summary.strip(), next_goal=next_goal.strip(), attempts=entry.get('attempts', 0) + 1,
                 updated_at=utcnow().isoformat())
    if material_id and material_id not in entry.setdefault('material_ids', []):
        entry['material_ids'].append(material_id)
    if evidence_id is not None:
        entry['evidence_event_ids'] = (entry.get('evidence_event_ids', []) + [evidence_id])[-CONCEPT_EVIDENCE:]
    note = (profile_note or '').strip()
    if note and note.casefold() not in data.get('learner_profile', '').casefold():
        profile = ' '.join(filter(None, [data.get('learner_profile', ''), note]))
        data['learner_profile'] = profile[-PROFILE_CHARS:].lstrip()
    return data


def add_concept_note(data: dict, concept: str, note: str) -> dict:
    entry = concept_entry(data, concept)
    entry['notes'] = (entry.get('notes', []) + [note])[-CONCEPT_NOTES:]
    return data


def scoped_long_term(data: dict, concepts, primary: str | None = None) -> dict:
    """Only the memory about `concepts`; untagged archived notes are never included."""
    keys = [normalize_concept(c) for c in ([primary] if primary else []) + list(concepts or []) if c]
    keys = list(dict.fromkeys(keys))
    wanted = set(keys)
    memories = data.get('concept_memory', {})
    hypotheses = hypotheses_for_concepts(data.get('hypotheses', []), keys)
    priority = {key: index for index, key in enumerate(keys)}
    hypotheses.sort(key=lambda item: priority[normalize_concept(item['concept'])])
    return dict(
        mastery={k: v for k, v in data.get('mastery', {}).items() if normalize_concept(k) in wanted},
        concept_memory={k: memories[k] for k in keys if k in memories},
        misconceptions=[m for m in data.get('misconceptions', []) if normalize_concept(m.get('concept')) in wanted],
        hypotheses=hypotheses[:HYPOTHESES_PER_CONCEPT],
        student_goals=list(data.get('student_goals', [])),
        learner_profile=data.get('learner_profile', ''),
        effective_interventions=deepcopy(data.get('effective_interventions', {})),
        memory_revision=data.get('memory_revision', 0))


def get_mastery(db, user_id: int) -> dict:
    row = db.get(StudentState, user_id)
    data = row.data if row else {}
    return normalize_mastery(data.get('mastery', {}))


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
    data = migrate_legacy({**StudentData().model_dump(mode='json'), **deepcopy(row.data)})
    data.pop('policy', None)
    workflow.memory_read(db, 'learning memory', data, memory_revision=data.get('memory_revision', 0))
    data['mastery'] = normalize_mastery(data.get('mastery', {}))
    return data


def update_long_term_memory(db, user_id: int, updates: dict, evidence_id: int | None = None) -> dict:
    data = get_long_term_memory(db, user_id)
    before = deepcopy(data)
    reconcile_items(db, user_id, data, legacy=True)
    data.update(deepcopy(updates))
    migrate_legacy(data)
    data['archived_notes'] = data.get('archived_notes', [])[-ARCHIVED_NOTES:]
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


def get_relevant_history(db, session_id: int, limit: int = 20, *, concepts=None) -> list[dict]:
    kinds = ['student_message', 'problem_attempt', 'intervention', 'assessment', 'feedback']
    if concepts is None:
        kinds.append('memory_correction')
    relevant = Event.type.in_(kinds)
    opening = (Event.type == 'student_action') & (Event.data['action'].as_string() == 'problem_opened')
    rows = db.scalars(select(Event).where(Event.session_id == session_id, relevant if concepts is None else relevant | opening)
                      .order_by(Event.id.desc()).limit(min(limit, 100) if concepts is None else 100)).all()
    if concepts is not None:
        wanted = {normalize_concept(name) for name in concepts if name}
        last_concept = ''
        matched = []
        for row in reversed(rows):
            if row.type == 'student_action':
                # Older teaching events can inherit the concept of their question.
                last_concept = normalize_concept(row.data.get('concept'))
            elif normalize_concept(row.data.get('concept') or last_concept) in wanted:
                matched.append(row)
        rows = list(reversed(matched))[:min(limit, 100)]
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

    def item_for(kind, text, concept=None, source=None):
        item = next((i for i in items if i.kind == kind and i.data.get('concept') == concept
                     and text in (i.data['original_text'], i.data['effective_text'])), None)
        if item is None:
            item = MemoryItem(id=uuid4().hex, user_id=user_id, kind=kind,
                data=dict(original_text=text, effective_text=text, concept=concept, source_event_id=source,
                    updated_at=None if legacy else utcnow().isoformat(),
                    disputed=False, student_note='', edits=[]))
            db.add(item)
            items.append(item)
        active.add(item.id)
        return item

    # One rolling summary and one suggested next goal per concept.
    for key, entry in data.get('concept_memory', {}).items():
        name = entry.get('name') or key
        source = (entry.get('evidence_event_ids') or [evidence_id])[-1]
        for kind, field in (('summary', 'summary'), ('agent_goal', 'next_goal')):
            if entry.get(field):
                entry[field] = item_for(kind, entry[field], name, source).data['effective_text']
    if data.get('learner_profile'):
        data['learner_profile'] = item_for('profile', data['learner_profile'], source=evidence_id).data['effective_text']
    for note in data.get('archived_notes', []):
        item_for('archived', note)
    data['student_goals'] = [item_for('student_goal', goal, source=evidence_id).data['effective_text']
                             for goal in data.get('student_goals', [])]
    values = []
    for raw in data.get('misconceptions', []):
        item = item_for('misconception', raw.get('hypothesis', ''), raw.get('concept'), raw.get('evidence_event_id'))
        values.append({**raw, 'hypothesis': item.data['effective_text'],
            'status': 'disputed' if item.data['disputed'] else raw.get('status', 'unconfirmed'),
            'student_note': item.data['student_note']})
    data['misconceptions'] = values
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
        long_term={k: data.get(k) for k in ('mastery', 'student_goals', 'concept_memory', 'learner_profile',
                                            'archived_notes', 'hypotheses', 'misconceptions')},
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
        if item.kind not in ('summary', 'student_goal', 'agent_goal', 'profile', 'misconception'):
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
        if item.kind in ('summary', 'agent_goal'):
            entry = data.get('concept_memory', {}).get(normalize_concept(item.data.get('concept')))
            if entry is not None:
                entry['summary' if item.kind == 'summary' else 'next_goal'] = correction.text
        elif item.kind == 'profile':
            data['learner_profile'] = correction.text
        elif item.kind == 'student_goal':
            data['student_goals'] = [correction.text if x == before else x for x in data.get('student_goals', [])]
    append_event(db, session_id, 'memory_correction', correction.model_dump())
    update_long_term_memory(db, row.user_id, data)
    db.flush()
    return memory_snapshot(db, session_id)
