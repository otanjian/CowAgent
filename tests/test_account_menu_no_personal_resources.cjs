// The 「我的资源」 group and its five entries are gone from the account menu
// (change unify-console-by-data-scope, task 3.3; supersedes
// move-personal-menu-to-account, which had *added* them there).
//
// The account menu now carries account operations only. Business resources are
// reached through the existing console pages, so the panel must not host resource
// entries, must not own a resource verdict, and must not start a business read
// merely because it was opened or repainted. What survives is pinned here too:
// identity, 个人资料, 账号安全, 个人偏好, 帮助与关于, 退出登录 and the brand version.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const read = (...parts) => fs.readFileSync(path.join(__dirname, '..', ...parts), 'utf8');
const html = read('channel/web/chat.html');
const consoleJs = read('channel/web/static/js/console.js');
const accountI18n = read('channel/web/static/js/i18n/account.js');
const css = read('channel/web/static/css/console.css');

// The account panel markup only, so the checks below cannot be satisfied or
// broken by unrelated regions of the page.
const menuStart = html.indexOf('id="sidebar-account-menu"');
const menuEnd = html.indexOf('id="account-menu-backdrop"', menuStart);
const accountMenu = html.slice(menuStart, menuEnd);
const footerStart = html.indexOf('id="sidebar-account-footer"');
const footer = html.slice(footerStart, menuStart);

const PERSONAL_VIEWS = ['personal-agents', 'personal-channels', 'personal-memory',
    'personal-tools', 'personal-skills'];

// Every symbol that existed only to host the five entries in the account panel.
const RETIRED_SYMBOLS = ['ACCOUNT_PERSONAL_VIEWS', '_isPersonalView', '_accountPersonalEntries',
    '_accountPersonalEntry', '_accountProjectionPhase', '_consolePageEntryState',
    '_renderAccountResources', 'refreshAccountResources', '_syncAccountPersonalCurrent',
    '_clearAccountPersonalState', '_focusPersonalTarget', 'openPersonalEntry'];

test('the account panel hosts no 「我的资源」 group and no personal entry', () => {
    assert.ok(menuStart >= 0 && menuEnd > menuStart, 'the panel markup must be delimited');
    assert.doesNotMatch(accountMenu, /id="account-menu-resources"/);
    assert.doesNotMatch(accountMenu, /account-menu-personal/);
    assert.doesNotMatch(accountMenu, /data-i18n="account_menu_resources/);
    for (const view of PERSONAL_VIEWS) {
        assert.doesNotMatch(accountMenu, new RegExp(`data-view="${view}"`), view);
    }
    // The five 我的* labels must not survive anywhere in the panel either.
    for (const key of ['menu_personal_agents', 'menu_personal_channels', 'menu_personal_memory',
                       'menu_personal_tools', 'menu_personal_skills']) {
        assert.doesNotMatch(accountMenu, new RegExp(`data-i18n="${key}"`), key);
    }
    assert.doesNotMatch(html, /我的资源/);
});

test('the account panel keeps exactly the retained entries, in order', () => {
    const order = ['account-menu-identity', 'account-menu-settings', 'account-menu-about',
        'account-menu-logout', 'sidebar-version']
        .map(id => html.indexOf('id="' + id + '"'));
    assert.ok(order.every(index => index >= 0), 'every retained region must exist');
    assert.deepEqual([...order].sort((a, b) => a - b), order,
        'identity → 账号设置 → 帮助与关于/退出 → 品牌版本');
    // The account-setting actions the panel still owns.
    for (const id of ['account-menu-profile', 'account-menu-password', 'account-menu-prefs']) {
        assert.ok(html.indexOf('id="' + id + '"') >= 0, id + ' must survive');
    }
    // And no empty group was left behind: 账号设置 is the only group title.
    assert.equal((accountMenu.match(/account-menu-group-title/g) || []).length, 1);
    assert.equal((accountMenu.match(/data-i18n="account_menu_settings"/g) || []).length, 1);
});

test('the account trigger describes account settings, not personal resources', () => {
    // The personal-area description lived on the trigger and only made sense
    // while the five entries did.
    assert.ok(footer.indexOf('id="sidebar-account-toggle"') >= 0, 'the trigger must exist');
    assert.doesNotMatch(footer, /id="sidebar-account-region"/);
    assert.doesNotMatch(html, /account_menu_region_personal/);
    assert.doesNotMatch(footer, /aria-describedby="sidebar-account-region"/);
    // 三语: the hint no longer promises personal resources.
    const zh = accountI18n.slice(accountI18n.indexOf('"account_menu_trigger_hint"'));
    const hint = zh.slice(0, zh.indexOf('\n'));
    assert.match(hint, /账号设置/);
    assert.doesNotMatch(hint, /个人资源/);
    assert.doesNotMatch(accountI18n, /"account_menu_trigger_hint": "[^"]*[Pp]ersonal resources/);
});

test('the retired resource-hosting symbols are gone from console.js', () => {
    for (const symbol of RETIRED_SYMBOLS) {
        assert.doesNotMatch(consoleJs, new RegExp(`\\b${symbol}\\b`), symbol + ' must be removed');
    }
});

test('no group, status or retry chrome is left for the deleted entries', () => {
    assert.doesNotMatch(html, /account-menu-resources-status/);
    assert.doesNotMatch(html, /account-menu-resources-retry/);
    assert.doesNotMatch(html, /refreshAccountResources/);
    assert.doesNotMatch(css, /\.account-menu-resources-status/);
    assert.doesNotMatch(css, /\.account-menu-personal/);
    assert.doesNotMatch(css, /\.sidebar-account-footer\.is-personal/);
});

test('the account panel repaint never asks for the five business resources', () => {
    // Opening, repainting (language switch, brand update) or retrying the panel
    // must not become a business read. The panel's own retry still re-asks the
    // tenant-scoped capability summary, which is identity, not resource data.
    const repaint = consoleJs.slice(consoleJs.indexOf('function refreshAccountIdentity'),
        consoleJs.indexOf('function refreshAccountIdentity') + 4000);
    assert.doesNotMatch(repaint, /_renderAccountResources|_syncAccountPersonalCurrent/);
    // The single current-page marker is now the main navigation's alone: the
    // account panel no longer claims a current item.
    assert.doesNotMatch(consoleJs, /sidebar-account-region/);
    assert.doesNotMatch(consoleJs, /is-personal/);
});

test('the removed 三语 keys are gone and no locale still ships them', () => {
    for (const key of ['account_menu_resources', 'account_menu_resources_checking',
                       'account_menu_resources_failed', 'account_menu_resources_retry',
                       'account_menu_region_personal']) {
        assert.doesNotMatch(accountI18n, new RegExp(`"${key}"`), key + ' must be removed');
    }
});
