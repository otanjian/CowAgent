// Task 5.5: the personal-parameter editor lives in the shared detail component,
// and every control it draws comes from the server's own answer (5.4/5.5).
//
// The spec forbids a standalone personal resource page because two surfaces for
// one configuration drift: the page would have to re-derive what the caller may
// configure, and the write would land somewhere other than the row that
// advertised it. So the component is only allowed to render what the row
// carries — ``personal`` (the caller's saved parameters plus the ``configure`` /
// ``clear`` verbs the request would be judged by) — and these tests pin that in
// both directions: a row with no state gets no editor, and a row whose verbs
// allow it gets exactly that editor.
//
// The write path is asserted on the request it builds, not on a status: the
// endpoint (``/api/tools`` vs ``/api/skills``), the ``action``, the resource id
// and — for the secret — the difference between "leave the saved one alone" and
// "replace it".
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(
    path.join(__dirname, '../channel/web/static/js/console.js'), 'utf8');

// The endpoint map is read from the source rather than restated here: the point
// of the assertions below is *which* endpoint the component writes to, and a
// copy of the map in the test would keep passing after the real one changed.
const ENDPOINTS_DECL = (() => {
    const match = source.match(/const RESOURCE_DETAIL_ENDPOINTS = \{[^}]*\};/);
    assert.ok(match, 'Missing RESOURCE_DETAIL_ENDPOINTS');
    return match[0];
})();

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

/** A stub for one element the component looks up by id or data attribute. */
function stubElement(html = '') {
    const el = {
        innerHTML: html,
        textContent: '',
        value: '',
        dataset: {},
        classes: new Set(),
        click: null,
        querySelector(selector) {
            return stubElement.htmlHas(this.innerHTML, selector)
                ? stubElement() : null;
        },
    };
    el.classList = {
        add: (name) => el.classes.add(name),
        remove: (name) => el.classes.delete(name),
        toggle: (name, on) => (on ? el.classes.add(name) : el.classes.delete(name)),
        contains: (name) => el.classes.has(name),
    };
    return el;
}

// The attribute behind a simple `[data-x]` selector, so a stub can answer
// `querySelector` from the markup the component just wrote.
stubElement.htmlHas = (html, selector) => {
    const attr = String(selector).replace(/^\[|\]$/g, '');
    return String(html || '').includes(attr);
};

function sandbox(overrides) {
    return Object.assign({
        console,
        currentLang: 'en',
        t: (key) => key,
        escapeHtml: (value) => String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;'),
        activeAgentId: 'agent-a',
        document: { getElementById: () => null, querySelector: () => null },
        fetch: async () => ({ ok: true, json: async () => ({ status: 'success' }) }),
    }, overrides || {});
}

function run(box, ...names) {
    vm.runInNewContext([ENDPOINTS_DECL].concat(names.map(fnSource)).join('\n'), box);
    return box;
}

function detailsDocument(elements) {
    return {
        getElementById: (id) => elements[id] || null,
        querySelector: (selector) => {
            for (const el of Object.values(elements)) {
                if (el && stubElement.htmlHas(el.innerHTML, selector)) return el;
            }
            return null;
        },
    };
}

function renderBox(row, kind = 'tool') {
    const body = stubElement();
    const box = sandbox({
        resourceDetailState: { kind, row },
        document: detailsDocument({ 'resource-detail-body': body }),
    });
    run(box, 'personalParamsSection', 'skillDetailActions', 'renderResourceDetail');
    box.renderResourceDetail();
    return { box, body };
}

// ---------- the editor is the server's answer, not the page's guess --------

test('a row with no personal state gets no parameter section at all', () => {
    const box = run(sandbox(), 'personalParamsSection');
    assert.equal(box.personalParamsSection({ resource_id: 'builtin:read', name: 'read' }),
        '', 'a row the server sent no state for must not grow an editor');
});

test('a configure verb renders the editor, a plain state does not', () => {
    const { body } = renderBox({
        resource_id: 'builtin:read', name: 'read', personal: {
            configured: true, params: { region: 'cn' }, actions:
                { configure: true, clear: true },
        },
    });
    assert.match(body.innerHTML, /data-resource-personal-params/);
    assert.match(body.innerHTML, /data-resource-personal-save/);
    assert.match(body.innerHTML, /data-resource-personal-clear/);
});

test('saved parameters without the configure verb are shown read-only', () => {
    // The grant was withdrawn (or the capability switched off) after a save.
    // What is already stored stays visible and removable — the *save* is what
    // the server would refuse, so the page must not offer it.
    const { body } = renderBox({
        resource_id: 'builtin:read', name: 'read', personal: {
            configured: true, params: { region: 'cn' }, actions:
                { configure: false, clear: true },
        },
    });
    assert.match(body.innerHTML, /resource_detail_personal_readonly/);
    assert.ok(!/data-resource-personal-params/.test(body.innerHTML),
        'a refused save must not be advertised as an editor');
    assert.ok(!/data-resource-personal-save/.test(body.innerHTML));
    assert.match(body.innerHTML, /&quot;region&quot;|region/,
        'the stored parameters stay visible');
    assert.match(body.innerHTML, /data-resource-personal-clear/,
        'withdrawing a saved secret stays possible');
});

test('a saved secret is never echoed back into the form', () => {
    const box = run(sandbox(), 'personalParamsSection');
    const withSecret = box.personalParamsSection({
        personal: { configured: true, params: {}, has_credential: true,
                    actions: { configure: true, clear: false } },
    });
    const withoutSecret = box.personalParamsSection({
        personal: { configured: true, params: {}, has_credential: false,
                    actions: { configure: true, clear: false } },
    });
    assert.match(withSecret, /resource_detail_personal_secret_saved/);
    assert.match(withoutSecret, /resource_detail_personal_secret(?!_saved)/);
    assert.ok(!/type="text"[^>]*data-resource-personal-secret/.test(withSecret),
        'the secret input must not become a readable field');
});

// ---------- the shared detail draws the skill switch from the server -------

test('the skill switch appears only where the server allowed it', () => {
    const allowed = renderBox({
        resource_id: 'custom:demo', name: 'demo', enabled: false,
        actions: { enable: true }, personal: null,
    }, 'skill');
    assert.match(allowed.body.innerHTML, /data-resource-detail-toggle/);

    const managed = renderBox({
        resource_id: 'builtin:demo', name: 'demo', enabled: true,
        actions: { enable: false }, personal: null,
    }, 'skill');
    assert.match(managed.body.innerHTML, /skill_global_toggle_managed/);
    assert.ok(!/data-resource-detail-toggle/.test(managed.body.innerHTML),
        'a refused toggle must not be drawn as a control');
});

// ---------- the write path -------------------------------------------------

function writeBox(kind, row, answer) {
    const calls = [];
    const status = stubElement();
    status.classes.add('hidden');
    const paramsEl = { value: '{"region":"cn"}' };
    const secretEl = { value: '' };
    const box = sandbox({
        resourceDetailState: { kind, row },
        fetch: async (url, options) => {
            calls.push({ url, options });
            return { ok: answer.ok !== false,
                     json: async () => answer.body };
        },
        document: {
            querySelector: (selector) => {
                if (selector === '[data-resource-personal-params]') return paramsEl;
                if (selector === '[data-resource-personal-secret]') return secretEl;
                if (selector === '[data-resource-personal-status]') return status;
                return null;
            },
        },
        renderResourceDetail: () => {},
        loadSkillsSection: () => {},
    });
    run(box, 'personalParamsSection', 'savePersonalParamsFromDetail',
        'clearPersonalParamsFromDetail', '_personalDetailStatus',
        '_personalDetailRequest', '_applyPersonalConfig');
    box.__params = paramsEl;
    box.__secret = secretEl;
    box.__status = status;
    return { box, calls };
}

const ROW = { resource_id: 'custom:demo', name: 'demo', personal: {
    configured: false, params: {}, actions: { configure: true, clear: false } } };

test('a save posts the caller-scoped verb to the row\'s own endpoint', async () => {
    const { box, calls } = writeBox('tool', Object.assign({}, ROW),
                                    { body: { status: 'success', config: { resource_id: 'custom:demo' } } });
    await box.savePersonalParamsFromDetail();

    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, '/api/tools');
    const body = JSON.parse(calls[0].options.body);
    assert.equal(body.action, 'save-personal');
    assert.equal(body.resource_id, 'custom:demo');
    assert.deepEqual(body.params, { region: 'cn' });
    assert.ok(!('secret' in body),
        'an empty secret field means "leave the saved one alone"');
    assert.ok(!('owner' in body),
        'the owner comes from the session, never from the page');
});

test('a typed secret is sent, and a skill names its anchor Agent', async () => {
    const { box, calls } = writeBox('skill', Object.assign({}, ROW, {
        resource_id: 'custom:writer' },
    ), { body: { status: 'success', config: { resource_id: 'custom:writer' } } });
    box.__secret.value = 'sk-live-123';
    await box.savePersonalParamsFromDetail();

    assert.equal(calls[0].url, '/api/skills');
    const body = JSON.parse(calls[0].options.body);
    assert.equal(body.secret, 'sk-live-123');
    assert.equal(body.agent_id, 'agent-a',
        'the skill library is the anchor Agent\'s, not whichever one is implied');
});

test('a refusal is reported as the server stated it and changes nothing', async () => {
    const row = Object.assign({}, ROW);
    const { box } = writeBox('tool', row, {
        ok: false,
        body: { status: 'error', code: 'skill_name_ambiguous',
                message: "skill name 'demo' is ambiguous; pass resource_id" },
    });
    await box.savePersonalParamsFromDetail();

    assert.match(box.__status.textContent, /ambiguous/,
        'the server\'s own message is what the member sees');
    assert.ok(box.__status.classes.has('text-red-500'));
    assert.equal(row.personal.configured, false,
        'a refused save must not look like it landed');
});

test('unparsable parameters never reach the wire', async () => {
    const { box, calls } = writeBox('tool', Object.assign({}, ROW),
                                    { body: { status: 'success' } });
    box.__params.value = '{not json';
    await box.savePersonalParamsFromDetail();

    assert.equal(calls.length, 0, 'the request must not be sent at all');
    assert.equal(box.__status.textContent, 'resource_detail_personal_invalid_json');
});
