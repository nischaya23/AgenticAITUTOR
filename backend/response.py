"""Bounded, JSON-only response diagrams shared by clients and model outputs."""
import json
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal


class Schema(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Position(Schema):
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)


class DiagramNode(Schema):
    id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    position: Position


class DiagramEdge(Schema):
    id: str = Field(min_length=1, max_length=80)
    source: str = Field(min_length=1, max_length=80)
    target: str = Field(min_length=1, max_length=80)


class Diagram(Schema):
    nodes: list[DiagramNode] = Field(default_factory=list, max_length=30)
    edges: list[DiagramEdge] = Field(default_factory=list, max_length=60)

    @model_validator(mode='after')
    def valid_graph(self):
        ids = [node.id for node in self.nodes]
        edge_ids = [edge.id for edge in self.edges]
        if len(set(ids + edge_ids)) != len(ids) + len(edge_ids):
            raise ValueError('Diagram node and edge IDs must be unique.')
        if any(edge.source not in ids or edge.target not in ids for edge in self.edges):
            raise ValueError('Diagram edges must reference existing nodes.')
        return self


class Response(Schema):
    text: str = Field(default='', max_length=20000)
    diagram: Diagram = Field(default_factory=Diagram)


class DiagramAnnotation(Schema):
    target: Literal['node', 'edge']
    id: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=2000)


def response_for(state: dict) -> dict:
    return Response.model_validate(state.get('response') or {'text': state.get('workspace', '')}).model_dump()


def content_key(response: dict) -> str:
    """Moving/reordering graph elements does not change diagnostic content."""
    diagram = response['diagram']
    return json.dumps(dict(text=response['text'],
        nodes=sorted((n['id'], n['label']) for n in diagram['nodes']),
        edges=sorted((e['id'], e['source'], e['target']) for e in diagram['edges'])), sort_keys=True)


def has_content(response: dict) -> bool:
    return bool(response['text'].strip() or response['diagram']['nodes'])
