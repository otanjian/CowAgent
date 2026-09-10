// Tenant creation flow: the tenant lifecycle no longer carries account
// provisioning. Creating a tenant collects only code/name/recent_password.
//
// Creation used to be a modal that chained into a "configure tenant admin"
// dialog. It is now the tabbed editor in create mode: on success it becomes the
// editor for the new tenant and points at the management tab, so the admin is
// designated there instead of in a follow-up dialog. The create/edit tab
// contract itself lives in tests/test_tenant_tabbed_editor_frontend.cjs; this
// file keeps the create-specific contract: what create collects, what it posts,
// and how a failure behaves.
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
            for (const fn of listeners.get(type) || []) fn({ target: this, type, ...event });
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
            if (selector.startsWith('.')) return classes.has(selector.slice(1));
            if (selector === 'input[type=checkbox]') return this.tagName === 'input' && this.type === 'checkbox';
            const attrMatch = selector.match(/^\[([\w-]+)=(?:"|')([^"']*)(?:"|')?\]$/);
            if (attrMatch) return String(this[attrMatch[1]] || '') === attrMatch[2];
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
            const stack = [this];
            for (const match of html.matchAll(/<\/?([a-z][a-z0-9-]*)\b([^>]*)>/gi)) {
                if (match[0].startsWith('</')) { stack.pop(); continue; }
                const child = element(match[1]);
                for (const attr of match[2].matchAll(/([\w-]+)(?:="([^"]*)")?/g)) {
                    const [, name, content = ''] = attr;
                    if (name === 'class') child.className = content;
                    else if (['id', 'value', 'type', 'data-kind', 'data-tab', 'data-cap', 'data-group', 'data-user-id'].includes(name)) child[name] = content;
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

const settle = () => new Promise(resolve => setImmediate(resolve));
const flush = async () => { for (let i = 0; i < 8; i++) await settle(); };

async function confirmPassword(h, password = 'Str0ngAdminPass') {
    const input = h.node('tenant-password-input');
    if (!input) return false;
    input.value = password;
    h.node('tenant-password-confirm').dispatch('click');
    await flush();
    return true;
}

function setup() {
    const calls = [];
    const document = { ...eventTarget(), body: element('body'), createElement: element };
    document.getElementById = id => document.body.querySelector('#' + id);
    document.querySelector = sel => document.body.querySelector(sel);
    document.querySelectorAll = sel => document.body.querySelectorAll(sel);
    for (const id of ['tenant-list', 'tenant-status', 'tenant-create-btn', 'tenant-search',
                      'tenant-empty', 'view-tenant']) {
        const el = element(id === 'tenant-create-btn' ? 'button' : 'div');
        el.id = id;
        if (id === 'tenant-search') el.value = '';
        document.body.appendChild(el);
    }
    const createdTenant = { id: 'tnt-new', code: 'newco', name: 'NewCo', active: true, version: 1 };
    const ctx = {
        document, console,
        sessionStorage: { getItem: () => 't1' },
        setTimeout() {},
        confirm: () => true,
        fetch: async (url, options) => {
            calls.push({ url, options });
            if (url === '/auth/me') {
                return response({ status: 'success', user: { id: 'u-root', username: 'root', is_platform_admin: true } });
            }
            if (url === '/api/platform/tenants' && (!options || !options.method || options.method === 'GET')) {
                return response({ status: 'success', items: [createdTenant] });
            }
            if (url === '/api/platform/tenants' && options.method === 'POST') {
                return response({ status: 'success', tenant: createdTenant });
            }
            if (url === '/api/platform/tenants/tnt-new' && (!options || !options.method || options.method === 'GET')) {
                return response({ status: 'success', tenant: createdTenant });
            }
            if (url === '/api/platform/tenants/tnt-new/resources') {
                return response({ status: 'success', grants: [] });
            }
            if (url.startsWith('/api/platform/users')) {
                return response({ status: 'success', items: [
                    { id: 'usr-root', username: 'root', display_name: 'Root', active: true },
                ], total: 1, page: 1 });
            }
            throw Error('Unexpected request: ' + url);
        },
    };
    ctx.window = ctx;
    vm.runInNewContext(source, ctx, { filename: 'identity-admin.js' });
    document.dispatch('DOMContentLoaded');
    return {
        ctx, calls,
        node: id => document.getElementById(id),
        editorOpen: () => {
            const ed = document.getElementById('tenant-editor');
            return !!ed && !ed.classList.contains('hidden');
        },
        activeTab: () => {
            const tabs = [...document.querySelectorAll('.tenant-editor-tab')];
            const el = tabs.find(t => t.classList.contains('active'));
            return el ? el.getAttribute('data-tab') : null;
        },
        // Field ids the create tab renders, so a reintroduced account field
        // cannot slip through unnoticed. Scoped to the basics panel: the
        // management tab legitimately holds the admin designation controls.
        fieldIds: () => {
            const panel = document.getElementById('tenant-panel-basic');
            return panel ? [...panel.querySelectorAll('input')].map(el => el.id).filter(Boolean) : [];
        },
        async openCreate() {
            await ctx.loadTenantView();
            await flush();
            document.getElementById('tenant-create-btn').dispatch('click');
            await flush();
        },
        async submitCreate(code = 'newco', name = 'NewCo', password = 'Str0ngAdminPass') {
            document.getElementById('tenant-fld-code').value = code;
            document.getElementById('tenant-fld-code').dispatch('input');
            document.getElementById('tenant-fld-name').value = name;
            document.getElementById('tenant-fld-name').dispatch('input');
            document.getElementById('tenant-editor-submit').dispatch('click');
            await flush();
            // The password is collected by the save-time prompt, not by a
            // standing form field.
            await confirmPassword(this, password);
        },
        posts: () => calls.filter(c => c.url === '/api/platform/tenants' && c.options && c.options.method === 'POST'),
        adminPosts: () => calls.filter(c => /\/admins$/.test(c.url)),
    };
}

test('the create editor collects only code/name, with the password collected at save time', async () => {
    const h = setup();
    await h.openCreate();

    assert.equal(h.editorOpen(), true, 'the tabbed editor opens for creation');
    const ids = h.fieldIds();
    assert.ok(ids.includes('tenant-fld-code'), 'code is collected');
    assert.ok(ids.includes('tenant-fld-name'), 'name is collected');
    assert.equal(ids.includes('tenant-fld-recent_password'), false,
        'the password is prompted on save instead of living in the form');
    // Account provisioning is not part of the tenant lifecycle.
    ids.filter(id => /admin_username|admin_password|admin_display|user_id/.test(id)).forEach(id => {
        assert.fail('the create form must not offer an account field, found ' + id);
    });
});

test('submitting only code/name/recent_password creates the tenant', async () => {
    const h = setup();
    await h.openCreate();
    await h.submitCreate();

    const posts = h.posts();
    assert.equal(posts.length, 1);
    assert.deepEqual(Object.keys(JSON.parse(posts[0].options.body)).sort(),
        ['code', 'name', 'recent_password']);
});

test('a failed create keeps the editor open and never chains into an admin dialog', async () => {
    const h = setup();
    h.ctx.fetch = async (url, options) => {
        h.calls.push({ url, options });
        if (url === '/auth/me') {
            return response({ status: 'success', user: { id: 'u-root', is_platform_admin: true } });
        }
        if (url === '/api/platform/tenants' && options.method === 'POST') {
            return response({ status: 'error', message: 'conflict' }, 409);
        }
        return response({ status: 'success', items: [] });
    };
    await h.openCreate();
    await h.submitCreate();

    assert.equal(h.editorOpen(), true, 'the draft survives a rejected create');
    assert.equal(h.node('tenant-editor-error').classList.contains('hidden'), false,
        'the failure is surfaced');
    assert.equal(h.ctx.document.getElementById('admin-modal'), null,
        'no follow-up dialog exists to open');
    assert.equal(h.adminPosts().length, 0, 'nothing binds an admin after a failed create');
});

test('tenant rows expose no cross-tenant role button and no separate admin action', async () => {
    // The row edits the tenant; designation of the tenant admin lives inside the
    // editor's management tab, and managing a tenant's roles is not offered here.
    const h = setup();
    await h.ctx.loadTenantView();
    await flush();
    const html = h.node('tenant-list').innerHTML;
    assert.equal(html.includes("'tenant','roles'"), false, 'no roles row action');
    assert.ok(html.includes("'tenant','edit'"), 'edit row action stays');
    assert.equal(html.includes("'tenant','admin'"), false,
        'the admin action moved into the editor management tab');
});
