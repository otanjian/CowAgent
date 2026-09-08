// Run the complete shipped admin script. The DOM fixture only models the
// generated modal controls; browser acceptance owns the visual layout.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../channel/web/static/js/identity-admin.js'), 'utf8');
const catalog = ['tenant.info.read', 'tenant.members.read', 'tenant.org.read'];
const role = {
    id: 'role-reviewer', code: 'reviewer', name: 'Organization reviewer',
    version: 7, builtin: false, permissions: ['tenant.info.read', 'tenant.org.read'],
};

function eventTarget() {
    const listeners = new Map();
    return {
        addEventListener(type, fn) {
            if (!listeners.has(type)) listeners.set(type, []);
            listeners.get(type).push(fn);
        },
        dispatch(type) {
            for (const fn of listeners.get(type) || []) fn({ target: this, type });
        },
    };
}

function element(tag = 'div') {
    const classes = new Set();
    let html = '';
    const el = {
        ...eventTarget(), tagName: tag.toLowerCase(), id: '', value: '', type: '',
        children: [], style: {}, checked: false, disabled: false, textContent: '',
        get className() { return [...classes].join(' '); },
        set className(value) {
            classes.clear(); String(value).split(/\s+/).filter(Boolean).forEach(c => classes.add(c));
        },
        classList: {
            add: (...names) => names.forEach(c => classes.add(c)),
            remove: (...names) => names.forEach(c => classes.delete(c)),
            contains: name => classes.has(name),
        },
        appendChild(child) { this.children.push(child); return child; },
        focus() {},
        matches(selector) {
            if (selector.startsWith('#')) return this.id === selector.slice(1);
            if (selector.startsWith('.')) return classes.has(selector.slice(1));
            if (selector === 'input:checked') return this.tagName === 'input' && this.checked;
            if (selector === 'input[type=checkbox]') return this.tagName === 'input' && this.type === 'checkbox';
            return this.tagName === selector;
        },
        querySelectorAll(selector) {
            const selectors = selector.split(',').map(s => s.trim());
            return this.children.flatMap(child => [
                ...(selectors.some(s => child.matches(s)) ? [child] : []),
                ...child.querySelectorAll(selector),
            ]);
        },
        querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
        get innerHTML() { return html; },
        set innerHTML(value) {
            html = String(value);
            this.children = [];
            // Parse the script's generated elements so checked values and form
            // submission use its actual HTML, not test-authored control state.
            const stack = [this];
            for (const match of html.matchAll(/<\/?([a-z][a-z0-9-]*)\b([^>]*)>/gi)) {
                if (match[0].startsWith('</')) { stack.pop(); continue; }
                const child = element(match[1]);
                for (const attr of match[2].matchAll(/([\w-]+)(?:="([^"]*)")?/g)) {
                    const [, name, content = ''] = attr;
                    if (name === 'class') child.className = content;
                    else if (['id', 'value', 'type'].includes(name)) child[name] = content;
                    else if (['checked', 'disabled'].includes(name)) child[name] = true;
                }
                stack[stack.length - 1].appendChild(child);
                if (!['input', 'br', 'hr', 'img', 'meta', 'link'].includes(child.tagName)) stack.push(child);
            }
        },
    };
    return el;
}

function response(data, status = 200) {
    return { ok: status >= 200 && status < 300, status, json: async () => data };
}

function setup(permissionResponse = () => response({ status: 'success', permissions: catalog })) {
    const calls = [];
    const document = { ...eventTarget(), body: element('body'), createElement: element };
    document.getElementById = id => document.body.querySelector('#' + id);
    for (const id of ['role-list', 'role-status', 'role-create-btn']) {
        const el = element(id.endsWith('-btn') ? 'button' : 'div');
        el.id = id;
        document.body.appendChild(el);
    }
    const ctx = {
        document, console,
        sessionStorage: { getItem: () => 'test-tenant' },
        setTimeout() {},
        confirm: () => true,
        fetch: async (url, options) => {
            calls.push({ url, options });
            if (url === '/api/tenant/permissions') return permissionResponse();
            if (url === '/api/tenant/roles') return response({ status: 'success', items: [role] });
            if (url === '/api/tenant/roles/' + role.id) return response({ status: 'success' });
            throw Error('Unexpected request: ' + url);
        },
    };
    ctx.window = ctx;
    vm.runInNewContext(source, ctx, { filename: 'identity-admin.js' });
    document.dispatch('DOMContentLoaded');
    return {
        ctx, calls, node: id => document.getElementById(id),
        permissions: () => document.getElementById('adm-fld-permissions')?.querySelectorAll('input[type=checkbox]') || [],
        modalOpen: () => {
            const modal = document.getElementById('admin-modal');
            return !!modal && !modal.classList.contains('hidden');
        },
        catalogRequests: () => calls.filter(call => call.url === '/api/tenant/permissions'),
    };
}

const settle = () => new Promise(resolve => setImmediate(resolve));

async function editRole(h) {
    await h.ctx.loadRolesView();
    h.ctx.adminRowAction('role', 'edit', role.id);
    await settle();
}

test('editing renders the permission catalog, preselects existing grants, and preserves them on save', async () => {
    const h = setup();
    await editRole(h);
    assert.equal(h.modalOpen(), true);
    assert.deepEqual(h.permissions().map(p => p.value), catalog);
    assert.deepEqual(h.permissions().filter(p => p.checked).map(p => p.value), role.permissions);
    assert.equal(h.catalogRequests()[0].options.headers['X-Tenant-ID'], 'test-tenant');
    h.node('admin-modal-submit').dispatch('click');
    await settle();
    const saved = h.calls.find(call => call.url === '/api/tenant/roles/' + role.id);
    assert.deepEqual(JSON.parse(saved.options.body), {
        name: role.name, permissions: role.permissions, expected_version: role.version,
    });
    assert.equal(h.modalOpen(), false);
    await editRole(h);
    assert.equal(h.catalogRequests().length, 1, 'successful catalog is reused');
});

const unavailableCatalogs = [
    ['HTTP 500', () => response({ status: 'error', message: 'catalog unavailable' }, 500)],
    ['empty catalog', () => response({ status: 'success', permissions: [] })],
    ['missing catalog', () => response({ status: 'success' })],
    ['non-array catalog', () => response({ status: 'success', permissions: 'tenant.org.read' })],
    ['invalid catalog entries', () => response({ status: 'success', permissions: ['tenant.org.read', null] })],
    ['blank permission', () => response({ status: 'success', permissions: ['   '] })],
    ['invalid JSON', () => ({ ok: true, status: 200, json: async () => { throw Error('Invalid JSON'); } })],
];

for (const [label, unavailable] of unavailableCatalogs) {
    test(`${label} blocks editing and can be retried without retaining an empty catalog`, async () => {
        let ready = false;
        const h = setup(() => ready ? response({ status: 'success', permissions: catalog }) : unavailable());
        await editRole(h);
        assert.equal(h.modalOpen(), false, 'must not expose a saveable role form without permission choices');
        assert.ok(h.node('role-status').textContent, 'show an actionable load error');
        assert.equal(h.node('role-status').classList.contains('opacity-0'), false);
        assert.equal(h.node('role-status').style.color, '#ef4444');
        assert.equal(h.calls.some(call => call.options.method !== 'GET'), false);
        ready = true;
        h.ctx.adminRowAction('role', 'edit', role.id);
        await settle();
        assert.equal(h.modalOpen(), true);
        assert.deepEqual(h.permissions().filter(p => p.checked).map(p => p.value), role.permissions);
        assert.equal(h.catalogRequests().length, 2, 'retry fetches the catalog again');
    });
}

test('new-role button blocks an unavailable catalog and renders choices after retry', async () => {
    let ready = false;
    const h = setup(() => response({ status: 'success', permissions: ready ? catalog : [] }));
    await h.ctx.loadRolesView();
    h.node('role-create-btn').dispatch('click');
    await settle();
    assert.equal(h.modalOpen(), false);
    assert.ok(h.node('role-status').textContent);
    ready = true;
    h.node('role-create-btn').dispatch('click');
    await settle();
    assert.equal(h.modalOpen(), true);
    assert.deepEqual(h.permissions().map(p => p.value), catalog);
    assert.equal(h.permissions().some(p => p.checked), false);
    assert.equal(h.node('admin-modal-title').textContent, 'role_create');
    assert.equal(h.catalogRequests().length, 2);
});
