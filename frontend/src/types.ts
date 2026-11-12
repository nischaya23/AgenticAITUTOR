export type Annotation = {
  line: number; quote: string; reason: string; start: number; end: number;
  category: 'possible_error' | 'important_concept' | 'missing_justification' | 'reconsider' | 'explanation_needed';
};
export type Response = { text: string };
export type Hypothesis = { id: string; concept: string; possible_gap: string; confidence: number;
  evidence: { reference: string; summary: string }[]; diagnostic_focus: string;
  status: 'tentative' | 'supported' | 'weakened' | 'resolved' };
export type HypothesisState = { status: 'analyzing' | 'stale' | 'unavailable' | 'empty' | 'ready';
  items: Hypothesis[]; origin?: 'draft' | 'submission'; version?: number; error?: string;
  response_revision?: number; question_id?: string; plan_version?: number };
export type Graph = {
  nodes: { id: string; label: string; mastery?: number | null; active?: boolean; available?: boolean }[];
  edges: { source: string; target: string }[];
};
export type Video = { url: string; title: string; duration: number;
  chapters: { start: number; end: number; text: string }[] };
export type Observer = { decision?: string; reason?: string; revision?: number; decision_source?: string; feedback_intervention_id?: number | null };
export type Session = {
  current_problem: { id?: string; prompt?: string; solved?: boolean;
    format?: 'explanation' | 'trace' };
  plan?: Partial<LearningPlan>; plan_version?: number; plan_suggestion?: PlanSuggestion | null;
  current_concept: string; workspace: string; material_id?: string;
  proactive_enabled?: boolean; study_complete?: boolean; ended: boolean; observer_status: Observer; assessment_pending?: boolean; focus_concept?: string | null;
  response?: Response; response_revision?: number; hypothesis_state?: HypothesisState;
  last_assessment?: Assessment; assessment_stages?: { reflection: string; hypotheses: string };
  question_generation_error?: string | null; pending_video?: string | null; last_video_operation?: string;
};
export type Study = {
  type: 'study_state'; state: Session; graph: Graph; mastery_target: number;
  material: { id: string; name: string; sources?: string[]; origin?: 'upload' | 'web' | 'topic'; topic?: string; web_enhance?: boolean;
    graph_sources?: {id: string; title: string; url?: string}[]; graph?: {evidence?: {concept: string; prerequisite: string; source_id: string; quote: string}[]}; web_sources?: { title: string; url: string; concept?: string }[]; chunks: { id: string; source?: string; url?: string; text: string }[] };
};
export type Meaning = { label: string; query: string; description: string };
export type Tool = 'hint' | 'explanation' | 'highlight' | 'visualization' | 'guiding_question' | 'web_search';
export type Result = {
  type: Tool; content: string;
  metadata: Partial<Graph> & {
    annotations?: Annotation[]; video?: Video; steps?: string[];
    retrieved_sources?: {id: string; source: string; url?: string}[];
    target?: 'workspace' | 'material'; source_id?: string; quote?: string; start?: number; end?: number;
  };
};
export type Assessment = { correct: boolean; reason: string; verified: boolean; method?: string };
export type HistoryItem =
  | { id: number; workflow_run_id?: string | null; type: 'student_message'; data: { content: string } }
  | { id: number; workflow_run_id?: string | null; type: 'intervention'; data: Result & { proactive?: boolean } };


export type LearningPlan = { objective: string; steps: string[]; learning_goals: string[] };
export type PlanSuggestion = { id: string; plan: LearningPlan; reason: string; base_version: number };
export type PlanState = { type: 'plan'; plan: Partial<LearningPlan>; plan_version: number; plan_suggestion?: PlanSuggestion | null };

export type ProgressEvent = { id?: number; kind: string; message?: string; title?: string; url?: string;
  query?: string; reason?: string; quote?: string; timestamp?: string };
export type Operation = { kind?: string; content?: string; id: string; status: string; error?: string; events: ProgressEvent[];
  result?: { id?: string; type?: string; discarded?: boolean; url?: string; meanings?: Meaning[] } };
export type MemoryItem = { id: string; kind: string; original_text: string; effective_text: string;
  concept?: string; source_event_id?: number; updated_at?: string; disputed: boolean; student_note: string;
  edits: {at: string; action: string; before: string; text: string}[] };
export type MemorySnapshot = { revision: number; short_term: { concept?: string; question?: string; response: Response;
  plan: Partial<LearningPlan>; assessment?: Assessment; hypotheses?: HypothesisState; assessment_pending: boolean };
  long_term: {mastery: Record<string,number>; hypotheses: Hypothesis[]}; items: MemoryItem[] };
