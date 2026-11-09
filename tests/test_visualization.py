from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from backend import config, tools, tutor


def test_gemini_gets_a_flat_visualization_function(monkeypatch):
    monkeypatch.setattr(config, 'LLM_PROVIDER', 'gemini')
    parameters = tools.function_definition('visualization')['parameters']
    assert set(parameters['properties']) == {'content', 'concept', 'scene_json'}
    assert all(field['type'] == 'string' for field in parameters['properties'].values())
    assert '$defs' not in parameters


def test_scene_accepts_labeled_arrows_and_reports_only_matching_shape_errors():
    scene = {
        'kind': 'scene', 'title': 'Encoder',
        'objects': [{'id': 'encode', 'kind': 'arrow', 'x': 20, 'y': 50, 'dx': 80, 'dy': 0,
                     'tone': 'accent', 'label': 'Encoder'}],
        'steps': [{'explanation': 'Encode the input.', 'changes': [{'id': 'encode', 'tone': 'success'}]}],
    }
    args = {'content': 'Encode the input.', 'concept': 'Encoders', 'scene_json': json.dumps(scene)}
    result = tools.interactive_visualization(args, gemini=True)
    assert result['metadata']['visualization']['objects'][0]['label'] == 'Encoder'

    del scene['objects'][0]['dx']
    args['scene_json'] = json.dumps(scene)
    with pytest.raises(ValidationError) as error:
        tools.interactive_visualization(args, gemini=True)
    assert tutor.scene_corrections(error.value) == ['objects.0.dx: Field required']


def test_general_scene_accepts_non_algorithm_topics_and_checks_motion_bounds():
    args = {
        'content': 'Water evaporates from the surface.',
        'concept': 'Water cycle',
        'visualization': {
            'kind': 'scene',
            'title': 'Evaporation',
            'objects': [
                {'id': 'water', 'kind': 'rect', 'x': 70, 'y': 300, 'width': 600, 'height': 95,
                 'tone': 'accent', 'label': 'Surface water'},
                {'id': 'droplet', 'kind': 'circle', 'x': 380, 'y': 290, 'radius': 25,
                 'tone': 'neutral', 'label': None},
            ],
            'steps': [
                {'explanation': 'Start at the water surface.', 'changes': []},
                {'explanation': 'Water vapor rises.', 'changes': [
                    {'id': 'droplet', 'x': None, 'y': 120, 'tone': 'accent',
                     'label': None, 'visible': True},
                ]},
            ],
        },
    }
    result = tools.interactive_visualization(args)
    assert result['metadata']['visualization'] == args['visualization']

    def gemini_arguments():
        return json.dumps({'content': args['content'], 'concept': args['concept'],
                           'scene_json': json.dumps(args['visualization'])})

    assert tools.interactive_visualization(gemini_arguments(), gemini=True) == result

    change = args['visualization']['steps'][1]['changes'][0]
    change['y'] = 5
    with pytest.raises(ValidationError, match='canvas'):
        tools.interactive_visualization(args)
    with pytest.raises(ValidationError, match='canvas'):
        tools.interactive_visualization(gemini_arguments(), gemini=True)

    change.update(y=120, id='unknown')
    with pytest.raises(ValidationError, match='existing object'):
        tools.interactive_visualization(args)


def test_binary_search_rejects_an_inconsistent_trace():
    args = __import__('model_fixtures').tool_arguments('visualization', {'concept': 'Binary Search'})
    assert tools.interactive_visualization(args)['metadata']['visualization']['steps'][-1]['mid'] is None
    args['visualization']['steps'][1]['right'] = 3
    with pytest.raises(ValidationError, match='Search bounds'):
        tools.interactive_visualization(args)


@pytest.mark.parametrize('kind', ['concept_graph', 'tree_traversal', 'graph_traversal'])
def test_graph_renderers_validate_their_own_payloads(kind):
    graph = {
        'kind': kind, 'title': 'Connections',
        'nodes': [{'id': 'a', 'label': 'A'}, {'id': 'b', 'label': 'B'}],
        'edges': [{'source': 'a', 'target': 'b'}],
    }
    if kind != 'concept_graph':
        graph['steps'] = [
            {'node': 'a', 'via': None, 'explanation': 'Start at A.'},
            {'node': 'b', 'via': 'a', 'explanation': 'Follow the edge to B.'},
        ]
    if kind == 'tree_traversal':
        graph['root'] = 'a'
    args = {'content': 'Follow the arrows.', 'concept': 'Graphs', 'visualization': graph}
    assert tools.interactive_visualization(args)['metadata']['visualization']['kind'] == kind

    broken = deepcopy(args)
    broken['visualization']['edges'][0]['target'] = 'missing'
    with pytest.raises(ValidationError, match='edge endpoints'):
        tools.interactive_visualization(broken)

    if kind != 'concept_graph':
        graph['steps'][1]['via'] = None
        with pytest.raises(ValidationError, match='previously visited node'):
            tools.interactive_visualization(args)


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

