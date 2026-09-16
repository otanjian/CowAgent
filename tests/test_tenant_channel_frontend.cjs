// Console contract for the 消息渠道 page (tenant-owned-message-channels, group 9).
//
// Two defects motivated these tests:
//   1. A failed /api/channels request left the page spinning forever, because the
//      handler returned early on a non-success payload without clearing the
//      spinner. Every failure must now resolve to a FINAL explanation.
//   2. The explanation must distinguish "you may not" from "not open here" — the
//      two need different actions from the operator.
// Plus the tenant form contract: the type list must be what the server offered,
// a rejected write must not wipe the operator's typed secret, and no secret may
// ever be pre-filled from a response.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const { loadWithModule } = require('./support/channel_workbench.cjs');

const source = fs.readFileSync(path.join(__dirname, '../channel/web/static/js/console.js'), 'utf8');

// Extract one top-level function by brace matching; the channels section is
// surrounded by unrelated helpers, so whole-script evaluation is not viable.
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

const CORE = [
    'channelScope', 'channelPageScope', 'channelsFailureKey', 'renderChannelsUnavailable',
    'tenantChannelType', 'channelTypeLabel', 'channelFieldLabel',
    'tenantChannelTypeOptions', 'tenantChannelTypeChoices', 'tenantChannelFieldInput',
    'collectTenantChannelFields', 'tenantChannelPayload',
    'tenantChannelWriteErrorKey',
    'tenantChannelRuntimeNoticeFrom', 'tenantChannelRuntimeNoticeHtml',
    'tenantChannelMissingRequiredFields',
];

function boot({ pages = null, types = [], lang = 'zh', fields = {}, promptText = '' } = {}) {
    const container = { _html: '', classes: new Set(['hidden']), innerHTML: '' };
    Object.defineProperty(container, 'innerHTML', {
        get() { return this._html; },
        set(v) { this._html = v; },
    });
    container.classList = { add: () => {}, remove: () => {}, contains: () => false };

    const inputs = Object.entries(fields).map(([key, value]) => ({
        getAttribute: (k) => (k === 'data-tenant-channel-field' ? key : null),
        value,
    }));

    const sandbox = {
        console,
        currentLang: lang,
        tenantChannelTypes: types,
        tenantChannelInstances: [],
        tenantChannelDraft: null,
        tenantChannelRuntimeNotice: null,
        agentCatalog: [],
        I18N: {
            zh: {
                channels_not_open: '该功能尚未开放',
                channels_not_open_desc: '未开放实例级配置',
                channels_no_permission: '没有访问权限',
                channels_no_permission_desc: '无权管理渠道',
                channels_load_failed: '加载失败',
                channels_load_failed_desc: '请稍后重试',
                tenant_channel_error_password: '密码校验失败',
                tenant_channel_error_conflict: '名称冲突',
                tenant_channel_error_agent: 'Agent 不属于本租户',
                tenant_channel_error_missing: '渠道不存在',
                tenant_channel_error_invalid: '提交内容不合法',
                tenant_channel_applied: '已保存，并已即时生效',
                tenant_channel_not_applied: '已保存，但尚未生效：',
                tenant_channel_error_required: '请填写必填凭据：',
            },
            en: {},
        },
        _baseAuthContext: () => (pages === null ? null : { console_pages: pages }),
        document: {
            getElementById: () => container,
            querySelectorAll: (sel) => (sel === '[data-tenant-channel-field]' ? inputs : []),
        },
        window: { prompt: () => promptText },
        // The real helper escapes text/attribute values; a pass-through is enough
        // here because the assertions below inspect structure, not encoding.
        escapeHtml: (v) => String(v === undefined || v === null ? '' : v),
    };
    sandbox.t = (key) => (sandbox.I18N[sandbox.currentLang] || {})[key] || key;

    // console.js's channel presentation functions are thin delegations to the
    // shared module, so it has to be evaluated in the sandbox too.
    vm.runInNewContext(loadWithModule(CORE.map(fnSource), sandbox), sandbox);
    return { sandbox, container };
}

// --- 9.1 failure branch ----------------------------------------------------

test('a permission failure explains itself instead of spinning', () => {
    const { sandbox, container } = boot();
    sandbox.renderChannelsUnavailable(container, 403, 'forbidden');
    assert.match(container.innerHTML, /没有访问权限/);
    assert.doesNotMatch(container.innerHTML, /fa-spinner/);
});

test('a not-yet-open deployment is distinguished from a permission denial', () => {
    const { sandbox, container } = boot();
    sandbox.renderChannelsUnavailable(container, 503, 'database_unavailable');
    assert.match(container.innerHTML, /该功能尚未开放/);
    assert.doesNotMatch(container.innerHTML, /没有访问权限/);
});

test('an unexpected failure still reaches a final state', () => {
    const { sandbox, container } = boot();
    sandbox.renderChannelsUnavailable(container, 500, '');
    assert.match(container.innerHTML, /加载失败/);
    assert.ok(container.innerHTML.length > 0);
});

test('the failure mapping is total — every status yields a known key', () => {
    const { sandbox } = boot();
    const known = new Set(['channels_not_open', 'channels_no_permission', 'channels_load_failed']);
    for (const status of [0, 400, 401, 403, 404, 405, 409, 500, 502, 503, undefined]) {
        for (const code of ['', 'database_unavailable', 'forbidden', null]) {
            assert.ok(known.has(sandbox.channelsFailureKey(status, code)),
                `${status}/${code} mapped outside the known set`);
        }
    }
});

// --- scope dispatch -------------------------------------------------------

test('the projection scope decides which view loads', () => {
    assert.equal(boot({ pages: { 'admin.channels': { scope: 'tenant' } } }).sandbox.channelScope(), 'tenant');
    assert.equal(boot({ pages: { 'admin.channels': { scope: 'platform' } } }).sandbox.channelScope(), 'platform');
});

test("a member's own range loads the same business surface as a tenant admin's", () => {
    // Task 6.1: `self` is a *range* on the tenant page, not a third page.
    const { sandbox } = boot({ pages: { 'admin.channels': { scope: 'self' } } });
    assert.equal(sandbox.channelPageScope(), 'self');
    assert.equal(sandbox.channelScope(), 'tenant');
});

test('the own-surface type list offers only what the server reports ready', () => {
    // Offering a type the create would refuse is the "clickable but refused"
    // shape; on the own surface the picker is narrowed to the ready declarations.
    const types = [
        { channel_type: 'feishu', label: { zh: '飞书', en: 'Feishu' }, ready: true },
        { channel_type: 'wecom_bot', label: { zh: '企微', en: 'WeCom' }, ready: false },
    ];
    const shared = boot({ types });
    shared.sandbox.tenantChannelSelfScope = false;
    assert.deepEqual(shared.sandbox.tenantChannelTypeChoices().map(t => t.channel_type),
                     ['feishu', 'wecom_bot']);
    const own = boot({ types });
    own.sandbox.tenantChannelSelfScope = true;
    assert.deepEqual(own.sandbox.tenantChannelTypeChoices().map(t => t.channel_type),
                     ['feishu']);
});

test('a missing or unknown projection keeps the historic platform view', () => {
    assert.equal(boot({ pages: null }).sandbox.channelScope(), 'platform');
    assert.equal(boot({ pages: {} }).sandbox.channelScope(), 'platform');
    assert.equal(boot({ pages: { 'admin.channels': { scope: 'nonsense' } } }).sandbox.channelScope(), 'platform');
});

// --- 9.4/9.5 tenant form --------------------------------------------------

const FEISHU = {
    channel_type: 'feishu',
    label: { zh: '飞书', en: 'Feishu' },
    credential_fields: [
        { key: 'feishu_app_id', label: { zh: 'App ID', en: 'App ID' }, secret: false, required: true },
        { key: 'feishu_app_secret', label: { zh: 'App Secret', en: 'App Secret' }, secret: true, required: true },
        { key: 'feishu_bot_name', label: { zh: '机器人名称', en: 'Bot name' }, secret: false, required: false },
    ],
};

test('the type list is exactly what the server offered', () => {
    const { sandbox } = boot({ types: [FEISHU] });
    const options = sandbox.tenantChannelTypeOptions();
    assert.equal(options.length, 1);
    assert.equal(options[0].value, 'feishu');
    // A type the server did not offer (the deferred 企微自建应用) is absent.
    assert.ok(!options.some(o => o.value === 'wechatcom_app'));
});

test('a secret field is never pre-filled and renders as a password input', () => {
    const { sandbox } = boot();
    const secret = sandbox.tenantChannelFieldInput(FEISHU.credential_fields[1], 'leaked');
    assert.match(secret, /type="password"/);
    assert.match(secret, /value=""/);
    assert.doesNotMatch(secret, /leaked/);
    const plain = sandbox.tenantChannelFieldInput(FEISHU.credential_fields[0], 'cli_abc');
    assert.match(plain, /value="cli_abc"/);
});

test('only non-empty fields are submitted, so a blank secret keeps its stored value', () => {
    const { sandbox } = boot({ fields: { feishu_app_id: 'cli_abc', feishu_app_secret: '   ' } });
    const collected = sandbox.collectTenantChannelFields();
    // Cross-realm object: compare the contents, not the prototype.
    assert.deepEqual(Object.keys(collected), ['feishu_app_id']);
    assert.equal(collected.feishu_app_id, 'cli_abc');
});

test('an edit payload never blanks a credential the operator did not retype', () => {
    const { sandbox } = boot();
    const payload = sandbox.tenantChannelPayload({
        display_name: 'Support Bot',
        agent_id: 'agent-a',
        expected_version: 3,
        credentials: undefined,
        recent_password: 'pw',
    });
    assert.equal(payload.display_name, 'Support Bot');
    assert.equal(payload.expected_version, 3);
    assert.ok(!('credentials' in payload));
});

test('every write failure maps to a specific, translated explanation', () => {
    const { sandbox } = boot();
    assert.equal(sandbox.tenantChannelWriteErrorKey(401, 'invalid_old'), 'tenant_channel_error_password');
    assert.equal(sandbox.tenantChannelWriteErrorKey(409, ''), 'tenant_channel_error_conflict');
    assert.equal(sandbox.tenantChannelWriteErrorKey(403, 'forbidden'), 'tenant_channel_error_agent');
    assert.equal(sandbox.tenantChannelWriteErrorKey(404, ''), 'tenant_channel_error_missing');
    assert.equal(sandbox.tenantChannelWriteErrorKey(400, 'bad'), 'tenant_channel_error_invalid');
});

// --- the rejected-write draft survives ------------------------------------

test('a rejected write keeps the draft instead of clearing the form', () => {
    // The submit path stores what was typed into tenantChannelDraft before the
    // request and only clears it on success; re-rendering reads the draft back.
    const body = source.slice(source.indexOf('function submitTenantChannel('),
        source.indexOf('function toggleTenantChannel('));
    const onFailure = body.slice(body.indexOf('if (!data || data.status'), body.indexOf('tenantChannelDraft = null'));
    assert.doesNotMatch(onFailure, /tenantChannelDraft = null/,
        'the failure branch must not clear the draft');
    assert.match(body, /tenantChannelFormError\(tenantChannelWriteErrorKey/);
});

// --- 4.5 a save says whether it is actually in service --------------------

test('a committed write that could not be applied reports "not in service yet"', () => {
    const { sandbox } = boot();
    const notice = sandbox.tenantChannelRuntimeNoticeFrom({
        status: 'success',
        instance: { id: 'chan_1' },
        runtime: { applied: false, pending: false, error: 'invalid app secret' },
    });
    assert.equal(notice.applied, false);
    assert.equal(notice.reason, 'invalid app secret');
});

test('a payload with no runtime report raises no notice at all', () => {
    const { sandbox } = boot();
    assert.equal(sandbox.tenantChannelRuntimeNoticeFrom({ status: 'success' }), null);
    assert.equal(sandbox.tenantChannelRuntimeNoticeFrom(null), null);
});

test('the notice names the reason, and claims success only when applied', () => {
    const { sandbox } = boot();
    assert.equal(sandbox.tenantChannelRuntimeNoticeHtml(), '',
        'nothing to say before the first write');

    sandbox.tenantChannelRuntimeNotice = { applied: false, pending: false, reason: 'invalid app secret' };
    const pending = sandbox.tenantChannelRuntimeNoticeHtml();
    assert.match(pending, /尚未生效/);
    assert.match(pending, /invalid app secret/, 'the reason must reach the operator');
    assert.doesNotMatch(pending, /即时生效/);

    sandbox.tenantChannelRuntimeNotice = { applied: true, pending: false, reason: '' };
    const appliedHtml = sandbox.tenantChannelRuntimeNoticeHtml();
    assert.match(appliedHtml, /即时生效/);
    assert.doesNotMatch(appliedHtml, /尚未生效/);
});

test('the submit and disable paths record the runtime report', () => {
    const submit = source.slice(source.indexOf('function submitTenantChannel('),
        source.indexOf('function toggleTenantChannel('));
    assert.match(submit, /tenantChannelRuntimeNoticeFrom\(data\)/,
        'a save that is not in service yet cannot be reported if it is ignored');

    const toggle = source.slice(source.indexOf('function toggleTenantChannel('),
        source.indexOf('function loadChannelsView('));
    assert.match(toggle, /tenantChannelRuntimeNoticeFrom\(data\)/,
        'disabling is the rollback path and must report its effect too');
});

test('the list renders the runtime notice', () => {
    const body = source.slice(source.indexOf('function renderTenantChannels('),
        source.indexOf('function openTenantChannelForm('));
    assert.match(body, /tenantChannelRuntimeNoticeHtml\(\)/);
});

test('the secret note promises immediate effect, not a maintenance restart', () => {
    // The dictionaries moved to per-domain namespace files (task 8.5), so the
    // note is asserted where it now lives.
    const tenantChannelI18n = fs.readFileSync(
        path.join(__dirname, '../channel/web/static/js/i18n/tenant-channel.js'), 'utf8');
    const lines = tenantChannelI18n.split('\n').filter(l => /tenant_channel_secret_note["']?\s*:/.test(l));
    assert.equal(lines.length, 3, 'zh / zh-Hant / en must all carry the note');
    assert.ok(lines.some(l => /即时生效/.test(l)), 'zh must say it takes effect now');
    assert.ok(lines.some(l => /即時生效/.test(l)), 'zh-Hant must say it takes effect now');
    assert.ok(lines.some(l => /immediately|without a restart|no restart/i.test(l)),
        'en must say it takes effect now');
    for (const line of lines) {
        assert.doesNotMatch(line, /维护窗口|維護視窗|maintenance/i,
            `stale maintenance-window copy: ${line.trim()}`);
    }
});

// --- 5.5/5.6 the minimum set, from the server's own declaration -----------

test('required-ness comes from the declaration, not a second list in the console', () => {
    const { sandbox } = boot({ types: [FEISHU] });
    const missing = sandbox.tenantChannelMissingRequiredFields('feishu', { feishu_app_id: 'cli_abc' });
    assert.deepEqual(missing.map(f => f.key), ['feishu_app_secret']);
    // An optional field is never reported, and a complete bundle is complete.
    assert.deepEqual(
        sandbox.tenantChannelMissingRequiredFields('feishu', {
            feishu_app_id: 'cli_abc', feishu_app_secret: 's3cr3t' }), []);
    // Whitespace is not a value.
    assert.deepEqual(
        sandbox.tenantChannelMissingRequiredFields('feishu', {
            feishu_app_id: 'cli_abc', feishu_app_secret: '   ' }).map(f => f.key),
        ['feishu_app_secret']);
});

test('an unknown type is left to the server rather than guessed at', () => {
    const { sandbox } = boot({ types: [FEISHU] });
    // The console only knows what the server declared; an unknown type is not
    // given an invented minimum set (the realm-crossing array makes a length
    // check clearer than a deep compare here).
    assert.equal(sandbox.tenantChannelMissingRequiredFields('nonsense', {}).length, 0);
});

test('a required field is marked, an optional one is not', () => {
    const { sandbox } = boot();
    const required = sandbox.tenantChannelFieldInput(FEISHU.credential_fields[0], '');
    const optional = sandbox.tenantChannelFieldInput(FEISHU.credential_fields[2], '');
    assert.match(required, /data-tenant-channel-required/);
    assert.doesNotMatch(optional, /data-tenant-channel-required/);
});

test('a create that is missing a required field is not sent at all', () => {
    const body = source.slice(source.indexOf('function submitTenantChannel('),
        source.indexOf('function toggleTenantChannel('));
    const guard = body.indexOf('tenantChannelMissingRequiredFields(');
    const request = body.indexOf('fetch(url');
    assert.ok(guard >= 0, 'the create path must consult the minimum set');
    assert.ok(request > guard,
        'the request must not be built before the minimum set is checked');
    assert.match(body.slice(guard, request), /return/,
        'a missing required field must stop the submit, keeping the typed draft');
});

test('the rejection names the missing fields', () => {
    const { sandbox } = boot({ types: [FEISHU] });
    // tenantChannelFormError is the one place that renders a rejection, and it
    // must be able to carry the field names the operator has to fill in.
    const body = source.slice(source.indexOf('function tenantChannelFormError('),
        source.indexOf('function submitTenantChannel('));
    assert.match(body, /detail/,
        'the error must be able to name the missing fields');
    assert.ok(sandbox.t('tenant_channel_error_required'));
});

// --- 6.1 the empty state speaks to the scope it is showing -----------------

test('the empty state is scoped, not tenant-worded on a member surface', () => {
    // The description above the list was already scope-aware; the empty state
    // was not, so a member was told no channel existed for "this tenant" while
    // looking at their own connections. Both wordings must stay distinct.
    const body = source.slice(source.indexOf('function renderTenantChannels('),
        source.indexOf('function openTenantChannelForm('));
    assert.match(body, /tenantChannelSelfScope\s*\?\s*'tenant_channel_empty_desc_self'\s*:\s*'tenant_channel_empty_desc'/,
        'the empty state must pick its wording from the console scope');
});

test('both empty-state wordings exist in all three languages', () => {
    const tenantChannelI18n = fs.readFileSync(
        path.join(__dirname, '../channel/web/static/js/i18n/tenant-channel.js'), 'utf8');
    for (const key of ['tenant_channel_empty_desc', 'tenant_channel_empty_desc_self']) {
        const lines = tenantChannelI18n.split('\n').filter(l => l.includes(`"${key}":`));
        assert.equal(lines.length, 3, `zh / zh-Hant / en must all carry ${key}`);
    }
    // The own-surface wording must not claim anything about the tenant: that is
    // exactly the confusion this key was added to remove.
    const selfLines = tenantChannelI18n.split('\n')
        .filter(l => l.includes('"tenant_channel_empty_desc_self":'));
    for (const line of selfLines) {
        assert.doesNotMatch(line, /本租户|本租戶|this tenant/i,
            `the own-surface empty state must not mention the tenant: ${line.trim()}`);
    }
});
