import { useState } from 'react';
import { Background, Controls, MarkerType, ReactFlow, type Connection, type NodeChange, type EdgeChange } from '@xyflow/react';
import type { Diagram, DiagramAnnotation } from './types';
import '@xyflow/react/dist/style.css';

export const emptyDiagram = (): Diagram => ({ nodes: [], edges: [] });
const uid = () => crypto.randomUUID();

export default function DiagramView({ value, onChange, disabled = false, annotations = [], label = 'Response diagram' }: {
  value: Diagram; onChange?: (diagram: Diagram) => void; disabled?: boolean;
  annotations?: DiagramAnnotation[]; label?: string;
}) {
  const [source, setSource] = useState('');
  const [target, setTarget] = useState('');
  const [selection, setSelection] = useState<string[]>([]);
  const editing = Boolean(onChange) && !disabled;
  const issue = (kind: 'node' | 'edge', id: string) => annotations.filter(a => a.target === kind && a.id === id);
  const nodes = value.nodes.map(node => ({ id: node.id, position: node.position,
    data: { label: node.label }, selected: selection.includes(node.id), ariaLabel: node.label,
    className: issue('node', node.id).length ? 'diagram-issue' : '' }));
  const edges = value.edges.map(edge => ({ ...edge, selected: selection.includes(edge.id),
    markerEnd: { type: MarkerType.ArrowClosed },
    style: { stroke: issue('edge', edge.id).length ? '#b32335' : '#687f6c', strokeWidth: issue('edge', edge.id).length ? 4 : 2 } }));
  function connect(connection: Connection) {
    if (!editing || value.edges.length >= 60 || !connection.source || !connection.target) return;
    onChange?.({ ...value, edges: [...value.edges, { id: uid(), source: connection.source, target: connection.target }] });
  }
  function removeNode(id: string) {
    onChange?.({ nodes: value.nodes.filter(n => n.id !== id), edges: value.edges.filter(e => e.source !== id && e.target !== id) });
  }
  function changeNodes(changes: NodeChange[]) {
    if (!editing) return;
    let next = value;
    for (const change of changes) {
      if (change.type === 'select') setSelection(ids => change.selected ? [...ids, change.id] : ids.filter(id => id !== change.id));
      if (change.type === 'position' && change.position && Number.isFinite(change.position.x) && Number.isFinite(change.position.y)) {
        next = { ...next, nodes: next.nodes.map(n => n.id === change.id ? { ...n, position: change.position! } : n) };
      }
      if (change.type === 'remove') next = { nodes: next.nodes.filter(n => n.id !== change.id), edges: next.edges.filter(e => e.source !== change.id && e.target !== change.id) };
    }
    if (next !== value) onChange?.(next);
  }
  function changeEdges(changes: EdgeChange[]) {
    if (!editing) return;
    const removed = new Set(changes.filter(c => c.type === 'remove').map(c => c.id));
    for (const change of changes) if (change.type === 'select') setSelection(ids => change.selected ? [...ids, change.id] : ids.filter(id => id !== change.id));
    if (removed.size) onChange?.({ ...value, edges: value.edges.filter(e => !removed.has(e.id)) });
  }
  return <section className="diagram" aria-label={label}>
    <div className="flow"><ReactFlow nodes={nodes} edges={edges} onNodesChange={changeNodes} onEdgesChange={changeEdges}
      onConnect={connect} nodesDraggable={editing} nodesConnectable={editing} elementsSelectable={editing}
      deleteKeyCode={editing ? ['Backspace', 'Delete'] : null} fitView fitViewOptions={{ padding: 0.25 }}>
      <Background /><Controls showInteractive={false} />
    </ReactFlow></div>
    {onChange && <div className="player-controls">
      <button type="button" disabled={!editing || value.nodes.length >= 30} onClick={() => onChange({ ...value,
        nodes: [...value.nodes, { id: uid(), label: 'Node ' + (value.nodes.length + 1), position: { x: (value.nodes.length % 3) * 190, y: Math.floor(value.nodes.length / 3) * 110 } }] })}>Add node</button>
      <button type="button" disabled={!editing || !value.nodes.length} onClick={() => onChange(emptyDiagram())}>Reset diagram</button>
      <span>{value.nodes.length}/30 nodes · {value.edges.length}/60 arrows</span>
    </div>}
    {onChange && <div className="diagram-connect">
      <label>Arrow from<select aria-label="Arrow from" value={source} disabled={!editing} onChange={e => setSource(e.target.value)}>
        <option value="">Choose node</option>{value.nodes.map(n => <option key={n.id} value={n.id}>{n.label}</option>)}</select></label>
      <label>Arrow to<select aria-label="Arrow to" value={target} disabled={!editing} onChange={e => setTarget(e.target.value)}>
        <option value="">Choose node</option>{value.nodes.map(n => <option key={n.id} value={n.id}>{n.label}</option>)}</select></label>
      <button type="button" disabled={!editing || value.edges.length >= 60 || !value.nodes.some(n => n.id === source) || !value.nodes.some(n => n.id === target)}
        onClick={() => connect({ source, target, sourceHandle: null, targetHandle: null })}>Connect nodes</button>
    </div>}
    <ul className="diagram-nodes" aria-label={label + ' nodes'}>{value.nodes.map(n => <li key={n.id} className={issue('node', n.id).length ? 'diagram-issue' : ''}>
      {onChange ? <><label>Node label<input aria-label={'Label for ' + n.label} value={n.label} maxLength={200} disabled={!editing}
        onChange={e => onChange({ ...value, nodes: value.nodes.map(item => item.id === n.id ? { ...item, label: e.target.value || 'Node' } : item) })} /></label>
        <div className="node-controls">{(['x', 'y'] as const).map(axis => <label key={axis}>{axis.toUpperCase()}<input type="number" aria-label={axis.toUpperCase() + ' position for ' + n.label}
          value={n.position[axis]} disabled={!editing} onChange={e => {
            const position = Number(e.target.value); if (Number.isFinite(position)) onChange({ ...value,
              nodes: value.nodes.map(item => item.id === n.id ? { ...item, position: { ...item.position, [axis]: position } } : item) });
          }} /></label>)}<button type="button" disabled={!editing} onClick={() => removeNode(n.id)}>Delete {n.label}</button></div></> : n.label}
      {issue('node', n.id).map((a, i) => <p key={i} className="issue-reason">{a.reason}</p>)}
    </li>)}</ul>
    <ul aria-label={label + ' arrows'}>{value.edges.map(e => <li key={e.id} className={issue('edge', e.id).length ? 'diagram-issue' : ''}>
      {value.nodes.find(n => n.id === e.source)?.label} → {value.nodes.find(n => n.id === e.target)?.label}{' '}
      {onChange && <button type="button" disabled={!editing} aria-label={'Delete arrow ' + e.id}
        onClick={() => onChange({ ...value, edges: value.edges.filter(item => item.id !== e.id) })}>Delete arrow</button>}
      {issue('edge', e.id).map((a, i) => <p key={i} className="issue-reason">{a.reason}</p>)}
    </li>)}</ul>
  </section>;
}
