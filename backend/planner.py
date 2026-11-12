"""Two planning horizons; selection chooses an action rather than UI prose."""
from typing import Literal
from pydantic import Field, StringConstraints
from typing import Annotated
from sqlalchemy import func, select
from . import workflow, prompts, utils
from .db import Event

class Plan(utils.Schema):
    objective: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    steps: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]] = Field(min_length=1, max_length=6)
    learning_goals: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]] = Field(max_length=6)

class Selection(utils.Schema):
    action: Literal['none', 'hint', 'explanation', 'highlight', 'visualization', 'guiding_question']
    strategy: str


TOOLS = ('explanation', 'guiding_question', 'highlight', 'hint', 'visualization')


def tool_scores(db, session_id: int, problem_id: str, workspace: str) -> dict[str, float]:
    opened = db.scalar(select(func.max(Event.id)).where(
        Event.session_id == session_id, Event.type == 'student_action',
        Event.data['action'].as_string() == 'problem_opened',
        Event.data['problem_id'].as_string() == problem_id))
    tool = Event.data['type'].as_string()
    question = Event.data['problem_id'].as_string()
    delivered = select(tool, func.count()).where(Event.session_id == session_id, Event.type == 'intervention')
    if opened is not None:
        delivered = delivered.where(Event.id > opened, (question == problem_id) | (question.is_(None)))
    else:
        delivered = delivered.where(question == problem_id)
    counts = dict(db.execute(delivered.group_by(tool)).all())
    return {name: 1 / (1 + counts.get(name, 0)) for name in TOOLS
            if (name != 'highlight' or workspace.strip()) and (name != 'hint' or not counts.get('hint'))}

@workflow.stage('Planner · Create plan', 'agents')
async def create_learning_plan(context: dict) -> dict:
    return (await utils.call_llm(prompts.CREATE_LEARNING_PLAN, context, schema=Plan, operation='CREATE_LEARNING_PLAN')).model_dump()

@workflow.stage('Planner · Select teaching tool', 'agents')
async def select_intervention(context: dict, explicit: bool, requested: str | None=None,
                              *, scores: dict[str, float]) -> dict:
    if requested:
        selection = Selection(action=requested, strategy=prompts.REQUESTED_TOOL)
    else:
        selection = await utils.call_llm(prompts.SELECT_INTERVENTION, {
            **context, 'explicit': explicit, 'eligible_tool_scores': scores,
        }, schema=Selection, operation='SELECT_INTERVENTION')
        if selection.action not in scores:
            fallback = max((name for name in TOOLS if name in scores and name != 'visualization'), key=scores.get)
            selection = Selection(action=fallback, strategy='Use a different approach suited to the current question.')
    workflow.record('planner.tool_selected', tool=selection.action, requested=requested, eligible_tool_scores=scores)
    return selection.model_dump()

@workflow.stage('Planner · Revise plan', 'agents')
async def revise_plan(context: dict) -> dict:
    return await create_learning_plan({**context, 'replan': prompts.REPLAN})
