"""The five terminal teaching tools and four internal tools live here."""
import asyncio
import json
import uuid
from typing import Literal
from openai import pydantic_function_tool
from pydantic import Field
from . import config, knowledge, utils


class TextArgs(utils.Schema):
    content: str = Field(min_length=1, max_length=12000)
    concept: str = Field(max_length=200)


class HighlightArgs(TextArgs):
    target: Literal['workspace', 'material']
    source_id: str
    quote: str = Field(min_length=1, max_length=2000)
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class Node(utils.Schema):
    id: str = Field(max_length=80)
    label: str = Field(max_length=200)


class Edge(utils.Schema):
    source: str
    target: str


class VisualizationArgs(TextArgs):
    nodes: list[Node] = Field(min_length=1, max_length=30)
    edges: list[Edge] = Field(max_length=60)
    steps: list[str] = Field(max_length=20)


class ToolResult(utils.Schema):
    type: Literal['hint', 'explanation', 'highlight', 'visualization', 'guiding_question']
    content: str
    metadata: dict


def text_result(kind: str, context: dict) -> dict:
    args = TextArgs.model_validate(context)
    return ToolResult(type=kind, content=args.content, metadata={'concept': args.concept}).model_dump()


def hint(context: dict) -> dict:
    return text_result('hint', context)


def explanation(context: dict) -> dict:
    return text_result('explanation', context)


def guiding_question(context: dict) -> dict:
    return text_result('guiding_question', context)


def highlight(context: dict) -> dict:
    args = HighlightArgs.model_validate(context)
    if args.end <= args.start:
        raise ValueError('Highlight end must follow start.')
    return ToolResult(type='highlight', content=args.content,
        metadata=args.model_dump(exclude={'content'})).model_dump()


def visualization(context: dict) -> dict:
    args = VisualizationArgs.model_validate(context)
    ids = [n.id for n in args.nodes]
    if len(set(ids)) != len(ids) or any(e.source not in ids or e.target not in ids for e in args.edges):
        raise ValueError('Visualization must use unique nodes and valid edge endpoints.')
    return ToolResult(type='visualization', content=args.content,
        metadata=args.model_dump(exclude={'content'})).model_dump()


REGISTRY = {'hint': (TextArgs, hint), 'explanation': (TextArgs, explanation),
            'highlight': (HighlightArgs, highlight), 'visualization': (VisualizationArgs, visualization),
            'guiding_question': (TextArgs, guiding_question)}


def function_definition(name: str) -> dict:
    definition = pydantic_function_tool(REGISTRY[name][0], name=name,
        description=f'Display a {name.replace("_", " ")} to the student.')['function']
    return {'type': 'function', **definition}


async def invoke_registered_tool(name: str, arguments: str | dict) -> dict:
    if name not in REGISTRY:
        raise ValueError('Unknown intervention tool.')
    schema, function = REGISTRY[name]
    args = schema.model_validate_json(arguments) if isinstance(arguments, str) else schema.model_validate(arguments)
    async with asyncio.timeout(3):
        return function(args.model_dump())


def validate_target(result: dict, context: dict) -> dict:
    if result['type'] != 'highlight':
        return result
    m = result['metadata']
    if m['target'] == 'workspace':
        source = context['workspace']
    else:
        source = next((c['text'] for c in context['knowledge'] if c['id'] == m['source_id']), '')
    if not source or source[m['start']:m['end']] != m['quote']:
        raise ValueError('Highlight offsets must match the current source text exactly.')
    return result


def retrieve_knowledge(query: str) -> list[dict]:
    return knowledge.retrieve_knowledge(query)


def get_prerequisites(db, concept: str) -> list[str]:
    return knowledge.get_prerequisites(db, concept)


def verify_solution(problem: dict, answer: str) -> dict | None:
    """Only server-generated exact-answer tasks have a deterministic verifier."""
    if 'expected_answer' not in problem:
        return None
    normalized = lambda value: ' '.join(str(value).strip().casefold().split())
    return {'correct': normalized(answer) == normalized(problem['expected_answer']),
            'verified': True, 'score': float(normalized(answer) == normalized(problem['expected_answer'])),
            'reason': 'Compared with the server-side exact-answer rubric.', 'misconceptions': []}


class Problem(utils.Schema):
    prompt: str
    concept: str
    expected_answer: str
    rubric: str


async def generate_problem(concept: str) -> dict:
    if config.DEMO_MODE:
        if concept.casefold() != 'binary search':
            raise ValueError('Offline demo supports Binary Search; enable OpenAI for other concepts.')
        problem = Problem(prompt='For sorted array [1, 2, 2, 2, 5], what is the zero-based index '
                          'of the first occurrence of 2? Enter only the index.', concept=concept,
                          expected_answer='1', rubric='The first 2 is at index 1; indices are zero-based.')
    else:
        problem = await utils.call_llm(
            'Create one short practice task for the requested concept. Require one exact short answer '
            'with unambiguous formatting. Include that answer and a brief private grading rubric. '
            'No student code execution is available.', concept, schema=Problem)
    return {**problem.model_dump(), 'id': uuid.uuid4().hex}
