// The overview KPI strip must not turn a failed read into a zero.
//
// ``_adminHomeFormatInt`` had ``Number(null) === 0`` as a live trap: with the
// server now reporting an unreadable region as ``null`` (task 3.5), the old
// formatter would have printed "0" — telling the operator their tenant is empty
// when in fact the source broke. These pin the two facts apart, and pin the
// per-region retry hint that goes with them.
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

const I18N = {
    zh: {
        admin_home_kpi_dash: '—',
        admin_home_kpi_agents: '智能体',
        admin_home_kpi_messages_today: '今日消息',
        admin_home_kpi_members: '成员数',
        admin_home_kpi_system: '系统状态',
        admin_home_kpi_system_ok: '正常运行',
        admin_home_kpi_system_bad: '异常',
        admin_home_kpi_unavailable: '部分数据暂时无法读取',
        admin_home_kpi_retry: '重试',
    },
};

function makeEl(id) {
    return {
        id,
        innerHTML: '',
        dataset: {},
        parentNode: { insertBefore() {} },
        remove() { this.removed = true; },
        nextSibling: null,
    };
}

function boot({ existing = [] } = {}) {
    const box = makeEl('admin-home-kpis');
    const elements = { 'admin-home-kpis': box };
    // The retry hint is created lazily and inserted beside the strip, so the
    // test needs the elements the render actually made.
    const created = [];
    const insertions = [];
    for (const el of existing) elements[el.id] = el;
    const sandbox = {
        console,
        currentLang: 'zh',
        I18N,
        document: {
            getElementById: (id) => elements[id] || null,
            createElement: (tag) => {
                const el = makeEl('');
                el.tagName = tag;
                created.push(el);
                return el;
            },
        },
        escapeHtml: (v) => String(v == null ? '' : v),
    };
    sandbox.t = (key) => (sandbox.I18N[sandbox.currentLang] || {})[key] || key;
    sandbox.created = created;
    sandbox.insertions = insertions;
    // ``_renderAdminHomeKpis`` inserts the note after the strip; record it so
    // the assertion can look at what was inserted rather than at the strip.
    box.parentNode = {
        insertBefore: (node) => { insertions.push(node); },
    };
    vm.runInNewContext(
        fnSource('_adminHomeFormatInt')
        + '\n' + fnSource('_adminHomeKpiUnavailable')
        + '\n' + fnSource('_renderAdminHomeKpis'),
        sandbox);
    return sandbox;
}

test('an unreadable figure is a dash, never a zero', () => {
    // ``Number(null)`` is 0, so this is the exact input the old code got wrong.
    const sandbox = boot();
    assert.equal(sandbox._adminHomeFormatInt(null), '—');
    assert.notEqual(sandbox._adminHomeFormatInt(null), '0');
    assert.equal(sandbox._adminHomeFormatInt(undefined), '—');
});

test('a real zero is still printed as zero', () => {
    // The two must stay distinguishable: this is the whole point.
    const sandbox = boot();
    assert.equal(sandbox._adminHomeFormatInt(0), '0');
    assert.equal(sandbox._adminHomeFormatInt(1234), (1234).toLocaleString());
});

test('a failed region reads as missing and offers a retry', () => {
    const sandbox = boot();
    sandbox._renderAdminHomeKpis(
        { agent_count: null, messages_today: 5, member_count: 3,
          system_status: 'degraded' },
        { unavailable: ['agent_count'], member_count_scope: 'tenant' });
    const html = sandbox.document.getElementById('admin-home-kpis').innerHTML;
    assert.match(html, /—/, 'the failed region must not print a number');
    assert.match(html, /今日消息/, 'the regions that did read stay usable');
    const note = sandbox.insertions[0];
    assert.ok(note, 'a failed region must add a note');
    assert.match(note.innerHTML, /data-admin-home-retry/,
        'a failed region must be retryable');
    assert.match(note.innerHTML, /部分数据暂时无法读取/);
    assert.equal(note.dataset.unavailable, 'agent_count',
        'the note names which region failed, not just that one did');
});

test('a member without a member count is not told the read failed', () => {
    // ``not_permitted`` is a member's normal state, not a failure: showing a
    // retry hint would promise a number that can never arrive.
    const sandbox = boot();
    sandbox._renderAdminHomeKpis(
        { agent_count: 1, messages_today: 0, member_count: null,
          system_status: 'ok' },
        { member_count_scope: 'not_permitted', unavailable: [] });
    const html = sandbox.document.getElementById('admin-home-kpis').innerHTML;
    assert.match(html, /成员数/);
    assert.match(html, /—/);
    assert.doesNotMatch(html, /data-admin-home-retry/);
});

test('the unavailable list is data, not prose', () => {
    const sandbox = boot();
    assert.equal(sandbox._adminHomeKpiUnavailable({ unavailable: [] }), false);
    assert.equal(sandbox._adminHomeKpiUnavailable({}), false);
    assert.equal(sandbox._adminHomeKpiUnavailable(null), false);
    assert.equal(sandbox._adminHomeKpiUnavailable({ unavailable: ['x'] }), true);
});
