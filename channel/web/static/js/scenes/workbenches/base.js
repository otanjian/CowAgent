/* scenes/workbenches/base.js — 通用工作台渲染器。
 *
 * 由 scenes/registry.js 按 workbench 类型（'base'）注册。渲染工作台标题 +
 * 子场景面板 + 功能模块卡片；子场景含 ``params`` 时渲染表单（表单/查询/
 * 导入），含 ``import_config`` 时渲染文件导入，含 ``erp_config`` 时渲染
 * ERP 系统展示。无专用渲染器且无子场景的场景走普通激活（index.js 处理）。
 *
 * DOM 全部通过 createElement/appendChild 构建（便于单测与浏览器一致）。
 */
(function () {
    'use strict';

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function el(tag, className, text) {
        const e = document.createElement(tag);
        if (className) e.className = className;
        if (text != null) e.textContent = text;
        return e;
    }

    // 渲染子场景的功能模块卡片列表。
    function renderModules(sub, parent) {
        const noData = !(sub.indicators && sub.indicators.length)
            && !(sub.required_fields && sub.required_fields.length)
            && !(sub.optional_fields && sub.optional_fields.length);
        if (noData) {
            const desc = el('div', 'text-sm text-slate-400 py-4', sub.description || '');
            parent.appendChild(desc);
            return;
        }
        const grid = el('div', 'grid grid-cols-1 sm:grid-cols-2 gap-3 mt-4');
        const items = [];
        (sub.indicators || []).forEach(function (ind) {
            items.push({ icon: 'fa-chart-line', title: ind.name || '', desc: ind.meaning || ind.formula || '' });
        });
        (sub.required_fields || []).forEach(function (f) {
            items.push({ icon: 'fa-circle-check', title: f, desc: 'required' });
        });
        (sub.optional_fields || []).forEach(function (f) {
            items.push({ icon: 'fa-circle', title: f, desc: 'optional' });
        });
        items.forEach(function (it) {
            const card = el('div', 'rounded-xl border border-slate-200 dark:border-white/10 p-3');
            const head = el('div', 'flex items-center gap-2 text-sm font-medium text-slate-700 dark:text-slate-200');
            const icon = el('i', 'fas ' + it.icon + ' text-slate-400');
            head.appendChild(icon);
            head.appendChild(document.createTextNode(it.title));
            card.appendChild(head);
            if (it.desc) card.appendChild(el('p', 'text-xs text-slate-400 mt-1', it.desc));
            grid.appendChild(card);
        });
        parent.appendChild(grid);
    }

    function paramField(param, form) {
        const wrapper = el('label', 'block mb-3');
        const label = el('span', 'text-sm text-slate-600 dark:text-slate-300 mb-1 block',
            (param.label || param.name || '') + (param.required ? ' *' : ''));
        wrapper.appendChild(label);
        const name = param.name || '';
        if (param.type === 'select' && Array.isArray(param.options)) {
            const select = el('select', 'wb-select w-full px-3 py-2 rounded-lg text-sm bg-slate-100 dark:bg-white/10 text-slate-800 dark:text-slate-100 border border-slate-200 dark:border-white/10');
            select.name = name;
            if (param.required) select.required = true;
            param.options.forEach(function (o) {
                const opt = el('option', null, o.label);
                opt.value = o.value;
                select.appendChild(opt);
            });
            wrapper.appendChild(select);
        } else if (param.type === 'textarea') {
            const ta = el('textarea', 'wb-textarea w-full px-3 py-2 rounded-lg text-sm bg-slate-100 dark:bg-white/10 text-slate-800 dark:text-slate-100 border border-slate-200 dark:border-white/10');
            ta.name = name;
            if (param.required) ta.required = true;
            if (param.placeholder) ta.placeholder = param.placeholder;
            wrapper.appendChild(ta);
        } else {
            const input = el('input', 'wb-input w-full px-3 py-2 rounded-lg text-sm bg-slate-100 dark:bg-white/10 text-slate-800 dark:text-slate-100 border border-slate-200 dark:border-white/10');
            input.type = (param.type === 'number' ? 'number' : 'text');
            input.name = name;
            if (param.required) input.required = true;
            if (param.placeholder) input.placeholder = param.placeholder;
            wrapper.appendChild(input);
        }
        form.appendChild(wrapper);
    }

    // 渲染文件导入区（如需）。
    function renderImport(sub, scene, sessionId, panel) {
        const cfg = sub.import_config;
        if (!cfg || !cfg.enabled) return;
        const box = el('div', 'mt-4 p-4 rounded-xl border border-slate-200 dark:border-white/10');
        const head = el('div', 'text-sm font-medium text-slate-700 dark:text-slate-200 mb-2');
        head.appendChild(el('i', 'fas fa-file-import mr-1 text-indigo-400'));
        head.appendChild(document.createTextNode('导入数据'));
        box.appendChild(head);
        if (cfg.description) box.appendChild(el('p', 'text-xs text-slate-400 mb-2', cfg.description));
        const file = el('input', 'wb-import-file');
        file.type = 'file';
        file.accept = cfg.accept || '';
        file.dataset.scene = scene.id;
        file.dataset.session = sessionId;
        box.appendChild(file);
        const btn = el('button', 'wb-import-btn mt-2 px-3 py-1.5 rounded-lg bg-indigo-500 hover:bg-indigo-600 text-white text-sm', '上传');
        btn.type = 'button';
        box.appendChild(btn);
        panel.appendChild(box);

        const upload = doUpload.bind(null, btn);
        btn.addEventListener('click', function () {
            const f = file.files && file.files[0];
            if (!f) { alert('请先选择文件'); return; }
            upload(f, scene);
        });
    }

    function doUpload(btn, file, scene) {
        const reader = new FileReader();
        btn.disabled = true;
        btn.textContent = '上传中...';
        reader.onload = function () {
            const dataUrl = reader.result || '';
            fetch('/api/scenes/workbench/import', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    scene_id: scene.id,
                    filename: file.name,
                    content: dataUrl,
                    content_encoding: 'base64',
                }),
            }).then(function (r) { return r.json(); }).then(function (data) {
                btn.disabled = false;
                btn.textContent = (data && data.status === 'success') ? '上传成功' : '上传失败';
                setTimeout(function () { btn.textContent = '上传'; }, 1500);
            }).catch(function () {
                btn.disabled = false;
                btn.textContent = '上传失败';
            });
        };
        reader.readAsDataURL(file);
    }

    // 渲染 ERP 系统展示（仅元数据，无真实连接）。
    function renderErp(sub, panel) {
        const cfg = sub.erp_config;
        if (!cfg || !cfg.enabled) return;
        const systems = (cfg.systems || []);
        if (!systems.length) return;
        const box = el('div', 'mt-3 flex flex-wrap gap-2 align-center items-center');
        box.appendChild(el('span', 'text-xs text-slate-400', '数据源：'));
        const labels = { u9: '用友U9', sap: 'SAP', kingdee: '金蝶', sql: 'SQL Server', hana: 'SAP HANA', orion: 'Oracle', fine: '帆软' };
        systems.forEach(function (s) {
            box.appendChild(el('span', 'text-[11px] px-2 py-0.5 rounded-full bg-slate-100 dark:bg-white/10 text-slate-500 dark:text-slate-300',
                labels[s] || s));
        });
        panel.appendChild(box);
    }

    function renderPanel(scene, sub, panel, sessionId) {
        const wrap = el('div', 'rounded-2xl border border-slate-200 dark:border-white/10 bg-white dark:bg-[#1c1c1c] p-5');
        const head = el('div', 'flex items-center gap-2 mb-2');
        const icon = el('i', 'fas ' + (sub.icon || 'fa-cube'));
        icon.style.color = sub.color || '#0ea5e9';
        head.appendChild(icon);
        head.appendChild(el('h3', 'text-lg font-semibold text-slate-800 dark:text-slate-100', sub.name));
        wrap.appendChild(head);
        wrap.appendChild(el('p', 'text-sm text-slate-500 dark:text-slate-400 mb-2', sub.description || ''));
        if (sub.params && sub.params.length) {
            const form = el('form', 'wb-param-form mt-3');
            sub.params.forEach(function (p) { paramField(p, form); });
            const submit = el('button', 'mt-2 px-4 py-2 rounded-lg bg-primary-500 hover:bg-primary-600 text-white text-sm', '提交');
            submit.type = 'submit';
            form.appendChild(submit);
            form.addEventListener('submit', function (ev) {
                ev.preventDefault();
                if (typeof window.activateScene === 'function') window.activateScene(scene);
            });
            wrap.appendChild(form);
        }
        renderModules(sub, wrap);
        renderImport(sub, scene, sessionId, wrap);
        renderErp(sub, wrap);
        panel.appendChild(wrap);
    }

    function renderWorkbench(scene) {
        const subs = scene.sub_scenes || [];
        if (!subs.length) return false;
        const catalog = document.getElementById('scene-catalog');
        const container = document.getElementById('scene-workbench');
        if (!container) return false;
        // 当前会话 id（工作台文件导入归属到该会话工作区）。
        const sessionId = (typeof window.sessionId !== 'undefined' && window.sessionId) || '';

        if (catalog) catalog.classList.add('hidden');
        container.classList.remove('hidden');
        container.replaceChildren();

        const scroll = el('div', 'flex-1 min-h-0 overflow-y-auto p-4 md:p-8 lg:p-10');
        const inner = el('div', 'w-full max-w-[1600px] mx-auto');
        const header = el('div', 'mb-4 flex items-center justify-between');
        header.appendChild(el('h2', 'text-xl font-bold text-slate-800 dark:text-slate-100',
            scene.workbench_title || scene.name || '工作台'));
        const back = el('button', 'wb-back-btn px-3 py-1.5 rounded-lg bg-slate-100 dark:bg-white/10 text-slate-600 dark:text-slate-300 text-sm', '返回场景中心');
        back.type = 'button';
        back.addEventListener('click', function () {
            container.classList.add('hidden');
            if (catalog) catalog.classList.remove('hidden');
            if (typeof window.navigateTo === 'function') window.navigateTo('scenes');
        });
        header.appendChild(back);
        inner.appendChild(header);
        const tabsEl = el('div', 'wb-subscene-tabs flex flex-wrap gap-2 mb-4');
        const panelEl = el('div', 'wb-subscene-panel');
        inner.appendChild(tabsEl);
        inner.appendChild(panelEl);
        scroll.appendChild(inner);
        container.appendChild(scroll);

        subs.forEach(function (sub, idx) {
            const btn = el('button', 'wb-subscene-tab px-3 py-1.5 rounded-full text-sm font-medium transition-colors ' +
                (idx === 0 ? 'bg-primary-500 text-white' : 'bg-slate-100 dark:bg-white/10 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-white/15'), sub.name);
            btn.type = 'button';
            btn.addEventListener('click', function () {
                tabsEl.querySelectorAll('.wb-subscene-tab').forEach(function (t) {
                    t.className = 'wb-subscene-tab px-3 py-1.5 rounded-full text-sm font-medium transition-colors bg-slate-100 dark:bg-white/10 text-slate-600 dark:text-slate-300';
                });
                btn.className = 'wb-subscene-tab px-3 py-1.5 rounded-full text-sm font-medium transition-colors bg-primary-500 text-white';
                panelEl.replaceChildren();
                renderPanel(scene, sub, panelEl, sessionId);
            });
            tabsEl.appendChild(btn);
        });

        renderPanel(scene, subs[0], panelEl, sessionId);
        return true;
    }

    if (window.ScenesRegistry) {
        window.ScenesRegistry.registerRenderer('base', renderWorkbench);
    }
})();
