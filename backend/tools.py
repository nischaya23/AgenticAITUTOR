"""Validated teaching tools and grounded question generation."""
import asyncio
import json
import uuid
from typing import Literal
from openai import pydantic_function_tool
from pydantic import Field, model_validator
from . import prompts, config, knowledge, utils
from .response import Diagram, DiagramAnnotation
from .logging_setup import event as log_event
from .visualization import GeminiSceneRequest, Scene, VisualizationRequest

class TextArgs(utils.Schema):
    content: str = Field(min_length=1, max_length=12000)
    concept: str = Field(max_length=200)

class ReasoningIssue(utils.Schema):
    line: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=2000)

class Annotation(ReasoningIssue):
    category: Literal['possible_error', 'important_concept', 'missing_justification', 'reconsider', 'explanation_needed']

class ReasoningReviewArgs(TextArgs):
    issues: list[Annotation] = Field(max_length=15)
    diagram_annotations: list[DiagramAnnotation] = Field(default_factory=list, max_length=30)

class HighlightArgs(TextArgs):
    issues: list[ReasoningIssue] = Field(default_factory=list, max_length=30)
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
    return ToolResult(type='highlight', content=args.content, metadata=args.model_dump(exclude={'content'})).model_dump()

def visualization(context: dict) -> dict:
    args = VisualizationArgs.model_validate(context)
    ids = [n.id for n in args.nodes]
    if len(set(ids)) != len(ids) or any((e.source not in ids or e.target not in ids for e in args.edges)):
        raise ValueError('Visualization must use unique nodes and valid edge endpoints.')
    return ToolResult(type='visualization', content=args.content, metadata=args.model_dump(exclude={'content'})).model_dump()
REGISTRY = {'hint': (TextArgs, hint), 'explanation': (TextArgs, explanation), 'highlight': (HighlightArgs, highlight), 'visualization': (VisualizationArgs, visualization), 'guiding_question': (TextArgs, guiding_question)}

def function_definition(name: str) -> dict:
    visual_schema = GeminiSceneRequest if config.LLM_PROVIDER == 'gemini' else VisualizationRequest
    schema = {'highlight': ReasoningReviewArgs, 'visualization': visual_schema}.get(name, REGISTRY[name][0])
    definition = pydantic_function_tool(schema, name=name, description=prompts.TOOL_DESCRIPTIONS.get(name, prompts.tool_description(name)))['function']
    return {'type': 'function', **definition}

async def invoke_registered_tool(name: str, arguments: str | dict, context: dict | None=None) -> dict:
    if name not in REGISTRY:
        raise ValueError('Unknown intervention tool.')
    if context is not None and name == 'highlight':
        return reasoning_review(arguments, context)
    if context is not None and name == 'visualization':
        return interactive_visualization(arguments, gemini=config.LLM_PROVIDER == 'gemini')
    schema, function = REGISTRY[name]
    args = schema.model_validate_json(arguments) if isinstance(arguments, str) else schema.model_validate(arguments)
    async with asyncio.timeout(3):
        return function(args.model_dump())

def validate_target(result: dict, context: dict) -> dict:
    if result['type'] != 'highlight':
        return result
    m = result['metadata']
    if m.get('target') != 'workspace':
        raise ValueError('Highlight is restricted to the response workspace.')
    source = context['workspace']
    diagram = context.get('response', {}).get('diagram', {'nodes': [], 'edges': []})
    for raw in m.get('diagram_annotations', []):
        issue = DiagramAnnotation.model_validate(raw)
        if issue.id not in {item['id'] for item in diagram['nodes' if issue.target == 'node' else 'edges']}:
            raise ValueError('Diagram annotation must reference a current response element.')
    for issue in m.get('annotations', []):
        lines = source.split('\n')
        if m['target'] != 'workspace' or issue['line'] > len(lines) or issue['quote'] not in lines[issue['line'] - 1] or (source[issue['start']:issue['end']] != issue['quote']):
            raise ValueError('Annotation must match the current reasoning exactly.')
    if 'annotations' in m:
        return result
    for issue in m.get('issues', []):
        lines = context['workspace'].splitlines()
        if m['target'] != 'workspace' or issue['line'] > len(lines) or lines[issue['line'] - 1] != issue['quote']:
            raise ValueError('Issue must match a current reasoning line exactly.')
    if not source or source[m['start']:m['end']] != m['quote']:
        raise ValueError('Highlight offsets must match the current source text exactly.')
    return result

def reasoning_review(arguments: str | dict, context: dict) -> dict:
    raw = json.loads(arguments) if isinstance(arguments, str) else arguments
    args = ReasoningReviewArgs.model_validate(raw)
    source = context['workspace']
    lines = source.split('\n')
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line) + 1)
    annotations = []
    for issue in args.issues:
        index = issue.line - 1
        quote = issue.quote
        if '\n' in quote or '\r' in quote or (not quote.strip()):
            raise ValueError('Annotation quote must contain text from one line.')
        if index >= len(lines) or quote not in lines[index]:
            matches = [i for i, line in enumerate(lines) if quote in line]
            if len(matches) != 1:
                raise ValueError('Reasoning changed or review line could not be located. Request a new review.')
            index = matches[0]
        first = lines[index].find(quote)
        if first != lines[index].rfind(quote):
            raise ValueError('Annotation quote is ambiguous within the selected line.')
        start = offsets[index] + first
        annotations.append({**issue.model_dump(), 'line': index + 1, 'start': start, 'end': start + len(quote)})
    result = ToolResult(type='highlight', content=args.content, metadata=dict(concept=args.concept,
        target='workspace', source_id='workspace', annotations=annotations,
        diagram_annotations=[item.model_dump() for item in args.diagram_annotations])).model_dump()
    return validate_target(result, context)

def interactive_visualization(arguments: str | dict, *, gemini: bool=False) -> dict:
    schema = GeminiSceneRequest if gemini else VisualizationRequest
    args = schema.model_validate_json(arguments) if isinstance(arguments, str) else schema.model_validate(arguments)
    visual = Scene.model_validate_json(args.scene_json) if isinstance(args, GeminiSceneRequest) else args.visualization
    return ToolResult(type='visualization', content=args.content, metadata={'concept': args.concept, 'visualization': visual.model_dump()}).model_dump()

def retrieve_knowledge(query: str, material_id: str | None=None, user_id: int | None=None) -> list[dict]:
    return knowledge.retrieve_knowledge(query, material_id=material_id, user_id=user_id)

def get_prerequisites(db, concept: str, material_id: str | None=None, user_id: int | None=None) -> list[str]:
    return knowledge.get_prerequisites(db, concept, material_id=material_id, user_id=user_id)

class Problem(utils.Schema):
    prompt: str
    concept: str
    expected_answer: str
    rubric: str
    format: Literal['explanation', 'trace', 'diagram_interpretation', 'diagram_construction'] = 'explanation'
    diagram: Diagram | None = None
    starter_diagram: Diagram | None = None
    diagnostic_focus: str = Field(default='', max_length=500)

    @model_validator(mode='after')
    def interpretation_has_diagram(self):
        if self.format == 'diagram_interpretation' and (not self.diagram or not self.diagram.nodes):
            raise ValueError('Diagram interpretation requires a question diagram.')
        return self


class GeminiProblem(utils.Schema):
    """A shallow wire schema; diagrams are validated locally after decoding.

    Gemini's compatibility endpoint rejects the nested Problem schema. Keep
    this transport format separate from the public question representation.
    """
    prompt: str
    concept: str
    expected_answer: str
    rubric: str
    format: Literal['explanation', 'trace', 'diagram_interpretation', 'diagram_construction']
    diagnostic_focus: str
    diagram_json: str
    starter_diagram_json: str

    def to_problem(self) -> Problem:
        return Problem.model_validate({
            **self.model_dump(exclude={'diagram_json', 'starter_diagram_json'}),
            'diagram': Diagram.model_validate_json(self.diagram_json).model_dump() if self.diagram_json.strip() else None,
            'starter_diagram': Diagram.model_validate_json(self.starter_diagram_json).model_dump() if self.starter_diagram_json.strip() else None,
        })


async def generate_problem(concept: str, sources: list[dict] | None=None, previous_prompts: list[str] | None=None, plan: dict | None=None, plan_version: int=0, hypotheses: list[dict] | None=None, hypothesis_version: int=0, eligible_concepts: list[str] | None=None, recent_formats: list[str] | None=None) -> dict:
    log_event('question.generate', plan_version=plan_version, hypothesis_version=hypothesis_version)
    gemini = config.LLM_PROVIDER == 'gemini'
    problem = await utils.call_llm(prompts.GENERATE_QUESTION_GEMINI if gemini else prompts.GENERATE_QUESTION, {'concept': concept, 'sources': sources if sources is not None else retrieve_knowledge(concept), 'recent_questions': (previous_prompts or [])[-6:], 'plan': plan or {}, 'plan_version': plan_version,
        'hypotheses': hypotheses or [], 'hypothesis_version': hypothesis_version,
        'eligible_concepts': eligible_concepts or [concept], 'recent_formats': (recent_formats or [])[-6:]}, schema=GeminiProblem if gemini else Problem, operation='GENERATE_QUESTION')
    if gemini:
        try:
            problem = problem.to_problem()
        except ValueError as exc:
            raise ValueError('The model returned an invalid question diagram. Please retry.') from exc
    return {**problem.model_dump(), 'concept': concept, 'id': uuid.uuid4().hex}
