// Memory target picker (task 5.1).
//
// The memory page reads three different domains through one interface: the
// caller's own user memory, a privately owned Agent's memory, and the tenant's
// shared Agent memory. Which of those a caller may address is a server
// decision — a member may *chat* with the tenant's shared Agent but may not
// manage its memory — so the picker must be built from what the server says,
// not from the console's own Agent catalogue. Before this change the picker was
// built from `agentCatalog` (a *use* range), which offered the shared Agent to
// a member and was then refused on read: the "clickable but refused" shape.
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

// A scalar top-level `const` (`const MEMORY_PERSONAL = 'personal';`), which the
// console declares once and every target function reads.
function scalarConst(name) {
    const match = source.match(new RegExp(`const ${name}\\s*=\\s*([^;]+);`));
    assert.ok(match, `Missing ${name}`);
    return `const ${name} = ${match[1]};`;
}

function boot({ targets = [], catalog = [], chosen = '', active = '', fallback = '' } = {}) {
    const sandbox = {
        console,
        currentLang: 'zh',
        I18N: { zh: { memory_target_personal: '我的记忆' }, en: {} },
        // The server's answer. Empty means "the server did not send a set", the
        // pre-5.1 backend (task 5.1).
        memoryTargets: targets,
        memoryAgentId: chosen,
        activeAgentId: active,
        defaultAgentId: fallback,
        agentCatalog: catalog,
    };
    sandbox.t = (key) => (sandbox.I18N[sandbox.currentLang] || {})[key] || key;
    const code = scalarConst('MEMORY_PERSONAL')
        + '\n' + ['viewingMemoryTarget', 'memoryTargetQuery', 'memoryTargetOptions']
            .map(fnSource).join('\n');
    vm.runInNewContext(code, sandbox);
    return sandbox;
}

const PERSONAL = { kind: 'personal', value: 'personal', scope: 'personal' };
const PRIVATE_TARGET = {
    kind: 'agent', value: 'mine', agent_id: 'mine', name: 'Mine',
    scope: 'private_agent', enabled: true,
};
const SHARED_TARGET = {
    kind: 'agent', value: 'shared', agent_id: 'shared', name: 'Shared',
    scope: 'shared', enabled: true,
};

test('the target list comes from the server, personal domain included', () => {
    // Only the server knows which domains this caller may address, so the
    // picker must not rebuild the list from the console's own catalogue.
    const sandbox = boot({
        targets: [PERSONAL, PRIVATE_TARGET, SHARED_TARGET],
        // A catalogue entry the server did NOT offer must not appear: for a
        // member that is exactly the shared Agent whose memory would be refused.
        catalog: [{ id: 'not-offered', name: 'Nope', enabled: true }],
    });
    // Cross-realm array: copy it into this realm before comparing.
    const values = Array.from(sandbox.memoryTargetOptions(), o => o.value);
    assert.deepEqual(values, ['personal', 'mine', 'shared']);
    assert.ok(!values.includes('not-offered'),
        'the picker invented a target the server did not offer');
});

test('the personal domain is a target, not an empty choice', () => {
    // The personal domain needs no Agent and no grant, and it is what a member
    // who owns no private Agent has to reach — so it must be selectable, and
    // selecting it must name it rather than mean "nothing chosen".
    const sandbox = boot({ targets: [PERSONAL, PRIVATE_TARGET], chosen: 'personal' });
    const values = Array.from(sandbox.memoryTargetOptions(), o => o.value);
    assert.ok(values.includes('personal'));
    assert.equal(sandbox.viewingMemoryTarget(), 'personal',
        "'' means nothing chosen, so the personal domain needs its own value");
    assert.equal(sandbox.memoryTargetQuery(), 'scope=personal');
});

test('choosing an Agent addresses that Agent', () => {
    const sandbox = boot({ targets: [PERSONAL, PRIVATE_TARGET], chosen: 'mine' });
    assert.equal(sandbox.viewingMemoryTarget(), 'mine');
    assert.equal(sandbox.memoryTargetQuery(), 'agent_id=mine');
});

test('an unset target falls back to the Agent the console is working with', () => {
    // The page is entered from an Agent's context, so an unset preference keeps
    // addressing that Agent rather than silently switching domain.
    const sandbox = boot({
        targets: [PERSONAL, PRIVATE_TARGET], chosen: '', active: 'mine', fallback: 'other',
    });
    assert.equal(sandbox.viewingMemoryTarget(), 'mine');
    assert.equal(sandbox.memoryTargetQuery(), 'agent_id=mine');
});

test('an unread or older backend still produces a usable picker', () => {
    // The catalogue fallback keeps a deployment that does not send a target set
    // working; an empty picker would read as "you may manage no memory at all".
    const sandbox = boot({
        targets: [],
        catalog: [{ id: 'mine', name: 'Mine', enabled: true }],
    });
    const values = Array.from(sandbox.memoryTargetOptions(), o => o.value);
    assert.deepEqual(values, ['personal', 'mine']);
});

test('a stopped Agent stays offered, because its memory is still stored', () => {
    // Stopping refuses new *traffic*, not access to what is already on disk. The
    // entry stays so its owner can read or clear it, and it keeps the server's
    // `enabled: false` rather than being dressed up as runnable.
    const stopped = Object.assign({}, PRIVATE_TARGET, { enabled: false });
    const sandbox = boot({ targets: [PERSONAL, stopped] });
    const values = Array.from(sandbox.memoryTargetOptions(), o => o.value);
    assert.ok(values.includes('mine'), 'a stopped Agent disappeared from the picker');
});
