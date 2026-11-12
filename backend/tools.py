"""Validated teaching tools and grounded question generation."""
import asyncio
import json
import uuid
from typing import Literal
from openai import pydantic_function_tool
from pydantic import Field
from . import workflow, prompts, config, knowledge, utils
from .logging_setup import event as log_event
from .video import VideoScript

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

class HighlightArgs(TextArgs):
    issues: list[ReasoningIssue] = Field(default_factory=list, max_length=30)
    target: Literal['workspace', 'material']
    source_id: str
    quote: str = Field(min_length=1, max_length=2000)
    start: int = Field(ge=0)
    end: int = Field(gt=0)

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
    return interactive_visualization(context)

REGISTRY = {'hint': (TextArgs, hint), 'explanation': (TextArgs, explanation),
    'highlight': (HighlightArgs, highlight), 'visualization': (VideoScript, visualization),
    'guiding_question': (TextArgs, guiding_question)}

def function_definition(name: str) -> dict:
    schema = {'highlight': ReasoningReviewArgs, 'visualization': VideoScript}.get(name, REGISTRY[name][0])
    definition = pydantic_function_tool(schema, name=name, description=prompts.TOOL_DESCRIPTIONS.get(name, prompts.tool_description(name)))['function']
    return {'type': 'function', **definition}

@workflow.stage('Tutor · Execute tool', 'tools')
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
        target='workspace', source_id='workspace', annotations=annotations)).model_dump()
    return validate_target(result, context)

def interactive_visualization(arguments: str | dict, *, gemini: bool=False) -> dict:
    args = VideoScript.model_validate_json(arguments) if isinstance(arguments, str) else VideoScript.model_validate(arguments)
    return ToolResult(type='visualization', content=args.content, metadata={'concept': args.concept, 'video_script': args.model_dump()}).model_dump()

def retrieve_knowledge(query: str, material_id: str | None=None, user_id: int | None=None) -> list[dict]:
    return knowledge.retrieve_knowledge(query, material_id=material_id, user_id=user_id)

def get_prerequisites(db, concept: str, material_id: str | None=None, user_id: int | None=None) -> list[str]:
    return knowledge.get_prerequisites(db, concept, material_id=material_id, user_id=user_id)

class Problem(utils.Schema):
    prompt: str
    concept: str
    expected_answer: str
    rubric: str
    format: Literal['explanation', 'trace'] = 'explanation'
    diagnostic_focus: str = Field(default='', max_length=500)


@workflow.stage('Knowledge · Generate question', 'agents')
async def generate_problem(concept: str, sources: list[dict] | None=None, previous_prompts: list[str] | None=None, plan: dict | None=None, plan_version: int=0, hypotheses: list[dict] | None=None, hypothesis_version: int=0, eligible_concepts: list[str] | None=None, recent_formats: list[str] | None=None, learning_context: dict | None=None) -> dict:
    log_event('question.generate', plan_version=plan_version, hypothesis_version=hypothesis_version)
    problem = await utils.call_llm(prompts.GENERATE_QUESTION, {'learning_context': learning_context or {}, 'concept': concept, 'sources': sources if sources is not None else retrieve_knowledge(concept), 'recent_questions': (previous_prompts or [])[-6:], 'plan': plan or {}, 'plan_version': plan_version,
        'hypotheses': hypotheses or [], 'hypothesis_version': hypothesis_version,
        'eligible_concepts': eligible_concepts or [concept], 'recent_formats': (recent_formats or [])[-6:]}, schema=Problem, operation='GENERATE_QUESTION')
    return {**problem.model_dump(), 'concept': concept, 'id': uuid.uuid4().hex}
