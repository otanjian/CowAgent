// Shared channel presentation contract (change upgrade-personal-channel-workbench,
// task 3.1).
//
// channel-workbench.js owns the channel card, the type icon, the credential
// fields and the 扫码 / 手工 mode strip once, because the public 消息渠道 console
// and the member workbench show the same objects from two different request
// contexts. The two things that make that safe — and that these tests pin — are
// the ones the design decision names:
//
//   1. the markup is *namespaced*: every attribute and pane id carries a caller
//      chosen prefix, so the personal form can neither be found nor overwritten
//      by the public form's collectors;
//   2. the builders read *no ambient state*: escaping, translation, language and
//      the scan tables all arrive as arguments (with a documented page-global
//      fallback for the browser), and nothing here fetches or touches a draft.
//
// console.js's own contract tests (test_tenant_channel_*_frontend.cjs) keep
// asserting the rendered public markup through the delegations; this file asserts
// the module those delegations land in.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const { moduleSource, SOURCE_PATH } = require('./support/channel_workbench.cjs');
const { loadDictionaries } = require('./support/i18n_namespaces.cjs');

const source = fs.readFileSync(SOURCE_PATH, 'utf8');
const chatHtml = fs.readFileSync(
    path.join(__dirname, '../channel/web/chat.html'), 'utf8');

// Evaluate the module with exactly the globals a caller wants it to see. The
// sandbox deliberately gets no `escapeHtml` / `t` / `currentLang` unless a test
// asks for them: the point is that the module works without them.
function boot(globals = {}) {
    const sandbox = Object.assign({ console }, globals);
    if (!sandbox.window) sandbox.window = {};
    vm.runInNewContext(moduleSource(), sandbox);
    assert.ok(sandbox.window.ChannelWorkbench,
        'the module must publish window.ChannelWorkbench for console.js to delegate to');
    return sandbox.window.ChannelWorkbench;
}

// The published surface — the names console.js delegates to and the ones the
// member workbench is coded against. A rename here would silently break a
// consumer at runtime, so the surface is pinned rather than assumed.
const API = [
    'fieldAttr', 'requiredAttr', 'heldAttr',
    'typeSpec', 'typeLabel', 'fieldLabel', 'appearance', 'typeOptions',
    'agentOptions', 'supportsScan', 'hasScanCopy', 'scanReady', 'scanCopy',
    'scanStartFor', 'autoName', 'missingRequiredFields', 'collectFields', 'payload',
    'credentialDescriptors',
    'cardShell', 'fieldInput', 'fieldsHtml', 'modeTabs', 'scanPane', 'manualPane',
    'applyMode', 'bindModeSwitch',
];

const FIELD = {
    key: 'feishu_app_secret',
    label: { zh: 'App Secret', en: 'App Secret' },
    secret: true,
    required: true,
};

const PLAIN_FIELD = {
    key: 'feishu_app_id',
    label: { zh: 'App ID', en: 'App ID' },
    secret: false,
    required: true,
};

// A minimal stand-in for a DOM root: `querySelectorAll('[data-x-field]')` returns
// only the elements that actually carry that attribute, and `getAttribute` reads
// it back — which is what makes the prefix scoping observable.
function fakeRoot(entries) {
    return {
        querySelectorAll(selector) {
            const m = /^\[([^\]]+)\]$/.exec(selector);
            const attr = m ? m[1] : null;
            return entries.filter(e => attr && e.attrs[attr] !== undefined);
        },
    };
}

function element(kind, key, value) {
    const attrs = {};
    attrs['data-' + kind + '-field'] = key;
    return {
        attrs,
        value,
        getAttribute: (name) => (name in attrs ? attrs[name] : null),
    };
}

// --- the interface console.js delegates to --------------------------------

test('the module publishes every presentation function the controllers delegate', () => {
    const api = boot();
    for (const name of API) {
        assert.equal(typeof api[name], 'function', `${name} is missing from the module`);
    }
});

test('the recorded attribute helpers agree with what the builders emit', () => {
    const api = boot();
    assert.equal(api.fieldAttr('tenant-channel'), 'data-tenant-channel-field');
    assert.equal(api.requiredAttr('tenant-channel'), 'data-tenant-channel-required');
    assert.equal(api.heldAttr('tenant-channel'), 'data-tenant-channel-held');
    assert.equal(api.fieldAttr('personal-channel'), 'data-personal-channel-field');
});

// --- the prefix really isolates the two surfaces --------------------------

test('one credential field renders under two prefixes without a shared attribute', () => {
    const api = boot();
    const tenant = api.fieldInput({ field: FIELD, value: 'typed', prefix: 'tenant-channel' });
    const personal = api.fieldInput({ field: FIELD, value: 'typed', prefix: 'personal-channel' });

    assert.notEqual(tenant, personal, 'the two surfaces rendered the same markup');
    assert.match(tenant, /data-tenant-channel-field="feishu_app_secret"/);
    assert.match(tenant, /data-tenant-channel-required="1"/);
    assert.match(tenant, /data-tenant-channel-held="feishu_app_secret"/);
    assert.match(personal, /data-personal-channel-field="feishu_app_secret"/);
    assert.match(personal, /data-personal-channel-required="1"/);
    assert.match(personal, /data-personal-channel-held="feishu_app_secret"/);

    // The whole point: neither surface can be collected by the other's selector.
    assert.doesNotMatch(tenant, /personal-channel/,
        'the public form carries an attribute the personal collector reads');
    assert.doesNotMatch(personal, /tenant-channel/,
        'the personal form carries an attribute the public collector reads');
});

test('the mode strip and the panes carry the prefix on every id', () => {
    const api = boot();
    const copy = { tab: 'feishu_mode_scan', manualTab: 'feishu_mode_manual',
                   desc: 'feishu_scan_desc', btn: 'feishu_scan_btn' };
    const opts = { iid: 'i7', mode: 'manual', supportsScan: true, copy };

    const tenantTabs = api.modeTabs(Object.assign({ prefix: 'tenant-channel' }, opts));
    const personalTabs = api.modeTabs(Object.assign({ prefix: 'personal-channel' }, opts));
    assert.match(tenantTabs, /data-tenant-channel-mode="scan"/);
    assert.match(tenantTabs, /data-tenant-channel-mode="manual"/);
    assert.match(personalTabs, /data-personal-channel-mode="scan"/);
    assert.doesNotMatch(personalTabs, /data-tenant-channel-mode/);

    const tenantManual = api.manualPane(Object.assign({ prefix: 'tenant-channel' }, opts));
    const personalManual = api.manualPane(Object.assign({ prefix: 'personal-channel' }, opts));
    assert.match(tenantManual, /id="tenant-channel-pane-manual-i7"/);
    assert.match(tenantManual, /id="tenant-channel-fields"/);
    assert.match(personalManual, /id="personal-channel-pane-manual-i7"/);
    assert.doesNotMatch(personalManual, /tenant-channel/);

    const tenantScan = api.scanPane(Object.assign({ prefix: 'tenant-channel' }, opts));
    const personalScan = api.scanPane(Object.assign({ prefix: 'personal-channel' }, opts));
    assert.match(tenantScan, /id="tenant-channel-pane-scan-i7"/);
    assert.match(personalScan, /id="personal-channel-pane-scan-i7"/);
    // The status element the start function writes into is namespaced too, so
    // two live scans cannot report into each other.
    assert.match(tenantScan, /id="tenant-channel-scan-status-i7"/);
    assert.match(personalScan, /id="personal-channel-scan-status-i7"/);
});

test('a collector only sees its own prefix', () => {
    const api = boot();
    const root = fakeRoot([
        element('tenant-channel', 'feishu_app_id', 'cli_public'),
        element('personal-channel', 'agent_id', 'agent-private'),
    ]);
    assert.deepEqual(Object.keys(api.collectFields(root, 'tenant-channel')),
        ['feishu_app_id']);
    assert.equal(api.collectFields(root, 'tenant-channel').feishu_app_id, 'cli_public');
    // Cross-realm object: compare contents, not the prototype.
    assert.deepEqual(Object.keys(api.collectFields(root, 'personal-channel')), ['agent_id']);
    assert.equal(api.collectFields(root, 'personal-channel').agent_id, 'agent-private');
});

test('applyMode toggles only the prefixed panes of the instance it was given', () => {
    const api = boot();
    const toggled = [];
    const make = (id) => ({
        id,
        classList: { toggle: (cls, on) => toggled.push([id, cls, on]) },
    });
    const nodes = {
        'tenant-channel-pane-scan-a': make('tenant-channel-pane-scan-a'),
        'tenant-channel-pane-manual-a': make('tenant-channel-pane-manual-a'),
        'tenant-channel-pane-scan-b': make('tenant-channel-pane-scan-b'),
        'tenant-channel-pane-manual-b': make('tenant-channel-pane-manual-b'),
        'personal-channel-pane-scan-a': make('personal-channel-pane-scan-a'),
        'personal-channel-pane-manual-a': make('personal-channel-pane-manual-a'),
    };
    const doc = { getElementById: (id) => nodes[id] || null };

    api.applyMode(doc, 'tenant-channel', 'a', 'scan');
    assert.deepEqual(toggled, [
        ['tenant-channel-pane-scan-a', 'hidden', false],
        ['tenant-channel-pane-manual-a', 'hidden', true],
    ], 'another instance or prefix was touched');
});

// --- the builders read no ambient state -----------------------------------

test('every markup builder works with no escape/t/lang globals at all', () => {
    // A sandbox with no escapeHtml, no t and no currentLang: a builder that
    // reaches for a page global instead of its option would throw a
    // ReferenceError here.
    const api = boot();
    const copy = { tab: 'feishu_mode_scan', manualTab: 'feishu_mode_manual',
                   desc: 'feishu_scan_desc', btn: 'feishu_scan_btn' };

    const card = api.cardShell({ iid: 'x1', label: 'Support Bot' });
    assert.match(card, /Support Bot/);

    const input = api.fieldInput({ field: PLAIN_FIELD, value: 'cli_abc' });
    assert.match(input, /data-tenant-channel-field="feishu_app_id"/,
        'the public prefix must be the documented default');
    assert.match(input, /value="cli_abc"/);

    const fields = api.fieldsHtml({ fields: [PLAIN_FIELD], values: { feishu_app_id: 'v' } });
    assert.match(fields, /App ID/);

    const tabs = api.modeTabs({ iid: 'x1', mode: 'scan', supportsScan: true, copy });
    assert.match(tabs, /data-tenant-channel-mode="scan"/);

    const scan = api.scanPane({ iid: 'x1', mode: 'scan', supportsScan: true,
                                copy, startCall: 'startFeishuRegister' });
    assert.match(scan, /onclick="startFeishuRegister\('tenant-channel-scan-status-x1', 'x1'\)"/);

    const manual = api.manualPane({ iid: 'x1', mode: 'manual', fields: [PLAIN_FIELD], values: {} });
    assert.match(manual, /id="tenant-channel-pane-manual-x1"/);
});

test('the injected escape and t win over the page globals', () => {
    // The globals are poisoned: if a builder consulted them the call would throw,
    // which is how a silent fallback to the wrong surface's translation would be
    // caught.
    const explode = () => { throw new Error('a page global was consulted'); };
    const api = boot({ escapeHtml: explode, t: explode, currentLang: 'nonsense' });
    const copy = { tab: 'feishu_mode_scan', manualTab: 'feishu_mode_manual',
                   desc: 'feishu_scan_desc', btn: 'feishu_scan_btn' };
    const escape = (v) => `[${v}]`;
    const t = (k) => `«${k}»`;
    const injected = { escape, t, lang: 'en', prefix: 'personal-channel' };

    assert.match(api.cardShell(Object.assign({ iid: 'x', label: 'L' }, injected)), /\[L\]/);
    assert.match(api.fieldInput(Object.assign({ field: PLAIN_FIELD, value: 'v' }, injected)),
        /placeholder="\[App ID\]"/);
    assert.match(api.modeTabs(Object.assign({ iid: 'x', mode: 'scan', supportsScan: true,
        copy, switchCall: () => 'go()' }, injected)), /«feishu_mode_scan»/);
    assert.match(api.scanPane(Object.assign({ iid: 'x', mode: 'scan', supportsScan: true,
        copy, startCall: 'go' }, injected)), /«feishu_scan_desc»/);
    assert.match(api.manualPane(Object.assign({ iid: 'x', mode: 'manual',
        fields: [PLAIN_FIELD], values: {} }, injected)), /«tenant_channel_secret_note»/);
});

test('the module never fetches, stores, or reaches for a controller draft', () => {
    // A request or a draft in here would be exactly the coupling the design
    // decision forbids: the module must not know which verb is allowed or which
    // surface's list it is rendering.
    const code = source
        .replace(/\/\*[\s\S]*?\*\//g, '')   // block comments (the design notes)
        .replace(/^\s*\/\/.*$/gm, '');      // line comments
    for (const forbidden of ['fetch(', 'XMLHttpRequest', 'localStorage',
        'sessionStorage', 'tenantChannelDraft', 'agentCatalog', 'channelScope(',
        'tenantChannelTypes', 'tenantChannelInstances', 'personalChannelDraft']) {
        assert.ok(!code.includes(forbidden),
            `the shared module must not touch ${forbidden}`);
    }
});

// --- the scan gating ------------------------------------------------------

test('a type without scan copy gets the manual entry alone', () => {
    const api = boot();
    const notReady = { tab: 'feishu_mode_scan', manualTab: 'feishu_mode_manual',
                       desc: 'feishu_scan_desc', btn: 'feishu_scan_btn' };
    // supportsScan false and copy null are both "no scan entry".
    const noSupport = api.modeTabs({ iid: 'x', mode: 'manual', supportsScan: false, copy: notReady });
    assert.doesNotMatch(noSupport, /data-tenant-channel-mode="scan"/);
    assert.match(noSupport, /data-tenant-channel-mode="manual"/);

    const noCopy = api.modeTabs({ iid: 'x', mode: 'manual', supportsScan: true, copy: null });
    assert.doesNotMatch(noCopy, /data-tenant-channel-mode="scan"/);
    assert.match(noCopy, /data-tenant-channel-mode="manual"/);

    assert.equal(api.scanPane({ iid: 'x', supportsScan: false, copy: notReady }), '');
    assert.equal(api.scanPane({ iid: 'x', supportsScan: true, copy: null }), '');
});

test('a start function is offered only when both tables know the type', () => {
    const api = boot();
    const start = { feishu: 'startFeishuRegister' };
    const copy = { feishu: { tab: 'a', manualTab: 'b', desc: 'c', btn: 'd' } };
    assert.equal(api.supportsScan(start, 'feishu'), true);
    assert.equal(api.hasScanCopy(copy, 'feishu'), true);
    assert.equal(api.scanReady(start, copy, 'feishu'), true);
    assert.equal(api.scanStartFor(start, 'feishu'), 'startFeishuRegister');
    assert.equal(api.scanCopy(copy, 'feishu').btn, 'd');

    // Half-registered: a start function with no copy is not offered at all, so a
    // type can never be rendered with another type's wording.
    assert.equal(api.scanReady(start, {}, 'feishu'), false);
    assert.equal(api.scanReady({}, copy, 'feishu'), false);
    assert.equal(api.scanReady(start, copy, 'wechatcom_app'), false);
});

// --- the descriptor helpers ----------------------------------------------

test('a credential field is described from the server declaration, not a local list', () => {
    const api = boot();
    const spec = { channel_type: 'feishu', label: { zh: '飞书', en: 'Feishu' } };
    assert.equal(api.typeSpec([spec], 'feishu'), spec);
    assert.equal(api.typeSpec([spec], 'nope'), null);
    assert.equal(api.typeSpec(null, 'feishu'), null);
    assert.equal(api.typeLabel(spec, 'feishu', 'en'), 'Feishu');
    // A type the deployment does not describe keeps its identifier rather than
    // borrowing another type's name.
    assert.equal(api.typeLabel(null, 'nope', 'zh'), 'nope');
    assert.equal(api.fieldLabel({ key: 'k', label: { en: 'K' } }, 'zh'), 'K');
    assert.equal(api.fieldLabel({ key: 'k' }, 'zh'), 'k');

    // Cross-realm object: compare the fields, not the prototype.
    const appearance = api.appearance({ icon: 'fa-x', color: 'blue' });
    assert.equal(appearance.icon, 'fa-x');
    assert.equal(appearance.color, 'blue');
    const fallback = api.appearance(null);
    assert.equal(fallback.icon, api.DEFAULT_ICON);
    assert.equal(fallback.color, api.DEFAULT_COLOR);
});

test('a secret field is never rendered with a value, but says it is held', () => {
    const api = boot();
    const blank = api.fieldInput({ field: FIELD, value: '', prefix: 'tenant-channel' });
    assert.match(blank, /type="password"/);
    assert.match(blank, /value=""/);
    assert.doesNotMatch(blank, /data-tenant-channel-held/);

    const held = api.fieldInput({ field: FIELD, value: 'scanned-secret', prefix: 'tenant-channel' });
    assert.match(held, /value=""/, 'a secret must never be pre-filled');
    assert.doesNotMatch(held, /scanned-secret/, 'the scanned plaintext leaked into the DOM');
    assert.match(held, /data-tenant-channel-held="feishu_app_secret"/,
        'the operator cannot tell the scan arrived');
});

test('required-ness and the auto name come from the declared fields', () => {
    const api = boot();
    const fields = [
        { key: 'feishu_app_id', required: true },
        { key: 'feishu_app_secret', required: true },
        { key: 'note', required: false },
    ];
    assert.deepEqual(
        api.missingRequiredFields(fields, { feishu_app_id: 'cli_x' }).map(f => f.key),
        ['feishu_app_secret']);
    assert.deepEqual(
        api.missingRequiredFields(fields, { feishu_app_id: 'x', feishu_app_secret: '  ' }).map(f => f.key),
        ['feishu_app_secret'], 'whitespace is not a value');
    // `null` fields is "nothing declared", not "everything missing" (the
    // realm-crossing empty array makes a length check clearer).
    assert.equal(api.missingRequiredFields(null, {}).length, 0);

    const spec = { label: { zh: '飞书', en: 'Feishu' }, credential_fields: fields };
    assert.equal(api.autoName(spec, 'feishu', { feishu_app_id: 'cli_aa281fe031f85cda' }, 'zh'),
        '飞书 · 5cda');
    assert.equal(api.autoName(spec, 'feishu', {}, 'zh'), '飞书');
});

test('the payload only carries the keys the caller supplied', () => {
    const api = boot();
    const body = api.payload({
        display_name: 'D', agent_id: 'a', expected_version: 2,
        credentials: { feishu_app_id: 'x' }, recent_password: 'pw', scan_ticket: '',
    });
    assert.deepEqual(Object.keys(body).sort(),
        ['agent_id', 'credentials', 'display_name', 'expected_version', 'recent_password']);
    assert.equal(body.recent_password, 'pw');
    assert.ok(!('scan_ticket' in body), 'an empty grant must stay absent, not be sent as ""');

    // An edit that retyped nothing must not blank the stored bundle.
    const edit = api.payload({ display_name: 'D', credentials: undefined });
    assert.ok(!('credentials' in edit));
    assert.equal(api.payload({}).recent_password, '');
    assert.equal(api.payload({ display_name: 'D', scan_ticket: 'g' }).scan_ticket, 'g');
});

// --- the page actually loads it -------------------------------------------

test('chat.html loads the shared module after fragments and before every consumer', () => {
    const at = (needle) => {
        const i = chatHtml.indexOf(needle);
        assert.ok(i >= 0, `${needle} is not loaded`);
        return i;
    };
    const moduleAt = at('assets/js/channel-workbench.js');
    assert.ok(moduleAt > at('assets/js/fragments.js'),
        'the module must load after the fragment mount');
    assert.ok(moduleAt < at('assets/js/console.js'),
        'console.js delegates to the module at call time, so it must load first');
    // The member personal console used to be the second consumer. It is retired
    // (task 8.8), so the tag is gone — asserting the absence keeps a re-added
    // loader from silently reintroducing a deleted module, while the module
    // itself stays: console.js is still the live consumer, and both its channel
    // card and the tenant console render through it.
    assert.ok(chatHtml.indexOf('assets/js/personal-console.js') < 0,
        'the retired personal console must not be loaded again');
    // Exactly one tag: a second one would re-register the API and hide the fact
    // that a consumer is loading a different copy.
    assert.equal(chatHtml.split('assets/js/channel-workbench.js').length - 1, 1);
});

test('every translation key the module owns exists in all three languages', () => {
    // The module renders its own copy through a key (the scan copy is supplied by
    // the caller), and a missing key renders as the raw key name. The keys are
    // read out of the source rather than listed here, so a new one cannot be
    // added without a translation.
    const dicts = loadDictionaries();
    const keys = new Set();
    const re = /'(tenant_channel_[a-z_]+)'/g;
    let m;
    while ((m = re.exec(source))) keys.add(m[1]);
    assert.ok(keys.size >= 2, 'the scan found the module-owned keys');
    for (const key of keys) {
        for (const lang of ['zh', 'zh-Hant', 'en']) {
            assert.ok(Object.prototype.hasOwnProperty.call(dicts[lang] || {}, key),
                `${key} is used by the shared module but missing from ${lang}`);
        }
    }
    // The exported defaults are the tenant console's wording, so the public path
    // renders exactly what it always did.
    const api = boot();
    assert.equal(api.HELD_HINT_KEY, 'tenant_channel_held_hint');
    assert.equal(api.SECRET_NOTE_KEY, 'tenant_channel_secret_note');
    assert.match(api.fieldInput({ field: FIELD, value: 'held', prefix: 'tenant-channel',
        t: (k) => dicts.zh[k] || k }),
        new RegExp(dicts.zh[api.HELD_HINT_KEY]));
    assert.match(api.manualPane({ iid: 'x', mode: 'manual', fields: [], values: {},
        t: (k) => dicts.zh[k] || k }),
        new RegExp(dicts.zh[api.SECRET_NOTE_KEY]));
});
