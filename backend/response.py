"""Text-only answers. Legacy diagrams are ignored only when reading saved data."""
import json
from pydantic import BaseModel, ConfigDict, Field

class Schema(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Response(Schema):
    text: str = Field(default='', max_length=20000)

def response_for(state: dict) -> dict:
    saved = state.get('response') or {}
    return Response(text=saved.get('text', state.get('workspace', ''))).model_dump()

def content_key(response: dict) -> str:
    return json.dumps({'text': response.get('text', '')}, sort_keys=True)

def has_content(response: dict) -> bool:
    return bool(response.get('text', '').strip())
