import { lazy, Suspense, useEffect, useState } from 'react';
import { Background, Controls, MarkerType, ReactFlow, type NodeChange } from '@xyflow/react';
import { LayoutGroup, motion, useReducedMotion } from 'motion/react';
import type { Graph, Search, Visualization, Visit } from './types';
import '@xyflow/react/dist/style.css';

const SceneCanvas = lazy(() => import('./SceneCanvas'));

function graphPositions(graph: Graph, visits?: Visit[]) {
  const levels = new Map(graph.nodes.map(node => [node.id, 0]));
  if (visits) {
    for (const visit of visits) {
      if (visit.via !== null) levels.set(visit.node, (levels.get(visit.via) ?? 0) + 1);
    }
  } else {
    for (const node of graph.nodes) {
      for (const edge of graph.edges) {
        if (levels.has(edge.source) && levels.has(edge.target)) {
          levels.set(edge.target, Math.min(graph.nodes.length - 1,
            Math.max(levels.get(edge.target)!, levels.get(edge.source)! + 1)));
        }
      }
    }
  }
  const counts = new Map<number, number>();
  return new Map(graph.nodes.map(node => {
    const level = levels.get(node.id) ?? 0;
    const column = counts.get(level) ?? 0;
    counts.set(level, column + 1);
    return [node.id, { x: column * 195, y: level * 105 }];
  }));
}

export function FlowGraph({ graph, visits, step = 0, masteryTarget, label, onSelect }: {
  graph: Graph; visits?: Visit[]; step?: number; masteryTarget?: number; label: string;
  onSelect?: (concept: string) => void;
}) {
  const [moved, setMoved] = useState(new Map<string, { x: number; y: number }>());
  const reducedMotion = useReducedMotion();
  const positions = graphPositions(graph, visits);
  const current = visits?.[step];
  const visited = new Set(visits?.slice(0, step + 1).map(visit => visit.node));
  const selectable = (node: Graph['nodes'][number]) => Boolean(onSelect && node.available && !node.active);
  const nodes = graph.nodes.map(node => ({
    id: node.id,
    position: moved.get(node.id) ?? positions.get(node.id)!,
    data: { label: <><strong>{node.label}</strong>
      {masteryTarget !== undefined && <small>{node.mastery == null ? 'Not assessed yet'
        : 'Estimated mastery: ' + Math.round(node.mastery * 100) + '%'}</small>}
      {onSelect && !node.available && <small>Locked: finish its prerequisites</small>}</> },
    className: [node.active || current?.node === node.id ? 'flow-active'
      : visited.has(node.id) || (node.mastery ?? -1) >= (masteryTarget ?? 1) ? 'flow-visited' : '',
      selectable(node) ? 'flow-selectable' : '', onSelect && !node.available ? 'flow-locked' : ''].join(' ').trim(),
    ariaLabel: selectable(node) ? 'Study ' + node.label : node.label,
  }));
  const edges = graph.edges.map((edge, index) => {
    const active = current?.via === edge.source && current.node === edge.target;
    return { ...edge, id: 'edge-' + index, animated: active && !reducedMotion,
      markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: active ? '#a45a15' : '#687f6c', strokeWidth: active ? 3 : 1.5 } };
  });

  function moveNodes(changes: NodeChange[]) {
    const moves = changes.filter(change => change.type === 'position' && change.position);
    if (!moves.length) return;
    setMoved(previous => {
      const next = new Map(previous);
      for (const move of moves) if (move.type === 'position' && move.position) next.set(move.id, move.position);
      return next;
    });
  }

  return <div className="flow" aria-label={label}>
    <ReactFlow nodes={nodes} edges={edges} onNodesChange={moveNodes} fitView
      onNodeClick={(_, clicked) => { const node = graph.nodes.find(n => n.id === clicked.id); if (node && selectable(node)) onSelect!(node.id); }}
      fitViewOptions={{ padding: 0.22 }} minZoom={0.12} nodesConnectable={false} deleteKeyCode={null}>
      <Background /><Controls showInteractive={false} />
    </ReactFlow>
  </div>;
}

function SearchArray({ data, index }: { data: Search; index: number }) {
  const step = data.steps[index];
  const reducedMotion = useReducedMotion();
  const pointers = [['L', step.left], ['M', step.mid], ['R', step.right]] as const;
  return <>
    <p>Target {data.target} · Interval [{step.left}, {step.right}) · {step.comparison}</p>
    <div className="array-scroll">
      <LayoutGroup>
        <div className="search-array">
          {[...data.array, null].map((value, i) => <div className="search-column" key={i}>
            <div className="pointers">
              {pointers.filter(([, at]) => at === i).map(([label]) =>
                <motion.span layoutId={label} transition={{ duration: reducedMotion ? 0 : 0.28 }} key={label}>{label}</motion.span>)}
            </div>
            <motion.div className="array-cell" animate={{
              backgroundColor: i === step.mid ? '#f5cc8b' : i >= step.left && i < step.right ? '#cce7d3' : '#f0f3ee',
              scale: i === step.mid && !reducedMotion ? 1.06 : 1,
            }} transition={{ duration: reducedMotion ? 0 : 0.25 }}>{value ?? 'End'}</motion.div>
            <small>{i}</small>
          </div>)}
        </div>
      </LayoutGroup>
    </div>
  </>;
}

export default function VisualPlayer({ data }: { data: Visualization }) {
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const count = 'steps' in data ? data.steps.length : 0;
  useEffect(() => {
    if (!playing || index >= count - 1) return;
    const timer = window.setTimeout(() => {
      setIndex(index + 1);
      if (index + 1 === count - 1) setPlaying(false);
    }, 1250 / speed);
    return () => window.clearTimeout(timer);
  }, [count, index, playing, speed]);

  const visits = data.kind === 'graph_traversal' || data.kind === 'tree_traversal' ? data.steps : undefined;
  function seek(at: number) { setPlaying(false); setIndex(at); }
  return <section className="visual-player" aria-label={data.title}>
    <h3>{data.title}</h3>
    {data.kind === 'binary_search' ? <SearchArray data={data} index={index} />
      : data.kind === 'scene' ? <Suspense fallback={<p role="status">Loading visualization…</p>}>
        <SceneCanvas scene={data} index={index} speed={speed} />
      </Suspense>
      : <FlowGraph graph={data} visits={visits} step={index} label={data.title} />}
    {visits && <p>Traversal order: {visits.slice(0, index + 1).map(visit =>
      'nodes' in data && data.nodes.find(node => node.id === visit.node)?.label).join(' → ')}</p>}
    {'steps' in data && <>
      <p className="step-description" aria-live="polite">Step {index + 1} of {count}. {data.steps[index].explanation}</p>
      <div className="player-controls">
        <button type="button" onClick={() => seek(0)}>Restart</button>
        <button type="button" disabled={!index} onClick={() => seek(index - 1)}>Previous</button>
        <button type="button" disabled={count < 2} onClick={() => {
          if (!playing && index === count - 1) setIndex(0);
          setPlaying(!playing);
        }}>{playing ? 'Pause' : 'Play'}</button>
        <button type="button" disabled={index >= count - 1} onClick={() => seek(index + 1)}>Next</button>
        <label>Speed <select value={speed} onChange={event => setSpeed(Number(event.target.value))}>
          {[0.5, 1, 1.5, 2].map(value => <option key={value} value={value}>{value}×</option>)}
        </select></label>
      </div>
    </>}
  </section>;
}

