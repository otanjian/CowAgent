// The 工具与技能 page must not offer the global enable/disable to a caller who
// may not move it.
//
// The switch is rendered from the server's per-row `actions` flag
// (`_annotate_skill_actions`), which is the write path's own rule: the global
// state decides what every member and Agent reads, so it needs the management
// qualification on top of the per-resource `enable` grant. Before this, the
// switch was written into the card unconditionally and a member's click was
// simply refused — a control advertising a request the server rejects.
//
// The state itself stays visible when the control is not: reading that a skill
// is enabled is not the action, and hiding it would make the page less honest
// than the refusal.
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

/** Render one card through the real function and return its markup. */
function render(skill) {
    const card = {
        innerHTML: '',
        querySelector: () => null,
    };
    const sandbox = {
        console,
        currentLang: 'en',
        // The real keys are asserted for existence by the i18n parity test; here
        // the key name is the value so the markup says which key it used.
        t: (key) => key,
        escapeHtml: (value) => String(value == null ? '' : value),
        CSS: { escape: (value) => String(value) },
        document: { querySelector: () => null },
        fetch: async () => ({ json: async () => ({}) }),
    };
    vm.runInNewContext(fnSource('renderSkillCard'), sandbox);
    sandbox.renderSkillCard(card, skill);
    return card.innerHTML;
}

const SKILL = { name: 'tenant-note', display_name: 'Tenant note',
                description: 'A tenant authored skill.', enabled: true };

test('a caller who may move the global state gets the switch', () => {
    const html = render({ ...SKILL, actions: { enable: true } });
    assert.match(html, /data-skill-switch/);
    assert.match(html, /role="switch"/);
    assert.ok(!/data-skill-state/.test(html));
});

test('a caller who may not gets the state, not the control', () => {
    const html = render({ ...SKILL, actions: { enable: false } });
    assert.ok(!/data-skill-switch/.test(html),
        'no switch may be rendered where the request would be refused');
    assert.ok(!/role="switch"/.test(html), 'not even a disabled-looking switch');
    assert.match(html, /data-skill-state/,
        'the current state must still be visible');
    assert.match(html, /skill_global_toggle_managed/,
        'the state must say why it cannot be moved');
});

test('the state is reported honestly in both directions', () => {
    const on = render({ ...SKILL, enabled: true, actions: { enable: false } });
    const off = render({ ...SKILL, enabled: false, actions: { enable: false } });
    assert.match(on, />skill_enable</);
    assert.match(off, />skill_disable</);
});

test('a payload that carries no actions flag still renders the control', () => {
    // Older/legacy responses have no flag; defaulting to hidden would silently
    // remove a working control in single-install mode, where every gate in
    // `web_channel` is unrestricted.
    const html = render({ ...SKILL });
    assert.match(html, /data-skill-switch/);
});
