import { useEffect, useState } from 'react';
import type { LongTermMemory } from './types';

type ConceptRow = {
  name: string;
  mastery?: number;
  summary?: string;
  nextGoal?: string;
  gaps: string[];
};

function conceptRows(memory: LongTermMemory) {
  const rows = new Map<string, ConceptRow>();
  function rowFor(name: string) {
    const key = name.trim().replace(/\s+/g, ' ').toLowerCase();
    let row = rows.get(key);
    if (!row) {
      row = { name, gaps: [] };
      rows.set(key, row);
    }
    return row;
  }

  for (const entry of Object.values(memory.concept_memory ?? {})) {
    const row = rowFor(entry.name);
    row.summary = entry.summary;
    row.nextGoal = entry.next_goal;
  }
  for (const [name, mastery] of Object.entries(memory.mastery)) rowFor(name).mastery = mastery;
  for (const hypothesis of memory.hypotheses) {
    if (hypothesis.status !== 'resolved' && hypothesis.status !== 'weakened') {
      rowFor(hypothesis.concept).gaps.push(hypothesis.possible_gap);
    }
  }
  return [...rows.values()].sort((a, b) => a.name.localeCompare(b.name));
}

export default function MemoryPanel({ sessionId, active, version }: {
  sessionId: number; active: boolean; version: number;
}) {
  const [memory, setMemory] = useState<LongTermMemory | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!active) return;
    let stopped = false;
    void fetch('/api/sessions/' + sessionId + '/memory').then(async response => {
      const data = await response.json();
      if (!response.ok) throw Error(data.detail ?? 'Memory unavailable.');
      if (!stopped) {
        setMemory(data.long_term);
        setError('');
      }
    }).catch(error => { if (!stopped) setError(error.message); });
    return () => { stopped = true; };
  }, [sessionId, active, version]);

  const rows = memory ? conceptRows(memory) : [];
  const profile = memory?.learner_profile;
  const goals = memory?.student_goals ?? [];
  const hasMemory = Boolean(rows.length || profile || goals.length);

  return <section aria-labelledby="learning-memory-title">
    <h2 id="learning-memory-title">Learning memory</h2>
    {error && <p role="alert">{error}</p>}
    {!memory && !error && <p role="status">Loading memory…</p>}
    {memory && !hasMemory && <p className="muted">No long-term learning memory yet.</p>}
    {memory && hasMemory && <div className="memory-table-wrap">
      <table className="memory-table">
        <caption>Long-term memory across your study sessions</caption>
        <thead><tr>
          <th scope="col">Concept</th>
          <th scope="col">What the tutor remembers</th>
          <th scope="col">Suggested next step</th>
        </tr></thead>
        <tbody>
          {rows.map(row => <tr key={row.name}>
            <th scope="row">
              {row.name}
              {row.mastery !== undefined && <span className="memory-mastery">{Math.round(row.mastery * 100)}% estimated mastery</span>}
            </th>
            <td>
              {row.summary && <p>{row.summary}</p>}
              {row.gaps.length > 0 && <ul>{row.gaps.map((gap, index) =>
                <li key={index}>Possible gap · {gap}</li>
              )}</ul>}
              {!row.summary && !row.gaps.length && <span className="muted">—</span>}
            </td>
            <td>{row.nextGoal || <span className="muted">—</span>}</td>
          </tr>)}
        </tbody>
        {(profile || goals.length > 0) && <tfoot>
          {profile && <tr><th scope="row">Learner profile</th><td colSpan={2}>{profile}</td></tr>}
          {goals.length > 0 && <tr><th scope="row">Your goals</th><td colSpan={2}>
            <ul>{goals.map((goal, index) => <li key={index}>{goal}</li>)}</ul>
          </td></tr>}
        </tfoot>}
      </table>
    </div>}
  </section>;
}
