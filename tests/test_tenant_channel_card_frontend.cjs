// Tenant channel card + Tab contract (P1, task 3.3/3.4).
//
// The tenant 消息渠道 page must offer the same access actions as the platform
// page operators already know: a scan entry next to the manual credential form
// for the types that support it, on a card that looks like the platform card.
// Before this change the tenant page was a summary row plus a flat form, so the
// scan entry the master branch offers was simply not reachable per tenant.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../channel/web/static/js/console.js'), 'utf8');

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

function constSource(name) {
    const head = `const ${name} = {`;
    const from = source.indexOf(head);
    assert.ok(from >= 0, `Missing ${name}`);
    let depth = 0;
    for (let i = source.indexOf('{', from); i < source.length; i++) {
        if (source[i] === '{') depth++;
        else if (source[i] === '}') {
            depth--;
            if (depth === 0) return source.slice(from, i + 2); // include trailing ';'
        }
    }
    throw new Error(`Unbalanced ${name}`);
}

const CORE = [
    'buildChannelCardShell', 'tenantChannelType', 'tenantChannelAppearance',
    'tenantChannelSupportsScan', 'tenantChannelFieldInput',
    'buildTenantChannelForm', 'renderTenantChannelCard',
    'channelTypeLabel', 'channelFieldLabel', 'tenantChannelAgentOptions',
    'channelsFailureKey', 'scanFailureText',
];

const CORE_CONSTS = ['TENANT_CHANNEL_SCAN_TYPES'];

function boot({ types = [], instances = [], draft = null } = {}) {
    const sandbox = {
        console,
        currentLang: 'zh',
        I18N: { zh: {}, en: {} },
        tenantChannelTypes: types,
        tenantChannelInstances: instances,
        tenantChannelDraft: draft,
        agentCatalog: [],
        escapeHtml: (v) => String(v === undefined || v === null ? '' : v),
        multiAgentMode: () => false,
    };
    sandbox.t = (key) => (sandbox.I18N[sandbox.currentLang] || {})[key] || key;
    const core = CORE_CONSTS.map(constSource).concat(CORE.map(fnSource));
    vm.runInNewContext(core.join('\n'), sandbox);
    return sandbox;
}

const FEISHU = {
    channel_type: 'feishu',
    label: { zh: '飞书', en: 'Feishu' },
    icon: 'fa-paper-plane',
    color: 'blue',
    credential_fields: [
        { key: 'feishu_app_id', label: { zh: 'App ID', en: 'App ID' }, secret: false },
        { key: 'feishu_app_secret', label: { zh: 'App Secret', en: 'App Secret' }, secret: true },
    ],
};
const WECOM = {
    channel_type: 'wecom_bot',
    label: { zh: '企微智能机器人', en: 'WeCom Bot' },
    icon: 'fa-robot',
    color: 'emerald',
    credential_fields: [
        { key: 'wecom_bot_id', label: { zh: 'Bot ID', en: 'Bot ID' }, secret: false },
        { key: 'wecom_bot_secret', label: { zh: 'Secret', en: 'Secret' }, secret: true },
    ],
};
const TELEGRAM = {
    channel_type: 'telegram',
    label: { zh: 'Telegram', en: 'Telegram' },
    icon: 'fa-paper-plane',
    color: 'sky',
    credential_fields: [
        { key: 'telegram_token', label: { zh: 'Bot Token', en: 'Bot Token' }, secret: true },
    ],
};

const FEISHU_INSTANCE = {
    id: 'inst-feishu-1', channel_type: 'feishu', display_name: 'Support Bot',
    agent_id: '', active: true, version: 1,
};

// --- 3.4 shared card -------------------------------------------------------

test('the tenant card uses the shared shell and shows the display name', () => {
    const sandbox = boot({ types: [FEISHU] });
    const html = sandbox.renderTenantChannelCard(FEISHU_INSTANCE);
    assert.match(html, /data-tenant-channel-row="inst-feishu-1"/);
    // Shared shell markup: the icon tile every card has.
    assert.match(html, /w-10 h-10 rounded-xl/);
    assert.match(html, /fa-paper-plane/);
    assert.match(html, /Support Bot/);
});

// --- 3.3 feishu scan + manual --------------------------------------------

test('a feishu tenant card offers both a scan and a manual entry', () => {
    const sandbox = boot({ types: [FEISHU] });
    const html = sandbox.buildTenantChannelForm(FEISHU_INSTANCE);
    assert.match(html, /data-tenant-channel-mode="scan"/);
    assert.match(html, /data-tenant-channel-mode="manual"/);
    assert.match(html, /startFeishuRegister\(/);
});

test('a wecom_bot tenant card offers a scan entry', () => {
    const sandbox = boot({ types: [WECOM] });
    const inst = Object.assign({}, FEISHU_INSTANCE, { channel_type: 'wecom_bot', id: 'i2' });
    const html = sandbox.buildTenantChannelForm(inst);
    assert.match(html, /data-tenant-channel-mode="scan"/);
    assert.match(html, /startTenantWecomScan\(/);
});

test('a type without scan support keeps the manual form only', () => {
    const sandbox = boot({ types: [TELEGRAM] });
    const inst = Object.assign({}, FEISHU_INSTANCE, { channel_type: 'telegram', id: 'i3' });
    const html = sandbox.buildTenantChannelForm(inst);
    assert.doesNotMatch(html, /data-tenant-channel-mode="scan"/);
    assert.match(html, /data-tenant-channel-mode="manual"/);
});

test('every tenant form still has its credential fields and a save action', () => {
    const sandbox = boot({ types: [FEISHU] });
    const html = sandbox.buildTenantChannelForm(FEISHU_INSTANCE);
    assert.match(html, /data-tenant-channel-field="feishu_app_id"/);
    assert.match(html, /data-tenant-channel-field="feishu_app_secret"/);
    assert.match(html, /onclick="submitTenantChannel\(\)"/);
});

test('the scan capability is declared, not guessed from the label', () => {
    const sandbox = boot();
    assert.equal(sandbox.tenantChannelSupportsScan('feishu'), true);
    assert.equal(sandbox.tenantChannelSupportsScan('wecom_bot'), true);
    assert.equal(sandbox.tenantChannelSupportsScan('telegram'), false);
    // The deferred self-built WeCom app is not offered here.
    assert.equal(sandbox.tenantChannelSupportsScan('wechatcom_app'), false);
});

// --- 3.6 scan failures carry the reason --------------------------------

test('a scan failure distinguishes "not open" from "no permission"', () => {
    const sandbox = boot();
    sandbox.I18N.zh = {
        channels_not_open: '该功能尚未开放', channels_not_open_desc: '未开放实例级配置',
        channels_no_permission: '没有访问权限', channels_no_permission_desc: '无权管理渠道',
        feishu_scan_fail: '扫码失败',
    };
    assert.match(sandbox.scanFailureText(503, 'database_unavailable', ''), /尚未开放/);
    assert.match(sandbox.scanFailureText(403, 'forbidden', ''), /没有访问权限/);
    // An unexplained failure still reaches a final, non-empty message.
    assert.equal(sandbox.scanFailureText(500, '', 'boom'), 'boom');
    assert.equal(sandbox.scanFailureText(500, '', ''), '扫码失败');
});

// --- 3.5 scan results pre-fill, never persist ---------------------------

test('a scan result is written to the draft and nothing is persisted', () => {
    const body = fnSource('applyScanToTenantForm');
    assert.match(body, /draft\.credentials/,
        'the scanned credentials never reach the draft');
    assert.match(body, /draft\.mode = 'manual'/,
        'the operator cannot review the scanned values in the form');
    assert.doesNotMatch(body, /fetch\(/,
        'the scan result must not be persisted on its own');

    const feishu = fnSource('applyFeishuScanToTenantForm');
    assert.match(feishu, /feishu_app_id/);
    assert.match(feishu, /feishu_app_secret/);
});

test('closing the form drops the draft (and thus the scanned secret)', () => {
    const body = fnSource('closeTenantChannelForm');
    assert.match(body, /tenantChannelDraft = null/,
        'closing the form keeps the scanned credentials in memory');
});

// --- 3.5b a scanned secret must survive the submit -----------------------
//
// The secret input renders blank on purpose (an edit must never display a
// stored secret), so after a scan the plaintext exists only in the draft.
// Letting the empty input replace the draft destroys the operator's own scan
// result, and the write is then refused as "missing a required field" — the
// exact shape of "I scanned, it said success, and nothing shows up".

const FEISHU_REQUIRED = {
    channel_type: 'feishu',
    label: { zh: '飞书', en: 'Feishu' },
    credential_fields: [
        { key: 'feishu_app_id', label: { zh: 'App ID', en: 'App ID' }, secret: false, required: true },
        { key: 'feishu_app_secret', label: { zh: 'App Secret', en: 'App Secret' }, secret: true, required: true },
    ],
};
const SUBMIT_CORE = [
    'tenantChannelMissingRequiredFields', 'collectTenantChannelFields',
    'tenantChannelPayload', 'tenantChannelWriteErrorKey',
    'tenantChannelRuntimeNoticeFrom', 'submitTenantChannel',
    'channelFieldLabel', 'applyScanTicketToTenantForm', 'tenantChannelAutoName',
    'autoPersistScannedTenantChannel',
];

function inputEl(key, value) {
    return {
        value,
        getAttribute: (name) => (name === 'data-tenant-channel-field' ? key : null),
    };
}

function bootSubmit({ draft, inputs, extraCore = [], displayValue = 'Support',
                       passwordValue = 'MemberPass123!', failWith = null }) {
    const requests = [];
    const errors = [];
    const passwordAsked = [];
    const nodes = {};
    const node = (id) => nodes[id] || (nodes[id] = {
        id, value: '', textContent: '', innerHTML: '',
        classList: { add() {}, remove() {}, toggle() {} },
        querySelector: () => null,
    });
    node('tenant-channel-display').value = displayValue;
    const sandbox = {
        console,
        currentLang: 'zh',
        I18N: { zh: {}, en: {} },
        tenantChannelTypes: [FEISHU_REQUIRED],
        tenantChannelInstances: [],
        tenantChannelDraft: draft,
        agentCatalog: [],
        escapeHtml: (v) => String(v === undefined || v === null ? '' : v),
        document: {
            getElementById: node,
            querySelectorAll: (selector) => (selector === '[data-tenant-channel-field]' ? inputs : []),
        },
        window: { prompt: () => { throw new Error('window.prompt must not be used'); } },
        // The real dialog builds DOM the sandbox has none of, so the tests
        // substitute the promise seam. Returning null models a cancel.
        askRecentPassword: () => {
            passwordAsked.push(true);
            return Promise.resolve(passwordValue);
        },
        fetch: (url, options) => {
            requests.push({ url, body: JSON.parse(options.body) });
            if (failWith) {
                return Promise.resolve({
                    status: failWith.status,
                    json: async () => ({ status: 'error', code: failWith.code }),
                });
            }
            return Promise.resolve({ status: 200, json: async () => ({ status: 'success' }) });
        },
        loadTenantChannelsView: () => Promise.resolve(),
        renderTenantChannels: () => {},
        tenantChannelFormError: (key, detail) => errors.push({ key, detail }),
        // The scan handler's own collaborators.
        _feishuScanTarget: 'new',
        setInputValue: (key, value) => {
            const el = inputs.find(i => i.getAttribute('data-tenant-channel-field') === key);
            if (el) el.value = value;
            return !!el;
        },
    };
    sandbox.t = (key) => (sandbox.I18N[sandbox.currentLang] || {})[key] || key;
    const core = CORE_CONSTS.map(constSource)
        .concat(SUBMIT_CORE.concat(extraCore).map(fnSource));
    vm.runInNewContext(core.join('\n'), sandbox);
    return { sandbox, requests, errors, passwordAsked };
}

test('a scanned secret survives the submit that saves it', async () => {
    const { sandbox, requests, errors } = bootSubmit({
        draft: {
            instance_id: '', channel_type: 'feishu', display_name: 'Support',
            agent_id: '', mode: 'manual',
            credentials: { feishu_app_id: 'cli_scanned', feishu_app_secret: 'scanned-secret-xyz' },
        },
        inputs: [inputEl('feishu_app_id', 'cli_scanned'), inputEl('feishu_app_secret', '')],
    });
    sandbox.document.getElementById('tenant-channel-display').value = 'Support';
    await sandbox.submitTenantChannel();

    assert.deepEqual(errors, [], 'the write was refused before it was ever sent');
    assert.equal(requests.length, 1, 'no request carried the scanned credentials');
    assert.equal(requests[0].body.credentials.feishu_app_id, 'cli_scanned');
    assert.equal(requests[0].body.credentials.feishu_app_secret, 'scanned-secret-xyz',
        'the scanned secret was erased by the blank input');
});

test('editing without retyping the secret sends no secret at all', async () => {
    const { sandbox, requests, errors } = bootSubmit({
        draft: {
            instance_id: 'inst-1', channel_type: 'feishu', display_name: 'S',
            agent_id: '', mode: 'manual', expected_version: 3, credentials: {},
        },
        inputs: [inputEl('feishu_app_id', 'cli_stored'), inputEl('feishu_app_secret', '')],
    });
    await sandbox.submitTenantChannel();

    assert.deepEqual(errors, []);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].body.credentials.feishu_app_secret, undefined,
        'a blank secret must not blank the stored one');
    assert.equal(requests[0].body.expected_version, 3);
});

// --- 3.10 the whole scan -> save chain ----------------------------------
//
// The unit above proves the merge in isolation. This one walks the real
// sequence the operator performs — the scan handler writes the draft, the
// blank secret input and the filled App ID input are read back, and the save
// is submitted — so a regression anywhere in that chain fails here.

test('a freshly scanned feishu app can be saved without retyping anything', async () => {
    const { sandbox, requests, errors } = bootSubmit({
        draft: {
            instance_id: '', channel_type: 'feishu', display_name: '',
            agent_id: '', mode: 'scan', credentials: {},
        },
        inputs: [inputEl('feishu_app_id', ''), inputEl('feishu_app_secret', '')],
        extraCore: ['applyScanToTenantForm', 'applyFeishuScanToTenantForm'],
        displayValue: 'Support Bot',
    });

    // 1. The scan reports the app it just created.
    assert.equal(sandbox._feishuScanTarget, 'new');
    sandbox.applyFeishuScanToTenantForm('cli_scanned', 'scanned-secret-xyz');
    assert.equal(sandbox.tenantChannelDraft.credentials.feishu_app_secret,
        'scanned-secret-xyz', 'the scan result never reached the draft');

    // 2. The form now shows the App ID in the input and leaves the secret blank.
    sandbox.setInputValue('feishu_app_id', 'cli_scanned');

    // 3. Saving succeeds: the secret came from the draft, not the blank input.
    await sandbox.submitTenantChannel();
    assert.deepEqual(errors, [], 'a complete scan was refused at save time');
    assert.equal(requests.length, 1, 'the scanned app was never sent to the server');
    assert.equal(requests[0].body.credentials.feishu_app_id, 'cli_scanned');
    assert.equal(requests[0].body.credentials.feishu_app_secret, 'scanned-secret-xyz');
    assert.equal(requests[0].body.display_name, 'Support Bot');
});

test('a create without a display name says so instead of failing generically', async () => {
    const { sandbox, requests, errors } = bootSubmit({
        draft: {
            instance_id: '', channel_type: 'feishu', display_name: '',
            agent_id: '', mode: 'manual',
            credentials: { feishu_app_id: 'cli_x', feishu_app_secret: 'sec_y' },
        },
        inputs: [inputEl('feishu_app_id', 'cli_x'), inputEl('feishu_app_secret', '')],
        displayValue: '',
    });
    await sandbox.submitTenantChannel();

    assert.equal(requests.length, 0, 'a nameless instance was sent to the server');
    assert.equal(errors.length, 1);
    assert.equal(errors[0].key, 'tenant_channel_error_display_required',
        'the operator is told only that the save failed');
});

test('the display name is marked required on a create form', () => {
    const sandbox = boot({ types: [FEISHU] });
    const html = sandbox.buildTenantChannelForm(null);
    assert.match(html, /data-tenant-channel-display-required="1"/,
        'nothing tells the operator the name is mandatory');
});

// --- 3.13 a scan persists itself ----------------------------------------
//
// The scan reports success and the channel exists: no second click, no name to
// invent, and no password dialog. The grant the server minted for that scan is
// what stands in for the password, so these tests are about the whole chain —
// the grant reaching the draft, riding on the write, and being spent.

test('a scan grant rides along with the write and skips the password dialog', async () => {
    const { sandbox, requests, errors, passwordAsked } = bootSubmit({
        draft: {
            instance_id: '', channel_type: 'feishu', display_name: 'Support',
            agent_id: '', mode: 'scan', scan_ticket: 'grant-1',
            credentials: { feishu_app_secret: 'scanned-secret' },
        },
        inputs: [inputEl('feishu_app_id', 'cli_x'), inputEl('feishu_app_secret', '')],
    });
    await sandbox.submitTenantChannel();

    assert.deepEqual(errors, []);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].body.scan_ticket, 'grant-1');
    assert.equal(requests[0].body.recent_password, '',
        'a grant must not be sent alongside a password');
    assert.equal(requests[0].body.credentials.feishu_app_secret, 'scanned-secret');
    assert.equal(passwordAsked.length, 0,
        'the whole point of the grant is that the operator is not asked');
});

test('a scan persists itself with a name derived from the app id', async () => {
    const { sandbox, requests, errors, passwordAsked } = bootSubmit({
        draft: {
            instance_id: '', channel_type: 'feishu', display_name: '',
            agent_id: '', mode: 'scan', credentials: {},
        },
        inputs: [inputEl('feishu_app_id', ''), inputEl('feishu_app_secret', '')],
        extraCore: ['applyScanToTenantForm', 'applyFeishuScanToTenantForm',
                    'tenantChannelType'],
        displayValue: '',
    });

    // The scan reports the app it created, grant included.
    sandbox.applyFeishuScanToTenantForm(
        'cli_aa281fe031f85cda', 'scanned-secret', 'grant-2');
    assert.equal(sandbox.tenantChannelDraft.scan_ticket, 'grant-2');
    sandbox.setInputValue('feishu_app_id', 'cli_aa281fe031f85cda');

    await sandbox.autoPersistScannedTenantChannel();

    assert.deepEqual(errors, [], 'a completed scan was refused at save time');
    assert.equal(requests.length, 1, 'the scan never became a channel');
    const body = requests[0].body;
    assert.equal(body.display_name, '飞书 · 5cda',
        'the operator was asked to invent a name');
    assert.equal(body.credentials.feishu_app_secret, 'scanned-secret');
    assert.equal(body.scan_ticket, 'grant-2');
    assert.equal(passwordAsked.length, 0);
});

test('auto-persist stands down when no grant was minted', async () => {
    // WeCom's browser-side scan mints nothing server-side, so its form is
    // submitted by the operator; auto-persist must not fire on a bare draft.
    const { sandbox, requests } = bootSubmit({
        draft: {
            instance_id: '', channel_type: 'wecom_bot', display_name: '',
            agent_id: '', mode: 'scan',
            credentials: { wecom_bot_id: 'b', wecom_bot_secret: 's' },
        },
        inputs: [],
    });
    await sandbox.autoPersistScannedTenantChannel();
    assert.equal(requests.length, 0);
});

test('a manual save asks for the password in the dialog, not a native prompt', async () => {
    // The stub's ``window.prompt`` throws, so any regression to the native
    // dialog fails loudly here rather than as an inexplicable 401.
    const { sandbox, requests, passwordAsked } = bootSubmit({
        draft: {
            instance_id: '', channel_type: 'feishu', display_name: 'Support',
            agent_id: '', mode: 'manual',
            credentials: { feishu_app_id: 'cli_x', feishu_app_secret: 'sec_y' },
        },
        inputs: [inputEl('feishu_app_id', 'cli_x'), inputEl('feishu_app_secret', '')],
        passwordValue: 'typed-by-hand',
    });
    await sandbox.submitTenantChannel();

    assert.equal(passwordAsked.length, 1);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].body.recent_password, 'typed-by-hand');
    assert.equal(requests[0].body.scan_ticket, undefined,
        'no grant was minted, so none may be claimed');
});

test('cancelling the password dialog sends nothing', async () => {
    const { sandbox, requests, errors } = bootSubmit({
        draft: {
            instance_id: '', channel_type: 'feishu', display_name: 'Support',
            agent_id: '', mode: 'manual',
            credentials: { feishu_app_id: 'cli_x', feishu_app_secret: 'sec_y' },
        },
        inputs: [inputEl('feishu_app_id', 'cli_x'), inputEl('feishu_app_secret', '')],
        passwordValue: null,
    });
    await sandbox.submitTenantChannel();

    assert.equal(requests.length, 0, 'a cancelled dialog must not send a write');
    assert.deepEqual(errors, [], 'cancelling is not an error to explain');
});

test('an empty password is refused before it reaches the server', async () => {
    const { sandbox, requests, errors } = bootSubmit({
        draft: {
            instance_id: '', channel_type: 'feishu', display_name: 'Support',
            agent_id: '', mode: 'manual',
            credentials: { feishu_app_id: 'cli_x', feishu_app_secret: 'sec_y' },
        },
        inputs: [inputEl('feishu_app_id', 'cli_x'), inputEl('feishu_app_secret', '')],
        passwordValue: '',
    });
    await sandbox.submitTenantChannel();

    assert.equal(requests.length, 0, 'an empty password cannot authorize a write');
    assert.equal(errors[0].key, 'tenant_channel_error_password_required');
});

test('a refused grant is dropped so the retry can ask for a password', async () => {
    // Otherwise the spent grant would be re-sent forever and every retry would
    // fail the same way, which is the loop this change exists to end.
    const { sandbox, requests, errors } = bootSubmit({
        draft: {
            instance_id: '', channel_type: 'feishu', display_name: 'Support',
            agent_id: '', mode: 'scan', scan_ticket: 'spent-grant',
            credentials: { feishu_app_secret: 'scanned-secret' },
        },
        inputs: [inputEl('feishu_app_id', 'cli_x'), inputEl('feishu_app_secret', '')],
        failWith: { status: 401, code: 'invalid_old' },
    });
    await sandbox.submitTenantChannel();

    assert.equal(requests.length, 1);
    assert.equal(errors[0].key, 'tenant_channel_error_password');
    assert.equal(sandbox.tenantChannelDraft.scan_ticket, '',
        'the spent grant still looks usable to the next attempt');
});

test('an agent chosen before the scan survives the auto-save', async () => {
    // The scan re-renders the form, so a selection made beforehand has to be
    // carried in the draft or it silently reverts to the default.
    const { sandbox, requests } = bootSubmit({
        draft: {
            instance_id: '', channel_type: 'feishu', display_name: 'Support',
            agent_id: '', mode: 'scan', credentials: {},
        },
        inputs: [inputEl('feishu_app_id', 'cli_x'), inputEl('feishu_app_secret', '')],
        extraCore: ['applyScanToTenantForm', 'applyFeishuScanToTenantForm',
                    'tenantChannelType'],
    });
    sandbox.document.getElementById('tenant-channel-agent').value = 'agent-picked';
    sandbox.applyFeishuScanToTenantForm('cli_x', 'sec', 'grant-3');

    assert.equal(sandbox.tenantChannelDraft.agent_id, 'agent-picked');
    await sandbox.autoPersistScannedTenantChannel();
    assert.equal(requests[0].body.agent_id, 'agent-picked',
        'the chosen agent was dropped by the scan re-render');
});

test('a misconfigured server says so instead of blaming the values', () => {
    // The operator can retry a misconfigured server forever: the message has to
    // distinguish "your input is wrong" from "this deployment cannot store
    // credentials at all".
    const sandbox = { t: (key) => key, escapeHtml: (v) => String(v == null ? '' : v) };
    vm.runInNewContext(fnSource('tenantChannelWriteErrorKey'), sandbox);
    assert.equal(
        sandbox.tenantChannelWriteErrorKey(500, 'credential_crypto'),
        'tenant_channel_error_crypto');
    // A transport failure is likewise not a rejected form.
    const body = source.slice(source.indexOf('function submitTenantChannel('),
        source.indexOf('function toggleTenantChannel('));
    assert.match(body, /tenantChannelFormError\('tenant_channel_error_network'\)/,
        'a network failure must not read as invalid input');
});

test('no tenant channel write path uses a suppressible native prompt', () => {
    const body = source.slice(source.indexOf('function submitTenantChannel('),
        source.indexOf('function loadChannelsView('));
    assert.doesNotMatch(body, /window\.prompt/,
        'a native dialog can be suppressed and then reads as an empty password');
    assert.match(body, /askRecentPassword\(\)/);
});

test('the password dialog offers a real field the code can actually read', () => {
    // askRecentPassword reads ``#tenant-channel-password``; a renamed field
    // would make every manual save collect nothing.
    const sandbox = { t: (key) => key, escapeHtml: (v) => String(v == null ? '' : v) };
    vm.runInNewContext(fnSource('recentPasswordDialogHtml'), sandbox);
    const html = sandbox.recentPasswordDialogHtml();
    assert.match(html, /id="tenant-channel-password"/);
    assert.match(html, /type="password"/);
    assert.match(html, /data-recent-password-ok/);
    assert.match(html, /data-recent-password-cancel/);
});
