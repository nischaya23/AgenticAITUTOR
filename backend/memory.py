"""Compact state plus a bounded view of the append-only history."""
from copy import deepcopy
from .activity import record
from sqlalchemy import select
from .db import Session, StudentState, Event
from .utils import SessionData, StudentData


def get_observer_memory(db, user_id: int) -> dict:
    row = db.get(StudentState, user_id)
    data = row.data if row else {}
    return {'mastery': deepcopy(data.get('mastery', {})), 'policy': deepcopy(data.get('policy'))}


def get_short_term_memory(db, session_id: int) -> dict:
    row = db.get(Session, session_id)
    if row is None:
        raise ValueError('Session not found')
    record('memory_read', 'Retrieved current question, reasoning, and session context.')
    return deepcopy(row.state)


def update_short_term_memory(db, session_id: int, updates: dict) -> dict:
    state = get_short_term_memory(db, session_id)
    state.update(deepcopy(updates))
    state = SessionData.model_validate(state).model_dump(mode='json')
    db.get(Session, session_id).state = state
    record('memory_update', 'Updated session memory: ' + ', '.join(sorted(updates)) + '.')
    return deepcopy(state)


def get_long_term_memory(db, user_id: int) -> dict:
    row = db.get(StudentState, user_id)
    record('memory_read', 'Retrieved learning memory: mastery, misconceptions, and prior summaries.')
    return deepcopy(row.data) if row else StudentData().model_dump(mode='json')


def update_long_term_memory(db, user_id: int, updates: dict) -> dict:
    data = get_long_term_memory(db, user_id)
    data.update(deepcopy(updates))
    data['memories'] = data.get('memories', [])[-30:]
    data = StudentData.model_validate(data).model_dump(mode='json')
    row = db.get(StudentState, user_id)
    if row:
        row.data = data
    else:
        db.add(StudentState(user_id=user_id, data=data))
    record('memory_update', 'Updated learning memory: ' + ', '.join(sorted(updates)) + '.')
    return deepcopy(data)


def get_relevant_history(db, session_id: int, limit: int = 20) -> list[dict]:
    rows = db.scalars(select(Event).where(Event.session_id == session_id)
                      .order_by(Event.id.desc()).limit(min(limit, 100))).all()
    record('memory_read', 'Retrieved relevant session history.')
    return [dict(id=e.id, type=e.type, data=deepcopy(e.data)) for e in reversed(rows)]
