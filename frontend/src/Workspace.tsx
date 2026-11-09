import { useEffect, useRef, useState } from 'react';
import { basicSetup } from 'codemirror';
import { Compartment, EditorState, StateEffect, StateField } from '@codemirror/state';
import { Decoration, EditorView, showTooltip, tooltips, type DecorationSet, type Tooltip } from '@codemirror/view';
import type { Annotation } from './types';

const emptyReview = { marks: Decoration.none, tooltip: null };
const setReview = StateEffect.define<{ marks: DecorationSet; tooltip: Tooltip | null }>();
const review = StateField.define<{ marks: DecorationSet; tooltip: Tooltip | null }>({
  create: () => emptyReview,
  update(value, transaction) {
    for (const effect of transaction.effects) {
      if (effect.is(setReview)) return effect.value;
    }
    return transaction.docChanged ? emptyReview : value;
  },
  provide: field => [
    EditorView.decorations.from(field, state => state.marks),
    showTooltip.from(field, state => state.tooltip),
  ],
});

export function annotationRange(text: string, item: Annotation) {
  // Python offsets count code points. CodeMirror positions count UTF-16 units.
  const chars = Array.from(text);
  if (item.start < 0 || item.end > chars.length || item.end <= item.start
      || chars.slice(item.start, item.end).join('') !== item.quote) return null;
  const from = chars.slice(0, item.start).join('').length;
  return { from, to: from + item.quote.length };
}

export default function Workspace({ initial, disabled, annotations, onEdit, canAsk, onAsk, onDismiss }: {
  initial: string; disabled: boolean; annotations: Annotation[]; onEdit: (text: string) => void;
  canAsk: boolean; onAsk: (item: Annotation) => void; onDismiss: (item: Annotation) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const callbacks = useRef({ onEdit, onAsk, onDismiss, annotations });
  const initialDoc = useRef(initial);
  const editable = useRef(new Compartment());
  const [selected, setSelected] = useState<Annotation>();
  callbacks.current = { onEdit, onAsk, onDismiss, annotations };
  const active = selected && annotations.includes(selected) ? selected : annotations[0];

  useEffect(() => {
    const editor = new EditorView({
      doc: initialDoc.current,
      parent: host.current!,
      extensions: [basicSetup, EditorView.lineWrapping, review,
        tooltips({ position: 'absolute', tooltipSpace: view => view.dom.getBoundingClientRect() }),
        EditorView.domEventHandlers({
          click(event) {
            if (!(event.target instanceof Element)) return;
            const mark = event.target.closest<HTMLElement>('[data-annotation-index]');
            if (mark) setSelected(callbacks.current.annotations[Number(mark.dataset.annotationIndex)]);
          },
        }),
        EditorState.changeFilter.of(transaction => !transaction.docChanged
          || Array.from(transaction.newDoc.toString()).length <= 20000),
        editable.current.of([EditorView.editable.of(false), EditorState.readOnly.of(true)]),
        EditorView.contentAttributes.of({ 'aria-label': 'Your response text' }),
        EditorView.updateListener.of(update => {
          if (update.docChanged) callbacks.current.onEdit(update.state.doc.toString());
        })],
    });
    view.current = editor;
    return () => { editor.destroy(); view.current = null; };
  }, []);

  useEffect(() => {
    view.current?.dispatch({ effects: editable.current.reconfigure([
      EditorView.editable.of(!disabled), EditorState.readOnly.of(disabled),
    ]) });
  }, [disabled]);

  useEffect(() => {
    const editor = view.current;
    if (!editor) return;
    const text = editor.state.doc.toString();
    const decorations = annotations.flatMap((item, index) => {
      const range = annotationRange(text, item);
      return range ? [Decoration.mark({
        class: 'annotation annotation-' + item.category,
        attributes: { 'data-annotation-index': String(index) },
      }).range(range.from, range.to)] : [];
    });
    const range = active && annotationRange(text, active);
    const tooltip: Tooltip | null = active && range ? {
      pos: range.from, end: range.to,
      create(view) {
        const doc = view.dom.ownerDocument;
        const dom = doc.createElement('section');
        dom.className = 'annotation-popup';
        dom.setAttribute('aria-label', 'Tutor annotation');
        const heading = doc.createElement('strong');
        heading.textContent = active.category.replaceAll('_', ' ') + ' · Line ' + active.line;
        const close = doc.createElement('button');
        close.type = 'button';
        close.className = 'annotation-close';
        close.textContent = '×';
        close.setAttribute('aria-label', 'Dismiss annotation');
        close.onclick = () => {
          callbacks.current.onDismiss(active);
          view.focus();
        };
        const reason = doc.createElement('p');
        reason.textContent = active.reason;
        const ask = doc.createElement('button');
        ask.type = 'button';
        ask.className = 'annotation-ask';
        ask.textContent = 'Ask about annotation';
        ask.disabled = !canAsk;
        ask.onclick = () => callbacks.current.onAsk(active);
        dom.append(heading, close, reason, ask);
        return { dom };
      },
    } : null;
    editor.dispatch({ effects: setReview.of({ marks: Decoration.set(decorations, true), tooltip }) });
  }, [active, annotations, canAsk]);

  useEffect(() => {
    const editor = view.current;
    if (!editor || !active) return;
    const range = annotationRange(editor.state.doc.toString(), active);
    if (range) editor.dispatch({ effects: EditorView.scrollIntoView(range.from, { y: 'nearest' }) });
  }, [active]);

  return <div id="workspace" ref={host} />;
}

