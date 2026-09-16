// Personal console in a real browser (change enable-member-personal-console,
// task 8.6: "桌面/窄屏交互测试").
//
// Task 8.8 retired the independent member personal console: the module, its
// loader and the issuance of the five `personal.*` page ids are gone, and each
// retired `#view-personal-*` address now forwards to the shared page that
// carries the same objects (task 8.1). This file still drives the *production
// page* at two widths, because what is left to prove only exists once the shell,
// the CSS and the modules run together:
//
//   * a retired address resolves as an address — the shell lands on the shared
//     view and `personal-console.js` is never fetched;
//   * no retired page is ever mounted, even though this fixture's projection
//     still carries the five `personal.*` ids (a stale or hostile payload must
//     not resurrect a page the server no longer signs);
//   * no retired consumer is started by any navigation, denial or withdrawal;
//   * the account panel keeps no host for them at either width.
//
// Requires Playwright with an installed Chromium; no request reaches a live
// service:
//   NODE_PATH=.../node_modules node tests/test_personal_console_browser.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');

let chromium;
try {
    ({ chromium } = require('playwright'));
} catch (error) {
    // Without Playwright the file still loads, so a wrapper can skip it.
    console.log('SKIP personal console browser contract: playwright is not installed');
    process.exit(0);
}

const repo = path.resolve(__dirname, '..');
const staticRoot = path.join(repo, 'channel/web/static');
const output = process.env.COW_PERSONAL_BROWSER_OUTPUT
    || fs.mkdtempSync(path.join(os.tmpdir(), 'cow-personal-browser-'));
const report = { fixtureOnly: true, scenarios: [], pageErrors: [], requests: [], unexpectedRoutes: [] };
fs.mkdirSync(output, { recursive: true });

const TENANT = { id: 'fixture-tenant', name: '测试租户', code: 'fixture' };
const mime = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.ico': 'image/x-icon',
    '.png': 'image/png', '.jpg': 'image/jpeg', '.woff2': 'font/woff2', '.woff': 'font/woff',
    '.ttf': 'font/ttf' };

// One projection per scenario, so a denial can be added without touching the
// rows the accepting scenarios use.
function personalPages(overrides = {}) {
    const base = {
        'workbench.chat': { available: true, read_allowed: true, scope: 'self' },
        'workbench.agents': { available: true, read_allowed: true, scope: 'self' },
        'workbench.history': { available: true, read_allowed: true, scope: 'self' },
        // The shared pages that carry the retired member surfaces now (task 8.1):
        // a member reaches agents / channels / memory / skills through these ids,
        // with the range decided by the server's object scope.
        'admin.agents': { available: true, read_allowed: true, menu_denied: false,
            scope: 'agent', actions: { create: true, update: true } },
        'admin.channels': { available: true, read_allowed: true, menu_denied: false,
            scope: 'self', actions: { create: true, update: true },
            switches: { member_personal_console: true, personal_channel_onboarding: true },
            states: { read: true, config: true, execution: false } },
        'admin.memory': { available: true, read_allowed: true, menu_denied: false,
            scope: 'agent', actions: {} },
        'admin.skills': { available: true, read_allowed: true, menu_denied: false,
            scope: 'agent', actions: {} },
        // The five retired ids are served **on purpose** (task 8.8). The server
        // does not sign them any more, so this fixture is the hostile case: a
        // payload that still carries them must not give the shell a host, a
        // mount point or a consumer to start.
        'personal.agents': { available: true, read_allowed: true, menu_denied: false,
            scope: 'self', actions: { create: true, update: true, enable: true },
            states: { read: true, config: true, execution: true } },
        'personal.channels': { available: true, read_allowed: true, menu_denied: false,
            scope: 'self', actions: { create: true, update: true, enable: true },
            states: { read: true, config: true, execution: false } },
        'personal.memory': { available: true, read_allowed: true, menu_denied: false,
            scope: 'self', actions: { update: true, delete: true },
            states: { read: true, config: true, execution: true } },
        'personal.tools': { available: true, read_allowed: true, menu_denied: false,
            scope: 'self', actions: { configure: true },
            states: { read: true, config: true, execution: true } },
        'personal.skills': { available: true, read_allowed: true, menu_denied: false,
            scope: 'self', actions: { configure: true },
            states: { read: true, config: true, execution: true } },
    };
    return Object.assign(base, overrides);
}

//: The five retired view ids and the shared view each one forwards to
//: (`console.js` ``LEGACY_PERSONAL_FORWARD`` / ``VIEW_META``, task 8.1).
const RETIRED_ADDRESSES = {
    'personal-agents': 'agents',
    'personal-channels': 'channels',
    'personal-memory': 'memory',
    'personal-tools': 'skills',
    'personal-skills': 'skills',
};

//: Every endpoint the retired module used to call. Not one of them may be
//: requested any more: the pages they served do not exist, and the thin
//: adapters are kept for external callers only (task 8.8).
const RETIRED_ENDPOINTS = ['/api/memory/personal', '/api/memory/personal/content',
    '/api/personal/channels', '/api/personal/resources'];

const memoryRows = [
    { id: 'MEMORY.md', title: 'MEMORY.md', revision: 'rev-1',
      actions: { edit: true, delete: true } },
    { id: 'preferences.md', title: 'preferences.md', revision: 'rev-2',
      actions: { edit: true, delete: true } },
];
const agentRows = [
    { id: 'mine-1', name: '我的助手', scope: 'private', origin: 'user_created',
      actions: { edit: true, enable: true, delete: true, configure_personal: true } },
];
const channelRows = [
    { id: 'ci-1', channel_type: 'feishu', display_name: '我的飞书', version: 3,
      active: false, governance_disabled: false, binding: { status: 'linked' },
      actions: { edit: true, enable: true, revoke: true, unbind: true } },
];
const resourceRows = [
    { resource_kind: 'tool', resource_id: 'builtin:echo', name: 'echo',
      configured: false, params: {}, actions: { configure: true, clear: false } },
];

let scenarioPages = personalPages();
// A projection that cannot be read (task 3.2): the panel must offer a retry and
// never treat an unconfirmed entry as activatable.
let contextFailure = null;
// Layout-only presentation switch (classic|split); it must never duplicate an
// entry or change authorization.
let navigationMode = 'classic';
// A console-qualified identity for the "enter a personal page from /admin" case.
let tenantAdmin = false;

const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://fixture');
    const pathname = url.pathname;
    if (!pathname.startsWith('/assets/')) report.requests.push({ pathname, query: url.search, method: req.method });
    const json = (data, status = 200) => {
        res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
        res.end(JSON.stringify(data));
    };
    const identity = { status: 'success', identity_mode: 'database', auth_required: true,
        authenticated: true, user: { username: 'personal-member', display_name: '普通成员',
            roles: [tenantAdmin ? 'tenant_admin' : 'member'], is_admin: tenantAdmin } };

    if (pathname === '/chat' || pathname === '/' || pathname === '/admin') {
        res.writeHead(200, { 'Content-Type': mime['.html'], 'Cache-Control': 'no-store' });
        res.end(fs.readFileSync(path.join(repo, 'channel/web/chat.html'), 'utf8')
            .replaceAll('{{COW_DEFAULT_LANG}}', 'zh')
            .replaceAll('{{COW_NAVIGATION_MODE}}', navigationMode));
    } else if (pathname.startsWith('/assets/')) {
        const file = path.resolve(staticRoot, '.' + pathname.slice('/assets'.length));
        if (!file.startsWith(staticRoot + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
            report.unexpectedRoutes.push({ pathname, method: req.method });
            json({ status: 'error', message: 'Missing fixture asset' }, 404);
        } else {
            res.writeHead(200, { 'Content-Type': mime[path.extname(file)] || 'application/octet-stream' });
            fs.createReadStream(file).pipe(res);
        }
    } else if (pathname === '/auth/check') json(identity);
    else if (pathname === '/auth/login') json({ ...identity, tenants: [TENANT] });
    else if (pathname === '/auth/logout') json({ status: 'success' });
    else if (pathname === '/auth/me') json({ ...identity, tenants: [TENANT], current_tenant: TENANT });
    else if (pathname === '/auth/context') contextFailure
        ? json({ status: 'error', message: 'projection unavailable' }, contextFailure)
        : json({ status: 'success', authorization_mode: 'role',
        is_tenant_admin: tenantAdmin, is_platform_admin: false, tenant: TENANT,
        console_pages: scenarioPages });
    else if (pathname === '/api/version') json({ status: 'success', version: 'personal-fixture' });
    else if (pathname === '/api/branding/public') json({ enabled: false, revision: 1, logo_url: '', favicon_url: '' });
    else if (pathname === '/config') json({ status: 'success', title: '容大AI', model: 'fixture-model',
        providers: {}, agent_permission_mode: 'workspace-write',
        permission_modes: ['read-only', 'workspace-write', 'full-access'] });
    else if (pathname === '/api/agents' && url.searchParams.get('view') === 'personal') {
        json({ status: 'success', scope: 'self', default_agent_id: '', agents: agentRows });
    } else if (pathname === '/api/agents') {
        json({ status: 'success', default_agent_id: 'default', revision: 'fixture',
            agents: [{ id: 'default', name: 'default', enabled: true, is_default: true }],
            channel_instances: [] });
    } else if (pathname === '/api/memory/personal') {
        json({ status: 'success', scope: 'personal', entries: memoryRows });
    } else if (pathname === '/api/personal/channels') {
        json({ status: 'success', items: channelRows, channel_types: [
            { channel_type: 'feishu', ready: true, label: { zh: '飞书', en: 'Feishu' },
              credential_fields: [
                  { key: 'feishu_app_id', label: { zh: '应用 ID' }, secret: false, required: true },
                  { key: 'feishu_app_secret', label: { zh: '应用密钥' }, secret: true, required: true },
              ] },
        ] });
    } else if (['/api/sessions', '/api/history', '/api/projects', '/api/knowledge/list',
                '/api/models', '/api/tools', '/api/skills'].includes(pathname)) {
        json({ status: 'success', sessions: [], has_more: false, total: 0, messages: [],
            recents: [], tree: [], root_files: [], providers: [], items: [] });
    } else if (pathname === '/poll') json({ status: 'success', has_content: false });
    else if (pathname.startsWith('/api/')) json({ status: 'success', items: [] });
    else {
        report.unexpectedRoutes.push({ pathname, method: req.method });
        json({ status: 'error', message: 'Unconfigured fixture route' }, 404);
    }
});

let browser, origin;
async function open(options = {}) {
    const context = await browser.newContext({
        viewport: options.viewport || { width: 1440, height: 900 },
        locale: 'zh-CN',
        colorScheme: options.colorScheme || 'light',
        reducedMotion: options.reducedMotion || 'no-preference',
    });
    context.on('page', page => page.on('pageerror', error => {
        report.pageErrors.push({ scenario: report.currentScenario, message: error.message });
    }));
    const storage = options.storage || {};
    await context.addInitScript(([lang, extra]) => {
        if (!location.href.startsWith('http:')) return;
        sessionStorage.setItem('cow_tenant_id', 'fixture-tenant');
        localStorage.setItem('cow_lang', lang);
        Object.entries(extra).forEach(([key, value]) => localStorage.setItem(key, value));
    }, [options.lang || 'zh', storage]);
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    await page.goto(origin + (options.path || '/chat') + (options.hash || ''), { waitUntil: 'networkidle' });
    await page.locator('#app').waitFor({ state: 'visible' });
    // The shell is ready once it has mounted a view and rendered its navigation.
    // This used to wait for the personal module to publish itself; the module is
    // retired (task 8.8), so the signal is the shell's own — and asserting the
    // module's *absence* here keeps every scenario honest about it.
    await page.waitForFunction(() => !!document.querySelector('.view.active')
        && !!document.querySelector('#sidebar .sidebar-item'));
    assert.equal(await page.evaluate(() => typeof window.PersonalConsole),
        'undefined', 'the retired personal module must not be published');
    return page;
}

async function scenario(name, run, options) {
    report.currentScenario = name;
    // Scenarios run one at a time, so a per-scenario projection is safe and keeps
    // the denial case from leaking into the accepting ones.
    scenarioPages = personalPages((options && options.pages) || {});
    contextFailure = null;
    navigationMode = (options && options.navMode) || 'classic';
    tenantAdmin = !!(options && options.admin);
    if (options && options.prepare) options.prepare();
    // Requests are recorded for the whole run; this scenario only cares about its
    // own (a repeated endpoint would otherwise accumulate across scenarios).
    report.scenarioBase = report.requests.length;
    const start = Date.now();
    let page = null;
    try {
        page = await open(options || {});
        await run(page);
        report.scenarios.push({ name, passed: true, durationMs: Date.now() - start });
    } catch (error) {
        report.scenarios.push({ name, passed: false, message: error.message, stack: error.stack });
        throw error;
    } finally {
        if (page) await page.context().close().catch(() => {});
        fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(report, null, 2));
    }
}

// The account panel no longer hosts the five 「我的」 entries, and the five page
// ids are retired (tasks 3.3 / 8.8): a retired address is reached by its own
// deep link, which the shell forwards to the shared view carrying the same
// objects. Both shapes are opened through one helper so a scenario can assert
// the address still resolves while the retired page never mounts.
async function openRetiredAddress(page, viewId, expectedView) {
    await page.goto(origin + '/admin#view-' + viewId);
    await page.waitForFunction(
        expected => location.hash === '#view-' + expected, expectedView, { timeout: 10000 });
    await page.locator('#view-' + expectedView).waitFor({ state: 'visible' });
}

async function openSharedView(page, viewId) {
    await page.goto(origin + '/admin#view-' + viewId);
    await page.locator('#view-' + viewId).waitFor({ state: 'visible' });
}

async function assertNoRetiredSurface(page, viewId) {
    // The address is gone from the shell as a *page*: no container is mounted,
    // no host exists for it, and the retired module is not even fetched.
    assert.equal(await page.locator('#view-' + viewId).count(), 0,
        viewId + ' must not be mounted');
    assert.equal(await page.locator('[data-view="' + viewId + '"]').count(), 0,
        viewId + ' must have no host in the shell');
    assert.equal(await page.evaluate(() => typeof window.PersonalConsole),
        'undefined', 'the retired module must not be published');
    for (const endpoint of RETIRED_ENDPOINTS) {
        assert.equal(requestsTo(endpoint).length, 0,
            endpoint + ' must not be called by ' + viewId);
    }
    const assets = report.requests.slice(report.scenarioBase || 0)
        .filter(entry => entry.pathname.indexOf('personal-console') >= 0);
    assert.equal(assets.length, 0, 'the retired module must not be fetched');
}

// Opening the account surface: at narrow widths the drawer first, then the panel.
async function openAccountPanel(page) {
    const narrow = await page.evaluate(() => window.innerWidth < 1024);
    if (narrow) {
        const open = await page.locator('#sidebar')
            .evaluate(el => !el.classList.contains('-translate-x-full'));
        if (!open) await page.locator('#menu-toggle').click();
    }
    await page.locator('#sidebar-account-toggle').click();
    await page.locator('#sidebar-account-menu').waitFor({ state: 'visible' });
    return narrow;
}

function requestsTo(pathname) {
    return report.requests.slice(report.scenarioBase || 0)
        .filter(entry => entry.pathname === pathname);
}

async function main() {
    browser = await chromium.launch();
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    origin = 'http://127.0.0.1:' + server.address().port;

    await scenario('desktop: the account panel hosts no personal entry at all', async page => {
        // change unify-console-by-data-scope, task 3.3: the five entries were
        // removed from the panel and no other host took them. The panel is an
        // account surface, so opening it must not resurrect a second product
        // surface — and the retained account actions must survive.
        await openAccountPanel(page);
        await page.locator('#sidebar-account-menu').waitFor({ state: 'visible' });
        assert.equal(await page.locator('#account-menu-resources').count(), 0, 'no 「我的资源」 group');
        assert.equal(await page.locator('.account-menu-personal').count(), 0, 'no personal entry');
        for (const viewId of ['personal-agents', 'personal-channels', 'personal-memory',
                              'personal-tools', 'personal-skills']) {
            assert.equal(await page.locator(`[data-view="${viewId}"]`).count(), 0,
                `${viewId} has no host in the shell`);
        }
        // The old sidebar group stays gone as well.
        assert.equal(await page.locator('#sidebar [data-group="personal"]').count(), 0,
            'the main navigation keeps no personal group');
        assert.equal(await page.locator('#sidebar .sidebar-item[data-view^="personal-"]').count(), 0);
        for (const id of ['account-menu-profile', 'account-menu-password', 'account-menu-prefs',
                          'account-menu-about', 'account-menu-logout']) {
            assert.equal(await page.locator('#' + id).count(), 1, id + ' stays');
        }
    });

    await scenario('desktop: opening the panel preloads no personal page', async page => {
        // Opening the account surface may confirm the capability projection; it
        // must not start any of the five page consumers (task 3.2 / 5.2).
        const watched = ['/api/memory/personal', '/api/personal/channels', '/api/personal/resources'];
        const personalAgents = () => report.requests
            .slice(report.scenarioBase || 0)
            .filter(entry => entry.pathname === '/api/agents' && String(entry.query).includes('view=personal'))
            .length;
        const before = Object.fromEntries(watched.map(path => [path, requestsTo(path).length]));
        const agentsBefore = personalAgents();
        await openAccountPanel(page);
        await page.waitForTimeout(300);
        for (const path of watched) {
            assert.equal(requestsTo(path).length, before[path],
                path + ' must not be requested by opening the panel');
        }
        assert.equal(personalAgents(), agentsBefore, 'the personal agent catalog must not start either');
    });

    await scenario('desktop: every retired address forwards and paints no retired page', async page => {
        // Task 8.8. The five ids are addresses only: the shell forwards each one
        // to the shared page that carries the same objects (task 8.1), and the
        // retired page is never mounted, never hosted and never loaded — even
        // though this fixture's projection still carries all five `personal.*`
        // entries.
        for (const [viewId, target] of Object.entries(RETIRED_ADDRESSES)) {
            await openRetiredAddress(page, viewId, target);
            assert.equal(new URL(page.url()).hash, '#view-' + target,
                viewId + ' must forward to ' + target);
            await assertNoRetiredSurface(page, viewId);
        }
        // The shared pages are the ones a member's objects are managed on, and a
        // forward must not have touched a public maintenance surface on the way.
        for (const forbidden of ['/api/channels', '/api/tenant/channels']) {
            assert.equal(requestsTo(forbidden).length, 0, forbidden + ' was requested');
        }
        const assets = report.requests.slice(report.scenarioBase || 0)
            .filter(entry => entry.pathname.indexOf('personal-console') >= 0);
        assert.equal(assets.length, 0, 'the retired module must not be fetched');
    });

    await scenario('desktop: a kept thin adapter is not what an address resolves to', async page => {
        // The four legacy endpoints stay registered as forwarding adapters
        // (task 8.8: spec-mandated), but the console must not reach a page
        // through them: the shared view reads the shared endpoint. This is the
        // browser-level half of the "no live consumer" measurement.
        await openRetiredAddress(page, 'personal-memory', 'memory');
        await page.waitForTimeout(300);
        assert.equal(requestsTo('/api/memory/personal').length, 0,
            'the retired adapter must not be what renders the page');
        assert.equal(requestsTo('/api/memory/personal/content').length, 0);
    });

    await scenario('desktop: a deep link from the workbench area still lands on the shared page', async page => {
        // The address is not area-bound: `/chat#view-personal-memory` forwards to
        // a page that lives in the console area, so the shell switches area and
        // mounts the shared view. The retired page must not be mounted on the way
        // (task 8.1's cross-area case).
        await page.goto(origin + '/chat#view-personal-memory');
        await page.waitForFunction(() => location.pathname === '/admin', null, { timeout: 10000 });
        await page.locator('#view-memory').waitFor({ state: 'visible' });
        await assertNoRetiredSurface(page, 'personal-memory');
    });

    await scenario('narrow: the account panel is a modal sheet outside the drawer', async page => {
        const sidebar = page.locator('#sidebar');
        assert.ok(await sidebar.evaluate(el => el.classList.contains('-translate-x-full')),
            'the off-canvas sidebar starts hidden');
        await openAccountPanel(page);
        assert.ok(!await page.locator('#sidebar-overlay').evaluate(el => el.classList.contains('hidden')));
        const sheet = page.locator('body > #sidebar-account-menu');
        assert.equal(await sheet.count(), 1,
            'the sheet is hosted at the document root, not inside the transformed drawer');
        assert.equal(await sheet.getAttribute('role'), 'dialog');
        assert.equal(await sheet.getAttribute('aria-modal'), 'true');
        assert.ok(await sheet.locator('#account-menu-sheet-close').isVisible(), 'the sheet can be closed');
        assert.ok(!await page.locator('#account-menu-backdrop').evaluate(el => el.classList.contains('hidden')),
            'the sheet has a backdrop');
        assert.ok(await page.evaluate(() => document.body.classList.contains('account-menu-sheet-open')),
            'the page behind the sheet does not scroll');
        // Task 3.3: the sheet carries account operations only — no resource entry
        // is reachable inside it, on a narrow viewport either.
        assert.equal(await page.locator('#sidebar-account-menu .account-menu-personal').count(), 0);
        assert.ok(await page.locator('#account-menu-logout').isVisible(),
            'the retained account actions stay reachable in the sheet');
    }, { viewport: { width: 390, height: 844 } });

    await scenario('narrow: a deep-linked retired address leaves no sheet or drawer residue', async page => {
        await openRetiredAddress(page, 'personal-agents', 'agents');
        await page.waitForFunction(() => document.getElementById('sidebar')
            .classList.contains('-translate-x-full'));
        assert.ok(await page.locator('#sidebar-overlay').evaluate(el => el.classList.contains('hidden')));
        assert.ok(await page.locator('#view-agents').isVisible());
        assert.equal(await page.locator('#view-personal-agents').count(), 0,
            'the retired page must not be mounted on a narrow viewport either');
        // No panel, no backdrop, no scroll lock: the account surface was never
        // involved in reaching the page.
        assert.ok(!await page.locator('#sidebar-account-menu').isVisible());
        assert.ok(await page.locator('#account-menu-backdrop').evaluate(el => el.classList.contains('hidden')));
        assert.ok(!await page.evaluate(() => document.body.classList.contains('account-menu-sheet-open')));
    }, { viewport: { width: 390, height: 844 } });

    await scenario('narrow: the forwarded shared page stays usable at 390px', async page => {
        await page.locator('#menu-toggle').click();
        await openRetiredAddress(page, 'personal-memory', 'memory');
        const box = await page.locator('#view-memory').boundingBox();
        assert.ok(box && box.width <= 390, 'the shared page must fit the viewport');
        await assertNoRetiredSurface(page, 'personal-memory');
    }, { viewport: { width: 390, height: 844 } });

    await scenario('a denied shared page is refused by the shell and starts nothing', async page => {
        // The carrier page is denied (menu withheld). The retired address still
        // resolves — it forwards to the shared view — and the shell's gate then
        // refuses *that* view, which is the only page the address ever names now.
        // Nothing behind the retired id may be started on the way.
        await page.waitForFunction(() => !!document.getElementById('view-unavailable'));
        assert.ok(await page.locator('#view-unavailable').evaluate(
            el => el.classList.contains('active')));
        assert.equal(await page.locator('#view-personal-memory').count(), 0,
            'the retired page must not be mounted at all');
        assert.equal(await page.locator('#view-memory').evaluate(
            el => el.classList.contains('active')), false,
            'the denied shared page must not be the active view');
        assert.equal(requestsTo('/api/memory/personal').length, 0,
            'a denied page must not start the consumer behind it');
    }, { hash: '#view-personal-memory',
         pages: { 'admin.memory': { available: false, read_allowed: false,
             menu_denied: true, scope: 'agent', actions: {},
             states: { read: false, config: false, execution: false } } } });

    await scenario('a withdrawn capability no longer closes a page, and opens none', async page => {
        // The deployment turned the memory-write capability off (task 9.1). The
        // switch never decided *availability*, and now that the personal pages are
        // retired (task 8.8) there is no page for it to close: the retired address
        // forwards to the shared page, which stays readable because the switch
        // gates the write path (asserted in the Python switch tests), and nothing
        // behind the retired id is started.
        await openAccountPanel(page);
        assert.equal(await page.locator('#account-menu-resources').count(), 0);
        assert.equal(await page.locator('[data-view="personal-memory"]').count(), 0,
            'a withdrawn capability has no entry to hide');
        await page.keyboard.press('Escape');
        await openRetiredAddress(page, 'personal-memory', 'memory');
        assert.ok(await page.locator('#view-memory').isVisible(),
            'the shared page stays readable when the write slice is off');
        assert.equal(await page.locator('#view-unavailable').count() > 0
            && await page.locator('#view-unavailable').evaluate(el => el.classList.contains('active')),
            false, 'a slice switch must not read as a denial');
        await assertNoRetiredSurface(page, 'personal-memory');
    }, { pages: { 'admin.memory': { available: true, read_allowed: true, menu_denied: false,
             scope: 'agent', actions: {},
             switches: { member_personal_console: true, personal_memory_write: false },
             states: { read: true, config: false, execution: false } } } });

    await scenario('desktop: the account panel claims no current page', async page => {
        // Task 3.3 removed the panel's current-page marker with the entries it
        // belonged to: the marker lives on the main navigation alone, and the
        // trigger no longer describes a personal area.
        await openSharedView(page, 'memory');
        const marks = await page.locator('[aria-current="page"]').evaluateAll(nodes => nodes.map(node => ({
            host: node.closest('#sidebar-account-menu') ? 'panel' : 'other',
            view: node.dataset.view || node.id || '',
        })));
        assert.deepEqual(marks.filter(mark => mark.host === 'panel'), [],
            'the panel carries no current marker');
        assert.ok(!await page.locator('#sidebar-account-footer')
            .evaluate(el => el.classList.contains('is-personal')), 'no personal-area state on the trigger');
        assert.equal(await page.locator('#sidebar-account-region').count(), 0,
            'the personal-area description is gone');
        // Re-opening the panel changes nothing about the current page.
        await openAccountPanel(page);
        assert.equal(await page.locator('#account-menu-resources-status').count(), 0);
        assert.equal(await page.locator('#sidebar-account-menu [aria-current="page"]').count(), 0);
        assert.equal(requestsTo('/api/memory/personal').length, 0,
            'the shared page reads the shared endpoint, and reopening it re-runs nothing retired');
    });

    await scenario('desktop: Escape and an outside click close the panel and return focus', async page => {
        await openAccountPanel(page);
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => document.getElementById('sidebar-account-menu')
            .classList.contains('hidden'));
        assert.equal(await page.evaluate(() => document.activeElement.id), 'sidebar-account-toggle',
            'focus comes back to the trigger');
        await openAccountPanel(page);
        // Tab past the last item leaves the non-modal desktop popover.
        await page.locator('#sidebar-version').focus();
        await page.keyboard.press('Tab');
        await page.waitForFunction(() => document.getElementById('sidebar-account-menu')
            .classList.contains('hidden'));
        assert.ok(await page.locator('#sidebar-account-toggle')
            .evaluate(el => el.getAttribute('aria-expanded') === 'false'));
        await openAccountPanel(page);
        await page.mouse.click(1200, 700);
        await page.waitForFunction(() => document.getElementById('sidebar-account-menu')
            .classList.contains('hidden'));
        assert.ok(!await page.locator('#account-menu-backdrop').isVisible(),
            'the desktop popover closes on an outside click without leaving a backdrop');
    });

    await scenario('desktop: a short viewport caps the panel and keeps the last action reachable', async page => {
        await openAccountPanel(page);
        const menu = page.locator('#sidebar-account-menu');
        const cap = await menu.evaluate(el => parseFloat(el.style.maxHeight));
        const room = await page.evaluate(() => document.getElementById('sidebar-account-footer')
            .getBoundingClientRect().top - 12);
        assert.ok(Math.abs(cap - room) < 1.5, `the cap follows the room above the card (${cap} vs ${room})`);
        // The panel is capped by the room above the card, and it never grows past
        // that cap: on a 420px-tall window the account actions stay inside the
        // window instead of being pushed off it. Whether the content is tall
        // enough to need its internal scrollbar depends on how many actions the
        // panel owns — task 3.3 shrank that list, so what is asserted here is the
        // cap and the window bound, not the scrollbar.
        const metrics = await menu.evaluate(el => ({
            scrollHeight: el.scrollHeight, clientHeight: el.clientHeight,
            maxHeight: parseFloat(el.style.maxHeight), offsetHeight: el.offsetHeight,
            bottom: el.getBoundingClientRect().bottom, windowHeight: window.innerHeight }));
        assert.ok(metrics.scrollHeight <= metrics.maxHeight + 1,
            'the panel never grows past the cap: ' + JSON.stringify(metrics));
        assert.ok(metrics.bottom <= metrics.windowHeight + 1,
            'the panel stays inside the window: ' + JSON.stringify(metrics));
        // The last action the panel still owns: logout, at the end of the list.
        const last = page.locator('#account-menu-logout');
        await last.scrollIntoViewIfNeeded();
        const box = await last.boundingBox();
        assert.ok(box && box.y >= 0 && box.y + box.height <= 420, 'the last action stays operable: ' + JSON.stringify(box));
    }, { viewport: { width: 1440, height: 420 } });

    await scenario('the account texts follow the selected language', async page => {
        await openAccountPanel(page);
        assert.equal(await page.locator('#account-menu-settings .account-menu-group-title').textContent(),
            '账号设置');
        assert.ok((await page.locator('#sidebar-account-toggle').getAttribute('aria-label'))
            .includes('账号设置'), 'the accessible name carries the account-settings hint');
    });

    await scenario('zh-Hant: the account texts are traditional', async page => {
        await openAccountPanel(page);
        assert.equal(await page.locator('#account-menu-settings .account-menu-group-title').textContent(),
            '帳號設定');
        assert.ok((await page.locator('#sidebar-account-toggle').getAttribute('aria-label'))
            .includes('帳號設定'), 'the accessible name carries the hint');
    }, { lang: 'zh-Hant' });

    await scenario('en: the account texts are English', async page => {
        await openAccountPanel(page);
        assert.equal(await page.locator('#account-menu-settings .account-menu-group-title').textContent(),
            'Account settings');
        assert.ok((await page.locator('#sidebar-account-toggle').getAttribute('aria-label'))
            .includes('Account settings'));
    }, { lang: 'en' });

    await scenario('dark theme and reduced motion: the sheet still opens and leaves no residue', async page => {
        await openAccountPanel(page);
        const sheet = page.locator('body > #sidebar-account-menu');
        await sheet.waitFor({ state: 'visible' });
        const painted = await sheet.evaluate(el => {
            const style = getComputedStyle(el);
            return { background: style.backgroundColor, animation: style.animationName };
        });
        assert.ok(painted.background && painted.background !== 'rgba(0, 0, 0, 0)',
            'the sheet is themed, not transparent: ' + painted.background);
        assert.equal(painted.animation, 'none', 'reduced motion drops the sheet animation');
        await page.locator('#account-menu-sheet-close').click();
        await page.waitForFunction(() => document.getElementById('sidebar-account-menu')
            .classList.contains('hidden'));
        assert.ok(!await page.evaluate(() => document.body.classList.contains('account-menu-sheet-open')));
        assert.ok(await page.locator('#account-menu-backdrop').evaluate(el => el.classList.contains('hidden')));
    }, { viewport: { width: 390, height: 844 }, colorScheme: 'dark', reducedMotion: 'reduce',
         storage: { cow_theme: 'dark', cow_web_palette: 'classic' } });

    await scenario('a failed projection leaves the panel usable and offers no resource retry', async page => {
        // The projection could not be read. The panel no longer owns a resource
        // verdict (task 3.3), so it shows no checking/failed state for resources
        // and never offers an unconfirmed entry — while the account actions it
        // does own stay usable, and no personal page consumer is started.
        await openAccountPanel(page);
        assert.equal(await page.locator('#account-menu-resources').count(), 0);
        assert.equal(await page.locator('#account-menu-resources-status').count(), 0);
        assert.equal(await page.locator('#account-menu-resources-retry').count(), 0);
        assert.ok(await page.locator('#account-menu-logout').isVisible(),
            'the account actions do not depend on the capability projection');
        for (const path of ['/api/memory/personal', '/api/personal/channels', '/api/personal/resources']) {
            assert.equal(requestsTo(path).length, 0, path + ' must not be requested by the failed check');
        }
        // The panel's own retry re-asks the tenant-scoped capability summary and
        // still opens no personal page.
        contextFailure = null;
        await page.locator('#account-menu-retry').click();
        await page.waitForFunction(() => document.getElementById('account-menu-retry')
            .classList.contains('hidden'));
        assert.equal(requestsTo('/api/memory/personal').length, 0, 'the retry opens no personal page');
    }, { prepare: () => { contextFailure = 503; } });

    await scenario('a breakpoint change closes the sheet and leaves no residue', async page => {
        // Widening the window while the sheet is open must not leave a fixed
        // panel, a backdrop or a scroll lock behind (task 4.4).
        await openAccountPanel(page);
        await page.locator('body > #sidebar-account-menu').waitFor({ state: 'visible' });
        await page.setViewportSize({ width: 1440, height: 900 });
        await page.waitForFunction(() => document.getElementById('sidebar-account-menu')
            .classList.contains('hidden'));
        assert.equal(await page.locator('body > #sidebar-account-menu').count(), 0,
            'the single panel node is back inside the account footer');
        assert.equal(await page.locator('#sidebar-account-footer > #sidebar-account-menu').count(), 1);
        assert.ok(await page.locator('#account-menu-backdrop').evaluate(el => el.classList.contains('hidden')));
        assert.ok(!await page.evaluate(() => document.body.classList.contains('account-menu-sheet-open')));
    }, { viewport: { width: 390, height: 844 } });

    await scenario('split navigation duplicates no personal entry', async page => {
        // The presentation switch is layout-only: it must not introduce a host for
        // the five entries, which task 3.3 retired entirely.
        assert.equal(await page.evaluate(() => document.getElementById('app').dataset.navMode), 'split');
        await openAccountPanel(page);
        assert.equal(await page.locator('.account-menu-personal').count(), 0,
            'no personal entry, whatever the navigation presentation');
        assert.equal(await page.locator('#sidebar .sidebar-item[data-view^="personal-"]').count(), 0);
        assert.equal(await page.locator('#sidebar [data-group="personal"]').count(), 0);
    }, { navMode: 'split' });

    await scenario('with every personal capability off the panel is unchanged', async page => {
        // Every retired page id is reported withdrawn by a capability switch — a
        // payload the server no longer sends, kept here as the hostile case. There
        // is no group left to empty (task 3.3) and no page left to close
        // (task 8.8), so the panel renders the same account surface, with no blank
        // separator and no dangling entry.
        await openAccountPanel(page);
        assert.equal(await page.locator('#account-menu-resources').count(), 0);
        assert.ok(await page.locator('#account-menu-settings').isVisible(),
            'the account settings group stays');
        assert.ok(await page.locator('#account-menu-logout').isVisible());
        assert.equal(await page.locator('#sidebar [data-group="personal"]').count(), 0);
        for (const viewId of Object.keys(RETIRED_ADDRESSES)) {
            assert.equal(await page.locator('[data-view="' + viewId + '"]').count(), 0, viewId);
        }
    }, { pages: { 'personal.agents': { available: false, read_allowed: false,
            reason: 'capability_disabled', scope: 'self', actions: {}, states: {} },
        'personal.channels': { available: false, read_allowed: false,
            reason: 'capability_disabled', scope: 'self', actions: {}, states: {} },
        'personal.memory': { available: false, read_allowed: false,
            reason: 'capability_disabled', scope: 'self', actions: {}, states: {} },
        'personal.tools': { available: false, read_allowed: false,
            reason: 'capability_disabled', scope: 'self', actions: {}, states: {} },
        'personal.skills': { available: false, read_allowed: false,
            reason: 'capability_disabled', scope: 'self', actions: {}, states: {} } } });

    await scenario('console: the account panel inside /admin hosts no personal entry', async page => {
        // A tenant admin opens the account panel inside /admin. Since task 3.3 the
        // panel is an account surface there too: no personal entry, no panel-owned
        // resource verdict, and no personal page consumer started by opening it.
        // Business resources are reached through the console pages themselves.
        assert.equal(new URL(page.url()).pathname, '/admin', 'the console area is kept for a qualified admin');
        await openAccountPanel(page);
        assert.equal(await page.locator('.account-menu-personal').count(), 0,
            'the console host exposes no personal entry either');
        assert.equal(await page.locator('#account-menu-resources').count(), 0);
        assert.ok(await page.locator('#account-menu-logout').isVisible());
        assert.equal(new URL(page.url()).pathname, '/admin', 'opening the panel never leaves the console area');
        for (const path of ['/api/memory/personal', '/api/personal/channels', '/api/personal/resources']) {
            assert.equal(requestsTo(path).length, 0, path + ' must not be requested by opening the panel');
        }
    }, { path: '/admin', admin: true });

    const failed = report.scenarios.filter(item => !item.passed);
    assert.equal(failed.length, 0, JSON.stringify(failed, null, 2));
    assert.equal(report.pageErrors.length, 0, JSON.stringify(report.pageErrors, null, 2));
    assert.equal(report.unexpectedRoutes.length, 0, JSON.stringify(report.unexpectedRoutes, null, 2));
    console.log(`personal console browser contract: ${report.scenarios.length} scenarios passed`);
    console.log('artifact: ' + path.join(output, 'results.json'));
}

main()
    .catch(error => { console.error(error); process.exitCode = 1; })
    .finally(() => { server.close(); if (browser) return browser.close(); });
