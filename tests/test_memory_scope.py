import json
from backend import context, memory, reflection, utils
from backend.db import Session, StudentState


def prepare(client):
    material = client.post('/api/materials', files={'file': (
        'course.md', b'# Binary Search\nSorted binary search narrows an interval.', 'text/markdown')}).json()
    sid = client.post('/api/sessions').json()['id']
    response = client.post(f'/api/sessions/{sid}/study', json={'material_id': material['id']})
    assert response.status_code == 200, response.text
    return sid


def seed_two_topics(db, uid):
    memory.update_long_term_memory(db, uid, dict(
        mastery={'Binary Search': .4, 'Photosynthesis': .9},
        concept_memory={
            'binary search': dict(name='Binary Search', summary='Mixes up interval bounds.', next_goal='Track bounds'),
            'photosynthesis': dict(name='Photosynthesis', summary='OTHER MATERIAL SUMMARY', next_goal='Light reactions')},
        misconceptions=[dict(concept='Binary Search', hypothesis='Off-by-one on bounds', evidence_event_id=1),
                        dict(concept='Photosynthesis', hypothesis='OTHER MISCONCEPTION', evidence_event_id=2)],
        hypotheses=[dict(id='a', concept='Photosynthesis', possible_gap='OTHER HYPOTHESIS')],
        learner_profile='Prefers worked examples.', student_goals=['Finish the course'],
        archived_notes=['OLD UNTAGGED NOTE']))


def test_agents_only_receive_memory_for_the_current_concept_graph(app_env):
    client, factory, _ = app_env
    sid = prepare(client)
    with factory.begin() as db:
        uid = db.get(Session, sid).user_id
        seed_two_topics(db, uid)
        for role in ('teaching', 'planning', 'assessment'):
            student = context.for_session(db, sid, role=role)['student']
            text = json.dumps(student)
            assert 'OTHER' not in text and 'Photosynthesis' not in text, role
            assert 'OLD UNTAGGED NOTE' not in text and 'archived_notes' not in student
            assert student['concept_memory']['binary search']['summary'] == 'Mixes up interval bounds.'
            assert student['mastery'] == {'Binary Search': .4}
            assert [m['hypothesis'] for m in student['misconceptions']] == ['Off-by-one on bounds']
            assert student['learner_profile'] == 'Prefers worked examples.'
            assert student['student_goals'] == ['Finish the course']
    # The Memory panel is the student's own view and still shows everything.
    snapshot = client.get(f'/api/sessions/{sid}/memory').json()
    assert 'photosynthesis' in snapshot['long_term']['concept_memory']


def test_planner_gets_whole_graph_and_other_agents_get_concept_and_prerequisites():
    graph = {'concepts': ['Arrays', 'Binary Search', 'Graphs'],
             'prerequisites': [{'prerequisite': 'Arrays', 'concept': 'Binary Search'}]}
    assert context.memory_scope(graph, 'Binary Search', 'planning') == ['Arrays', 'Binary Search', 'Graphs']
    assert context.memory_scope(graph, 'Binary Search', 'teaching') == ['Binary Search', 'Arrays']
    assert context.memory_scope({}, 'Binary Search', 'planning') == ['Binary Search']
    data = {'concept_memory': {k: {'name': k} for k in ('arrays', 'binary search', 'graphs')}}
    scoped = memory.scoped_long_term(data, ['Binary Search', 'Arrays'], 'Binary Search')
    assert list(scoped['concept_memory']) == ['binary search', 'arrays']


def test_reflections_replace_one_rolling_summary_per_concept():
    data = {'concept_memory': {}, 'learner_profile': ''}
    memory.record_concept_reflection(data, 'Binary Search', 'First summary.', 'Goal 1', 'm1', 10)
    memory.record_concept_reflection(data, ' binary  SEARCH ', 'Merged summary.', 'Goal 2', 'm2', 11, 'Likes diagrams.')
    memory.record_concept_reflection(data, 'Binary Search', 'Latest.', 'Goal 3', 'm1', 12, 'Likes diagrams.')
    assert list(data['concept_memory']) == ['binary search']
    entry = data['concept_memory']['binary search']
    assert entry['summary'] == 'Latest.' and entry['next_goal'] == 'Goal 3' and entry['attempts'] == 3
    assert entry['material_ids'] == ['m1', 'm2'] and entry['evidence_event_ids'] == [10, 11, 12]
    assert data['learner_profile'] == 'Likes diagrams.'  # Not duplicated.
    for i in range(10):
        memory.add_concept_note(data, 'Binary Search', f'note {i}')
    assert entry['notes'] == [f'note {i}' for i in range(5, 10)]


def test_legacy_flat_memories_are_archived_and_not_sent_to_agents(app_env):
    client, factory, _ = app_env
    sid = prepare(client)
    with factory.begin() as db:
        row = db.get(StudentState, db.get(Session, sid).user_id)
        row.data = {**row.data, 'memories': ['Legacy reflection'], 'learning_goals': ['Legacy goal']}
    with factory.begin() as db:
        uid = db.get(Session, sid).user_id
        data = memory.get_long_term_memory(db, uid)
        assert data['archived_notes'] == ['Legacy reflection']
        assert 'memories' not in data and 'learning_goals' not in data
        assert 'Legacy' not in json.dumps(context.for_session(db, sid)['student'])
        memory.update_long_term_memory(db, uid, {})
        assert 'memories' not in db.get(StudentState, uid).data
    snapshot = client.get(f'/api/sessions/{sid}/memory').json()
    archived = next(i for i in snapshot['items'] if i['kind'] == 'archived')
    correction = dict(expected_revision=snapshot['revision'], action='edit', item_id=archived['id'], text='x')
    assert client.post(f'/api/sessions/{sid}/memory/corrections', json=correction).status_code != 200


def test_preparation_context_has_no_concept_summaries(app_env):
    client, factory, _ = app_env
    sid = prepare(client)
    with factory.begin() as db:
        uid = db.get(Session, sid).user_id
        seed_two_topics(db, uid)
        student = context.preparation(db, uid, 'Learn graphs')['student']
    assert set(student) == {'student_goals', 'learner_profile', 'known_concepts', 'memory_revision'}
    assert student['known_concepts'] == {'Binary Search': .4, 'Photosynthesis': .9}


async def test_student_corrected_summary_is_what_the_next_reflection_merges(app_env, monkeypatch):
    client, factory, _ = app_env
    sid = prepare(client)
    with factory.begin() as db:
        seed_two_topics(db, db.get(Session, sid).user_id)
    snapshot = client.get(f'/api/sessions/{sid}/memory').json()
    item = next(i for i in snapshot['items'] if i['kind'] == 'summary' and i['concept'] == 'Binary Search')
    response = client.post(f'/api/sessions/{sid}/memory/corrections', json=dict(
        expected_revision=snapshot['revision'], action='edit', item_id=item['id'], text='I now track bounds correctly.'))
    assert response.status_code == 200, response.text
    with factory() as db:
        ctx = context.for_session(db, sid)
    payloads = []
    async def fake(instructions, payload, **kwargs):
        payloads.append(payload)
        return reflection.SessionReflection(summary='Did well.', next_goal='Harder cases', concept_summary='')
    monkeypatch.setattr(utils, 'call_llm', fake)
    result = await reflection.reflect_on_answer(ctx, {'correct': True})
    assert payloads[0]['prior_concept_summary'] == 'I now track bounds correctly.'
    assert payloads[0]['learner_profile'] == 'Prefers worked examples.'
    # An empty model summary falls back to prior summary plus this answer, never losing memory.
    assert result['concept_summary'] == 'I now track bounds correctly. Did well.'
