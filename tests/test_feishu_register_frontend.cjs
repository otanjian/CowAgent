// Register-session handle plumbing (P0, task 2.8).
//
// The Feishu one-click register flow is bound to the caller's identity by an
// opaque handle the server issues on GET. Every poll must present that handle:
// the server has no "whoever asks first" fallback, so a poll without it reads as
// "expired" forever and the QR modal would spin until the user gives up. These
// assertions are source-level because the flow needs the Feishu SDK to run.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(
    path.join(__dirname, '../channel/web/static/js/console.js'), 'utf8');

function functionBody(src, name) {
    const start = src.indexOf('function ' + name + '(');
    assert.ok(start >= 0, 'function ' + name + ' still exists');
    // Match braces from the first '{' after the parameter list.
    const open = src.indexOf('{', start);
    let depth = 0;
    let quote = null;
    for (let i = open; i < src.length; i++) {
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
        if (c === '{') depth++;
        else if (c === '}') {
            depth--;
            if (depth === 0) return src.slice(open, i + 1);
        }
    }
    throw new Error('unbalanced braces in ' + name);
}

test('the start call stores the handle the server issued', () => {
    const body = functionBody(source, 'startFeishuRegister');
    assert.match(body, /_feishuRegisterHandle\s*=\s*data\.handle/,
        'GET response handle is not kept for polling');
    assert.match(body, /if\s*\(!_feishuRegisterHandle\)/,
        'a session without a handle would be polled anyway');
});

test('the poll presents the handle instead of an anonymous request', () => {
    const body = functionBody(source, 'pollFeishuRegisterStatus');
    assert.match(body, /action:\s*'poll',\s*handle:\s*handle/,
        'poll body does not carry the session handle');
    assert.match(body, /if\s*\(!_feishuRegisterHandle\)\s*\{[\s\S]*?return;/,
        'polling continues even though there is no session to address');
});

test('terminal states release the handle', () => {
    const body = functionBody(source, 'pollFeishuRegisterStatus');
    // done / expired / denied / error each end the session; a stale handle would
    // let a later poll keep asking the server about a session that is over.
    for (const status of ["'done'", "'expired'", "'denied'", "'error'"]) {
        const branch = body.indexOf('rs === ' + status);
        assert.ok(branch >= 0, 'missing branch for ' + status);
        // Up to the next branch: the reset belongs to this one.
        const next = body.indexOf('} else if (rs ===', branch + 1);
        const tail = body.slice(branch, next === -1 ? body.length : next);
        assert.match(tail, /_feishuRegisterHandle\s*=\s*''/,
            'handle not released on ' + status);
    }
});

test('leaving the scan tab drops the handle', () => {
    const body = functionBody(source, 'switchFeishuMode');
    assert.match(body, /_feishuRegisterHandle\s*=\s*''/,
        'switching away from the scan tab keeps a live handle');
});
