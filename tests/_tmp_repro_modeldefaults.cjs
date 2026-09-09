// TEMP repro: does changing a model-default select write _modelDefaultSel and
// flow into the save body for a *new* role (empty model_defaults)?
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../channel/web/static/js/identity-admin.js'), 'utf8');
const catalog = ['tenant.info.read', 'tenant.members.read', 'tenant.org.read'];
const permsResp = () => ({ ok: true, status: 200, json: async () => ({ status: 'success', permissions: catalog }) });

function eventTarget() {
    const listeners = new Map();
    return {
        addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(fn); },
        dispatch(type) { for (const fn of listeners.get(type) || []) fn({ target: this, type }); },
    };
}

function element(tag = 'div') {
    const classes = new Set(); let html = '';
    const el = {
        ...eventTarget(), tagName: tag.toLowerCase(), id: '', value: '', type: '',
        children: [], style: {}, checked: false, disabled: false, textContent: '',
        get className() { return [...classes].join(' '); },
        set className(value) { classes.clear(); String(value).split(/\s+/).filter(Boolean).forEach(c => classes.add(c)); },
        classList: { add: (...n) => n.forEach(c => classes.add(c)), remove: (...n) => n.forEach(c => classes.delete(c)), contains: n => classes.has(n) },
        appendChild(c) { this.children.push(c); c.__parent = this; return c; },
        focus() {}, remove() {},
        closest(sel) { let n = this; while (n) { if (n.matches && n.matches(sel)) return n; n = n.__parent; } return null; },
        getAttribute(name) { return this[name] != null ? String(this[name]) : null; },
        matches(sel) {
            if (sel.startsWith('#')) return this.id === sel.slice(1);
            const clsAttr = sel.match(/^\.([\w-]+)\[([\w-]+)=(?:"|')([^"']*)(?:"|')?\]$/);
            if (clsAttr) return classes.has(clsAttr[1]) && String(this[clsAttr[2]] || '') === clsAttr[3];
            if (sel.startsWith('.')) return classes.has(sel.slice(1));
            if (sel === 'input:checked') return this.tagName === 'input' && this.checked;
            if (sel === 'input[type=checkbox]') return this.tagName === 'input' && this.type === 'checkbox';
            const am = sel.match(/^\[([\w-]+)=(?:"|')([^"']*)(?:"|')?\]$/);
            if (am) return String(this[am[1]] || '') === am[2];
            return this.tagName === sel;
        },
        querySelectorAll(sel) {
            const sels = sel.split(',').map(s => s.trim());
            return this.children.flatMap(c => [...(sels.some(s => c.matches(s)) ? [c] : []), ...c.querySelectorAll(sel)]);
        },
        querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
        get innerHTML() { return html; },
        set innerHTML(v) {
            html = String(v); this.children = [];
            const stack = [this];
            for (const m of html.matchAll(/<\/?([a-z][a-z0-9-]*)\b([^>]*)>/gi)) {
                if (m[0].startsWith('</')) { stack.pop(); continue; }
                const child = element(m[1]);
                for (const a of m[2].matchAll(/([\w-]+)(?:="([^"]*)")?/g)) {
                    const [, name, content = ''] = a;
                    if (name === 'class') child.className = content;
                    else if (['id', 'value', 'type', 'data-kind', 'data-cap'].includes(name)) child[name] = content;
                    else if (['checked', 'disabled'].includes(name)) child[name] = true;
                }
                stack[stack.length - 1].appendChild(child);
                if (!['input', 'br', 'hr', 'img', 'meta', 'link'].includes(child.tagName)) stack.push(child);
            }
        },
    };
    return el;
}

function response(data, status = 200) { return { ok: status >= 200 && status < 300, status, json: async () => data }; }

function setup() {
    const calls = [];
    const document = { ...eventTarget(), body: element('body'), createElement: element };
    document.getElementById = id => document.body.querySelector('#' + id);
    for (const id of ['role-list', 'role-status', 'role-create-btn']) { const e = element(id.endsWith('-btn') ? 'button' : 'div'); e.id = id; document.body.appendChild(e); }
    const ctx = { document, console, sessionStorage: { getItem: () => 'test-tenant' }, setTimeout() {}, confirm: () => true,
        fetch: async (url, options) => { calls.push({ url, options });
            if (url === '/api/tenant/permissions') return permsResp();
            if (url === '/api/tenant/roles') return response({ status: 'success', items: [] });
            if (url === '/api/tenant/roles/XXX') return response({ status: 'success' });
            if (url.startsWith('/api/tenant/authorization/catalog?')) {
                const p = new URL(url, 'http://test'); const kind = p.searchParams.get('kind');
                const byKind = { model: [{ resource_id: 'provider:deepseek:deepseek-v4-flash', name: 'deepseek-v4-flash', capability: 'model', provider: 'deepseek' }], skill: [] };
                return response({ status: 'success', kind, items: byKind[kind] || [], total: (byKind[kind] || []).length, page: 1, resource_actions: { model: ['read', 'use'], skill: [] }[kind] || [] });
            }
            throw Error('Unexpected request: ' + url);
        } };
    ctx.window = ctx;
    vm.runInNewContext(source, ctx, { filename: 'identity-admin.js' });
    document.dispatch('DOMContentLoaded');
    return { ctx, calls, node: id => document.getElementById(id) };
}

const settle = () => new Promise(r => setImmediate(r));

test('new role: selecting a chat model default flows into save body', async () => {
    const h = setup();
    const { ctx, calls } = h;
    // Open the new-role modal via its button.
    h.node('role-create-btn').dispatch('click');
    await settle();
    assert.equal(h.node('admin-modal').classList.contains('hidden'), false, 'modal open');

    // Expand the model resource group.
    const row = h.node('adm-fld-resource_grants').querySelector('[data-kind="model"]');
    const toggle = row.querySelector('.resource-kind-toggle');
    toggle.dispatch('click');
    await settle();
    const cb = row.querySelector('.resource-kind-list input[type=checkbox]');
    if (!cb) { console.log('NO-CB; manage=', row.querySelector('.resource-kind-manage') ? row.querySelector('.resource-kind-manage').innerHTML : 'No manage'); }
    else { cb.checked = true; cb.dispatch('change'); }
    await settle();
    await settle();

    // Change the chat model-default select.
    const md = h.node('adm-fld-modeldefaults');
    const chat = md ? md.querySelector('select[data-cap="chat"]') : null;
    if (!chat) { console.log('NO-CHAT-SELECT; md=', md ? md.innerHTML.slice(0,200) : 'null'); }
    else {
        chat.value = 'provider:deepseek:deepseek-v4-flash';
        chat.dispatch('change');
    }
    await settle();

    // Submit.
    const codeIn = h.node('adm-fld-code'); const nameIn = h.node('adm-fld-name');
    codeIn.value = 'e2e_repro'; nameIn.value = 'Repro'; codeIn.dispatch('input'); nameIn.dispatch('input');
    h.node('admin-modal-submit').dispatch('click');
    await settle();

    const saved = calls.find(c => c.url === '/api/tenant/roles');
    assert.ok(saved, 'a POST /api/tenant/roles was issued');
    const body = JSON.parse(saved.options.body);
    console.log('SAVE BODY model_defaults =', JSON.stringify(body.model_defaults));
    console.log('SAVE BODY resource_grants =', JSON.stringify(body.resource_grants));
    assert.deepEqual(body.model_defaults, { chat: 'provider:deepseek:deepseek-v4-flash' });
});
