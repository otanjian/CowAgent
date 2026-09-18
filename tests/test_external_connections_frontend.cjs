// 外部系统接入 console page (change add-external-system-access, task group 10).
//
// The page's two load-bearing promises are honesty constraints, so they are
// asserted against the code the browser actually runs rather than restated here:
//   * the test/execute classes are closed by the deployment, and the page renders
//     the server's own reason on a present-but-disabled control — it never
//     simulates a result and never offers a button that can only fail;
//   * credentials live only in the open form — never in a URL, never in
//     localStorage/sessionStorage, and a version conflict preserves what the user
//     typed instead of overwriting it.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const read = p => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const chatHtml = read('channel/web/chat.html');
const consoleJs = read('channel/web/static/js/console.js');
const pageJs = read('channel/web/static/js/external-connections.js');
const i18nJs = read('channel/web/static/js/i18n/external-connections.js');
const css = read('channel/web/static/css/console.css');

// -- the namespace, loaded the way console.js merges it -------------------
function loadNamespaces() {
    const ctx = { window: {} };
    vm.createContext(ctx);
    vm.runInContext(i18nJs, ctx, { filename: 'external-connections.js' });
    return ctx.window.__cowI18N__['external-connections'];
}
const ns = loadNamespaces();
const zh = ns.zh;

// -- minimal DOM -----------------------------------------------------------
// -- the tiny bit of markup reading the stub needs -------------------------
// A value read from a rendered control has to come from the markup the page
// wrote, otherwise "what the user typed" would be fabricated by the test.
function attrOf(tag, name) {
    const quoted = new RegExp(name + '\\s*=\\s*"([^"]*)"').exec(tag);
    if (quoted) return quoted[1];
    return new RegExp('(?:^|\\s)' + name + '(?=[\\s/>])').test(tag) ? '' : null;
}

function tagMarkup(html, sel) {
    if (!html) return '';
    const tags = html.match(/<[a-zA-Z][^>]*>/g) || [];
    if (sel.charAt(0) === '#') {
        const id = sel.slice(1);
        return tags.find(t => attrOf(t, 'id') === id) || '';
    }
    const attr = /^\[([A-Za-z0-9_-]+)(?:="([^"]*)")?\]$/.exec(sel);
    if (attr) {
        return tags.find(t => {
            const value = attrOf(t, attr[1]);
            if (value === null) return false;
            return attr[2] === undefined || value === attr[2];
        }) || '';
    }
    if (/^[a-z]+$/.test(sel)) return tags.find(t => t.toLowerCase().indexOf('<' + sel) === 0) || '';
    return '';
}

function valueFromMarkup(html, tag) {
    if (!tag) return '';
    if (tag.toLowerCase().indexOf('<textarea') === 0) {
        const start = html.indexOf(tag) + tag.length;
        const end = html.indexOf('</textarea>', start);
        return end > start ? html.slice(start, end) : '';
    }
    if (tag.toLowerCase().indexOf('<select') === 0) {
        const start = html.indexOf(tag) + tag.length;
        const end = html.indexOf('</select>', start);
        const body = end > start ? html.slice(start, end) : '';
        const options = body.match(/<option[^>]*>/g) || [];
        const chosen = options.find(o => attrOf(o, 'selected') !== null) || options[0];
        return chosen ? (attrOf(chosen, 'value') || '') : '';
    }
    const value = attrOf(tag, 'value');
    return value === null ? '' : value;
}

// Elements answer selector lookups with a memoized stub, so the page's real
// code path runs (it attaches listeners to whatever `querySelector` returns)
// while the test can inject values and fire those listeners. `querySelectorAll`
// stays empty on purpose: DOM-wide collection re-renders are not what these
// assertions are about, and an empty list keeps the draft the test set intact.
function element(tag) {
    const el = {
        tagName: String(tag || 'div').toUpperCase(),
        id: '', className: '', hidden: false, value: '', type: 'text',
        textContent: '', style: {}, children: [],
        parentNode: null, _key: null, _q: Object.create(null),
        _attrs: Object.create(null),
        classList: {
            add() {}, remove() {}, toggle() {}, contains() { return false; },
        },
        setAttribute(name, value) { el._attrs[name] = String(value); },
        getAttribute(name) {
            return Object.prototype.hasOwnProperty.call(el._attrs, name) ? el._attrs[name] : null;
        },
        removeAttribute(name) { delete el._attrs[name]; },
        addEventListener(type, fn) { harness.handlers(el._key + ' ' + type, []).push(fn); },
        removeEventListener() {},
        appendChild(child) { el.children.push(child); return child; },
        insertBefore(child) { el.children.push(child); return child; },
        removeChild() {},
        querySelector(sel) {
            const key = el._key + ' > ' + sel;
            if (!el._q[sel]) {
                const source = el._ctx ? el._ctx.html : el.innerHTML;
                const tag = tagMarkup(source, sel);
                const child = element('div');
                child._key = key;
                child.parentNode = el;
                child._ctx = { html: source, tag };
                child.value = valueFromMarkup(source, tag);
                el._q[sel] = child;
            }
            return el._q[sel];
        },
        querySelectorAll() { return []; },
        closest() { return null; },
        focus() {}, setSelectionRange() {},
    };
    // Replacing the markup of a container is what the page does between modals;
    // the stub drops its old lookup elements and their listeners with it, so a
    // confirmation cannot fire a handler registered by an earlier modal.
    let html = '';
    Object.defineProperty(el, 'innerHTML', {
        get() { return html; },
        set(value) {
            html = String(value);
            el._q = Object.create(null);
            harness.clearDescendants(el._key);
        },
    });
    return el;
}

// Listener registry for the stub elements, keyed by "<element key> <event>".
const harness = {
    store: new Map(),
    handlers(key, fallback) {
        if (!this.store.has(key)) this.store.set(key, fallback);
        return this.store.get(key);
    },
    fire(key, event) {
        (this.store.get(key) || []).forEach(fn => fn(event || { target: {} }));
    },
    // Drop listeners registered below a container that just re-rendered.
    clearDescendants(key) {
        for (const storeKey of Array.from(this.store.keys())) {
            if (storeKey.indexOf(key + ' > ') === 0) this.store.delete(storeKey);
        }
    },
    reset() { this.store.clear(); },
};

function boot(options) {
    const opts = options || {};
    const nodes = new Map();
    const docQueries = Object.create(null);
    harness.reset();
    let keySeq = 0;
    const document = {
        activeElement: null,
        documentElement: element('html'),
        addEventListener() {},
        createElement(tag) { return element(tag); },
        getElementById(id) {
            if (!nodes.has(id)) {
                const node = element('div');
                node.id = id;
                node._key = '#id:' + id + ':' + (keySeq += 1);
                nodes.set(id, node);
            }
            return nodes.get(id);
        },
        // A document-level lookup only answers when the selector's own marker is
        // in the modal currently rendered, so a lookup for a block the page did
        // not render behaves like the real DOM and returns null.
        querySelector(sel) {
            const html = (nodes.get('ec-modal-panel') || {}).innerHTML || '';
            const marker = sel.replace(/[\[\]"'.#]/g, '').split('=')[0];
            if (marker && html.indexOf(marker) === -1) return null;
            const key = 'doc > ' + sel;
            if (!docQueries[key]) {
                const node = element('div');
                node._key = key;
                docQueries[key] = node;
            }
            return docQueries[key];
        },
        querySelectorAll() { return []; },
    };
    document.body = element('body');
    document.activeElement = document.body;

    const fetchCalls = [];
    const localStorageWrites = [];
    const sessionWrites = [];
    let responder = opts.responder || (() => ({ ok: true, status: 200, json: { status: 'success' } }));

    const ctx = {
        console: { warn() {}, error() {}, log() {} },
        URL,
        Promise,
        setInterval: () => 0,
        clearInterval: () => {},
        localStorage: {
            getItem: () => null,
            setItem(k, v) { localStorageWrites.push([k, v]); },
            removeItem(k) { localStorageWrites.push([k, null]); },
        },
        sessionStorage: {
            // The console shell carries the current tenant here; only a test that
            // asks for it gets one, so the other tests keep reading one scope.
            getItem: k => (k === 'cow_tenant_id' ? (opts.tenantId || null) : null),
            setItem(k, v) { sessionWrites.push([k, v]); },
            removeItem(k) { sessionWrites.push([k, null]); },
        },
        fetch(url, init) {
            fetchCalls.push({ url, init });
            const out = responder(url, init);
            return Promise.resolve({
                ok: out.status ? out.status >= 200 && out.status < 300 : true,
                status: out.status || 200,
                json: () => Promise.resolve(out.json === undefined ? { status: 'success' } : out.json),
            });
        },
        t(key) { return Object.prototype.hasOwnProperty.call(zh, key) ? zh[key] : key; },
        crypto: { randomUUID: () => 'uuid-' + fetchCalls.length },
        navigateTo() {},
        _wsToast() {},
        registerConsoleView(spec) { ctx.__specs.push(spec); },
        __specs: [],
        document,
    };
    ctx.window = ctx;
    vm.createContext(ctx);
    vm.runInContext(pageJs, ctx, { filename: 'external-connections.js' });
    return {
        ctx, document, fetchCalls, localStorageWrites, sessionWrites,
        page: ctx.ExternalConnectionsPage,
        specs: ctx.__specs,
        nodes, harness,
        setResponder(fn) { responder = fn; },
        // DOM handles for the operation flows: the modal panel the page renders
        // into and its elements, plus the recorded listeners.
        panel() { return nodes.get('ec-modal-panel') || document.getElementById('ec-modal-panel'); },
        panelEl(sel) { return this.panel().querySelector(sel); },
        drawerBody() { return document.getElementById('ec-drawer-body'); },
        fire(node, type) { harness.fire(node._key + ' ' + type, { target: node }); },
    };
}

const MCP_TYPE = {
    kind: 'mcp',
    label: 'MCP',
    label_key: 'ext_conn_type_mcp',
    config_keys: ['transport', 'url'],
    secret_slots: ['header', 'env', 'oauth'],
    scopes: [
        { scope: 'platform', available: true, reason: '' },
        { scope: 'tenant', available: false, reason: 'management_required' },
    ],
    // The deployment has not accepted an MCP test environment: the class is
    // closed and the reason is the server's, not a placeholder.
    capabilities: {
        open: ['configure'],
        test_available: false,
        execute_available: false,
        unavailable_reason: 'awaiting_mcp_test_environment',
        missing_secret_slots: ['header'],
    },
};

const MCP_CARD = {
    id: 'card-1', effective_id: 'card-1', kind: 'mcp', scope: 'platform', name: 'Shared tools',
    enabled: true, source: 'platform', version: 1, test_status: 'untested', tested_at: null,
    actions: ['read', 'manage'], base_connection_id: null,
};

function typesResponse(types, scopes) {
    return {
        status: 'success',
        types: types || [MCP_TYPE],
        form_version: 1,
        scopes: scopes,
    };
}

const okCatalog = items => ({
    status: 'success', items: items || [], total: (items || []).length, scope: 'platform',
});

// The last POST the page sent (a GET after a successful save is not a write).
const lastWrite = app => app.fetchCalls.filter(
    call => call.init && call.init.method === 'POST').pop();

// =====================================================================
// 1. Registration: menu entry, group order, view meta, lazy module
// =====================================================================
test('the menu entry sits in 模型与接入, immediately after 消息渠道', () => {
    const from = chatHtml.indexOf('data-group="model-access"');
    const to = chatHtml.indexOf('<!-- 组织与权限', from);
    assert.ok(from > 0 && to > from, 'the 模型与接入 group must exist');
    const group = chatHtml.slice(from, to);
    const config = group.indexOf('data-view="config"');
    const channels = group.indexOf('data-view="channels"');
    const external = group.indexOf('data-view="external_connections"');
    assert.ok(config > 0 && channels > config && external > channels,
        'order must be 模型服务 → 消息渠道 → 外部系统接入');
    assert.match(group, /data-i18n="menu_external_connections"/);
    // Nothing between 消息渠道 and the new entry: "immediately after".
    const between = group.slice(channels + 'data-view="channels"'.length, external);
    assert.equal((between.match(/data-view=/g) || []).length, 0);
});

test('console.js signs the view with a stable group and lazy module', () => {
    assert.match(consoleJs,
        /'external_connections':\s*\{\s*group:\s*'nav_group_model_access',\s*page:\s*'menu_external_connections',\s*console:\s*'admin\.external_connections'\s*\}/);
    assert.match(consoleJs, /external_connections:\s*'assets\/js\/external-connections\.js'/);
    // The page body is a container only; the page module is not a <script> in
    // chat.html (only its i18n namespace file is), so nothing is fetched until
    // the view is entered.
    assert.match(chatHtml, /id="view-external_connections"/);
    assert.doesNotMatch(chatHtml, /<script[^>]*src="assets\/js\/external-connections\.js"/);
    assert.match(chatHtml, /<script defer src="assets\/js\/i18n\/external-connections\.js"><\/script>/);
    // classic/split share one sidebar; the entry is a plain sidebar-item.
    assert.match(chatHtml, /class="sidebar-item[^"]*"\s*\n?\s*data-view="external_connections"/);
});

test('the module registers the page with its route, title and leave guards', () => {
    const app = boot();
    assert.equal(app.specs.length, 1);
    const spec = app.specs[0];
    assert.equal(spec.id, 'external_connections');
    assert.equal(spec.title, 'ec_title');
    assert.equal(spec.page, 'menu_external_connections');
    assert.equal(typeof spec.load, 'function');
    assert.equal(typeof spec.isDirty, 'function');
    assert.equal(typeof spec.confirmLeave, 'function');
    assert.equal(typeof spec.onLeave, 'function');
    // The page owns its own addresses; the alias normalises to the view id.
    assert.match(consoleJs, /viewId === 'external-connections' \? 'external_connections' : viewId/);
});

test('every i18n key the page renders exists in all three languages', () => {
    const keys = new Set();
    const quoted = /'(ec_[a-z0-9_]+|ext_conn_type_[a-z0-9_]+)'/g;
    let match;
    while ((match = quoted.exec(pageJs)) !== null) keys.add(match[1]);
    assert.ok(keys.size > 60, 'the page should name its keys, not inline copy');
    ['zh', 'zh-Hant', 'en'].forEach(lang => {
        keys.forEach(key => {
            assert.ok(Object.prototype.hasOwnProperty.call(ns[lang], key),
                `${lang} is missing ${key}`);
            assert.ok(String(ns[lang][key]).length > 0, `${lang}.${key} must not be empty`);
        });
    });
});

// =====================================================================
// 2. The closed test/execute state is the server's, never simulated
// =====================================================================
test('a closed test class renders the server reason on a disabled control', async () => {
    const app = boot({
        responder(url) {
            if (url.indexOf('/types') >= 0) return { json: typesResponse([MCP_TYPE]) };
            if (url.indexOf('/catalog') >= 0) return { json: okCatalog([MCP_CARD]) };
            return { json: { status: 'success' } };
        },
    });
    await app.page.load();
    const html = app.page.pageHtml();
    // The real reason sentence, taken from the deployment's own answer.
    assert.match(html, /尚未取得 MCP 测试环境的验收证据，测试保持关闭。/);
    assert.match(html, /缺少凭据：认证请求头/);
    // Present but disabled, with an accessible description.
    assert.match(html, /<button type="button" class="ec-btn ec-btn-ghost ec-btn-small" disabled/);
    assert.match(html, /aria-describedby="ec-test-reason-card-1"/);
    // Never a simulated success: no "连接正常" was invented anywhere.
    assert.doesNotMatch(html, /连接正常/);
    assert.doesNotMatch(html, /演示|模拟/);
});

test('a closed test class never fires a request, and stays closed after the read', async () => {
    const app = boot({
        responder(url) {
            if (url.indexOf('/types') >= 0) return { json: typesResponse([MCP_TYPE]) };
            if (url.indexOf('/catalog') >= 0) return { json: okCatalog([MCP_CARD]) };
            return { json: { status: 'success' } };
        },
    });
    await app.page.load();
    const card = app.page.state.cards[0];
    const before = app.fetchCalls.length;
    app.page.runTest(card);
    assert.equal(app.fetchCalls.length, before, 'a disabled control must not call the test route');
    assert.ok(app.page.capabilityFor('mcp').test_available === false);
});

test('a load failure is not rendered as "no connections"', async () => {
    const app = boot({
        responder(url) {
            if (url.indexOf('/types') >= 0) return { json: typesResponse([MCP_TYPE]) };
            return { status: 500, json: { status: 'error', code: 'internal', message: 'boom' } };
        },
    });
    await app.page.load();
    const html = app.page.pageHtml();
    assert.match(html, /连接目录读取失败/);
    assert.match(html, /这不代表没有连接/);
    assert.doesNotMatch(html, /还没有连接/);
    assert.doesNotMatch(html, /共 0 个连接/);
});

// =====================================================================
// 3. Secrets: only in the form, only in the request body
// =====================================================================
test('a typed credential goes into the request body and nowhere else', async () => {
    const app = boot({
        responder(url) {
            if (url.indexOf('/types') >= 0) return { json: typesResponse([MCP_TYPE]) };
            if (url.indexOf('/catalog') >= 0) return { json: okCatalog([]) };
            return { json: { status: 'success' } };
        },
    });
    await app.page.load();
    app.page.openDrawer({ mode: 'create', kind: 'mcp', scope: 'tenant', original: {} });
    app.page.setDraftValue('name', 'Tenant MCP');
    app.page.setDraftValue('transport', 'streamable_http');
    app.page.setDraftValue('url', 'https://mcp.example.com/mcp');
    app.page.setDraftValue('auth', 'header');
    app.page.setDraftValue('header_name', 'X-API-Key');
    app.page.setSecretInput('header', 'TOPSECRET-123');
    await app.page.save();

    const create = app.fetchCalls.filter(c => c.url.indexOf('/tenant') >= 0
        && c.init && c.init.method === 'POST')[0];
    assert.ok(create, 'the create request must be sent');
    assert.equal(create.url.indexOf('TOPSECRET-123'), -1, 'a secret never enters a URL');
    const body = JSON.parse(create.init.body);
    assert.deepEqual(body.secrets, { header: 'TOPSECRET-123' });
    assert.equal(body.config.header, undefined, 'a secret never enters the config');
    assert.equal(JSON.stringify(body).split('TOPSECRET-123').length - 1, 1,
        'the credential appears exactly once, in `secrets`');
    assert.equal(create.init.headers['Idempotency-Key'].length > 0, true,
        'a create carries an idempotency key');
    // Browser storage is never touched, in any form.
    assert.deepEqual(app.localStorageWrites, []);
    assert.deepEqual(app.sessionWrites, []);
    // The module persists nothing anywhere (no setItem at all), so a credential
    // has no durable home outside the live input.
    assert.equal(/\.setItem\s*\(/.test(pageJs), false, 'the page never writes storage');
    // A confirmed leave drops the live secret with the form.
    app.page.closeDrawer();
    assert.equal(app.page.drawerState(), null);
    assert.doesNotMatch(JSON.stringify(app.page.state), /TOPSECRET-123/);
});

test('blank means keep and an explicit clear sends null', async () => {
    const original = {
        kind: 'erp', scope: 'tenant', name: 'ERP', enabled: true, version: 3,
        actions: ['read', 'manage'], base_connection_id: null,
        config: { provider: 'rfc', ashost: 'sap.internal', sysnr: '00', client: '100', user: 'u', lang: 'EN' },
        secrets: { password: { configured: true } },
    };
    const app = boot({
        responder(url) {
            if (url.indexOf('/types') >= 0) return { json: typesResponse([]) };
            if (url.indexOf('/catalog') >= 0) return { json: okCatalog([]) };
            return { json: { status: 'success' } };
        },
    });
    await app.page.load();

    app.page.openDrawer({ mode: 'edit', kind: 'erp', scope: 'tenant', id: 'c1', version: 3, original });
    app.page.setDraftValue('name', 'ERP renamed');
    await app.page.save();
    const keep = JSON.parse(lastWrite(app).init.body);
    assert.equal(keep.expected_version, 3);
    assert.equal(keep.secrets, undefined, 'an untouched input keeps the stored credential');

    app.page.openDrawer({ mode: 'edit', kind: 'erp', scope: 'tenant', id: 'c1', version: 4, original });
    app.page.setClear('password', true);
    await app.page.save();
    const cleared = JSON.parse(lastWrite(app).init.body);
    assert.deepEqual(cleared.secrets, { password: null });
});

test('a mask is refused as a credential instead of being submitted', async () => {
    const app = boot();
    const drawer = {
        kind: 'mcp', draft: { name: 'x', transport: 'streamable_http', url: 'https://a.example.com/mcp', auth: 'header', header_name: 'H' },
        secrets: { header: true }, secretInputs: { header: '••••••••' }, clears: {},
    };
    const errors = app.page.validateDraft(drawer);
    assert.equal(errors['secret:header'], zh.ec_validate_secret_masked);
    // And it never reaches the payload builder.
    assert.equal(Object.keys(app.page.buildSecrets(drawer)).length, 0);
});

// =====================================================================
// 4. A 409 preserves the input; nothing is auto-overwritten
// =====================================================================
test('a version conflict keeps the typed input and offers a re-read', async () => {
    const original = {
        kind: 'erp', scope: 'tenant', name: 'ERP', enabled: true, version: 3,
        actions: ['read', 'manage'], base_connection_id: null,
        config: { provider: 'rfc', ashost: 'sap.internal', sysnr: '00', client: '100', user: 'u', lang: 'EN' },
        secrets: { password: { configured: true } },
    };
    const app = boot({
        responder(url) {
            if (url.indexOf('/types') >= 0) return { json: typesResponse([]) };
            if (url.indexOf('/catalog') >= 0) return { json: okCatalog([]) };
            return { status: 409, json: { status: 'error', code: 'version_conflict', message: 'connection was modified by another writer' } };
        },
    });
    await app.page.load();
    app.page.openDrawer({ mode: 'edit', kind: 'erp', scope: 'tenant', id: 'c1', version: 3, original });
    app.page.setDraftValue('name', 'Renamed ERP');
    const before = app.fetchCalls.length;
    const saved = await app.page.save();
    assert.equal(saved, false);
    assert.equal(app.fetchCalls.length, before + 1, 'a conflict is not retried automatically');
    assert.equal(app.page.drawerState().draft.name, 'Renamed ERP', 'the input survives');
    assert.equal(app.page.drawerState().conflict, true);
    const body = app.document.getElementById('ec-drawer-body').innerHTML;
    assert.match(body, /为避免覆盖新版本/);
    assert.match(body, /data-ec-action="drawer-reload"/);
});

test('a lost response is reported as unknown and not retried', async () => {
    const original = {
        kind: 'erp', scope: 'tenant', name: 'ERP', enabled: true, version: 1,
        actions: ['read', 'manage'], base_connection_id: null,
        config: { provider: 'rfc', ashost: 'h', sysnr: '00', client: '1', user: 'u', lang: 'EN' },
        secrets: {},
    };
    const app = boot({
        responder(url) {
            if (url.indexOf('/types') >= 0) return { json: typesResponse([]) };
            if (url.indexOf('/catalog') >= 0) return { json: okCatalog([]) };
            return { json: { status: 'success' } };
        },
    });
    await app.page.load();
    app.page.openDrawer({ mode: 'edit', kind: 'erp', scope: 'tenant', id: 'c1', version: 1, original });
    app.page.setDraftValue('name', 'Kept name');
    // The transport fails: ecRequest resolves with transport:true.
    app.setResponder(() => { throw new Error('socket closed'); });
    const before = app.fetchCalls.length;
    await app.page.save();
    assert.equal(app.fetchCalls.length, before + 1, 'no silent retry');
    assert.equal(app.page.drawerState().draft.name, 'Kept name');
    assert.equal(app.page.drawerState().unknown, true);
    assert.match(app.document.getElementById('ec-drawer-body').innerHTML, /请求结果未知/);
});

// =====================================================================
// 5. Per-kind forms, the type picker and the operations
// =====================================================================
const ALL_TYPES = [
    MCP_TYPE,
    {
        kind: 'erp', label: 'ERP', config_keys: ['provider'],
        secret_slots: ['password'],
        scopes: [{ scope: 'tenant', available: true, reason: '' }],
        capabilities: {
            open: ['configure'], test_available: false, execute_available: false,
            unavailable_reason: 'awaiting_sap_test_environment', missing_secret_slots: ['password'],
        },
    },
    {
        kind: 'oa', label: 'OA', config_keys: ['base_url'],
        secret_slots: ['password', 'app_secret'],
        scopes: [{ scope: 'tenant', available: false, reason: 'singleton_exists' }],
        capabilities: {
            open: ['configure'], test_available: false, execute_available: false,
            unavailable_reason: 'awaiting_oa_test_environment', missing_secret_slots: [],
        },
    },
    {
        kind: 'email', label: 'Email', config_keys: ['imap', 'smtp'],
        secret_slots: ['imap_password', 'smtp_password'],
        scopes: [{ scope: 'personal', available: false, reason: 'management_required' }],
        capabilities: {
            open: ['configure'], test_available: false, execute_available: false,
            unavailable_reason: 'awaiting_mail_test_environment', missing_secret_slots: [],
        },
    },
];

const envelope = payload => ({ json: { data: payload, request_id: 'req-1' } });
const flush = () => new Promise(resolve => setImmediate(resolve));

test('each kind builds its full field set, and the picker routes to what exists', async () => {
    const app = boot({
        responder(url) {
            if (url.indexOf('/types') >= 0) return { json: typesResponse(ALL_TYPES) };
            if (url.indexOf('/catalog') >= 0) return envelope({ items: [], total: 0, scope: 'tenant' });
            return envelope({});
        },
    });
    await app.page.load();

    const fields = {
        mcp: ['data-ec-field="transport"', 'data-ec-field="url"', 'data-ec-group="mcp-stdio"',
            'data-ec-field="command"', 'data-ec-secret="header"', 'data-ec-secret="env"'],
        erp: ['data-ec-field="provider"', 'data-ec-field="ashost"', 'data-ec-field="client"',
            'data-ec-secret="password"'],
        oa: ['data-ec-field="base_url"', 'data-ec-field="app_key"',
            'data-ec-secret="app_secret"', 'data-ec-secret="password"'],
        email: ['data-ec-field="imap_host"', 'data-ec-field="imap_port"', 'data-ec-field="smtp_host"',
            'data-ec-field="smtp_tls_mode"', 'data-ec-secret="imap_password"', 'data-ec-secret="smtp_password"'],
    };
    Object.keys(fields).forEach(kind => {
        const scope = kind === 'email' ? 'personal' : 'tenant';
        app.page.openDrawer({ mode: 'create', kind, scope, original: {} });
        const form = app.drawerBody().innerHTML;
        assert.ok(form.length > 200, kind + ' renders a form');
        fields[kind].forEach(marker => assert.ok(
            form.indexOf(marker) >= 0, kind + ' must render ' + marker));
        // The whole per-type field set is present in one form; switching
        // transport/auth reveals groups instead of rebuilding and losing input.
        if (kind === 'mcp') {
            assert.ok(form.indexOf('data-ec-group="mcp-remote"') >= 0
                && form.indexOf('data-ec-group="mcp-stdio"') >= 0);
        }
        assert.doesNotMatch(form, /演示|模拟/);
        app.page.closeDrawer(true);
    });

    app.page.openTypePicker();
    const picker = app.panel().innerHTML;
    assert.ok(picker.indexOf('data-ec-action="pick-type" data-kind="mcp"') >= 0,
        'a creatable type offers create');
    assert.ok(picker.indexOf('data-ec-action="pick-type" data-kind="erp"') >= 0);
    assert.equal(picker.indexOf('data-ec-action="pick-type" data-kind="oa"'), -1,
        'a singleton is never offered for creation');
    assert.ok(picker.indexOf('data-ec-action="pick-existing" data-kind="oa"') >= 0);
    assert.ok(picker.indexOf(zh.ec_type_go_edit_existing) >= 0);
    // A type that cannot be created carries the server's own reason sentence.
    assert.ok(picker.indexOf(zh.ec_type_reason_management_required) >= 0);
});

const ERP_DEFAULT_CARD = {
    id: 'erp-1', effective_id: 'erp-1', kind: 'erp', scope: 'tenant', name: 'SAP 生产',
    enabled: true, source: 'tenant', version: 3, actions: ['read', 'manage'],
    test_status: 'untested', tested_at: null, base_connection_id: null,
    config: { provider: 'rfc', ashost: 'h', sysnr: '00', client: '1', user: 'u', lang: 'EN' },
    secrets: { password: { configured: true } },
};
const ERP_SPARE_CARD = {
    id: 'erp-2', effective_id: 'erp-2', kind: 'erp', scope: 'tenant', name: 'SAP 备用',
    enabled: false, source: 'tenant', version: 4, actions: ['read', 'manage'],
    test_status: 'untested', tested_at: null, base_connection_id: null,
    config: { provider: 'rfc', ashost: 'h2', sysnr: '00', client: '1', user: 'u', lang: 'EN' },
    secrets: {},
};
const ERP_THIRD_CARD = {
    id: 'erp-3', effective_id: 'erp-3', kind: 'erp', scope: 'tenant', name: 'SAP 备用二',
    enabled: true, source: 'tenant', version: 9, actions: ['read', 'manage'],
    test_status: 'untested', tested_at: null, base_connection_id: null,
    config: { provider: 'rfc', ashost: 'h3', sysnr: '00', client: '1', user: 'u', lang: 'EN' },
    secrets: {},
};
const INHERITED_CARD = {
    id: 'plat-1', effective_id: 'ovr-9', kind: 'mcp', scope: 'platform', name: '平台 MCP',
    enabled: true, source: 'inherited', version: 5, actions: ['read', 'manage'],
    test_status: 'untested', tested_at: null, base_connection_id: 'ovr-9', config: {},
};

test('operations carry the impact copy and render the server refusal', async () => {
    let deleteAnswer = envelope({});
    const app = boot({
        tenantId: 'tenant-1',
        responder(url, init) {
            const post = !!(init && init.method === 'POST');
            if (url.indexOf('/types') >= 0) return { json: typesResponse(ALL_TYPES) };
            if (url.indexOf('/catalog') >= 0) {
                if (url.indexOf('scope=platform') >= 0) {
                    return envelope({ items: [INHERITED_CARD], total: 1, scope: 'platform' });
                }
                if (url.indexOf('scope=personal') >= 0) return envelope({ items: [], total: 0, scope: 'personal' });
                return envelope({ items: [ERP_DEFAULT_CARD, ERP_SPARE_CARD, ERP_THIRD_CARD], total: 3, scope: 'tenant' });
            }
            if (url.indexOf('/erp-default') >= 0 && !post) {
                return envelope({ connection_id: 'erp-1', revision: 7 });
            }
            if (url.indexOf('/delete') >= 0) return deleteAnswer;
            if (url.indexOf('/tenant-access') >= 0 && !post) {
                return envelope({ tenants: [{ tenant_id: 't1', enabled: true }, { tenant_id: 't2', enabled: false }], revision: 3 });
            }
            return envelope({});
        },
    });
    await app.page.load();
    const cardFor = id => app.page.state.cards.filter(c => c.id === id)[0];
    assert.ok(cardFor('erp-1') && cardFor('erp-2') && cardFor('plat-1'), 'the catalogue loaded');

    assert.ok(app.page.state.scopes.indexOf('tenant') >= 0, 'the tenant range is readable');
    await flush();   // the ERP-default pointer is read after the catalogue lands
    assert.equal(app.page.state.erpDefault && app.page.state.erpDefault.connection_id, 'erp-1');

    // 1. Deleting the ERP default states the impact and asks what happens to it.
    //    The tenant admin picks a replacement, and that choice must reach the
    //    server (it is read before the modal is emptied).
    app.page.confirmDelete(cardFor('erp-1'));
    const confirmCopy = app.panel().innerHTML;
    assert.ok(confirmCopy.indexOf(zh.ec_confirm_delete_title) >= 0);
    assert.ok(confirmCopy.indexOf(ERP_DEFAULT_CARD.name) >= 0);
    assert.ok(confirmCopy.indexOf('data-ec-default-handling') >= 0, 'the default is addressed');
    app.panel().querySelector('[data-ec-default-handling]').querySelector('select').value = 'erp-3';
    app.fire(app.panelEl('[data-ec-action="modal-confirm"]'), 'click');
    await flush();
    let write = lastWrite(app);
    assert.ok(write.url.indexOf('/api/external-connections/tenant/erp-1/delete') >= 0);
    assert.deepEqual(JSON.parse(write.init.body),
        { expected_version: 3, default_handling: { action: 'replace', connection_id: 'erp-3' } });

    // 2. A live reference refuses the delete with the server's summary, once.
    deleteAnswer = {
        status: 409,
        json: {
            error: {
                code: 'referenced', message: 'in use',
                references: [{ kind: 'erp_default' }, { kind: 'override', count: 2 }],
            },
            request_id: 'req-2',
        },
    };
    app.page.confirmDelete(cardFor('erp-1'));
    app.fire(app.panelEl('[data-ec-action="modal-confirm"]'), 'click');
    await flush();
    assert.equal(app.fetchCalls.filter(c => c.url.indexOf('/delete') >= 0).length, 2,
        'a refused delete is not retried automatically');
    const refusal = app.panel().innerHTML;
    assert.ok(refusal.indexOf(zh.ec_referenced_title) >= 0);
    assert.ok(refusal.indexOf(zh.ec_reference_kind_erp_default) >= 0);
    assert.ok(refusal.indexOf(zh.ec_reference_kind_override + ' × 2') >= 0);

    // 3. Enable / disable. Turning one on is immediate; disabling confirms first.
    app.page.toggleConnection(cardFor('erp-2'));
    await flush();
    assert.deepEqual(JSON.parse(lastWrite(app).init.body), { expected_version: 4, enabled: true });
    app.page.toggleConnection(cardFor('erp-1'));
    assert.ok(app.panel().innerHTML.indexOf(zh.ec_confirm_disable_title) >= 0);
    app.fire(app.panelEl('[data-ec-action="modal-confirm"]'), 'click');
    await flush();
    // Disabling the default also says what happens to the pointer.
    assert.deepEqual(JSON.parse(lastWrite(app).init.body),
        { expected_version: 3, enabled: false, default_handling: { action: 'clear' } });

    // 4. The ERP default pointer moves with its CAS revision, and clears explicitly.
    app.page.setErpDefault(cardFor('erp-1'), false);
    app.fire(app.panelEl('[data-ec-action="modal-confirm"]'), 'click');
    await flush();
    write = lastWrite(app);
    assert.ok(write.url.indexOf('/api/external-connections/tenant/erp-default') >= 0);
    assert.deepEqual(JSON.parse(write.init.body), { connection_id: 'erp-1', expected_revision: 7 });
    app.page.setErpDefault(cardFor('erp-1'), true);
    app.fire(app.panelEl('[data-ec-action="modal-confirm"]'), 'click');
    await flush();
    assert.deepEqual(JSON.parse(lastWrite(app).init.body), { connection_id: null, expected_revision: 7 });

    // 5. Dropping a tenant override goes through the platform connection it overrides.
    app.page.restoreInheritance(cardFor('plat-1'));
    app.fire(app.panelEl('[data-ec-action="modal-confirm"]'), 'click');
    await flush();
    write = lastWrite(app);
    assert.ok(write.url.indexOf('/api/external-connections/tenant/ovr-9/restore-inheritance') >= 0);
    assert.deepEqual(JSON.parse(write.init.body), { expected_version: 5 });

    // 6. Tenant access lists only the tenants that are actually enabled, and
    //    saves with the revision the read returned.
    await app.page.openTenantAccess(cardFor('plat-1'));
    await flush();
    const area = app.panelEl('#ec-tenant-access-input');
    assert.equal(area.value, 't1', 'a disabled grant is not presented as access');
    area.value = 't1\nt3';
    app.fire(area, 'input');
    app.fire(app.panelEl('[data-ec-action="tenant-access-save"]'), 'click');
    await flush();
    write = lastWrite(app);
    assert.ok(write.url.indexOf('/api/external-connections/platform/plat-1/tenant-access') >= 0);
    assert.deepEqual(JSON.parse(write.init.body), { tenant_ids: ['t1', 't3'], expected_revision: 3 });
});

// =====================================================================
// 6. Accessibility / layout conventions
// =====================================================================
test('the page keeps 390px, large-font and theme conventions', () => {
    assert.match(css, /@media \(max-width: 420px\)/);
    assert.match(css, /\.ec-drawer-panel \{ width: 100%; \}/);
    // Grids collapse instead of overflowing.
    assert.match(css, /minmax\(min\(22rem, 100%\), 1fr\)/);
    assert.match(css, /minmax\(min\(13rem, 100%\), 1fr\)/);
    // Dark theme coverage for the page surfaces.
    assert.match(css, /\.dark \.ec-card \{/);
    assert.match(css, /\.dark \.ec-drawer-panel \{/);
    // Keyboard reachability: focus rings on the interactive controls.
    assert.match(css, /\.ec-btn:focus-visible/);
    assert.match(css, /\.ec-chip:focus-visible/);
    assert.match(css, /\.ec-input:focus/);
    // Reduced-motion-free, but the drawer is a dialog with a focus trap.
    assert.match(pageJs, /setAttribute\('role', 'dialog'\)/);
    assert.match(pageJs, /function ecTrapFocus/);
    assert.match(pageJs, /function ecRestoreFocus/);
});
