// Member personal console frontend contract (change enable-member-personal-console,
// tasks 8.2-8.5).
//
// The five personal pages are *self* surfaces on the shared console shell. This
// file pins the parts that must not drift:
//
//   * the sidebar/shell wiring exists (entries, view ids, script order);
//   * the verb a row offers comes from the server's ``actions`` projection, and
//     the request that verb makes goes to the personal endpoint — never to a
//     public maintenance path;
//   * read / configure / execute stay apart in the page header;
//   * a denied page renders the denial and issues no request, so the consumer
//     behind it is never started by a denied visit;
//   * every new key exists in all three languages.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const { loadDictionaries } = require('./support/i18n_namespaces.cjs');

const ROOT = path.join(__dirname, '..');
const CHAT_HTML = path.join(ROOT, 'channel/web/chat.html');
const CONSOLE = path.join(ROOT, 'channel/web/static/js/console.js');
const PERSONAL = path.join(ROOT, 'channel/web/static/js/personal-console.js');

const VIEW_IDS = ['personal-agents', 'personal-channels', 'personal-memory',
    'personal-tools', 'personal-skills'];
const PAGE_IDS = ['personal.agents', 'personal.channels', 'personal.memory',
    'personal.tools', 'personal.skills'];

function loadPersonalConsole() {
    const registrations = [];
    const source = fs.readFileSync(PERSONAL, 'utf8');
    // Evaluate in *this* realm: values created inside a fresh ``vm`` context
    // carry that realm's prototypes, which makes every deep-equal assertion fail
    // with "same structure but not reference-equal". The module only needs a few
    // globals, so they are stubbed and restored around the load.
    const stubs = {
        window: { registerConsoleView: spec => registrations.push(spec) },
        document: { getElementById: () => null },
        sessionStorage: { getItem: () => null, setItem: () => {} },
        fetch: () => Promise.reject(new Error('no network in tests')),
    };
    const previous = {};
    for (const key of Object.keys(stubs)) {
        previous[key] = Object.prototype.hasOwnProperty.call(globalThis, key)
            ? globalThis[key] : undefined;
        globalThis[key] = stubs[key];
    }
    try {
        vm.runInThisContext(source, { filename: 'personal-console.js' });
        return { api: globalThis.window.PersonalConsole, registrations };
    } finally {
        for (const key of Object.keys(stubs)) {
            if (previous[key] === undefined) delete globalThis[key];
            else globalThis[key] = previous[key];
        }
    }
}

const { api, registrations } = loadPersonalConsole();

test('the module publishes the pure helpers the pages are built from', () => {
    for (const name of ['personalView', 'personalPageFor', 'personalPageDenied',
        'personalPageDenial', 'personalDeniedSwitchKeys',
        'personalStateKeys', 'personalAllowedVerbs', 'personalVerbLabel',
        'personalFilterItems', 'personalPaginate', 'personalActionRequest',
        'personalFormFields', 'personalCredentialFields', 'invalidatePersonalViews',
        'personalConsoleDirtyGuard']) {
        assert.equal(typeof api[name], 'function', `missing ${name}`);
    }
});

test('every personal view is registered exactly once, with load and repaint', () => {
    const ids = registrations.map(spec => spec.id).sort();
    assert.deepEqual(ids, VIEW_IDS.slice().sort());
    for (const spec of registrations) {
        assert.equal(typeof spec.load, 'function', spec.id);
        assert.equal(typeof spec.repaint, 'function', spec.id);
        assert.ok(spec.label, `${spec.id} carries a label key`);
    }
});

test('the view ids map one-to-one to the signed personal pages', () => {
    const pages = api.PERSONAL_VIEWS.map(view => view.page).sort();
    assert.deepEqual(pages, PAGE_IDS.slice().sort());
});

test('each view reads and writes the personal surface, not a public one', () => {
    // A member page may only reach the personal collection endpoints, the
    // self-scoped Agents read/write, or the personal memory surface. Anything
    // else would be a public maintenance path reachable from a member page.
    const allowedWrites = ['/api/agents', '/api/personal/channels',
        '/api/personal/resources', '/api/memory/personal'];
    for (const view of api.PERSONAL_VIEWS) {
        assert.ok(view.endpoint.startsWith('/api/'),
            `${view.id} endpoint: ${view.endpoint}`);
        assert.ok(allowedWrites.includes(view.write),
            `${view.id} write: ${view.write}`);
    }
    const channels = api.personalView('personal-channels');
    assert.equal(channels.itemWrite, '/api/personal/channels/');
    const tools = api.personalView('personal-tools');
    assert.equal(tools.resourceKind, 'tool');
    const skills = api.personalView('personal-skills');
    assert.equal(skills.resourceKind, 'skill');
});

// ---- sidebar / shell wiring ------------------------------------------------

test('the five personal entries live in the account menu, in their original order', () => {
    // change move-personal-menu-to-account: the entries left the main navigation
    // and are hosted by the account panel (「我的资源」), shared by both areas.
    const html = fs.readFileSync(CHAT_HTML, 'utf8');
    const groupAt = html.indexOf('id="account-menu-resources"');
    assert.ok(groupAt > 0, 'the account panel carries the 「我的资源」 group');
    const groupEnd = html.indexOf('id="account-menu-settings"', groupAt);
    assert.ok(groupEnd > groupAt, 'the resources group closes before the account actions');
    const group = html.slice(groupAt, groupEnd);
    assert.ok(group.includes('data-i18n="account_menu_resources"'), 'the group label exists');
    let cursor = -1;
    for (const id of VIEW_IDS) {
        const at = group.indexOf(`data-view="${id}"`);
        assert.ok(at > cursor, `account entry ${id} in its original order`);
        cursor = at;
    }
    // Exactly one host: no duplicate entry of the same page anywhere in the shell.
    for (const id of VIEW_IDS) {
        const occurrences = html.split(`data-view="${id}"`).length - 1;
        assert.equal(occurrences, 1, `${id} has exactly one host`);
    }
    // The old main-navigation personal group is gone, so no stale group label or
    // second current-item marker can survive in #sidebar-nav.
    const nav = html.slice(html.indexOf('id="sidebar-nav"'), html.indexOf('data-nav-shell="admin"'));
    for (const id of VIEW_IDS) {
        assert.ok(!nav.includes(`data-view="${id}"`), `${id} is not in #sidebar-nav`);
    }
    assert.ok(!nav.includes('data-i18n="nav_group_personal"'),
        'the workbench sidebar no longer carries the 「我的」 group label');
});

test('the account trigger names the account and its personal-resource hint', () => {
    const html = fs.readFileSync(CHAT_HTML, 'utf8');
    const triggerAt = html.indexOf('id="sidebar-account-toggle"');
    const trigger = html.slice(triggerAt, html.indexOf('</button>', triggerAt));
    assert.ok(trigger.includes('class="sidebar-account-avatar"'), 'avatar stays');
    assert.ok(trigger.includes('id="sidebar-account-name"'), 'the account name stays');
    assert.ok(trigger.includes('sidebar-account-chevron'), 'the expand chevron stays');
    assert.ok(trigger.includes('aria-controls="sidebar-account-menu"'), 'the panel is named');
    assert.ok(trigger.includes('aria-describedby="sidebar-account-region"'),
        'the personal-area region state is described');
    assert.ok(trigger.includes('id="sidebar-account-region"'), 'the region state node exists');
    const source = fs.readFileSync(CONSOLE, 'utf8');
    assert.ok(source.includes("t('account_menu_trigger_hint')"),
        'the trigger hint comes from the shared dictionary');
    assert.ok(/trigger\.setAttribute\('aria-label'/.test(source),
        'the hint becomes part of the accessible name');
});

test('the personal entries are not marked as admin-only', () => {
    // ``sidebar-hidden-admin-area`` is toggled by admin qualification; a member
    // page must not carry it or the entry would disappear for members.
    const html = fs.readFileSync(CHAT_HTML, 'utf8');
    for (const id of VIEW_IDS) {
        const from = html.indexOf(`data-view="${id}"`);
        const lineStart = html.lastIndexOf('<a ', from);
        const lineEnd = html.indexOf('>', from);
        const tag = html.slice(lineStart, lineEnd);
        assert.ok(!tag.includes('sidebar-hidden-admin-area'),
            `${id} must stay visible to members`);
    }
});

test('console.js signs every personal view id with its console page key', () => {
    const source = fs.readFileSync(CONSOLE, 'utf8');
    for (let i = 0; i < VIEW_IDS.length; i += 1) {
        const pattern = new RegExp(
            `'${VIEW_IDS[i]}':\\s*\\{[^}]*console:\\s*'${PAGE_IDS[i]}'`);
        assert.ok(pattern.test(source), `${VIEW_IDS[i]} -> ${PAGE_IDS[i]}`);
    }
});

test('the personal scripts load after the shell and its i18n', () => {
    const html = fs.readFileSync(CHAT_HTML, 'utf8');
    assert.ok(html.includes('assets/js/i18n/personal-console.js'), 'i18n namespace loaded');
    const i18nAt = html.indexOf('assets/js/i18n/personal-console.js');
    const consoleAt = html.indexOf('assets/js/console.js');
    const personalAt = html.indexOf('assets/js/personal-console.js');
    assert.ok(consoleAt > 0, 'console.js is loaded');
    assert.ok(personalAt > 0, 'personal views load');
    assert.ok(consoleAt < personalAt, 'personal-console.js loads after console.js');
    assert.ok(i18nAt > 0 && i18nAt < personalAt, 'i18n loads before the view module');
});

test('leaving an open personal form is guarded', () => {
    const source = fs.readFileSync(CONSOLE, 'utf8');
    assert.ok(source.includes('__personalConsoleDirtyGuard__'),
        'navigateTo consults the personal dirty guard');
    const personal = fs.readFileSync(PERSONAL, 'utf8');
    assert.ok(personal.includes('__personalConsoleDirtyGuard__'),
        'the guard is published by the personal module');
});

test('a direct URL may open a personal page by hash', () => {
    const source = fs.readFileSync(CONSOLE, 'utf8');
    assert.ok(/#view-/.test(source), 'the shell understands #view-<id>');
});

// ---- denial and consumer separation ---------------------------------------

test('a withheld menu grant always denies the page', () => {
    const context = { authorization_mode: 'role', console_pages: {
        'personal.tools': { menu_denied: true, available: true, read_allowed: true },
    } };
    assert.equal(api.personalPageDenied(context, 'personal.tools'), true);
});

test('a page that is neither readable nor available is denied', () => {
    const context = { authorization_mode: 'role', console_pages: {
        'personal.channels': { available: false, read_allowed: false },
    } };
    assert.equal(api.personalPageDenied(context, 'personal.channels'), true);
});

test('an unreadable but available page is not denied (read-only surface)', () => {
    const context = { authorization_mode: 'role', console_pages: {
        'personal.agents': { available: true, read_allowed: false, states: {
            read: false, config: false, execution: true } },
    } };
    assert.equal(api.personalPageDenied(context, 'personal.agents'), false);
});

test('a page the backend never signed is not guessed into a denial', () => {
    assert.equal(api.personalPageDenied({ console_pages: {} }, 'personal.memory'), false);
    assert.equal(api.personalPageDenied(null, 'personal.memory'), false);
});

test('an unknown page key reads as absent rather than denied', () => {
    const context = { console_pages: { 'personal.memory': { available: true } } };
    assert.equal(api.personalPageFor(context, 'personal.nope'), null);
});

test('platform all-mode is never treated as a denial', () => {
    const context = { authorization_mode: 'all', console_pages: {
        'personal.skills': { available: false, read_allowed: false },
    } };
    assert.equal(api.personalPageDenied(context, 'personal.skills'), false);
});

// A capability switch is a deployment state, not a grant (task 9.1): the
// platform-admin shortcut must not walk past one the deployment turned off, and
// the denial has to name *which* capability is off instead of staying generic.
test('a withdrawn capability denies a platform admin too', () => {
    const context = { authorization_mode: 'all', console_pages: {
        'personal.memory': { available: false, read_allowed: false,
            reason: 'capability_disabled',
            switches: { member_personal_console: true,
                        personal_memory_write: false } },
    } };
    assert.equal(api.personalPageDenied(context, 'personal.memory'), true);
});

test('a withdrawn capability names the switch that is off', () => {
    const context = { authorization_mode: 'role', console_pages: {
        'personal.memory': { available: false, read_allowed: false,
            reason: 'capability_disabled',
            switches: { member_personal_console: true,
                        personal_memory_write: false } },
    } };
    const denial = api.personalPageDenial(context, 'personal.memory');
    assert.equal(denial.reason, 'capability_disabled');
    assert.deepEqual(denial.switchKeys, ['personal_switch_personal_memory_write'],
        'only the switch that is actually off is named');

    const both = api.personalPageDenial({ authorization_mode: 'role',
        console_pages: { 'personal.memory': { available: false, read_allowed: false,
            reason: 'capability_disabled',
            switches: { member_personal_console: false,
                        personal_memory_write: false } } } }, 'personal.memory');
    assert.deepEqual(both.switchKeys, ['personal_switch_member_personal_console',
        'personal_switch_personal_memory_write'], 'every off switch is named, sorted');
});

test('a withdrawn capability is not confused with a missing grant', () => {
    const menu = api.personalPageDenial({ authorization_mode: 'role', console_pages: {
        'personal.tools': { menu_denied: true, available: false,
            read_allowed: false, reason: 'menu_not_granted' },
    } }, 'personal.tools');
    assert.equal(menu.reason, 'menu_not_granted');
    assert.deepEqual(menu.switchKeys, [],
        'a menu denial asks for a role, not for a capability to be enabled');
});

test('a page that may be shown has no denial verdict', () => {
    const context = { authorization_mode: 'role', console_pages: {
        'personal.skills': { available: true, read_allowed: true, switches: {
            member_personal_console: true } },
    } };
    assert.equal(api.personalPageDenial(context, 'personal.skills'), null);
    assert.equal(api.personalPageDenial({ console_pages: {} }, 'personal.skills'), null);
});

test('the header reports read/config/execute separately', () => {
    const keys = api.personalStateKeys({ states: { read: true, config: false, execution: false } });
    assert.deepEqual(keys, ['personal_state_read', 'personal_state_execution_closed']);
});

test('a closed execution is stated, not omitted', () => {
    const keys = api.personalStateKeys({ states: { read: true, config: true, execution: false } });
    assert.ok(keys.includes('personal_state_execution_closed'));
    assert.ok(!keys.includes('personal_state_execution'));
});

test('an open execution is reported with the state itself', () => {
    const keys = api.personalStateKeys({ states: { read: true, config: true, execution: true } });
    assert.deepEqual(keys, ['personal_state_read', 'personal_state_config',
        'personal_state_execution']);
});

test('a page without a states block reports no state lines', () => {
    assert.deepEqual(api.personalStateKeys({ available: true }), []);
    assert.deepEqual(api.personalStateKeys(null), []);
});

// ---- server-driven verbs ---------------------------------------------------

test('verbs are rendered from actions, in canonical order', () => {
    assert.deepEqual(
        api.personalAllowedVerbs({ delete: true, enable: true, edit: true }),
        ['edit', 'enable', 'delete']);
    assert.deepEqual(
        api.personalAllowedVerbs({ unbind: true, revoke: true, disable: true }),
        ['disable', 'revoke', 'unbind']);
});

test('a verb the server did not offer is never rendered', () => {
    assert.deepEqual(api.personalAllowedVerbs({ edit: true }), ['edit']);
    assert.ok(!api.personalAllowedVerbs({ edit: true }).includes('delete'));
});

test('an empty or malformed actions block yields no verbs', () => {
    assert.deepEqual(api.personalAllowedVerbs({}), []);
    assert.deepEqual(api.personalAllowedVerbs(null), []);
    assert.deepEqual(api.personalAllowedVerbs('delete'), []);
});

test('an unknown verb in actions is ignored, not labelled', () => {
    assert.deepEqual(api.personalAllowedVerbs({ grant_everything: true }), []);
});

test('every rendered verb has copy', () => {
    for (const verb of api.PERSONAL_VERB_ORDER) {
        assert.ok(api.personalVerbLabel(verb), `missing label for ${verb}`);
    }
    assert.equal(api.personalVerbLabel('nope'), '');
});

test('a provisioned assistant is badged as system, a private one as private', () => {
    assert.deepEqual(api.personalRowBadges({ is_system_assistant: true, scope: 'private' }),
        ['personal_badge_system']);
    assert.deepEqual(api.personalRowBadges({ scope: 'private' }), ['personal_badge_private']);
    assert.deepEqual(api.personalRowBadges({ governance_disabled: true }),
        ['personal_channels_governance_disabled']);
});

// ---- request construction --------------------------------------------------

test('enabling and disabling an agent updates that agent', () => {
    const on = api.personalActionRequest('personal-agents', 'enable', { id: 'a1' }, {});
    assert.equal(on.path, '/api/agents');
    assert.equal(on.method, 'POST');
    assert.deepEqual(on.body, { action: 'update', id: 'a1', enabled: true });

    const off = api.personalActionRequest('personal-agents', 'disable', { id: 'a1' }, {});
    assert.deepEqual(off.body, { action: 'update', id: 'a1', enabled: false });
});

test('deleting an agent targets only that agent', () => {
    const request = api.personalActionRequest('personal-agents', 'delete', { id: 'a9' }, {});
    assert.deepEqual(request.body, { action: 'delete', id: 'a9' });
});

test('creating an agent carries the name and the id the server will bind', () => {
    const request = api.personalActionRequest('personal-agents', 'create', null,
        { id: 'helper', name: 'Helper' });
    assert.equal(request.path, '/api/agents');
    assert.deepEqual(request.body,
        { action: 'create', id: 'helper', name: 'Helper', description: undefined });
});

test('channel state verbs go to the personal instance, not the tenant surface', () => {
    // The API action name is not always the console verb (bind/unbind are
    // ``start_binding``/``unlink`` there), which is exactly why this table is
    // pinned: a verb sent under the wrong name is refused as unknown.
    const actions = { enable: 'enable', disable: 'disable', revoke: 'revoke',
        bind: 'start_binding', unbind: 'unlink' };
    for (const [verb, action] of Object.entries(actions)) {
        const request = api.personalActionRequest('personal-channels', verb,
            { id: 'ci1', version: 4 }, { recent_password: 'pw' });
        assert.equal(request.path, '/api/personal/channels/ci1', verb);
        assert.deepEqual(request.body,
            { action: action, expected_version: 4, recent_password: 'pw' }, verb);
    }
});

test('registering a personal channel posts to the personal collection', () => {
    const request = api.personalActionRequest('personal-channels', 'create', null, {
        channel_type: 'feishu', display_name: '我的飞书', agent_id: 'a1',
        credentials: { app_id: 'x', app_secret: 'y' }, recent_password: 'pw',
    });
    assert.equal(request.path, '/api/personal/channels');
    assert.deepEqual(request.body, {
        channel_type: 'feishu', display_name: '我的飞书', agent_id: 'a1',
        credentials: { app_id: 'x', app_secret: 'y' }, recent_password: 'pw',
    });
});

test('editing a channel keeps the current name when none is entered', () => {
    const request = api.personalActionRequest('personal-channels', 'edit',
        { id: 'ci1', display_name: '旧名', version: 2 }, {});
    assert.equal(request.path, '/api/personal/channels/ci1');
    assert.equal(request.body.action, 'update',
        'the instance endpoint names this action update, not edit');
    assert.equal(request.body.display_name, '旧名');
    assert.equal(request.body.expected_version, 2);
});

test('memory writes carry the revision the server compares', () => {
    const save = api.personalActionRequest('personal-memory', 'edit',
        { id: 'MEMORY.md' }, { content: 'hello', revision: 'rev-1' });
    assert.deepEqual(save.body,
        { action: 'save', id: 'MEMORY.md', content: 'hello', revision: 'rev-1' });

    const remove = api.personalActionRequest('personal-memory', 'delete',
        { id: 'MEMORY.md' }, { revision: 'rev-1' });
    assert.deepEqual(remove.body,
        { action: 'delete', id: 'MEMORY.md', revision: 'rev-1' });
});

test('a personal resource write never names a public field', () => {
    const request = api.personalActionRequest('personal-tools', 'configure',
        { resource_id: 'builtin:echo' }, { params: { timeout: 5 }, secret: 'tok' });
    assert.equal(request.path, '/api/personal/resources');
    assert.deepEqual(Object.keys(request.body).sort(),
        ['action', 'params', 'resource_id', 'resource_kind', 'secret']);
    assert.equal(request.body.resource_kind, 'tool');
    assert.equal(request.body.action, 'save');
});

test('clearing a personal resource is scoped to the kind and id', () => {
    const request = api.personalActionRequest('personal-skills', 'clear',
        { resource_id: 'custom:writer' }, {});
    assert.deepEqual(request.body,
        { action: 'clear', resource_kind: 'skill', resource_id: 'custom:writer' });
});

test('an unimplemented verb yields no request instead of a guess', () => {
    assert.equal(api.personalActionRequest('personal-memory', 'enable', { id: 'x' }, {}), null);
    assert.equal(api.personalActionRequest('personal-tools', 'create', null, {}), null);
    assert.equal(api.personalActionRequest('personal-tools', 'edit', null, {}), null);
    assert.equal(api.personalActionRequest('nope', 'edit', null, {}), null);
    assert.equal(api.personalActionRequest('personal-agents', '', null, {}), null);
});

// ---- forms -----------------------------------------------------------------

test('a channel create form offers only ready types', () => {
    const fields = api.personalFormFields('personal-channels', 'create', null, [
        { channel_type: 'feishu', ready: true, label: { zh: '飞书' } },
        { channel_type: 'wecom', ready: false, label: { zh: '企业微信' } },
    ]);
    const typeField = fields.filter(f => f.name === 'channel_type')[0];
    assert.deepEqual(typeField.options, ['feishu']);
    assert.deepEqual(typeField.optionLabels.feishu, { zh: '飞书' });
});

test('the channel forms collect the password the write path re-proves', () => {
    // Every personal channel write re-proves presence with the member's own
    // password, so the form asks for it instead of sending a request that comes
    // back 401.
    const create = api.personalFormFields('personal-channels', 'create', null, [])
        .map(f => f.name);
    assert.deepEqual(create, ['channel_type', 'display_name', 'recent_password']);
    const edit = api.personalFormFields('personal-channels', 'edit', { id: 'ci1' }, [])
        .map(f => f.name);
    assert.deepEqual(edit, ['display_name', 'recent_password']);
});

test('the credential step is built from the selected type declaration', () => {
    const fields = api.personalChannelCredentialFields({
        channel_type: 'feishu',
        credential_fields: [
            { key: 'feishu_app_id', label: { zh: '应用 ID' }, secret: false, required: true },
            { key: 'feishu_app_secret', label: { zh: '应用密钥' }, secret: true, required: true },
        ],
    }, 'zh');
    assert.deepEqual(fields.map(f => f.name), ['feishu_app_id', 'feishu_app_secret']);
    assert.deepEqual(fields.map(f => f.type), ['text', 'password']);
    assert.deepEqual(fields.map(f => f.rawLabel), [true, true],
        'the server label is already display text, not an i18n key');
    assert.equal(fields[0].label, '应用 ID');
    assert.deepEqual(api.personalChannelCredentialFields(null, 'zh'), []);
});

test('the memory form edits content, the resource form edits params', () => {
    assert.deepEqual(api.personalFormFields('personal-memory', 'edit', { id: 'M.md' }, {})
        .map(f => f.name), ['content']);
    assert.deepEqual(api.personalFormFields('personal-tools', 'configure',
        { resource_id: 'r' }, {}).map(f => f.name), ['params', 'secret']);
});

test('credential fields come from the server declaration', () => {
    const fields = api.personalCredentialFields({
        credential_fields: [
            { key: 'app_id', label: { zh: 'App ID', en: 'App ID' }, secret: false, required: true },
            { key: 'app_secret', label: { zh: 'App Secret', en: 'App Secret' },
              secret: true, required: true },
        ],
    }, 'zh');
    assert.deepEqual(fields.map(f => f.key), ['app_id', 'app_secret']);
    assert.equal(fields[1].secret, true);
    assert.equal(fields[0].requiresSecret, undefined);
    assert.equal(fields[1].required, true);
    assert.equal(fields[0].label, 'App ID');
});

test('a type without a declaration yields no credential inputs', () => {
    assert.deepEqual(api.personalCredentialFields(null, 'zh'), []);
    assert.deepEqual(api.personalCredentialFields({}, 'zh'), []);
});

// ---- search and pagination -------------------------------------------------

test('search filters on the human fields and ignores case', () => {
    const items = [{ name: 'Alpha' }, { display_name: 'beta' }, { id: 'Gamma' }];
    assert.deepEqual(api.personalFilterItems(items, 'ALP').map(i => i.name), ['Alpha']);
    assert.deepEqual(api.personalFilterItems(items, 'bet').map(i => i.display_name), ['beta']);
    assert.equal(api.personalFilterItems(items, '').length, 3);
    assert.equal(api.personalFilterItems(items, 'zzz').length, 0);
});

test('pagination clamps the page and reports the filtered total', () => {
    const items = Array.from({ length: 25 }, (_, i) => ({ id: 'a' + i }));
    const first = api.personalPaginate(items, 1, 20);
    assert.equal(first.total, 25);
    assert.equal(first.pages, 2);
    assert.equal(first.items.length, 20);
    const second = api.personalPaginate(items, 2, 20);
    assert.equal(second.items.length, 5);
    const clamped = api.personalPaginate(items, 99, 20);
    assert.equal(clamped.page, 2);
    assert.equal(api.personalPaginate(items, 0, 20).page, 1);
});

test('an empty list still reports one page and no items', () => {
    const page = api.personalPaginate([], 3, 20);
    assert.equal(page.total, 0);
    assert.equal(page.page, 1);
    assert.deepEqual(page.items, []);
});

test('row titles fall back so a row is never blank', () => {
    assert.equal(api.personalRowTitle({ display_name: 'A' }), 'A');
    assert.equal(api.personalRowTitle({ resource_id: 'builtin:echo' }), 'builtin:echo');
    assert.equal(api.personalRowTitle({ id: 'ci1' }), 'ci1');
    assert.equal(api.personalRowTitle(null), '');
});

test('a channel type label follows the active language', () => {
    const item = { channel_type: 'feishu', label: { zh: '飞书', en: 'Feishu' } };
    assert.equal(api.personalTypeLabel(item, 'zh'), '飞书');
    assert.equal(api.personalTypeLabel(item, 'en'), 'Feishu');
    assert.equal(api.personalTypeLabel({ channel_type: 'x' }, 'zh'), 'x');
});

test('the total line is a formatted count', () => {
    assert.equal(api.personalTotalText(0, key => `${key}:{n}`), 'personal_total:0');
    assert.equal(api.personalTotalText(7, key => `${key}:{n}`), 'personal_total:7');
});

// ---- i18n ------------------------------------------------------------------

test('every personal key exists in all three languages', () => {
    const dict = loadDictionaries();
    const personal = require('./support/i18n_namespaces.cjs')
        .loadNamespaces()['personal-console'];
    const keys = Object.keys(personal.zh);
    assert.ok(keys.length > 40, 'the namespace is populated');
    for (const key of keys) {
        for (const lang of ['zh', 'zh-Hant', 'en']) {
            assert.ok(personal[lang][key], `${lang} missing ${key}`);
            assert.equal(dict[lang][key], personal[lang][key], `${lang}/${key} merged`);
        }
    }
});

test('the personal copy never describes member capability as system administration', () => {
    const personal = require('./support/i18n_namespaces.cjs')
        .loadNamespaces()['personal-console'];
    // A *negated* mention is the point of ``personal_scope_hint`` ("needs no
    // system-administration rights"); what must not appear is copy that presents
    // a member page as an administrative capability.
    const zhClaim = /(?<![不无沒没])需要(系统管理|系統管理)/;
    const enClaim = /(requires?|needs?) system[- ]administration/i;
    for (const lang of ['zh', 'zh-Hant', 'en']) {
        for (const [key, value] of Object.entries(personal[lang])) {
            assert.ok(!zhClaim.test(value) && !enClaim.test(value),
                `${lang}/${key} reads like an admin privilege: ${value}`);
        }
    }
});

test('the view and page labels are translated in every language', () => {
    const dict = loadDictionaries();
    for (const lang of ['zh', 'zh-Hant', 'en']) {
        for (const key of ['nav_group_personal', 'menu_personal_agents',
            'menu_personal_channels', 'menu_personal_memory', 'menu_personal_tools',
            'menu_personal_skills', 'personal_scope_self', 'personal_scope_hint']) {
            assert.ok(dict[lang][key], `${lang} missing ${key}`);
        }
    }
});

test('the shell data-i18n attributes all resolve', () => {
    const dict = loadDictionaries();
    const html = fs.readFileSync(CHAT_HTML, 'utf8');
    const keys = [...html.matchAll(/data-i18n="([^"]+)"/g)].map(m => m[1]);
    for (const key of keys) {
        if (!key.startsWith('personal') && !key.startsWith('menu_personal')
            && key !== 'nav_group_personal') continue;
        for (const lang of ['zh', 'zh-Hant', 'en']) {
            assert.ok(dict[lang][key], `${lang} missing shell key ${key}`);
        }
    }
});
