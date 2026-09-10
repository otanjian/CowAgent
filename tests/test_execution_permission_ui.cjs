// The conversation surfaces must not offer a self-service way to raise the
// session permission mode: execution is governed by the caller's role grants.
// The refused-tool hint stays (it explains why) but carries no action button.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');

test('web console has no session permission selector', () => {
    const markup = read('channel/web/chat.html');
    const consoleJs = read('channel/web/static/js/console.js');
    assert.doesNotMatch(markup, /permission-selector-btn/);
    assert.doesNotMatch(markup, /permission-selector-menu/);
    assert.doesNotMatch(consoleJs, /togglePermissionSelector/);
    assert.doesNotMatch(consoleJs, /renderPermissionMenu/);
});

test('web refused-tool hint keeps its explanation but drops the action button', () => {
    const consoleJs = read('channel/web/static/js/console.js');
    assert.match(consoleJs, /perm-denied-hint/);
    assert.match(consoleJs, /perm_denied_hint/);
    assert.match(consoleJs, /perm_denied_role_hint/);
    assert.doesNotMatch(consoleJs, /perm-denied-btn/);
    assert.doesNotMatch(consoleJs, /perm_denied_action/);
});

test('desktop has no permission selector or adjust button', () => {
    const chatInput = read('desktop/src/renderer/src/components/ChatInput.tsx');
    const messageSteps = read('desktop/src/renderer/src/components/MessageSteps.tsx');
    const store = read('desktop/src/renderer/src/store/sessionSettingsStore.ts');
    assert.doesNotMatch(chatInput, /PermissionSelector/);
    assert.doesNotMatch(messageSteps, /perm_denied_action/);
    assert.match(messageSteps, /perm_denied_role_hint/);
    assert.doesNotMatch(store, /'permission'/);
    assert.equal(
        fs.existsSync(path.join(root, 'desktop/src/renderer/src/components/PermissionSelector.tsx')),
        false,
    );
});

test('the global default permission reads as owned by roles in database mode', () => {
    const handlers = read('channel/web/web_channel.py');
    assert.match(handlers, /permission_mode_editable/);
    assert.match(handlers, /permission_mode_source/);

    // Web console: the dropdown is rendered read-only from that flag.
    const consoleJs = read('channel/web/static/js/console.js');
    assert.match(consoleJs, /permission_mode_editable/);
    assert.match(consoleJs, /cfg-dropdown-readonly/);
    assert.match(consoleJs, /config_permission_role_desc/);
    assert.match(read('channel/web/chat.html'), /cfg-permission-role-desc/);

    // Desktop settings page: same read-only treatment.
    const basic = read('desktop/src/renderer/src/pages/settings/BasicSettings.tsx');
    assert.match(basic, /permission_mode_editable/);
    assert.match(basic, /config_permission_role_desc/);
});
