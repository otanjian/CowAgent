// Tenant archive (soft delete) UI: rows offer "delete" for an ordinary tenant
// and a "restore" action for an archived one, the status filter is forwarded to
// the list endpoint, and the delete confirmation collects the tenant code plus
// the operator password before issuing DELETE.
//
// These cases drive the real `identity-admin.js` through a minimal DOM/fetch
// harness, following tests/test_tenant_admin_account_picker.cjs.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../channel/web/static/js/identity-admin.js'), 'utf8');

function eventTarget() {
    const listeners = new Map();
    return {
        addEventListener(type, fn) {
            if (!listeners.has(type)) listeners.set(type, []);
            listeners.get(type).push(fn);
        },
        dispatch(type, event = {}) {
            for (const fn of listeners.get(type) || []) fn.call(this, { target: this, type, ...event });
        },
    };
}

function element(tag = 'div') {
    const classes = new Set();
    let html = '';
    const el = {
        ...eventTarget(), tagName: tag.toLowerCase(), id: '', value: '', type: '',
        placeholder: '', title: '', style: {}, checked: false, disabled: false,
        textContent: '', children: [],
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
        setAttribute(name, value) { this[name] = String(value); },
        matches(selector) {
            if (selector.startsWith('#')) return this.id === selector.slice(1);
            if (selector.startsWith('.')) return classes.has(selector.slice(1));
            const attr = selector.match(/^([\w-]*)\[([\w-]+)(?:=(?:"|')?([^"'\]]*)(?:"|')?)?\]$/);
            if (attr) {
                const [, tag, name, expected] = attr;
                if (tag && this.tagName !== tag.toLowerCase()) return false;
                const actual = this[name];
                return expected === undefined ? actual != null : String(actual || '') === expected;
            }
            return this.tagName === selector;
        },
        querySelectorAll(selector) {
            const selectors = String(selector).split(',').map(s => s.trim());
            const hit = node => selectors.some(s => node.matches(s));
            const out = [];
            if (hit(this)) out.push(this);
            const walk = node => {
                for (const child of node.children) {
                    if (hit(child)) out.push(child);
                    walk(child);
                }
            };
            walk(this);
            return out;
        },
        querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
        get innerHTML() { return html; },
        set innerHTML(value) {
            html = String(value);
            this.children = [];
            const stack = [this];
            for (const match of html.matchAll(/<\/?([a-z][a-z0-9-]*)\b([^>]*)>/gi)) {
                if (match[0].startsWith('</')) { stack.pop(); continue; }
                const child = element(match[1]);
                for (const attr of match[2].matchAll(/([\w-]+)(?:="([^"]*)")?/g)) {
                    const [, name, content = ''] = attr;
                    if (name === 'class') child.className = content;
                    else if (name === 'checked' || name === 'disabled') child[name] = true;
                    else child[name] = content;
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

const settle = () => new Promise(resolve => setImmediate(resolve));
const flush = async () => { for (let i = 0; i < 12; i++) await settle(); };

function tenant(over) {
    return Object.assign({ id: 'tnt-1', code: 'test02', name: 'test02',
                           active: true, version: 1, archived: false }, over);
}

function setup(options = {}) {
    const calls = [];
    const document = { ...eventTarget(), body: element('body'), createElement: element };
    document.getElementById = id => document.body.querySelector('#' + id);
    document.querySelector = sel => document.body.querySelector(sel);
    document.querySelectorAll = sel => document.body.querySelectorAll(sel);
    for (const id of ['tenant-list', 'tenant-status', 'tenant-create-btn', 'tenant-search',
                      'tenant-empty', 'tenant-status-filter']) {
        const el = element(id === 'tenant-create-btn' ? 'button' : (id === 'tenant-status-filter' ? 'select' : 'div'));
        el.id = id;
        if (id === 'tenant-search' || id === 'tenant-status-filter') el.value = '';
        document.body.appendChild(el);
    }

    const items = options.items || [tenant()];
    const ctx = {
        document, console,
        sessionStorage: { getItem: () => (options.currentTenant || 'tenant-of-operator') },
        setTimeout() {},
        clearTimeout() {},
        confirm: () => true,
        fetch: async (url, opts) => {
            calls.push({ url, options: opts });
            if (url === '/auth/me') {
                return response({ status: 'success', user: { id: 'u-root', username: 'root', is_platform_admin: true } });
            }
            if (url.startsWith('/api/platform/tenants?') || url === '/api/platform/tenants') {
                return response({ status: 'success', items: items });
            }
            if (/^\/api\/platform\/tenants\/[^/]+$/.test(url)) {
                // DELETE (archive) and POST operation=restore both land here.
                if (options.deleteResponse && opts && opts.method === 'DELETE') {
                    return options.deleteResponse();
                }
                return response({ status: 'success', tenant: items[0] });
            }
            throw Error('Unexpected request: ' + url);
        },
    };
    ctx.window = ctx;
    vm.runInNewContext(source, ctx, { filename: 'identity-admin.js' });
    document.dispatch('DOMContentLoaded');

    return {
        ctx, calls, node: id => document.getElementById(id),
        async load() { await ctx.loadTenantView(); await flush(); },
        listHtml: () => document.getElementById('tenant-list').innerHTML,
        deleteCalls: () => calls.filter(c => c.options && c.options.method === 'DELETE'),
        restoreCalls: () => calls.filter(c => c.options && c.options.method === 'POST' &&
            c.url.indexOf('/api/platform/tenants/') === 0),
        lastListGet: () => [...calls].reverse().find(c => (c.options ? c.options.method || 'GET' : 'GET') === 'GET' &&
            c.url.indexOf('/api/platform/tenants') === 0),
        modalOpen: () => {
            const el = document.getElementById('admin-modal');
            return !!el && !el.classList.contains('hidden');
        },
        modalError: () => (document.getElementById('admin-modal-error') || {}).textContent || '',
        modalErrorVisible: () => {
            const el = document.getElementById('admin-modal-error');
            return !!el && !el.classList.contains('hidden');
        },
        setField: (name, value) => { const el = document.getElementById('adm-fld-' + name); if (el) el.value = value; },
        submitModal() { document.getElementById('admin-modal-submit').dispatch('click'); },
    };
}

test('an ordinary tenant row offers delete, the default and the operator tenant do not', async () => {
    const h = setup({
        currentTenant: 'tnt-cur',
        items: [
            tenant({ id: 'tnt-1', code: 'test02' }),
            tenant({ id: 'tnt-def', code: 'default', name: '默认租户' }),
            tenant({ id: 'tnt-cur', code: 'operator-co', name: 'Operator Co' }),
        ],
    });
    await h.load();
    const html = h.listHtml();
    assert.ok(html.includes("'tenant','delete','tnt-1'"), 'the ordinary tenant has a delete action');
    assert.equal(html.includes("'tenant','delete','tnt-def'"), false,
        'the default tenant is protected and offers no delete');
    assert.equal(html.includes("'tenant','delete','tnt-cur'"), false,
        "the operator's own current tenant is protected and offers no delete");
});

test('an archived row shows the archived tag and a restore action instead of delete', async () => {
    const h = setup({
        items: [tenant({ id: 'tnt-arch', code: 'oldco', name: 'OldCo', active: false, archived: true })],
    });
    await h.load();
    const html = h.listHtml();
    assert.ok(html.includes("'tenant','restore','tnt-arch'"), 'the archived row offers restore');
    assert.equal(html.includes("'tenant','delete','tnt-arch'"), false,
        'an archived row must not offer delete');
    assert.ok(html.includes('tenant_archived_tag'), 'the archived marker is rendered');
});

test('the status filter is forwarded to the tenant list request', async () => {
    const h = setup();
    await h.load();
    assert.equal(h.lastListGet().url.includes('status='), false,
        'with no filter the request carries no status parameter');
    h.node('tenant-status-filter').value = 'archived';
    await h.load();
    const url = h.lastListGet().url;
    assert.ok(url.includes('status=archived'), 'the selected status is sent, got ' + url);
});

test('the delete dialog asks only for the platform admin password', async () => {
    const h = setup();
    await h.load();
    h.ctx.adminRowAction('tenant', 'delete', 'tnt-1');
    await flush();

    assert.equal(h.modalOpen(), true, 'the confirmation dialog opens');
    assert.ok(h.node('adm-fld-recent_password'), 'the password field is collected');
    assert.equal(h.node('adm-fld-confirm_code'), null,
        'no tenant-code confirmation field is required any more');
});

test('a rejected password keeps the delete dialog open with a friendly error', async () => {
    const h = setup({
        deleteResponse: () => response({ status: 'error', message: 'invalid old password' }, 401),
    });
    await h.load();
    h.ctx.adminRowAction('tenant', 'delete', 'tnt-1');
    await flush();
    h.setField('recent_password', 'WrongPassword');
    h.submitModal();
    await flush();

    assert.equal(h.modalOpen(), true, 'the dialog stays open so the password can be retyped');
    assert.equal(h.modalError(), 'tenant_password_wrong');
    assert.equal(h.node('adm-fld-recent_password').value, '',
        'the rejected password is cleared');
    assert.equal(h.deleteCalls().length, 1, 'the delete was attempted exactly once');
});

test('the confirmation sends the released version and password via DELETE', async () => {
    const h = setup();
    await h.load();
    h.ctx.adminRowAction('tenant', 'delete', 'tnt-1');
    await flush();
    h.setField('recent_password', 'Str0ngAdminPass');
    h.submitModal();
    await flush();

    const deletes = h.deleteCalls();
    assert.equal(deletes.length, 1);
    assert.equal(deletes[0].url, '/api/platform/tenants/tnt-1');
    assert.deepEqual(JSON.parse(deletes[0].options.body), {
        expected_version: 1,
        recent_password: 'Str0ngAdminPass',
    });
});

test('restore reuses the password prompt and posts operation=restore with the version', async () => {
    const h = setup({
        items: [tenant({ id: 'tnt-arch', code: 'oldco', name: 'OldCo', active: false, archived: true, version: 4 })],
    });
    await h.load();
    h.ctx.adminRowAction('tenant', 'restore', 'tnt-arch');
    await flush();

    const input = h.node('tenant-password-input');
    assert.ok(input, 'the shared password prompt opens');
    input.value = 'Str0ngAdminPass';
    h.node('tenant-password-confirm').dispatch('click');
    await flush();

    const posts = h.restoreCalls();
    assert.equal(posts.length, 1);
    assert.equal(posts[0].url, '/api/platform/tenants/tnt-arch');
    assert.deepEqual(JSON.parse(posts[0].options.body), {
        operation: 'restore',
        expected_version: 4,
        recent_password: 'Str0ngAdminPass',
    });
});
