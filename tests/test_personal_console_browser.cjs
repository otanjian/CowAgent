// Personal console in a real browser (change enable-member-personal-console,
// task 8.6: "桌面/窄屏交互测试").
//
// The frontend contract (tests/test_personal_console_frontend.cjs) pins the pure
// helpers; this file drives the *production page* at two widths, because layout
// behaviour (the off-canvas sidebar, the collapsible group, a deep link, and the
// "a denied page never starts its consumer" rule) only exists once the shell,
// the CSS and the modules run together. Only backend responses are fixtures.
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
    } else if (pathname === '/api/personal/resources') {
        json({ status: 'success', scope: 'personal',
            resources: url.searchParams.get('kind') === 'skill' ? [] : resourceRows });
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
    await page.waitForFunction(() => typeof window.PersonalConsole === 'object');
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

// The five personal entries live in the account panel now (change
// move-personal-menu-to-account), so reaching a personal page means opening the
// account surface: at narrow widths the drawer first, then the panel itself.
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

async function openPersonalView(page, viewId) {
    await openAccountPanel(page);
    await page.locator(`#sidebar-account-menu .account-menu-personal[data-view="${viewId}"]`).click();
    await page.locator('#view-' + viewId).waitFor({ state: 'visible' });
}

function requestsTo(pathname) {
    return report.requests.slice(report.scenarioBase || 0)
        .filter(entry => entry.pathname === pathname);
}

async function main() {
    browser = await chromium.launch();
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    origin = 'http://127.0.0.1:' + server.address().port;

    await scenario('desktop: the account panel hosts all five personal pages in order', async page => {
        await openAccountPanel(page);
        const group = page.locator('#account-menu-resources');
        await group.waitFor({ state: 'visible' });
        const entries = group.locator('.account-menu-personal');
        assert.equal(await entries.count(), 5);
        assert.deepEqual(
            await entries.evaluateAll(nodes => nodes.map(node => node.dataset.view)),
            ['personal-agents', 'personal-channels', 'personal-memory', 'personal-tools', 'personal-skills']);
        const labels = await group.locator('.account-menu-personal [data-i18n]').allTextContents();
        assert.deepEqual(labels, ['我的智能体', '我的渠道', '我的记忆', '我的工具', '我的技能']);
        // One host per entry: the old sidebar group must be gone entirely.
        assert.equal(await page.locator('#sidebar [data-group="personal"]').count(), 0,
            'the main navigation keeps no personal group');
        assert.equal(await page.locator('#sidebar .sidebar-item[data-view^="personal-"]').count(), 0);
        assert.equal(await page.locator('.account-menu-personal').count(), 5,
            'exactly one DOM copy of the five entries');
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

    await scenario('desktop: every personal entry renders its own page', async page => {
        for (const viewId of ['personal-agents', 'personal-channels', 'personal-memory',
                              'personal-tools', 'personal-skills']) {
            await openPersonalView(page, viewId);
            const node = page.locator('#view-' + viewId);
            await node.waitFor({ state: 'visible' });
            assert.ok(await node.locator('[data-i18n]').first().isVisible(), viewId);
        }
        // The personal pages must not have touched a public maintenance surface.
        for (const forbidden of ['/api/channels', '/api/tenant/channels']) {
            assert.equal(requestsTo(forbidden).length, 0, forbidden + ' was requested');
        }
    });

    await scenario('desktop: a page reports read / config / execute separately', async page => {
        await openPersonalView(page, 'personal-channels');
        const keys = await page.locator('#view-personal-channels [data-personal-states]')
            .getAttribute('data-personal-state-keys');
        // The channel consumer is closed in this fixture: the page says so
        // instead of promising a live connection.
        assert.equal(keys, 'personal_state_read,personal_state_config,personal_state_execution_closed');
    });

    await scenario('desktop: rows carry only the verbs the server signed', async page => {
        await openPersonalView(page, 'personal-memory');
        const verbs = await page.locator('#view-personal-memory [data-personal-verb]')
            .evaluateAll(nodes => nodes.map(node => node.dataset.personalVerb));
        assert.deepEqual([...new Set(verbs)].sort(), ['delete', 'edit']);
        const total = await page.locator('#view-personal-memory [data-personal-footer]').textContent();
        assert.ok(total.includes('2'), 'the footer counts the two entries: ' + total);
    });

    await scenario('desktop: search filters what is on screen', async page => {
        await openPersonalView(page, 'personal-memory');
        await page.locator('#view-personal-memory [data-personal-search]').fill('preferences');
        await page.waitForFunction(() => document.querySelectorAll(
            '#view-personal-memory [data-personal-verb]').length === 2);
        const titles = await page.locator('#view-personal-memory [data-personal-body] .truncate')
            .allTextContents();
        assert.deepEqual(titles, ['preferences.md']);
        const total = await page.locator('#view-personal-memory [data-personal-footer]').textContent();
        assert.ok(total.includes('1'), 'the total follows the filter: ' + total);
    });

    await scenario('desktop: a deep link opens the personal page directly', async page => {
        const node = page.locator('#view-personal-memory');
        await node.waitFor({ state: 'visible' });
        assert.ok(await node.locator('[data-personal-body]').isVisible());
        assert.equal(requestsTo('/api/memory/personal').length, 1);
    }, { hash: '#view-personal-memory' });

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
        await page.locator('#account-menu-resources').waitFor({ state: 'visible' });
        // Inside the sheet the five entries are reachable exactly as on desktop.
        assert.equal(await page.locator('#sidebar-account-menu .account-menu-personal').count(), 5);
    }, { viewport: { width: 390, height: 844 } });

    await scenario('narrow: choosing a personal page leaves no sheet or drawer residue', async page => {
        await openPersonalView(page, 'personal-agents');
        await page.waitForFunction(() => document.getElementById('sidebar')
            .classList.contains('-translate-x-full'));
        assert.ok(await page.locator('#sidebar-overlay').evaluate(el => el.classList.contains('hidden')));
        assert.ok(await page.locator('#view-personal-agents').isVisible());
        // The sheet is torn down: no visible panel, no backdrop, no scroll lock.
        assert.ok(!await page.locator('#sidebar-account-menu').isVisible());
        assert.ok(await page.locator('#account-menu-backdrop').evaluate(el => el.classList.contains('hidden')));
        assert.ok(!await page.evaluate(() => document.body.classList.contains('account-menu-sheet-open')));
    }, { viewport: { width: 390, height: 844 } });

    await scenario('narrow: the page itself stays usable at 390px', async page => {
        await page.locator('#menu-toggle').click();
        await openPersonalView(page, 'personal-memory');
        const box = await page.locator('#view-personal-memory [data-personal-body]').boundingBox();
        assert.ok(box && box.width <= 390, 'the body must fit the viewport');
        await page.locator('#view-personal-memory [data-personal-search]').fill('MEMORY');
        await page.waitForFunction(() => document.querySelectorAll(
            '#view-personal-memory [data-personal-verb]').length === 2);
    }, { viewport: { width: 390, height: 844 } });

    await scenario('a denied page is refused by the shell and starts nothing', async page => {
        // The entry is withheld by the menu, so even a direct link must land on
        // the shell's denial view — and the consumer behind the page must never
        // be called, which is the point of refusing at navigation time.
        await page.waitForFunction(() => !!document.getElementById('view-unavailable'));
        assert.ok(await page.locator('#view-unavailable').evaluate(
            el => el.classList.contains('active')));
        assert.equal(await page.locator('#view-personal-memory').count(), 0,
            'the denied page must not be mounted at all');
        assert.equal(requestsTo('/api/memory/personal').length, 0,
            'a denied page must not start the consumer behind it');
    }, { hash: '#view-personal-memory',
         pages: { 'personal.memory': { available: false, read_allowed: false,
             menu_denied: true, scope: 'self', actions: {},
             states: { read: false, config: false, execution: false } } } });

    await scenario('a withdrawn capability hides its entry and names itself', async page => {
        // The deployment turned the memory-write capability off (task 9.1): the
        // entry is not offered, the other personal entries still are, and a
        // direct link lands on a body that names the capability — neither a
        // menu-denial wording nor a started consumer.
        await openAccountPanel(page);
        await page.waitForFunction(() => {
            const entry = document.querySelector('#sidebar-account-menu [data-view="personal-memory"]');
            return !!entry && entry.classList.contains('hidden');
        });
        const group = page.locator('#account-menu-resources');
        assert.ok(await group.isVisible(), 'the 「我的资源」 group stays while other capabilities are on');
        assert.equal(await group.locator('.account-menu-personal:not(.hidden)').count(), 4);

        const body = page.locator('#view-personal-memory [data-personal-body]');
        await body.waitFor({ state: 'visible' });
        assert.equal(await body.getAttribute('data-personal-denied-reason'),
            'capability_disabled');
        const text = await body.textContent();
        assert.ok(text.includes('个人记忆写入未启用'),
            'the denial names the switch that is off: ' + text);
        assert.equal(requestsTo('/api/memory/personal').length, 0,
            'a withdrawn capability must not start the consumer behind it');
    }, { hash: '#view-personal-memory',
         pages: { 'personal.memory': { available: false, read_allowed: false,
             reason: 'capability_disabled', scope: 'self', actions: {},
             switches: { member_personal_console: true, personal_memory_write: false },
             states: { read: false, config: false, execution: false } } } });

    await scenario('desktop: the panel marks the current personal page exactly once', async page => {
        await openPersonalView(page, 'personal-memory');
        const marks = await page.locator('[aria-current="page"]').evaluateAll(nodes => nodes.map(node => ({
            host: node.closest('#sidebar-account-menu') ? 'panel' : 'other',
            view: node.dataset.view || node.id || '',
        })));
        assert.deepEqual(marks, [{ host: 'panel', view: 'personal-memory' }],
            'one current marker, on the account entry');
        assert.ok(await page.locator('#sidebar-account-footer')
            .evaluate(el => el.classList.contains('is-personal')), 'the trigger names the personal area');
        const region = page.locator('#sidebar-account-region');
        assert.ok(await region.evaluate(el => !el.classList.contains('hidden')));
        assert.equal(await region.textContent(), '当前位于个人区域');
        // Re-opening the panel shows where the member is, without re-navigating.
        await openAccountPanel(page);
        assert.ok(!await page.locator('#account-menu-resources-status').isVisible(),
            'a confirmed projection shows no checking/failed state');
        const entry = page.locator('#sidebar-account-menu .account-menu-personal[data-view="personal-memory"]');
        assert.equal(await entry.getAttribute('aria-current'), 'page');
        assert.ok(await entry.isVisible(), 'the current entry is reachable when the panel reopens');
        assert.equal(requestsTo('/api/memory/personal').length, 1, 'reopening does not re-run the consumer');
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

    await scenario('desktop: a short viewport caps the panel and keeps the last entry reachable', async page => {
        await openAccountPanel(page);
        const menu = page.locator('#sidebar-account-menu');
        const cap = await menu.evaluate(el => parseFloat(el.style.maxHeight));
        const room = await page.evaluate(() => document.getElementById('sidebar-account-footer')
            .getBoundingClientRect().top - 12);
        assert.ok(Math.abs(cap - room) < 1.5, `the cap follows the room above the card (${cap} vs ${room})`);
        const scrolls = await menu.evaluate(el => el.scrollHeight > el.clientHeight + 1);
        assert.ok(scrolls, 'the panel scrolls internally instead of overflowing the window');
        const last = page.locator('#account-menu-resources .account-menu-personal[data-view="personal-skills"]');
        await last.scrollIntoViewIfNeeded();
        const box = await last.boundingBox();
        assert.ok(box && box.y >= 0 && box.y + box.height <= 420, 'the last entry stays operable: ' + JSON.stringify(box));
    }, { viewport: { width: 1440, height: 420 } });

    await scenario('the new account texts follow the selected language', async page => {
        await openAccountPanel(page);
        assert.equal(await page.locator('#account-menu-resources .account-menu-group-title').textContent(),
            '我的资源');
        assert.equal(await page.locator('#account-menu-settings .account-menu-group-title').textContent(),
            '账号设置');
        assert.ok((await page.locator('#sidebar-account-toggle').getAttribute('aria-label'))
            .includes('个人资源与设置'), 'the accessible name carries the hint');
    });

    await scenario('zh-Hant: the resources group and the trigger hint are traditional', async page => {
        await openAccountPanel(page);
        assert.equal(await page.locator('#account-menu-resources .account-menu-group-title').textContent(),
            '我的資源');
        assert.equal(await page.locator('#account-menu-settings .account-menu-group-title').textContent(),
            '帳號設定');
        assert.ok((await page.locator('#sidebar-account-toggle').getAttribute('aria-label'))
            .includes('個人資源與設定'), 'the accessible name carries the hint');
    }, { lang: 'zh-Hant' });

    await scenario('en: the resources group and the trigger hint are English', async page => {
        await openAccountPanel(page);
        assert.equal(await page.locator('#account-menu-resources .account-menu-group-title').textContent(),
            'My resources');
        assert.equal(await page.locator('#account-menu-settings .account-menu-group-title').textContent(),
            'Account settings');
        assert.ok((await page.locator('#sidebar-account-toggle').getAttribute('aria-label'))
            .includes('Personal resources and settings'));
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

    await scenario('a failed projection offers a retry, never an unconfirmed entry', async page => {
        // The projection could not be read. Opening the panel must not guess: no
        // entry is activatable, the group explains itself, and the retry re-asks
        // the projection — without starting any personal page consumer.
        await openAccountPanel(page);
        const group = page.locator('#account-menu-resources');
        await group.waitFor({ state: 'visible' });
        assert.equal(await group.locator('.account-menu-personal:not(.hidden)').count(), 0,
            'an unconfirmed entry is not offered');
        const status = page.locator('#account-menu-resources-status');
        assert.ok(await status.isVisible(), 'the panel explains the failed check');
        assert.equal(await status.textContent(), '个人资源暂不可用');
        const retry = page.locator('#account-menu-resources-retry');
        assert.ok(await retry.isVisible());
        for (const path of ['/api/memory/personal', '/api/personal/channels', '/api/personal/resources']) {
            assert.equal(requestsTo(path).length, 0, path + ' must not be requested by the failed check');
        }
        contextFailure = null;
        await retry.click();
        await page.waitForFunction(() => document.querySelectorAll(
            '#account-menu-resources .account-menu-personal:not(.hidden)').length === 5);
        assert.ok(!await status.isVisible(), 'the confirmed projection clears the failed state');
        assert.equal(requestsTo('/api/memory/personal').length, 0, 'the retry still opens no personal page');
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
        // The presentation switch is layout-only: it must not add a second host
        // for the five entries (task 5.4).
        assert.equal(await page.evaluate(() => document.getElementById('app').dataset.navMode), 'split');
        await openAccountPanel(page);
        assert.equal(await page.locator('.account-menu-personal').count(), 5,
            'exactly one DOM copy per entry, whatever the navigation presentation');
        assert.equal(await page.locator('#sidebar .sidebar-item[data-view^="personal-"]').count(), 0);
        assert.equal(await page.locator('#sidebar [data-group="personal"]').count(), 0);
    }, { navMode: 'split' });

    await scenario('with the personal console off the empty group is removed, actions stay', async page => {
        // Every personal page is withdrawn by a capability switch: the group
        // disappears instead of leaving a blank separator, and the account
        // actions remain usable (task 3.1 / 5.4).
        await openAccountPanel(page);
        await page.waitForFunction(() => document.getElementById('account-menu-resources')
            .classList.contains('hidden'));
        assert.ok(!await page.locator('#account-menu-resources').isVisible(),
            'an all-withdrawn group is removed');
        assert.ok(await page.locator('#account-menu-settings').isVisible(),
            'the account settings group stays');
        assert.ok(await page.locator('#account-menu-logout').isVisible());
        assert.equal(await page.locator('#sidebar [data-group="personal"]').count(), 0);
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

    await scenario('console: an account entry enters the personal page in the workbench host', async page => {
        // A tenant admin opens the account panel inside /admin and picks a
        // personal page: the entry must reuse the shared protected navigation and
        // land on the existing workbench host, still scoped to the current
        // tenant and to the member themselves (no admin qualification, no
        // duplicate consumer).
        assert.equal(new URL(page.url()).pathname, '/admin', 'the console area is kept for a qualified admin');
        await openAccountPanel(page);
        await page.locator('#sidebar-account-menu .account-menu-personal[data-view="personal-memory"]').click();
        await page.waitForFunction(() => document.getElementById('view-personal-memory')
            .classList.contains('active'));
        assert.equal(new URL(page.url()).pathname, '/chat', 'personal pages live in the workbench host');
        assert.ok(await page.locator('#view-personal-memory').isVisible());
        assert.equal(await page.locator('#sidebar-account-menu .account-menu-personal[data-view="personal-memory"]')
            .getAttribute('aria-current'), 'page', 'the account entry reflects the current page');
        assert.equal(requestsTo('/api/memory/personal').length, 1,
            'the consumer starts exactly once for the entry');
        assert.equal(requestsTo('/api/personal/resources').length, 0,
            'no other personal consumer is started by the switch');
        assert.ok(await page.locator('#view-personal-memory [data-personal-body]').isVisible());
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
