// Contract tests for the external-identity binding dialog.
//
// The load-bearing rule is *which administrator hits which HTTP surface*: a
// member row must never reach the platform routes (that would skip the tenant
// containment), and a platform account row must never be sent to the tenant
// route with a user id where a membership id is expected.
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
            if (selector.startsWith('.')) return classes.has(selector.slice(1));
            if (selector === 'input:checked') return this.tagName === 'input' && this.checked;
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
                    else if (['id', 'value', 'type'].includes(name)) child[name] = content;
                    else if (['checked', 'disabled', 'selected'].includes(name)) child[name] = true;
                }
                stack[stack.length - 1].appendChild(child);
                if (!['input', 'br', 'hr', 'img', 'meta', 'link'].includes(child.tagName)) stack.push(child);
            }
            // A real <select> reports the selected option's value; without this
            // the emulated DOM could not witness a provider being chosen.
            const resolveSelects = node => {
                for (const child of node.children) {
                    if (child.tagName === 'select') {
                        const chosen = child.children.find(o => o.selected);
                        if (chosen) child.value = chosen.value;
                    }
                    resolveSelects(child);
                }
            };
            resolveSelects(this);
        },
    };
    return el;
}

function response(data, status = 200) {
    return { ok: status >= 200 && status < 300, status, json: async () => data };
}

function setup(opts = {}) {
    const calls = [];
    const document = { ...eventTarget(), body: element('body'), createElement: element };
    document.getElementById = id => document.body.querySelector('#' + id);
    document.querySelector = sel => document.body.querySelector(sel);
    document.querySelectorAll = sel => document.body.querySelectorAll(sel);
    const ctx = {
        document, console,
        sessionStorage: { getItem: () => 'test-tenant' },
        setTimeout() {},
        confirm: () => true,
        I18N: opts.i18n || { zh: {}, en: {} },
        fetch: async (url, options) => {
            calls.push({ url, options });
            if (opts.onRequest) {
                const handled = opts.onRequest(url, options);
                if (handled) return handled;
            }
            if (/external-identity-attempts$/.test(url)) {
                return response({ status: 'success', items: opts.attempts || [] });
            }
            if (/external-identities$/.test(url) && (!options || !options.method || options.method === 'GET')) {
                return response({ status: 'success', items: opts.bindings || [] });
            }
            if (options && options.method === 'POST') return response({ status: 'success' });
            if (options && options.method === 'DELETE') return response({ status: 'success' });
            throw Error('Unexpected request: ' + url);
        },
    };
    ctx.window = ctx;
    vm.runInNewContext(source, ctx, { filename: 'identity-admin.js' });
    document.dispatch('DOMContentLoaded');
    return { ctx, calls, node: id => document.getElementById(id) };
}

const settle = () => new Promise(resolve => setImmediate(resolve));

function member(over = {}) {
    return Object.assign({
        id: 'mem_1', user_id: 'usr_1', display_name: 'Acme Member',
        username: 'acmemember', active: 1, role_codes: ['member'], version: 1,
    }, over);
}

function platformUser(over = {}) {
    return Object.assign({
        id: 'usr_9', display_name: 'Platform Person', username: 'platformperson',
        active: 1, is_platform_admin: 1, version: 1,
    }, over);
}

async function openMember(h, over = {}) {
    await h.ctx.extidOpen('member', member(over));
    await settle();
    return member(over);
}

// --- which surface each entry point uses -----------------------------------

test('a member row binds through the tenant route with the membership id', () => {
    const h = setup();
    const routes = h.ctx.externalIdentityRoutes({
        platform: false, memberId: 'mem_1', userId: 'usr_1',
    });
    assert.equal(routes.base, '/api/tenant/members/mem_1/external-identities');
    assert.equal(routes.attempts, '/api/tenant/external-identity-attempts');
});

test('a platform account row binds through the platform route', () => {
    const h = setup();
    const routes = h.ctx.externalIdentityRoutes({
        platform: true, memberId: '', userId: 'usr_9',
    });
    assert.equal(routes.base, '/api/platform/users/usr_9/external-identities');
    assert.equal(routes.attempts, '/api/platform/external-identity-attempts');
});

test('the tenant entry point never reaches the platform surface', async () => {
    const h = setup();
    await openMember(h);
    const urls = h.calls.map(c => c.url);
    assert.ok(urls.some(u => u === '/api/tenant/members/mem_1/external-identities'), urls);
    assert.ok(!urls.some(u => u.indexOf('/api/platform/') === 0),
        'a member row must not touch the platform routes: ' + JSON.stringify(urls));
});

test('a platform admin viewing a member row is served, not refused', () => {
    // A platform admin may open a tenant's member list without holding that
    // tenant's admin role: the tenant surface would answer 403, so the dialog
    // must route them to the platform surface. Both administrators therefore
    // have a working path — which is the requirement.
    const h = setup();
    assert.equal(h.ctx.externalIdentitySurface('member', true), 'platform');
    assert.equal(h.ctx.externalIdentitySurface('member', false), 'tenant');
    assert.equal(h.ctx.externalIdentitySurface('platform_user', false), 'platform');
    assert.equal(h.ctx.externalIdentitySurface('platform_user', true), 'platform');
});

test('a tenant admin viewing a member row stays on the tenant surface', async () => {
    const h = setup();
    await h.ctx.extidOpen('member', member(), { platform: false });
    await settle();
    assert.ok(h.calls.every(c => c.url.indexOf('/api/tenant/') === 0),
        JSON.stringify(h.calls.map(c => c.url)));
});

test('a platform admin uses the platform surface with the account id', async () => {
    const h = setup();
    await h.ctx.extidOpen('member', member(), { platform: true });
    await settle();
    const urls = h.calls.map(c => c.url);
    assert.ok(urls.includes('/api/platform/users/usr_1/external-identities'), urls);
    assert.ok(!urls.some(u => u.indexOf('/api/tenant/') === 0), urls);
});

test('the dialog is a single shared component, not one per scope', () => {
    // Both entry points render through the same builder; a second, drifted copy
    // is exactly how "who / which channel / which open_id" stops being true in
    // one of the two views.
    const builders = source.match(/function externalIdentitiesModalHtml/g) || [];
    assert.equal(builders.length, 1);
    const entryButtons = source.match(/onclick="openExternalIdentities\(/g) || [];
    assert.equal(entryButtons.length, 2, 'expected exactly two entry points');
});

// --- what the administrator is shown ---------------------------------------

test('a binding row names the channel, the app and the open_id', () => {
    const h = setup();
    const html = h.ctx.externalIdentitiesModalHtml({
        platform: false, name: 'Acme Member', username: 'acmemember',
        form: { provider: 'feishu', issuer: '', subject: '' },
        bindings: [{
            id: 'ext_1', provider: 'feishu', issuer: 'cli_acme',
            subject: 'ou_acme_1', created_at: 1,
        }],
        attempts: [],
    });
    assert.match(html, /cli_acme/);
    assert.match(html, /ou_acme_1/);
    // The provider is shown as a channel a human recognises, not as a code.
    assert.match(html, /extid_provider_feishu|飞书|feishu/);
    assert.match(html, /extidDelete\('ext_1'\)/);
});

test('the account label says who before it says which open_id', () => {
    const h = setup();
    assert.equal(
        h.ctx.externalIdentityAccountLabel({ name: 'Acme Member', username: 'acmemember' }),
        'Acme Member · acmemember');
    assert.equal(h.ctx.externalIdentityAccountLabel({ name: '', username: 'acmemember' }), 'acmemember');
});

test('the dialog header names the account it is editing', async () => {
    const h = setup();
    await openMember(h);
    assert.match(h.node('extid-subtitle').textContent, /Acme Member/);
    assert.match(h.node('extid-subtitle').textContent, /acmemember/);
});

test('an unbound account says so instead of showing an empty table', () => {
    const h = setup();
    const html = h.ctx.externalIdentitiesModalHtml({
        platform: false, name: 'X', username: 'x',
        form: { provider: 'feishu', issuer: '', subject: '' },
        bindings: [], attempts: [],
    });
    assert.match(html, /extid_no_bindings/);
    assert.match(html, /extid_no_attempts/);
});

// --- the pending picker: where the open_id comes from ----------------------

test('pending attempts are offered as one-click picks', async () => {
    const h = setup({ attempts: [
        { provider: 'feishu', issuer: 'cli_acme', subject: 'ou_stranger',
          channel_type: 'feishu', instance_id: 'chan_1', attempts: 3 },
    ] });
    await openMember(h);
    assert.match(h.node('extid-body').innerHTML, /ou_stranger/);
    assert.match(h.node('extid-body').innerHTML, /extidUseAttempt\(0\)/);
});

test('a pending attempt says who sent it and what they said', async () => {
    // An open_id names nobody. Without the sender's name and a preview of the
    // message, an administrator staring at this row cannot decide whether the
    // stranger is the colleague they are looking for.
    const h = setup({ attempts: [
        { provider: 'feishu', issuer: 'cli_acme', subject: 'ou_stranger',
          sender_name: '张三', message_preview: '帮我查下上季度报销',
          is_group: 0, attempts: 2 },
    ] });
    await openMember(h);
    const html = h.node('extid-body').innerHTML;
    assert.match(html, /张三/);
    assert.match(html, /帮我查下上季度报销/);
    assert.match(html, /ou_stranger/, 'the open_id is still the thing being bound');
});

test('a pending attempt with no name still shows what was said', async () => {
    // A channel without the contact scope resolves no name; the message is then
    // the only clue and must not be dropped.
    const h = setup({ attempts: [
        { provider: 'feishu', issuer: 'cli_acme', subject: 'ou_stranger',
          sender_name: '', message_preview: '帮我查下上季度报销', is_group: 0 },
    ] });
    await openMember(h);
    assert.match(h.node('extid-body').innerHTML, /帮我查下上季度报销/);
});

test('a group attempt is marked as coming from a group', async () => {
    const h = setup({ attempts: [
        { provider: 'feishu', issuer: 'cli_acme', subject: 'ou_stranger',
          sender_name: '李四', message_preview: '总结一下', is_group: 1 },
    ] });
    await openMember(h);
    const html = h.node('extid-body').innerHTML;
    assert.match(html, /extid_group_tag/, 'a group message reads differently');
});

test('a private attempt carries no group tag', async () => {
    const h = setup({ attempts: [
        { provider: 'feishu', issuer: 'cli_acme', subject: 'ou_stranger',
          sender_name: '张三', message_preview: '你好', is_group: 0 },
    ] });
    await openMember(h);
    assert.doesNotMatch(h.node('extid-body').innerHTML, /extid_group_tag/);
});

test('the newest evidence survives an older row that lacks it', async () => {
    // A row recorded before the evidence columns existed must still render.
    const h = setup({ attempts: [
        { provider: 'feishu', issuer: 'cli_acme', subject: 'ou_old' },
    ] });
    await openMember(h);
    const html = h.node('extid-body').innerHTML;
    assert.match(html, /ou_old/);
});

test('picking an attempt fills the form without a request', async () => {
    const h = setup({ attempts: [
        { provider: 'feishu', issuer: 'cli_acme', subject: 'ou_stranger' },
    ] });
    await openMember(h);
    const before = h.calls.length;
    h.ctx.extidUseAttempt(0);
    assert.equal(h.calls.length, before, 'filling a form must not write anything');
    assert.equal(h.node('extid-subject').value, 'ou_stranger');
    assert.equal(h.node('extid-issuer').value, 'cli_acme');
    assert.equal(h.node('extid-provider').value, 'feishu');
});

test('picking an out-of-range attempt is a no-op', async () => {
    const h = setup({ attempts: [] });
    await openMember(h);
    h.ctx.extidUseAttempt(4);
    assert.equal(h.node('extid-subject').value, '');
});

// --- writing ---------------------------------------------------------------

test('binding posts the triple to the tenant surface', async () => {
    const h = setup();
    await openMember(h);
    h.node('extid-provider').value = 'feishu';
    h.node('extid-issuer').value = 'cli_acme';
    h.node('extid-subject').value = 'ou_new';
    await h.ctx.extidSubmit();
    await settle();
    const post = h.calls.find(c => c.options && c.options.method === 'POST');
    assert.ok(post, 'expected a POST: ' + JSON.stringify(h.calls.map(c => c.url)));
    assert.equal(post.url, '/api/tenant/members/mem_1/external-identities');
    assert.deepEqual(JSON.parse(post.options.body),
        { provider: 'feishu', issuer: 'cli_acme', subject: 'ou_new' });
});

test('an empty open_id is refused before it reaches the server', async () => {
    const h = setup();
    await openMember(h);
    h.node('extid-subject').value = '';
    await h.ctx.extidSubmit();
    assert.ok(!h.calls.some(c => c.options && c.options.method === 'POST'),
        'nothing should be sent without an open_id');
    assert.match(h.node('extid-body').innerHTML, /extid_error_subject_required/);
});

test('unbinding deletes through the same surface it listed with', async () => {
    const h = setup({ bindings: [
        { id: 'ext_7', provider: 'feishu', issuer: 'cli_acme', subject: 'ou_a' },
    ] });
    await openMember(h);
    await h.ctx.extidDelete('ext_7');
    await settle();
    const del = h.calls.find(c => c.options && c.options.method === 'DELETE');
    assert.ok(del);
    assert.equal(del.url, '/api/tenant/members/mem_1/external-identities/ext_7');
});

test('a conflict is explained as an existing binding, not as a bad value', async () => {
    const h = setup({ i18n: { zh: { extid_error_conflict: '已绑定到其他账号' }, en: {} } });
    h.ctx.fetch = async (url, options) => {
        h.calls.push({ url, options });
        if (options && options.method === 'POST') {
            const err = new Error('external identity is already bound');
            err.status = 409;
            err.code = 'conflict';
            throw err;
        }
        if (/attempts$/.test(url)) return response({ status: 'success', items: [] });
        return response({ status: 'success', items: [] });
    };
    await openMember(h);
    h.node('extid-subject').value = 'ou_dup';
    await h.ctx.extidSubmit();
    assert.match(h.node('extid-body').innerHTML, /已绑定到其他账号/);
});

test('the platform entry point posts to the platform surface', async () => {
    const h = setup();
    await h.ctx.extidOpen('platform_user', platformUser());
    await settle();
    h.node('extid-subject').value = 'ou_platform';
    await h.ctx.extidSubmit();
    await settle();
    const post = h.calls.find(c => c.options && c.options.method === 'POST');
    assert.equal(post.url, '/api/platform/users/usr_9/external-identities');
});

test('an account with no user id cannot be opened (no half-addressed dialog)', async () => {
    const h = setup();
    await h.ctx.extidOpen('member', member({ user_id: '' }));
    assert.equal(h.node('extid-modal'), null, 'dialog must not open without an account to bind');
});

test('closing forgets the account so the next open starts clean', async () => {
    const h = setup();
    await openMember(h);
    h.ctx.extidClose();
    assert.ok(h.node('extid-modal').classList.contains('hidden'));
});
