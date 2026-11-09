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
        trace = [
            (0, 5, 2, 'equal', 'Index 2 matches. Keep looking to the left.'),
            (0, 2, 1, 'equal', 'Index 1 matches. Move right to 1.'),
            (0, 1, 0, 'less', 'Index 0 is below the target. Move left to 1.'),
            (1, 1, None, 'complete', 'The first match is at index 1.'),
        ]
        args['visualization'] = dict(kind='binary_search', title='Finding the first occurrence',
            array=[1, 2, 2, 2, 5], target=2,
            steps=[dict(left=l, right=r, mid=m, comparison=c, explanation=e) for l, r, m, c, e in trace])
    return args



async def mock_llm(instructions, data, schema=None, **kwargs):
    if schema is planner.Plan:
        return schema(objective='Practice',steps=['Solve independently'],learning_goals=['Understand'])
    if schema is planner.Selection:
        return schema(action='explanation' if data.get('explicit') else 'guiding_question',strategy='One step')
    if schema is knowledge.ConceptGraph:
        names=[s.strip('# ').strip() for s in data.splitlines() if s.startswith('#')][:15]
        return schema(concepts=names or ['Uploaded material'],prerequisites=[])
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
