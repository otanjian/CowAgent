// i18n contract for the 消息渠道 page (tenant-owned-message-channels 9.7).
//
// A key referenced by `t('...')` but missing from a dictionary renders as the
// raw key name, so a partially translated console is a real defect. This checks
// both directions for the keys this change introduced: they exist in all three
// languages, and every key the new code calls actually exists.
//
// The dictionaries used to be extracted from console.js by brace matching.
// After change fork-decoupling-and-tenant-hardening (task 8.5) they live in the
// per-domain namespace files under static/js/i18n/; the shared loader merges
// them the same way console.js does.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { loadDictionaries } = require('./support/i18n_namespaces.cjs');

const source = fs.readFileSync(path.join(__dirname, '../channel/web/static/js/console.js'), 'utf8');
const LANGS = ['zh', 'zh-Hant', 'en'];

const dicts = loadDictionaries();

function hasKey(lang, key) {
    return Object.prototype.hasOwnProperty.call(dicts[lang] || {}, key);
}

// The keys this change introduces, grouped by the surface that renders them.
const NEW_KEYS = [
    // 9.1 — the terminal states of a failed channels load.
    'channels_not_open', 'channels_not_open_desc',
    'channels_no_permission', 'channels_no_permission_desc',
    'channels_load_failed', 'channels_load_failed_desc',
    // 9.2-9.5 — the tenant-owned instance list and form.
    'tenant_channel_desc', 'tenant_channel_empty_desc',
    'tenant_channel_active', 'tenant_channel_inactive',
    'tenant_channel_enable', 'tenant_channel_disable',
    'tenant_channel_edit', 'tenant_channel_edit_title',
    'tenant_channel_type_label', 'tenant_channel_display_label',
    'tenant_channel_agent_label', 'tenant_channel_agent_none',
    'tenant_channel_save', 'tenant_channel_secret_note',
    'tenant_password_prompt',
    // 9.5/9.6 — one explanation per write failure the server can return.
    'tenant_channel_error_password', 'tenant_channel_error_conflict',
    'tenant_channel_error_agent', 'tenant_channel_error_missing',
    'tenant_channel_error_invalid',
];

test('every key this change adds exists in all three languages', () => {
    for (const key of NEW_KEYS) {
        for (const lang of LANGS) {
            assert.ok(hasKey(lang, key), `${key} is missing from the ${lang} dictionary`);
        }
    }
});

test('the tenant-channel key set is identical across languages', () => {
    const keysOf = (lang) => {
        const keys = new Set();
        const re = /^(tenant_channel_[a-z_]+|channels_(?:not_open|no_permission|load_failed)[a-z_]*)$/;
        for (const key of Object.keys(dicts[lang] || {})) if (re.test(key)) keys.add(key);
        return keys;
    };
    const per = LANGS.map(keysOf);
    assert.ok(per[0].size >= 20, 'the scan really found the new keys, not an empty set');
    const union = new Set();
    per.forEach(s => s.forEach(k => union.add(k)));
    for (const key of [...union].sort()) {
        for (let i = 0; i < LANGS.length; i++) {
            assert.ok(per[i].has(key), `${key} exists in some languages but not ${LANGS[i]}`);
        }
    }
});

// The strongest direction: a key the code calls must exist everywhere, or the
// operator sees the literal key name. Scan only the region this change added.
test('every translated key the new channels code calls exists', () => {
    const start = source.indexOf('// --- Scope resolution and failure presentation');
    const end = source.indexOf('// --- Add channel panel ---');
    assert.ok(start >= 0 && end > start, 'the new channels section is still locatable');
    const region = source.slice(start, end);

    const called = new Set();
    const re = /\bt\(\s*'([a-zA-Z0-9_]+)'\s*\)/g;
    let m;
    while ((m = re.exec(region))) called.add(m[1]);
    assert.ok(called.size >= 15, 'the scan found the new t() calls');

    for (const key of called) {
        for (const lang of LANGS) {
            assert.ok(hasKey(lang, key),
                `${key} is called by console.js but missing from the ${lang} dictionary`);
        }
    }
});

test('the dictionary loader is not silently reading empty blocks', () => {
    for (const lang of LANGS) {
        assert.ok(hasKey(lang, 'cancel'), lang + ' dictionary content was not extracted');
    }
});
