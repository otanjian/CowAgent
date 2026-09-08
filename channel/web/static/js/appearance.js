/* Web appearance preferences. Loaded synchronously before first paint.
 * This module owns only cow_theme / cow_web_palette, never server settings.
 */
(function (host, factory) {
    'use strict';
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else {
        host.CowAppearance = api.createAppearanceController({
            get storage() { return host.localStorage; },
            root: host.document.documentElement,
            matchMedia: host.matchMedia && host.matchMedia.bind(host),
            addEventListener: host.addEventListener.bind(host),
            removeEventListener: host.removeEventListener.bind(host),
        });
    }
})(typeof window === 'undefined' ? globalThis : window, function () {
    'use strict';
    const MODE_KEY = 'cow_theme';
    const PALETTE_KEY = 'cow_web_palette';
    const MODES = ['light', 'dark', 'system'];
    const PALETTES = ['business', 'slate', 'classic'];

    function createAppearanceController(env) {
        const listeners = new Set();
        let media = null;
        let destroyed = false;
        try { media = env.matchMedia && env.matchMedia('(prefers-color-scheme: dark)'); } catch (_) {}

        function readPreferences() {
            let rawMode = null, rawPalette = null, failed = false;
            try { rawMode = env.storage.getItem(MODE_KEY); } catch (_) { failed = true; }
            try { rawPalette = env.storage.getItem(PALETTE_KEY); } catch (_) { failed = true; }
            return {
                mode: MODES.includes(rawMode) ? rawMode : 'system',
                palette: PALETTES.includes(rawPalette) ? rawPalette
                    : rawPalette === null && ['light', 'dark'].includes(rawMode) ? 'classic' : 'business',
                storageFailed: failed,
            };
        }

        let state = readPreferences();
        function getState() { return { ...state }; }
        function apply() {
            if (destroyed) return;
            state.resolved = state.mode === 'system' ? (media && media.matches ? 'dark' : 'light') : state.mode;
            const root = env.root;
            if (root) {
                root.setAttribute('data-web-palette', state.palette);
                root.classList.toggle('dark', state.resolved === 'dark');
                if (root.style) root.style.colorScheme = state.resolved;
            }
            listeners.forEach(listener => listener(getState()));
        }

        function persist() {
            // Save the complete selection on every explicit choice. In
            // particular, a migrated classic palette must survive a mode-only
            // edit. A partial write is reported rather than claimed atomic.
            let failed = false;
            try { env.storage.setItem(PALETTE_KEY, state.palette); } catch (_) { failed = true; }
            try { env.storage.setItem(MODE_KEY, state.mode); } catch (_) { failed = true; }
            // Successful writes alone do not guarantee restoration: reads can
            // be blocked independently, or a browser can discard the writes.
            try {
                if (env.storage.getItem(PALETTE_KEY) !== state.palette || env.storage.getItem(MODE_KEY) !== state.mode) failed = true;
            } catch (_) { failed = true; }
            state.storageFailed = failed;
        }

        function update(field, value, allowed) {
            if (destroyed || !allowed.includes(value)) return false;
            state[field] = value;
            persist();
            apply();
            return true;
        }
        function setPalette(value) { return update('palette', value, PALETTES); }
        function setMode(value) { return update('mode', value, MODES); }
        function reset() {
            if (destroyed) return;
            state.mode = 'system';
            state.palette = 'business';
            persist();
            apply();
        }
        function systemChanged() { if (state.mode === 'system') apply(); }
        function storageChanged(event) {
            if (destroyed || (event.key !== null && event.key !== MODE_KEY && event.key !== PALETTE_KEY)) return;
            try { if (event.storageArea && event.storageArea !== env.storage) return; } catch (_) { return; }
            const next = readPreferences();
            // A page without storage keeps its in-memory choice.
            if (next.storageFailed) { state.storageFailed = true; apply(); return; }
            state = next;
            apply();
        }
        function subscribe(listener) {
            if (!destroyed) listeners.add(listener);
            return () => listeners.delete(listener);
        }
        function destroy() {
            destroyed = true;
            listeners.clear();
            if (env.removeEventListener) env.removeEventListener('storage', storageChanged);
            if (media && media.removeEventListener) media.removeEventListener('change', systemChanged);
            else if (media && media.removeListener) media.removeListener(systemChanged);
        }
        if (env.addEventListener) env.addEventListener('storage', storageChanged);
        if (media && media.addEventListener) media.addEventListener('change', systemChanged);
        else if (media && media.addListener) media.addListener(systemChanged);
        apply();
        return { getState, setPalette, setMode, reset, subscribe, apply, destroy };
    }
    return { createAppearanceController };
});
