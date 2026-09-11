const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { File } = require('node:buffer');

const source = fs.readFileSync(path.join(__dirname, '../channel/web/static/js/console.js'), 'utf8');
const start = source.indexOf('let brandingDraft =');
const brandingSource = source.slice(start, source.indexOf("\ndocument.addEventListener('DOMContentLoaded', function()", start));

// Small DOM boundary fixture; tests execute production event handlers and
// state transitions, including the actual FormData sent to the route.
function element(value = '') {
    const classes = new Set();
    return {
        value, dataset: {}, disabled: false, handlers: {}, textContent: '', innerHTML: '',
        classList: {
            add(...names) { names.forEach(n => classes.add(n)); },
            remove(...names) { names.forEach(n => classes.delete(n)); },
            contains(n) { return classes.has(n); },
            toggle(n, force) { if (force) classes.add(n); else classes.delete(n); },
        },
        addEventListener(event, handler) { this.handlers[event] = handler; },
        querySelectorAll() { return []; },
        closest() { return null; },
    };
}

function setup() {
    const elements = new Map();
    const el = id => { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); };
    const captions = [element()];
    const descriptions = [element(), element()];
    const slots = selector => selector === '[data-brand-slot="caption"]' ? captions
        : selector === '[data-brand-slot="desc"]' ? descriptions : [];
    el('branding-preview-canvas').querySelectorAll = slots;
    const published = { status: 'success', enabled: true, revision: 1, brand_name: 'Saved', logo_description: 'Description',
        logo_url: '/old.png', favicon_url: '/old-icon.png', can_manage: true, can_reset: true };
    const response = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });
    const calls = [];
    let confirm;
    const context = vm.createContext({
        document: { getElementById: el, querySelectorAll: slots },
        FormData, File, URL: { createObjectURL: () => 'blob:chosen-logo' },
        setTimeout() {}, brandingDirty: false, brandFetchSeq: 0, brandSaveEpoch: 0,
        brandLoaded: true, appConfig: {},
        DEFAULT_BRAND: { brand_name: 'Default', logo_url: '/default.svg', favicon_url: '/default.ico' },
        t: s => s, escapeHtml: s => s, brandWordmarkHTML: s => s, productTitle: s => s,
        effectiveLogoUrl: () => published.logo_url,
        _isDefaultLogoDescription: () => false,
        applyBrandToDocument() {}, applyBrandToAgentAvatars() {}, renderAccountVersion() {},
        showConfirmDialog(options) { confirm = options; },
        fetch: async (url, options) => { calls.push({url, options}); return response(published); },
    });
    const run = code => vm.runInContext(code, context);
    run('function _isDefaultLogoDescription() { return false; }');
    run(brandingSource);
    run('_brandingBindEvents()');
    return {context, run, el, captions, descriptions, calls, published, response, getConfirm: () => confirm};
}

test('upload then default sends no file, preserves text, platform-admin session only', async () => {
    const h = setup();
    await h.run('_loadBrandingSetup()');
    h.el('branding-brand-name').value = 'Draft name';
    h.el('branding-brand-name').handlers.input();
    h.el('branding-logo-file').files = [new File(['png'], 'logo.png', {type: 'image/png'})];
    h.el('branding-logo-file').handlers.change();
    h.el('branding-default-logo-btn').handlers.click();
    await h.run('_brandingSubmitSave()');
    const {options} = h.calls.at(-1);
    assert.equal(options.body.get('logo_action'), 'default');
    assert.equal(options.body.has('logo'), false);
    assert.equal(options.body.get('brand_name'), 'Draft name');
    assert.equal(options.body.get('logo_description'), 'Description');
    assert.equal(options.credentials, 'same-origin');
    assert.equal(options.headers && options.headers['X-Branding-CSRF'], undefined);
});

for (const conflict of [false, true]) {
    test(`cancel discard keeps draft; confirmed reload replaces it (conflict=${conflict})`, async () => {
        const h = setup();
        await h.run('_loadBrandingSetup()');
        h.el('branding-brand-name').value = 'Keep this draft';
        h.el('branding-brand-name').handlers.input();
        h.run(`brandingConflict = ${conflict}; initBrandingView()`);
        assert.ok(h.getConfirm());
        // Dismissing the production modal doesn't call onConfirm.
        assert.equal(h.calls.length, 1);
        assert.equal(h.el('branding-brand-name').value, 'Keep this draft');
        assert.equal(h.run('brandingDirty'), true);
        await h.getConfirm().onConfirm();
        assert.equal(h.calls.length, 2);
        assert.equal(h.el('branding-brand-name').value, 'Saved');
        assert.equal(h.run('brandingConflict'), false);
    });
}

test('empty description hides all preview and published caption rows', async () => {
    const h = setup();
    await h.run('_loadBrandingSetup()');
    h.el('branding-logo-desc').value = '';
    h.el('branding-logo-desc').handlers.input();
    for (const row of [...h.captions, ...h.descriptions]) {
        assert.equal(row.textContent, '');
        assert.equal(row.classList.contains('hidden'), true);
    }
    Object.assign(h.context, {effectiveBrandName: () => 'Saved', effectiveLogoDescription: () => '',
        effectiveFaviconUrl: () => '/icon.png', _brandArmFallback() {}});
    h.run('function welcomeHeroDescription() { return ""; }');
    h.run(source.slice(source.indexOf('function applyBrandToDocument()'), source.indexOf('function applyBrandToAgentAvatars()')));
    h.run('applyBrandToDocument()');
    assert.equal(h.el('sidebar-brand-caption').textContent, '');
    assert.equal(h.el('sidebar-brand-caption').classList.contains('hidden'), true);
});

test('409 preserves draft and offers a confirmed reload', async () => {
    const h = setup();
    await h.run('_loadBrandingSetup()');
    h.el('branding-brand-name').value = 'Draft';
    h.el('branding-brand-name').handlers.input();
    h.context.fetch = async () => h.response({status:'error',code:'version_conflict'},409);
    await h.run('_brandingSubmitSave()');
    assert.equal(h.el('branding-brand-name').value, 'Draft');
    assert.equal(h.run('brandingConflict'), true);
    assert.equal(h.el('branding-save').disabled, true);
    h.el('branding-reload-btn').handlers.click();
    assert.ok(h.getConfirm());
});

test('damaged storage disables editing but allows explicit protected reset', async () => {
    const h = setup();
    h.published.can_manage = false;
    h.published.readonly_reason = 'branding_storage_corrupt';
    await h.run('_loadBrandingSetup()');
    assert.equal(h.el('branding-brand-name').disabled, true);
    assert.equal(h.el('branding-save').disabled, true);
    assert.equal(h.el('branding-reset-all').disabled, false);
    h.el('branding-reset-all').handlers.click();
    assert.equal(h.calls.length, 1);
    h.getConfirm().onConfirm();
    assert.equal(h.calls.at(-1).url, '/api/branding/reset');
    assert.equal(h.calls.at(-1).options.credentials, 'same-origin');
    assert.equal(
        h.calls.at(-1).options.headers && h.calls.at(-1).options.headers['X-Branding-CSRF'],
        undefined,
    );
});

test('management read failure disables writes and offers retry', async () => {
    const h = setup();
    h.context.fetch = async () => { throw new Error('offline'); };
    await h.run('_loadBrandingSetup()');
    assert.equal(h.el('branding-brand-name').disabled, true);
    assert.equal(h.el('branding-save').disabled, true);
    assert.equal(h.el('branding-reset-all').disabled, true);
    assert.ok(h.el('branding-reload-btn').handlers.click);
});
