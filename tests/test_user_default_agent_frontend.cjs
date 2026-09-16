// Exercise the console's two "default Agent" actions, which are deliberately
// separate acts:
//
// * `setAgentAsMyDefault` — the *user's own* preference, offered for every Agent
//   the caller may manage and available to every user; it posts
//   {action:"set_user_default"} together with the revision it read, so a stale
//   form fails loudly instead of overwriting a newer choice.
// * `setAgentAsDefault` — the *tenant's* default, a management act, offered only
//   when the server's payload says the caller may appoint it.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(
    path.join(__dirname, '../channel/web/static/js/console.js'), 'utf8');

function section(start, end) {
    const from = source.indexOf(start), to = source.indexOf(end, from);
    assert.ok(from >= 0 && to > from, `Missing console section ${start}`);
    return source.slice(from, to);
}

function node() {
    const classes = new Set([]);
    return {
        innerHTML: '', textContent: '', dataset: {},
        classList: {
            add: (...x) => x.forEach(v => classes.add(v)),
            remove: (...x) => x.forEach(v => classes.delete(v)),
            contains: x => classes.has(x),
            toggle: (x, on) => (on ? classes.add(x) : classes.delete(x)),
        },
        querySelector: () => null,
        querySelectorAll: () => [],
        focus() {},
    };
}

const agent = (extra = {}) => ({
    id: 'beta', name: 'Beta', description: '', category: 'beta', tags: [],
    greeting: '', persona_summary: '', scene_id: '', enabled: true, ...extra,
});

// Load renderAgentDetail with just enough context for its own template.
function renderCtx({ defaultAgentId, userDefault, tenantDefaultManageable,
                     defaultResolution, current }) {
    const nodes = new Map();
    const ctx = {
        console,
        selectedAdminAgentId: 'beta',
        defaultAgentId,
        userDefault: userDefault || { agent_id: '', revision: null, origin: null },
        tenantDefaultManageable: tenantDefaultManageable === true,
        defaultResolution: defaultResolution || { agent_id: '', source: null },
        t: key => key,
        escapeHtml: x => String(x),
        findAgent: id => (id === 'beta' ? agent(current) : null),
        agentAvatarHTML: () => '<img class="agent-avatar" alt="">',
        fieldLabelWithTip: label => String(label),
        renderAvatarPicker: () => {},
        initDropdown: () => {},
        refreshAgentCategoryDropdown: () => {},
        refreshAgentSceneDropdown: () => {},
        paintAgentSavedFlash: () => {},
        agentModelDropdownOptions: () => [],
        _sessCfg: { model: { providers: [] } },
        document: {
            getElementById(id) {
                if (!nodes.has(id)) nodes.set(id, node());
                return nodes.get(id);
            },
        },
    };
    vm.createContext(ctx);
    vm.runInContext(
        section('function agentAnchorHintText()', 'function agentWorkbenchCardHTML(')
        + section('function renderAgentDetail()', 'function renderAvatarPicker('), ctx);
    return { ctx, node: id => ctx.document.getElementById(id) };
}

// -- the caller's own default ------------------------------------------------

test('every user is offered "set as my default" for a manageable Agent', () => {
    const { ctx, node } = renderCtx({ defaultAgentId: 'alpha' });

    ctx.renderAgentDetail();

    const html = node('agent-detail-profile').innerHTML;
    assert.ok(html.includes('setAgentAsMyDefault'), (
        'the detail pane must offer the caller\'s own preference'));
    assert.ok(html.includes('agents_set_my_default'), 'the action must be labelled');
});

test('the Agent that already is my default offers no "set as my default"', () => {
    const { ctx, node } = renderCtx({
        defaultAgentId: 'beta',
        userDefault: { agent_id: 'beta', revision: 3, origin: 'user' },
    });

    ctx.renderAgentDetail();

    const html = node('agent-detail-profile').innerHTML;
    assert.ok(!html.includes('setAgentAsMyDefault'), (
        'an Agent already registered as mine must not offer to become it again'));
});

test('a fallback anchor is not mistaken for my own choice', () => {
    // `defaultAgentId` is the *resolved* anchor: a member with no registered
    // preference still has one (the tenant's, or the first shared Agent). The
    // button must follow the registered pointer, not the fallback, or the pane
    // would claim a choice nobody made and hide the action that could make it.
    const { ctx, node } = renderCtx({ defaultAgentId: 'beta' });

    ctx.renderAgentDetail();

    const html = node('agent-detail-profile').innerHTML;
    assert.ok(html.includes('setAgentAsMyDefault'), (
        'an unregistered fallback must still allow registering a preference'));
});

test('a disabled Agent offers no "set as my default"', () => {
    // The server refuses a disabled target (agent_not_usable), so offering the
    // action would be a button that can only fail.
    const { ctx, node } = renderCtx({ defaultAgentId: 'alpha', current: { enabled: false } });

    ctx.renderAgentDetail();

    const html = node('agent-detail-profile').innerHTML;
    assert.ok(!html.includes('setAgentAsMyDefault'), (
        'a stopped Agent cannot become a default'));
});

// -- the tenant's default ----------------------------------------------------

test('the tenant default is offered only when the caller may appoint it', () => {
    const allowed = renderCtx({ defaultAgentId: 'alpha', tenantDefaultManageable: true });
    allowed.ctx.renderAgentDetail();
    const allowedHtml = allowed.node('agent-detail-profile').innerHTML;
    assert.ok(allowedHtml.includes('setAgentAsDefault'));
    assert.ok(allowedHtml.includes('agents_set_tenant_default'), (
        'the tenant action needs its own label, distinct from my own default'));

    const refused = renderCtx({ defaultAgentId: 'alpha', tenantDefaultManageable: false });
    refused.ctx.renderAgentDetail();
    const refusedHtml = refused.node('agent-detail-profile').innerHTML;
    assert.ok(!refusedHtml.includes('setAgentAsDefault'), (
        'a caller who may not appoint the tenant default must not be offered it'));
});

test('the current tenant default offers no "set as tenant default"', () => {
    const { ctx, node } = renderCtx({
        defaultAgentId: 'beta', tenantDefaultManageable: true,
    });

    ctx.renderAgentDetail();

    const html = node('agent-detail-profile').innerHTML;
    assert.ok(!html.includes('setAgentAsDefault'), (
        'the Agent that already is the tenant default must not offer to become it'));
});

// -- the writes --------------------------------------------------------------

function writeCtx({ userDefault, response }) {
    const calls = [];
    const statusNode = node();
    const ctx = {
        console,
        t: key => key,
        selectedAdminAgentId: 'beta',
        userDefault: userDefault || { agent_id: '', revision: null, origin: null },
        fetch: async (url, opts) => {
            calls.push({ url, opts });
            return { ok: true, json: async () => response };
        },
        loadAgentCatalog: async () => { calls.push({ url: 'loadAgentCatalog' }); },
        renderAgentsGrid: () => { calls.push({ url: 'renderAgentsGrid' }); },
        renderAgentDetail: () => { calls.push({ url: 'renderAgentDetail' }); },
        document: { getElementById: () => statusNode },
    };
    vm.createContext(ctx);
    vm.runInContext(
        section('function setAgentAsMyDefault(', 'function setAgentAsDefault('), ctx);
    return { ctx, calls, statusNode };
}

test('setAgentAsMyDefault posts the user action with the revision it read', async () => {
    const { ctx, calls } = writeCtx({
        userDefault: { agent_id: 'alpha', revision: 4, origin: 'user' },
        response: { status: 'success', result: { changed: true } },
    });

    await ctx.setAgentAsMyDefault('beta');

    const post = calls.find(c => c.opts && String(c.opts.body).includes('set_user_default'));
    assert.ok(post, 'a set_user_default write must be issued');
    assert.equal(post.url, '/api/agents');
    const body = JSON.parse(post.opts.body);
    assert.equal(body.id, 'beta');
    assert.equal(body.default_revision, 4, (
        'the pointer revision must be round-tripped so a stale form is refused'));
    assert.ok(!('user_id' in body) && !('tenant_id' in body), (
        'the subject is the session, never the body'));
    assert.ok(calls.some(c => c.url === 'loadAgentCatalog'), (
        'the catalogue must be reloaded so the resolved anchor moves everywhere'));
});

test('a first choice sends no revision', async () => {
    const { ctx, calls } = writeCtx({
        userDefault: { agent_id: '', revision: null, origin: null },
        response: { status: 'success', result: { changed: true } },
    });

    await ctx.setAgentAsMyDefault('beta');

    const post = calls.find(c => c.opts && String(c.opts.body).includes('set_user_default'));
    assert.equal(JSON.parse(post.opts.body).default_revision, null, (
        'null only means "nothing registered yet", which the server accepts once'));
});

test('the marker moves only after the server accepts it', async () => {
    const { ctx, calls } = writeCtx({
        userDefault: { agent_id: 'alpha', revision: 4, origin: 'user' },
        response: { status: 'error', code: 'version_conflict' },
    });

    const ok = await ctx.setAgentAsMyDefault('beta');

    assert.equal(ok, false, 'a refused choice must report failure');
    assert.ok(!calls.some(c => c.url === 'loadAgentCatalog'), (
        'a refusal must not reload (or repaint) as if the preference had moved'));
});

test('every refusal code surfaces its own wording', async () => {
    const expected = {
        version_conflict: 'agents_set_my_default_conflict',
        agent_not_usable: 'agents_set_my_default_disabled',
        forbidden: 'agents_set_my_default_forbidden',
    };
    for (const [code, key] of Object.entries(expected)) {
        const { ctx, statusNode } = writeCtx({
            userDefault: { agent_id: 'alpha', revision: 1, origin: 'provisioned' },
            response: { status: 'error', code },
        });

        const ok = await ctx.setAgentAsMyDefault('beta');

        assert.equal(ok, false, `${code} must report failure`);
        assert.equal(statusNode.textContent, key, (
            `${code} must be explained by its own message, not a generic failure`));
    }
});

// -- where the anchor came from (task 4.6) -----------------------------------

test('the anchor hint names the source and appears only on the anchor', () => {
    const anchored = renderCtx({
        defaultAgentId: 'beta',
        defaultResolution: { agent_id: 'beta', source: 'tenant' },
    });
    anchored.ctx.renderAgentDetail();
    assert.ok(anchored.node('agent-detail-profile').innerHTML
        .includes('agents_anchor_source_tenant'), (
        'the Agent a new session lands on must say who made it the anchor'));

    const other = renderCtx({
        defaultAgentId: 'alpha',
        defaultResolution: { agent_id: 'alpha', source: 'tenant' },
    });
    other.ctx.renderAgentDetail();
    assert.ok(!other.node('agent-detail-profile').innerHTML
        .includes('agent-anchor-hint'), (
        'an Agent that is not the anchor must not claim to be one'));
});

test('a fallback anchor is worded as a fallback, never as a decision', () => {
    // The distinction the payload exists for: `shared`/`any` mean nobody chose
    // this Agent. Wording them like the tenant's configuration would tell an
    // operator the opposite of the truth.
    for (const source of ['shared', 'own']) {
        const { ctx, node } = renderCtx({
            defaultAgentId: 'beta',
            defaultResolution: { agent_id: 'beta', source },
        });

        ctx.renderAgentDetail();

        const html = node('agent-detail-profile').innerHTML;
        assert.ok(html.includes(`agents_anchor_source_${source}`), (
            `${source} must be explained by its own message`));
        assert.ok(!html.includes('agents_anchor_source_tenant'), (
            `${source} must not borrow the tenant's authority`));
    }
});

test('the users own choice reports itself, not the fallback it displaces', () => {
    const { ctx, node } = renderCtx({
        defaultAgentId: 'beta',
        userDefault: { agent_id: 'beta', revision: 2, origin: 'user' },
        defaultResolution: { agent_id: 'beta', source: 'user' },
    });

    ctx.renderAgentDetail();

    assert.ok(node('agent-detail-profile').innerHTML
        .includes('agents_anchor_source_user'));
});

test('an unknown source falls back to neutral wording instead of a guess', () => {
    // A newer server may add a source this build does not know. Saying nothing
    // useful is recoverable; claiming "the tenant configured this" is not.
    const { ctx, node } = renderCtx({
        defaultAgentId: 'beta',
        defaultResolution: { agent_id: 'beta', source: 'something_new' },
    });

    ctx.renderAgentDetail();

    const html = node('agent-detail-profile').innerHTML;
    assert.ok(html.includes('agents_anchor_source_unknown'));
    assert.ok(!html.includes('agents_anchor_source_tenant'));
    assert.ok(!html.includes('agents_anchor_source_user'));
});

test('no resolution at all renders no hint', () => {
    const { ctx, node } = renderCtx({ defaultAgentId: 'beta' });

    ctx.renderAgentDetail();

    assert.ok(!node('agent-detail-profile').innerHTML.includes('agent-anchor-hint'));
});
