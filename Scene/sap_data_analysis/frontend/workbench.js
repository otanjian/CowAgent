let _sapAnalysisScene = null;
let _sapAnalysisMessage = '';
let _sapAnalysisResult = null; // 保存最近一次 SAP 执行结果（含 chart/dashboard）
let _sapAnalysisConnections = [];
let _sapFollowUpContext = null; // 保存上一次 SAP 分析的上下文，用于聊天窗口继续分析
let _lastSapQueryPlan = null;   // 保存当前工作台生成的查询计划，用于预览和编辑
let _currentSapOutputMode = 'table'; // table | chart | dashboard
let _currentSapOutputTab = 'table';  // 当前结果区展示的 tab
let _sapAnalysisUIInited = false;    // 防止重复绑定事件
const SAP_FOLLOW_UP_CONTEXT_KEY = 'sap_follow_up_context'; // sessionStorage key

// SAP 数据分析：领域 → 常见意图模板
const _sapDomainIntents = {
    sales: [
        { value: 'sales_orders_this_month', label: '本月销售订单', question: '本月销售订单有哪些' },
        { value: 'sales_amount_this_month', label: '本月销售额', question: '本月销售额是多少' },
        { value: 'top_customers_by_sales', label: '销售额 Top 客户', question: '本月销售额排名前10的客户' },
    ],
    procurement: [
        { value: 'purchase_orders_this_month', label: '本月采购订单', question: '本月采购订单有哪些' },
        { value: 'purchase_amount_this_month', label: '本月采购金额', question: '本月采购金额是多少' },
        { value: 'top_suppliers_by_purchase', label: '采购额 Top 供应商', question: '本月采购额排名前10的供应商' },
    ],
    inventory: [
        { value: 'inventory_overview', label: '库存总览', question: '当前库存情况' },
        { value: 'inventory_by_material', label: '按物料查询库存', question: '按物料查询库存金额' },
    ],
    production: [
        { value: 'production_orders_this_month', label: '本月生产订单', question: '本月生产订单有哪些' },
        { value: 'production_order_schedule', label: '生产订单进度', question: '生产订单进度如何' },
        { value: 'production_order_items', label: '生产订单明细', question: '生产订单明细' },
    ],
    finance: [
        { value: 'finance_overview', label: '财务凭证总览', question: '本月财务凭证总览' },
        { value: 'finance_receivables', label: '应收账款', question: '应收账款情况' },
        { value: 'finance_payables', label: '应付账款', question: '应付账款情况' },
        { value: 'finance_gl_accounts', label: '总账科目', question: '总账科目余额' },
    ],
    master_data: [
        { value: 'new_customers_this_month', label: '本月新增客户', question: '本月新增了多少家客户' },
        { value: 'new_suppliers_this_month', label: '本月新增供应商', question: '本月新增了多少家供应商' },
        { value: 'new_materials_this_period', label: '本月新增物料', question: '本月新增了多少物料' },
        { value: 'customer_list', label: '客户列表', question: '客户列表' },
    ],
};

function _persistSapFollowUpContext() {
    try {
        if (_sapFollowUpContext) {
            sessionStorage.setItem(SAP_FOLLOW_UP_CONTEXT_KEY, JSON.stringify(_sapFollowUpContext));
        } else {
            sessionStorage.removeItem(SAP_FOLLOW_UP_CONTEXT_KEY);
        }
    } catch (_) {}
}

function _restoreSapFollowUpContext() {
    if (_sapFollowUpContext) return _sapFollowUpContext;
    try {
        const raw = sessionStorage.getItem(SAP_FOLLOW_UP_CONTEXT_KEY);
        if (raw) {
            _sapFollowUpContext = JSON.parse(raw);
        }
    } catch (_) {
        _sapFollowUpContext = null;
    }
    return _sapFollowUpContext;
}

function _clearSapFollowUpContext() {
    _sapFollowUpContext = null;
    try {
        sessionStorage.removeItem(SAP_FOLLOW_UP_CONTEXT_KEY);
    } catch (_) {}
}

// 构建 SAP 图表/看板 HTML（用于聊天消息可视化区）
function _buildSapVisualsHtml(visuals) {
    if (!visuals) return '';
    const parts = [];
    if (visuals.chart && visuals.chart.base64) {
        parts.push(`
            <div class="mb-3">
                <div class="flex justify-between items-center mb-2">
                    <span class="text-xs font-semibold text-slate-600 dark:text-slate-300">${escapeHtml(visuals.chart.title || '分析图表')}</span>
                    <button type="button" class="text-xs text-cyan-600 hover:text-cyan-700 dark:hover:text-cyan-400" onclick="_openImageLightbox('data:image/png;base64,${visuals.chart.base64}')">全屏查看</button>
                </div>
                <img src="data:image/png;base64,${visuals.chart.base64}" alt="分析图表" class="max-w-full rounded-xl border border-slate-200 dark:border-white/10 shadow-sm cursor-zoom-in" style="max-height:320px;">
            </div>
        `);
    }
    if (visuals.dashboard && visuals.dashboard.html) {
        parts.push(`
            <div class="mb-1" data-sap-dashboard="1">
                <div class="flex justify-between items-center mb-2">
                    <span class="text-xs font-semibold text-slate-600 dark:text-slate-300">${escapeHtml(visuals.dashboard.title || '数据看板')}</span>
                    <button type="button" class="text-xs text-cyan-600 hover:text-cyan-700 dark:hover:text-cyan-400" onclick="_openSapDashboardFullscreen(this)">全屏查看</button>
                </div>
                <div class="w-full rounded-xl border border-slate-200 dark:border-white/10 overflow-hidden shadow-sm" style="height:320px;">
                    <iframe srcdoc="${visuals.dashboard.html.replace(/"/g, '&quot;')}" class="w-full h-full border-0"></iframe>
                </div>
            </div>
        `);
    }
    return parts.join('');
}

// 全屏查看 SAP 看板
function _openSapDashboardFullscreen(btn) {
    const container = btn.closest('[data-sap-dashboard]');
    if (!container) return;
    const iframe = container.querySelector('iframe');
    const titleEl = container.querySelector('.text-xs.font-semibold');
    if (!iframe) return;
    _openSapHtmlFullscreen(iframe.getAttribute('srcdoc'), titleEl ? titleEl.textContent : '数据看板');
}

function _openSapHtmlFullscreen(html, title) {
    let overlay = document.getElementById('sap-dashboard-lightbox');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'sap-dashboard-lightbox';
        overlay.className = 'fixed inset-0 z-[9999] bg-slate-900/90 flex flex-col p-4 hidden';
        overlay.innerHTML = `
            <div class="flex items-center justify-between mb-3 px-2">
                <h3 class="text-white text-base font-semibold sap-dashboard-lightbox-title"></h3>
                <button type="button" class="text-white/80 hover:text-white text-sm px-3 py-1 rounded-lg bg-white/10" onclick="document.getElementById('sap-dashboard-lightbox').classList.add('hidden')">关闭</button>
            </div>
            <div class="flex-1 rounded-xl overflow-hidden bg-white">
                <iframe class="w-full h-full border-0 sap-dashboard-lightbox-frame"></iframe>
            </div>
        `;
        document.body.appendChild(overlay);
    }
    overlay.querySelector('.sap-dashboard-lightbox-title').textContent = title || '数据看板';
    overlay.querySelector('.sap-dashboard-lightbox-frame').setAttribute('srcdoc', html || '');
    overlay.classList.remove('hidden');
}

function _formatDate(d) {
    const year = d.getFullYear();
    const month = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
}

function _applySapQuickDate(period) {
    const today = new Date();
    let from = '';
    let to = '';

    if (period === 'this_month') {
        from = _formatDate(new Date(today.getFullYear(), today.getMonth(), 1));
        to = _formatDate(new Date(today.getFullYear(), today.getMonth() + 1, 0));
    } else if (period === 'this_quarter') {
        const q = Math.floor(today.getMonth() / 3);
        from = _formatDate(new Date(today.getFullYear(), q * 3, 1));
        to = _formatDate(new Date(today.getFullYear(), q * 3 + 3, 0));
    } else if (period === 'this_year') {
        from = _formatDate(new Date(today.getFullYear(), 0, 1));
        to = _formatDate(new Date(today.getFullYear(), 11, 31));
    } else if (period === 'last_year') {
        from = _formatDate(new Date(today.getFullYear() - 1, 0, 1));
        to = _formatDate(new Date(today.getFullYear() - 1, 11, 31));
    } else if (period === 'q1') {
        from = _formatDate(new Date(today.getFullYear(), 0, 1));
        to = _formatDate(new Date(today.getFullYear(), 2, 31));
    } else if (period === 'q2') {
        from = _formatDate(new Date(today.getFullYear(), 3, 1));
        to = _formatDate(new Date(today.getFullYear(), 5, 30));
    } else if (period === 'q3') {
        from = _formatDate(new Date(today.getFullYear(), 6, 1));
        to = _formatDate(new Date(today.getFullYear(), 8, 30));
    } else if (period === 'q4') {
        from = _formatDate(new Date(today.getFullYear(), 9, 1));
        to = _formatDate(new Date(today.getFullYear(), 11, 31));
    }

    document.getElementById('sap-analysis-date-from').value = from;
    document.getElementById('sap-analysis-date-to').value = to;
}

function _selectSapDomain(domain, activeBtn) {
    document.querySelectorAll('.sap-domain-chip').forEach(btn => {
        btn.classList.remove('bg-cyan-50', 'border-cyan-400', 'text-cyan-600', 'dark:bg-cyan-900/20', 'dark:text-cyan-400');
        btn.classList.add('bg-white', 'border-slate-200', 'text-slate-600', 'dark:bg-white/5', 'dark:border-slate-600', 'dark:text-slate-300');
    });
    activeBtn.classList.remove('bg-white', 'border-slate-200', 'text-slate-600', 'dark:bg-white/5', 'dark:border-slate-600', 'dark:text-slate-300');
    activeBtn.classList.add('bg-cyan-50', 'border-cyan-400', 'text-cyan-600', 'dark:bg-cyan-900/20', 'dark:text-cyan-400');

    _renderSapIntentOptions(domain);
}

function _renderSapIntentOptions(domain) {
    const section = document.getElementById('sap-analysis-intent-section');
    const select = document.getElementById('sap-analysis-intent');
    const intents = _sapDomainIntents[domain] || [];

    if (intents.length === 0) {
        section.classList.add('hidden');
        select.innerHTML = '<option value="">选择意图或直接在下方输入问题</option>';
        return;
    }

    let html = '<option value="">选择意图或直接在下方输入问题</option>';
    intents.forEach(item => {
        html += `<option value="${item.question}">${item.label}</option>`;
    });
    select.innerHTML = html;
    section.classList.remove('hidden');
}

function _onSapIntentChange() {
    const select = document.getElementById('sap-analysis-intent');
    const value = select.value;
    if (value) {
        document.getElementById('sap-analysis-question').value = value;
    }
}

function _selectSapOutputMode(mode, activeBtn) {
    _currentSapOutputMode = mode;
    document.querySelectorAll('.sap-output-mode-btn').forEach(btn => {
        btn.classList.remove('bg-white', 'dark:bg-slate-600', 'text-cyan-600', 'dark:text-cyan-400', 'shadow-sm');
        btn.classList.add('text-slate-500', 'dark:text-slate-400');
    });
    activeBtn.classList.remove('text-slate-500', 'dark:text-slate-400');
    activeBtn.classList.add('bg-white', 'dark:bg-slate-600', 'text-cyan-600', 'dark:text-cyan-400', 'shadow-sm');
}

function _selectSapOutputTab(tab, activeBtn) {
    _currentSapOutputTab = tab;
    document.querySelectorAll('.sap-output-tab').forEach(btn => {
        btn.classList.remove('text-cyan-600', 'dark:text-cyan-400', 'border-b-2', 'border-cyan-500');
        btn.classList.add('text-slate-500', 'dark:text-slate-400');
    });
    activeBtn.classList.remove('text-slate-500', 'dark:text-slate-400');
    activeBtn.classList.add('text-cyan-600', 'dark:text-cyan-400', 'border-b-2', 'border-cyan-500');

    document.querySelectorAll('.sap-output-panel').forEach(panel => panel.classList.add('hidden'));
    document.getElementById(`sap-analysis-output-${tab}`).classList.remove('hidden');
}

function _resetSapAnalysisResultUI() {
    document.getElementById('sap-analysis-empty-state').classList.remove('hidden');
    document.getElementById('sap-analysis-plan-section').classList.add('hidden');
    document.getElementById('sap-analysis-result').classList.add('hidden');
    document.getElementById('sap-analysis-error').classList.add('hidden');
    document.getElementById('sap-analysis-plan-json').value = '';
    _lastSapQueryPlan = null;

    // 重置输出 tab 为表格
    const tableTab = document.querySelector('.sap-output-tab[data-tab="table"]');
    if (tableTab) _selectSapOutputTab('table', tableTab);

    document.getElementById('sap-analysis-output-table').innerHTML = '<div class="text-xs text-slate-500 dark:text-slate-400 text-center py-8">数据加载中...</div>';
    document.getElementById('sap-analysis-output-chart').innerHTML = '<div class="text-xs text-slate-500 dark:text-slate-400 text-center py-8">图表将在执行分析后生成</div>';
    document.getElementById('sap-analysis-output-dashboard').innerHTML = '<div class="text-xs text-slate-500 dark:text-slate-400 text-center py-8">看板将在执行分析后生成</div>';
}

function _renderSapTablePreview(data, totalRows) {
    const container = document.getElementById('sap-analysis-output-table');
    if (!data || data.length === 0) {
        container.innerHTML = '<div class="text-xs text-slate-500 dark:text-slate-400 text-center py-8">暂无数据</div>';
        return;
    }
    const headers = Object.keys(data[0]);
    let html = `<div class="overflow-x-auto"><table class="w-full text-xs text-left"><thead class="bg-slate-100 dark:bg-white/5 text-slate-600 dark:text-slate-300 sticky top-0"><tr>`;
    headers.forEach(h => {
        html += `<th class="px-3 py-2 font-medium whitespace-nowrap border-b border-slate-200 dark:border-white/10">${h}</th>`;
    });
    html += '</tr></thead><tbody class="divide-y divide-slate-100 dark:divide-white/5">';
    data.forEach(row => {
        html += '<tr class="hover:bg-slate-50 dark:hover:bg-white/5 transition-colors">';
        headers.forEach(h => {
            const v = row[h];
            html += `<td class="px-3 py-2 whitespace-nowrap text-slate-700 dark:text-slate-300">${v === null || v === undefined ? '' : v}</td>`;
        });
        html += '</tr>';
    });
    html += '</tbody></table></div>';
    if (totalRows > data.length) {
        html += `<div class="mt-2 text-xs text-slate-400 dark:text-slate-500 text-center">共 ${totalRows} 行，已预览前 ${data.length} 行</div>`;
    }
    container.innerHTML = html;
}

function _updateSapOutputPanel(data) {
    // 表格预览
    const previewRows = (data && data.preview_data) || [];
    const totalRows = (data && data.total_rows) || previewRows.length;
    _renderSapTablePreview(previewRows, totalRows);

    // 图表预览
    const chartContainer = document.getElementById('sap-analysis-output-chart');
    if (data && data.chart && data.chart.base64) {
        chartContainer.innerHTML = `
            <div class="flex flex-col items-center gap-3">
                <div class="flex justify-between items-center w-full">
                    <div class="text-sm font-medium text-slate-700 dark:text-slate-200">${data.chart.title || '图表'}</div>
                    <button type="button" class="text-xs text-cyan-600 hover:text-cyan-700 dark:hover:text-cyan-400" onclick="_openImageLightbox('data:image/png;base64,${data.chart.base64}')">全屏查看</button>
                </div>
                <img src="data:image/png;base64,${data.chart.base64}" alt="分析图表" class="max-w-full rounded-xl border border-slate-200 dark:border-white/10 shadow-sm cursor-zoom-in">
            </div>
        `;
    } else if (data && data.chart && data.chart.message) {
        chartContainer.innerHTML = `<div class="text-xs text-slate-500 dark:text-slate-400 text-center py-8">图表生成失败：${data.chart.message}</div>`;
    } else {
        chartContainer.innerHTML = '<div class="text-xs text-slate-500 dark:text-slate-400 text-center py-8">当前输出模式未生成图表</div>';
    }

    // 看板渲染
    const dashboardContainer = document.getElementById('sap-analysis-output-dashboard');
    if (data && data.dashboard && data.dashboard.html) {
        dashboardContainer.innerHTML = `
            <div data-sap-dashboard="1">
                <div class="flex justify-between items-center mb-2">
                    <div class="text-sm font-medium text-slate-700 dark:text-slate-200">${data.dashboard.title || '数据看板'}</div>
                    <button type="button" class="text-xs text-cyan-600 hover:text-cyan-700 dark:hover:text-cyan-400" onclick="_openSapDashboardFullscreen(this)">全屏查看</button>
                </div>
                <div class="w-full h-[600px] rounded-xl border border-slate-200 dark:border-white/10 overflow-hidden shadow-sm">
                    <iframe srcdoc="${data.dashboard.html.replace(/"/g, '&quot;')}" class="w-full h-full border-0"></iframe>
                </div>
            </div>
        `;
    } else {
        dashboardContainer.innerHTML = '<div class="text-xs text-slate-500 dark:text-slate-400 text-center py-8">看板生成失败或当前输出模式未选择看板</div>';
    }

    // 根据输出模式自动切换到对应 tab
    const targetTab = _currentSapOutputMode;
    const tabBtn = document.querySelector(`.sap-output-tab[data-tab="${targetTab}"]`);
    if (tabBtn) _selectSapOutputTab(targetTab, tabBtn);
}

function _initSapAnalysisUI() {
    if (_sapAnalysisUIInited) return;
    _sapAnalysisUIInited = true;

    // 领域选择
    document.querySelectorAll('.sap-domain-chip').forEach(btn => {
        btn.addEventListener('click', () => _selectSapDomain(btn.dataset.domain, btn));
    });

    // 意图选择
    document.getElementById('sap-analysis-intent').addEventListener('change', _onSapIntentChange);

    // 输出模式
    document.querySelectorAll('.sap-output-mode-btn').forEach(btn => {
        btn.addEventListener('click', () => _selectSapOutputMode(btn.dataset.mode, btn));
    });

    // 结果区 tab
    document.querySelectorAll('.sap-output-tab').forEach(btn => {
        btn.addEventListener('click', () => _selectSapOutputTab(btn.dataset.tab, btn));
    });

    // 日期快捷按钮
    document.querySelectorAll('.sap-date-quick-btn').forEach(btn => {
        btn.addEventListener('click', () => _applySapQuickDate(btn.dataset.period));
    });
}

function openSapDataAnalysisWorkbench(scene) {
    _sapAnalysisScene = scene;
    _sapAnalysisMessage = '';

    // Create new session and activate scene so system_prompt is applied
    newChat();
    activateScene(scene);

    const modal = document.getElementById('sap-data-analysis-workbench-modal');
    modal.classList.remove('hidden');

    // Reset form
    document.getElementById('sap-analysis-question').value = '';
    document.getElementById('sap-analysis-company-code').value = '';
    document.getElementById('sap-analysis-date-from').value = '';
    document.getElementById('sap-analysis-date-to').value = '';
    document.getElementById('sap-analysis-max-rows').value = '5000';
    document.getElementById('sap-analysis-execute-btn').classList.add('hidden');

    // Reset UI states
    document.querySelectorAll('.sap-domain-chip').forEach(btn => {
        btn.classList.remove('bg-cyan-50', 'border-cyan-400', 'text-cyan-600', 'dark:bg-cyan-900/20', 'dark:text-cyan-400');
        btn.classList.add('bg-white', 'border-slate-200', 'text-slate-600', 'dark:bg-white/5', 'dark:border-slate-600', 'dark:text-slate-300');
    });
    document.getElementById('sap-analysis-intent-section').classList.add('hidden');
    document.getElementById('sap-analysis-intent').innerHTML = '<option value="">选择意图或直接在下方输入问题</option>';

    // Reset output mode to table
    const tableModeBtn = document.querySelector('.sap-output-mode-btn[data-mode="table"]');
    if (tableModeBtn) _selectSapOutputMode('table', tableModeBtn);

    _resetSapAnalysisResultUI();

    // Init event listeners once
    _initSapAnalysisUI();

    loadSapAnalysisConnections();
}

function closeSapDataAnalysisWorkbench() {
    const modal = document.getElementById('sap-data-analysis-workbench-modal');
    modal.classList.add('hidden');
    _sapAnalysisScene = null;
    _sapAnalysisMessage = '';
}

function loadSapAnalysisConnections() {
    const select = document.getElementById('sap-analysis-connection');
    const card = document.getElementById('sap-analysis-connection-card');
    select.innerHTML = '<option value="">加载中...</option>';

    fetch('/api/erp/connections/options')
        .then(r => r.json())
        .then(data => {
            _sapAnalysisConnections = (data.connections || []).filter(c => (c.system || '').toLowerCase() === 'sap');
            select.innerHTML = '';
            if (_sapAnalysisConnections.length === 0) {
                select.innerHTML = '<option value="">暂无 SAP 连接</option>';
                if (card) card.classList.remove('hidden');
                return;
            }
            const defaultConn = _sapAnalysisConnections.find(c => c.is_default === true);
            if (defaultConn) {
                // 有默认连接：自动选中并保持选择框隐藏
                if (card) card.classList.add('hidden');
                const opt = document.createElement('option');
                opt.value = defaultConn.id;
                opt.textContent = defaultConn.name || defaultConn.id;
                select.appendChild(opt);
                select.value = defaultConn.id;
            } else {
                // 无默认连接：显示选择框供用户手动选择
                if (card) card.classList.remove('hidden');
                const placeholder = document.createElement('option');
                placeholder.value = '';
                placeholder.textContent = '请选择 SAP 连接';
                select.appendChild(placeholder);
                _sapAnalysisConnections.forEach(conn => {
                    const opt = document.createElement('option');
                    opt.value = conn.id;
                    opt.textContent = conn.name || conn.id;
                    select.appendChild(opt);
                });
            }
        })
        .catch(() => {
            select.innerHTML = '<option value="">加载失败</option>';
        });
}

function _buildSapAnalysisContext() {
    const companyCode = document.getElementById('sap-analysis-company-code').value.trim();
    const dateFrom = document.getElementById('sap-analysis-date-from').value;
    const dateTo = document.getElementById('sap-analysis-date-to').value;
    const maxRows = parseInt(document.getElementById('sap-analysis-max-rows').value, 10) || 5000;
    const context = {};
    if (companyCode) context.company_code = companyCode;
    if (dateFrom) context.date_from = dateFrom;
    if (dateTo) context.date_to = dateTo;
    if (maxRows) context.max_rows = maxRows;
    context.output_mode = _currentSapOutputMode;
    return context;
}

function _validateSapAnalysisInputs() {
    const connectionId = document.getElementById('sap-analysis-connection').value;
    const question = document.getElementById('sap-analysis-question').value.trim();
    const companyCode = document.getElementById('sap-analysis-company-code').value.trim();
    const dateFrom = document.getElementById('sap-analysis-date-from').value;
    const dateTo = document.getElementById('sap-analysis-date-to').value;
    const maxRows = parseInt(document.getElementById('sap-analysis-max-rows').value, 10) || 5000;

    const errorEl = document.getElementById('sap-analysis-error');
    errorEl.classList.add('hidden');

    if (!connectionId) {
        errorEl.textContent = '请选择 SAP 连接';
        errorEl.classList.remove('hidden');
        return null;
    }
    if (!question) {
        errorEl.textContent = '请输入自然语言问题';
        errorEl.classList.remove('hidden');
        return null;
    }
    if (!companyCode && !dateFrom && !dateTo && !maxRows) {
        errorEl.textContent = '请至少填写一项数据范围参数：公司代码、日期范围或最大行数';
        errorEl.classList.remove('hidden');
        return null;
    }
    return { connectionId, question, context: _buildSapAnalysisContext() };
}

function generateSapAnalysisPlan() {
    const inputs = _validateSapAnalysisInputs();
    if (!inputs) return;

    const { connectionId, question, context } = inputs;
    const planBtn = document.getElementById('sap-analysis-plan-btn');
    const originalText = planBtn.innerHTML;
    planBtn.disabled = true;
    planBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>生成计划中...';

    document.getElementById('sap-analysis-error').classList.add('hidden');
    document.getElementById('sap-analysis-empty-state').classList.add('hidden');
    document.getElementById('sap-analysis-result').classList.add('hidden');
    document.getElementById('sap-analysis-plan-section').classList.add('hidden');
    document.getElementById('sap-analysis-execute-btn').classList.add('hidden');

    fetch('/api/sap-data-analysis/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ connection_id: connectionId, question, context, plan_only: true }),
    })
        .then(r => r.json())
        .then(data => {
            planBtn.disabled = false;
            planBtn.innerHTML = originalText;

            if (data.status !== 'success') {
                const errorEl = document.getElementById('sap-analysis-error');
                errorEl.textContent = data.message || '生成计划失败';
                errorEl.classList.remove('hidden');
                return;
            }

            _lastSapQueryPlan = data.query_plan || {};
            document.getElementById('sap-analysis-plan-json').value = JSON.stringify(_lastSapQueryPlan, null, 2);
            document.getElementById('sap-analysis-plan-section').classList.remove('hidden');
            document.getElementById('sap-analysis-execute-btn').classList.remove('hidden');
        })
        .catch(err => {
            planBtn.disabled = false;
            planBtn.innerHTML = originalText;
            const errorEl = document.getElementById('sap-analysis-error');
            errorEl.textContent = '网络错误：' + (err.message || '请求失败');
            errorEl.classList.remove('hidden');
        });
}

function executeSapAnalysisPlan() {
    const inputs = _validateSapAnalysisInputs();
    if (!inputs) return;
    const { connectionId, question, context } = inputs;

    const planJson = document.getElementById('sap-analysis-plan-json').value.trim();
    let queryPlan;
    try {
        queryPlan = JSON.parse(planJson);
    } catch (e) {
        const errorEl = document.getElementById('sap-analysis-error');
        errorEl.textContent = '查询计划 JSON 格式错误：' + e.message;
        errorEl.classList.remove('hidden');
        return;
    }

    const executeBtn = document.getElementById('sap-analysis-execute-btn');
    const originalText = executeBtn.innerHTML;
    executeBtn.disabled = true;
    executeBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>执行中...';

    document.getElementById('sap-analysis-error').classList.add('hidden');
    document.getElementById('sap-analysis-empty-state').classList.add('hidden');

    fetch('/api/sap-data-analysis/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ connection_id: connectionId, question, query_plan: queryPlan, output_mode: _currentSapOutputMode }),
    })
        .then(r => r.json())
        .then(data => {
            executeBtn.disabled = false;
            executeBtn.innerHTML = originalText;

            if (data.status !== 'success') {
                const errorEl = document.getElementById('sap-analysis-error');
                errorEl.textContent = data.message || '执行失败';
                errorEl.classList.remove('hidden');
                return;
            }

            _sapAnalysisMessage = data.message || '';
            _sapAnalysisResult = data || null;
            _sapFollowUpContext = {
                connection_id: connectionId,
                question: question,
                context: context,
                query_plan: data.query_plan || queryPlan,
                data_file: data.data_file || '',
                summary: data.summary || '',
            };
            _persistSapFollowUpContext();

            const resultEl = document.getElementById('sap-analysis-result');
            document.getElementById('sap-analysis-summary').textContent = data.summary || `已抽取 ${data.total_rows || 0} 条记录`;
            document.getElementById('sap-analysis-file-path').textContent = data.data_file || '';

            const fileId = (data.data_file || '').split(/[\\/]/).pop();
            const downloadLink = document.getElementById('sap-analysis-download');
            if (fileId) {
                downloadLink.href = `/api/sap-data-analysis/${encodeURIComponent(fileId)}/csv`;
                downloadLink.classList.remove('hidden');
            } else {
                downloadLink.classList.add('hidden');
            }

            // 渲染数据预览与图表
            _updateSapOutputPanel(data);

            resultEl.classList.remove('hidden');
            resultEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        })
        .catch(err => {
            executeBtn.disabled = false;
            executeBtn.innerHTML = originalText;
            const errorEl = document.getElementById('sap-analysis-error');
            errorEl.textContent = '网络错误：' + (err.message || '请求失败');
            errorEl.classList.remove('hidden');
        });
}

// 保留旧函数名作为兼容入口
function submitSapDataAnalysis() {
    generateSapAnalysisPlan();
}

function sendSapAnalysisToChat() {
    const msg = _sapAnalysisMessage;
    console.log('[sendSapAnalysisToChat] message length', msg ? msg.length : 0);
    if (!msg) return;
    closeSapDataAnalysisWorkbench();
    const visuals = _sapAnalysisResult ? {
        chart: _sapAnalysisResult.chart,
        dashboard: _sapAnalysisResult.dashboard,
        question: _sapAnalysisResult.question
    } : null;
    sendMessage(msg, visuals);
}

function sendSapFollowUpAnalysis(question) {
    // 在聊天窗口中继续 SAP 数据分析
    _restoreSapFollowUpContext();
    if (!_sapFollowUpContext) {
        addBotMessage('请先通过 SAP 数据分析工作台进行一次查询，再使用 `/sap <问题>` 继续分析。', new Date());
        return;
    }

    const loadingEl = addLoadingIndicator();
    const timestamp = new Date();
    addUserMessage(`/sap ${question}`, timestamp, []);

    fetch('/api/sap-data-analysis/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            connection_id: _sapFollowUpContext.connection_id,
            question: question,
            context: _sapFollowUpContext.context || {},
            follow_up_context: _sapFollowUpContext,
        }),
    })
        .then(r => r.json())
        .then(data => {
            loadingEl.remove();
            if (data.status !== 'success') {
                addBotMessage(`继续分析失败：${data.message || '未知错误'}`, new Date());
                return;
            }
            // 更新上下文，支持多次连续分析
            _sapFollowUpContext = {
                connection_id: _sapFollowUpContext.connection_id,
                question: data.question || question,
                context: _sapFollowUpContext.context || {},
                query_plan: data.query_plan || {},
                data_file: data.data_file || '',
                summary: data.summary || '',
            };
            _persistSapFollowUpContext();
            // 把结果消息发送到聊天窗口，由 LLM 继续分析
            sendMessage(data.message || '');
        })
        .catch(err => {
            loadingEl.remove();
            addBotMessage('继续分析请求失败：' + (err.message || '网络错误'), new Date());
        });
}
