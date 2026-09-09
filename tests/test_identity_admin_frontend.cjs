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
            toggle(name, force) {
                const on = force === undefined ? !classes.has(name) : !!force;
                if (on) classes.add(name); else classes.delete(name);
                return on;
            },
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
                    else if (['id', 'value', 'type', 'data-kind', 'data-tab', 'data-cap', 'data-group'].includes(name)) child[name] = content;
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
    document.querySelector = sel => document.body.querySelector(sel);
    document.querySelectorAll = sel => document.body.querySelectorAll(sel);
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
        resourceTab: kind => document.querySelector('.role-editor-tab[data-tab="' + kind + '"]'),
        resourceList: kind => {
            const list = document.getElementById('role-res-list-' + kind);
            return list ? list.querySelectorAll('input[type=checkbox]') : [];
        },
        editorOpen: () => {
            const ed = document.getElementById('role-editor');
            return !!ed && !ed.classList.contains('hidden');
        },
        editorTabs: () => [...(document.querySelectorAll('.role-editor-tab') || [])].map(el => el.getAttribute('data-tab')),
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

test('editing opens the role page editor (not modal) with expected tabs and preserves grants on save', async () => {
    const h = setup();
    await editRole(h);
    assert.equal(h.editorOpen(), true);
    assert.equal(h.modalOpen(), false, 'role edit must not use the shared admin modal');
    assert.deepEqual(h.editorTabs(), ['basic', 'menu', 'skill', 'tool', 'agent', 'model']);
    assert.deepEqual(h.permissions().map(p => p.value), catalog);
    assert.deepEqual(h.permissions().filter(p => p.checked).map(p => p.value), role.permissions);
    assert.equal(h.catalogRequests()[0].options.headers['X-Tenant-ID'], 'test-tenant');
    h.node('role-editor-submit').dispatch('click');
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
    assert.equal(h.editorOpen(), false);
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
        assert.equal(h.editorOpen(), false, 'must not expose a saveable role form without permission choices');
        assert.ok(h.node('role-status').textContent, 'show an actionable load error');
        assert.equal(h.node('role-status').classList.contains('opacity-0'), false);
        assert.equal(h.node('role-status').style.color, '#ef4444');
        assert.equal(h.calls.some(call => call.options.method !== 'GET'), false);
        ready = true;
        h.ctx.adminRowAction('role', 'edit', role.id);
        await settle();
        assert.equal(h.editorOpen(), true);
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
    assert.equal(h.editorOpen(), false);
    assert.ok(h.node('role-status').textContent);
    ready = true;
    h.node('role-create-btn').dispatch('click');
    await settle();
    assert.equal(h.editorOpen(), true);
    assert.deepEqual(h.permissions().map(p => p.value), catalog);
    assert.equal(h.permissions().some(p => p.checked), false);
    assert.equal(h.node('role-editor-title').textContent, 'role_create');
    assert.equal(h.catalogRequests().length, 2);
});

test('resource tab preselects existing grants and shows a searchable list', async () => {
    const h = setup();
    await editRole(h);
    assert.equal(h.editorOpen(), true);
    const tab = h.resourceTab('skill');
    assert.ok(tab, 'skill tab exists');
    tab.dispatch('click');
    await settle(); await settle();
    const boxes = h.resourceList('skill');
    assert.ok(boxes.length >= 1, 'skill list renders from the catalog');
    const checked = boxes.filter(b => b.checked).map(b => b.value);
    assert.ok(checked.includes('custom:knowledge-wiki'), 'existing grant is preselected');
});

// ---- member ↔ tenant multi-assignment -----------------------------------

const flush = async () => { for (let i = 0; i < 8; i++) await settle(); };

function setupMembers(opts = {}) {
    const calls = [];
    const document = { ...eventTarget(), body: element('body'), createElement: element };
    document.getElementById = id => document.body.querySelector('#' + id);
    for (const id of ['member-list', 'member-status', 'member-create-btn', 'member-pagination']) {
        const el = element(id.endsWith('-btn') ? 'button' : 'div');
        el.id = id;
        document.body.appendChild(el);
    }
    const adminTenants = opts.adminTenants || [
        { id: 't1', code: 'acme', name: 'Acme', member: false },
        { id: 't2', code: 'beta', name: 'Beta', member: false },
    ];
    const member = opts.member || {
        id: 'm1', username: 'alice', display_name: 'Alice', active: true,
        role_codes: ['member'], department_id: '', position_text: '', version: 3,
        user_id: 'usr-alice',
    };
    const ctx = {
        document, console,
        sessionStorage: { getItem: () => 't1' },
        setTimeout() {},
        confirm: () => true,
        fetch: async (url, options) => {
            calls.push({ url, options });
            if (url === '/api/tenant/roles') return response({ status: 'success', items: [
                { id: 'r-member', code: 'member', name: 'Member', builtin: true },
                { id: 'r-admin', code: 'tenant_admin', name: 'Tenant Admin', builtin: true },
            ] });
            if (url === '/api/tenant/departments') return response({ status: 'success', items: [
                { id: 'd-root', code: '__root__', name: 'Root' },
            ] });
            if (url.startsWith('/api/identity/administered-tenants')) {
                const parsed = new URL(url, 'http://test');
                const target = parsed.searchParams.get('user_id');
                const items = adminTenants.map(t => {
                    const o = { id: t.id, code: t.code, name: t.name };
                    if (target) {
                        o.member = !!t.member;
                        o.member_id = t.member ? (t.member_id || ('mid-' + t.id)) : null;
                        o.member_version = t.member ? (t.member_version || 1) : null;
                    }
                    return o;
                });
                return response({ status: 'success', items });
            }
            if (url.startsWith('/api/tenant/members?')) {
                return response({ status: 'success', items: [member], total: 1 });
            }
            if (url.startsWith('/api/tenant/members/')) return response({ status: 'success' });
            if (url === '/api/tenant/members') {
                return response({ status: 'success', member: { membership_id: 'mem-new', user_id: 'usr-new' } });
            }
            throw Error('Unexpected request: ' + url);
        },
    };
    ctx.window = ctx;
    vm.runInNewContext(source, ctx, { filename: 'identity-admin.js' });
    document.dispatch('DOMContentLoaded');
    return { ctx, calls, node: id => document.getElementById(id) };
}

function memberPosts(h) {
    return h.calls.filter(c => c.url === '/api/tenant/members' && c.options && c.options.method === 'POST');
}

test('member create issues create-new for the first tenant and bind-existing for the rest', async () => {
    const h = setupMembers();
    h.node('member-create-btn').dispatch('click');
    await flush();
    assert.equal(h.node('admin-modal') && !h.node('admin-modal').classList.contains('hidden'), true);
    // Select the second tenant (t1 is pre-checked as the current tenant).
    const boxes = h.node('adm-fld-tenants').querySelectorAll('input[type=checkbox]');
    const t2 = boxes.find(b => b.value === 't2');
    assert.ok(t2, 'tenant checkbox t2 rendered');
    t2.checked = true;
    t2.dispatch('change');
    h.node('adm-fld-username').value = 'alice';
    h.node('adm-fld-display_name').value = 'Alice';
    h.node('adm-fld-temporary_password').value = 'Str0ngTempPass';
    h.node('admin-modal-submit').dispatch('click');
    await flush();
    const posts = memberPosts(h);
    assert.equal(posts.length, 2);
    const first = JSON.parse(posts[0].options.body);
    const second = JSON.parse(posts[1].options.body);
    assert.equal(first.operation, 'create-new');
    assert.equal(first.temporary_password, 'Str0ngTempPass');
    assert.equal(posts[0].options.headers['X-Tenant-ID'], 't1');
    assert.equal(second.operation, 'bind-existing');
    assert.equal(second.username, 'alice');
    assert.equal(posts[1].options.headers['X-Tenant-ID'], 't2');
});

test('member tenant edit binds a newly-selected tenant and leaves existing memberships alone', async () => {
    const h = setupMembers({
        adminTenants: [
            { id: 't1', code: 'acme', name: 'Acme', member: true, member_id: 'mid-t1', member_version: 2 },
            { id: 't2', code: 'beta', name: 'Beta', member: false },
        ],
    });
    await h.ctx.loadMembersView();
    await flush();
    h.ctx.adminRowAction('member', 'tenants', 'm1');
    await flush();
    assert.equal(h.node('admin-modal') && !h.node('admin-modal').classList.contains('hidden'), true);
    const boxes = h.node('adm-fld-tenants').querySelectorAll('input[type=checkbox]');
    assert.ok(boxes.find(b => b.value === 't1' && b.checked), 'existing t1 membership preselected');
    assert.ok(boxes.find(b => b.value === 't2' && !b.checked), 't2 not yet a member');
    boxes.find(b => b.value === 't2').checked = true;
    boxes.find(b => b.value === 't2').dispatch('change');
    h.node('admin-modal-submit').dispatch('click');
    await flush();
    const posts = memberPosts(h);
    assert.equal(posts.length, 1, 'only the newly-added tenant is written');
    const added = JSON.parse(posts[0].options.body);
    assert.equal(added.operation, 'bind-existing');
    assert.equal(posts[0].options.headers['X-Tenant-ID'], 't2');
});

test('member tenant edit deactivates a deselected tenant with its expected_version', async () => {
    const h = setupMembers({
        adminTenants: [
            { id: 't1', code: 'acme', name: 'Acme', member: true, member_id: 'mid-t1', member_version: 2 },
            { id: 't2', code: 'beta', name: 'Beta', member: true, member_id: 'mid-t2', member_version: 5 },
        ],
    });
    await h.ctx.loadMembersView();
    await flush();
    h.ctx.adminRowAction('member', 'tenants', 'm1');
    await flush();
    const boxes = h.node('adm-fld-tenants').querySelectorAll('input[type=checkbox]');
    const t2 = boxes.find(b => b.value === 't2');
    t2.checked = false;
    t2.dispatch('change');
    h.node('admin-modal-submit').dispatch('click');
    await flush();
    const posts = h.calls.filter(c =>
        c.url.startsWith('/api/tenant/members/') && c.options && c.options.method === 'POST');
    assert.equal(posts.length, 1, 'only the deselected tenant is deactivated');
    assert.ok(posts[0].url.endsWith('/mid-t2'));
    assert.equal(posts[0].options.headers['X-Tenant-ID'], 't2');
    const body = JSON.parse(posts[0].options.body);
    assert.equal(body.active, false);
    assert.equal(body.expected_version, 5);
});
