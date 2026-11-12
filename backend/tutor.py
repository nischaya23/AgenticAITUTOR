"""Grounded teaching, terminating in one validated intervention function call."""
from pydantic import ValidationError
from . import workflow, prompts, config, tools, utils

async def execute_intervention(selection: dict, context: dict) -> dict:
    kind = selection['action']
    if kind == 'highlight' and not context.get('workspace', '').strip():
        raise ValueError('Add text to your response before requesting a review.')
    if kind == 'none':
        return None
    instructions = prompts.tutor_instructions(config.LLM_PROVIDER)
    if kind == 'visualization':
        instructions += prompts.VISUALIZATION_SLIDES
        if config.LLM_PROVIDER == 'gemini':
            instructions += prompts.VISUALIZATION_GEMINI
    payload = {'selection': selection, 'context': context}
    for attempt in range(2 if kind == 'visualization' else 1):
        response = await utils.call_llm(instructions, payload,
            tools=[tools.function_definition(kind)], tool_choice={'type': 'function', 'name': kind},
            parallel_tool_calls=False, max_output_tokens=max(config.MAX_OUTPUT_TOKENS, 3072) if kind == 'visualization' else None,
            operation='VISUALIZATION_SLIDES' if kind == 'visualization' else 'TUTOR_RESPONSE')
        calls = [item for item in response.output if item.type == 'function_call']
        if len(calls) != 1 or calls[0].name != kind:
            raise ValueError('Expected exactly one selected intervention tool call.')
        try:
            result = await tools.invoke_registered_tool(kind, calls[0].arguments, context)
            return tools.validate_target(result, context)
        except ValueError as exc:
            if kind != 'visualization':
                raise
            if attempt:
                raise ValueError('The model returned an invalid slideshow. Please try again.') from exc
            workflow.record('validation.repair', 'tools', 'warnings', attempt=attempt+1, message='Repairing slideshow validation errors')
            issues = exc.errors(include_input=False, include_url=False, include_context=False)[:8] if isinstance(exc, ValidationError) else str(exc)
            payload = {**payload, 'repair_error': issues, 'previous_arguments': calls[0].arguments}

@workflow.stage('Tutor · Generate response', 'agents')
async def respond_to_student(context: dict, selection: dict) -> dict:
    return await execute_intervention(selection, context)
