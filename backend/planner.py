"""Two planning horizons; selection chooses an action rather than UI prose."""
from typing import Literal
from pydantic import Field, StringConstraints
from typing import Annotated
from . import prompts, utils

class Plan(utils.Schema):
    objective: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    steps: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]] = Field(min_length=1, max_length=6)
    learning_goals: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]] = Field(max_length=6)

class Selection(utils.Schema):
    action: Literal['none', 'hint', 'explanation', 'highlight', 'visualization', 'guiding_question']
    strategy: str

async def create_learning_plan(context: dict) -> dict:
    return (await utils.call_llm(prompts.CREATE_LEARNING_PLAN, context, schema=Plan, operation='CREATE_LEARNING_PLAN')).model_dump()

async def select_intervention(context: dict, explicit: bool, requested: str | None=None) -> dict:
    if requested:
        return Selection(action=requested, strategy=prompts.REQUESTED_TOOL).model_dump()
    return (await utils.call_llm(prompts.SELECT_INTERVENTION, {'explicit': explicit, **context}, schema=Selection, operation='SELECT_INTERVENTION')).model_dump()

async def revise_plan(context: dict) -> dict:
    return await create_learning_plan({**context, 'replan': prompts.REPLAN})
