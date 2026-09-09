// Path-based workbench/admin nav helpers (chat/admin split).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../channel/web/static/js/console.js'), 'utf8');

function section(start, end) {
    const from = source.indexOf(start);
    const to = source.indexOf(end, from + start.length);
    assert.ok(from >= 0 && to > from, `Missing console section ${start}`);
    return source.slice(from, to + end.length);
}

test('nav area helpers: path, open, qualify entry', () => {
    const code = section('// === NAV_AREA_BEGIN ===', '// === NAV_AREA_END ===');
    const sandbox = {
        window: {
            open(url, name) {
                sandbox._opened = { url, name };
                return { name };
            },
        },
    };
    vm.runInNewContext(code, sandbox);
    assert.equal(sandbox._navAreaFromPath('/chat'), 'workbench');
    assert.equal(sandbox._navAreaFromPath('/admin'), 'admin');
    assert.equal(sandbox._navAreaFromPath('/admin/'), 'admin');
    assert.equal(sandbox._navAreaFromPath('/admin?x=1'.split('?')[0]), 'admin');
    sandbox._openNavArea('admin');
    assert.deepEqual(sandbox._opened, { url: '/admin', name: 'cow-admin' });
    sandbox._openNavArea('workbench');
    assert.deepEqual(sandbox._opened, { url: '/chat', name: 'cow-workbench' });
    assert.equal(sandbox._qualifyAdminConsoleEntry({
        identityMode: 'database', isPlatformAdmin: false, isTenantAdmin: false,
    }), false);
    assert.equal(sandbox._qualifyAdminConsoleEntry({
        identityMode: 'database', isPlatformAdmin: false, isTenantAdmin: true,
    }), true);
    assert.equal(sandbox._qualifyAdminConsoleEntry({
        identityMode: 'database', isPlatformAdmin: true, isTenantAdmin: false,
    }), true);
    assert.equal(sandbox._qualifyAdminConsoleEntry({
        identityMode: 'legacy', isPlatformAdmin: false, isTenantAdmin: false,
    }), true);
});

test('chat.html has area markers and admin home', () => {
    const htmlSource = fs.readFileSync(path.join(__dirname, '../channel/web/chat.html'), 'utf8');
    assert.match(htmlSource, /id="nav-open-admin"/);
    assert.match(htmlSource, /id="nav-return-workbench"/);
    assert.match(htmlSource, /id="view-admin-home"/);
    assert.match(htmlSource, /data-nav-shell="workbench"/);
    assert.match(htmlSource, /data-nav-shell="admin"/);
});

test('chat.html pins admin entry above account footer', () => {
    const htmlSource = fs.readFileSync(path.join(__dirname, '../channel/web/chat.html'), 'utf8');
    const adminIdx = htmlSource.indexOf('id="nav-open-admin"');
    const footerIdx = htmlSource.indexOf('id="sidebar-account-footer"');
    const navOpen = htmlSource.indexOf('id="sidebar-nav"');
    const navClose = htmlSource.indexOf('</nav>', navOpen);
    assert.ok(adminIdx > 0 && footerIdx > adminIdx, 'nav-open-admin must sit above sidebar-account-footer');
    assert.match(htmlSource, /sidebar-admin-entry-wrap/);
    assert.ok(adminIdx > navClose, 'admin entry must be outside scrolling sidebar-nav');
});

test('chat.html pins recent sessions under scenes', () => {
    const htmlSource = fs.readFileSync(path.join(__dirname, '../channel/web/chat.html'), 'utf8');
    const scenesIdx = htmlSource.indexOf('data-view="scenes"');
    const recentIdx = htmlSource.indexOf('id="sidebar-recent"');
    const historyItem = htmlSource.indexOf('data-view="history"');
    assert.ok(scenesIdx > 0, 'scenes menu item exists');
    assert.ok(recentIdx > scenesIdx, 'sidebar-recent sits after scenes');
    assert.equal(historyItem, -1, 'top-level history menu item is removed');
    assert.match(htmlSource, /id="sidebar-recent-list"/);
    assert.match(htmlSource, /id="sidebar-recent-label"/);
    assert.match(htmlSource, /fa-clock-rotate-left/);
    assert.match(htmlSource, /会话历史/);
});

test('sidebar recent sessions keep at most 10', () => {
    const code = section('// === SIDEBAR_RECENT_BEGIN ===', '// === SIDEBAR_RECENT_END ===');
    const sandbox = {};
    vm.runInNewContext(code, sandbox);
    const items = Array.from({ length: 15 }, (_, i) => ({ session_id: 's' + i, title: 't' + i }));
    assert.equal(sandbox._sidebarRecentLimit(items).length, 10);
    assert.equal(sandbox._sidebarRecentLimit(items)[0].session_id, 's0');
    assert.equal(sandbox._sidebarRecentLimit(items.slice(0, 3)).length, 3);
});
