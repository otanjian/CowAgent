'use strict';
// A wrong "recent password" must not look like an expired session.
//
// ``_require_recent_password`` refuses a channel/agent write with HTTP 401 and
// body code ``invalid_old`` when the password the operator typed is wrong. The
// session is fine; only that one factor check failed. The global fetch wrapper
// used to treat *every* same-origin non-/auth/ 401 as "your session is gone" and
// call ``showLoginScreen()``, so saving a channel with a mistyped password threw
// the operator out to the login page — and, because the form's stored password
// is reused, every later retry bounced again.
//
// These tests run the real wrapper against real ``Response`` objects so the
// interceptor's body handling (it must not consume what the caller still needs)
// is exercised rather than mocked away.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const JS = fs.readFileSync(path.join(__dirname, '../channel/web/static/js/console.js'), 'utf8');

function section(start, end) {
    const from = JS.indexOf(start);
    assert.ok(from >= 0, `missing section start: ${start}`);
    const to = JS.indexOf(end, from + start.length);
    assert.ok(to > from, `missing section end: ${end}`);
    return JS.slice(from, to);
}

// Load the shipped global fetch wrapper into a minimal window. ``transport``
// stands in for the network; everything inside the wrapper is the real code.
function harness(transport) {
    const seen = { logins: 0 };
    const href = 'http://console.test/console';
    const ctx = {
        URL,
        console,
        // ``origin`` is read by the wrapper's same-origin guard, so the stub has
        // to carry it: a location with only ``href`` would make every 401 look
        // cross-origin and the test would pass without exercising anything.
        location: { href, origin: new URL(href).origin },
        showLoginScreen() { seen.logins += 1; },
        fetch(url, options) { return Promise.resolve(transport(url, options)); },
    };
    ctx.window = ctx;
    ctx._authEpoch = 7;
    ctx._accountState = { phase: 'ready' };
    ctx._accountWritePending = null;
    vm.createContext(ctx);
    vm.runInContext(
        section('// Only a 401 from the current identity', 'function initApp()'), ctx);
    return { ctx, seen };
}

const CHANNEL_URL = '/api/tenant/channels/chan_1';

function errorResponse(status, body) {
    return new Response(body === undefined ? '' : JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
    });
}

test('a wrong recent password (401 invalid_old) does not show the login screen', async () => {
    const { ctx, seen } = harness(() => errorResponse(401, {
        status: 'error',
        message: 'recent password required',
        code: 'invalid_old',
    }));

    await ctx.fetch(CHANNEL_URL, { method: 'POST' });

    assert.equal(seen.logins, 0,
        'invalid_old means the typed password was wrong, not that the session died: '
        + 'the operator must stay on the form and see the error inline');
});

test('the caller can still read the body the interceptor inspected', async () => {
    const { ctx } = harness(() => errorResponse(401, {
        status: 'error',
        message: 'recent password required',
        code: 'invalid_old',
    }));

    const response = await ctx.fetch(CHANNEL_URL, { method: 'POST' });
    const body = await response.json();

    assert.equal(body.code, 'invalid_old',
        'the interceptor must peek at a clone; consuming the body would leave the '
        + 'write path unable to report the reason it failed');
});

test('a genuinely expired session (401 unauthorized) still shows the login screen', async () => {
    const { ctx, seen } = harness(() => errorResponse(401, {
        status: 'error',
        message: 'unauthorized',
        code: 'unauthorized',
    }));

    await ctx.fetch(CHANNEL_URL, { method: 'POST' });

    assert.equal(seen.logins, 1,
        'only invalid_old is exempt; a dead session must keep returning the user to login');
});

test('a 401 with no parseable body still shows the login screen', async () => {
    const { ctx, seen } = harness(() => new Response('not json', { status: 401 }));

    await ctx.fetch(CHANNEL_URL, { method: 'POST' });

    assert.equal(seen.logins, 1,
        'an unreadable 401 body cannot prove the session is alive, so it keeps the '
        + 'conservative behaviour of returning to login');
});

test('non-401 responses are left alone', async () => {
    const { ctx, seen } = harness(() => errorResponse(409, {
        status: 'error',
        message: 'version conflict',
        code: 'conflict',
    }));

    const response = await ctx.fetch(CHANNEL_URL, { method: 'POST' });

    assert.equal(seen.logins, 0, 'only 401 is the interceptor\'s business');
    assert.equal(response.status, 409);
});

test('a 401 from /auth/ is not intercepted', async () => {
    const { ctx, seen } = harness(() => errorResponse(401, {
        status: 'error',
        message: 'invalid old password',
        code: 'invalid_old',
    }));

    await ctx.fetch('/auth/password', { method: 'POST' });

    assert.equal(seen.logins, 0,
        'auth endpoints own their 401 handling; the wrapper must keep skipping them');
});
