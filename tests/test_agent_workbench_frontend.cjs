// Exercise the shipped console functions with controlled transport and DOM.
// Browser acceptance separately verifies layout; these cases cover races that
// source-string assertions and a happy-path screenshot cannot detect.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../channel/web/static/js/console.js'), 'utf8');
function section(start, end) {
    const from = source.indexOf(start);
    const to = source.indexOf(end, from);
    assert.ok(from >= 0 && to > from, `Missing console section ${start}`);
    return source.slice(from, to);
}
function element() {
    const classes = new Set(['opacity-0']);
    return {
        textContent: '', innerHTML: '', dataset: {}, attrs: {},
        classList: {
            add: (...xs) => xs.forEach(x => classes.add(x)),
            remove: (...xs) => xs.forEach(x => classes.delete(x)),
            contains: x => classes.has(x),
            toggle: (x, on) => on ? classes.add(x) : classes.delete(x),
        },
        setAttribute(k, v) { this.attrs[k] = v; },
        removeAttribute(k) { delete this.attrs[k]; },
        querySelector: () => null,
        focus() { this.focused = true; },
    };
}
const agent = (id, extra = {}) => ({ id, name: id, avatar: null, description: '',
    is_default: false, can_chat: true, unavailable_reason: null, ...extra });
const response = agents => ({ ok: true, json: async () => ({ status: 'success', agents }) });
function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}

function setup(fetchImpl = async () => response([agent('B')])) {
    const nodes = new Map();
    const events = [];
    const storage = new Map();
    const ctx = {
        console, Date, activeAgentId: 'A', sessionId: 'old-session', currentView: 'agent-workbench',
        agentNavigationVersion: 0, defaultAgentId: 'A', avatarVersions: {},
        agentCatalog: [{ ...agent('A'), enabled: true }],
        _identityMode: () => 'legacy', sessionStorage: { getItem: k => storage.get(k) || '' },
        localStorage: { setItem: (k, v) => storage.set(k, v) },
        writeScopedPreference: (k, v) => storage.set(k, v),
        document: { getElementById(id) { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); } },
        t: key => key, escapeHtml: value => String(value),
        agentAvatarHTML: a => `<avatar>${a.name}</avatar>`,
        fetch: (...args) => { events.push(['fetch', args[0]]); return fetchImpl(...args); },
        wsGuardUnsaved: () => true,
        newChat: (...args) => { events.push(['newChat', ...args]); ctx.sessionId = 'new-session'; },
        resetWorkspaceToAgentRoot: () => events.push(['workspace-root']),
        requestAnimationFrame: fn => fn(), multiAgentMode: () => true,
        currentTeamIds: () => [], conversationHasMessages: () => false,
        _renderPermissionChip: () => {}, _renderModelChip: () => {}, _wsSelUpdateLabel: () => {},
        showConfirmDialog: options => events.push(['notice', options.message]),
    };
    ctx.findAgent = id => ctx.agentCatalog.find(a => a.id === id);
    ctx.navigateTo = view => { ctx.currentView = view; ctx.agentNavigationVersion++; ctx.cancelAgentStart(); };
    vm.createContext(ctx);
    vm.runInContext(section('let agentWorkbench =', 'function openAgentDetail'), ctx);
    vm.runInContext(section('let _agentStartInFlight =', 'function conversationHasMessages'), ctx);
    vm.runInContext(section('function renderComposerIdentity()', 'function toggleComposerAgentMenu'), ctx);
    vm.runInContext(section('function pickComposerAgent(', 'function inviteTeamMember('), ctx);
    vm.runInContext(section('async function refreshSessionSettings()', 'function _renderPermissionChip'), ctx);
    vm.runInContext(section('async function refreshWorkspaceSelector()', '// Sync the selector button'), ctx);
    vm.runInContext(`let _sessCfg = null; let _wsSelState = {};
        this.state = () => ({agents: agentWorkbench, loading: agentWorkbenchLoading,
            error: _wbLoadedError, notice: _wbNoticeKey, busy: !!_agentStartInFlight,
            settings: _sessCfg, workspace: _wsSelState});`, ctx);
    return { ctx, nodes, events, storage, node: id => ctx.document.getElementById(id) };
}

test('fresh workbench Agent starts even when absent from management cache', async () => {
    const { ctx, events, node } = setup(async () => response([agent('B', { name: 'New name' })]));
    await ctx.startChatWithAgent('B');
    assert.equal(ctx.activeAgentId, 'B');
    assert.equal(ctx.currentView, 'chat');
    assert.equal(events.filter(e => e[0] === 'newChat').length, 1);
    assert.deepEqual(events.find(e => e[0] === 'newChat'), ['newChat', true, false]);
    assert.equal(node('chat-agent-name').textContent, 'New name');
    assert.equal(node('chat-input').focused, true);
});

test('removed target is rejected despite stale enabled management entry', async () => {
    const { ctx, events, node } = setup(async () => response([]));
    ctx.agentCatalog.push({ ...agent('B'), enabled: true });
    await ctx.startChatWithAgent('B');
    assert.equal(ctx.activeAgentId, 'A');
    assert.equal(ctx.sessionId, 'old-session');
    assert.equal(events.filter(e => e[0] === 'newChat').length, 0);
    assert.equal(node('agent-workbench-status').textContent, 'agent_target_unavailable');
    assert.equal(node('agent-workbench-status').classList.contains('opacity-0'), false);
});

test('runtime-disabled target cannot launch and explains why', async () => {
    const { ctx, events, node } = setup(async () => response([agent('B', {
        can_chat: false, unavailable_reason: 'runtime_not_enabled',
    })]));
    await ctx.startChatWithAgent('B');
    assert.equal(events.filter(e => e[0] === 'newChat').length, 0);
    assert.match(node('agent-workbench-grid').innerHTML, /disabled aria-disabled="true"/);
    assert.match(node('agent-workbench-grid').innerHTML, /agent_runtime_not_enabled/);
});

test('double-click while validation is pending submits one session', async () => {
    const request = deferred();
    const { ctx, events } = setup(() => request.promise);
    const first = ctx.startChatWithAgent('B');
    await ctx.startChatWithAgent('B');
    assert.equal(events.filter(e => e[0] === 'fetch').length, 1);
    request.resolve(response([agent('B')]));
    await first;
    assert.equal(events.filter(e => e[0] === 'newChat').length, 1);
    assert.equal(ctx.state().busy, false);
});

test('navigation away and back discards a late start result', async () => {
    const request = deferred();
    const { ctx, events } = setup(() => request.promise);
    const start = ctx.startChatWithAgent('B');
    ctx.navigateTo('config');
    ctx.navigateTo('agent-workbench');
    request.resolve(response([agent('B')]));
    await start;
    assert.equal(ctx.activeAgentId, 'A');
    assert.equal(events.filter(e => e[0] === 'newChat').length, 0);
});

test('cancel before validation preserves all state', async () => {
    const { ctx, events } = setup();
    ctx.wsGuardUnsaved = () => false;
    await ctx.startChatWithAgent('B');
    assert.equal(events.length, 0);
    assert.equal(ctx.activeAgentId, 'A');
});

test('content becomes dirty while validating: do not partially switch', async () => {
    const request = deferred();
    const { ctx, events } = setup(() => request.promise);
    const start = ctx.startChatWithAgent('B');
    ctx.wsGuardUnsaved = () => false;
    request.resolve(response([agent('B')]));
    await start;
    assert.equal(ctx.activeAgentId, 'A');
    assert.equal(ctx.sessionId, 'old-session');
    assert.equal(events.filter(e => e[0] === 'newChat').length, 0);
});

test('network failure keeps context and allows retry', async () => {
    let failed = true;
    const { ctx, node } = setup(async () => { if (failed) throw Error('offline'); return response([agent('B')]); });
    await ctx.startChatWithAgent('B');
    assert.equal(ctx.activeAgentId, 'A');
    assert.equal(ctx.state().busy, false);
    assert.equal(node('agent-workbench-status').textContent, 'agent_start_failed');
    failed = false;
    await ctx.startChatWithAgent('B');
    assert.equal(ctx.activeAgentId, 'B');
});

test('newer list wins over an older response without changing owner', async () => {
    const a = deferred(), b = deferred(); let calls = 0;
    const { ctx } = setup(() => (++calls === 1 ? a.promise : b.promise));
    const first = ctx.loadAgentWorkbench();
    const second = ctx.loadAgentWorkbench(true);
    b.resolve(response([agent('new')])); await second;
    a.resolve(response([agent('old')])); await first;
    assert.equal(ctx.state().agents[0].id, 'new');
    assert.equal(ctx.activeAgentId, 'A');
});

test('tenant switch discards list response', async () => {
    const request = deferred();
    const { ctx, storage } = setup(() => request.promise);
    storage.set('cow_tenant_id', 'one');
    const load = ctx.loadAgentWorkbench();
    storage.set('cow_tenant_id', 'two');
    request.resolve(response([agent('private')])); await load;
    assert.equal(ctx.state().agents.length, 0);
});

test('empty state is visible and full management fallback is rejected', async () => {
    const { ctx, node } = setup(async () => response([]));
    await ctx.loadAgentWorkbench();
    assert.equal(node('agent-workbench-status').textContent, 'agent_workbench_empty');
    assert.equal(node('agent-workbench-status').classList.contains('opacity-0'), false);
    ctx.fetch = async () => ({ ok: true, json: async () => ({ status: 'success', agents: [], revision: 'old' }) });
    await ctx.loadAgentWorkbench();
    assert.equal(ctx.state().error, true);
    assert.match(node('agent-workbench-grid').innerHTML, /agent_workbench_retry/);
});

test('default card leads even when its ID sorts last', async () => {
    const { ctx } = setup(async () => response([agent('aaa'), agent('zzz', { is_default: true })]));
    await ctx.loadAgentWorkbench();
    assert.equal(ctx.state().agents[0].id, 'zzz');
});

test('chat header follows composer changes, including single-Agent mode', () => {
    const { ctx, node } = setup();
    ctx.agentCatalog.push({ ...agent('B'), enabled: true });
    ctx.activeAgentId = 'B'; ctx.renderComposerIdentity();
    assert.equal(node('chat-agent-name').textContent, 'B');
    ctx.multiAgentMode = () => false;
    ctx.activeAgentId = 'A'; ctx.renderComposerIdentity();
    assert.equal(node('chat-agent-name').textContent, 'A');
    assert.equal(node('composer-identity').classList.contains('hidden'), true);
});

test('composer switch shares cancellation and fresh target validation', async () => {
    const { ctx, events } = setup(async () => response([]));
    ctx.currentView = 'chat';
    ctx.wsGuardUnsaved = () => false;
    await ctx.pickComposerAgent('B');
    assert.equal(ctx.activeAgentId, 'A');
    assert.equal(events.length, 0);
    ctx.wsGuardUnsaved = () => true;
    await ctx.pickComposerAgent('B');
    assert.equal(ctx.activeAgentId, 'A');
    assert.equal(ctx.sessionId, 'old-session');
    assert.ok(events.some(e => e[0] === 'notice' && e[1] === 'agent_target_unavailable'));
});

test('old session model and project responses cannot overwrite new context', async () => {
    const request = deferred();
    const { ctx } = setup(() => request.promise);
    const settings = ctx.refreshSessionSettings();
    const workspace = ctx.refreshWorkspaceSelector();
    ctx.activeAgentId = 'B'; ctx.sessionId = 'new-session';
    request.resolve({ ok: true, json: async () => ({ status: 'success', model: { id: 'old' }, current: { name: 'old-project' } }) });
    await Promise.all([settings, workspace]);
    assert.equal(ctx.state().settings, null);
    assert.equal(ctx.state().workspace.current, undefined);
});
