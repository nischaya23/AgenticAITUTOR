import { useState } from 'react';
import type { LearningPlan, PlanState } from './types';

const empty: LearningPlan = { objective: '', steps: [''], learning_goals: [] };
function PlanView({ plan }: { plan: Partial<LearningPlan> }) {
  return <><p className="plan-objective">{plan.objective}</p>
    <ol>{plan.steps?.map((step, i) => <li key={i}>{step}</li>)}</ol>
    {!!plan.learning_goals?.length && <><strong>Learning goals</strong><ul>{plan.learning_goals.map((goal, i) => <li key={i}>{goal}</li>)}</ul></>}
  </>;
}
export default function LearningPlanPanel({ sessionId, value, disabled, onChange }: {
  sessionId: number; value: PlanState; disabled: boolean; onChange: (value: PlanState) => void;
}) {
  const [draft, setDraft] = useState<LearningPlan | null>(null);
  const [base, setBase] = useState(0);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const conflict = draft !== null && base !== value.plan_version;
  async function request(path: string, method: string, body: object) {
    setSaving(true); setMessage('');
    try {
      const response = await fetch(`/api/sessions/${sessionId}/plan${path}`, { method,
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const data = await response.json();
      if (!response.ok) {
        if (response.status === 409) {
          const latest = await fetch(`/api/sessions/${sessionId}/study`).then(r => r.json());
          if (latest.state) onChange({ type: 'plan', plan: latest.state.plan ?? {}, plan_version: latest.state.plan_version ?? 0, plan_suggestion: latest.state.plan_suggestion });
        }
        throw Error(typeof data.detail === 'string' ? data.detail : 'Check the plan fields and try again.');
      }
      onChange(data); setDraft(null); setMessage('Saved—used by future agent calls.');
    } catch (error) { setMessage((error as Error).message); }
    finally { setSaving(false); }
  }
  function editList(key: 'steps' | 'learning_goals', index: number, text: string) {
    if (draft) setDraft({ ...draft, [key]: draft[key].map((old, i) => i === index ? text : old) });
  }
  return <section className="learning-plan" aria-label="Learning plan">
    <h2>Learning plan</h2>
    {!value.plan.steps?.length && !draft ? <p>No plan yet. It will appear when study starts.</p> : <PlanView plan={value.plan} />}
    {!draft && <button type="button" disabled={disabled || saving} onClick={() => {
      setDraft({ ...empty, ...value.plan, steps: [...(value.plan.steps ?? [''])], learning_goals: [...(value.plan.learning_goals ?? [])] });
      setBase(value.plan_version); setMessage('');
    }}>Edit plan</button>}
    {draft && <form onSubmit={event => { event.preventDefault(); void request('', 'PUT', { plan: draft, expected_version: base }); }}>
      <label>Objective<input disabled={disabled || saving} required maxLength={1000} value={draft.objective} onChange={e => setDraft({ ...draft, objective: e.target.value })} /></label>
      {(['steps', 'learning_goals'] as const).map(key => <fieldset key={key} disabled={disabled || saving}>
        <legend>{key === 'steps' ? 'Steps' : 'Learning goals'}</legend>
        {draft[key].map((text, i) => <div className="plan-edit-row" key={i}>
          <input aria-label={`${key === 'steps' ? 'Step' : 'Goal'} ${i+1}`} required maxLength={500} value={text} onChange={e => editList(key, i, e.target.value)} />
          <button type="button" aria-label={`Remove ${key === 'steps' ? 'step' : 'goal'} ${i+1}`} disabled={key === 'steps' && draft.steps.length === 1} onClick={() => setDraft({ ...draft, [key]: draft[key].filter((_, index) => index !== i) })}>Remove</button>
        </div>)}
        <button type="button" disabled={draft[key].length >= 6} onClick={() => setDraft({ ...draft, [key]: [...draft[key], ''] })}>Add {key === 'steps' ? 'step' : 'goal'}</button>
      </fieldset>)}
      {conflict && <p role="alert">The saved plan changed. Your draft is preserved. Review the saved plan above, then use “Keep draft against latest” to retry.</p>}
      {conflict && <button type="button" onClick={() => setBase(value.plan_version)}>Keep draft against latest</button>}
      <div className="plan-controls"><button type="submit" disabled={disabled || saving || conflict}>Save plan</button>
      <button type="button" disabled={saving} onClick={() => { setDraft(null); setMessage(''); }}>Cancel</button></div>
    </form>}
    {value.plan_suggestion && <div className="plan-suggestion"><h3>Suggested revision</h3><p>{value.plan_suggestion.reason}</p>
      <PlanView plan={value.plan_suggestion.plan} />
      {(['accept', 'dismiss'] as const).map(action => <button key={action} type="button" disabled={disabled || saving || !!draft} onClick={() => void request('/suggestion', 'POST', {
        action, suggestion_id: value.plan_suggestion!.id, expected_version: value.plan_version,
      })}>{action === 'accept' ? 'Accept suggestion' : 'Dismiss'}</button>)}
    </div>}
    <p role="status">{saving ? 'Saving plan…' : message}</p>
  </section>;
}
