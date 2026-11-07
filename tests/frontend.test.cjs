const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { SourceTextModule } = require('node:vm');
const { JSDOM } = require('jsdom');

const build = path.join(__dirname, '..', 'frontend', 'dist');
const html = fs.readFileSync(path.join(build, 'index.html'), 'utf8');
const script = fs.readFileSync(path.join(build, html.match(/src="\/(assets\/[^"]+\.js)"/)[1]), 'utf8');
const workspace = '😀 left = mid + 1';
const study = {
  type: 'study_state',
  state: { material_id: 'm1', current_concept: 'Binary Search', workspace,
    observer_status: { revision: 1 }, current_problem: { id: 'p1', prompt: 'Find the first 2.' } },
  material: { id: 'm1', name: 'course.md', chunks: [] },
  graph: { nodes: [], edges: [] }, mastery_target: 0.75,
};

async function until(predicate) {
  for (let attempt = 0; attempt < 100; attempt++) {
    const result = predicate();
    if (result) return result;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  throw Error('Expected UI update did not occur');
}

async function setup(t) {
  const dom = new JSDOM(html, { url: 'http://localhost:8000', runScripts: 'outside-only', pretendToBeVisual: true });
  t.after(() => dom.window.close());
  const w = dom.window;
  const messages = [];
  let socket;
  w.localStorage.setItem('studyroom-auto-session', '1');
  w.fetch = async url => ({ ok: true, json: async () => url.endsWith('/study') ? study : { history: [] } });
  w.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
  w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  w.Range.prototype.getClientRects = () => [];
  w.Range.prototype.getBoundingClientRect = () => new w.DOMRect();
  w.WebSocket = class {
    static OPEN = 1;
    readyState = 1;
    constructor() { socket = this; queueMicrotask(() => this.onopen()); }
    send(raw) { messages.push(JSON.parse(raw)); }
    close() {}
  };
  const entry = new SourceTextModule(script, { context: dom.getInternalVMContext() });
  await entry.link(() => { throw Error('Unexpected static import in the built entry'); });
  await entry.evaluate();
  const document = w.document;
  await until(() => document.querySelector('#submit:not(:disabled)'));
  return { document, messages, dispatch: data => socket.onmessage({ data: JSON.stringify(data) }) };
}

test('annotations decorate exact Unicode-aware ranges and dismissal preserves student text', async t => {
  const { document, dispatch } = await setup(t);
  dispatch({ type: 'intervention', event_id: 1, revision: 1, result: {
    type: 'highlight', content: 'Review the interval.',
    metadata: { annotations: [
      { line: 1, start: 2, end: 6, quote: 'left', reason: 'Check the boundary.', category: 'possible_error' },
      { line: 1, start: 9, end: 12, quote: 'mid', reason: 'Explain this choice.', category: 'missing_justification' },
    ] },
  } });
  await until(() => document.querySelectorAll('.cm-content .annotation').length === 2);
  assert.equal(document.querySelector('.cm-content .annotation').textContent, 'left');
  const dismiss = await until(() => document.querySelector('#workspace [aria-label="Dismiss annotation"]'));
  dismiss.click();
  await until(() => document.querySelectorAll('.cm-content .annotation').length === 1);
  assert.equal(document.querySelector('.cm-line').textContent, workspace);
});

test('binary search playback shows interval, controls and explanations', async t => {
  const { document, dispatch } = await setup(t);
  dispatch({ type: 'intervention', event_id: 2, revision: 1, result: {
    type: 'visualization', content: 'Find the lower bound.', metadata: { visualization: {
      kind: 'binary_search', title: 'Search trace', array: [2], target: 2,
      steps: [
        { left: 0, right: 1, mid: 0, comparison: 'equal', explanation: 'Found a match.' },
        { left: 0, right: 0, mid: null, comparison: 'complete', explanation: 'Finish at index 0.' },
      ],
    } },
  } });
  const player = await until(() => document.querySelector('.visual-player'));
  const button = name => [...player.querySelectorAll('button')].find(item => item.textContent === name);
  assert.match(player.textContent, /Interval \[0, 1\)/);
  button('Next').click();
  await until(() => player.textContent.includes('Step 2 of 2'));
  button('Previous').click();
  await until(() => player.textContent.includes('Step 1 of 2'));
  button('Play').click();
  await until(() => player.textContent.includes('Step 2 of 2'));
  button('Restart').click();
  await until(() => player.textContent.includes('Step 1 of 2'));
});

test('manual tools still use the existing WebSocket request protocol', async t => {
  const { document, messages } = await setup(t);
  document.querySelector('[data-tool="hint"]').click();
  assert.deepEqual(messages[0], { type: 'student_action', action: 'hint_request', tool: 'hint' });
});

