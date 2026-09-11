// i18n contract for the external-identity binding dialog.
//
// A key the dialog calls but a dictionary lacks renders as the literal key name
// — "extid_error_conflict" shown to an administrator who just hit a conflict is
// exactly the situation the message exists to explain. So both directions are
// checked: the keys the code calls exist in all three languages, and the three
// dictionaries carry the same set.
//
// The strongest check reads the *code* rather than a hand-kept list, so a key
// added later without a translation fails here instead of shipping.
//
// After change fork-decoupling-and-tenant-hardening (task 8.5) the dictionaries
// live in per-domain namespace files under static/js/i18n/; the shared loader
// merges them the way console.js does.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { loadDictionaries } = require('./support/i18n_namespaces.cjs');

const adminSource = fs.readFileSync(path.join(__dirname, '../channel/web/static/js/identity-admin.js'), 'utf8');
const LANGS = ['zh', 'zh-Hant', 'en'];

const dicts = loadDictionaries();

function hasKey(lang, key) {
    return Object.prototype.hasOwnProperty.call(dicts[lang] || {}, key);
}

// The keys the dialog itself names, so a renamed key cannot quietly drop a row.
const NAMED_KEYS = [
    'extid_title', 'extid_bound_title', 'extid_no_bindings',
    'extid_add_title', 'extid_field_issuer', 'extid_field_subject',
    'extid_field_hint', 'extid_bind', 'extid_attempts_title',
    'extid_attempts_hint', 'extid_no_attempts', 'extid_bound_ok',
    'extid_unbound_ok', 'extid_error_subject_required', 'extid_error_conflict',
    'extid_error_forbidden', 'extid_error_not_found', 'extid_error_bad_request',
    'extid_error_generic', 'extid_provider_unknown',
];

test('every key the dialog names exists in all three languages', () => {
    for (const key of NAMED_KEYS) {
        for (const lang of LANGS) {
            assert.ok(hasKey(lang, key), `${key} is missing from the ${lang} dictionary`);
        }
    }
});

test('every extid key the dialog code calls exists in all three languages', () => {
    // Provider labels are built from a code (`'extid_provider_' + code`), so
    // scan both the literal calls and the provider codes.
    const called = new Set();
    const literals = /\bt\(\s*'(extid_[a-zA-Z0-9_]+)'\s*\)/g;
    let m;
    while ((m = literals.exec(adminSource))) called.add(m[1]);
    for (const code of ['feishu', 'wecom_bot', 'weixin', 'dingtalk', 'wechatcom_app']) {
        called.add('extid_provider_' + code);
    }
    assert.ok(called.size >= 15, 'the scan really found the dialog t() calls');

    for (const key of called) {
        for (const lang of LANGS) {
            assert.ok(hasKey(lang, key),
                `${key} is called by identity-admin.js but missing from the ${lang} dictionary`);
        }
    }
});

test('the provider label falls back to the raw code, never to a blank', () => {
    // A channel type this build does not know about must still identify the
    // channel; showing nothing would leave the binding unidentifiable.
    assert.match(adminSource, /label === key \? raw : label/);
});

test('the extid key set is identical across languages', () => {
    const keysOf = (lang) => {
        const keys = new Set();
        const re = /^extid_[a-z_]+$/;
        for (const key of Object.keys(dicts[lang] || {})) if (re.test(key)) keys.add(key);
        return keys;
    };
    const per = LANGS.map(keysOf);
    assert.ok(per[0].size >= 20, 'the scan found the new keys, not an empty set');
    const union = new Set();
    per.forEach(s => s.forEach(k => union.add(k)));
    for (const key of [...union].sort()) {
        for (let i = 0; i < LANGS.length; i++) {
            assert.ok(per[i].has(key), `${key} exists in some languages but not ${LANGS[i]}`);
        }
    }
});

test('the dictionary loader is not silently reading empty blocks', () => {
    for (const lang of LANGS) {
        assert.ok(hasKey(lang, 'cancel'), lang + ' dictionary content was not extracted');
    }
});
