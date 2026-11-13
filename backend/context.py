"""One bounded, role-aware context contract for all model-facing agents."""
from copy import deepcopy
import json
from . import workflow, config, memory, knowledge, utils
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
    # Before a concept graph exists, send only goals, the short profile and known concept names.
    data = memory.get_long_term_memory(db, user_id)
    student = dict(student_goals=data.get('student_goals', []), learner_profile=data.get('learner_profile', ''),
        known_concepts=dict(data.get('mastery', {})), memory_revision=data.get('memory_revision', 0))
    return bound(dict(learning_request=request, student=student,
        documents=documents or [], sources_are_untrusted=True))


def memory_scope(graph: dict, concept: str, role: str) -> list[str]:
    """Planner: the whole concept graph. Other agents: current concept and its direct prerequisites."""
    if role == 'planning' and graph.get('concepts'):
        return list(graph['concepts'])
    return [concept] + [e.get('prerequisite') for e in graph.get('prerequisites', []) if e.get('concept') == concept]


def for_session(db, session_id, query='', role='teaching', *, concept=None):
    from . import hypothesis
    row = db.get(Session, session_id)
    state = memory.get_short_term_memory(db, session_id)
    material = knowledge.get_material(state['material_id']) if state.get('material_id') else {}
    current_concept = state.get('current_concept', '')
    concept = current_concept if concept is None else concept
    scope = memory_scope(material.get('graph', {}), concept, role)
    same_concept = utils.normalize_concept(current_concept) == utils.normalize_concept(concept)
    problem = deepcopy(state.get('current_problem', {})) if same_concept else {}
    response = response_for(state) if same_concept else {'text': ''}
    if role != 'assessment':
        problem = {k:v for k,v in problem.items() if k not in ('expected_answer', 'rubric')}
    student = memory.scoped_long_term(memory.get_long_term_memory(db, row.user_id), scope, concept)
    hypotheses = hypothesis.eligible(state, scope, student['hypotheses']) if same_concept else student['hypotheses']
    result = bound(dict(concept=concept, problem=problem,
        workspace=response['text'], response=response, query=query,
        learning_request=material.get('learning_request', material.get('topic_description', '')),
        plan=state.get('plan', {}), plan_version=state.get('plan_version', 0),
        material_graph=material.get('graph', {}), graph_sources=material.get('graph_sources',[]),
        hypotheses=hypotheses, hypothesis_scope=scope,
        student=student,
        history=memory.get_relevant_history(db, session_id, 12, concepts=scope),
        knowledge=knowledge.retrieve_knowledge(f"{concept} {query[:1000]}",
            material_id=state.get('material_id'), user_id=row.user_id),
        prerequisites=knowledge.get_prerequisites(db, concept,
            material_id=state.get('material_id'), user_id=row.user_id),
        snapshot=dict(question_id=problem.get('id'), revision=state['revision'],
            response_revision=state.get('response_revision', 0), plan_version=state.get('plan_version', 0),
            memory_revision=student.get('memory_revision', 0),
            hypothesis_version=hypothesis.context_version(state) if same_concept else 0)))
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
