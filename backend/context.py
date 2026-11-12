"""One bounded, role-aware context contract for all model-facing agents."""
from copy import deepcopy
import json
from . import workflow, config, memory, knowledge
from .db import Session
from .response import response_for


def bound(value):
    value = deepcopy(value)
    original_counts = {k: len(value.get(k, [])) for k in ('history', 'knowledge', 'summaries')}
    for field in ('history', 'knowledge', 'summaries'):
        while value.get(field) and len(json.dumps(value, ensure_ascii=False)) > config.MAX_INPUT_CHARS - 2000:
            value[field].pop(0 if field == 'history' else -1)
    if len(json.dumps(value, ensure_ascii=False)) > config.MAX_INPUT_CHARS:
        raise ValueError('The current task exceeds the context budget. Shorten the response or request.')
    omitted = {k: count-len(value.get(k, [])) for k, count in original_counts.items() if count > len(value.get(k, []))}
    if omitted:
        value['context_budget'] = {'omitted_items': omitted}
    return value


def preparation(db, user_id, request, documents=None):
    student = memory.get_long_term_memory(db, user_id)
    return bound(dict(learning_request=request, student=student,
        documents=documents or [], sources_are_untrusted=True))


def for_session(db, session_id, query='', role='teaching'):
    from . import hypothesis
    row = db.get(Session, session_id)
    state = memory.get_short_term_memory(db, session_id)
    student = memory.get_long_term_memory(db, row.user_id)
    problem = deepcopy(state.get('current_problem', {}))
    if role != 'assessment':
        problem = {k:v for k,v in problem.items() if k not in ('expected_answer', 'rubric')}
    material = knowledge.get_material(state['material_id']) if state.get('material_id') else {}
    result = bound(dict(concept=state.get('current_concept', ''), problem=problem,
        workspace=response_for(state)['text'], response=response_for(state), query=query,
        learning_request=material.get('learning_request', material.get('topic_description', '')),
        plan=state.get('plan', {}), plan_version=state.get('plan_version', 0),
        material_graph=material.get('graph', {}), graph_sources=material.get('graph_sources',[]), hypotheses=hypothesis.eligible(state),
        student=student,
        history=memory.get_relevant_history(db, session_id, 12),
        knowledge=knowledge.retrieve_knowledge(f"{state.get('current_concept', '')} {query[:1000]}",
            material_id=state.get('material_id'), user_id=row.user_id),
        prerequisites=knowledge.get_prerequisites(db, state.get('current_concept', ''),
            material_id=state.get('material_id'), user_id=row.user_id),
        snapshot=dict(question_id=problem.get('id'), revision=state['revision'],
            response_revision=state.get('response_revision', 0), plan_version=state.get('plan_version', 0),
            memory_revision=student.get('memory_revision', 0),
            hypothesis_version=hypothesis.context_version(state))))
    return result


def compact_payload(value):
    """Avoid duplicating the same long answer at several places in a model request."""
    if isinstance(value,list): return [compact_payload(v) for v in value]
    if not isinstance(value,dict): return value
    value={k:compact_payload(v) for k,v in value.items()}
    if isinstance(value.get('response'),dict) and value.get('workspace')==value['response'].get('text'):
        value.pop('workspace',None)
    if 'answer' in value and isinstance(value.get('context'),dict):
        value['context'].pop('response',None); value['context'].pop('workspace',None)
    return value
