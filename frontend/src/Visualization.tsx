import { useState } from 'react';
import { Background, Controls, MarkerType, ReactFlow, type NodeChange } from '@xyflow/react';
import { useReducedMotion } from 'motion/react';
import type { Graph } from './types';
type Visit = {node: string; via: string | null};
import { useTheme } from './Theme';
import '@xyflow/react/dist/style.css';


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
  const { theme } = useTheme();
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
      markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: active ? (theme === 'dark' ? '#d0e9a3' : '#365c37') : '#7d9387', strokeWidth: active ? 3 : 1.5 } };
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
    <ReactFlow colorMode={theme} nodes={nodes} edges={edges} onNodesChange={moveNodes} fitView
      onNodeClick={(_, clicked) => { const node = graph.nodes.find(n => n.id === clicked.id); if (node && selectable(node)) onSelect!(node.id); }}
      fitViewOptions={{ padding: 0.22 }} minZoom={0.12} nodesConnectable={false} deleteKeyCode={null}>
      <Background /><Controls showInteractive={false} />
    </ReactFlow>
  </div>;
}

