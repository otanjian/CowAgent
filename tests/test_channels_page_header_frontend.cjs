// The 消息渠道 page paints exactly ONE header for both scopes.
//
// The page is a single console view serving a platform scope and a tenant
// scope. The static markup in chat.html already carries the title, the
// description and the "接入通道" button, so the tenant list render must NOT
// paint a second copy into #channels-content — that is the "two 接入通道 cards"
// defect. Only the description and the button's entry point differ by scope,
// and those are settled by syncChannelsHeader / openChannelsAddEntry.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(
    path.join(__dirname, '../channel/web/static/js/console.js'), 'utf8');
const html = fs.readFileSync(
    path.join(__dirname, '../channel/web/chat.html'), 'utf8');

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

// A minimal element the header helpers actually touch.
function makeEl() {
    return { textContent: '', dataset: {}, onclick: null };
}

function boot({ scope = 'platform', elements = {} } = {}) {
    const calls = [];
    const sandbox = {
        console,
        currentLang: 'zh',
        I18N: {
            zh: {
                channels_title: '消息渠道',
                channels_desc: '管理已接入的消息通道',
                tenant_channel_desc: '配置本租户的消息渠道，凭据加密存储且不会回显',
            },
            en: {},
        },
        document: { getElementById: (id) => elements[id] || null },
        _baseAuthContext: () => ({
            console_pages: { 'admin.channels': { scope } },
        }),
        openTenantChannelForm: () => { calls.push('tenant'); },
        openAddChannelPanel: () => { calls.push('platform'); },
    };
    sandbox.t = (key) => (sandbox.I18N[sandbox.currentLang] || {})[key] || key;
    vm.runInNewContext(
        ['channelScope', 'syncChannelsHeader', 'openChannelsAddEntry']
            .map(fnSource).join('\n'),
        sandbox);
    return { sandbox, calls, elements };
}

// --- the duplicate header -------------------------------------------------

test('the tenant channel list does not paint a second page header', () => {
    const body = source.slice(
        source.indexOf('function renderTenantChannels('),
        source.indexOf('function openTenantChannelForm('));
    assert.ok(body.length > 0, 'renderTenantChannels is missing');
    // The page header lives only in chat.html; a second <h2>/title inside the
    // list container is the duplicate card operators see.
    assert.doesNotMatch(body, /<h2/,
        'the tenant list paints a duplicate page header');
    assert.doesNotMatch(body, /channels_title/,
        'the tenant list duplicates the page title');
});

test('the page header markup exists once, in chat.html', () => {
    assert.match(html, /id="channels-subtitle"/,
        'the scope-aware description has no target');
    assert.match(html, /id="add-channel-btn"/);
    // The single title/description pair must not be duplicated in the view.
    assert.equal((html.match(/channels_title/g) || []).length, 1,
        'the 消息渠道 view declares its title more than once');
});

// --- the one header follows the scope -------------------------------------

test('the page description follows the console scope', () => {
    const elements = { 'channels-subtitle': makeEl() };
    const { sandbox } = boot({ scope: 'tenant', elements });

    sandbox.syncChannelsHeader('tenant');
    assert.equal(elements['channels-subtitle'].textContent,
        '配置本租户的消息渠道，凭据加密存储且不会回显');
    // data-i18n must move with it, or a language switch reverts to the platform
    // description.
    assert.equal(elements['channels-subtitle'].dataset.i18n, 'tenant_channel_desc');

    sandbox.syncChannelsHeader('platform');
    assert.equal(elements['channels-subtitle'].textContent, '管理已接入的消息通道');
    assert.equal(elements['channels-subtitle'].dataset.i18n, 'channels_desc');
});

test('the scope is synced by the one entry point every nav path uses', () => {
    const body = source.slice(
        source.indexOf('function loadChannelsView('),
        source.indexOf('function channelRenderList('));
    // loadChannelsView is the entry point every nav path calls; it must settle
    // the header from the resolved scope before dispatching to either list.
    assert.match(body, /syncChannelsHeader\(/,
        'loadChannelsView must settle the header before dispatching');
    assert.match(body, /channelScope\(\)/,
        'the header must follow the resolved scope');
    const syncAt = body.indexOf('syncChannelsHeader(');
    const dispatchAt = body.indexOf('loadTenantChannelsView()');
    assert.ok(syncAt >= 0 && dispatchAt > syncAt,
        'the header must be settled before the tenant list loads');
});

// --- the single button reaches the right form ------------------------------

test('the header add button dispatches to the scope it is shown in', () => {
    const tenant = boot({ scope: 'tenant' });
    tenant.sandbox.openChannelsAddEntry();
    assert.deepEqual(tenant.calls, ['tenant'],
        'a tenant admin got the platform add panel');

    const platform = boot({ scope: 'platform' });
    platform.sandbox.openChannelsAddEntry();
    assert.deepEqual(platform.calls, ['platform'],
        'a platform admin got the tenant add form');
});

test('the static button routes through the dispatcher, not one scope directly', () => {
    assert.match(html, /onclick="openChannelsAddEntry\(\)"/,
        'the shared header button is hard-wired to a single scope');
    assert.doesNotMatch(html, /onclick="openAddChannelPanel\(\)"/,
        'the shared header button bypasses the scope dispatcher');
});
