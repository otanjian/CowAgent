// Fork fragment mount-point contract (change fork-decoupling-and-tenant-hardening, task 8.8).
//
// Fork-specific markup used to live inline in chat.html, which made every
// upstream merge conflict on that region. It now lives in a standalone fragment
// under channel/web/static/fragments/ that a generic loader injects into a mount
// element. The mount element keeps the original id/classes/ARIA contract, and the
// fragment keeps every id/name/attribute that JS and the browser contract tests
// depend on. These assertions fail if either half of that contract regresses.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const read = p => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const chatHtml = read('channel/web/chat.html');
const loader = read('channel/web/static/js/fragments.js');
const fragment = read('channel/web/static/fragments/appearance-dialog.html');

test('chat.html keeps the appearance dialog mount point and drops the inline markup', () => {
    assert.match(chatHtml,
        /<dialog id="appearance-dialog" aria-labelledby="appearance-title" aria-describedby="appearance-scope"[\s\S]{0,200}?data-fork-fragment="assets\/fragments\/appearance-dialog\.html"><\/dialog>/);
    // The fork-only inner markup must no longer be inlined in the core file.
    assert.doesNotMatch(chatHtml, /id="appearance-options"/);
    assert.doesNotMatch(chatHtml, /name="web-palette"/);
});

test('the loader script is deferred and runs before console.js', () => {
    assert.match(loader, /window\.CowFragments\s*=/);
    assert.match(loader, /querySelectorAll\('\[data-fork-fragment\]'\)/);
    assert.match(loader, /typeof window\.applyI18n === 'function'/);
    const loaderIdx = chatHtml.indexOf('assets/js/fragments.js');
    const consoleIdx = chatHtml.indexOf('assets/js/console.js');
    assert.ok(loaderIdx >= 0, 'fragments.js script tag missing');
    assert.ok(consoleIdx >= 0, 'console.js script tag missing');
    assert.ok(loaderIdx < consoleIdx, 'fragments.js must load before console.js');
});

test('the fragment preserves the DOM contract JS and browser tests rely on', () => {
    for (const id of ['appearance-options', 'appearance-title', 'appearance-scope',
        'appearance-resolved', 'appearance-storage-warning', 'appearance-language-warning']) {
        assert.match(fragment, new RegExp(`id="${id}"`), `fragment lost #${id}`);
    }
    for (const name of ['web-palette', 'web-mode', 'web-language']) {
        assert.match(fragment, new RegExp(`name="${name}"`), `fragment lost input[name=${name}]`);
    }
    for (const key of ['appearance_title', 'appearance_scope', 'appearance_storage_failed',
        'appearance_reset', 'appearance_instant', 'account_prefs_lang']) {
        assert.match(fragment, new RegExp(`data-i18n="${key}"`),
            `fragment lost data-i18n=${key}`);
    }
    assert.match(fragment, /onclick="closeAppearancePreferences\(\)"/);
    assert.match(fragment, /onclick="CowAppearance\.reset\(\)"/);
});

test('the mount point is a documented, stable anchor', () => {
    // The comment is what tells a future upstream merge that the element is a
    // deliberate mount point, not dead markup it may delete. Without it the
    // anchor (and the fragment contract) can be silently lost.
    assert.match(chatHtml, /Fork fragment mount point[\s\S]{0,700}?data-fork-fragment=/,
        'the appearance-dialog mount point must carry its documenting comment');
});

test('the server cache-busts the fragment loader and the fragment markup', () => {
    // fragments.js fetches the fragment HTML at runtime. If either URL is not
    // cache-busted, an upgraded server can still serve a browser-cached copy of
    // the old loader / fragment, which is exactly the stale-asset bug the
    // console's existing cache_bust list prevents for the other first-party
    // assets.
    const server = read('channel/web/web_channel.py');
    assert.match(server, /'js\/fragments\.js'/, 'fragments.js must be cache-busted');
    assert.match(server, /fragments_dir = os\.path\.join\([^\n]*'static', 'fragments'\)/,
        'static/fragments must be discovered so its markup is cache-busted');
    assert.match(server, /f'fragments\/\{name\}'/,
        'fragment assets must be added to the cache_bust list');
});
