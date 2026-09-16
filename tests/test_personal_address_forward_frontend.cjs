// Retired personal addresses forward to the shared pages (task 8.1).
//
// The five personal pages were removed from the account menu (tasks 3.3/3.4) —
// a member's own objects are managed on the formal pages now, with the range
// decided server-side by `auth.object_scope`. A bookmark or a pasted
// `#view-personal-*` link still exists in the wild, though, and a retired page
// left reachable is a second surface with its own rules: exactly what the
// consolidation exists to remove.
//
// What this file locks:
//   * every retired personal id forwards, and to the page that carries the same
//     objects — a personal id nobody decided about would silently reopen the
//     retired surface through a bookmark;
//   * the forward happens BEFORE the availability and leave gates, so the
//     destination is judged by the same verdicts it would get on its own. A
//     forward that ran after the gate, or that skipped it, would be an exemption
//     — an old bookmark as a way around a denial, or as a way to discard an
//     unsaved draft without asking.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(
    path.join(__dirname, '../channel/web/static/js/console.js'), 'utf8');

function fnSource(name) {
    const head = `function ${name}(`;
    const from = source.indexOf(head);
    assert.ok(from >= 0, `Missing ${name}`);
    let depth = 0;
    for (let i = source.indexOf('{', from); i < source.length; i++) {
        if (source[i] === '{') depth++;
        else if (source[i] === '}') {
            depth--;
            if (depth === 0) return source.slice(from, i + 1);
        }
    }
    throw new Error(`Unbalanced ${name}`);
}

function objectLiteral(name) {
    const from = source.indexOf(`const ${name} = {`);
    assert.ok(from >= 0, `Missing ${name}`);
    let depth = 0;
    for (let i = source.indexOf('{', from); i < source.length; i++) {
        if (source[i] === '{') depth++;
        else if (source[i] === '}') {
            depth--;
            if (depth === 0) return source.slice(from, i + 1);
        }
    }
    throw new Error(`Unbalanced ${name}`);
}

function boot() {
    // Top-level `const` in a vm context is a lexical binding, not a property of
    // the sandbox, so the tables are returned by value instead of being read off
    // `sandbox`.
    const code = [objectLiteral('VIEW_META') + ';',
                  objectLiteral('LEGACY_PERSONAL_FORWARD') + ';',
                  fnSource('legacyPersonalForward')].join('\n')
        + '\n({ VIEW_META: VIEW_META, LEGACY_PERSONAL_FORWARD: LEGACY_PERSONAL_FORWARD,'
        + ' legacyPersonalForward: legacyPersonalForward })';
    return vm.runInNewContext(code, {});
}

test('every retired personal page declares where it forwards', () => {
    // Membership is derived from VIEW_META rather than hard-coded, so a personal
    // id added later cannot quietly skip the decision.
    const sandbox = boot();
    const declared = Array.from(Object.keys(sandbox.VIEW_META))
        .filter(id => id.indexOf('personal-') === 0).sort();
    assert.deepEqual(declared,
        Array.from(Object.keys(sandbox.LEGACY_PERSONAL_FORWARD)).sort());
    for (const id of declared) {
        assert.ok(sandbox.legacyPersonalForward(id), `${id} has no destination`);
    }
});

test('a retired address forwards to the page carrying the same objects', () => {
    const sandbox = boot();
    assert.equal(sandbox.legacyPersonalForward('personal-agents'), 'agents');
    assert.equal(sandbox.legacyPersonalForward('personal-channels'), 'channels');
    assert.equal(sandbox.legacyPersonalForward('personal-memory'), 'memory');
    // 工具 and 技能 are one page on the consolidated console (admin.skills).
    assert.equal(sandbox.legacyPersonalForward('personal-tools'), 'skills');
    assert.equal(sandbox.legacyPersonalForward('personal-skills'), 'skills');
});

test('a forward never names a view the console does not have', () => {
    // Resolving through VIEW_META is what keeps the map from inventing a target;
    // a silent `navigateTo` miss would leave the user on the old page instead.
    const sandbox = boot();
    for (const id of Array.from(Object.keys(sandbox.LEGACY_PERSONAL_FORWARD))) {
        const target = sandbox.legacyPersonalForward(id);
        assert.ok(sandbox.VIEW_META[target], `${id} forwards to unknown view ${target}`);
    }
});

test('an ordinary view is left alone', () => {
    const sandbox = boot();
    assert.equal(sandbox.legacyPersonalForward('memory'), '');
    assert.equal(sandbox.legacyPersonalForward('chat'), '');
    assert.equal(sandbox.legacyPersonalForward(''), '');
});

test('the forward runs before the availability gate, not around it', () => {
    // "受权转接" means the destination is authorised like any other navigation.
    // Placing the forward after the gate would judge the *retired* page and then
    // open the destination unchecked; placing it before means the destination is
    // the thing gated. Source order is the only thing that can express this, so
    // it is asserted directly rather than inferred.
    const body = fnSource('navigateTo');
    const forward = body.indexOf('legacyPersonalForward(viewId)');
    const gate = body.indexOf('_viewNavDenied(viewId)');
    assert.ok(forward >= 0, 'navigateTo must consult the legacy forward');
    assert.ok(gate >= 0, 'navigateTo must keep the availability gate');
    assert.ok(forward < gate, 'the forward must be applied before the gate judges the view');

    const leave = body.indexOf('_viewLeaveCheck(viewId)');
    assert.ok(leave >= 0, 'navigateTo must keep the leave check');
    assert.ok(forward < leave, 'an unsaved draft must still be asked about, on the destination');
});

test('the forward carries no retired-module hook and drops nothing in flight', () => {
    // The forward used to invalidate the personal module's in-flight loads
    // (``PersonalConsole.invalidatePersonalViews``). The module is retired
    // (task 8.8), so the hook is gone from ``navigateTo`` *and* from the shell:
    // a leftover call would be a ReferenceError on the very path a bookmark
    // takes. There is nothing left to invalidate, because no module registers a
    // personal view any more — which is asserted here on the shell, not assumed.
    const body = fnSource('navigateTo');
    assert.ok(body.indexOf('invalidatePersonalViews') < 0,
        'navigateTo must not call a retired module hook');
    assert.ok(body.indexOf('PersonalConsole') < 0,
        'navigateTo must not reach for the retired module');
    assert.ok(!source.includes('invalidatePersonalViews'),
        'console.js must not carry a hook into the retired module');
    // The guard the retired module published must not be consulted either: it
    // decided whether a personal page had unsaved edits, and the pages are gone.
    assert.ok(source.indexOf('__personalConsoleDirtyGuard__') < 0,
        'console.js must not consult the retired module dirty guard');
    const identityAdmin = fs.readFileSync(
        path.join(__dirname, '../channel/web/static/js/identity-admin.js'), 'utf8');
    assert.ok(identityAdmin.indexOf('PersonalConsole') < 0,
        'identity-admin.js must not reach for the retired module');
    // The forward still happens before the gates (asserted above) — retiring the
    // hook must not have moved it.
    assert.ok(body.indexOf('legacyPersonalForward(viewId)') <
        body.indexOf('_viewNavDenied(viewId)'));
});
