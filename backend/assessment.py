"""Contextual LLM grading against the question and learning memory."""
from pydantic import Field
from . import workflow, prompts, utils

class Assessment(utils.Schema):
    correct: bool
    score: float = Field(ge=0, le=1)
    reason: str
    misconceptions: list[str] = Field(max_length=3)

@workflow.stage('Assessment · Grade submission', 'agents')
async def evaluate_attempt(problem: dict, answer: str | dict, context: dict, reasoning: bool=False) -> dict:
    result = await utils.call_llm(prompts.ASSESS_ANSWER, {'problem': problem, 'answer': answer, 'context': context}, schema=Assessment, operation='ASSESS_ANSWER')
    return {**result.model_dump(), 'verified': False, 'method': 'llm'}

def diagnose_misconception(assessment: dict) -> list[str]:
    return assessment.get('misconceptions', [])

def estimate_mastery(previous: float, score: float, verified: bool) -> float:
    weight = 0.2 if verified else 0.04
    return max(0, min(1, previous * (1 - weight) + score * weight))
