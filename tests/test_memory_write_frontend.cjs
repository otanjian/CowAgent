// Memory page writes (task 5.1, second half).
//
// The page used to edit an Agent's memory entry through the *workspace file*
// API: `docReadFile(doc.relPath)` / `docWriteFile(doc.relPath, ...)`. That path
// knows nothing about memory, so an edit skipped the version condition and the
// index publish — a save could silently overwrite a newer revision, and the new
// body never reached retrieval. These assertions pin the three things the
// switch has to get right, none of which a status-only test would catch:
//
//   1. the revision the server handed out is the one sent back (the version
//      condition only works if the token round-trips);
//   2. the memory API's `stale_revision` becomes the editor's `conflict`, so the
//      existing "someone else changed it — overwrite?" flow runs;
//   3. an intentional overwrite re-reads the current revision instead of sending
//      none, because the server refuses a blind write of an existing entry.
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
    // Keep the `async` that precedes the keyword: slicing from `function`
    // would extract a body whose `await` is a syntax error.
    const start = source.slice(from - 6, from) === 'async ' ? from - 6 : from;
    let depth = 0;
    for (let i = source.indexOf('{', from); i < source.length; i++) {
        if (source[i] === '{') depth++;
        else if (source[i] === '}') {
            depth--;
            if (depth === 0) return source.slice(start, i + 1);
        }
    }
    throw new Error(`Unbalanced ${name}`);
}

/** A top-level `const NAME = <call>({ ... });` declaration, braces balanced. */
function constBlock(name) {
    const head = `const ${name} = `;
    const from = source.indexOf(head);
    assert.ok(from >= 0, `Missing ${name}`);
    let depth = 0;
    let started = false;
    for (let i = from; i < source.length; i++) {
        if (source[i] === '{') { depth++; started = true; }
        else if (source[i] === '}') {
            depth--;
            if (started && depth === 0) {
                let end = i + 1;
                while (end < source.length && source[end] !== ';') end++;
                return source.slice(from, end + 1);
            }
        }
    }
    throw new Error(`Unbalanced ${name}`);
}

function scalarConst(name) {
    const match = source.match(new RegExp(`const ${name}\\s*=\\s*([^;]+);`));
    assert.ok(match, `Missing ${name}`);
    return `const ${name} = ${match[1]};`;
}

/** A minimal DOM node recording the class toggles the buttons are given. */
function fakeButton() {
    return { classes: new Set(['hidden']), classList: null };
}
function makeNode() {
    const node = { classes: new Set(['hidden']) };
    node.classList = {
        toggle(cls, on) { if (on) node.classes.add(cls); else node.classes.delete(cls); },
        add(cls) { node.classes.add(cls); },
        remove(cls) { node.classes.delete(cls); },
    };
    return node;
}

/**
 * A sandbox holding the memory write wiring plus recording doubles.
 *
 * `responses` scripts `fetch`: each call shifts the next entry, which is either
 * a payload or a function of the request (used to answer the re-read an
 * intentional overwrite performs).
 */
function boot({ responses = [], doc = null, target = 'agent-1', category = 'memory' } = {}) {
    const calls = [];
    const toasts = [];
    const refusals = [];
    const confirms = [];
    const nodes = {
        'memory-btn-delete': makeNode(),
        'memory-btn-clear': makeNode(),
    };
    const sandbox = {
        console,
        _wsToast: (msg) => toasts.push(msg),
        _memoryRefusal: (data) => { refusals.push(data); return true; },
        t: (key) => key,
        MEMORY_PERSONAL: 'personal',
        memoryCategory: category,
        viewingMemoryTarget: () => target,
        memoryEditor: {
            current: () => doc,
            guard: () => true,
        },
        showConfirmDialog: (opts) => { confirms.push(opts); opts.onConfirm?.(); },
        closeMemoryViewer: () => calls.push({ url: 'closeMemoryViewer' }),
        document: { getElementById: (id) => nodes[id] || null },
        fetch: async (url, opts) => {
            const body = opts && opts.body ? JSON.parse(opts.body) : null;
            calls.push({ url, method: (opts && opts.method) || 'GET', body });
            const next = responses.shift();
            const payload = typeof next === 'function' ? next(calls[calls.length - 1]) : next;
            return { json: async () => payload };
        },
    };
    const code = [
        scalarConst('MEMORY_PERSONAL'),
        'function memoryTargetQuery() { return viewingMemoryTarget() === MEMORY_PERSONAL'
            + " ? 'scope=personal' : `agent_id=${encodeURIComponent(viewingMemoryTarget() || '')}`; }",
        'memoryTargetBody', 'memoryEntryEditable', 'memoryDocRead', 'memoryDocWrite',
        'memoryRequest', 'memorySucceeded', 'memoryReportMutation',
        'memorySyncDocButtons', 'memoryDocDelete', 'memoryDocClear',
    ].map((name) => (name.includes(' ') || name.startsWith('function')
        ? name : fnSource(name))).join('\n');
    vm.runInNewContext(code, sandbox);
    return { sandbox, calls, toasts, refusals, confirms, nodes };
}

const ENTRY = { filename: 'notes.md', category: 'memory' };

test('the read hands the editor the revision as its version token', async () => {
    const { sandbox, calls } = boot({
        responses: [{ status: 'success', content: 'BODY\n', revision: 'rev-1',
                      actions: { edit: true, delete: true }, read_only: false }],
    });
    const data = await sandbox.memoryDocRead(ENTRY);
    assert.equal(data.content, 'BODY\n');
    assert.equal(data.mtime, 'rev-1', 'the revision must round-trip as mtime');
    assert.equal(data.editable, true);
    assert.match(calls[0].url, /\/api\/memory\/content\?filename=notes\.md/);
    assert.match(calls[0].url, /agent_id=agent-1/);
});

test('a read-only category is not editable, so no edit is offered', async () => {
    const { sandbox } = boot({
        responses: [{ status: 'success', content: 'DREAM\n', revision: 'r',
                      actions: { edit: false, delete: false }, read_only: true }],
    });
    const data = await sandbox.memoryDocRead({ filename: '2026-01-01.md',
                                               category: 'dream' });
    assert.equal(data.editable, false);
});

test('a range the write would refuse is not editable either', async () => {
    // The second, independent reason: an Agent memory root is the tenant's
    // shared root, so a member may read the entry but not rewrite it. The
    // category here is an ordinary one and `read_only` is false, so only
    // `actions.edit` can carry the answer — which is what this pins.
    const { sandbox } = boot({
        responses: [{ status: 'success', content: 'BODY\n', revision: 'r',
                      actions: { edit: false, delete: false }, read_only: false }],
    });
    const data = await sandbox.memoryDocRead(ENTRY);
    assert.equal(data.content, 'BODY\n', 'the read itself is still served');
    assert.equal(data.editable, false,
        'no edit is offered where the write would be refused');
});

test('a save sends back the revision it was given', async () => {
    const { sandbox, calls } = boot({
        responses: [{ status: 'success', result: { revision: 'rev-2' } }],
    });
    const data = await sandbox.memoryDocWrite(ENTRY, 'NEW\n', 'rev-1');
    assert.equal(calls[0].method, 'POST');
    assert.match(calls[0].url, /\/api\/memory\/save$/);
    assert.deepEqual(calls[0].body, {
        filename: 'notes.md', category: 'memory', content: 'NEW\n',
        revision: 'rev-1', agent_id: 'agent-1',
    });
    assert.equal(data.status, 'success');
    assert.equal(data.mtime, 'rev-2', 'the new revision replaces the old one');
});

test('a stale revision becomes the editor conflict the page already handles', async () => {
    const { sandbox } = boot({
        responses: [{ status: 'error', code: 'stale_revision' }],
    });
    const data = await sandbox.memoryDocWrite(ENTRY, 'MINE\n', 'rev-old');
    assert.equal(data.code, 'conflict',
        'the overwrite flow only runs for the code the editor knows');
});

test('an intentional overwrite re-reads the current revision', async () => {
    // `force` in the editor passes a null token. The server refuses a blind
    // write of an existing entry (that refusal is what protects the other
    // page's edit), so "overwrite" has to mean "commit against what is there
    // now" — which requires reading it first.
    const { sandbox, calls } = boot({
        responses: [
            { status: 'success', content: 'THEIRS\n', revision: 'rev-theirs',
              actions: { edit: true, delete: true }, read_only: false },
            { status: 'success', result: { revision: 'rev-mine' } },
        ],
    });
    const data = await sandbox.memoryDocWrite(ENTRY, 'MINE\n', null);
    assert.match(calls[0].url, /\/api\/memory\/content/);
    assert.equal(calls[1].body.revision, 'rev-theirs',
        'the overwrite must commit against the revision that is actually there');
    assert.equal(data.status, 'success');
});

test('an index that is behind is reported, not hidden', async () => {
    const { sandbox, toasts } = boot({
        responses: [{ status: 'pending', code: 'index_pending',
                      result: { revision: 'rev-2' } }],
    });
    const data = await sandbox.memoryDocWrite(ENTRY, 'NEW\n', 'rev-1');
    assert.equal(data.status, 'success', 'the content did save');
    assert.deepEqual(toasts, ['memory_index_pending']);
});

test('delete confirms, posts the entry, then leaves the viewer', async () => {
    const { sandbox, calls, confirms, toasts } = boot({
        responses: [{ status: 'success', result: {} }],
        doc: { filename: 'notes.md', category: 'memory', actions: { delete: true } },
    });
    sandbox.memoryDocDelete();
    await new Promise((r) => setImmediate(r));
    assert.equal(confirms.length, 1);
    assert.match(calls[0].url, /\/api\/memory\/delete$/);
    assert.deepEqual(calls[0].body, {
        filename: 'notes.md', category: 'memory', agent_id: 'agent-1',
    });
    assert.ok(calls.some((c) => c.url === 'closeMemoryViewer'));
    assert.deepEqual(toasts, ['memory_deleted']);
});

test('clear posts the category for an Agent target', async () => {
    const { sandbox, calls } = boot({
        responses: [{ status: 'success', result: { removed: 2 } }],
        category: 'memory',
    });
    sandbox.memoryDocClear();
    await new Promise((r) => setImmediate(r));
    assert.match(calls[0].url, /\/api\/memory\/clear$/);
    assert.deepEqual(calls[0].body, { category: 'memory', agent_id: 'agent-1' });
});

test('clear is not offered for the personal domain', async () => {
    const { sandbox, calls } = boot({ target: 'personal' });
    sandbox.memoryDocClear();
    assert.deepEqual(calls, [],
        'the personal domain has its own write surface, and this verb refuses it');
});

test('the delete and clear buttons follow the server answer', () => {
    const { sandbox, nodes } = boot({
        doc: { filename: 'notes.md', actions: { edit: true, delete: true },
               readOnly: false },
    });
    sandbox.memorySyncDocButtons({ editing: false });
    assert.ok(!nodes['memory-btn-delete'].classes.has('hidden'),
        'a writable entry offers delete');
    assert.ok(!nodes['memory-btn-clear'].classes.has('hidden'),
        'an Agent target offers clear');

    sandbox.memorySyncDocButtons({ editing: true });
    assert.ok(nodes['memory-btn-delete'].classes.has('hidden'),
        'editing hides the destructive verbs rather than deleting mid-edit');
    assert.ok(nodes['memory-btn-clear'].classes.has('hidden'));
});

test('a read-only document offers neither verb', () => {
    const { sandbox, nodes } = boot({
        doc: { filename: '2026-01-01.md', actions: { edit: false, delete: false },
               readOnly: true },
    });
    sandbox.memorySyncDocButtons({ editing: false });
    assert.ok(nodes['memory-btn-delete'].classes.has('hidden'));
});

test('a document the write would refuse offers neither verb', () => {
    // `readOnly` is false here: the only signal is the server's `actions`, and
    // clear follows it too — clear is a delete-class verb on the same root, so
    // it must not stay clickable where the write is refused.
    const { sandbox, nodes } = boot({
        doc: { filename: 'notes.md', actions: { edit: false, delete: false },
               readOnly: false },
    });
    sandbox.memorySyncDocButtons({ editing: false });
    assert.ok(nodes['memory-btn-delete'].classes.has('hidden'));
    assert.ok(nodes['memory-btn-clear'].classes.has('hidden'));
});

test('the memory editor no longer writes through the workspace file API', () => {
    // The regression this whole change removes: an edit that bypassed the
    // version condition and the index publish.
    const editor = constBlock('memoryEditor');
    assert.ok(!/docWriteFile/.test(editor),
        'the memory editor must not save through the workspace file API');
    assert.ok(!/docReadFile/.test(editor));
    assert.match(editor, /memoryDocWrite/);
    assert.match(editor, /memoryDocRead/);
});
