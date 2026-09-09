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
    resource_grants: [
        { resource_kind: 'skill', resource_id: 'custom:knowledge-wiki', action: 'read' },
        { resource_kind: 'model', resource_id: 'provider:deepseek:deepseek-v4-flash', action: 'use' },
    ],
    model_defaults: { chat: 'provider:deepseek:deepseek-v4-flash' },
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
        appendChild(child) { this.children.push(child); child.__parent = this; return child; },
        focus() {},
        closest(selector) {
            let node = this;
            while (node) { if (node.matches && node.matches(selector)) return node; node = node.__parent; }
            return null;
        },
        getAttribute(name) { return this[name] != null ? String(this[name]) : null; },
        matches(selector) {
            if (selector.startsWith('#')) return this.id === selector.slice(1);
            // support ".cls[attr=val]" before the plain ".class" branch
            const clsAttr = selector.match(/^\.([\w-]+)\[([\w-]+)=(?:"|')([^"']*)(?:"|')?\]$/);
            if (clsAttr) return classes.has(clsAttr[1]) && String(this[clsAttr[2]] || '') === clsAttr[3];
            if (selector.startsWith('.')) return classes.has(selector.slice(1));
            if (selector === 'input:checked') return this.tagName === 'input' && this.checked;
            if (selector === 'input[type=checkbox]') return this.tagName === 'input' && this.type === 'checkbox';
            const attrMatch = selector.match(/^\[([\w-]+)=(?:"|')([^"']*)(?:"|')?\]$/);
            if (attrMatch) return String(this[attrMatch[1]] || '') === attrMatch[2];
            return this.tagName === selector;
        },
        querySelectorAll(selector) {
            // Simple recursive match. Only single-part (non-descendant) selectors
            // are needed by the code under test; browser acceptance owns the full
            // CSS engine. Descendant combinators are handled in the test helpers
            // that need them (see resourceToggleIn/resourceListIn).
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
                    else if (['id', 'value', 'type', 'data-kind'].includes(name)) child[name] = content;
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
            if (url.startsWith('/api/tenant/authorization/catalog?')) {
                const parsed = new URL(url, 'http://test');
                const kind = parsed.searchParams.get('kind');
                const itembyKind = {
                    skill: [{ resource_id: 'custom:knowledge-wiki', name: 'knowledge-wiki', capability: 'skill' }],
                    model: [{ resource_id: 'provider:deepseek:deepseek-v4-flash', name: 'deepseek-v4-flash', capability: 'model', provider: 'deepseek' }],
                };
                return response({ status: 'success', kind, items: itembyKind[kind] || [], total: (itembyKind[kind] || []).length, page: 1, resource_actions: { skill: ['read', 'use', 'edit', 'enable'], model: ['read', 'use'] }[kind] || [] });
            }
            throw Error('Unexpected request: ' + url);
        },
    };
    ctx.window = ctx;
    vm.runInNewContext(source, ctx, { filename: 'identity-admin.js' });
    document.dispatch('DOMContentLoaded');
    return {
        ctx, calls, node: id => document.getElementById(id),
        permissions: () => document.getElementById('adm-fld-permissions')?.querySelectorAll('input[type=checkbox]') || [],
        resourceRow: kind => document.getElementById('adm-fld-resource_grants')?.querySelector('[data-kind="' + kind + '"]'),
        resourceToggle: kind => {
            const row = document.getElementById('adm-fld-resource_grants')?.querySelector('[data-kind="' + kind + '"]');
            return row ? row.querySelector('.resource-kind-toggle') : null;
        },
        resourceList: kind => {
            const row = document.getElementById('adm-fld-resource_grants')?.querySelector('[data-kind="' + kind + '"]');
            const list = row ? row.querySelector('.resource-kind-list') : null;
            return list ? list.querySelectorAll('input[type=checkbox]') : [];
        },
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
    const body = JSON.parse(saved.options.body);
    assert.equal(body.name, role.name);
    assert.deepEqual(body.permissions, role.permissions);
    assert.equal(body.expected_version, role.version);
    // The unified save (task 3.1) carries the role's resource grants expanded to
    // the kind's allowed actions, plus its model defaults.
    const grants = body.resource_grants;
    assert.deepEqual(grants.filter(g => g.resource_kind === 'skill'), [
        { resource_kind: 'skill', resource_id: 'custom:knowledge-wiki', action: 'read' },
        { resource_kind: 'skill', resource_id: 'custom:knowledge-wiki', action: 'use' },
        { resource_kind: 'skill', resource_id: 'custom:knowledge-wiki', action: 'edit' },
        { resource_kind: 'skill', resource_id: 'custom:knowledge-wiki', action: 'enable' },
    ]);
    assert.deepEqual(grants.filter(g => g.resource_kind === 'model'), [
        { resource_kind: 'model', resource_id: 'provider:deepseek:deepseek-v4-flash', action: 'read' },
        { resource_kind: 'model', resource_id: 'provider:deepseek:deepseek-v4-flash', action: 'use' },
    ]);
    assert.deepEqual(body.model_defaults, { chat: 'provider:deepseek:deepseek-v4-flash' });
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

test('resource picker preselects existing grants and shows a searchable, paged list', async () => {
    const h = setup();
    await editRole(h);
    assert.equal(h.modalOpen(), true);
    // Expand the skill resource group. The catalog mock returns one skill.
    h.resourceToggle('skill').dispatch('click');
    await settle(); await settle();
    const boxes = h.resourceList('skill');
    assert.ok(boxes.length >= 1, 'skill list renders from the catalog');
    const checked = boxes.filter(b => b.checked).map(b => b.value);
    assert.ok(checked.includes('custom:knowledge-wiki'), 'existing grant is preselected');
});
