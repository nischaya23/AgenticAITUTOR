export type StepDescription = { label: string; actor?: string };
export type FlowStep = StepDescription & { id: string; state: 'working' | 'done' | 'paused' };

export type WorkflowEvent = {
  run_id: string;
  span_id?: string;
  type: string;
  status: string;
  data: Record<string, unknown>;
};

export type TutorStep = FlowStep & { runId: string };

const tutorLabels: Record<string, StepDescription> = {
  'Assessment · Grade submission': { label: 'Checking your answer', actor: 'Assessment Agent' },
  'Hypothesis · Analyze response': { label: 'Reviewing your work', actor: 'Hypothesis Agent' },
  'Knowledge · Generate question': { label: 'Creating a practice question', actor: 'Question Generator' },
  'OBSERVER_REVIEW': { label: 'Checking whether to offer help', actor: 'Observer LLM' },
  'Planner · Create plan': { label: 'Planning your next step', actor: 'Planner Agent' },
  'Planner · Select teaching tool': { label: 'Choosing how to help', actor: 'Planner Agent' },
  'Planner · Revise plan': { label: 'Updating your plan', actor: 'Planner Agent' },
  'Reflection · Review assessment': { label: 'Reviewing feedback', actor: 'Reflection Agent' },
  'Research · Gather evidence': { label: 'Gathering evidence', actor: 'Research Agent' },
  'Research · Search': { label: 'Searching sources', actor: 'Research Agent' },
  'Research · Select evidence': { label: 'Reviewing sources', actor: 'Research Agent' },
  'Tutor · Generate response': { label: 'Writing a reply', actor: 'Tutor Agent' },
  'Tutor · Execute tool': { label: 'Preparing your request', actor: 'Tutor Agent' },
};

export function updateTutorFlow(previous: TutorStep[], events: WorkflowEvent[]) {
  let steps = previous;
  for (const event of events) {
    const description = typeof event.data.name === 'string' ? tutorLabels[event.data.name] : undefined;
    if (event.type === 'span.started' && event.span_id && description) {
      if (steps.some(step => step.id === event.span_id)) continue;
      if (!steps.some(step => step.state === 'working' || step.runId === event.run_id)) steps = [];
      const step: TutorStep = { ...description, id: event.span_id, runId: event.run_id, state: 'working' };
      steps = [...steps, step].slice(-4);
    } else if (['span.completed', 'span.failed', 'span.cancelled'].includes(event.type)) {
      const state = event.status === 'completed' ? 'done' : 'paused';
      steps = steps.map(step => step.id === event.span_id ? { ...step, state } : step);
    } else if (event.type.startsWith('run.') && !['queued', 'running', 'waiting'].includes(event.status)) {
      const state = ['completed', 'warnings'].includes(event.status) ? 'done' : 'paused';
      steps = steps.map(step => step.runId === event.run_id && step.state === 'working' ? { ...step, state } : step);
    }
  }
  return steps;
}

export default function ActivityFlow({ label, steps }: { label: string; steps: FlowStep[] }) {
  return <ol className="activity-flow" aria-label={label} aria-live="polite">
    {steps.map(step => <li key={step.id} data-state={step.state}
      aria-current={step.state === 'working' ? 'step' : undefined}>
      <span className="activity-step">
        <span className="activity-marker" aria-hidden="true">{step.state === 'done' ? '✓' : step.state === 'paused' ? '–' : ''}</span>
        <span>{step.label}{step.actor && <span className="activity-actor"> ({step.actor})</span>}</span>
        {step.state !== 'working' && <span className="sr-only"> ({step.state === 'done' ? 'complete' : 'paused'})</span>}
      </span>
    </li>)}
  </ol>;
}
