// tests/test_admin_home_frontend.cjs
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const { test } = require('node:test');

test('admin-home markup has KPI and shortcut hooks', () => {
    const html = fs.readFileSync(path.join(__dirname, '../channel/web/chat.html'), 'utf8');
    assert.match(html, /id="admin-home-kpis"/);
    assert.match(html, /id="admin-home-shortcuts"/);
    assert.match(html, /id="view-admin-home"/);
});

test('console.js has honest KPI labels and overview fetch', () => {
    const js = fs.readFileSync(path.join(__dirname, '../channel/web/static/js/console.js'), 'utf8');
    assert.match(js, /admin_home_kpi_messages_today/);
    assert.match(js, /admin_home_kpi_members/);
    assert.match(js, /\/api\/admin\/overview/);
    assert.doesNotMatch(js, /较上月|较昨日|99\.98%/);
});
