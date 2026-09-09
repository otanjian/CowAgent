// Path-based workbench/admin nav helpers (chat/admin split).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../channel/web/static/js/console.js'), 'utf8');

function section(start, end) {
    const from = source.indexOf(start);
    const to = source.indexOf(end, from + start.length);
    assert.ok(from >= 0 && to > from, `Missing console section ${start}`);
    return source.slice(from, to + end.length);
}

test('nav area helpers: path, open, qualify entry', () => {
    const code = section('// === NAV_AREA_BEGIN ===', '// === NAV_AREA_END ===');
    const sandbox = {
        window: {
            open(url, name) {
                sandbox._opened = { url, name };
                return { name };
            },
        },
    };
    vm.runInNewContext(code, sandbox);
    assert.equal(sandbox._navAreaFromPath('/chat'), 'workbench');
    assert.equal(sandbox._navAreaFromPath('/admin'), 'admin');
    assert.equal(sandbox._navAreaFromPath('/admin/'), 'admin');
    assert.equal(sandbox._navAreaFromPath('/admin?x=1'.split('?')[0]), 'admin');
    sandbox._openNavArea('admin');
    assert.deepEqual(sandbox._opened, { url: '/admin', name: 'cow-admin' });
    sandbox._openNavArea('workbench');
    assert.deepEqual(sandbox._opened, { url: '/chat', name: 'cow-workbench' });
    assert.equal(sandbox._qualifyAdminConsoleEntry({
        identityMode: 'database', isPlatformAdmin: false, isTenantAdmin: false,
    }), false);
    assert.equal(sandbox._qualifyAdminConsoleEntry({
        identityMode: 'database', isPlatformAdmin: false, isTenantAdmin: true,
    }), true);
    assert.equal(sandbox._qualifyAdminConsoleEntry({
        identityMode: 'database', isPlatformAdmin: true, isTenantAdmin: false,
    }), true);
    assert.equal(sandbox._qualifyAdminConsoleEntry({
        identityMode: 'legacy', isPlatformAdmin: false, isTenantAdmin: false,
    }), true);
});
