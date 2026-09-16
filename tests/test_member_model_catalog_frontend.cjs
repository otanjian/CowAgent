// 模型与接入 is one page with two shapes, and the console must pick the right one
// from the *server's* answer (task 5.4).
//
// `actions.manage` is the platform qualification alone — what `/config` and
// `/api/models` gate on — and the authorized catalog is the grant-filtered
// endpoint the page's own `read_allowed` is computed from. Before this, a member
// who could reach the page (because their menu granted it) was shown the vendor
// and key management tabs, whose every request the server refuses.
//
// So the two assertions that matter here are a pair: a caller without the
// management action gets the catalog and *no* management request is issued at
// all, and a caller with it keeps the management surface unchanged. Either one
// alone would pass on "always show the catalog" / "always show the tabs".
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(
    path.join(__dirname, '../channel/web/static/js/console.js'), 'utf8');

function fnSource(name) {
    const head = `function ${name}(`;
    const from = source.indexOf(head);
    assert.ok(from >= 0, `Missing ${name}`);
    let depth = 0;
    for (let i = source.indexOf('{', from); i < source.length; i++) {
        if (source[i] === '{') depth++;
        else if (source[i] === '}') {
            depth--;
            if (depth === 0) return source.slice(from, i + 1);
        }
    }
    throw new Error(`Unbalanced ${name}`);
}

/** A minimal element stub: enough for innerHTML / classList / appendChild. */
function element() {
    const el = {
        innerHTML: '',
        className: '',
        children: [],
        classes: new Set(),
        appendChild(child) { el.children.push(child); return child; },
    };
    el.classList = {
        add: (name) => el.classes.add(name),
        remove: (name) => el.classes.delete(name),
        toggle: (name, on) => (on ? el.classes.add(name) : el.classes.delete(name)),
        contains: (name) => el.classes.has(name),
    };
    return el;
}

function sandbox(overrides) {
    const base = {
        console,
        currentLang: 'en',
        // The real keys are asserted for existence by the i18n parity test; here
        // the key name is the value so the markup says which key it used.
        t: (key) => key,
        escapeHtml: (value) => String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;'),
        document: {
            createElement: () => element(),
            getElementById: () => null,
        },
        fetch: async () => ({ status: 200, json: async () => ({ status: 'success' }) }),
        // Module-level state the catalog loaders read and write.
        memberCatalogState: { items: [] },
    };
    return Object.assign(base, overrides || {});
}

function run(sandbox_, ...names) {
    vm.runInNewContext(names.map(fnSource).join('\n'), sandbox_);
    return sandbox_;
}

/** A document whose elements are the ones this test wants to observe. */
function documentWith(elements) {
    return {
        createElement: () => element(),
        getElementById: (id) => elements[id] || null,
    };
}

// A resolved projection for a caller who may maintain the public model service.
function manageContext(manage) {
    return {
        status: 'success',
        console_pages: { 'admin.models': { available: true, read_allowed: true,
                                           actions: { manage } } },
    };
}

// ---------- the entry shape -------------------------------------------

test('a caller without the management action gets the catalog, not the tabs', () => {
    const tabs = element();
    const calls = [];
    const box = run(sandbox({
        _modelsManageAllowed: () => false,
        document: documentWith({ 'config-tabs': tabs }),
        switchConfigTab: (tab) => calls.push(['tab', tab]),
        loadConfigView: () => calls.push(['config']),
        loadMemberCatalog: () => calls.push(['catalog']),
    }), 'enterConfigView');

    box.enterConfigView();

    assert.deepEqual(calls, [['tab', 'catalog']],
        'the catalog is the page, and no management request is issued');
    assert.ok(tabs.classes.has('hidden'),
        'the management tabs must not be advertised');
});

test('a caller with the management action gets the page unchanged', () => {
    const tabs = element();
    const calls = [];
    const box = run(sandbox({
        _modelsManageAllowed: () => true,
        document: documentWith({ 'config-tabs': tabs }),
        switchConfigTab: (tab) => calls.push(['tab', tab]),
        loadConfigView: () => calls.push(['config']),
        loadMemberCatalog: () => calls.push(['catalog']),
    }), 'enterConfigView');

    box.enterConfigView();

    assert.deepEqual(calls, [['config'], ['tab', 'basic']]);
    assert.ok(!tabs.classes.has('hidden'));
});

test('the management action is read from the projection, not guessed', () => {
    const cases = [
        // [identity mode, projection, platform admin, expected]
        ['database', manageContext(true), false, true],
        ['database', manageContext(false), false, false],
        // Undecided projection (a deep link can beat /auth/context): the
        // platform qualification is the same fact the server computes
        // `actions.manage` from, so it cannot disagree.
        ['database', null, true, true],
        ['database', null, false, false],
        // Legacy mode has no projection and no member catalog: the config page
        // is the single unrestricted surface it always was.
        ['legacy', null, false, true],
    ];
    for (const [mode, ctx, isPlatformAdmin, expected] of cases) {
        const box = run(sandbox({
            _identityMode: () => mode,
            _baseAuthContext: () => ctx,
            _baseAccountSelf: () => ({ user: { is_platform_admin: isPlatformAdmin } }),
            CONFIG_VIEW_CONSOLE_PAGE: 'admin.models',
        }), '_modelsManageAllowed');
        assert.equal(box._modelsManageAllowed(), expected,
            `${mode} / admin=${isPlatformAdmin} / projection=${!!ctx}`);
    }
});

// ---------- the catalog itself ----------------------------------------

const CATALOG = {
    status: 'success',
    items: [
        { resource_id: 'provider:deepseek:deepseek-v4-flash',
          name: 'deepseek-v4-flash', capability: 'chat' },
        { resource_id: 'provider:openai:gpt-4o', name: 'gpt-4o', capability: 'chat' },
    ],
};

function catalogBox(fetchImpl, payload) {
    const loading = element();
    const content = element();
    const box = run(sandbox({
        fetch: fetchImpl,
        document: documentWith({ 'catalog-loading': loading,
                                 'catalog-content': content }),
    }), 'loadMemberCatalog', 'renderMemberCatalog', 'renderMemberCatalogRow');
    box.__loading = loading;
    box.__content = content;
    return box;
}

// `loadMemberCatalog` renders from a promise chain rather than returning one, so
// the test has to let the chain settle: the assertion is about what the page
// shows after the fetch resolves, which is exactly what the console does.
function settle() {
    return new Promise(resolve => setImmediate(resolve));
}

test('the catalog asks the grant-filtered endpoint, not the vendor grid', async () => {
    const seen = [];
    const box = catalogBox(async (url) => {
        seen.push(url);
        return { json: async () => CATALOG };
    });

    box.loadMemberCatalog();
    await settle();

    assert.deepEqual(seen,
        ['/api/tenant/authorization/catalog?kind=model&purpose=use&page_size=200'],
        'the page must read the member catalog, never /api/models');
    const html = box.__content.children.map(c => c.innerHTML).join('\n');
    assert.match(html, /deepseek-v4-flash/, 'rows come from the catalog');
    assert.match(html, /openai/, 'and the provider is labelled');
});

test('an unauthorized caller is told so rather than shown an empty page', async () => {
    const box = catalogBox(async () => ({ json: async () => ({ status: 'success', items: [] }) }));

    box.loadMemberCatalog();
    await settle();

    assert.match(box.__content.children.map(c => c.innerHTML).join(''),
                 /models_catalog_empty/);
});

test('a catalog row escapes what the catalog says', () => {
    const box = run(sandbox(), 'renderMemberCatalogRow');
    const html = box.renderMemberCatalogRow({
        resource_id: 'provider:<img src=x>:"onmouseover=1',
        name: '<img src=x onerror=alert(1)>',
    });
    assert.ok(!/<img/.test(html), `unescaped markup leaked into the row: ${html}`);
    assert.match(html, /&lt;img/);
});

test('the tab switch loads exactly the tab that was opened', () => {
    const calls = [];
    const panels = {};
    const tabs = {};
    const elements = {};
    for (const name of ['basic', 'models', 'catalog']) {
        panels[name] = element();
        tabs[name] = element();
        elements[`config-panel-${name}`] = panels[name];
        elements[`config-tab-${name}`] = tabs[name];
    }
    const box = run(sandbox({
        document: documentWith(elements),
        loadModelsView: () => calls.push('models'),
        loadMemberCatalog: () => calls.push('catalog'),
        loadConfigView: () => calls.push('config'),
    }), 'switchConfigTab');

    box.switchConfigTab('catalog');

    assert.deepEqual(calls, ['catalog'],
        'opening the catalog must not trigger a management load');
    assert.ok(!panels.catalog.classes.has('hidden'));
    assert.ok(panels.basic.classes.has('hidden'));
    assert.ok(panels.models.classes.has('hidden'));
    assert.ok(tabs.catalog.classes.has('active'));
});
