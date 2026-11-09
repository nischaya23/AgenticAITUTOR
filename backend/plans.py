"""Material-level learning plans and explicit student-controlled revisions."""
from uuid import uuid4
from typing import Literal
from .utils import Schema
from pydantic import Field
from . import knowledge, memory, planner, logging_setup as logs
from .db import Session, append_event


def view(state):
    return dict(type='plan', plan=state.get('plan', {}), plan_version=state.get('plan_version', 0),
                plan_suggestion=state.get('plan_suggestion'))


async def ensure(session_id, factory):
    with factory() as db:
        row = db.get(Session, session_id)
        if row.state.get('plan', {}).get('steps'):
            return view(row.state)
        from .orchestrator import context_for
        context = context_for(db, session_id)
        material = knowledge.get_material(row.state['material_id'])
        context.update(material_graph=material['graph'], knowledge=material['chunks'][:4])
    plan = await planner.create_learning_plan(context)
    with factory.begin() as db:
        state = memory.get_short_term_memory(db, session_id)
        if not state.get('plan', {}).get('steps') and not state['ended']:
            memory.update_short_term_memory(db, session_id, dict(plan=plan,
                plan_version=state.get('plan_version', 0)+1, plan_suggestion=None))
            append_event(db, session_id, 'plan_created', {'plan': plan})
        result = view(db.get(Session, session_id).state)
    logs.event('plan.initialized', plan_version=result['plan_version'])
    return result


def suggest(plan, state, reason):
    return dict(id=uuid4().hex, plan=plan, reason=reason, base_version=state.get('plan_version', 0))


class Update(Schema):
    plan: planner.Plan
    expected_version: int = Field(ge=0)


class SuggestionAction(Schema):
    suggestion_id: str
    expected_version: int = Field(ge=0)
    action: Literal['accept', 'dismiss']
