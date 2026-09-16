// The message-channel type picker must not offer a type the server will refuse
// (change unify-console-by-data-scope, task 7.7).
//
// The server's `channel_types` list serves two masters: it is the candidate set
// for a *new* connection, and it is the field contract the form reads for an
// instance that already exists. A type whose adapter cannot stamp the sender is
// no longer a candidate — its create is refused — but its entry must stay in the
// list so an existing row still renders its fields. The console narrows the
// picker on the server's separate `inbound_admissible` verdict; the contract
// lookup keeps reading the full list.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../channel/web/static/js/console.js'), 'utf8');

// Extract one top-level function by brace matching; the channels section sits
// among unrelated helpers, so whole-script evaluation is not viable.
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

function boot({ types = [], selfScope = false } = {}) {
    const sandbox = { tenantChannelTypes: types, tenantChannelSelfScope: selfScope };
    vm.runInNewContext(
        [fnSource('tenantChannelType'), fnSource('tenantChannelTypeChoices')].join('\n'),
        sandbox);
    return sandbox;
}

const FEISHU = {
    channel_type: 'feishu',
    inbound_admissible: true,
    label: { zh: '飞书', en: 'Feishu' },
    credential_fields: [{ key: 'feishu_app_id' }],
};
const SLACK = {
    channel_type: 'slack',
    inbound_admissible: false,
    label: { zh: 'Slack', en: 'Slack' },
    credential_fields: [{ key: 'slack_bot_token' }, { key: 'slack_app_token' }],
};

test('the shared-surface picker does not offer a type the create would refuse', () => {
    const sandbox = boot({ types: [FEISHU, SLACK] });
    assert.deepEqual(
        sandbox.tenantChannelTypeChoices().map(t => t.channel_type), ['feishu']);
});

test("a member's own picker still narrows on the readiness verdict", () => {
    // Task 6.1 behaviour, unchanged: the own surface keys on `ready`, which the
    // personal catalogue already computes with the same predicate.
    const ready = { ...FEISHU, ready: true };
    const unready = { ...SLACK, ready: false };
    assert.deepEqual(
        boot({ types: [ready, unready], selfScope: true })
            .tenantChannelTypeChoices().map(t => t.channel_type),
        ['feishu']);
    // Control: the readiness flag really is what the own surface reads.
    assert.deepEqual(
        boot({ types: [{ ...ready, ready: false }, unready], selfScope: true })
            .tenantChannelTypeChoices().map(t => t.channel_type),
        []);
});

test("an existing row's field contract survives the narrowing", () => {
    // The edit form looks the type up in the *full* list on purpose: dropping
    // the entry would render an existing Slack row with no fields at all.
    const sandbox = boot({ types: [FEISHU, SLACK] });
    assert.equal(sandbox.tenantChannelTypeChoices().length, 1);
    const contract = sandbox.tenantChannelType('slack');
    assert.ok(contract, 'the contract of an existing row must still resolve');
    assert.deepEqual(contract.credential_fields.map(f => f.key),
        ['slack_bot_token', 'slack_app_token']);
});

test('a payload without the verdict is not narrowed by it', () => {
    // An older deployment that does not send the field keeps its picker; only an
    // explicit `false` withdraws a type.
    const sandbox = boot({ types: [FEISHU, { ...SLACK, inbound_admissible: undefined }] });
    assert.deepEqual(
        sandbox.tenantChannelTypeChoices().map(t => t.channel_type),
        ['feishu', 'slack']);
});
