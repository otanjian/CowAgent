// Fork fragment mount points (change fork-decoupling-and-tenant-hardening, task 8.8).
//
// Fork-specific markup that used to live inline in chat.html is kept in small
// standalone fragments under channel/web/static/fragments/. chat.html keeps the
// mount element (so its id/classes/ARIA attributes are unchanged for JS and the
// contract tests) and declares the fragment via data-fork-fragment. This loader
// fetches each fragment once the document is parsed and injects it in place,
// then re-runs the i18n pass so data-i18n* attributes inside the fragment are
// localized exactly as before.
(function () {
    'use strict';

    function mount(el) {
        var src = el.getAttribute('data-fork-fragment');
        if (!src) return Promise.resolve();
        return fetch(src, { credentials: 'same-origin' })
            .then(function (res) { return res.ok ? res.text() : ''; })
            .then(function (html) {
                if (!html) return;
                el.innerHTML = html;
                el.removeAttribute('data-fork-fragment');
                // applyI18n() also calls renderAppearancePreferences(), so any
                // controls inside the fragment get their state applied.
                if (typeof window.applyI18n === 'function') window.applyI18n();
                document.dispatchEvent(new CustomEvent('fork-fragment-mounted', {
                    detail: { element: el, src: src }
                }));
            });
    }

    function mountAll(root) {
        var nodes = (root || document).querySelectorAll('[data-fork-fragment]');
        var jobs = [];
        for (var i = 0; i < nodes.length; i++) jobs.push(mount(nodes[i]));
        return Promise.all(jobs);
    }

    window.CowFragments = { mount: mount, mountAll: mountAll };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { mountAll(document); });
    } else {
        mountAll(document);
    }
})();
