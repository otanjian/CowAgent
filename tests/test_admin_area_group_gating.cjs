// Admin-area group gating after the console entry stopped being admin-only
// (change unify-console-by-data-scope, task 3.1).
//
// Removing the tenant_admin-only gate on the 控制台 entry means an ordinary
// member can reach the admin area. The area's *groups* are still rendered from
// the same authoritative projection, and the console must never leave an empty
// 组织与权限 / 模型与接入 heading behind: console-information-architecture requires
// 实际菜单 to show only pages the identity may actually read, and forbids empty
// groups. So a group is visible exactly when at least one of its pages is.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../channel/web/static/js/console.js'), 'utf8');

// Extract one top-level function by brace matching (the navigation section has
// many neighbours and `section()` boundaries would drag in unrelated deps).
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

function makeEl(attrs = {}) {
    const classes = new Set();
    return {
        _attrs: { ...attrs },
        _items: [],
        getAttribute(k) { return this._attrs[k] === undefined ? null : this._attrs[k]; },
        classList: {
            contains(c) { return classes.has(c); },
            add(c) { classes.add(c); },
            remove(c) { classes.delete(c); },
            toggle(c, on) { if (on) classes.add(c); else classes.delete(c); return on; },
        },
        querySelectorAll(selector) {
            if (selector === '.sidebar-item[data-view]') return this._items;
            return [];
        },
        querySelector(selector) { return (this.querySelectorAll(selector) || [])[0] || null; },
    };
}

// Model the real markup: each `.menu-group` owns its own `.sidebar-item`s, and
// the group element is what carries `sidebar-hidden-admin-area`.
function boot({ ctx = null, isPlatformAdmin = false, groups = [], area = 'workbench' } = {}) {
    const allItems = [];
    groups.forEach(g => { g.el._items = g.items; allItems.push(...g.items); });
    const adminGroups = groups.map(g => g.el);
    const platformGroups = groups.filter(g => g.platform).map(g => g.el);
    // A holder, not a captured value: re-running the projection against a changed
    // context is what proves visibility is recomputed rather than accumulated.
    const ctxRef = { value: ctx };
    const selectors = {
        '#sidebar-nav .sidebar-hidden-admin-area': adminGroups,
        '#sidebar-nav .menu-group.sidebar-hidden-admin-area': adminGroups,
        '#sidebar-nav .sidebar-hidden-platform-scope': platformGroups,
        '#sidebar-nav .sidebar-item[data-view]': allItems,
    };
    const navOpenAdmin = makeEl();
    const sandbox = {
        VIEW_META: {
            agents: { console: 'admin.agents' },
            skills: { console: 'admin.skills' },
            memory: { console: 'admin.memory' },
            system_user: { console: 'admin.members' },
            org: { console: 'admin.organization' },
            roles: { console: 'admin.roles' },
        },
        document: {
            getElementById(id) { return id === 'nav-open-admin' ? navOpenAdmin : null; },
            querySelectorAll(selector) { return selectors[selector] || []; },
            querySelector(selector) { return (selectors[selector] || [])[0] || null; },
        },
        location: {
            pathname: area === 'admin' ? '/admin' : '/chat',
            replaced: null,
            replace(url) { this.replaced = url; },
            assign(url) { this.replaced = url; },
        },
        sessionStorage: { _m: {}, setItem(k, v) { this._m[k] = v; }, getItem(k) { return this._m[k] || null; } },
        _identityMode: () => 'database',
        _baseAuthContext: () => ctxRef.value,
        _baseAccountSelf: () => ({ user: { is_platform_admin: isPlatformAdmin } }),
        _navAreaFromPath: () => area,
        _openNavArea() {},
    };
    vm.runInNewContext(
        [fnSource('_consolePageForView'), fnSource('_viewNavDenied'),
         fnSource('_sidebarRecentDenied'), fnSource('_qualifyAdminConsoleEntry'),
         fnSource('_applySidebarPermissions')].join('\n'),
        sandbox);
    return { sandbox, groups, navOpenAdmin, ctxRef };
}

const READABLE_AGENT = { available: true, read_allowed: true, scope: 'agent', reason: '', actions: {} };
const DENIED_MEMBERS = { available: false, read_allowed: false, scope: 'tenant', reason: 'no_permission', actions: {} };

function agentDevGroup() {
    return {
        el: makeEl({ 'data-group': 'agent-dev' }),
        items: [makeEl({ 'data-view': 'agents' }), makeEl({ 'data-view': 'skills' })],
    };
}

function orgPermGroup() {
    return {
        el: makeEl({ 'data-group': 'org-perm' }),
        items: [makeEl({ 'data-view': 'system_user' }), makeEl({ 'data-view': 'org' }),
                makeEl({ 'data-view': 'roles' })],
    };
}

// A member's projection: 智能体管理 is reachable, 组织与权限 is not.
function memberCtx(extra = {}) {
    return {
        authorization_mode: 'role',
        console_pages: {
            'admin.agents': { ...READABLE_AGENT, scope: 'agent' },
            'admin.members': DENIED_MEMBERS,
            'admin.organization': DENIED_MEMBERS,
            'admin.roles': DENIED_MEMBERS,
            ...extra,
        },
    };
}

test('a member gets the console entry and only the groups holding a reachable page', () => {
    const agentDev = agentDevGroup();
    const orgPerm = orgPermGroup();
    const { sandbox, navOpenAdmin } = boot({ ctx: memberCtx(), groups: [agentDev, orgPerm] });
    sandbox._applySidebarPermissions();
    assert.equal(navOpenAdmin.classList.contains('hidden'), false,
        '控制台 entry must be reachable for a member whose role reaches 智能体管理');
    assert.equal(agentDev.el.classList.contains('hidden'), false,
        '智能体开发 must stay for a member that may read 智能体管理');
    assert.equal(orgPerm.el.classList.contains('hidden'), true,
        '组织与权限 must not leave an empty heading for a member');
});

test('a tenant admin keeps every non-platform group', () => {
    const agentDev = agentDevGroup();
    const orgPerm = orgPermGroup();
    const { sandbox, navOpenAdmin } = boot({
        ctx: {
            authorization_mode: 'role',
            is_tenant_admin: true,
            console_pages: {
                'admin.agents': READABLE_AGENT,
                'admin.skills': { ...READABLE_AGENT, scope: 'agent' },
                'admin.members': { ...READABLE_AGENT, scope: 'tenant' },
                'admin.organization': { ...READABLE_AGENT, scope: 'tenant' },
                'admin.roles': { ...READABLE_AGENT, scope: 'tenant' },
            },
        },
        groups: [agentDev, orgPerm],
    });
    sandbox._applySidebarPermissions();
    assert.equal(navOpenAdmin.classList.contains('hidden'), false);
    assert.equal(agentDev.el.classList.contains('hidden'), false);
    assert.equal(orgPerm.el.classList.contains('hidden'), false);
});

test('an identity with no reachable page gets neither the entry nor its groups', () => {
    const agentDev = agentDevGroup();
    const orgPerm = orgPermGroup();
    const denied = { ...DENIED_MEMBERS, scope: 'agent' };
    const { sandbox, navOpenAdmin } = boot({
        ctx: memberCtx({
            'admin.agents': denied, 'admin.skills': denied, 'admin.memory': denied,
        }),
        groups: [agentDev, orgPerm],
    });
    sandbox._applySidebarPermissions();
    assert.equal(navOpenAdmin.classList.contains('hidden'), true);
    assert.equal(agentDev.el.classList.contains('hidden'), true);
    assert.equal(orgPerm.el.classList.contains('hidden'), true);
});

test('a qualified member opening /admin directly is not bounced back to /chat', () => {
    const { sandbox } = boot({ ctx: memberCtx(), groups: [agentDevGroup()], area: 'admin' });
    sandbox._applySidebarPermissions();
    assert.equal(sandbox.location.replaced, null);
    assert.equal(sandbox.sessionStorage.getItem('cow_nav_admin_denied'), null);
});

test('an unqualified identity opening /admin is still bounced back to /chat', () => {
    const denied = { ...DENIED_MEMBERS, scope: 'agent' };
    const { sandbox } = boot({
        ctx: memberCtx({ 'admin.agents': denied }), groups: [agentDevGroup()], area: 'admin',
    });
    sandbox._applySidebarPermissions();
    assert.equal(sandbox.location.replaced, '/chat');
    assert.equal(sandbox.sessionStorage.getItem('cow_nav_admin_denied'), '1');
});

test('group visibility is recomputed, never accumulated', () => {
    // Re-running the projection after a grant is withdrawn must hide the group
    // again: the console computes visibility, it does not only ever reveal.
    const agentDev = agentDevGroup();
    const orgPerm = orgPermGroup();
    const { sandbox, ctxRef } = boot({ ctx: memberCtx(), groups: [agentDev, orgPerm] });
    sandbox._applySidebarPermissions();
    assert.equal(agentDev.el.classList.contains('hidden'), false);
    // Withdraw every page of the group and re-run. Both keys are named: an item
    // whose page the projection does not sign is deliberately left visible
    // ("unknown key -> don't guess"), so leaving 工具与技能 out would keep the
    // group reachable and the scenario would not test the recompute at all.
    ctxRef.value = memberCtx({
        'admin.agents': { ...DENIED_MEMBERS, scope: 'agent' },
        'admin.skills': { ...DENIED_MEMBERS, scope: 'agent' },
    });
    sandbox._applySidebarPermissions();
    assert.equal(agentDev.el.classList.contains('hidden'), true);
    assert.equal(orgPerm.el.classList.contains('hidden'), true);
});
