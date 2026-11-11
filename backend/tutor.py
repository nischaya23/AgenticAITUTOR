"""Grounded teaching, terminating in one validated intervention function call."""
from pydantic import ValidationError
from . import prompts, config, tools, utils

async def execute_intervention(selection: dict, context: dict) -> dict:
    kind = selection['action']
    if kind == 'highlight' and not context.get('workspace', '').strip():
        raise ValueError('Add text to your response before requesting a review.')
    if kind == 'none':
        return None
    instructions = prompts.tutor_instructions(config.LLM_PROVIDER)
    if kind == 'visualization':
        from .video import SCRIPT_INSTRUCTIONS
        instructions += SCRIPT_INSTRUCTIONS
    payload = {'selection': selection, 'context': context}
    for attempt in range(2 if kind == 'visualization' else 1):
        response = await utils.call_llm(instructions, payload,
            tools=[tools.function_definition(kind)], tool_choice={'type': 'function', 'name': kind},
            parallel_tool_calls=False, max_output_tokens=6000 if kind == 'visualization' else None,
            operation='VISUALIZATION_SCRIPT' if kind == 'visualization' else 'TUTOR_RESPONSE')
        calls = [item for item in response.output if item.type == 'function_call']
        if len(calls) != 1 or calls[0].name != kind:
            raise ValueError('Expected exactly one selected intervention tool call.')
        try:
            result = await tools.invoke_registered_tool(kind, calls[0].arguments, context)
            return tools.validate_target(result, context)
        except (ValidationError, ValueError) as exc:
            if kind != 'visualization' or attempt:
                raise
            payload = {**payload, 'repair_error': str(exc)[:1500], 'previous_arguments': calls[0].arguments}

async def respond_to_student(context: dict, selection: dict) -> dict:
    return await execute_intervention(selection, context)
