"""Validate the graph and slides before sending them to React Flow."""
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from .utils import Schema

NodeId = Annotated[str, StringConstraints(min_length=1, max_length=80)]
Label = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class Node(Schema):
    id: NodeId
    label: Label


class Edge(Schema):
    source: NodeId
    target: NodeId


class Slide(Schema):
    explanation: str = Field(min_length=1, max_length=600)
    active_nodes: list[NodeId] = Field(min_length=1, max_length=24)


class Slideshow(Schema):
    kind: Literal['slideshow']
    title: Label
    nodes: list[Node] = Field(min_length=1, max_length=24)
    edges: list[Edge] = Field(max_length=48)
    steps: list[Slide] = Field(min_length=2, max_length=12)

    @model_validator(mode='after')
    def validate_references(self):
        ids = {node.id for node in self.nodes}
        edges = {(edge.source, edge.target) for edge in self.edges}
        if len(ids) != len(self.nodes) or len(edges) != len(self.edges):
            raise ValueError('Node IDs and edges must be unique.')
        if any(source not in ids or target not in ids for source, target in edges):
            raise ValueError('Edge endpoints must reference existing nodes.')
        for slide in self.steps:
            if len(set(slide.active_nodes)) != len(slide.active_nodes) or not ids.issuperset(slide.active_nodes):
                raise ValueError('Each slide must highlight distinct existing nodes.')
        return self


class VisualizationRequest(Schema):
    content: str = Field(min_length=1, max_length=12000)
    concept: Label
    visualization: Slideshow


class GeminiVisualizationRequest(Schema):
    content: str = Field(min_length=1, max_length=12000)
    concept: Label
    slideshow_json: str = Field(min_length=1, max_length=24000)
