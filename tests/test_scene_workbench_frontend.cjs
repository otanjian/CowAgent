// Exercise the shipped scenes workbench renderer (scenes/workbenches/base.js)
// through the registry. Browser acceptance owns layout; these tests make the
// generic workbench (sub-scene tabs, module cards, import, ERP metadata) and
// the generic dispatch reproducible.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const registrySource = fs.readFileSync(path.join(__dirname, '../channel/web/static/js/scenes/registry.js'), 'utf8');
const baseSource = fs.readFileSync(path.join(__dirname, '../channel/web/static/js/scenes/workbenches/base.js'), 'utf8');

function response(data, status = 200) {
    return { ok: status >= 200 && status < 300, status, json: async () => data };
}

function element(document, tag = 'div') {
    const classes = new Set();
    let text = '', html = '';
    const el = {
        tagName: tag.toUpperCase(), id: '', attrs: {}, dataset: {}, style: {}, disabled: false,
        children: [], parentNode: null, value: '', placeholder: '', _hide: null,
        get className() { return [...classes].join(' '); },
        set className(value) { classes.clear(); String(value).split(/\s+/).filter(Boolean).forEach(v => classes.add(v)); },
        get textContent() { return text; },
        set textContent(value) { text = String(value); html = ''; this.children = []; },
        get innerHTML() { return html; },
        set innerHTML(value) { html = String(value); text = ''; this.children = []; },
        classList: {
            add: (...names) => names.forEach(name => classes.add(name)),
            remove: (...names) => names.forEach(name => classes.delete(name)),
            contains: name => classes.has(name),
            toggle(name, force) {
                const add = force === undefined ? !classes.has(name) : force;
                if (add) classes.add(name); else classes.delete(name);
                return add;
            },
        },
        setAttribute(key, value) { this.attrs[key] = String(value); },
        getAttribute(key) { return this.attrs[key] ?? null; },
        removeAttribute(key) { delete this.attrs[key]; },
        appendChild(child) {
            child.parentNode?.removeChild(child);
            this.children.push(child); child.parentNode = this;
            return child;
        },
        removeChild(child) { this.children = this.children.filter(c => c !== child); child.parentNode = null; },
        replaceChildren(...children) { this.children = []; children.forEach(child => this.appendChild(child)); },
        querySelectorAll(selector) {
            const selectors = selector.split(',').map(s => s.trim());
            return this.children.flatMap(child => [
                ...(selectors.some(s => child.matches(s)) ? [child] : []), ...child.querySelectorAll(selector),
            ]);
        },
        querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
        matches(selector) {
            const simple = selector.replace(/:not\([^)]*\)/g, '').trim();
            if (simple.startsWith('#')) return this.id === simple.slice(1);
            if (simple.startsWith('.')) return this.classList.contains(simple.slice(1));
            return this.tagName.toLowerCase() === simple.replace(/\[.*$/, '').toLowerCase();
        },
        focus() { this.placeholder = 'focus'; },
        addEventListener(type, fn) {
            if (!this._listeners) this._listeners = {};
            (this._listeners[type] = this._listeners[type] || []).push(fn);
        },
        dispatch(type, extra) {
            for (const fn of (this._listeners[type] || [])) fn.call(this, { target: this, preventDefault() {}, stopPropagation() {}, ...extra });
        },
        click() { this.dispatch('click'); },
    };
    return el;
}

function setup() {
    const nodes = new Map();
    const document = { body: null };
    document.body = element(document, 'body');
    const node = id => {
        if (!nodes.has(id)) {
            nodes.set(id, element(document, 'div'));
            nodes.get(id).id = id;
        }
        return nodes.get(id);
    };
    function byIdInTree(id) {
        const walk = el => {
            if (el.id === id) return el;
            for (const child of el.children || []) {
                const found = walk(child);
                if (found) return found;
            }
            return null;
        };
        return walk(document.body);
    }
    Object.assign(document, {
        getElementById: id => nodes.get(id) || byIdInTree(id),
        createElement: tag => element(document, tag),
        createTextNode: text => { const n = element(document, '#text'); n.textContent = text; return n; },
        body: document.body, activeElement: null,
        addEventListener() {}, removeEventListener() {},
        querySelector: () => null, querySelectorAll: () => [],
    });
    // 工作台容器固定存在于 chat.html。
    for (const id of ['scene-catalog', 'scene-workbench']) node(id);
    const requests = [];
    const ctx = {
        console, Date, URL, URLSearchParams, document,
        currentLang: 'zh', window: null,
        navigateTo(view) { ctx._nav = view; },
        setTimeout(fn) { return 0; }, clearTimeout() {},
        fetch(url, options) {
            requests.push({ url, options });
            return Promise.resolve(response({ status: 'success', path: '/tmp/x', size: 1,
                workbench: { import: { enabled: true }, erp: { systems: [] } } }));
        },
    };
    ctx.window = ctx;
    vm.createContext(ctx);
    const run = code => vm.runInContext(code, ctx);
    run(registrySource);
    run(baseSource);
    return { ctx, run, node, document, nodes, requests };
}

const scene = {
    id: 'procurement_supplier', name: '供应商管理', workbench_title: '供应商管理工作台',
    has_workbench: true, category: 'procurement', color: '#10b981',
    sub_scenes: [
        {
            id: 'supplier_qualification', name: '资质审核', icon: 'fa-file-check', color: '#10b981',
            description: '审核供应商资质文件，评估准入条件',
            indicators: [{ name: '准入标准', formula: '审核要点', meaning: '基本信息核实' }],
            required_fields: ['企业名称', '统一社会信用代码'],
            import_config: { enabled: true, accept: '.xlsx,.csv', description: '上传供应商资质信息表' },
            erp_config: { enabled: true, systems: ['u9', 'sap'] },
        },
        {
            id: 'supplier_performance', name: '绩效评估', icon: 'fa-chart-bar', color: '#3b82f6',
            description: '多维度评估供应商绩效表现',
            indicators: [{ name: '质量绩效', formula: '合格率', meaning: '来料检验合格率' }],
        },
    ],
};

test('generic workbench renders title, sub-scene tabs, modules, import and ERP', async () => {
    const h = setup();
    const rendered = h.ctx.ScenesRegistry.render('base', scene);
    assert.equal(rendered, true);
    const container = h.node('scene-workbench');
    assert.equal(container.classList.contains('hidden'), false);
    // 子场景页签 2 个
    const tabs = container.querySelector('.wb-subscene-tabs');
    assert.equal(tabs.children.length, 2);
    // 当前面板含导入区与 ERP 系统。
    const panel = container.querySelector('.wb-subscene-panel');
    const importFile = panel.querySelector('.wb-import-file');
    assert.ok(importFile, 'file input rendered');
    assert.equal(importFile.accept, '.xlsx,.csv');
    // ERP 系统 span（textContent='SAP'）。
    const erpTexts = [];
    (function walk(n) {
        (n.children || []).forEach(c => {
            if (c.textContent) erpTexts.push(c.textContent);
            walk(c);
        });
    })(panel);
    assert.ok(erpTexts.includes('SAP'), 'ERP system label rendered');
    // 模块卡片（indicator + required_fields）
    assert.ok(erpTexts.includes('准入标准'));
    assert.ok(erpTexts.includes('企业名称'));
});

test('back button returns to the scene catalog', async () => {
    const h = setup();
    h.node('scene-catalog').classList.remove('hidden');
    h.ctx.ScenesRegistry.render('base', scene);
    const back = h.node('scene-workbench').querySelector('.wb-back-btn');
    assert.ok(back, 'back button rendered');
    const catalog = h.node('scene-catalog');
    assert.equal(catalog.classList.contains('hidden'), true);
    back.click();
    assert.equal(h.node('scene-workbench').classList.contains('hidden'), true);
    assert.equal(catalog.classList.contains('hidden'), false);
    assert.equal(h.ctx._nav, 'scenes');
});

test('legacy registry preserves the generic workbench fallback', () => {
    const h = setup();
    for (const [type, file] of [
        ['voucher', 'voucher'], ['tax', 'tax'], ['financial_audit', 'financial_audit'],
        ['sap_analysis', 'sap_analysis'], ['quality_trace', 'quality_trace'], ['scheduling', 'scheduling'],
    ]) {
        assert.equal(h.ctx.ScenesRegistry.hasRenderer(type), false, type + ' renderer removed');
        assert.equal(h.ctx.ScenesRegistry.resolveWorkbenchType({ skill_name: typeMap(type) }), 'base');
    }
});

function typeMap(type) {
    return {
        voucher: 'finance-voucher', tax: 'finance-expert', financial_audit: 'financial-report-audit',
        sap_analysis: 'sap-integration', quality_trace: 'quality-trace', scheduling: 'production-plan',
    }[type];
}
