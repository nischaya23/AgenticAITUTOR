import { useEffect, useState } from 'react';
import { Background, Controls, getViewportForBounds, MarkerType, ReactFlow, useNodesInitialized, useReactFlow, type NodeChange } from '@xyflow/react';
import { useReducedMotion } from 'motion/react';
import type { Graph, GraphVisualization, Visit } from './types';
import { useTheme } from './Theme';
import '@xyflow/react/dist/style.css';

const fitOptions = { padding: 0.22 };
const nodeWidth = 170;
const minZoom = 0.04;
const maxZoom = 2;
type NodeLayout = { position?: { x: number; y: number }; measured?: { width: number; height: number } };

function FitVisibleGraph({ canvas }: { canvas: HTMLDivElement | null }) {
  const nodesInitialized = useNodesInitialized();
  const { getNodes, getNodesBounds, setViewport, viewportInitialized } = useReactFlow();

  useEffect(() => {
    if (!canvas || !viewportInitialized) return;
    let frame = 0;
    let previousSize = '';
    const scheduleFit = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        const { clientWidth, clientHeight } = canvas;
        if (!clientWidth || !clientHeight) {
          previousSize = '';
          return;
        }
        const size = clientWidth + 'x' + clientHeight;
        if (size === previousSize) return;
        const nodes = getNodes();
        if (!nodes.length) return;
        previousSize = size;
        const bounds = getNodesBounds(nodes.map(node => node.id));
        void setViewport(getViewportForBounds(bounds, clientWidth, clientHeight, minZoom, maxZoom, fitOptions.padding));
      });
    };
    const observer = new ResizeObserver(scheduleFit);
    observer.observe(canvas);
    scheduleFit();
    return () => {
      observer.disconnect();
      window.cancelAnimationFrame(frame);
    };
  }, [canvas, getNodes, getNodesBounds, nodesInitialized, setViewport, viewportInitialized]);

  return null;
}

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

export function FlowGraph({ graph, visits, step = 0, activeNodes, masteryTarget, label, onSelect }: {
  graph: Graph; visits?: Visit[]; step?: number; masteryTarget?: number; label: string;
  activeNodes?: string[];
  onSelect?: (concept: string) => void;
}) {
  const [canvas, setCanvas] = useState<HTMLDivElement | null>(null);
  const [layout, setLayout] = useState(new Map<string, NodeLayout>());
  const { theme } = useTheme();
  const reducedMotion = useReducedMotion();
  const positions = graphPositions(graph, visits);
  const current = visits?.[step];
  const active = new Set(activeNodes);
  const visited = new Set(visits?.slice(0, step + 1).map(visit => visit.node));
  const selectable = (node: Graph['nodes'][number]) => Boolean(onSelect && node.available && !node.active);
  const nodes = graph.nodes.map(node => ({
    id: node.id,
    initialWidth: nodeWidth,
    initialHeight: 48,
    position: layout.get(node.id)?.position ?? positions.get(node.id)!,
    measured: layout.get(node.id)?.measured,
    data: { label: <><strong>{node.label}</strong>
      {masteryTarget !== undefined && <small>{node.mastery == null ? 'Not assessed yet'
        : 'Estimated mastery: ' + Math.round(node.mastery * 100) + '%'}</small>}
      {onSelect && !node.available && <small>Locked: finish its prerequisites</small>}</> },
    className: [node.active || current?.node === node.id || active.has(node.id) ? 'flow-active'
      : visited.has(node.id) || (node.mastery ?? -1) >= (masteryTarget ?? 1) ? 'flow-visited' : '',
      selectable(node) ? 'flow-selectable' : '', onSelect && !node.available ? 'flow-locked' : ''].join(' ').trim(),
    ariaLabel: selectable(node) ? 'Study ' + node.label : node.label,
  }));
  const edges = graph.edges.map((edge, index) => {
    const active = current?.via === edge.source && current.node === edge.target;
    return { ...edge, id: 'edge-' + index, animated: active && !reducedMotion,
      markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: active ? (theme === 'dark' ? '#d0e9a3' : '#365c37') : '#7d9387', strokeWidth: active ? 3 : 1.5 } };
  });

  function updateLayout(changes: NodeChange[]) {
    const updates = changes.filter(change => (change.type === 'position' && change.position)
      || (change.type === 'dimensions' && change.dimensions));
    if (!updates.length) return;
    setLayout(previous => {
      const next = new Map(previous);
      for (const change of updates) {
        if (change.type === 'position' && change.position) {
          next.set(change.id, { ...next.get(change.id), position: change.position });
        } else if (change.type === 'dimensions' && change.dimensions) {
          next.set(change.id, { ...next.get(change.id), measured: change.dimensions });
        }
      }
      return next;
    });
  }

  return <div className="flow" ref={setCanvas} aria-label={label}>
    <ReactFlow colorMode={theme} nodes={nodes} edges={edges} onNodesChange={updateLayout}
      defaultViewport={{ x: 12, y: 12, zoom: 0.8 }}
      onNodeClick={(_, clicked) => { const node = graph.nodes.find(n => n.id === clicked.id); if (node && selectable(node)) onSelect!(node.id); }}
      fitViewOptions={fitOptions} minZoom={minZoom} maxZoom={maxZoom} nodesConnectable={false} deleteKeyCode={null}>
      <Background /><Controls showInteractive={false} />
      <FitVisibleGraph canvas={canvas} />
    </ReactFlow>
  </div>;
}

export function VisualPlayer({ data }: { data: GraphVisualization }) {
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const slides = 'steps' in data ? data.steps : [];
  const last = slides.length - 1;
  const slide = slides[index];
  const visits = data.kind === 'graph_traversal' || data.kind === 'tree_traversal' ? data.steps : undefined;

  useEffect(() => {
    if (!playing || index >= last) return;
    const timer = window.setTimeout(() => {
      setIndex(index + 1);
      if (index + 1 === last) setPlaying(false);
    }, 1250 / speed);
    return () => window.clearTimeout(timer);
  }, [index, last, playing, speed]);

  function seek(next: number) {
    setPlaying(false);
    setIndex(next);
  }

  return <section className="visual-player" aria-label={data.title}>
    <h3>{data.title}</h3>
    <FlowGraph graph={data} visits={visits} step={index}
      activeNodes={slide && 'active_nodes' in slide ? slide.active_nodes : undefined} label={data.title} />
    {slide && <>
      <p className="step-description" aria-live="polite">Step {index + 1} of {slides.length}. {slide.explanation}</p>
      <div className="player-controls">
        <button type="button" onClick={() => seek(0)}>Restart</button>
        <button type="button" disabled={index === 0} onClick={() => seek(index - 1)}>Previous</button>
        <button type="button" disabled={last < 1} onClick={() => {
          if (!playing && index === last) setIndex(0);
          setPlaying(!playing);
        }}>{playing ? 'Pause' : 'Play'}</button>
        <button type="button" disabled={index === last} onClick={() => seek(index + 1)}>Next</button>
        <label>Speed <select value={speed} onChange={event => setSpeed(Number(event.target.value))}>
          {[0.5, 1, 1.5, 2].map(value => <option key={value} value={value}>{value}×</option>)}
        </select></label>
      </div>
    </>}
  </section>;
}

