'use strict';
// The forced password-change gate must be able to actually render.
//
// An account whose service-side `must_change_password` is set is walked into
// `_enterForcedPassword()`: that hides `#app` (the business UI must stay out of
// reach) and then shows `#account-password-modal` over the login overlay as a
// backdrop. Both can only be true at once if the modal is NOT a descendant of
// `#app` — a hidden ancestor collapses the modal to 0x0, so the operator sees a
// dead login page (password cleared, no error, no app) and reports that
// "clicking 登录 does nothing".
//
// These tests read the real chat.html because the defect is purely structural;
// the DOM stubs used by the other frontend tests have no layout, so they cannot
// observe a zero-sized element.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const HTML = fs.readFileSync(path.join(__dirname, '../channel/web/chat.html'), 'utf8');
const JS = fs.readFileSync(path.join(__dirname, '../channel/web/static/js/console.js'), 'utf8');

const VOID_TAGS = new Set([
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
    'link', 'meta', 'param', 'source', 'track', 'wbr',
]);

// Blank out comments, <script> and <style> bodies so inline JS or template
// strings cannot skew tag depth, while keeping every index aligned with the
// original source (same length in, same length out).
function blankNonMarkup(html) {
    return html
        .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, m => ' '.repeat(m.length))
        .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, m => ' '.repeat(m.length))
        .replace(/<!--[\s\S]*?-->/g, m => ' '.repeat(m.length));
}

const MARKUP = blankNonMarkup(HTML);

// [start, end) of an element: located either by its `id` attribute, or by tag
// name for elements that have no id (e.g. <body>). Ends are found by walking
// tag depth.
function elementRange(id, tagName) {
    let start;
    if (tagName) {
        const m = new RegExp('<' + tagName + '\\b', 'i').exec(MARKUP);
        if (!m) return null;
        start = m.index;
    } else {
        const idIdx = MARKUP.indexOf('id="' + id + '"');
        if (idIdx < 0) return null;
        start = MARKUP.lastIndexOf('<', idIdx);
    }
    const re = /<(\/?)([a-zA-Z][a-zA-Z0-9-]*)\b[^>]*?(\/?)>/g;
    re.lastIndex = start;
    let depth = 0;
    let m;
    while ((m = re.exec(MARKUP))) {
        const tag = m[2].toLowerCase();
        if (VOID_TAGS.has(tag) || m[3]) continue;
        if (m[1]) {
            depth -= 1;
            if (depth === 0) return { start, end: m.index + m[0].length };
        } else {
            depth += 1;
        }
    }
    return null;
}

function isInside(inner, outer) {
    return !!inner && !!outer && inner.start > outer.start && inner.start < outer.end;
}

test('the change-password modal is not nested inside #app', () => {
    const app = elementRange('app');
    const modal = elementRange('account-password-modal');
    assert.ok(app, '#app must exist');
    assert.ok(modal, '#account-password-modal must exist');
    assert.ok(!isInside(modal, app),
        '#account-password-modal must not live inside #app: _enterForcedPassword() hides #app, '
        + 'and a hidden ancestor collapses the modal to 0x0, leaving the gate invisible');
});

test('the login overlay and the forced gate are both outside #app', () => {
    const app = elementRange('app');
    const overlay = elementRange('login-overlay');
    const modal = elementRange('account-password-modal');
    assert.ok(app && overlay && modal, '#app, #login-overlay and the modal must all exist');
    assert.ok(!isInside(overlay, app), '#login-overlay must stay outside #app');
    assert.ok(!isInside(modal, app), '#account-password-modal must stay outside #app');
});

test('the change-password modal is still a body-level overlay', () => {
    const body = elementRange('body', 'body');
    const modal = elementRange('account-password-modal');
    assert.ok(body, '<body> must exist');
    assert.ok(modal, '#account-password-modal must exist');
    assert.ok(isInside(modal, body),
        'the modal must still be inside <body>; moving it out of #app must not drop it from the document');
});

test('the forced gate hides #app, which is why the modal has to sit outside it', () => {
    const start = JS.indexOf('function _enterForcedPassword()');
    assert.ok(start >= 0, '_enterForcedPassword() must exist');
    const fn = JS.slice(start, JS.indexOf('\n}', start));
    assert.match(fn, /_openForcedPasswordModal\(\)/,
        'the gate shows the change-password modal');
    assert.match(fn, /_accountHidden\('app',\s*true\)/,
        'the gate hides #app; the modal must therefore live outside #app, '
        + 'not rely on #app staying visible');
});
