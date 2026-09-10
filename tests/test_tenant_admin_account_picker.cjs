// Tenant admin account picker: designating a tenant admin must let a platform
// admin choose the target account from the current valid accounts, rather than
// typing an internal `usr_…` primary key. The server resolves the target with
// `SELECT * FROM users WHERE id=?` and answers a single opaque
// `user not found or disabled` (404) for both "no such id" and "disabled", so a
// free-text field is unusable in practice.
//
// The picker used to live in the "configure tenant admin" dialog. That dialog
// was replaced by the tabbed tenant editor's "tenant management" tab, so these
// cases now drive the picker there; the picker itself is unchanged.
//
// These cases drive the real `identity-admin.js` through a minimal DOM/fetch
// harness, following tests/test_tenant_create_frontend.cjs.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../channel/web/static/js/identity-admin.js'), 'utf8');

const ADMIN_ID = 'usr_zV2k24R8SVRqX8fr';
const OTHER_ID = 'usr_ysyTCy1UlbVUkXk5';

function eventTarget() {
    const listeners = new Map();
    return {
        addEventListener(type, fn) {
            if (!listeners.has(type)) listeners.set(type, []);
            listeners.get(type).push(fn);
        },
        dispatch(type, event = {}) {
            // Real DOM listeners get the target as `this`; several call sites
            // rely on that (e.g. per-element debounce handles).
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

function user(id, username, displayName) {
    return { id, username, display_name: displayName, active: true,
             is_platform_admin: false, must_change_password: false, version: 1 };
}

function setup(options = {}) {
    const calls = [];
    const document = { ...eventTarget(), body: element('body'), createElement: element };
    document.getElementById = id => document.body.querySelector('#' + id);
    document.querySelector = sel => document.body.querySelector(sel);
    document.querySelectorAll = sel => document.body.querySelectorAll(sel);
    for (const id of ['tenant-list', 'tenant-status', 'tenant-create-btn', 'tenant-search', 'tenant-empty']) {
        const el = element(id === 'tenant-create-btn' ? 'button' : 'div');
        el.id = id;
        if (id === 'tenant-search') el.value = '';
        document.body.appendChild(el);
    }

    const timers = [];
    const ctx = {
        document, console,
        sessionStorage: { getItem: () => 't1' },
        setTimeout(fn, ms) { const t = { fn, ms, cleared: false }; timers.push(t); return t; },
        clearTimeout(t) { if (t) t.cleared = true; },
        confirm: () => true,
        fetch: async (url, opts) => {
            calls.push({ url, options: opts });
            if (url === '/auth/me') {
                return response({ status: 'success', user: { id: 'u-root', username: 'root', is_platform_admin: true } });
            }
            if (url === '/api/platform/tenants') {
                return response({ status: 'success', items: [{ id: 'tnt-1', code: 'test02', name: 'test02', active: true, version: 1 }] });
            }
            if (url === '/api/platform/tenants/tnt-1' && (!opts || !opts.method)) {
                return response({ status: 'success', tenant: {
                    id: 'tnt-1', code: 'test02', name: 'test02', active: true, version: 1,
                    space: { id: 'test02', status: 'ready', isolation: 'dedicated-root' },
                } });
            }
            if (url === '/api/platform/tenants/tnt-1/resources') {
                return response({ status: 'success', grants: [] });
            }
            if (url.startsWith('/api/platform/users')) {
                if (options.userFetch) return options.userFetch(url, calls);
                return response({ status: 'success', items: [
                    user(ADMIN_ID, 'admin', '系统管理员'),
                    user(OTHER_ID, 'e2e_member', 'E2E普通成员'),
                ], total: 2, page: 1 });
            }
            if (url === '/api/platform/tenants/tnt-1/admins' && (!opts || !opts.method)) {
                return response({ status: 'success', items: [] });
            }
            if (url === '/api/platform/tenants/tnt-1/admins') {
                return response({ status: 'success', membership: { membership_id: 'mem-1' } });
            }
            throw Error('Unexpected request: ' + url);
        },
    };
    ctx.window = ctx;
    vm.runInNewContext(source, ctx, { filename: 'identity-admin.js' });
    document.dispatch('DOMContentLoaded');

    const runTimers = async () => {
        while (timers.length) {
            const t = timers.shift();
            if (!t.cleared) t.fn();
            await flush();
        }
    };

    return {
        ctx, calls, node: id => document.getElementById(id),
        runTimers,
        // The tenant admin is designated on the tabbed editor's management tab.
        editorOpen: () => {
            const ed = document.getElementById('tenant-editor');
            return !!ed && !ed.classList.contains('hidden');
        },
        activeTab: () => {
            const tabs = [...document.querySelectorAll('.tenant-editor-tab')];
            const el = tabs.find(t => t.classList.contains('active'));
            return el ? el.getAttribute('data-tab') : null;
        },
        editorErr: () => (document.getElementById('tenant-editor-error') || {}).textContent,
        editorErrVisible: () => {
            const el = document.getElementById('tenant-editor-error');
            return !!el && !el.classList.contains('hidden');
        },
        userCalls: () => calls.filter(c => c.url.startsWith('/api/platform/users')),
        adminPosts: () => calls.filter(c => c.url === '/api/platform/tenants/tnt-1/admins' && c.options && c.options.method === 'POST'),
        // Drive the picker the way the tenant list does: edit, then the admin tab.
        async openAdmin(id = 'tnt-1') {
            await ctx.loadTenantView();
            await flush();
            ctx.adminRowAction('tenant', 'edit', id);
            await flush();
            document.getElementById('tenant-editor-tab-admin').dispatch('click');
            await flush();
        },
        picker: () => document.body.querySelector('.user-picker'),
        pickerList: () => document.body.querySelector('.user-picker-list'),
        pickerSearch: () => document.body.querySelector('.user-picker-search'),
        pickerStatus: () => document.body.querySelector('.user-picker-status'),
        options: () => document.body.querySelectorAll('.user-picker-option'),
        async submit(password = 'Str0ngAdminPass') {
            document.getElementById('tenant-editor-submit').dispatch('click');
            await flush();
            // The password is collected by the save-time prompt, not by a
            // standing field inside the tab.
            const input = document.getElementById('tenant-password-input');
            if (input) {
                input.value = password;
                document.getElementById('tenant-password-confirm').dispatch('click');
                await flush();
            }
        },
    };
}

test('the admin tab offers an account picker instead of a free-text user_id input', async () => {
    const h = setup();
    await h.openAdmin();

    assert.equal(h.editorOpen(), true, 'the tenant editor is open');
    assert.equal(h.activeTab(), 'admin', 'the management tab hosts the picker');

    assert.ok(h.picker(), 'an account picker is rendered');
    assert.ok(h.pickerSearch(), 'the picker offers a search box');
    assert.equal(h.ctx.document.querySelectorAll('input#adm-fld-user_id').length, 0,
        'no input with the raw user_id id remains');
});

test('picker seeds candidates from the current valid accounts', async () => {
    const h = setup();
    await h.openAdmin();

    const seeded = h.userCalls();
    assert.equal(seeded.length, 1, 'candidates are loaded once on open');
    assert.ok(seeded[0].url.includes('status=active'), 'only valid accounts are candidates');
    assert.ok(seeded[0].url.includes('page_size=100'), 'one page at the server cap');

    const html = h.pickerList().innerHTML;
    assert.ok(html.includes('系统管理员'), 'candidate shows the display name');
    assert.ok(html.includes('admin'), 'candidate shows the login name');
    assert.ok(html.includes(ADMIN_ID) || (h.options()[0] || {}).getAttribute('data-user-id') === ADMIN_ID,
        'candidate carries the stable user id');
});

test('selecting a candidate submits that stable user id', async () => {
    const h = setup();
    await h.openAdmin();

    const option = h.options()[0];
    assert.ok(option, 'at least one candidate is selectable');
    option.dispatch('click');
    await flush();

    await h.submit();
    const posts = h.adminPosts();
    assert.equal(posts.length, 1, 'exactly one admin binding request');
    const body = JSON.parse(posts[0].options.body);
    assert.equal(body.user_id, ADMIN_ID, 'the selected id is submitted, not typed text');
    assert.equal(body.recent_password, 'Str0ngAdminPass');
});

test('submitting without a selection does not post and shows an error', async () => {
    const h = setup();
    await h.openAdmin();
    // Edit the tab so it joins the batch, then submit without choosing an
    // account: that must never bind an empty target.
    const display = h.ctx.document.getElementById('tenant-fld-admin_display');
    display.value = 'Someone';
    display.dispatch('input');
    await h.submit();

    assert.equal(h.adminPosts().length, 0, 'no binding request without a chosen account');
    assert.equal(h.editorOpen(), true, 'the editor stays open');
    assert.equal(h.editorErrVisible(), true, 'an error is surfaced');
    assert.equal(h.editorErr(), 'admin_user_picker_required',
        'the error names the missing selection rather than a generic required-field message');
});

test('candidate load failure blocks submit and stays retryable', async () => {
    const h = setup({
        userFetch: async () => response({ status: 'error', message: 'boom' }, 500),
    });
    await h.openAdmin();

    const st = h.pickerStatus();
    assert.ok(st, 'a status area is rendered');
    assert.equal(st.textContent, 'admin_user_picker_load_failed', 'failure is distinguished from an empty list');
    assert.equal(st.classList.contains('hidden'), false, 'failure is visible');

    await h.submit();
    assert.equal(h.adminPosts().length, 0, 'a failed candidate load never submits');
    assert.equal(h.editorOpen(), true, 'the editor is preserved for retry');
});

test('an empty candidate set shows an empty state and blocks submit', async () => {
    const h = setup({
        userFetch: async () => response({ status: 'success', items: [], total: 0, page: 1 }),
    });
    await h.openAdmin();

    const st = h.pickerStatus();
    assert.ok(st, 'a status area is rendered');
    assert.equal(st.textContent, 'admin_user_picker_empty', 'empty is not reported as a failure');

    await h.submit();
    assert.equal(h.adminPosts().length, 0, 'no account means no binding request');
});

test('searching refetches candidates with the query term', async () => {
    const h = setup();
    await h.openAdmin();

    const search = h.pickerSearch();
    search.value = 'e2e';
    search.dispatch('input');
    await h.runTimers();

    const calls = h.userCalls();
    assert.ok(calls.length >= 2, 'search triggers a new candidate query');
    const last = calls[calls.length - 1].url;
    assert.ok(last.includes('q=e2e'), 'the query term is sent to the server');
    assert.ok(last.includes('status=active'), 'the validity filter is preserved');
});

test('a stale search response does not overwrite the newest result', async () => {
    let resolveStale;
    let n = 0;
    const h = setup({
        userFetch: async () => {
            if (n++ === 0) return response({ status: 'success', items: [user(ADMIN_ID, 'admin', '系统管理员')], total: 1, page: 1 });
            if (n === 2) return new Promise(resolve => { resolveStale = resolve; });
            return response({ status: 'success', items: [user(OTHER_ID, 'newest', '最新结果')], total: 1, page: 1 });
        },
    });
    await h.openAdmin();

    const search = h.pickerSearch();
    search.value = 'slo';
    search.dispatch('input');
    await h.runTimers();
    search.value = 'fas';
    search.dispatch('input');
    await h.runTimers();

    // The first search finally answers, after the second already rendered.
    resolveStale(response({ status: 'success', items: [user(ADMIN_ID, 'stale', '过期结果')], total: 1, page: 1 }));
    await flush();

    const html = h.pickerList().innerHTML;
    assert.ok(html.includes('最新结果'), 'newest candidates are shown');
    assert.equal(html.includes('过期结果'), false, 'the late response is discarded');
});

test('candidates beyond one page surface a truncation hint', async () => {
    const many = Array.from({ length: 100 }, (_, i) => user('usr_' + i, 'u' + i, 'User ' + i));
    const h = setup({
        userFetch: async () => response({ status: 'success', items: many, total: 250, page: 1 }),
    });
    await h.openAdmin();

    const st = h.pickerStatus();
    assert.ok(st, 'a status area is rendered');
    assert.equal(st.textContent, 'admin_user_picker_truncated', 'silent truncation is not acceptable');
});
