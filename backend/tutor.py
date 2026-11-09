"""Grounded teaching, terminating in one validated intervention function call."""
from pydantic import ValidationError
from . import prompts, config, tools, utils

def scene_corrections(error: ValidationError) -> list[str]:
    branches = {'rect': 'SceneRectangle', 'circle': 'SceneCircle', 'text': 'SceneText', 'line': 'SceneLine', 'arrow': 'SceneLine'}
    issues = error.errors(include_url=False, include_context=False)
    selected = {issue['loc'][1]: branches.get(str(issue['input'])) for issue in issues if len(issue['loc']) == 4 and issue['loc'][0] == 'objects' and (issue['loc'][-1] == 'kind') and (issue['type'] == 'literal_error')}
    corrections = []
    for issue in issues:
        location = issue['loc']
        if len(location) >= 3 and location[0] == 'objects':
            branch = selected.get(location[1])
            if branch and location[2] != branch:
                continue
        path = '.'.join((str(part) for part in location if part not in branches.values()))
        correction = f"{path or 'scene'}: {issue['msg']}"
        if correction not in corrections:
            corrections.append(correction)
    return corrections[:8]

async def execute_intervention(selection: dict, context: dict) -> dict:
    kind = selection['action']
    if kind == 'highlight' and not (context.get('workspace', '').strip() or context.get('response', {}).get('diagram', {}).get('nodes')):
        raise ValueError('Add text or a diagram to your response before requesting a review.')
    if kind == 'none':
        return None
    repair_scene = kind == 'visualization' and config.LLM_PROVIDER == 'gemini'
    instructions = prompts.tutor_instructions(config.LLM_PROVIDER)
    payload = {'selection': selection, 'context': context}
    for attempt in range(2 if repair_scene else 1):
        response = await utils.call_llm(instructions, payload, tools=[tools.function_definition(kind)], tool_choice={'type': 'function', 'name': kind}, parallel_tool_calls=False, max_output_tokens=max(config.MAX_OUTPUT_TOKENS, 3072) if kind == 'visualization' else None, operation='TUTOR_RESPONSE')
        calls = [item for item in response.output if item.type == 'function_call']
        if len(calls) != 1 or calls[0].name != kind:
            raise ValueError('Expected exactly one selected intervention tool call.')
        arguments = calls[0].arguments
        try:
            result = await tools.invoke_registered_tool(kind, arguments, context)
            break
        except ValidationError as exc:
            if kind != 'visualization':
                raise
            if not repair_scene or attempt:
                raise ValueError('The model returned a visualization that could not be rendered. Try again.') from None
            payload = {'repair': prompts.VISUALIZATION_REPAIR, 'issues': scene_corrections(exc), 'previous_arguments': arguments, **payload}
    return tools.validate_target(result, context)

async def respond_to_student(context: dict, selection: dict) -> dict:
    return await execute_intervention(selection, context)
