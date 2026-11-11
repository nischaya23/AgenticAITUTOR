import pytest
from backend import tools

def test_annotation_resolves_substrings_and_rejects_ambiguous_occurrences():
    args = {'content': 'Revisit this line.', 'concept': 'Binary Search', 'issues': [
        {'line': 1, 'quote': 'mid', 'reason': 'Justify your choice.', 'category': 'missing_justification'},
        {'line': 2, 'quote': 'left', 'reason': 'State the boundary invariant.', 'category': 'important_concept'},
    ]}
    context = {'workspace': '😀 use mid\nupdate left'}
    result = tools.reasoning_review(args, context)
    tools.validate_target(result, context)
    assert [item['start'] for item in result['metadata']['annotations']] == [6, 17]
    with pytest.raises(ValueError, match='ambiguous'):
        tools.reasoning_review(args, {'workspace': '😀 use mid or mid\nupdate left'})
    with pytest.raises(ValueError, match='current reasoning'):
        tools.validate_target(result, {'workspace': 'replace that step\nupdate left'})

