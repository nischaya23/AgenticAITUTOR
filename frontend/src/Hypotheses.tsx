import type { HypothesisState } from './types';

export default function Hypotheses({ state }: { state?: HypothesisState }) {
  return <section className="hypotheses" aria-label="Possible knowledge gaps" aria-live="polite">
    <h2>Possible knowledge gaps</h2>
    <p className="muted">Uncertain learning hypotheses, based on {state?.origin === 'submission' ? 'an assessed submission' : 'a draft'}. These may change with more evidence.</p>
    <p role="status">{state?.status === 'analyzing' ? 'Analyzing your response…'
      : state?.status === 'stale' ? 'Response, question, or plan changed. Previous hypotheses may be stale.'
      : state?.status === 'unavailable' ? state.error ?? 'Analysis unavailable. Previous hypotheses are shown.'
      : state?.items.length ? 'Possible gaps to explore' : 'No possible knowledge gaps identified yet.'}</p>
    <ul>{state?.items.map(item => <li key={item.concept + ':' + item.id}>
      <strong>{item.possible_gap}</strong><p>{item.concept} · {Math.round(item.confidence * 100)}% confidence · {item.status}</p>
      <ul>{item.evidence.map((e, i) => <li key={i}>{e.summary} <small>({e.reference})</small></li>)}</ul>
      <p>Next focus: {item.diagnostic_focus}</p>
    </li>)}</ul>
  </section>;
}
