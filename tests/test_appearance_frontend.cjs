// Execute the shipped controller with browser storage/system events. Real-page
// acceptance covers first paint, translations, focus and the CSS surfaces.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createAppearanceController } = require('../channel/web/static/js/appearance.js');

const MODE = 'cow_theme';
const PALETTE = 'cow_web_palette';

function eventTarget() {
    const listeners = new Map();
    return {
        addEventListener(type, listener) {
            if (!listeners.has(type)) listeners.set(type, new Set());
            listeners.get(type).add(listener);
        },
        removeEventListener(type, listener) { listeners.get(type)?.delete(listener); },
        dispatch(type, event = {}) {
            for (const listener of [...(listeners.get(type) || [])]) listener({ type, ...event });
        },
        listenerCount(type) { return listeners.get(type)?.size || 0; },
    };
}

function storageFixture(initial = {}) {
    const data = new Map(Object.entries(initial));
    const writes = [];
    const failures = { read: new Set(), write: new Set() };
    const storage = {
        getItem(key) {
            if (failures.read.has(key)) throw Error('Storage read denied');
            return data.get(key) ?? null;
        },
        setItem(key, value) {
            writes.push([key, String(value)]);
            if (failures.write.has(key)) throw Error('Storage write denied');
            data.set(key, String(value));
        },
        removeItem(key) { writes.push([key, null]); data.delete(key); },
        clear() { writes.push(['clear']); data.clear(); },
    };
    return { data, writes, failures, storage };
}

function setup({ initial = {}, store = storageFixture(initial), systemDark = false,
    detectSystem = true, legacyMedia = false, rootPresent = true } = {}) {
    const events = eventTarget();
    const mediaEvents = eventTarget();
    const media = { matches: systemDark };
    if (legacyMedia) {
        media.addListener = listener => mediaEvents.addEventListener('change', listener);
        media.removeListener = listener => mediaEvents.removeEventListener('change', listener);
    } else {
        media.addEventListener = mediaEvents.addEventListener;
        media.removeEventListener = mediaEvents.removeEventListener;
    }
    const classes = new Set();
    const root = {
        attrs: {}, style: {},
        setAttribute(key, value) { this.attrs[key] = value; },
        classList: {
            toggle(key, enabled) { if (enabled) classes.add(key); else classes.delete(key); },
            contains: key => classes.has(key),
        },
    };
    const env = {
        storage: store.storage, root: rootPresent ? root : null,
        addEventListener: events.addEventListener,
        removeEventListener: events.removeEventListener,
        ...(detectSystem ? { matchMedia: () => media } : {}),
    };
    const controller = createAppearanceController(env);
    return {
        controller, root, store, events, mediaEvents,
        systemChange(dark) { media.matches = dark; mediaEvents.dispatch('change', { matches: dark }); },
        storageEvent(key = null, extra = {}) {
            events.dispatch('storage', { key, storageArea: store.storage, ...extra });
        },
    };
}

function expectAppearance(h, palette, mode, resolved, storageFailed = false) {
    assert.deepEqual(h.controller.getState(), { palette, mode, resolved, storageFailed });
    assert.equal(h.root.attrs['data-web-palette'], palette);
    assert.equal(h.root.classList.contains('dark'), resolved === 'dark');
    assert.equal(h.root.style.colorScheme, resolved);
}

for (const [label, initial, palette, mode] of [
    ['new browser', {}, 'business', 'system'],
    ['legacy light', { [MODE]: 'light' }, 'classic', 'light'],
    ['legacy dark', { [MODE]: 'dark' }, 'classic', 'dark'],
    ['saved system without palette', { [MODE]: 'system' }, 'business', 'system'],
    ['bad mode without palette', { [MODE]: 'sepia' }, 'business', 'system'],
    ['bad palette preserves light', { [PALETTE]: 'unknown', [MODE]: 'light' }, 'business', 'light'],
    ['bad palette preserves dark', { [PALETTE]: '', [MODE]: 'dark' }, 'business', 'dark'],
    ['bad mode preserves palette', { [PALETTE]: 'slate', [MODE]: 'auto' }, 'slate', 'system'],
    ['missing mode preserves palette', { [PALETTE]: 'classic' }, 'classic', 'system'],
    ['both invalid', { [PALETTE]: 'remote-theme-url', [MODE]: 'DARK' }, 'business', 'system'],
]) {
    test(`startup: ${label}, without implicit persistence`, () => {
        const h = setup({ initial, systemDark: true });
        expectAppearance(h, palette, mode, mode === 'system' ? 'dark' : mode);
        h.controller.apply();
        assert.deepEqual(h.store.writes, []);
        assert.deepEqual(Object.fromEntries(h.store.data), initial);
    });
}

test('all saved preset/mode combinations restore independently', () => {
    for (const palette of ['business', 'slate', 'classic']) {
        for (const mode of ['light', 'dark', 'system']) {
            const h = setup({ initial: { [PALETTE]: palette, [MODE]: mode } });
            expectAppearance(h, palette, mode, mode === 'system' ? 'light' : mode);
            assert.deepEqual(h.store.writes, []);
        }
    }
});

test('system mode follows changes without overwriting the saved preference', () => {
    const h = setup({ initial: { [PALETTE]: 'slate', [MODE]: 'system' } });
    const notifications = [];
    h.controller.subscribe(state => notifications.push(state));
    h.systemChange(true);
    expectAppearance(h, 'slate', 'system', 'dark');
    assert.equal(notifications.at(-1).resolved, 'dark');
    h.systemChange(false);
    expectAppearance(h, 'slate', 'system', 'light');
    assert.equal(h.store.data.get(MODE), 'system');
    assert.deepEqual(h.store.writes, []);
});

test('explicit modes ignore system changes and selecting a palette preserves the mode', () => {
    const h = setup();
    h.controller.setMode('light');
    h.systemChange(true);
    h.controller.setPalette('classic');
    expectAppearance(h, 'classic', 'light', 'light');
    h.controller.setMode('dark');
    h.systemChange(false);
    h.controller.setPalette('slate');
    expectAppearance(h, 'slate', 'dark', 'dark');
    h.controller.setMode('system');
    expectAppearance(h, 'slate', 'system', 'light');
});

test('unavailable system detection falls back to light while retaining system', () => {
    const h = setup({ detectSystem: false, initial: { [PALETTE]: 'slate', [MODE]: 'system' } });
    expectAppearance(h, 'slate', 'system', 'light');
    h.controller.setPalette('classic');
    assert.equal(h.store.data.get(MODE), 'system');
});

test('legacy media listeners follow system changes and detach on destruction', () => {
    const h = setup({ legacyMedia: true });
    assert.equal(h.mediaEvents.listenerCount('change'), 1);
    h.systemChange(true);
    expectAppearance(h, 'business', 'system', 'dark');
    h.controller.destroy();
    assert.equal(h.mediaEvents.listenerCount('change'), 0);
});

test('the first mode-only edit persists the migrated classic palette across refresh', () => {
    const h = setup({ initial: { [MODE]: 'dark', cow_language: 'en' } });
    h.controller.setMode('system');
    expectAppearance(h, 'classic', 'system', 'light');
    assert.deepEqual(new Set(h.store.writes.map(([key]) => key)), new Set([MODE, PALETTE]));
    const refreshed = setup({ store: h.store });
    expectAppearance(refreshed, 'classic', 'system', 'light');
    assert.equal(h.store.data.get('cow_language'), 'en');
});

test('the first palette-only edit persists the complete selection', () => {
    const h = setup();
    h.controller.setPalette('slate');
    assert.equal(h.store.data.get(PALETTE), 'slate');
    assert.equal(h.store.data.get(MODE), 'system');
    expectAppearance(setup({ store: h.store, systemDark: true }), 'slate', 'system', 'dark');
});

for (const failedKey of [MODE, PALETTE]) {
    test(`partial write failure (${failedKey}) preserves the local selection and reports failure`, () => {
        const h = setup({ initial: { [PALETTE]: 'classic', [MODE]: 'dark' } });
        h.store.failures.write.add(failedKey);
        h.controller.setPalette('slate');
        h.controller.setMode('light');
        expectAppearance(h, 'slate', 'light', 'light', true);
        assert.ok(h.store.writes.some(([key]) => key === MODE));
        assert.ok(h.store.writes.some(([key]) => key === PALETTE));
        const refreshed = setup({ store: h.store });
        expectAppearance(refreshed, failedKey === PALETTE ? 'classic' : 'slate',
            failedKey === MODE ? 'dark' : 'light', failedKey === MODE ? 'dark' : 'light');
        h.store.failures.write.clear();
        h.controller.setMode('light');
        expectAppearance(h, 'slate', 'light', 'light');
        expectAppearance(setup({ store: h.store }), 'slate', 'light', 'light');
    });
}

test('denied reads and writes permit in-page selection and do not clobber it on storage events', () => {
    const store = storageFixture({ [PALETTE]: 'classic', [MODE]: 'dark' });
    for (const key of [MODE, PALETTE]) { store.failures.read.add(key); store.failures.write.add(key); }
    const h = setup({ store });
    expectAppearance(h, 'business', 'system', 'light', true);
    h.controller.setPalette('slate');
    h.controller.setMode('dark');
    expectAppearance(h, 'slate', 'dark', 'dark', true);
    const writeCount = store.writes.length;
    h.storageEvent(PALETTE);
    expectAppearance(h, 'slate', 'dark', 'dark', true);
    assert.equal(store.writes.length, writeCount);
    h.controller.reset();
    expectAppearance(h, 'business', 'system', 'light', true);
});

test('successful writes cannot claim persistence when read-back remains unavailable', () => {
    const store = storageFixture();
    for (const key of [MODE, PALETTE]) store.failures.read.add(key);
    const h = setup({ store });
    h.controller.setPalette('slate');
    h.controller.setMode('dark');
    expectAppearance(h, 'slate', 'dark', 'dark', true);
    assert.equal(store.data.get(PALETTE), 'slate');
    assert.equal(store.data.get(MODE), 'dark');
});

test('silently discarded writes retain the local choice but report persistence failure', () => {
    const store = storageFixture({ [PALETTE]: 'classic', [MODE]: 'light' });
    store.storage.setItem = (key, value) => store.writes.push([key, String(value)]);
    const h = setup({ store });
    h.controller.setPalette('slate');
    h.controller.setMode('dark');
    expectAppearance(h, 'slate', 'dark', 'dark', true);
    expectAppearance(setup({ store }), 'classic', 'light', 'light');
});

test('reset touches only the two appearance keys and remains refreshable', () => {
    const unrelated = {
        cow_language: 'en', cow_theme_id: 'desktop-theme', cow_current_agent: 'agent-a',
        auth_token: 'existing-test-token', cow_draft: 'unsent message', cow_tenant: 'tenant-a',
    };
    const h = setup({ initial: { ...unrelated, [PALETTE]: 'classic', [MODE]: 'dark' }, systemDark: true });
    h.controller.reset();
    expectAppearance(h, 'business', 'system', 'dark');
    assert.deepEqual(Object.fromEntries(h.store.data), { ...unrelated, [PALETTE]: 'business', [MODE]: 'system' });
    assert.deepEqual(new Set(h.store.writes.map(([key]) => key)), new Set([MODE, PALETTE]));
    expectAppearance(setup({ store: h.store, systemDark: true }), 'business', 'system', 'dark');
});

test('near-concurrent tabs converge to the final stored pair, ignoring stale event values and never writing back', () => {
    const store = storageFixture({ [PALETTE]: 'business', [MODE]: 'system' });
    const a = setup({ store }), b = setup({ store });
    a.controller.setPalette('slate');
    // B has not received A's events yet and writes its complete local choice.
    b.controller.setMode('dark');
    assert.equal(store.data.get(PALETTE), 'business');
    assert.equal(store.data.get(MODE), 'dark');
    const writes = store.writes.length;
    for (const h of [a, b]) {
        h.storageEvent(PALETTE, { oldValue: 'business', newValue: 'slate' });
        h.storageEvent(MODE, { oldValue: 'dark', newValue: 'system' });
        expectAppearance(h, 'business', 'dark', 'dark');
    }
    assert.equal(store.writes.length, writes);
});

test('remote removal of both keys and localStorage.clear events restore defaults without persisting', () => {
    const h = setup({ initial: { [PALETTE]: 'slate', [MODE]: 'dark', cow_language: 'en' } });
    h.store.data.delete(PALETTE);
    h.store.data.delete(MODE);
    h.storageEvent(PALETTE, { oldValue: 'slate', newValue: null });
    h.storageEvent(MODE, { oldValue: 'dark', newValue: null });
    expectAppearance(h, 'business', 'system', 'light');
    h.store.data.set(PALETTE, 'classic');
    h.store.data.set(MODE, 'dark');
    h.storageEvent(MODE);
    expectAppearance(h, 'classic', 'dark', 'dark');
    h.store.data.clear();
    h.storageEvent(null);
    expectAppearance(h, 'business', 'system', 'light');
    assert.deepEqual(h.store.writes, []);
});

test('unrelated keys and another storage area cannot overwrite current appearance', () => {
    const h = setup();
    h.store.data.set(PALETTE, 'slate');
    h.store.data.set(MODE, 'dark');
    h.storageEvent('cow_language');
    expectAppearance(h, 'business', 'system', 'light');
    h.storageEvent(MODE, { storageArea: storageFixture().storage });
    expectAppearance(h, 'business', 'system', 'light');
    h.storageEvent(MODE);
    expectAppearance(h, 'slate', 'dark', 'dark');
    assert.deepEqual(h.store.writes, []);
});

test('subscribers observe applied state, snapshots do not mutate state and disposal releases listeners', () => {
    const h = setup();
    const observed = [];
    const unsubscribe = h.controller.subscribe(state => {
        assert.equal(h.root.attrs['data-web-palette'], state.palette);
        assert.equal(h.root.style.colorScheme, state.resolved);
        observed.push(state);
    });
    const snapshot = h.controller.getState();
    snapshot.palette = 'classic';
    expectAppearance(h, 'business', 'system', 'light');
    h.controller.setPalette('slate');
    assert.equal(observed.at(-1).palette, 'slate');
    unsubscribe();
    const count = observed.length;
    h.controller.setMode('dark');
    assert.equal(observed.length, count);
    h.controller.destroy();
    assert.equal(h.events.listenerCount('storage'), 0);
    assert.equal(h.mediaEvents.listenerCount('change'), 0);
});

test('controller can initialize and change preferences without an optional root element', () => {
    const h = setup({ rootPresent: false });
    h.controller.setPalette('slate');
    h.controller.setMode('dark');
    assert.deepEqual(h.controller.getState(), { palette: 'slate', mode: 'dark', resolved: 'dark', storageFailed: false });
});

test('the browser entry initializes even when accessing window.localStorage itself throws', () => {
    const source = fs.readFileSync(path.join(__dirname, '../channel/web/static/js/appearance.js'), 'utf8');
    const h = setup();
    const browser = {
        document: { documentElement: h.root },
        addEventListener() {}, removeEventListener() {},
        get localStorage() { throw Error('Storage access denied'); },
    };
    vm.runInNewContext(source, { window: browser });
    assert.equal(browser.CowAppearance.getState().storageFailed, true);
    browser.CowAppearance.setPalette('classic');
    browser.CowAppearance.setMode('dark');
    assert.equal(h.root.attrs['data-web-palette'], 'classic');
    assert.equal(h.root.style.colorScheme, 'dark');
    assert.equal(browser.CowAppearance.getState().storageFailed, true);
});
