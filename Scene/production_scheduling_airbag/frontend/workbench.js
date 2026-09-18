/* =====================================================================
   Airbag Scheduling Workbench - 气袋排产工作台（pmc-scheduler-hmt-qd）
   功能：解析预览 / 执行排产 / 排产规则 / 产能配置 / 班组产线 / 拼线规则 / 模板管理 / 历史记录
   权限：场景入口 scenes.use.production；配置读写 scheduling.config.manage
   ===================================================================== */

// 工作台状态
let _airbagScene = null;
let _airbagModal = null;                 // 模态框根元素
let _airbagActiveTab = 'run';            // 当前 Tab：run/rules/capacity/teams/merge/template
let _airbagParseResult = null;           // 解析预览结果缓存
let _airbagTemplateInfo = null;          // 模板信息
let _airbagCanManage = false;            // 是否有 scheduling.config.manage
let _airbagUploadFile = null;            // 本次上传文件 {filename, content(base64), is_base64}
let _airbagBusy = false;                 // 防重复提交
let _airbagTemplateSheets = [];          // 模板各 sheet 内容预览
let _airbagActiveSheet = '';             // 当前预览的 sheet 名
let _airbagMaximized = false;            // 工作台是否最大化
let _airbagConfigSource = { capacity: 'system', teams: 'system', merge: 'system' }; // 配置来源 system/template
let _airbagDemands = null;               // 预估出货量表信息与内容预览
let _airbagDemandsFull = false;          // 预估出货量表是否已全量加载（>200 行时点「全量显示」）
let _airbagDemandsError = '';            // 预估出货量表加载/导入错误信息（非空时页面顶部显眼提示）
let _airbagDemandsLoading = false;       // 预估出货量表导入/加载中（显示进度提示）
let _airbagDemandsNotice = '';           // 预估出货量表导入成功提示（含条数统计）
let _airbagDemandsShown = 200;           // 全量加载后当前渲染的数据行数（逐批渲染，避免卡顿）
let _airbagSyncStatus = '';              // 从 Excel 同步配置状态（''=无 / 前缀 LOAD:/OK:/ERR:）
let _airbagSyncBusy = false;             // 从 Excel 同步进行中（防重复）

// Tab 定义（id / 图标 / 名称）
const _AIRBAG_TABS = [
    { id: 'run',      icon: 'fa-wand-magic-sparkles', name: '执行排产' },
    { id: 'demands',  icon: 'fa-boxes-stacked',       name: '预估出货量' },
    { id: 'rules',    icon: 'fa-sliders',             name: '排产规则' },
    { id: 'capacity', icon: 'fa-industry',            name: '产能配置' },
    { id: 'teams',    icon: 'fa-people-group',        name: '班组产线' },
    { id: 'merge',    icon: 'fa-object-group',        name: '拼线规则' },
    { id: 'template', icon: 'fa-file-excel',          name: '模板管理' },
];

const _AIRBAG_ACCENT = '#0d9488'; // 青绿色主色调

// 小工具：HTML 转义
function _airbagEscape(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// 小工具：数字安全格式化
function _airbagNum(v, digits) {
    const n = parseFloat(v);
    if (isNaN(n)) return _airbagEscape(v);
    return digits !== undefined ? n.toFixed(digits) : String(n);
}

// ---------------------------------------------------------------------
// 打开 / 关闭工作台
// ---------------------------------------------------------------------
function openAirbagSchedulingWorkbench(scene) {
    _airbagScene = scene;
    _airbagParseResult = null;
    _airbagUploadFile = null;

    // 新会话并激活场景（system_prompt 生效）
    newChat();
    activateScene(scene);

    _airbagEnsureModal();
    _airbagModal.classList.remove('hidden');

    // 权限判断：scheduling.config.manage 或 admin 角色
    const perms = (typeof currentUser !== 'undefined' && currentUser && currentUser.permissions) || [];
    const roles = (typeof currentUser !== 'undefined' && currentUser && currentUser.roles) || [];
    _airbagCanManage = perms.includes('scheduling.config.manage') || roles.includes('admin');

    _airbagSwitchTab('run');
    _airbagLoadTemplateInfo();
    _airbagLoadConfigs();
    _airbagLoadDemands();
    // 自动加载模板内容预览 + 解析模板配置（空配置时填充显示）
    _airbagLoadTemplateContent();
    _airbagAutoFillFromTemplate();
}

function closeAirbagSchedulingWorkbench() {
    if (_airbagModal) _airbagModal.classList.add('hidden');
    _airbagScene = null;
}

// 幂等创建模态框 DOM
function _airbagEnsureModal() {
    if (_airbagModal && document.body.contains(_airbagModal)) return;

    const modal = document.createElement('div');
    modal.id = 'airbag-scheduling-workbench-modal';
    modal.className = 'hidden fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm';
    modal.addEventListener('click', (e) => {
        if (e.target === modal) closeAirbagSchedulingWorkbench();
    });

    modal.innerHTML = `
        <div id="airbag-wb-box" class="bg-white dark:bg-[#1A1A1A] rounded-2xl border border-slate-200 dark:border-white/10 shadow-2xl w-[94vw] h-[88vh] max-w-7xl flex flex-col overflow-hidden">
            <!-- Header -->
            <div class="flex items-center justify-between px-6 py-4 border-b border-slate-200 dark:border-white/10 flex-shrink-0">
                <div class="flex items-center gap-3">
                    <div class="w-10 h-10 rounded-xl flex items-center justify-center" style="background:${_AIRBAG_ACCENT}">
                        <i class="fas fa-calendar-check text-white"></i>
                    </div>
                    <div>
                        <h3 class="text-lg font-bold text-slate-800 dark:text-slate-100">气袋排产工作台</h3>
                        <p class="text-xs text-slate-500 dark:text-slate-400">基于《生产排产模板.xlsx》自动生成排产2表（早班/夜班/班组）</p>
                    </div>
                </div>
                <div class="flex items-center gap-2">
                    <button id="airbag-wb-history-btn" class="px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors flex items-center gap-1.5">
                        <i class="fas fa-clock-rotate-left"></i>历史记录
                    </button>
                    <button id="airbag-wb-max-btn" title="最大化 / 还原" class="w-8 h-8 rounded-lg hover:bg-slate-100 dark:hover:bg-white/10 flex items-center justify-center transition-colors">
                        <i class="fas fa-expand text-slate-400"></i>
                    </button>
                    <button onclick="closeAirbagSchedulingWorkbench()" class="w-8 h-8 rounded-lg hover:bg-slate-100 dark:hover:bg-white/10 flex items-center justify-center transition-colors">
                        <i class="fas fa-xmark text-slate-400"></i>
                    </button>
                </div>
            </div>
            <!-- Body -->
            <div class="flex-1 flex overflow-hidden">
                <!-- Left Nav -->
                <div class="w-52 border-r border-slate-200 dark:border-white/10 overflow-y-auto p-3 space-y-1 flex-shrink-0 bg-slate-50/60 dark:bg-white/[0.02]">
                    <p class="text-[10px] font-semibold text-slate-400 uppercase tracking-wider px-3 pt-1 pb-2">工作台功能</p>
                    <div id="airbag-wb-nav"></div>
                    <div class="mt-4 pt-4 border-t border-slate-200 dark:border-white/10 px-3">
                        <p class="text-[10px] leading-relaxed text-slate-400 dark:text-slate-500">
                            <i class="fas fa-circle-info mr-1" style="color:${_AIRBAG_ACCENT}"></i>
                            解析预览不会写入文件；执行排产将生成新排产表（不修改原模板）。
                        </p>
                    </div>
                </div>
                <!-- Right Content -->
                <div id="airbag-wb-content" class="flex-1 overflow-y-auto p-6 bg-white dark:bg-[#1A1A1A]"></div>
            </div>
        </div>
    `;

    document.body.appendChild(modal);
    _airbagModal = modal;

    // 渲染左侧导航
    const nav = modal.querySelector('#airbag-wb-nav');
    _AIRBAG_TABS.forEach(tab => {
        const btn = document.createElement('button');
        btn.id = `airbag-nav-${tab.id}`;
        btn.dataset.tab = tab.id;
        btn.className = 'w-full flex items-center gap-2.5 px-3 py-2.5 rounded-xl text-sm font-medium transition-colors cursor-pointer text-slate-500 dark:text-slate-400 hover:bg-white hover:text-slate-800 dark:hover:bg-white/5 dark:hover:text-slate-100';
        btn.innerHTML = `<i class="fas ${tab.icon} w-4 text-center"></i><span>${tab.name}</span>`;
        btn.addEventListener('click', () => _airbagSwitchTab(tab.id));
        nav.appendChild(btn);
    });

    // 历史记录按钮
    modal.querySelector('#airbag-wb-history-btn').addEventListener('click', _airbagShowHistory);

    // 最大化 / 还原按钮
    modal.querySelector('#airbag-wb-max-btn').addEventListener('click', _airbagToggleMaximize);
}

// 工作台最大化 / 还原
function _airbagToggleMaximize() {
    const box = _airbagModal ? _airbagModal.querySelector('#airbag-wb-box') : null;
    if (!box) return;
    _airbagMaximized = !_airbagMaximized;
    const maxCls = ['w-[94vw]', 'h-[88vh]', 'max-w-7xl', 'rounded-2xl', 'shadow-2xl'];
    const fullCls = ['w-screen', 'h-screen', 'max-w-none', 'rounded-none', 'shadow-none'];
    maxCls.forEach(c => box.classList.toggle(c, !_airbagMaximized));
    fullCls.forEach(c => box.classList.toggle(c, _airbagMaximized));
    box.classList.toggle('border', !_airbagMaximized);
    box.classList.toggle('border-0', _airbagMaximized);
    const btn = _airbagModal.querySelector('#airbag-wb-max-btn');
    if (btn) btn.innerHTML = _airbagMaximized ? '<i class="fas fa-compress text-slate-400"></i>' : '<i class="fas fa-expand text-slate-400"></i>';
}

// ---------------------------------------------------------------------
// Tab 切换
// ---------------------------------------------------------------------
function _airbagSwitchTab(tabId) {
    _airbagActiveTab = tabId;
    if (!_airbagModal) return;
    _AIRBAG_TABS.forEach(tab => {
        const btn = _airbagModal.querySelector(`#airbag-nav-${tab.id}`);
        if (!btn) return;
        const active = tab.id === tabId;
        btn.className = 'w-full flex items-center gap-2.5 px-3 py-2.5 rounded-xl text-sm font-medium transition-colors cursor-pointer ' +
            (active
                ? 'bg-white dark:bg-white/10 text-slate-800 dark:text-slate-100 shadow-sm border border-slate-200 dark:border-white/10'
                : 'text-slate-500 dark:text-slate-400 hover:bg-white hover:text-slate-800 dark:hover:bg-white/5 dark:hover:text-slate-100');
    });
    const content = _airbagModal.querySelector('#airbag-wb-content');
    if (tabId === 'run') content.innerHTML = _airbagRenderRunTab();
    else if (tabId === 'demands') content.innerHTML = _airbagRenderDemandsTab();
    else if (tabId === 'rules') content.innerHTML = _airbagRenderRulesTab();
    else if (tabId === 'capacity') content.innerHTML = _airbagRenderCapacityTab();
    else if (tabId === 'teams') content.innerHTML = _airbagRenderTeamsTab();
    else if (tabId === 'merge') content.innerHTML = _airbagRenderMergeTab();
    else if (tabId === 'template') content.innerHTML = _airbagRenderTemplateTab();
    _airbagBindTabEvents(tabId);
}

// ---------------------------------------------------------------------
// API 封装
// ---------------------------------------------------------------------
async function _airbagApi(method, action, body) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const res = await fetch(`/api/airbag-scheduling/${action}`, opts);
    let data;
    try { data = await res.json(); } catch (e) { data = { status: 'error', message: '响应解析失败' }; }
    if (res.status === 403 || res.status === 401) {
        data = { status: 'error', message: data.message || '无权限执行该操作' };
    }
    return data;
}

// 文件 → base64
function _airbagFileToBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = (e) => {
            const raw = e.target.result; // data:application/...;base64,XXXX
            const idx = raw.indexOf(',');
            resolve(idx >= 0 ? raw.slice(idx + 1) : raw);
        };
        reader.onerror = () => reject(new Error('文件读取失败'));
        reader.readAsDataURL(file);
    });
}

// 组装上传 body
async function _airbagBuildBody(extra) {
    const body = {};
    if (_airbagUploadFile) body.files = { excel: _airbagUploadFile };
    const materialEl = document.getElementById('airbag-material');
    const material = materialEl ? materialEl.value.trim() : '';
    if (material) body.material = material;
    return Object.assign(body, extra || {});
}

function _airbagSetBusy(btn, busy, busyText) {
    if (!btn) return;
    if (busy) {
        btn.dataset.orig = btn.innerHTML;
        btn.disabled = true;
        btn.classList.add('opacity-60', 'cursor-not-allowed');
        btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> ${busyText || '处理中...'}`;
    } else {
        btn.disabled = false;
        btn.classList.remove('opacity-60', 'cursor-not-allowed');
        if (btn.dataset.orig) btn.innerHTML = btn.dataset.orig;
    }
}

// ---------------------------------------------------------------------
// 数据加载
// ---------------------------------------------------------------------
async function _airbagLoadTemplateInfo() {
    const data = await _airbagApi('GET', 'template');
    if (data.status === 'success') _airbagTemplateInfo = data.data;
    const el = document.getElementById('airbag-template-info');
    if (el) el.innerHTML = _airbagTemplateInfoCard();
}

function _airbagTemplateInfoCard() {
    const t = _airbagTemplateInfo || { source: 'missing', uploaded_at: null, filename: '' };
    const map = {
        uploaded: { label: '用户上传模板', icon: 'fa-upload', cls: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300' },
        builtin: { label: '内置模板', icon: 'fa-box', cls: 'bg-sky-50 text-sky-700 dark:bg-sky-900/30 dark:text-sky-300' },
        missing: { label: '未找到模板', icon: 'fa-triangle-exclamation', cls: 'bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-300' },
    }[t.source] || { label: '未知', icon: 'fa-circle-question', cls: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300' };
    return `
        <div class="flex items-center gap-3 p-3 rounded-xl bg-slate-50 dark:bg-white/5 border border-slate-200 dark:border-white/10">
            <span class="px-2.5 py-1 rounded-lg text-xs font-medium ${map.cls}"><i class="fas ${map.icon} mr-1"></i>${map.label}</span>
            <div class="min-w-0 flex-1">
                <p class="text-xs text-slate-600 dark:text-slate-300 truncate">${_airbagEscape(t.filename || '')}</p>
                <p class="text-[10px] text-slate-400">${t.uploaded_at ? '上传时间：' + _airbagEscape(t.uploaded_at) : '模板文件位于技能目录 templates/'}</p>
            </div>
        </div>
    `;
}

async function _airbagLoadConfigs() {
    const [rules, capacity, teams, merge] = await Promise.all([
        _airbagApi('GET', 'config'),
        _airbagApi('GET', 'capacity'),
        _airbagApi('GET', 'teams'),
        _airbagApi('GET', 'merge-rules'),
    ]);
    _airbagRules = rules.status === 'success' ? rules.data : {};
    _airbagCapacity = capacity.status === 'success' ? capacity.data : {};
    _airbagTeams = teams.status === 'success' ? teams.data : [];
    _airbagMerge = merge.status === 'success' ? merge.data : [];
    if (_airbagActiveTab === 'rules') {
        const c = document.getElementById('airbag-wb-content');
        if (c) c.innerHTML = _airbagRenderRulesTab();
    }
    if (_airbagActiveTab === 'capacity') {
        const c = document.getElementById('airbag-wb-content');
        if (c) c.innerHTML = _airbagRenderCapacityTab();
    }
    if (_airbagActiveTab === 'teams') {
        const c = document.getElementById('airbag-wb-content');
        if (c) c.innerHTML = _airbagRenderTeamsTab();
    }
    if (_airbagActiveTab === 'merge') {
        const c = document.getElementById('airbag-wb-content');
        if (c) c.innerHTML = _airbagRenderMergeTab();
    }
}

// 配置数据缓存（从后端加载，供渲染/编辑）
let _airbagRules = {};
let _airbagCapacity = {};
let _airbagTeams = [];
let _airbagMerge = [];

// ---------------------------------------------------------------------
// Tab：预估出货量（排产1）
// ---------------------------------------------------------------------
function _airbagRenderDemandsTab() {
    const d = _airbagDemands;
    const has = !!(d && d.exists);
    const preview = _airbagRenderDemandsPreview();
    return `
        <div class="space-y-4 max-w-5xl">
            ${_airbagDemandsLoading ? `
            <!-- 导入/加载进度提示 -->
            <div class="rounded-2xl border border-sky-200 dark:border-sky-900/40 bg-sky-50/70 dark:bg-sky-900/10 p-4 text-sm text-sky-700 dark:text-sky-300">
                <i class="fas fa-spinner fa-spin mr-2"></i>正在导入/加载预估出货量表，请稍候...
            </div>` : ''}
            ${_airbagDemandsNotice ? `
            <!-- 导入成功提示（含条数统计） -->
            <div class="rounded-2xl border border-emerald-200 dark:border-emerald-900/40 bg-emerald-50/70 dark:bg-emerald-900/10 p-4 text-sm text-emerald-700 dark:text-emerald-300">
                <i class="fas fa-circle-check mr-2"></i>${_airbagEscape(_airbagDemandsNotice)}
            </div>` : ''}
            <!-- 状态与上传 -->
            <div class="rounded-2xl border border-slate-200 dark:border-white/10 p-5">
                <div class="flex items-center justify-between mb-4 flex-wrap gap-2">
                    <h4 class="text-sm font-semibold text-slate-700 dark:text-slate-200 flex items-center gap-2">
                        <i class="fas fa-boxes-stacked" style="color:${_AIRBAG_ACCENT}"></i>预估出货量（排产1）
                    </h4>
                    ${has
                        ? `<span class="px-2.5 py-1 rounded-lg text-xs font-medium bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300"><i class="fas fa-circle-check mr-1"></i>已导入 ${_airbagEscape(d.uploaded_at)}</span>`
                        : '<span class="px-2.5 py-1 rounded-lg text-xs font-medium bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400"><i class="fas fa-circle-info mr-1"></i>未导入</span>'}
                </div>
                <div class="flex items-center gap-3 flex-wrap">
                    <label class="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium cursor-pointer bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors">
                        <i class="fas fa-upload"></i>
                        ${has ? '重新导入预估出货量表' : '导入预估出货量表'}
                        <input type="file" id="airbag-demands-file" accept=".xlsx,.xls" class="hidden">
                    </label>
                    <span class="text-[11px] text-slate-400">支持将《预估出货量模板.xlsx》（sheet1）或出货需求表另存为独立 Excel 后导入</span>
                </div>
                <div class="mt-3 rounded-xl bg-sky-50/70 dark:bg-sky-900/10 border border-sky-200 dark:border-sky-900/40 p-3 text-[11px] leading-relaxed text-slate-600 dark:text-slate-300">
                    <i class="fas fa-circle-info mr-1" style="color:${_AIRBAG_ACCENT}"></i>
                    执行排产时将<strong>优先读取此表</strong>作为出货需求数据（排产1），未导入时排产会提示先导入出货量表。
                    <br>导入时自动过滤前 3 周均无出货的记录（按表头日期识别周列），仅导入至最后一列表头非空的列（其后空列不导入）。
                </div>
            </div>
            ${_airbagDemandsError ? `
            <!-- 加载/导入失败提示（显眼报错，避免客户误以为已导入） -->
            <div class="rounded-2xl border border-red-200 dark:border-red-900/50 bg-red-50 dark:bg-red-900/20 p-4 text-sm text-red-600 dark:text-red-300">
                <i class="fas fa-triangle-exclamation mr-2"></i>预估出货量表读取失败：${_airbagEscape(_airbagDemandsError)}
            </div>` : ''}
            <!-- 内容预览 -->
            <div id="airbag-demands-preview" class="rounded-2xl border border-slate-200 dark:border-white/10 p-5">
                ${preview}
            </div>
        </div>
    `;
}

function _airbagRenderDemandsPreview() {
    const d = _airbagDemands;
    if (!d || !d.exists) {
        return `
            <div class="text-center py-10">
                <i class="fas fa-box-open text-3xl text-slate-300 dark:text-slate-600 mb-3"></i>
                <p class="text-sm text-slate-400">尚未导入预估出货量表</p>
                <p class="text-[11px] text-slate-300 dark:text-slate-600 mt-1">导入后此处将显示排产1 表内容与条数统计</p>
            </div>`;
    }
    const sheet = (d.sheets || [])[0];
    if (!sheet) {
        return `<div class="text-center py-8 text-sm text-slate-400">导入的文件中未找到「排产1」/「sheet1」sheet，请确认文件格式正确</div>`;
    }
    // 条数统计：共 N 条 = 总行-表头；过滤 K 条 = 前3周无出货；有效 M 条
    const total = Math.max(0, (sheet.max_row || 1) - 1);
    const filtered = sheet.filtered_rows || 0;
    const valid = Math.max(0, total - filtered);
    // 全量数据已加载时逐批渲染（每次 200 行），避免一次性渲染大表格卡顿
    const allRows = sheet.rows || [];
    const renderLimit = _airbagDemandsFull ? Math.min(allRows.length, _airbagDemandsShown + 1) : allRows.length;
    const shownData = allRows.slice(0, renderLimit);
    const shown = Math.max(0, shownData.length - 1);
    const headCells = (shownData[0] || []).map((c, i) =>
        `<th class="px-2 py-1.5 text-[10px] text-slate-400 font-medium border-b border-slate-200 dark:border-white/10 whitespace-nowrap">${_airbagEscape(c) || `<span class="text-slate-300">C${i + 1}</span>`}</th>`).join('');
    const bodyRows = shownData.slice(1).map((row, ri) => {
        const cells = row.map(c => `<td class="px-2 py-1 text-[11px] text-slate-600 dark:text-slate-300 whitespace-nowrap max-w-[200px] truncate">${_airbagEscape(c)}</td>`).join('');
        return `<tr class="border-b border-slate-50 dark:border-white/5 hover:bg-slate-50/60 dark:hover:bg-white/[0.03]">
            <td class="px-2 py-1 text-[10px] text-slate-300 text-right w-8">${ri + 2}</td>${cells}
        </tr>`;
    }).join('');
    // 顶部按钮：未全量→「全量显示」；已全量但未渲染完→「加载更多」；否则→已显示全部
    let headerRight;
    if (_airbagDemandsLoading) {
        headerRight = `<span class="text-[10px] text-slate-400 flex items-center gap-1"><i class="fas fa-spinner fa-spin"></i>加载中...</span>`;
    } else if (sheet.truncated && !_airbagDemandsFull) {
        headerRight = `<button id="airbag-demands-full-btn" class="px-2.5 py-1 rounded-lg text-[10px] font-medium bg-teal-50 text-teal-600 dark:bg-teal-900/30 dark:text-teal-300 hover:bg-teal-100 dark:hover:bg-teal-900/50 transition-colors flex items-center gap-1">
                <i class="fas fa-expand"></i>全量显示（有效 ${valid} 条）
           </button>`;
    } else if (allRows.length - shownData.length > 0) {
        headerRight = `<button id="airbag-demands-more-btn" class="px-2.5 py-1 rounded-lg text-[10px] font-medium bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors flex items-center gap-1">
                <i class="fas fa-chevron-down"></i>加载更多（剩余 ${allRows.length - shownData.length} 条）
           </button>`;
    } else {
        headerRight = `<span class="text-[10px] text-slate-400">已显示全部 ${valid} 条有效记录</span>`;
    }
    // 周列识别提示（导入是否执行了前3周过滤）
    const weekCols = sheet.week_cols || [];
    const weekNote = weekCols.length
        ? `已按前3周（第 ${weekCols.slice(0, 3).join('、')} 列）识别并过滤无出货记录`
        : `<span class="text-amber-500">未识别到周出货量列，未执行前3周过滤（请检查表头日期）</span>`;
    return `
        <div class="flex items-center justify-between mb-3">
            <h4 class="text-xs font-semibold text-slate-600 dark:text-slate-300 flex items-center gap-2">
                <i class="fas fa-table" style="color:${_AIRBAG_ACCENT}"></i>排产1 表内容（共 ${total} 条：有效 ${valid} 条，过滤 ${filtered} 条）
            </h4>
            ${headerRight}
        </div>
        <div class="overflow-x-auto overflow-y-auto rounded-xl border border-slate-200 dark:border-white/10" style="max-height:60vh">
            <table class="w-full text-left border-collapse">
                <thead class="sticky top-0 bg-slate-50 dark:bg-slate-900 z-10">
                    <tr><th class="px-2 py-1.5 text-[10px] text-slate-300 w-8 border-b border-slate-200 dark:border-white/10">#</th>${headCells}</tr>
                </thead>
                <tbody>${bodyRows}</tbody>
            </table>
        </div>
        ${shown < valid ? `<p class="text-[10px] text-slate-400 mt-2">当前显示前 ${shown} 条，可点击右上角「加载更多」继续查看全部有效记录</p>` : ''}
        <p class="text-[10px] text-slate-400 mt-1">${weekNote}</p>
    `;
}

async function _airbagLoadDemands(full) {
    _airbagDemandsLoading = true;
    if (_airbagActiveTab === 'demands') {
        const c = document.getElementById('airbag-wb-content');
        if (c) c.innerHTML = _airbagRenderDemandsTab();
    }
    _airbagBindDemandsEvents();
    const res = await _airbagApi('GET', 'demands' + (full ? '?full=1' : ''));
    _airbagDemandsLoading = false;
    if (res.status === 'success') {
        _airbagDemands = res.data;
        _airbagDemandsFull = !!full;
        _airbagDemandsShown = 200; // 全量数据按批渲染，从 200 行开始
        _airbagDemandsError = '';
    } else {
        _airbagDemandsError = res.message || '预估出货量表加载失败';
    }
    if (_airbagActiveTab === 'demands') {
        const c = document.getElementById('airbag-wb-content');
        if (c) c.innerHTML = _airbagRenderDemandsTab();
    }
    _airbagBindDemandsEvents();
}

// 绑定预估出货量表页事件（文件导入 + 全量显示 + 加载更多按钮）
function _airbagBindDemandsEvents() {
    const fileEl = document.getElementById('airbag-demands-file');
    if (fileEl) fileEl.addEventListener('change', (e) => {
        const file = e.target && e.target.files && e.target.files[0];
        if (file) _airbagHandleDemandsUpload(file);
        e.target.value = '';
    });
    const fullBtn = document.getElementById('airbag-demands-full-btn');
    if (fullBtn) fullBtn.addEventListener('click', () => _airbagLoadDemands(true));
    const moreBtn = document.getElementById('airbag-demands-more-btn');
    if (moreBtn) moreBtn.addEventListener('click', () => {
        _airbagDemandsShown += 300; // 每批追加 300 行
        const c = document.getElementById('airbag-wb-content');
        if (c) c.innerHTML = _airbagRenderDemandsTab();
        _airbagBindDemandsEvents();
    });
}

// 预估出货量表上传（含进度提示与条数统计反馈）
async function _airbagHandleDemandsUpload(file) {
    if (!file) return;
    _airbagDemandsLoading = true;
    _airbagDemandsNotice = '';
    _airbagDemandsError = '';
    const c = document.getElementById('airbag-wb-content');
    if (c) c.innerHTML = _airbagRenderDemandsTab();
    try {
        const content = await _airbagFileToBase64(file);
        const res = await _airbagApi('POST', 'demands', {
            files: { excel: { filename: file.name, content, is_base64: true } },
        });
        if (res.status === 'success') {
            _airbagDemands = res.data;
            _airbagDemandsFull = false; // 新导入后回到默认前 200 行展示
            _airbagDemandsShown = 200;
            _airbagDemandsNotice = res.message || '预估出货量表导入成功';
            showToast(res.message || '预估出货量表已保存', 'success');
        } else {
            _airbagDemandsError = res.message || '导入失败';
            showToast(res.message || '导入失败', 'error');
        }
    } catch (err) {
        _airbagDemandsError = '导入异常: ' + err.message;
        showToast('导入异常: ' + err.message, 'error');
    } finally {
        _airbagDemandsLoading = false;
    }
    if (c) c.innerHTML = _airbagRenderDemandsTab();
    _airbagBindDemandsEvents();
}

// 加载模板各 sheet 内容预览（有上传文件时预览上传文件，否则默认模板）
async function _airbagLoadTemplateContent() {
    const body = {};
    const uploaded = !!_airbagUploadFile;
    if (_airbagUploadFile) body.files = { excel: _airbagUploadFile };
    const titleEl = document.getElementById('airbag-template-title');
    const subEl = document.getElementById('airbag-template-sub');
    const bodyEl = document.getElementById('airbag-template-body');
    const dsEl = document.getElementById('airbag-data-source-status');
    // 导入进度提示（仅上传文件时需要）
    if (uploaded) {
        const hint = `<div class="mt-3 rounded-xl border border-sky-200 dark:border-sky-900/40 bg-sky-50/70 dark:bg-sky-900/10 p-3 text-sm text-sky-700 dark:text-sky-300">
            <i class="fas fa-spinner fa-spin mr-2"></i>正在导入排产模板（${_airbagEscape(_airbagUploadFile.filename)}），数据较多时请稍候...</div>`;
        if (bodyEl) bodyEl.innerHTML = `
            <div class="flex items-center gap-2 py-5 text-sm text-slate-500 dark:text-slate-400">
                <i class="fas fa-spinner fa-spin" style="color:${_AIRBAG_ACCENT}"></i>正在导入模板内容，请稍候...
            </div>`;
        if (dsEl) dsEl.innerHTML = hint;
    }
    const res = await _airbagApi('POST', 'template-content', body);
    if (res.status !== 'success') {
        if (bodyEl) bodyEl.innerHTML = `
            <div class="text-center py-8 text-sm text-slate-400">
                <i class="fas fa-triangle-exclamation mr-2"></i>模板内容加载失败：${_airbagEscape(res.message || '未知错误')}
            </div>`;
        if (dsEl) dsEl.innerHTML = `
            <div class="mt-3 rounded-xl border border-red-200 dark:border-red-900/50 bg-red-50 dark:bg-red-900/20 p-3 text-sm text-red-600 dark:text-red-300">
                <i class="fas fa-triangle-exclamation mr-2"></i>排产模板读取失败：${_airbagEscape(res.message || '未知错误')}
            </div>`;
        return;
    }
    _airbagTemplateSheets = res.data.sheets || [];
    _airbagActiveSheet = _airbagTemplateSheets.length ? _airbagTemplateSheets[0].name : '';
    const sheet = _airbagTemplateSheets[0] || {};
    if (titleEl) titleEl.innerHTML = uploaded
        ? '<i class="fas fa-circle-check" style="color:' + _AIRBAG_ACCENT + '"></i>模板导入结果'
        : '<i class="fas fa-table" style="color:' + _AIRBAG_ACCENT + '"></i>排产模板内容（排产2）';
    if (subEl) subEl.textContent = uploaded
        ? `本次导入成功，共 ${sheet.max_row || 0} 行 × ${sheet.max_col || 0} 列`
        : '';
    if (bodyEl) bodyEl.innerHTML = _airbagRenderTemplateContent();
    if (uploaded && dsEl) dsEl.innerHTML = `
        <div class="mt-3 rounded-xl border border-emerald-200 dark:border-emerald-900/40 bg-emerald-50/70 dark:bg-emerald-900/10 p-3 text-sm text-emerald-700 dark:text-emerald-300">
            <i class="fas fa-circle-check mr-2"></i>排产模板导入成功：共 ${sheet.max_row || 0} 行 × ${sheet.max_col || 0} 列
        </div>`;
    _airbagBindSheetTabs();
}

// 执行排产后：下方更新为本次排产结果摘要（不再渲染大表格）
function _airbagSetResultSheets(data) {
    _airbagTemplateSheets = (data && data.result_sheets) || [];
    _airbagActiveSheet = _airbagTemplateSheets.length ? _airbagTemplateSheets[0].name : '';
    const titleEl = document.getElementById('airbag-template-title');
    const subEl = document.getElementById('airbag-template-sub');
    const bodyEl = document.getElementById('airbag-template-body');
    if (titleEl) titleEl.innerHTML = '<i class="fas fa-circle-check" style="color:' + _AIRBAG_ACCENT + '"></i>本次排产结果';
    if (subEl) subEl.textContent = '';
    if (bodyEl) bodyEl.innerHTML = (data && data.written_rows != null)
        ? `<div class="text-sm text-slate-600 dark:text-slate-300 leading-relaxed">
               <i class="fas fa-file-excel mr-1" style="color:${_AIRBAG_ACCENT}"></i>
               排产完成，已写入排产2 表 <b>${data.written_rows}</b> 行。结果文件与分析报告见上方「执行结果」下载链接。
           </div>`
        : _airbagRenderTemplateContent();
    _airbagBindSheetTabs();
}

// 绑定模板内容 sheet 切换（摘要模式无 sheet 切换按钮，保留兼容）
function _airbagBindSheetTabs() {
    document.querySelectorAll('.airbag-sheet-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            _airbagActiveSheet = btn.dataset.sheet;
            const el = document.getElementById('airbag-template-body');
            if (el) el.innerHTML = _airbagRenderTemplateContent();
            _airbagBindSheetTabs();
        });
    });
}

// 渲染模板内容摘要（不逐行展示大表格，避免数据量大时卡顿）
function _airbagRenderTemplateContent() {
    if (!_airbagTemplateSheets.length) {
        return `<div class="text-center py-8 text-sm text-slate-400">模板中没有可预览的 sheet</div>`;
    }
    const parts = _airbagTemplateSheets.map(s =>
        `<span class="font-medium">${_airbagEscape(s.name)}</span>（${s.max_row} 行 × ${s.max_col} 列）`);
    const truncatedNote = _airbagTemplateSheets.some(s => s.truncated)
        ? '<span class="text-[11px] text-slate-400 ml-2">表内容较大，仅统计行列信息，不逐行展示</span>' : '';
    return `
        <div class="text-sm text-slate-600 dark:text-slate-300 leading-relaxed flex items-center gap-1 flex-wrap">
            <i class="fas fa-table mr-1" style="color:${_AIRBAG_ACCENT}"></i>
            ${parts.join('、')}
            ${truncatedNote}
        </div>`;
}

// 自动从模板解析配置（空配置时填充显示，来源标记 template）
async function _airbagAutoFillFromTemplate() {
    const res = await _airbagApi('POST', 'parse', {});
    if (res.status !== 'success') return;
    const d = res.data;
    let changed = false;
    if ((!_airbagCapacity || Object.keys(_airbagCapacity).length === 0) && d.capacity && Object.keys(d.capacity).length) {
        _airbagCapacity = d.capacity;
        _airbagConfigSource.capacity = 'template';
        changed = true;
    }
    if ((!_airbagTeams || _airbagTeams.length === 0) && d.teams && d.teams.length) {
        _airbagTeams = d.teams;
        _airbagConfigSource.teams = 'template';
        changed = true;
    }
    if ((!_airbagMerge || _airbagMerge.length === 0) && d.merge_rules && d.merge_rules.length) {
        _airbagMerge = d.merge_rules;
        _airbagConfigSource.merge = 'template';
        changed = true;
    }
    if (!changed) return;
    const content = document.getElementById('airbag-wb-content');
    if (!content) return;
    if (_airbagActiveTab === 'capacity') content.innerHTML = _airbagRenderCapacityTab();
    else if (_airbagActiveTab === 'teams') content.innerHTML = _airbagRenderTeamsTab();
    else if (_airbagActiveTab === 'merge') content.innerHTML = _airbagRenderMergeTab();
}

// ---------------------------------------------------------------------
// Tab 1：执行排产
// ---------------------------------------------------------------------
function _airbagRenderRunTab() {
    return `
        <div class="space-y-6">
            <!-- 模板与上传 -->
            <div class="rounded-2xl border border-slate-200 dark:border-white/10 p-5">
                <div class="flex items-center justify-between mb-4">
                    <h4 class="text-sm font-semibold text-slate-700 dark:text-slate-200 flex items-center gap-2">
                        <i class="fas fa-file-excel" style="color:${_AIRBAG_ACCENT}"></i>排产数据源
                    </h4>
                    <span id="airbag-template-info"></span>
                </div>
                <div class="flex items-center gap-3 flex-wrap">
                    <label class="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium cursor-pointer bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors">
                        <i class="fas fa-upload"></i>
                        ${_airbagUploadFile ? '已选择：' + _airbagEscape(_airbagUploadFile.filename) : '上传排产模板（可选）'}
                        <input type="file" id="airbag-file-input" accept=".xlsx,.xls" class="hidden">
                    </label>
                    ${_airbagUploadFile ? `<button id="airbag-file-clear" class="text-xs text-slate-400 hover:text-red-500 transition-colors"><i class="fas fa-xmark mr-1"></i>取消选择</button>` : ''}
                    <span class="text-[11px] text-slate-400">未上传时使用当前默认模板</span>
                </div>
                <div id="airbag-data-source-status"></div>
                <div class="mt-4 grid grid-cols-1 md:grid-cols-2 gap-3">
                    <div>
                        <label class="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">仅排产指定 SAP 物料号（可选）</label>
                        <input id="airbag-material" type="text" placeholder="留空则排产全部需求" class="w-full px-3 py-2 rounded-xl border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-sm text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40">
                    </div>
                    <div class="flex items-end gap-2">
                        <button id="airbag-parse-btn" class="px-4 py-2 rounded-xl text-sm font-medium bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors flex items-center gap-2">
                            <i class="fas fa-magnifying-glass"></i>解析预览
                        </button>
                        <button id="airbag-run-btn" class="px-5 py-2 rounded-xl text-sm font-medium text-white transition-colors flex items-center gap-2" style="background:${_AIRBAG_ACCENT}">
                            <i class="fas fa-wand-magic-sparkles"></i>执行排产
                        </button>
                    </div>
                </div>
            </div>

            <!-- 错误提示 -->
            <div id="airbag-run-error" class="hidden rounded-xl border border-red-200 dark:border-red-900/50 bg-red-50 dark:bg-red-900/20 p-4 text-sm text-red-600 dark:text-red-300 whitespace-pre-wrap"></div>

            <!-- 解析结果 -->
            <div id="airbag-parse-result"></div>
            <!-- 执行结果 -->
            <div id="airbag-run-result"></div>
            <!-- 模板内容预览（执行排产后更新为最新排产2 结果） -->
            <div id="airbag-template-content" class="rounded-2xl border border-slate-200 dark:border-white/10 p-5">
                <div class="flex items-center justify-between mb-4">
                    <h4 id="airbag-template-title" class="text-sm font-semibold text-slate-700 dark:text-slate-200 flex items-center gap-2">
                        <i class="fas fa-table" style="color:${_AIRBAG_ACCENT}"></i>排产模板内容（排产2）
                    </h4>
                    <span id="airbag-template-sub" class="text-[10px] text-slate-400">排产2 全部内容</span>
                </div>
                <div id="airbag-template-body">
                    <div class="text-center py-8 text-sm text-slate-400"><i class="fas fa-spinner fa-spin mr-2"></i>模板内容加载中...</div>
                </div>
            </div>
        </div>
    `;
}

function _airbagBindTabEvents(tabId) {
    if (tabId === 'run') {
        // 切回执行排产 Tab 时，若模板内容已加载则直接重渲染正文（避免退回"加载中"占位）
        const bodyEl = document.getElementById('airbag-template-body');
        if (bodyEl && _airbagTemplateSheets.length > 0) {
            bodyEl.innerHTML = _airbagRenderTemplateContent();
            _airbagBindSheetTabs();
        }
        const fileInput = document.getElementById('airbag-file-input');
        if (fileInput) fileInput.addEventListener('change', async (e) => {
            const file = e.target.files && e.target.files[0];
            if (!file) return;
            try {
                const content = await _airbagFileToBase64(file);
                _airbagUploadFile = { filename: file.name, content, is_base64: true };
            } catch (err) {
                showToast('文件读取失败: ' + err.message, 'error');
                return;
            }
            _airbagSwitchTab('run'); // 先重绘显示文件名/清除按钮
            await _airbagLoadTemplateContent(); // 再加载内容（含"正在导入/导入成功"提示，避免被重绘覆盖）
        });
        const clearBtn = document.getElementById('airbag-file-clear');
        if (clearBtn) clearBtn.addEventListener('click', () => {
            _airbagUploadFile = null;
            _airbagSwitchTab('run'); // 先重绘回到默认模板状态
            _airbagLoadTemplateContent(); // 再加载默认模板内容
        });
        const parseBtn = document.getElementById('airbag-parse-btn');
        if (parseBtn) parseBtn.addEventListener('click', _airbagParse);
        const runBtn = document.getElementById('airbag-run-btn');
        if (runBtn) runBtn.addEventListener('click', _airbagRun);
    } else if (tabId === 'demands') {
        _airbagBindDemandsEvents(); // 绑定文件导入 + 全量显示按钮（渲染后需重新绑定）
    } else if (tabId === 'rules') {
        const addBtn = document.getElementById('airbag-rules-add');
        if (addBtn) addBtn.addEventListener('click', _airbagAddRule);
        const saveBtn = document.getElementById('airbag-rules-save');
        if (saveBtn) saveBtn.addEventListener('click', _airbagSaveRules);
        _airbagBindRuleRowActions(document.getElementById('airbag-rules-tbody'));
    } else if (tabId === 'capacity') {
        const addBtn = document.getElementById('airbag-capacity-add');
        if (addBtn) addBtn.addEventListener('click', () => _airbagAddCapacityRow());
        const syncBtn = document.getElementById('airbag-capacity-sync');
        if (syncBtn) syncBtn.addEventListener('click', () => document.getElementById('airbag-capacity-file').click());
        const fileEl = document.getElementById('airbag-capacity-file');
        if (fileEl) fileEl.addEventListener('change', (e) => _airbagSyncFromExcel(e, 'capacity'));
        const saveBtn = document.getElementById('airbag-capacity-save');
        if (saveBtn) saveBtn.addEventListener('click', _airbagSaveCapacity);
    } else if (tabId === 'teams') {
        const addBtn = document.getElementById('airbag-teams-add');
        if (addBtn) addBtn.addEventListener('click', () => _airbagAddTeamRow());
        const syncBtn = document.getElementById('airbag-teams-sync');
        if (syncBtn) syncBtn.addEventListener('click', () => document.getElementById('airbag-teams-file').click());
        const fileEl = document.getElementById('airbag-teams-file');
        if (fileEl) fileEl.addEventListener('change', (e) => _airbagSyncFromExcel(e, 'teams'));
        const saveBtn = document.getElementById('airbag-teams-save');
        if (saveBtn) saveBtn.addEventListener('click', _airbagSaveTeams);
    } else if (tabId === 'merge') {
        const addBtn = document.getElementById('airbag-merge-add');
        if (addBtn) addBtn.addEventListener('click', () => _airbagAddMergeRow());
        const syncBtn = document.getElementById('airbag-merge-sync');
        if (syncBtn) syncBtn.addEventListener('click', () => document.getElementById('airbag-merge-file').click());
        const fileEl = document.getElementById('airbag-merge-file');
        if (fileEl) fileEl.addEventListener('change', (e) => _airbagSyncFromExcel(e, 'merge'));
        const saveBtn = document.getElementById('airbag-merge-save');
        if (saveBtn) saveBtn.addEventListener('click', _airbagSaveMerge);
    } else if (tabId === 'template') {
        const uploadBtn = document.getElementById('airbag-template-upload');
        if (uploadBtn) uploadBtn.addEventListener('click', () => document.getElementById('airbag-template-file').click());
        const fileEl = document.getElementById('airbag-template-file');
        if (fileEl) fileEl.addEventListener('change', _airbagUploadTemplate);
        const resetBtn = document.getElementById('airbag-template-reset');
        if (resetBtn) resetBtn.addEventListener('click', _airbagResetTemplate);
    }
}

// 解析预览
async function _airbagParse() {
    if (_airbagBusy) return;
    const btn = document.getElementById('airbag-parse-btn');
    const errEl = document.getElementById('airbag-run-error');
    const parseEl = document.getElementById('airbag-parse-result');
    const runEl = document.getElementById('airbag-run-result');
    _airbagHideError(errEl);
    if (parseEl) parseEl.innerHTML = '';
    if (runEl) runEl.innerHTML = '';
    _airbagBusy = true;
    _airbagSetBusy(btn, true, '解析中...');
    try {
        const body = await _airbagBuildBody();
        const data = await _airbagApi('POST', 'parse', body);
        if (data.status !== 'success') {
            _airbagShowError(errEl, data.message || '解析失败');
            return;
        }
        _airbagParseResult = data.data;
        if (parseEl) parseEl.innerHTML = _airbagRenderParseResult(data.data);
    } catch (err) {
        _airbagShowError(errEl, '请求异常: ' + err.message);
    } finally {
        _airbagBusy = false;
        _airbagSetBusy(btn, false);
    }
}

// 执行排产
async function _airbagRun() {
    if (_airbagBusy) return;
    const btn = document.getElementById('airbag-run-btn');
    const errEl = document.getElementById('airbag-run-error');
    const runEl = document.getElementById('airbag-run-result');
    _airbagHideError(errEl);
    if (runEl) runEl.innerHTML = '';
    _airbagBusy = true;
    _airbagSetBusy(btn, true, '排产中...');
    try {
        const body = await _airbagBuildBody();
        const data = await _airbagApi('POST', 'run', body);
        if (data.status !== 'success') {
            _airbagShowError(errEl, data.message || '执行失败');
            return;
        }
        if (runEl) runEl.innerHTML = _airbagRenderRunResult(data.data);
        _airbagBindSendChat(document.getElementById('airbag-send-chat-btn'), _airbagRunResultText(data.data));
        // 下方更新为本次排产结果摘要（不再渲染大表格）
        if (data.data) {
            _airbagSetResultSheets(data.data);
        } else {
            _airbagLoadTemplateContent(); // 后端未返回时恢复模板内容展示
        }
    } catch (err) {
        _airbagShowError(errEl, '请求异常: ' + err.message);
    } finally {
        _airbagBusy = false;
        _airbagSetBusy(btn, false);
    }
}

// 生成回传聊天的排产结果文本（明细以 JSON 文件链接提供，避免正文过长）
function _airbagRunResultText(data) {
    const skipped = data.skipped || [];
    const warnings = data.warnings || [];
    const fname = (data.output || '').split(/[\\/]/).pop();
    const results = data.results || [];
    // 跳过需求过多时只展示前 10 条，避免消息过长
    const skippedTxt = skipped.length
        ? skipped.slice(0, 10).map(s => s.sap + '(' + s.reason + ')').join('、') + (skipped.length > 10 ? ` 等 ${skipped.length} 条` : '')
        : '';
    const lines = [
        '【气袋排产结果】',
        `排产窗口：${data.window ? data.window.start + ' ~ ' + data.window.end + '（' + data.window.days + '天）' : '-'}`,
        `已排产物料 ${results.length} 个，写入排产2 表 ${data.written_rows || 0} 行`,
        data.detail_path
            ? `📋 排产明细（${results.length} 条完整结构化结果）：[${_airbagEscape(data.detail_name || '排产明细.json')}](/api/file?path=${encodeURIComponent(data.detail_path)})`
            : `【排产明细】共 ${results.length} 条（明细文件生成失败，请下载排产结果 Excel 查看）`,
        skippedTxt ? `【跳过需求】${skippedTxt}` : '无跳过需求',
        warnings.length ? `【引擎提示】${warnings[0]}` : '',
        `📊 排产分析报告：[${_airbagEscape(data.report_name || '排产分析报告.html')}](/api/file?path=${encodeURIComponent(data.report_path)})`,
        `📁 排产结果：[${fname}](/api/file?path=${encodeURIComponent(data.output)})`,
    ].filter(Boolean).join('\n');
    return lines;
}

function _airbagShowError(el, msg) {
    if (!el) return;
    el.textContent = msg;
    el.classList.remove('hidden');
}

function _airbagHideError(el) {
    if (!el) return;
    el.classList.add('hidden');
    el.textContent = '';
}

// 排产窗口卡片
function _airbagWindowCard(windowInfo) {
    if (!windowInfo) return '';
    return `
        <div class="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
            <div class="rounded-xl bg-slate-50 dark:bg-white/5 p-3 border border-slate-200 dark:border-white/10">
                <p class="text-[10px] text-slate-400 mb-1">排产窗口起始</p>
                <p class="text-sm font-semibold text-slate-700 dark:text-slate-200">${_airbagEscape(windowInfo.start)}</p>
            </div>
            <div class="rounded-xl bg-slate-50 dark:bg-white/5 p-3 border border-slate-200 dark:border-white/10">
                <p class="text-[10px] text-slate-400 mb-1">窗口结束</p>
                <p class="text-sm font-semibold text-slate-700 dark:text-slate-200">${_airbagEscape(windowInfo.end)}</p>
            </div>
            <div class="rounded-xl bg-slate-50 dark:bg-white/5 p-3 border border-slate-200 dark:border-white/10">
                <p class="text-[10px] text-slate-400 mb-1">窗口天数</p>
                <p class="text-sm font-semibold text-slate-700 dark:text-slate-200">${_airbagNum(windowInfo.days)} 天</p>
            </div>
            <div class="rounded-xl bg-slate-50 dark:bg-white/5 p-3 border border-slate-200 dark:border-white/10">
                <p class="text-[10px] text-slate-400 mb-1">备货起始</p>
                <p class="text-sm font-semibold text-slate-700 dark:text-slate-200">${_airbagEscape(windowInfo.stockup_start)}</p>
            </div>
            <div class="rounded-xl bg-slate-50 dark:bg-white/5 p-3 border border-slate-200 dark:border-white/10">
                <p class="text-[10px] text-slate-400 mb-1">备货提前天数</p>
                <p class="text-sm font-semibold text-slate-700 dark:text-slate-200">${_airbagNum(windowInfo.stockup_days)} 天</p>
            </div>
        </div>
    `;
}

// 需求明细行渲染（parse 与 run 共用）
function _airbagScheduleRows(list) {
    if (!list || list.length === 0) return `<tr><td colspan="7" class="px-4 py-6 text-center text-sm text-slate-400">无可排产需求</td></tr>`;
    return list.map((d, idx) => {
        const linesHtml = (d.lines || []).map(l => {
            const team = (l.team || []).length ? '班组:' + l.team.join('+') : '';
            const conflict = l.conflict ? `<span class="text-amber-500">（冲突:${_airbagEscape(l.conflict)}）</span>` : '';
            const note = l.note ? `<span class="text-slate-400">（${_airbagEscape(l.note)}）</span>` : '';
            return `<div class="text-[11px] py-0.5">
                <span class="font-mono font-medium">${_airbagEscape(l.line)}</span>
                <span class="text-slate-500">早${_airbagNum(l.early_days)}天 + 夜${_airbagNum(l.night_days)}天（产能${_airbagNum(l.capacity)}）</span>
                ${team ? '<span class="text-sky-600 dark:text-sky-400">' + team + '</span>' : ''}
                ${conflict}${note}
            </div>`;
        }).join('') || '<span class="text-slate-400 text-[11px]">未分配产线</span>';

        const badges = [];
        if (d.stockup) badges.push('<span class="px-1.5 py-0.5 rounded bg-teal-50 text-teal-600 dark:bg-teal-900/30 dark:text-teal-300 text-[10px] font-medium">备货</span>');
        if (d.shebian) badges.push('<span class="px-1.5 py-0.5 rounded bg-amber-50 text-amber-600 dark:bg-amber-900/30 dark:text-amber-300 text-[10px] font-medium">设变</span>');
        if (d.trigger_night) badges.push('<span class="px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-600 dark:bg-indigo-900/30 dark:text-indigo-300 text-[10px] font-medium">夜班</span>');

        return `<tr class="border-b border-slate-100 dark:border-white/5 hover:bg-slate-50/60 dark:hover:bg-white/[0.03]">
            <td class="px-3 py-2.5">
                <div class="text-xs font-mono font-semibold text-slate-700 dark:text-slate-200">${_airbagEscape(d.sap)}</div>
                <div class="text-[10px] text-slate-400 mt-0.5">${badges.join('') || '&nbsp;'}</div>
            </td>
            <td class="px-3 py-2.5 text-xs text-slate-500">${_airbagEscape(d.g)}</td>
            <td class="px-3 py-2.5 text-xs text-slate-500">${_airbagEscape(d.o)}</td>
            <td class="px-3 py-2.5 text-xs text-slate-500 text-right">${_airbagNum(d.ay)}</td>
            <td class="px-3 py-2.5 text-xs text-slate-500 text-right">${_airbagNum(d.w1)} / ${_airbagNum(d.w2)}</td>
            <td class="px-3 py-2.5 text-sm font-semibold text-slate-700 dark:text-slate-200 text-right">${_airbagNum(d.need, 2)}</td>
            <td class="px-3 py-2.5 min-w-[260px]">
                ${linesHtml}
                <div class="text-[10px] text-slate-400 mt-1">${_airbagEscape(d.reason || '')}</div>
            </td>
        </tr>`;
    }).join('');
}

// 渲染解析结果
function _airbagRenderParseResult(data) {
    const warnings = data.warnings || [];
    const skipped = data.skipped || [];
    const missing = data.missing_capacity || [];
    return `
        <div class="rounded-2xl border border-slate-200 dark:border-white/10 p-5">
            <h4 class="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-4 flex items-center gap-2">
                <i class="fas fa-magnifying-glass" style="color:${_AIRBAG_ACCENT}"></i>解析预览结果
                <span class="ml-auto text-[10px] font-normal text-slate-400">仅预览，未写入文件</span>
            </h4>
            ${_airbagWindowCard(data.window)}
            <div class="overflow-x-auto">
                <table class="w-full text-left">
                    <thead>
                        <tr class="text-[10px] text-slate-400 uppercase tracking-wide border-b border-slate-200 dark:border-white/10">
                            <th class="px-3 py-2">SAP物料号</th>
                            <th class="px-3 py-2">备货(G)</th>
                            <th class="px-3 py-2">备注(O)</th>
                            <th class="px-3 py-2 text-right">可用库存</th>
                            <th class="px-3 py-2 text-right">W1/W2出货</th>
                            <th class="px-3 py-2 text-right">需排产</th>
                            <th class="px-3 py-2">产线安排</th>
                        </tr>
                    </thead>
                    <tbody>${_airbagScheduleRows(data.demands)}</tbody>
                </table>
            </div>

            ${skipped.length ? `
            <div class="mt-4 rounded-xl border border-amber-200 dark:border-amber-900/40 bg-amber-50/70 dark:bg-amber-900/10 p-4">
                <h5 class="text-xs font-semibold text-amber-700 dark:text-amber-300 mb-2"><i class="fas fa-triangle-exclamation mr-1"></i>跳过需求（${skipped.length}）</h5>
                <div class="flex flex-wrap gap-2">
                    ${skipped.map(s => `<span class="text-[11px] px-2 py-1 rounded-lg bg-white dark:bg-white/5 text-slate-600 dark:text-slate-300">${_airbagEscape(s.sap)}：${_airbagEscape(s.reason)}</span>`).join('')}
                </div>
            </div>` : ''}

            ${missing.length ? `
            <div class="mt-4 rounded-xl border border-red-200 dark:border-red-900/40 bg-red-50/70 dark:bg-red-900/10 p-4">
                <h5 class="text-xs font-semibold text-red-600 dark:text-red-300 mb-2"><i class="fas fa-circle-exclamation mr-1"></i>缺失产能配置（${missing.length}）</h5>
                <div class="flex flex-wrap gap-2">
                    ${missing.map(s => `<span class="text-[11px] px-2 py-1 rounded-lg bg-white dark:bg-white/5 text-slate-600 dark:text-slate-300 font-mono">${_airbagEscape(s)}</span>`).join('')}
                </div>
                <p class="text-[10px] text-red-400 mt-2">请到「产能配置」Tab 维护这些物料的产能，或在「产能配置/班组产线/拼线规则」中从 Excel 同步。</p>
            </div>` : ''}

            ${warnings.length ? `
            <div class="mt-4 rounded-xl border border-sky-200 dark:border-sky-900/40 bg-sky-50/70 dark:bg-sky-900/10 p-4">
                <h5 class="text-xs font-semibold text-sky-700 dark:text-sky-300 mb-2"><i class="fas fa-circle-info mr-1"></i>引擎提示（${warnings.length}）</h5>
                <ul class="space-y-1">
                    ${warnings.map(w => `<li class="text-[11px] text-slate-600 dark:text-slate-300">${_airbagEscape(w)}</li>`).join('')}
                </ul>
            </div>` : ''}
        </div>
    `;
}

// 渲染执行结果（仅摘要；完整排产计划见下方更新的排产2 摘要）
function _airbagRenderRunResult(data) {
    const warnings = data.warnings || [];
    const skipped = data.skipped || [];
    return `
        <div class="rounded-2xl border border-teal-200 dark:border-teal-900/40 p-5" style="background:linear-gradient(180deg, rgba(13,148,136,0.04), transparent)">
            <div class="flex items-center justify-between mb-4 flex-wrap gap-2">
                <h4 class="text-sm font-semibold text-slate-700 dark:text-slate-200 flex items-center gap-2">
                    <i class="fas fa-circle-check" style="color:${_AIRBAG_ACCENT}"></i>执行排产完成
                    <span class="ml-auto text-[10px] font-normal text-slate-400">共写入 ${data.written_rows || 0} 行 · 已排物料 ${(data.results || []).length} 个</span>
                </h4>
            </div>
            <div class="flex flex-wrap items-center gap-2 mb-3">
                <a href="/api/file?path=${encodeURIComponent(data.output)}" target="_blank" download
                   class="px-3 py-1.5 rounded-xl text-xs font-medium bg-white dark:bg-slate-800 border border-slate-200 dark:border-white/10 text-slate-600 dark:text-slate-300 hover:border-teal-400 hover:text-teal-600 dark:hover:text-teal-300 transition-colors flex items-center gap-1.5">
                    <i class="fas fa-file-excel mr-1" style="color:${_AIRBAG_ACCENT}"></i>下载排产结果：${_airbagEscape(data.output.split(/[\\/]/).pop())}
                    <i class="fas fa-download text-[10px] opacity-60"></i>
                </a>
                ${data.report_path ? `
                <a href="/api/file?path=${encodeURIComponent(data.report_path)}" target="_blank" download
                   class="px-3 py-1.5 rounded-xl text-xs font-medium bg-white dark:bg-slate-800 border border-slate-200 dark:border-white/10 text-slate-600 dark:text-slate-300 hover:border-sky-400 hover:text-sky-600 dark:hover:text-sky-300 transition-colors flex items-center gap-1.5">
                    <i class="fas fa-chart-column mr-1" style="color:#0ea5e9"></i>查看排产分析报告
                    <i class="fas fa-arrow-up-right-from-square text-[10px] opacity-60"></i>
                </a>` : ''}
                <button id="airbag-send-chat-btn" class="px-4 py-1.5 rounded-xl text-xs font-medium text-white transition-colors flex items-center gap-1.5" style="background:${_AIRBAG_ACCENT}">
                    <i class="fas fa-paper-plane"></i>回传结果到聊天
                </button>
            </div>
            <p class="text-[10px] text-slate-400 mb-2 break-all font-mono">服务器路径：${_airbagEscape(data.output || '')}</p>
            <p class="text-[11px] text-slate-500 dark:text-slate-400"><i class="fas fa-arrow-down mr-1" style="color:${_AIRBAG_ACCENT}"></i>本次排产结果摘要见下方（完整数据请在下载的排产结果文件中查看）</p>

            ${skipped.length ? `
            <div class="mt-4 rounded-xl border border-amber-200 dark:border-amber-900/40 bg-amber-50/70 dark:bg-amber-900/10 p-4">
                <h5 class="text-xs font-semibold text-amber-700 dark:text-amber-300 mb-2"><i class="fas fa-triangle-exclamation mr-1"></i>跳过需求（${skipped.length}）</h5>
                <div class="flex flex-wrap gap-2">
                    ${skipped.map(s => `<span class="text-[11px] px-2 py-1 rounded-lg bg-white dark:bg-white/5 text-slate-600 dark:text-slate-300">${_airbagEscape(s.sap)}：${_airbagEscape(s.reason)}</span>`).join('')}
                </div>
            </div>` : ''}

            ${warnings.length ? `
            <div class="mt-4 rounded-xl border border-sky-200 dark:border-sky-900/40 bg-sky-50/70 dark:bg-sky-900/10 p-4">
                <h5 class="text-xs font-semibold text-sky-700 dark:text-sky-300 mb-2"><i class="fas fa-circle-info mr-1"></i>引擎提示</h5>
                <ul class="space-y-1">
                    ${warnings.map(w => `<li class="text-[11px] text-slate-600 dark:text-slate-300">${_airbagEscape(w)}</li>`).join('')}
                </ul>
            </div>` : ''}
        </div>
    `;
}

// 绑定执行结果上的回传聊天按钮（渲染后绑定）
function _airbagBindSendChat(btn, text) {
    if (!btn) return;
    btn.addEventListener('click', () => {
        sendMessage(text);
        closeAirbagSchedulingWorkbench();
    });
}

// ---------------------------------------------------------------------
// Tab 2：排产规则（可新增/删除规则参数）
// ---------------------------------------------------------------------
// 已知规则的类型与说明（未知规则按文本处理）
const _AIRBAG_RULE_META = {
    period_count: { label: '取前 N 个出货量非零期间', type: 'number' },
    max_priority: { label: '取优先级前 N 名产线', type: 'number' },
    shebian_keyword: { label: '设变关键字（备注O列）', type: 'text' },
    stockup_keywords: { label: '备货周期关键字（逗号分隔）', type: 'list' },
    stockup_trigger_keywords: { label: '提前备货触发关键字', type: 'list' },
    night_divisor: { label: '夜班判定分母（14=两周日历，10=两周工作日）', type: 'number' },
    night_basic_enabled: { label: '基础夜班触发（早班总量 < 第一周出货量）', type: 'bool' },
    night_enabled: { label: '夜班总开关', type: 'bool' },
    stockup_start_on_monday: { label: '备货从排产窗口内第一个周一开始提前排产', type: 'bool' },
    early_col_parity: { label: '早班列奇偶（odd=奇数列早班 / even=偶数列早班）', type: 'text' },
};

const _AIRBAG_RULE_TYPES = [['number', '数字'], ['text', '文本'], ['bool', '开关'], ['list', '列表']];

// 规则对象 → 行列表
function _airbagRuleRows() {
    const r = _airbagRules || {};
    return Object.keys(r).map(k => {
        const meta = _AIRBAG_RULE_META[k] || { label: '', type: 'text' };
        const v = r[k];
        let val;
        if (meta.type === 'list') val = Array.isArray(v) ? v.join(',') : String(v == null ? '' : v);
        else if (meta.type === 'bool') val = !!v;
        else val = v == null ? '' : String(v);
        return { key: k, label: meta.label, type: meta.type, value: val };
    });
}

// 规则值输入控件（按类型）
function _airbagRuleValueHtml(type, value, dis) {
    const base = `w-full px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40`;
    if (type === 'bool') {
        return `<label class="flex items-center h-[34px] cursor-pointer"><input class="ab-rule-val w-5 h-5 rounded accent-teal-600" type="checkbox" ${value ? 'checked' : ''} ${dis || ''}></label>`;
    }
    if (type === 'number') {
        return `<input class="ab-rule-val ${base}" type="number" value="${_airbagEscape(value)}" ${dis || ''}>`;
    }
    return `<input class="ab-rule-val ${base}" type="text" value="${_airbagEscape(value)}" placeholder="${type === 'list' ? '逗号分隔' : ''}" ${dis || ''}>`;
}

function _airbagRuleTypeOptions(cur) {
    return _AIRBAG_RULE_TYPES.map(([t, label]) => `<option value="${t}" ${cur === t ? 'selected' : ''}>${label}</option>`).join('');
}

function _airbagRenderRulesTab() {
    const rows = _airbagRuleRows();
    const readonly = !_airbagCanManage;
    const dis = readonly ? ' disabled' : '';
    const rowsHtml = rows.map(row => `
        <tr class="border-b border-slate-100 dark:border-white/5">
            <td class="px-2 py-1.5"><input class="ab-rule-key w-32 px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs font-mono text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40" value="${_airbagEscape(row.key)}" placeholder="参数名" ${dis}></td>
            <td class="px-2 py-1.5"><input class="ab-rule-label w-60 px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40" value="${_airbagEscape(row.label)}" placeholder="说明" ${dis}></td>
            <td class="px-2 py-1.5"><select class="ab-rule-type w-20 px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40" ${dis}>${_airbagRuleTypeOptions(row.type)}</select></td>
            <td class="px-2 py-1.5 min-w-[180px]">${_airbagRuleValueHtml(row.type, row.value, dis)}</td>
            ${readonly ? '' : `<td class="px-2 py-1.5 text-center"><button class="ab-rule-del w-7 h-7 rounded-lg text-slate-300 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"><i class="fas fa-trash-can text-xs"></i></button></td>`}
        </tr>`).join('');
    return `
        <div class="rounded-2xl border border-slate-200 dark:border-white/10 p-5 max-w-5xl">
            <div class="flex items-center justify-between mb-4 flex-wrap gap-2">
                <h4 class="text-sm font-semibold text-slate-700 dark:text-slate-200 flex items-center gap-2">
                    <i class="fas fa-sliders" style="color:${_AIRBAG_ACCENT}"></i>排产规则参数
                    <span class="text-[10px] font-normal text-slate-400">（${rows.length} 条，可新增/删除）</span>
                </h4>
                ${readonly ? '<span class="text-[10px] px-2 py-1 rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-400">只读（需要 scheduling.config.manage 权限）</span>' : `
                <div class="flex items-center gap-2">
                    <button id="airbag-rules-add" class="px-3 py-1.5 rounded-xl text-xs font-medium bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors"><i class="fas fa-plus mr-1"></i>新增规则</button>
                    <button id="airbag-rules-save" class="px-4 py-1.5 rounded-xl text-xs font-medium text-white transition-colors flex items-center gap-1.5" style="background:${_AIRBAG_ACCENT}"><i class="fas fa-save"></i>保存规则</button>
                </div>`}
            </div>
            <div class="overflow-x-auto">
                <table class="w-full text-left">
                    <thead>
                        <tr class="text-[10px] text-slate-400 uppercase tracking-wide border-b border-slate-200 dark:border-white/10">
                            <th class="px-3 py-2">参数名</th>
                            <th class="px-3 py-2">说明</th>
                            <th class="px-3 py-2">类型</th>
                            <th class="px-3 py-2">值</th>
                            ${readonly ? '' : '<th class="px-3 py-2"></th>'}
                        </tr>
                    </thead>
                    <tbody id="airbag-rules-tbody">
                        ${rowsHtml || `<tr><td colspan="5" class="px-4 py-8 text-center text-sm text-slate-400">暂无规则，点击「新增规则」添加</td></tr>`}
                    </tbody>
                </table>
            </div>
            <p class="text-[10px] text-slate-400 mt-2">类型：数字 / 文本 / 开关 / 列表（列表用逗号分隔，如「2周,3周」）。仅引擎识别的参数名生效，其余保留不生效。保存后下次解析/排产生效。</p>
        </div>
    `;
}

function _airbagAddRule() {
    const tbody = document.getElementById('airbag-rules-tbody');
    if (!tbody) return;
    const empty = tbody.querySelector('td[colspan]');
    if (empty) tbody.innerHTML = '';
    tbody.insertAdjacentHTML('beforeend', `<tr class="border-b border-slate-100 dark:border-white/5">
        <td class="px-2 py-1.5"><input class="ab-rule-key w-32 px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs font-mono text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40" value="" placeholder="参数名"></td>
        <td class="px-2 py-1.5"><input class="ab-rule-label w-60 px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40" value="" placeholder="说明"></td>
        <td class="px-2 py-1.5"><select class="ab-rule-type w-20 px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40">${_airbagRuleTypeOptions('text')}</select></td>
        <td class="px-2 py-1.5 min-w-[180px]">${_airbagRuleValueHtml('text', '', '')}</td>
        <td class="px-2 py-1.5 text-center"><button class="ab-rule-del w-7 h-7 rounded-lg text-slate-300 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"><i class="fas fa-trash-can text-xs"></i></button></td>
    </tr>`);
    _airbagBindRuleRowActions(tbody);
}

function _airbagBindRuleRowActions(tbody) {
    if (!tbody) return;
    tbody.querySelectorAll('.ab-rule-del').forEach(btn => {
        btn.addEventListener('click', () => btn.closest('tr').remove());
    });
    // 切换类型时重建值控件
    tbody.querySelectorAll('.ab-rule-type').forEach(sel => {
        sel.addEventListener('change', () => {
            const tr = sel.closest('tr');
            const type = sel.value;
            const cur = tr.querySelector('.ab-rule-val');
            const val = type === 'bool' ? !!cur?.checked : (cur?.value || '');
            const td = tr.querySelectorAll('td')[3];
            if (td) td.innerHTML = _airbagRuleValueHtml(type, val, '');
        });
    });
}

async function _airbagSaveRules() {
    const tbody = document.getElementById('airbag-rules-tbody');
    if (!tbody) return;
    const data = { version: 1 };
    tbody.querySelectorAll('tr').forEach(tr => {
        const key = (tr.querySelector('.ab-rule-key')?.value || '').trim();
        if (!key) return;
        const type = tr.querySelector('.ab-rule-type')?.value || 'text';
        let val;
        if (type === 'bool') {
            val = !!tr.querySelector('.ab-rule-val')?.checked;
        } else if (type === 'number') {
            val = parseFloat(tr.querySelector('.ab-rule-val')?.value) || 0;
        } else if (type === 'list') {
            val = (tr.querySelector('.ab-rule-val')?.value || '').split(/[,，]/).map(s => s.trim()).filter(Boolean);
        } else {
            val = (tr.querySelector('.ab-rule-val')?.value || '').trim();
        }
        data[key] = val;
    });
    const res = await _airbagApi('PUT', 'config', { data });
    if (res.status === 'success') {
        _airbagRules = data;
        showToast('排产规则已保存', 'success');
    } else {
        showToast(res.message || '保存失败', 'error');
    }
}

// ---------------------------------------------------------------------
// Tab 3：产能配置
// ---------------------------------------------------------------------
function _airbagCapacityRows() {
    // {sap: [{line, capacity, priority, dept}]} → 平铺数组
    const rows = [];
    Object.keys(_airbagCapacity || {}).sort().forEach(sap => {
        (_airbagCapacity[sap] || []).forEach(l => {
            rows.push({ sap, dept: l.dept, line: l.line, capacity: l.capacity, priority: l.priority });
        });
    });
    return rows;
}

function _airbagRenderCapacityTab() {
    const readonly = !_airbagCanManage;
    const rows = _airbagCapacityRows();
    return `
        <div class="space-y-4">
            ${_airbagSyncBanner()}
            <div class="rounded-2xl border border-slate-200 dark:border-white/10 p-5">
                <div class="flex items-center justify-between mb-4 flex-wrap gap-2">
                    <h4 class="text-sm font-semibold text-slate-700 dark:text-slate-200 flex items-center gap-2">
                        <i class="fas fa-industry" style="color:${_AIRBAG_ACCENT}"></i>产能配置
                        <span class="text-[10px] font-normal text-slate-400">（${rows.length} 条，按 SAP 物料分组）</span>
                        ${_airbagConfigSource.capacity === 'template' ? '<span class="text-[10px] px-2 py-0.5 rounded-lg bg-teal-50 text-teal-600 dark:bg-teal-900/30 dark:text-teal-300 font-medium">来自模板（未保存）</span>' : ''}
                    </h4>
                    <div class="flex items-center gap-2">
                        ${readonly ? '<span class="text-[10px] px-2 py-1 rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-400">只读</span>' : `
                        <label class="flex items-center gap-2 px-3 py-1.5 rounded-xl text-xs font-medium cursor-pointer bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors">
                            <i class="fas fa-file-excel"></i>从Excel同步
                            <input type="file" id="airbag-capacity-file" accept=".xlsx,.xls" class="hidden">
                        </label>
                        <button id="airbag-capacity-add" class="px-3 py-1.5 rounded-xl text-xs font-medium bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors"><i class="fas fa-plus mr-1"></i>新增行</button>
                        <button id="airbag-capacity-save" class="px-4 py-1.5 rounded-xl text-xs font-medium text-white transition-colors flex items-center gap-1.5" style="background:${_AIRBAG_ACCENT}"><i class="fas fa-save"></i>保存</button>
                        `}
                    </div>
                </div>
                <div class="overflow-x-auto">
                    <table class="w-full text-left">
                        <thead>
                            <tr class="text-[10px] text-slate-400 uppercase tracking-wide border-b border-slate-200 dark:border-white/10">
                                <th class="px-3 py-2">SAP物料号</th>
                                <th class="px-3 py-2">课别</th>
                                <th class="px-3 py-2">生产线</th>
                                <th class="px-3 py-2 text-right">产能</th>
                                <th class="px-3 py-2 text-right">优先级</th>
                                ${readonly ? '' : '<th class="px-3 py-2"></th>'}
                            </tr>
                        </thead>
                        <tbody id="airbag-capacity-tbody">
                            ${rows.map((r, i) => _airbagCapacityRowHtml(r, i, readonly)).join('') || `<tr><td colspan="6" class="px-4 py-8 text-center text-sm text-slate-400">暂无产能配置，可点击「从Excel同步」从模板同步，或手动新增行</td></tr>`}
                        </tbody>
                    </table>
                </div>
                <p class="text-[10px] text-slate-400 mt-2">优先级为空视为 1（仅此产线 = 最高优先级）。产能优先级越小越优先使用。</p>
            </div>
        </div>
    `;
}

function _airbagCapacityRowHtml(r, i, readonly) {
    const dis = readonly ? ' disabled' : '';
    return `<tr class="border-b border-slate-100 dark:border-white/5">
        <td class="px-2 py-1.5"><input class="ab-cap-sap w-full px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs font-mono text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40" value="${_airbagEscape(r.sap)}" ${dis}></td>
        <td class="px-2 py-1.5"><input class="ab-cap-dept w-full px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40" value="${_airbagEscape(r.dept || '')}" ${dis}></td>
        <td class="px-2 py-1.5"><input class="ab-cap-line w-full px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40" value="${_airbagEscape(r.line)}" ${dis}></td>
        <td class="px-2 py-1.5"><input class="ab-cap-capacity w-full px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs text-right text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40" type="number" value="${_airbagNum(r.capacity)}" ${dis}></td>
        <td class="px-2 py-1.5"><input class="ab-cap-priority w-full px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs text-right text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40" type="number" value="${_airbagNum(r.priority)}" ${dis}></td>
        ${readonly ? '' : `<td class="px-2 py-1.5 text-center"><button class="ab-cap-del w-7 h-7 rounded-lg text-slate-300 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"><i class="fas fa-trash-can text-xs"></i></button></td>`}
    </tr>`;
}

function _airbagAddCapacityRow() {
    const tbody = document.getElementById('airbag-capacity-tbody');
    if (!tbody) return;
    const empty = tbody.querySelector('td[colspan]');
    if (empty) tbody.innerHTML = '';
    tbody.insertAdjacentHTML('beforeend', _airbagCapacityRowHtml({ sap: '', dept: '', line: '', capacity: '', priority: '' }, tbody.children.length, false));
    _airbagBindCapacityRowActions(tbody);
}

function _airbagBindCapacityRowActions(tbody) {
    if (!tbody) return;
    tbody.querySelectorAll('.ab-cap-del').forEach(btn => {
        btn.addEventListener('click', () => btn.closest('tr').remove());
    });
}

async function _airbagSaveCapacity() {
    const tbody = document.getElementById('airbag-capacity-tbody');
    if (!tbody) return;
    const map = {};
    tbody.querySelectorAll('tr').forEach(tr => {
        const sap = (tr.querySelector('.ab-cap-sap')?.value || '').trim();
        if (!sap) return;
        const line = (tr.querySelector('.ab-cap-line')?.value || '').trim();
        if (!line) return;
        map[sap] = map[sap] || [];
        map[sap].push({
            line,
            capacity: parseFloat(tr.querySelector('.ab-cap-capacity')?.value) || 0,
            priority: parseFloat(tr.querySelector('.ab-cap-priority')?.value) || 1,
            dept: (tr.querySelector('.ab-cap-dept')?.value || '').trim(),
        });
    });
    Object.keys(map).forEach(sap => map[sap].sort((a, b) => (a.priority || 1) - (b.priority || 1)));
    const res = await _airbagApi('PUT', 'capacity', { data: map });
    if (res.status === 'success') {
        _airbagCapacity = map;
        showToast('产能配置已保存', 'success');
    } else {
        showToast(res.message || '保存失败', 'error');
    }
}

// 从 Excel 同步状态横幅（LOAD:/OK:/ERR: 三种状态）
function _airbagSyncBanner() {
    if (!_airbagSyncStatus) return '';
    let cls = 'border-sky-200 dark:border-sky-900/40 bg-sky-50/70 dark:bg-sky-900/10 text-sky-700 dark:text-sky-300';
    let icon = '<i class="fas fa-spinner fa-spin mr-2"></i>';
    let text = _airbagSyncStatus;
    if (text.startsWith('OK:')) {
        cls = 'border-emerald-200 dark:border-emerald-900/40 bg-emerald-50/70 dark:bg-emerald-900/10 text-emerald-700 dark:text-emerald-300';
        icon = '<i class="fas fa-circle-check mr-2"></i>';
        text = text.slice(3);
    } else if (text.startsWith('ERR:')) {
        cls = 'border-red-200 dark:border-red-900/50 bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-300';
        icon = '<i class="fas fa-triangle-exclamation mr-2"></i>';
        text = text.slice(4);
    }
    return `<div class="rounded-2xl border p-4 text-sm ${cls}">${icon}${_airbagEscape(text)}</div>`;
}

// 从 Excel 同步产能/班组/拼线（需 manage 权限；target 指定同步目标）
async function _airbagSyncFromExcel(e, target) {
    const file = e.target && e.target.files && e.target.files[0];
    if (!file) return;
    if (!_airbagCanManage) { showToast('无权限执行同步', 'error'); return; }
    if (_airbagSyncBusy) return;
    _airbagSyncBusy = true;
    _airbagSyncStatus = '正在从 Excel 同步配置，请稍候...';
    _airbagSwitchTab(_airbagActiveTab); // 显示进度横幅
    try {
        const content = await _airbagFileToBase64(file);
        const res = await _airbagApi('POST', 'sync-from-excel', {
            target: target || '',
            files: { excel: { filename: file.name, content, is_base64: true } },
        });
        if (res.status === 'success') {
            _airbagSyncStatus = 'OK:' + (res.message || '同步成功');
            showToast(res.message || '同步成功', 'success');
            await _airbagLoadConfigs();
        } else {
            _airbagSyncStatus = 'ERR:' + (res.message || '同步失败');
            showToast(res.message || '同步失败', 'error');
        }
    } catch (err) {
        _airbagSyncStatus = 'ERR:同步异常: ' + err.message;
        showToast('同步异常: ' + err.message, 'error');
    } finally {
        _airbagSyncBusy = false;
        e.target.value = '';
        _airbagSwitchTab(_airbagActiveTab); // 显示结果横幅
    }
}

// ---------------------------------------------------------------------
// Tab 4：班组产线配置
// ---------------------------------------------------------------------
function _airbagRenderTeamsTab() {
    const readonly = !_airbagCanManage;
    const teams = _airbagTeams || [];
    return `
        <div class="space-y-4">
            ${_airbagSyncBanner()}
            <div class="rounded-2xl border border-slate-200 dark:border-white/10 p-5">
                <div class="flex items-center justify-between mb-4 flex-wrap gap-2">
                    <h4 class="text-sm font-semibold text-slate-700 dark:text-slate-200 flex items-center gap-2">
                        <i class="fas fa-people-group" style="color:${_AIRBAG_ACCENT}"></i>班组产线配置
                        <span class="text-[10px] font-normal text-slate-400">（${teams.length} 个班组，第一~第五产线为偏好顺序）</span>
                        ${_airbagConfigSource.teams === 'template' ? '<span class="text-[10px] px-2 py-0.5 rounded-lg bg-teal-50 text-teal-600 dark:bg-teal-900/30 dark:text-teal-300 font-medium">来自模板（未保存）</span>' : ''}
                    </h4>
                    <div class="flex items-center gap-2">
                        ${readonly ? '<span class="text-[10px] px-2 py-1 rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-400">只读</span>' : `
                        <label class="flex items-center gap-2 px-3 py-1.5 rounded-xl text-xs font-medium cursor-pointer bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors">
                            <i class="fas fa-file-excel"></i>从Excel同步
                            <input type="file" id="airbag-teams-file" accept=".xlsx,.xls" class="hidden">
                        </label>
                        <button id="airbag-teams-add" class="px-3 py-1.5 rounded-xl text-xs font-medium bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors"><i class="fas fa-plus mr-1"></i>新增行</button>
                        <button id="airbag-teams-save" class="px-4 py-1.5 rounded-xl text-xs font-medium text-white transition-colors flex items-center gap-1.5" style="background:${_AIRBAG_ACCENT}"><i class="fas fa-save"></i>保存</button>
                        `}
                    </div>
                </div>
                <div class="overflow-x-auto">
                    <table class="w-full text-left">
                        <thead>
                            <tr class="text-[10px] text-slate-400 uppercase tracking-wide border-b border-slate-200 dark:border-white/10">
                                <th class="px-3 py-2">序号</th>
                                <th class="px-3 py-2">课别</th>
                                <th class="px-3 py-2">区域</th>
                                <th class="px-3 py-2">班组</th>
                                <th class="px-3 py-2 text-right">人数</th>
                                <th class="px-3 py-2">第一产线</th>
                                <th class="px-3 py-2">第二产线</th>
                                <th class="px-3 py-2">第三产线</th>
                                <th class="px-3 py-2">第四产线</th>
                                <th class="px-3 py-2">第五产线</th>
                                <th class="px-3 py-2">第六产线</th>
                                <th class="px-3 py-2">备注</th>
                                ${readonly ? '' : '<th class="px-3 py-2"></th>'}
                            </tr>
                        </thead>
                        <tbody id="airbag-teams-tbody">
                            ${teams.map((t, i) => _airbagTeamRowHtml(t, i, readonly)).join('') || `<tr><td colspan="13" class="px-4 py-8 text-center text-sm text-slate-400">暂无班组配置，可点击「从Excel同步」从配置表同步，或手动新增行</td></tr>`}
                        </tbody>
                    </table>
                </div>
                <p class="text-[10px] text-slate-400 mt-2">排产时按「同区域 → 同课别 → 跨课」优先分配班组。产线为空表示该班组不可用。备注中的拼线描述（如「开S5291线和黄根拼」）会同步解析到「拼线规则」。</p>
            </div>
        </div>
    `;
}

function _airbagTeamRowHtml(t, i, readonly) {
    const dis = readonly ? ' disabled' : '';
    const lines = t.lines || [];
    const cell = (idx) => `<td class="px-2 py-1.5"><input class="ab-team-line w-full px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40" value="${_airbagEscape(lines[idx] || '')}" ${dis}></td>`;
    return `<tr class="border-b border-slate-100 dark:border-white/5">
        <td class="px-2 py-1.5"><input class="ab-team-seq w-12 px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40" value="${_airbagEscape(t.seq || i + 1)}" ${dis}></td>
        <td class="px-2 py-1.5"><input class="ab-team-dept w-16 px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40" value="${_airbagEscape(t.dept || '')}" placeholder="一课" ${dis}></td>
        <td class="px-2 py-1.5"><input class="ab-team-area w-14 px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40" value="${_airbagEscape(t.area || '')}" placeholder="A1" ${dis}></td>
        <td class="px-2 py-1.5"><input class="ab-team-name w-28 px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs font-medium text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40" value="${_airbagEscape(t.team || '')}" ${dis}></td>
        <td class="px-2 py-1.5"><input class="ab-team-count w-16 px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs text-right text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40" type="number" value="${_airbagNum(t.count)}" ${dis}></td>
        ${cell(0)}${cell(1)}${cell(2)}${cell(3)}${cell(4)}${cell(5)}
        <td class="px-2 py-1.5"><input class="ab-team-remark w-40 px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40" value="${_airbagEscape(t.remark || '')}" ${dis}></td>
        ${readonly ? '' : `<td class="px-2 py-1.5 text-center"><button class="ab-team-del w-7 h-7 rounded-lg text-slate-300 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"><i class="fas fa-trash-can text-xs"></i></button></td>`}
    </tr>`;
}

function _airbagAddTeamRow() {
    const tbody = document.getElementById('airbag-teams-tbody');
    if (!tbody) return;
    const empty = tbody.querySelector('td[colspan]');
    if (empty) tbody.innerHTML = '';
    tbody.insertAdjacentHTML('beforeend', _airbagTeamRowHtml({ seq: '', dept: '', area: '', team: '', count: '', lines: [], remark: '' }, tbody.children.length, false));
    _airbagBindTeamRowActions(tbody);
}

function _airbagBindTeamRowActions(tbody) {
    if (!tbody) return;
    tbody.querySelectorAll('.ab-team-del').forEach(btn => {
        btn.addEventListener('click', () => btn.closest('tr').remove());
    });
}

async function _airbagSaveTeams() {
    const tbody = document.getElementById('airbag-teams-tbody');
    if (!tbody) return;
    const teams = [];
    tbody.querySelectorAll('tr').forEach(tr => {
        const name = (tr.querySelector('.ab-team-name')?.value || '').trim();
        if (!name) return;
        const lines = [];
        tr.querySelectorAll('.ab-team-line').forEach(inp => {
            const v = (inp.value || '').trim();
            if (v) lines.push(v);
        });
        teams.push({
            seq: (tr.querySelector('.ab-team-seq')?.value || '').trim(),
            dept: (tr.querySelector('.ab-team-dept')?.value || '').trim(),
            area: (tr.querySelector('.ab-team-area')?.value || '').trim(),
            team: name,
            count: tr.querySelector('.ab-team-count')?.value || null,
            lines,
            remark: (tr.querySelector('.ab-team-remark')?.value || '').trim(),
        });
    });
    const res = await _airbagApi('PUT', 'teams', { data: teams });
    if (res.status === 'success') {
        _airbagTeams = teams;
        showToast('班组产线配置已保存', 'success');
    } else {
        showToast(res.message || '保存失败', 'error');
    }
}

// ---------------------------------------------------------------------
// Tab 5：拼线规则
// ---------------------------------------------------------------------
function _airbagRenderMergeTab() {
    const readonly = !_airbagCanManage;
    const merge = _airbagMerge || [];
    return `
        <div class="space-y-4">
            ${_airbagSyncBanner()}
            <div class="rounded-2xl border border-slate-200 dark:border-white/10 p-5">
                <div class="flex items-center justify-between mb-4 flex-wrap gap-2">
                    <h4 class="text-sm font-semibold text-slate-700 dark:text-slate-200 flex items-center gap-2">
                        <i class="fas fa-object-group" style="color:${_AIRBAG_ACCENT}"></i>拼线规则
                        <span class="text-[10px] font-normal text-slate-400">（${merge.length} 条：某班组开某线时需与伙伴班组一起生产）</span>
                        ${_airbagConfigSource.merge === 'template' ? '<span class="text-[10px] px-2 py-0.5 rounded-lg bg-teal-50 text-teal-600 dark:bg-teal-900/30 dark:text-teal-300 font-medium">来自模板（未保存）</span>' : ''}
                    </h4>
                    <div class="flex items-center gap-2">
                        ${readonly ? '<span class="text-[10px] px-2 py-1 rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-400">只读</span>' : `
                        <label class="flex items-center gap-2 px-3 py-1.5 rounded-xl text-xs font-medium cursor-pointer bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors">
                            <i class="fas fa-file-excel"></i>从Excel同步
                            <input type="file" id="airbag-merge-file" accept=".xlsx,.xls" class="hidden">
                        </label>
                        <button id="airbag-merge-add" class="px-3 py-1.5 rounded-xl text-xs font-medium bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors"><i class="fas fa-plus mr-1"></i>新增行</button>
                        <button id="airbag-merge-save" class="px-4 py-1.5 rounded-xl text-xs font-medium text-white transition-colors flex items-center gap-1.5" style="background:${_AIRBAG_ACCENT}"><i class="fas fa-save"></i>保存</button>
                        `}
                    </div>
                </div>
                <div class="overflow-x-auto">
                    <table class="w-full text-left">
                        <thead>
                            <tr class="text-[10px] text-slate-400 uppercase tracking-wide border-b border-slate-200 dark:border-white/10">
                                <th class="px-3 py-2">班组</th>
                                <th class="px-3 py-2">产线</th>
                                <th class="px-3 py-2">伙伴班组（逗号分隔）</th>
                                <th class="px-3 py-2">原始描述</th>
                                <th class="px-3 py-2">状态</th>
                                ${readonly ? '' : '<th class="px-3 py-2"></th>'}
                            </tr>
                        </thead>
                        <tbody id="airbag-merge-tbody">
                            ${merge.map((m, i) => _airbagMergeRowHtml(m, i, readonly)).join('') || `<tr><td colspan="6" class="px-4 py-8 text-center text-sm text-slate-400">暂无拼线规则，可点击「从Excel同步」从班组备注自动解析</td></tr>`}
                        </tbody>
                    </table>
                </div>
                <p class="text-[10px] text-slate-400 mt-2">状态「已解析」来自班组备注自动识别；手动新增的行请将「原始描述」留空并在「状态」勾选生效。</p>
            </div>
        </div>
    `;
}

function _airbagMergeRowHtml(m, i, readonly) {
    const dis = readonly ? ' disabled' : '';
    const parsed = m.parsed !== false;
    const effective = m.parsed !== false || (m.line && (m.partners || []).length);
    return `<tr class="border-b border-slate-100 dark:border-white/5">
        <td class="px-2 py-1.5"><input class="ab-merge-team w-28 px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40" value="${_airbagEscape(m.team || '')}" ${dis}></td>
        <td class="px-2 py-1.5"><input class="ab-merge-line w-24 px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs font-mono text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40" value="${_airbagEscape(m.line || '')}" ${dis}></td>
        <td class="px-2 py-1.5"><input class="ab-merge-partners w-48 px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40" value="${_airbagEscape((m.partners || []).join(','))}" ${dis}></td>
        <td class="px-2 py-1.5"><input class="ab-merge-raw w-64 px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs text-slate-500 focus:outline-none focus:ring-2 focus:ring-teal-500/40" value="${_airbagEscape(m.raw || '')}" readonly></td>
        <td class="px-2 py-1.5">
            ${parsed
                ? '<span class="text-[10px] px-2 py-0.5 rounded-lg bg-sky-50 text-sky-600 dark:bg-sky-900/30 dark:text-sky-300">自动解析</span>'
                : `<label class="flex items-center gap-1.5 text-[10px] text-slate-500 cursor-pointer ${readonly ? 'opacity-50' : ''}"><input type="checkbox" class="ab-merge-enabled w-3.5 h-3.5 rounded accent-teal-600" ${effective ? 'checked' : ''} ${dis}>生效</label>`}
        </td>
        ${readonly ? '' : `<td class="px-2 py-1.5 text-center"><button class="ab-merge-del w-7 h-7 rounded-lg text-slate-300 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"><i class="fas fa-trash-can text-xs"></i></button></td>`}
    </tr>`;
}

function _airbagAddMergeRow() {
    const tbody = document.getElementById('airbag-merge-tbody');
    if (!tbody) return;
    const empty = tbody.querySelector('td[colspan]');
    if (empty) tbody.innerHTML = '';
    tbody.insertAdjacentHTML('beforeend', _airbagMergeRowHtml({ team: '', line: '', partners: [], raw: '', parsed: false }, tbody.children.length, false));
    _airbagBindMergeRowActions(tbody);
}

function _airbagBindMergeRowActions(tbody) {
    if (!tbody) return;
    tbody.querySelectorAll('.ab-merge-del').forEach(btn => {
        btn.addEventListener('click', () => btn.closest('tr').remove());
    });
}

async function _airbagSaveMerge() {
    const tbody = document.getElementById('airbag-merge-tbody');
    if (!tbody) return;
    const rules = [];
    tbody.querySelectorAll('tr').forEach(tr => {
        const team = (tr.querySelector('.ab-merge-team')?.value || '').trim();
        if (!team) return;
        const line = (tr.querySelector('.ab-merge-line')?.value || '').trim();
        const partners = (tr.querySelector('.ab-merge-partners')?.value || '').split(/[,，]/).map(s => s.trim()).filter(Boolean);
        const enabled = tr.querySelector('.ab-merge-enabled') ? tr.querySelector('.ab-merge-enabled').checked : (line && partners.length > 0);
        if (!enabled) return;
        rules.push({
            team,
            line: line || null,
            partners,
            raw: (tr.querySelector('.ab-merge-raw')?.value || '').trim(),
            parsed: false,
        });
    });
    const res = await _airbagApi('PUT', 'merge-rules', { data: rules });
    if (res.status === 'success') {
        _airbagMerge = rules;
        showToast('拼线规则已保存', 'success');
    } else {
        showToast(res.message || '保存失败', 'error');
    }
}

// ---------------------------------------------------------------------
// Tab 6：模板管理
// ---------------------------------------------------------------------
function _airbagRenderTemplateTab() {
    const readonly = !_airbagCanManage;
    return `
        <div class="space-y-4 max-w-3xl">
            <div class="rounded-2xl border border-slate-200 dark:border-white/10 p-5">
                <h4 class="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-4 flex items-center gap-2">
                    <i class="fas fa-file-excel" style="color:${_AIRBAG_ACCENT}"></i>排产模板管理
                </h4>
                <div id="airbag-template-info"></div>
                <div class="mt-4 flex flex-wrap items-center gap-3">
                    ${readonly ? '<span class="text-[10px] px-2 py-1 rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-400">模板更换需 scheduling.config.manage 权限</span>' : `
                    <label class="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium cursor-pointer bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors">
                        <i class="fas fa-upload"></i>上传新模板（替换默认）
                        <input type="file" id="airbag-template-file" accept=".xlsx,.xls" class="hidden">
                    </label>
                    <button id="airbag-template-reset" class="px-4 py-2.5 rounded-xl text-sm font-medium bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors">
                        <i class="fas fa-rotate-left mr-1"></i>恢复内置模板
                    </button>
                    `}
                </div>
                <div id="airbag-template-import-status"></div>
                <div class="mt-4 rounded-xl bg-slate-50 dark:bg-white/5 p-4 text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">
                    <i class="fas fa-circle-info mr-1" style="color:${_AIRBAG_ACCENT}"></i>
                    上传的模板将保存为默认模板（用户上传优先于内置模板）。执行排产时基于模板生成新表，不会修改原模板文件。
                    内置模板位于技能目录 <code class="font-mono">templates/生产排产模板.xlsx</code>。
                </div>
            </div>
        </div>
    `;
}

async function _airbagUploadTemplate(e) {
    const file = e.target && e.target.files && e.target.files[0];
    if (!file) return;
    const statusEl = document.getElementById('airbag-template-import-status');
    if (statusEl) statusEl.innerHTML = `
        <div class="mt-3 rounded-xl border border-sky-200 dark:border-sky-900/40 bg-sky-50/70 dark:bg-sky-900/10 p-3 text-sm text-sky-700 dark:text-sky-300">
            <i class="fas fa-spinner fa-spin mr-2"></i>正在导入模板（${_airbagEscape(file.name)}），数据较多时请稍候...
        </div>`;
    try {
        const content = await _airbagFileToBase64(file);
        const res = await _airbagApi('POST', 'template', {
            files: { excel: { filename: file.name, content, is_base64: true } },
        });
        if (res.status === 'success') {
            _airbagTemplateInfo = res.data;
            if (statusEl) statusEl.innerHTML = `
                <div class="mt-3 rounded-xl border border-emerald-200 dark:border-emerald-900/40 bg-emerald-50/70 dark:bg-emerald-900/10 p-3 text-sm text-emerald-700 dark:text-emerald-300">
                    <i class="fas fa-circle-check mr-2"></i>模板导入成功，已更新为默认模板
                </div>`;
            showToast('模板导入成功，已更新为默认模板', 'success');
            _airbagLoadTemplateContent(); // 刷新执行排产页的模板内容摘要
        } else {
            if (statusEl) statusEl.innerHTML = `
                <div class="mt-3 rounded-xl border border-red-200 dark:border-red-900/50 bg-red-50 dark:bg-red-900/20 p-3 text-sm text-red-600 dark:text-red-300">
                    <i class="fas fa-triangle-exclamation mr-2"></i>模板导入失败：${_airbagEscape(res.message || '未知错误')}
                </div>`;
            showToast(res.message || '模板更新失败', 'error');
        }
    } catch (err) {
        if (statusEl) statusEl.innerHTML = `
            <div class="mt-3 rounded-xl border border-red-200 dark:border-red-900/50 bg-red-50 dark:bg-red-900/20 p-3 text-sm text-red-600 dark:text-red-300">
                <i class="fas fa-triangle-exclamation mr-2"></i>模板上传异常：${_airbagEscape(err.message)}
            </div>`;
        showToast('上传异常: ' + err.message, 'error');
    } finally {
        e.target.value = '';
    }
    _airbagLoadTemplateInfo();
}

async function _airbagResetTemplate() {
    if (!confirm('确定恢复内置模板？将删除用户上传的默认模板副本。')) return;
    const res = await _airbagApi('POST', 'template/reset');
    if (res.status === 'success') {
        _airbagTemplateInfo = res.data;
        showToast(res.message || '已恢复内置模板', 'success');
    } else {
        showToast(res.message || '恢复失败', 'error');
    }
    _airbagLoadTemplateInfo();
}

// ---------------------------------------------------------------------
// 历史记录
// ---------------------------------------------------------------------
async function _airbagShowHistory() {
    const content = document.getElementById('airbag-wb-content');
    if (!content) return;
    content.innerHTML = `
        <div class="space-y-4 max-w-4xl">
            <div class="flex items-center justify-between">
                <h4 class="text-sm font-semibold text-slate-700 dark:text-slate-200 flex items-center gap-2">
                    <i class="fas fa-clock-rotate-left" style="color:${_AIRBAG_ACCENT}"></i>排产历史记录
                </h4>
                <button id="airbag-history-back" class="text-xs text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 transition-colors">
                    <i class="fas fa-arrow-left mr-1"></i>返回执行排产
                </button>
            </div>
            <div id="airbag-history-list" class="space-y-2">
                <div class="text-center py-10 text-sm text-slate-400"><i class="fas fa-spinner fa-spin mr-2"></i>加载中...</div>
            </div>
        </div>
    `;
    document.getElementById('airbag-history-back').addEventListener('click', () => _airbagSwitchTab('run'));
    try {
        const res = await _airbagApi('GET', 'history');
        const list = document.getElementById('airbag-history-list');
        if (!list) return;
        const history = res.status === 'success' ? res.data || [] : [];
        if (history.length === 0) {
            list.innerHTML = '<div class="text-center py-10 text-sm text-slate-400">暂无排产记录</div>';
            return;
        }
        list.innerHTML = history.map((h) => `
            <div class="rounded-xl border border-slate-200 dark:border-white/10 p-4 hover:shadow-sm transition-shadow">
                <div class="flex items-center justify-between flex-wrap gap-2">
                    <div class="flex items-center gap-2">
                        <i class="fas fa-calendar-check text-xs" style="color:${_AIRBAG_ACCENT}"></i>
                        <span class="text-sm font-semibold text-slate-700 dark:text-slate-200">${_airbagEscape(h.time)}</span>
                    </div>
                    <span class="text-[10px] px-2 py-0.5 rounded-lg bg-teal-50 text-teal-600 dark:bg-teal-900/30 dark:text-teal-300 font-medium">
                        ${_airbagEscape(h.window ? h.window.start + ' ~ ' + h.window.end : '')}
                    </span>
                </div>
                <div class="mt-3 flex flex-wrap gap-2">
                    <a href="/api/file?path=${encodeURIComponent(h.output_path || '')}" target="_blank" download
                       class="px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors flex items-center gap-1.5">
                        <i class="fas fa-file-excel" style="color:${_AIRBAG_ACCENT}"></i>排产结果 Excel
                    </a>
                    ${h.report_path ? `
                    <a href="/api/file?path=${encodeURIComponent(h.report_path)}" target="_blank" download
                       class="px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors flex items-center gap-1.5">
                        <i class="fas fa-file-lines" style="color:${_AIRBAG_ACCENT}"></i>排产分析报告 HTML
                    </a>` : ''}
                </div>
            </div>
        `).join('');
    } catch (err) {
        const list = document.getElementById('airbag-history-list');
        if (list) list.innerHTML = `<div class="text-center py-10 text-sm text-red-400">历史加载失败: ${_airbagEscape(err.message)}</div>`;
    }
}
