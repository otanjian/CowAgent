// i18n contract for the tenant editor: a key that exists in one language but
// not the others renders as a raw key name in that locale, which is exactly the
// "half-translated UI" defect this guards against.
//
// After change fork-decoupling-and-tenant-hardening (task 8.5) console.js no
// longer builds `I18N` from a huge literal plus Object.assign blocks; the
// dictionaries now live in per-domain namespace files under static/js/i18n/ and
// the shared loader merges them the way console.js does.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadDictionaries } = require('./support/i18n_namespaces.cjs');

const LANGS = ['zh', 'zh-Hant', 'en'];

const dicts = loadDictionaries();

function keysOf(lang, prefix) {
    const keys = new Set();
    const re = new RegExp('^' + prefix + '[a-z0-9_]*(?:_[a-z0-9_]+)*$');
    for (const key of Object.keys(dicts[lang] || {})) if (re.test(key)) keys.add(key);
    return keys;
}

// The keys this change introduces: the save-time password prompt, the
// multi-tab save result, and the admin tab's select-or-create modes.
const NEW_KEYS = [
    'tenant_password_title',
    'tenant_password_hint',
    'tenant_password_required',
    'tenant_password_wrong',
    'tenant_editor_save_failed_step',
    'tenant_admin_mode_existing',
    'tenant_admin_mode_new',
    'tenant_admin_new_username',
    'tenant_admin_new_display',
    'tenant_admin_new_password',
    'tenant_admin_new_username_required',
    'tenant_admin_new_display_required',
    'tenant_admin_new_password_required',
    'tenant_admin_username_taken',
    'tenant_admin_weak_password',
    'tenant_admin_invalid_username',
    'tenant_current_admin_label',
    'tenant_current_admin_none',
    'tenant_current_admin_unavailable',
    'tenant_tab_agent',
    'tenant_tab_agent_hint',
    'tenant_agent_copy_title',
    'tenant_agent_copy_hint',
    'tenant_agent_current_title',
    'tenant_agent_current_empty',
    'tenant_agent_current_unavailable',
    'tenant_agent_create_hint',
    'tenant_agent_enabled',
    'tenant_agent_disabled',
    'tenant_agent_default_badge',
    'tenant_agent_copied_badge',
    'tenant_agent_source_label',
    'tenant_agent_source_unavailable',
    'tenant_agent_source_empty',
    'tenant_agent_selected_count',
    'tenant_agent_result_copied',
    'tenant_agent_result_skipped',
    'tenant_agent_result_default',
    'tenant_agent_result_failed',
    'tenant_agent_pick_required',
    'tenant_agent_partial_failed',
    'admin_back_to_list',
];

test('every key this change adds exists in all three languages', async () => {
    for (const key of NEW_KEYS) {
        for (const lang of LANGS) {
            assert.ok(Object.prototype.hasOwnProperty.call(dicts[lang] || {}, key),
                `${key} is missing from the ${lang} dictionary`);
        }
    }
});

test('the tenant editor key set is identical across languages', async () => {
    const perLanguage = LANGS.map(lang => keysOf(lang, 'tenant_'));
    assert.ok(perLanguage[0].size > 20,
        'the scan really found the tenant-editor keys, not an empty set');

    const union = new Set();
    perLanguage.forEach(keys => keys.forEach(k => union.add(k)));
    for (const key of [...union].sort()) {
        for (let i = 0; i < LANGS.length; i++) {
            assert.ok(perLanguage[i].has(key),
                `${key} exists in some languages but not ${LANGS[i]}`);
        }
    }
});

test('the account-picker keys the admin tab relies on are translated everywhere', async () => {
    const perLanguage = LANGS.map(lang => keysOf(lang, 'admin_user_picker'));
    assert.ok(perLanguage[0].size > 0, 'the picker keys were found');
    const union = new Set();
    perLanguage.forEach(keys => keys.forEach(k => union.add(k)));
    for (const key of union) {
        for (let i = 0; i < LANGS.length; i++) {
            assert.ok(perLanguage[i].has(key),
                `${key} exists in some languages but not ${LANGS[i]}`);
        }
    }
});

// Guard the loader itself: if it silently returns nothing, the parity
// assertions above would pass vacuously and stop protecting anything.
test('the dictionary loader is not silently reading empty blocks', async () => {
    for (const lang of LANGS) {
        assert.ok(Object.keys(dicts[lang] || {}).length > 100,
            lang + ' dictionary content was not extracted');
        assert.ok(Object.prototype.hasOwnProperty.call(dicts[lang], 'cancel'),
            lang + ' dictionary content was not extracted');
    }
});
