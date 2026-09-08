// Exercise the shipped TODO script through its public view entry points.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../channel/web/static/js/todos.js'), 'utf8');
const messages = {
    todo_load_failed: '加载失败，请稍后重试。',
    todo_empty_open: '暂无未完成的待办',
    todo_disabled_banner: '待办功能未开启，可在配置中启用 todo_enabled。',
    todo_unauthorized_banner: '请先登录后再使用待办功能。',
};

function element() {
    const classes = new Set();
    return {
        textContent: '', innerHTML: '',
        get className() { return [...classes].join(' '); },
        set className(value) {
            classes.clear();
            String(value).split(/\s+/).filter(Boolean).forEach(name => classes.add(name));
        },
        classList: {
            add: (...names) => names.forEach(name => classes.add(name)),
            remove: (...names) => names.forEach(name => classes.delete(name)),
            contains: name => classes.has(name),
        },
        addEventListener() {},
    };
}

function response(data, status = 200) {
    return { ok: status >= 200 && status < 300, status, json: async () => data };
}

function setup(responses) {
    const nodes = new Map();
    const node = id => {
        if (!nodes.has(id)) nodes.set(id, element());
        return nodes.get(id);
    };
    const requests = [];
    const ctx = {
        URLSearchParams,
        I18N: { zh: messages },
        document: {
            readyState: 'complete', getElementById: node,
            querySelectorAll: () => [], querySelector: () => null,
            addEventListener() {},
        },
        fetch: async (url, options) => {
            requests.push({ url, options });
            if (url === '/api/todos/summary') return response({ status: 'success' });
            assert.match(url, /^\/api\/todos\?/);
            assert.ok(responses.length, 'unexpected list request');
            const next = responses.shift();
            return typeof next === 'function' ? next() : next;
        },
    };
    ctx.window = ctx;
    vm.runInNewContext(source, ctx, { filename: 'todos.js' });
    return {
        ctx, node,
        listRequests: () => requests.filter(r => r.url.startsWith('/api/todos?')),
        hidden: id => node(id).classList.contains('hidden'),
    };
}

function assertFailedView(h, message = messages.todo_load_failed) {
    assert.equal(h.hidden('todo-banner'), false);
    assert.equal(h.node('todo-banner').textContent, message);
    assert.equal(h.hidden('todo-empty'), true, 'failure must not claim the list is empty');
    assert.equal(h.hidden('todo-list'), true);
    assert.equal(h.hidden('todo-pagination'), true);
}

for (const [name, failedResponse] of [
    ['storage unavailable', () => response({ status: 'error', code: 'unavailable', message: '/private/server/path' }, 503)],
    ['server error', () => response({ status: 'error', code: 'internal', message: '/private/server/path' }, 500)],
    ['legacy HTTP 200 error body', () => response({ status: 'error', code: 'unavailable', message: '/private/server/path' })],
    ['non-JSON response', () => ({ ok: true, status: 200, json: async () => { throw new SyntaxError('invalid JSON'); } })],
    ['network failure', () => { throw new TypeError('Failed to fetch'); }],
]) {
    test(`${name} shows only a localized failure and retries on revisit`, async () => {
        const h = setup([failedResponse, response({ status: 'success', items: [], total: 0 })]);
        await h.ctx.loadTodosView();
        assertFailedView(h);
        await h.ctx.loadTodosView();
        assert.equal(h.listRequests().length, 2, 'revisiting retries without a forced refresh');
        assert.equal(h.hidden('todo-banner'), true);
        assert.equal(h.hidden('todo-empty'), false);
        assert.equal(h.node('todo-empty-text').textContent, messages.todo_empty_open);
        await h.ctx.loadTodosView();
        assert.equal(h.listRequests().length, 2, 'successful loads remain cached');
    });
}

for (const [code, status, expected] of [
    ['unauthorized', 401, messages.todo_unauthorized_banner],
    ['todo_disabled', 404, messages.todo_disabled_banner],
]) {
    test(`${code} retains its localized message without an empty result`, async () => {
        const h = setup([response({ status: 'error', code, message: 'server internals' }, status)]);
        await h.ctx.loadTodosView();
        assertFailedView(h, expected);
    });
}

test('failed refresh clears stale results and pagination, then retries on revisit', async () => {
    let finishRefresh;
    const h = setup([
        response({ status: 'success', items: [{ id: 'todo-1', title: 'Existing task', status: 'pending' }], total: 21 }),
        () => new Promise(resolve => { finishRefresh = resolve; }),
        response({ status: 'success', items: [], total: 0 }),
    ]);
    await h.ctx.loadTodosView();
    assert.equal(h.hidden('todo-list'), false);
    assert.match(h.node('todo-list').innerHTML, /Existing task/);
    assert.equal(h.hidden('todo-pagination'), false);

    const refresh = h.ctx.loadTodosView(true);
    assert.equal(h.hidden('todo-pagination'), true, 'stale pagination is hidden while loading');
    finishRefresh(response({ status: 'error', code: 'unavailable' }, 503));
    await refresh;
    assertFailedView(h);
    assert.equal(h.node('todo-list').innerHTML, '');
    assert.equal(h.node('todo-pagination').innerHTML, '');

    await h.ctx.loadTodosView();
    assert.equal(h.listRequests().length, 3);
    assert.equal(h.hidden('todo-empty'), false);
});
