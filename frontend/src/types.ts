export type Annotation = {
  line: number; quote: string; reason: string; start: number; end: number;
  category: 'possible_error' | 'important_concept' | 'missing_justification' | 'reconsider' | 'explanation_needed';
};
export type Diagram = {
  nodes: { id: string; label: string; position: { x: number; y: number } }[];
  edges: { id: string; source: string; target: string }[];
};
export type Response = { text: string; diagram: Diagram };
export type DiagramAnnotation = { target: 'node' | 'edge'; id: string; reason: string };
export type Hypothesis = { id: string; concept: string; possible_gap: string; confidence: number;
  evidence: { reference: string; summary: string }[]; diagnostic_focus: string;
  status: 'tentative' | 'supported' | 'weakened' | 'resolved' };
export type HypothesisState = { status: 'analyzing' | 'stale' | 'unavailable' | 'empty' | 'ready';
  items: Hypothesis[]; origin?: 'draft' | 'submission'; version?: number; error?: string;
  response_revision?: number; question_id?: string; plan_version?: number };
export type Graph = {
  nodes: { id: string; label: string; mastery?: number | null; active?: boolean }[];
  edges: { source: string; target: string }[];
};
export type Visit = { node: string; via: string | null; explanation: string };
export type SearchStep = {
  left: number; right: number; mid: number | null;
  comparison: 'less' | 'equal' | 'greater' | 'complete'; explanation: string;
};
export type Search = { kind: 'binary_search'; title: string; array: number[]; target: number; steps: SearchStep[] };
export type SceneTone = 'neutral' | 'accent' | 'success' | 'warning';
export type SceneObject = { id: string; x: number; y: number; tone: SceneTone } & (
  | { kind: 'rect'; width: number; height: number; label: string | null }
  | { kind: 'circle'; radius: number; label: string | null }
  | { kind: 'text'; width: number; label: string }
  | { kind: 'line' | 'arrow'; dx: number; dy: number; label?: string | null }
);
export type SceneChange = {
  id: string; x: number | null; y: number | null; tone: SceneTone | null;
  label: string | null; visible: boolean | null;
};
export type Scene = { kind: 'scene'; title: string; objects: SceneObject[];
  steps: { explanation: string; changes: SceneChange[] }[] };
export type Visualization = Search
  | Scene
  | (Graph & { kind: 'concept_graph'; title: string })
  | (Graph & { kind: 'graph_traversal' | 'tree_traversal'; title: string; steps: Visit[] });
export type Observer = { decision?: string; reason?: string; revision?: number; feedback_intervention_id?: number | null };
export type Session = {
  current_problem: { id?: string; prompt?: string; solved?: boolean;
    format?: 'explanation' | 'trace' | 'diagram_interpretation' | 'diagram_construction';
    diagram?: Diagram | null; starter_diagram?: Diagram | null };
  plan?: Partial<LearningPlan>; plan_version?: number; plan_suggestion?: PlanSuggestion | null;
  current_concept: string; workspace: string; material_id?: string;
  study_complete?: boolean; ended: boolean; observer_status: Observer;
  response?: Response; response_revision?: number; hypothesis_state?: HypothesisState;
  last_assessment?: Assessment; assessment_stages?: { reflection: string; hypotheses: string };
  question_generation_error?: string | null;
};
export type Study = {
  type: 'study_state'; state: Session; graph: Graph; mastery_target: number;
  material: { id: string; name: string; chunks: { id: string; text: string }[] };
};
export type Tool = 'hint' | 'explanation' | 'highlight' | 'visualization' | 'guiding_question';
export type Result = {
  type: Tool; content: string;
  metadata: Partial<Graph> & {
    annotations?: Annotation[]; visualization?: Visualization; steps?: string[];
    diagram_annotations?: DiagramAnnotation[];
    target?: 'workspace' | 'material'; source_id?: string; quote?: string; start?: number; end?: number;
  };
};
export type Assessment = { correct: boolean; reason: string; verified: boolean; method?: string };
export type HistoryItem =
  | { id: number; type: 'student_message'; data: { content: string } }
  | { id: number; type: 'intervention'; data: Result & { proactive?: boolean } };


export type LearningPlan = { objective: string; steps: string[]; learning_goals: string[] };
export type PlanSuggestion = { id: string; plan: LearningPlan; reason: string; base_version: number };
export type PlanState = { type: 'plan'; plan: Partial<LearningPlan>; plan_version: number; plan_suggestion?: PlanSuggestion | null };
