// i18n contract for the tenant editor: a key that exists in one language but
// not the others renders as a raw key name in that locale, which is exactly the
// "half-translated UI" defect this guards against.
//
// The dictionaries cannot be slurped with a regex: `console.js` builds `I18N`
// with a huge literal (containing functions and nested objects) and then extends
// each language with several `Object.assign(I18N.<lang>, {...})` blocks. Running
// the whole file in a sandbox is also not an option — it touches modules that do
// not exist outside the browser and throws partway through, before the later
// extension blocks are applied. So the blocks are located by brace matching and
// attributed to their language explicitly.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(
    path.join(__dirname, '../channel/web/static/js/console.js'), 'utf8');

const LANGS = ['zh', 'zh-Hant', 'en'];

// Index of the `}` matching the `{` at `openIdx`, skipping strings and comments
// so braces inside translated text cannot unbalance the scan.
function blockEnd(src, openIdx) {
    let depth = 0;
    let quote = null;
    for (let i = openIdx; i < src.length; i++) {
        const c = src[i];
        if (quote) {
            if (c === '\\') { i++; continue; }
            if (c === quote) quote = null;
            continue;
        }
        if (c === '"' || c === "'" || c === '`') { quote = c; continue; }
        if (c === '/' && src[i + 1] === '/') {
            const nl = src.indexOf('\n', i);
            i = nl === -1 ? src.length : nl;
            continue;
        }
        if (c === '/' && src[i + 1] === '*') {
            const close = src.indexOf('*/', i);
            i = close === -1 ? src.length : close + 1;
            continue;
        }
        if (c === '{') depth++;
        else if (c === '}') {
            depth--;
            if (depth === 0) return i;
        }
    }
    throw new Error('unbalanced braces at ' + openIdx);
}

function blocksByLanguage(src) {
    const out = { zh: [], 'zh-Hant': [], en: [] };

    const baseIdx = src.indexOf('const I18N = {');
    assert.ok(baseIdx >= 0, 'the I18N literal is still where this test expects it');
    const baseOpen = src.indexOf('{', baseIdx);
    const base = src.slice(baseOpen, blockEnd(src, baseOpen) + 1);
    for (const lang of LANGS) {
        const re = new RegExp("(?:^|[\\s,{])'?" + lang + "'?\\s*:\\s*\\{");
        const m = re.exec(base);
        assert.ok(m, 'the base literal still declares ' + lang);
        const open = base.indexOf('{', m.index + m[0].length - 1);
        out[lang].push(base.slice(open, blockEnd(base, open) + 1));
    }

    const assign = /Object\.assign\(\s*I18N(?:\.([A-Za-z-]+)|\[\s*['"]([A-Za-z-]+)['"]\s*\])\s*,\s*\{/g;
    let m;
    while ((m = assign.exec(src))) {
        const lang = m[1] || m[2];
        if (!LANGS.includes(lang)) continue;
        const open = m.index + m[0].length - 1;
        out[lang].push(src.slice(open, blockEnd(src, open) + 1));
    }
    return out;
}

function keysOf(blocks, prefix) {
    const keys = new Set();
    const re = new RegExp('\\b(' + prefix + '[a-z0-9_]*(?:_[a-z0-9_]+)*)\\s*:', 'g');
    for (const block of blocks) {
        let m;
        while ((m = re.exec(block))) keys.add(m[1]);
    }
    return keys;
}

const blocks = blocksByLanguage(source);

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
            const re = new RegExp('\\b' + key + '\\s*:');
            assert.ok(re.test(blocks[lang].join('\n')),
                `${key} is missing from the ${lang} dictionary`);
        }
    }
});

test('the tenant editor key set is identical across languages', async () => {
    const perLanguage = LANGS.map(lang => keysOf(blocks[lang], 'tenant_'));
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
    const perLanguage = LANGS.map(lang => keysOf(blocks[lang], 'admin_user_picker'));
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

// Guard the scanner itself: if the extraction silently returns nothing, the
// parity assertions above would pass vacuously and stop protecting anything.
test('the dictionary scanner is not silently reading empty blocks', async () => {
    for (const lang of LANGS) {
        assert.ok(blocks[lang].length >= 2,
            lang + ' should have the base literal plus its Object.assign blocks');
        assert.ok(blocks[lang].join('\n').includes('cancel'),
            lang + ' dictionary content was not extracted');
    }
});
