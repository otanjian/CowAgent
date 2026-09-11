'use strict';
// Load the shipped per-domain i18n dictionaries exactly the way console.js
// does: run every file under static/js/i18n/ with a minimal `window` and merge
// the registered namespaces. Used by the i18n contract tests after the split
// in change fork-decoupling-and-tenant-hardening (task 8.5).
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const I18N_DIR = path.join(__dirname, '../../channel/web/static/js/i18n');

function loadNamespaces(dir = I18N_DIR) {
    const ctx = { window: {} };
    vm.createContext(ctx);
    const files = fs.readdirSync(dir).filter(f => f.endsWith('.js')).sort();
    for (const file of files) {
        vm.runInContext(fs.readFileSync(path.join(dir, file), 'utf8'), ctx, { filename: file });
    }
    return ctx.window.__cowI18N__ || {};
}

function mergeNamespaces(registry) {
    const merged = {};
    for (const domain of Object.keys(registry)) {
        for (const lang of Object.keys(registry[domain])) {
            Object.assign(merged[lang] || (merged[lang] = {}), registry[domain][lang]);
        }
    }
    return merged;
}

function loadDictionaries() {
    return mergeNamespaces(loadNamespaces());
}

module.exports = { I18N_DIR, loadNamespaces, mergeNamespaces, loadDictionaries };
