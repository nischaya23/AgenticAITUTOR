"""Validated payloads for topic-independent scenes and specialized visualizations."""
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from .utils import Schema

Label = Annotated[str, StringConstraints(min_length=1, max_length=200)]
NodeId = Annotated[str, StringConstraints(min_length=1, max_length=80)]
Number = Annotated[float, Field(allow_inf_nan=False)]


class VisualizationSchema(Schema):
    model_config = ConfigDict(strict=True)


class SearchStep(VisualizationSchema):
    left: int
    right: int
    mid: int | None
    comparison: Literal['less', 'equal', 'greater', 'complete']
    explanation: Label


class BinarySearch(VisualizationSchema):
    kind: Literal['binary_search']
    title: Label
    array: list[Number] = Field(min_length=1, max_length=40)
    target: Number
    steps: list[SearchStep] = Field(min_length=2, max_length=20)

    @model_validator(mode='after')
    def validate_trace(self):
        if self.array != sorted(self.array):
            raise ValueError('Binary search requires a sorted array.')
        left, right = 0, len(self.array)
        for index, step in enumerate(self.steps):
            if (step.left, step.right) != (left, right):
                raise ValueError('Search bounds must follow the previous lower-bound step.')
            if index == len(self.steps) - 1:
                if left != right or step.mid is not None or step.comparison != 'complete':
                    raise ValueError('End the trace at the collapsed interval.')
                break
            mid = (left + right) // 2
            if left >= right or step.mid != mid:
                raise ValueError('Midpoint must be within the current half-open interval.')
            value = self.array[mid]
            comparison = 'less' if value < self.target else 'greater' if value > self.target else 'equal'
            if step.comparison != comparison:
                raise ValueError('Comparison must agree with the array and target.')
            if comparison == 'less':
                left = mid + 1
            else:
                right = mid
        return self


class GraphNode(VisualizationSchema):
    id: NodeId
    label: Label


class GraphEdge(VisualizationSchema):
    source: NodeId
    target: NodeId


class GraphData(VisualizationSchema):
    title: Label
    nodes: list[GraphNode] = Field(min_length=1, max_length=30)
    edges: list[GraphEdge] = Field(max_length=60)

    @model_validator(mode='after')
    def validate_graph(self):
        ids = {node.id for node in self.nodes}
        edges = {(edge.source, edge.target) for edge in self.edges}
        if len(ids) != len(self.nodes) or len(edges) != len(self.edges):
            raise ValueError('Graph nodes and edges must be unique.')
        if any(a not in ids or b not in ids for a, b in edges):
            raise ValueError('Graph edge endpoints must identify existing nodes.')
        return self


class ConceptGraph(GraphData):
    kind: Literal['concept_graph']


class Visit(VisualizationSchema):
    node: NodeId
    via: NodeId | None
    explanation: Label


class Traversal(GraphData):
    steps: list[Visit] = Field(min_length=1, max_length=30)

    @model_validator(mode='after')
    def validate_visits(self):
        ids = {node.id for node in self.nodes}
        edges = {(edge.source, edge.target) for edge in self.edges}
        visited = set()
        for step in self.steps:
            if step.node not in ids or step.node in visited:
                raise ValueError('Each visit must identify a distinct existing node.')
            if visited:
                if step.via not in visited or (step.via, step.node) not in edges:
                    raise ValueError('Visit must follow an edge from a previously visited node.')
            elif step.via is not None:
                raise ValueError('The starting node cannot have an incoming traversal step.')
            visited.add(step.node)
        return self


class GraphTraversal(Traversal):
    kind: Literal['graph_traversal']


class TreeTraversal(Traversal):
    kind: Literal['tree_traversal']
    root: NodeId

    @model_validator(mode='after')
    def validate_tree(self):
        ids = {node.id for node in self.nodes}
        children = [edge.target for edge in self.edges]
        if len(children) != len(ids) - 1 or set(children) != ids - {self.root} or self.root not in ids:
            raise ValueError('Tree edges must give each non-root node exactly one parent.')
        reached = {self.root}
        for _ in self.nodes:
            reached.update(edge.target for edge in self.edges if edge.source in reached)
        if reached != ids or self.steps[0].node != self.root:
            raise ValueError('Tree must be connected and traversal must start at its root.')
        return self


SCENE_WIDTH = 800
SCENE_HEIGHT = 450
SceneX = Annotated[int, Field(ge=0, le=SCENE_WIDTH)]
SceneY = Annotated[int, Field(ge=0, le=SCENE_HEIGHT)]
Tone = Literal['neutral', 'accent', 'success', 'warning']


class SceneObject(VisualizationSchema):
    id: NodeId
    x: SceneX
    y: SceneY
    tone: Tone


class SceneRectangle(SceneObject):
    kind: Literal['rect']
    width: Annotated[int, Field(ge=24, le=SCENE_WIDTH)]
    height: Annotated[int, Field(ge=24, le=SCENE_HEIGHT)]
    label: Label | None = None


class SceneCircle(SceneObject):
    kind: Literal['circle']
    radius: Annotated[int, Field(ge=10, le=SCENE_HEIGHT // 2)]
    label: Label | None = None


class SceneText(SceneObject):
    kind: Literal['text']
    width: Annotated[int, Field(ge=24, le=SCENE_WIDTH)]
    label: Label


class SceneLine(SceneObject):
    kind: Literal['line', 'arrow']
    dx: Annotated[int, Field(ge=-SCENE_WIDTH, le=SCENE_WIDTH)]
    dy: Annotated[int, Field(ge=-SCENE_HEIGHT, le=SCENE_HEIGHT)]
    label: Label | None = None


Primitive = SceneRectangle | SceneCircle | SceneText | SceneLine


class SceneChange(VisualizationSchema):
    id: NodeId
    x: SceneX | None = None
    y: SceneY | None = None
    tone: Tone | None = None
    label: Label | None = None
    visible: bool | None = None


class SceneStep(VisualizationSchema):
    explanation: Label
    changes: list[SceneChange] = Field(max_length=16)


def validate_position(shape: Primitive, x: int, y: int):
    if isinstance(shape, SceneCircle):
        r = shape.radius
        fits = r <= x <= SCENE_WIDTH - r and r <= y <= SCENE_HEIGHT - r
    elif isinstance(shape, SceneLine):
        fits = (shape.dx != 0 or shape.dy != 0) and (
            0 <= x + shape.dx <= SCENE_WIDTH and 0 <= y + shape.dy <= SCENE_HEIGHT)
    elif isinstance(shape, SceneRectangle):
        fits = x + shape.width <= SCENE_WIDTH and y + shape.height <= SCENE_HEIGHT
    else:
        fits = x + shape.width <= SCENE_WIDTH and y <= SCENE_HEIGHT - 24
    if not fits:
        raise ValueError(f'Scene object {shape.id!r} must fit inside the 800 × 450 canvas.')


class Scene(VisualizationSchema):
    kind: Literal['scene']
    title: Label
    objects: list[Primitive] = Field(min_length=1, max_length=24)
    steps: list[SceneStep] = Field(min_length=1, max_length=12)

    @model_validator(mode='after')
    def validate_scene(self):
        objects = {shape.id: shape for shape in self.objects}
        if len(objects) != len(self.objects):
            raise ValueError('Scene object IDs must be unique.')
        if sum(len(step.changes) for step in self.steps) > 64:
            raise ValueError('A scene may contain at most 64 changes.')
        positions = {shape.id: (shape.x, shape.y) for shape in self.objects}
        for shape in self.objects:
            validate_position(shape, shape.x, shape.y)
        for step in self.steps:
            seen = set()
            for change in step.changes:
                if change.id not in objects or change.id in seen:
                    raise ValueError('Each change must refer to a distinct existing object in its step.')
                shape = objects[change.id]
                x, y = positions[change.id]
                x = x if change.x is None else change.x
                y = y if change.y is None else change.y
                validate_position(shape, x, y)
                positions[change.id] = x, y
                seen.add(change.id)
        return self


class VisualizationText(VisualizationSchema):
    content: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    concept: Label


class GeminiSceneRequest(VisualizationText):
    # Keep the Gemini function declaration flat. Validate the decoded scene below it.
    scene_json: Annotated[str, StringConstraints(min_length=1, max_length=24000)]


class VisualizationRequest(VisualizationText):
    # Nested anyOf keeps the function schema root an object for strict tool calling.
    visualization: Scene | BinarySearch | TreeTraversal | GraphTraversal | ConceptGraph
