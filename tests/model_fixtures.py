from types import SimpleNamespace
from backend import tools, planner, assessment, knowledge, reflection, hypothesis
def tool_arguments(kind: str, context: dict) -> dict:
    concept = context['concept']
    content = {
        'hint': 'Track the interval boundary that can still contain the first matching value.',
        'explanation': 'For lower bound, keep [left, right). If a[mid] is below the target, '
                       'set left = mid + 1; otherwise set right = mid. When they meet, left '
                       'is the first possible matching index. Each step shrinks the interval.',
        'guiding_question': 'If the middle value equals the target, could an earlier occurrence still be on its left?',
        'highlight': 'This passage is relevant to your current reasoning.',
        'visualization': 'A lower-bound search keeps the first possible match inside a shrinking interval.'
    }[kind]
    args = {'content': '[Test fixture] ' + content, 'concept': concept}
    if kind == 'highlight':
        line, quote = next((i, line.strip()[:120]) for i, line in
                           enumerate(context['workspace'].split('\n'), 1) if line.strip())
        args.update(content='[Test fixture] Revisit this step in your reasoning.',
                    issues=[dict(line=line, quote=quote, category='reconsider',
                                 reason='Try stating how this step preserves the search interval invariant.')])
    if kind == 'visualization':
        args.update(title='Finding the first match',duration=2,
            code="def draw_frame(canvas, t):\n    canvas.text(40, 40, 'Find the first match')\n    canvas.circle(100 + t * 30, 220, 20)",
            chapters=[dict(start=0,end=2,text='Keep looking for an earlier match.')],source_ids=[])
    return args



async def mock_llm(instructions, data, schema=None, **kwargs):
    from backend import websearch, observer_review
    if kwargs.get('operation')=='RESEARCH_TOOL':
        choice=kwargs.get('tool_choice')
        required=isinstance(choice,dict) and choice.get('name')=='web_search'
        name='web_search' if required else 'finish_research'
        args=dict(query=data.get('suggested_query','Study topic'),subject=data.get('subject','Study topic') if isinstance(data.get('subject'),str) else 'Study topic',purpose='Ground the learning graph.') if required else dict(reason='Local sources suffice.')
        return SimpleNamespace(output=[SimpleNamespace(type='function_call',call_id='call-fixture',name=name,arguments=__import__('json').dumps(args))])
    if schema is observer_review.Decision:
        return schema(action='WAIT',reason='Continue working independently.',evidence_references=[])
    if schema is planner.Plan:
        return schema(objective='Practice',steps=['Solve independently'],learning_goals=['Understand'])
    if schema is planner.Selection:
        return schema(action='explanation' if data.get('explicit') else 'guiding_question',strategy='One step')
    from backend import websearch, observer_review
    if schema is websearch.TopicMeanings:
        topic=data['topic']
        return schema(meanings=[dict(label=topic,query=topic,description=f'The study subject {topic}.')])
    if schema is websearch.Relevance:
        return schema(relevant=[p['index'] for p in data['pages'] if 'durag' not in p['title'].lower()])
    if schema is knowledge.ConceptGraph:
        documents=data['documents']
        names=[line.strip('# ').strip() for d in documents for line in d['text'].splitlines() if line.startswith('#')][:15]
        names=names or ['Uploaded material']
        return schema(concepts=names,prerequisites=[],evidence=[dict(concept=name,prerequisite='',
            source_id=documents[0]['id'],quote=documents[0]['text'][:80]) for name in names])
    if schema is tools.Problem:
        n=len(data.get('recent_questions',[]))
        return schema(prompt=f'Question {n}: find the first occurrence of 2.',concept=data['concept'],expected_answer='1',rubric='First index is 1.')
    if schema is assessment.Assessment:
        answer = data['answer']
        correct=(answer['text'] if isinstance(answer,dict) else answer).strip()=='1'
        return schema(correct=correct,score=float(correct),reason='Compared with reference.',misconceptions=[])
    if schema is reflection.SessionReflection:
        return schema(summary='Practiced the concept.',next_goal='Check interval boundaries.')
    if schema is hypothesis.Analysis:
        return schema(hypotheses=[dict(id='boundary',concept='Binary Search',
            possible_gap='May need practice with interval boundaries.',confidence=.4,
            evidence=[dict(reference=data['evidence_references'][-1],summary='The response discusses search boundaries.')],
            diagnostic_focus='Explain why the interval contains the first match.',status='tentative')])
    kind=kwargs['tool_choice']['name']
    args=tool_arguments(kind,data['context'])
    return SimpleNamespace(output=[SimpleNamespace(type='function_call',name=kind,arguments=__import__('json').dumps(args))])
