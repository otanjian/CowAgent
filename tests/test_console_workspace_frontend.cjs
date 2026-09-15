// Verify the console workspace panel degrades readably when a request fails.
// Before this change the panel's routes were `closed` in database identity mode,
// so every tree/search/preview call answered `503 database_unavailable` and the
// panel rendered the raw backend string (or nothing at all). The panel must
// instead show a localized reason for the 503/403 cases and keep the backend
// message otherwise.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(
    path.join(__dirname, '../channel/web/static/js/workspace.js'), 'utf8');

function element() {
    return { innerHTML: '', value: '', classList: { add() {}, remove() {}, toggle() {}, contains: () => false } };
}

function makeCtx(payload) {
    const nodes = new Map();
    let lastRequest = '';
    const ctx = {
        wsPanelOpen: false,
        wsCurrentDir: '',
        wsCurrentRoot: '',
        wsSearchMode: false,
        currentLang: 'zh',
        t: key => key, // identity; assert on the raw key so it is locale-agnostic
        escapeHtml: x => String(x),
        window: { addEventListener() {}, removeEventListener() {} },
        document: {
            getElementById: id => nodes.get(id) || null,
            querySelector: () => null,
            querySelectorAll: () => [],
            addEventListener() {},
            removeEventListener() {},
        },
        fetch: async (url) => {
            lastRequest = url;
            return { ok: payload.status === 'success' ? true : false, status: payload.http || 200,
                json: async () => payload };
        },
    };
    vm.createContext(ctx);
    // Everything before the "Init" section: definitions and literals only, so no
    // DOM wire-up runs and the functions can be called directly.
    const initIdx = source.indexOf('// Init');
    assert.ok(initIdx > 0, 'Init section not found');
    const cut = source.lastIndexOf('// =====', initIdx);
    vm.runInContext(source.slice(0, cut), ctx);
    return { ctx, nodes, lastRequest: () => lastRequest };
}

test('wsErrorMessage localizes the database-unavailable 503', () => {
    const { ctx } = makeCtx({ status: 'success' });
    assert.equal(ctx.wsErrorMessage({ status: 503, code: 'database_unavailable',
        message: 'unavailable in database identity mode' }), 'ws_unavailable');
});

test('wsErrorMessage localizes forbidden 403 and keeps other messages', () => {
    const { ctx } = makeCtx({ status: 'success' });
    assert.equal(ctx.wsErrorMessage({ status: 403, code: 'forbidden', message: 'forbidden' }),
        'ws_forbidden');
    assert.equal(ctx.wsErrorMessage({ status: 404, code: 'not_found', message: 'boom' }), 'boom');
});

test('a database-unavailable tree renders the readable label, not the raw reason', async () => {
    const { ctx, nodes } = makeCtx({ status: 'error', code: 'database_unavailable', http: 503,
        message: 'unavailable in database identity mode' });
    nodes.set('ws-file-list', element());
    await ctx.loadWorkspaceDir('');
    const html = nodes.get('ws-file-list').innerHTML;
    assert.match(html, /ws_unavailable/);
    assert.doesNotMatch(html, /database_unavailable/);
    assert.doesNotMatch(html, /unavailable in database identity mode/);
});

test('a forbidden search renders the readable label', async () => {
    const { ctx, nodes } = makeCtx({ status: 'error', code: 'forbidden', http: 403,
        message: 'forbidden' });
    nodes.set('ws-file-list', element());
    await ctx.runWorkspaceSearch('report');
    const html = nodes.get('ws-file-list').innerHTML;
    assert.match(html, /ws_forbidden/);
    assert.ok(!html.includes('<span>forbidden</span>'), 'raw backend reason is not shown');
});

test('a successful tree still renders entries', async () => {
    const { ctx, nodes } = makeCtx({ status: 'success', path: '', root: '/ws/t1',
        entries: [{ name: 'a.txt', path: 'a.txt', is_dir: false, size: 3, kind: 'text' }] });
    nodes.set('ws-file-list', element());
    await ctx.loadWorkspaceDir('');
    const html = nodes.get('ws-file-list').innerHTML;
    assert.match(html, /a\.txt/);
});
