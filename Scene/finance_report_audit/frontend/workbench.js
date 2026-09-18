let _financialAuditScene = null;
let _financialAuditSubScene = null;
let _financialAuditParsedData = null;
let _financialAuditFilePath = null;
let _financialAuditFileName = null;

function openFinancialAuditWorkbench(scene) {
    _financialAuditScene = scene;
    _financialAuditSubScene = null;

    const modal = document.getElementById('financial-audit-workbench-modal');
    // 移动端：重置「审查依据」抽屉为收起状态
    const wbLeftPanel = modal ? modal.querySelector('.w-80') : null;
    if (wbLeftPanel) wbLeftPanel.classList.remove('open');
    const title = document.getElementById('financial-audit-wb-title');
    const subtitle = document.getElementById('financial-audit-wb-subtitle');

    title.textContent = scene.workbench_title || scene.name + '工作台';
    subtitle.textContent = '智能解析财务文件，执行合规审查与风险识别';

    // Reset UI
    document.getElementById('financial-audit-wb-subscene-selector').classList.remove('hidden');
    document.getElementById('financial-audit-wb-content-area').classList.add('hidden');
    document.getElementById('financial-audit-wb-actions').classList.add('hidden');

    // 清空上次打开时选中功能模块后残留的内容
    const financialAuditContentArea = document.getElementById('financial-audit-wb-content-area');
    if (financialAuditContentArea) financialAuditContentArea.innerHTML = '';
    const financialAuditIconBox = document.getElementById('financial-audit-wb-icon');
    if (financialAuditIconBox) financialAuditIconBox.style.background = '#6366f1';

    // Show file status banner if a file was previously uploaded in this session
    const statusBanner = document.getElementById('financial-audit-wb-file-status');
    const statusName = document.getElementById('financial-audit-wb-file-status-name');
    if (statusBanner && statusName) {
        if (_financialAuditFilePath && _financialAuditFileName) {
            statusBanner.classList.remove('hidden');
            statusName.textContent = _financialAuditFileName;
        } else {
            statusBanner.classList.add('hidden');
        }
    }

    // Render sub-scene cards
    renderFinancialAuditSubScenes(scene);

    // Reset indicators: show default comprehensive indicators
    renderDefaultFinancialAuditIndicators();

    modal.classList.remove('hidden');
}

function closeFinancialAuditWorkbench() {
    document.getElementById('financial-audit-workbench-modal').classList.add('hidden');
    _financialAuditScene = null;
    _financialAuditSubScene = null;
}

function renderFinancialAuditSubScenes(scene) {
    const grid = document.getElementById('financial-audit-wb-subscene-grid');
    grid.innerHTML = '';

    (scene.sub_scenes || []).forEach(sub => {
        const hasFile = _financialAuditFilePath && _financialAuditFileName;
        const fileBadge = (hasFile && sub.id !== 'report_upload_parse')
            ? `<span class="ml-auto text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400 flex-shrink-0">已关联</span>`
            : '';

        const card = document.createElement('div');
        card.className = 'bg-white dark:bg-[#1A1A1A] rounded-xl border border-slate-200 dark:border-white/10 p-4 cursor-pointer hover:shadow-md hover:-translate-y-0.5 transition-all duration-200 group';
        card.innerHTML = `
            <div class="flex items-center gap-3 mb-2">
                <div class="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0" style="background:${sub.color || '#64748b'}15">
                    <i class="fas ${sub.icon || 'fa-calculator'}" style="color:${sub.color || '#64748b'}"></i>
                </div>
                <div class="min-w-0 flex-1">
                    <div class="flex items-center">
                        <h5 class="font-semibold text-slate-800 dark:text-slate-100 text-sm">${escapeHtml(sub.name)}</h5>
                        ${fileBadge}
                    </div>
                </div>
            </div>
            <p class="text-xs text-slate-500 dark:text-slate-400 line-clamp-2">${escapeHtml(sub.description || '')}</p>
        `;
        card.onclick = () => selectFinancialAuditSubScene(sub, card, grid);
        grid.appendChild(card);
    });
}

function selectFinancialAuditSubScene(sub, card, grid) {
    _financialAuditSubScene = sub;

    // Highlight selected
    grid.querySelectorAll('div').forEach(c => {
        c.classList.remove('ring-2', 'ring-primary-500');
    });
    card.classList.add('ring-2', 'ring-primary-500');

    // Update header
    const iconBox = document.getElementById('financial-audit-wb-icon');
    iconBox.style.background = sub.color || '#6366f1';

    document.getElementById('financial-audit-wb-title').textContent = sub.name;
    document.getElementById('financial-audit-wb-subtitle').textContent = sub.description || '';

    // Render indicators
    renderFinancialAuditIndicators(sub);

    // Render content area
    renderFinancialAuditContent(sub);

    // Show actions
    document.getElementById('financial-audit-wb-actions').classList.remove('hidden');
}

function renderDefaultFinancialAuditIndicators() {
    const container = document.getElementById('financial-audit-wb-indicators');
    container.innerHTML = '';

    const groups = [
        {
            title: '财务报表审查',
            items: [
                { name: '资产负债表平衡', formula: '自动校验', meaning: '资产=负债+所有者权益，检查会计恒等式' },
                { name: '现金流量勾稽', formula: '自动校验', meaning: '验证经营/投资/筹资现金流与资产负债表、利润表的勾稽关系' },
                { name: '存货与成本匹配', formula: '自动校验', meaning: '检查存货变动与营业成本的逻辑一致性' },
            ]
        },
        {
            title: '纳税申报审查',
            items: [
                { name: '增值税收入比对', formula: '申报 vs 财务', meaning: '增值税申报收入与财务报表收入差异分析' },
                { name: '所得税利润比对', formula: '申报 vs 财务', meaning: '企业所得税申报利润与财务报表利润总额差异' },
                { name: '税负率异常检测', formula: '行业对比', meaning: '增值税/企业所得税税负率与同行业对比识别异常' },
            ]
        },
        {
            title: '科目与明细审查',
            items: [
                { name: '往来款项异常', formula: '账龄/余额分析', meaning: '应收账款、其他应收款长期挂账或异常波动' },
                { name: '固定资产变动', formula: '增减分析', meaning: '固定资产增减变动与折旧计提的合理性' },
                { name: '费用合规性', formula: '限额检查', meaning: '业务招待费、福利费、广告费等税前扣除限额检查' },
            ]
        },
        {
            title: '风险识别指标',
            items: [
                { name: '收入确认时点', formula: 'CAS 14', meaning: '识别年底突击确认收入、提前确认等风险' },
                { name: '关联交易占比', formula: '关联方识别', meaning: '关联销售/采购占比过高或定价异常' },
                { name: '资本弱化', formula: '债资比', meaning: '关联方借款债资比超过2:1的利息调整风险' },
            ]
        },
    ];

    groups.forEach(group => {
        const groupEl = document.createElement('div');
        groupEl.className = 'mb-3';
        groupEl.innerHTML = `<h5 class="text-[11px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-1.5">${escapeHtml(group.title)}</h5>`;
        const itemsContainer = document.createElement('div');
        itemsContainer.className = 'space-y-1.5';
        group.items.forEach(ind => {
            const el = document.createElement('div');
            el.className = 'p-2 bg-slate-50 dark:bg-slate-800/50 rounded-lg';
            el.innerHTML = `
                <div class="flex items-center gap-2 mb-0.5">
                    <span class="text-xs font-semibold text-slate-700 dark:text-slate-200">${escapeHtml(ind.name)}</span>
                    <span class="text-[10px] px-1.5 py-0.5 rounded bg-primary-100 dark:bg-primary-900/30 text-primary-600 dark:text-primary-400">${escapeHtml(ind.formula)}</span>
                </div>
                <p class="text-[11px] text-slate-500 dark:text-slate-400 leading-relaxed">${escapeHtml(ind.meaning)}</p>
            `;
            itemsContainer.appendChild(el);
        });
        groupEl.appendChild(itemsContainer);
        container.appendChild(groupEl);
    });
}

function renderFinancialAuditIndicators(sub) {
    const container = document.getElementById('financial-audit-wb-indicators');
    container.innerHTML = '';

    (sub.indicators || []).forEach(ind => {
        const el = document.createElement('div');
        el.className = 'p-3 bg-slate-50 dark:bg-slate-800/50 rounded-lg';
        el.innerHTML = `
            <div class="flex items-center gap-2 mb-1">
                <span class="text-xs font-semibold text-slate-700 dark:text-slate-200">${escapeHtml(ind.name)}</span>
                <span class="text-[10px] px-1.5 py-0.5 rounded bg-primary-100 dark:bg-primary-900/30 text-primary-600 dark:text-primary-400">${escapeHtml(ind.formula)}</span>
            </div>
            <p class="text-xs text-slate-500 dark:text-slate-400 leading-relaxed">${escapeHtml(ind.meaning)}</p>
        `;
        container.appendChild(el);
    });
}

function renderFinancialAuditContent(sub) {
    const contentArea = document.getElementById('financial-audit-wb-content-area');
    contentArea.classList.remove('hidden');

    const submitBtn = document.getElementById('financial-audit-wb-submit-btn');
    const submitText = document.getElementById('financial-audit-wb-submit-text');

    if (sub.id === 'report_upload_parse') {
        // Upload & Parse sub-scene
        submitText.textContent = '开始解析';
        submitBtn.onclick = () => submitFinancialAuditParse();

        contentArea.innerHTML = `
            <div class="space-y-4">
                <div class="bg-slate-50 dark:bg-slate-800/50 rounded-xl border-2 border-dashed border-slate-300 dark:border-slate-600 p-8 text-center" id="fa-upload-dropzone">
                    <i class="fas fa-cloud-arrow-up text-3xl text-slate-400 mb-3"></i>
                    <p class="text-sm text-slate-600 dark:text-slate-300 mb-1">点击上传或拖拽文件到此处</p>
                    <p class="text-xs text-slate-400 dark:text-slate-500">支持 Excel、PDF、CSV、Word 格式</p>
                    <input type="file" id="fa-file-input" class="hidden" accept=".xlsx,.xls,.xlsm,.pdf,.csv,.docx">
                </div>
                <div id="fa-file-info" class="hidden bg-emerald-50 dark:bg-emerald-900/10 rounded-lg p-3 border border-emerald-100 dark:border-emerald-900/20">
                    <div class="flex items-center gap-2">
                        <i class="fas fa-file-excel text-emerald-500"></i>
                        <span id="fa-file-name" class="text-sm text-slate-700 dark:text-slate-200 font-medium"></span>
                        <button onclick="clearFinancialAuditFile()" class="ml-auto text-xs text-slate-400 hover:text-red-500"><i class="fas fa-trash"></i></button>
                    </div>
                </div>
                <div id="fa-parse-result" class="hidden">
                    <h5 class="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-2">解析结果预览</h5>
                    <div id="fa-parse-result-content" class="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-3 text-xs font-mono max-h-64 overflow-y-auto"></div>
                </div>
                <!-- Quick Actions after parse -->
                <div id="fa-quick-actions" class="hidden pt-2 border-t border-slate-200 dark:border-white/10">
                    <p class="text-xs text-slate-500 dark:text-slate-400 mb-3">解析完成，请选择审查方式：</p>
                    <div class="flex flex-wrap gap-3">
                        <button onclick="submitFinancialAuditFullReview()" class="px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-medium transition-colors flex items-center gap-2">
                            <i class="fas fa-bolt"></i> 一键全面审查
                        </button>
                        <button onclick="showFinancialAuditSubSceneSelector()" class="px-4 py-2 rounded-lg bg-white dark:bg-[#1A1A1A] border border-slate-200 dark:border-white/10 text-slate-700 dark:text-slate-200 text-sm font-medium hover:bg-slate-50 dark:hover:bg-white/5 transition-colors">
                            分步审查（单项选择）
                        </button>
                    </div>
                </div>
            </div>
        `;

        // Setup file upload
        const dropzone = document.getElementById('fa-upload-dropzone');
        const fileInput = document.getElementById('fa-file-input');

        dropzone.onclick = () => fileInput.click();
        dropzone.ondragover = (e) => { e.preventDefault(); dropzone.classList.add('border-primary-500', 'bg-primary-50'); };
        dropzone.ondragleave = () => { dropzone.classList.remove('border-primary-500', 'bg-primary-50'); };
        dropzone.ondrop = (e) => {
            e.preventDefault();
            dropzone.classList.remove('border-primary-500', 'bg-primary-50');
            const files = e.dataTransfer.files;
            if (files.length > 0) handleFinancialAuditFile(files[0]);
        };
        fileInput.onchange = (e) => {
            if (e.target.files.length > 0) handleFinancialAuditFile(e.target.files[0]);
        };
    } else {
        // Review sub-scenes
        submitText.textContent = '开始审查';
        submitBtn.onclick = () => submitFinancialAuditReview();

        let dataSummary = '';
        if (_financialAuditParsedData) {
            const data = _financialAuditParsedData;
            dataSummary = `
                <div class="bg-emerald-50 dark:bg-emerald-900/10 rounded-lg p-3 border border-emerald-100 dark:border-emerald-900/20 mb-4">
                    <div class="flex items-center gap-2 mb-2">
                        <i class="fas fa-check-circle text-emerald-500"></i>
                        <span class="text-sm font-medium text-slate-700 dark:text-slate-200">已解析数据</span>
                    </div>
                    <div class="text-xs text-slate-600 dark:text-slate-300 space-y-1">
                        ${data.company_name ? `<p>公司：${escapeHtml(data.company_name)}</p>` : ''}
                        ${data.report_period ? `<p>期间：${escapeHtml(data.report_period)}</p>` : ''}
                        ${data.source_software ? `<p>来源：${escapeHtml(data.source_software)}</p>` : ''}
                        <p>文件：${escapeHtml(_financialAuditFileName || '')}</p>
                    </div>
                </div>
            `;
        } else {
            dataSummary = `
                <div class="bg-amber-50 dark:bg-amber-900/10 rounded-lg p-3 border border-amber-100 dark:border-amber-900/20 mb-4">
                    <div class="flex items-center gap-2">
                        <i class="fas fa-triangle-exclamation text-amber-500"></i>
                        <span class="text-sm text-slate-700 dark:text-slate-200">尚未上传报表</span>
                    </div>
                    <p class="text-xs text-slate-500 dark:text-slate-400 mt-1">建议先执行"报表上传解析"以获取最佳审查效果，也可直接提交进行通用审查。</p>
                </div>
            `;
        }

        contentArea.innerHTML = `
            <div class="space-y-4">
                ${dataSummary}
                <div>
                    <h5 class="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-2">补充信息（可选）</h5>
                    <textarea id="fa-supplement-text" rows="4" class="w-full px-3 py-2 rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-[#1A1A1A] text-sm text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-primary-500 resize-none" placeholder="可补充说明企业类型、行业、特殊事项等，便于更精准审查..."></textarea>
                </div>
            </div>
        `;
    }
}

function handleFinancialAuditFile(file) {
    _financialAuditFileName = file.name;

    // Show file info
    document.getElementById('fa-file-name').textContent = file.name;
    document.getElementById('fa-file-info').classList.remove('hidden');

    showToast('正在上传文件...', 'info');

    const reader = new FileReader();
    reader.onload = function(e) {
        const base64 = e.target.result.split(',')[1];
        const ext = file.name.split('.').pop().toLowerCase();
        const isBinary = ['xlsx', 'xls', 'xlsm', 'pdf', 'docx'].includes(ext);

        fetch('/api/workbench/upload', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                files: {
                    file: {
                        filename: file.name,
                        content: base64,
                        is_base64: isBinary
                    }
                },
                session_id: sessionId
            })
        })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'success') {
                _financialAuditFilePath = data.file_path || Object.values(data.files || {})[0];
                showToast('文件上传成功', 'success');

                // Show file status banner in right panel
                const statusBanner = document.getElementById('financial-audit-wb-file-status');
                const statusName = document.getElementById('financial-audit-wb-file-status-name');
                if (statusBanner && statusName) {
                    statusBanner.classList.remove('hidden');
                    statusName.textContent = _financialAuditFileName;
                }

                if (['xlsx', 'xls', 'xlsm', 'csv'].includes(ext)) {
                    parseFinancialAuditExcel(file);
                }
            } else {
                showToast(data.message || '上传失败', 'error');
            }
        })
        .catch(() => {
            showToast('上传失败，请重试', 'error');
        });
    };
    reader.onerror = function() {
        showToast('文件读取失败', 'error');
    };
    reader.readAsDataURL(file);
}

function clearFinancialAuditFile() {
    _financialAuditFilePath = null;
    _financialAuditFileName = null;
    _financialAuditParsedData = null;
    document.getElementById('fa-file-input').value = '';
    document.getElementById('fa-file-info').classList.add('hidden');
    document.getElementById('fa-parse-result').classList.add('hidden');
    const quickActions = document.getElementById('fa-quick-actions');
    if (quickActions) quickActions.classList.add('hidden');
}

function parseFinancialAuditExcel(file) {
    showToast('正在解析文件...', 'info');

    const reader = new FileReader();
    reader.onload = function(e) {
        const base64 = e.target.result.split(',')[1];

        fetch('/api/workbench/parse-excel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename: file.name, file_content: base64 })
        })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'success') {
                // Extract key financial data from parsed result
                // Backend returns { sheets: [{ name, headers, data, total_rows, layout }] }
                const firstSheet = (data.sheets || [])[0] || {};
                const rows = firstSheet.data || [];
                const headers = firstSheet.headers || [];

                // Try to extract common financial fields
                const extracted = extractFinancialData(rows, headers);
                _financialAuditParsedData = extracted;

                // Show preview
                const preview = document.getElementById('fa-parse-result');
                const content = document.getElementById('fa-parse-result-content');
                preview.classList.remove('hidden');

                let html = '';
                if (extracted.company_name) html += `<p><strong>公司：</strong>${escapeHtml(extracted.company_name)}</p>`;
                if (extracted.report_period) html += `<p><strong>期间：</strong>${escapeHtml(extracted.report_period)}</p>`;
                if (extracted.source_software) html += `<p><strong>来源：</strong>${escapeHtml(extracted.source_software)}</p>`;

                const bs = extracted.balance_sheet || {};
                const is = extracted.income_statement || {};
                if (Object.keys(bs).length > 0) {
                    html += `<p class="mt-2"><strong>资产总计：</strong>${bs['资产总计'] !== undefined ? bs['资产总计'].toLocaleString() : '-'}</p>`;
                    html += `<p><strong>负债合计：</strong>${bs['负债合计'] !== undefined ? bs['负债合计'].toLocaleString() : '-'}</p>`;
                }
                if (Object.keys(is).length > 0) {
                    html += `<p class="mt-2"><strong>营业收入：</strong>${is['营业收入'] !== undefined ? is['营业收入'].toLocaleString() : '-'}</p>`;
                    html += `<p><strong>净利润：</strong>${is['净利润'] !== undefined ? is['净利润'].toLocaleString() : '-'}</p>`;
                }
                if (html === '') {
                    html = `<p class="text-slate-400">已解析 ${rows.length} 行数据，${headers.length} 个字段</p>`;
                }
                content.innerHTML = html;

                // Show quick actions (one-click full review or step-by-step)
                const quickActions = document.getElementById('fa-quick-actions');
                if (quickActions) quickActions.classList.remove('hidden');

                showToast('文件解析成功', 'success');
            } else {
                showToast(data.message || '解析失败', 'error');
            }
        })
        .catch(() => {
            showToast('解析失败', 'error');
        });
    };
    reader.readAsDataURL(file);
}

function extractFinancialData(rows, headers) {
    const result = {
        company_name: '',
        report_period: '',
        source_software: '',
        balance_sheet: {},
        income_statement: {},
        cash_flow: {}
    };

    if (!rows || rows.length === 0) return result;

    // Try to detect software from headers
    const headerStr = headers.join('');
    if (headerStr.includes('金蝶') || headerStr.includes('Kingdee')) {
        result.source_software = '金蝶';
    } else if (headerStr.includes('用友') || headerStr.includes('UFIDA')) {
        result.source_software = '用友';
    }

    // Extract common fields by searching rows
    rows.forEach(row => {
        const keys = Object.keys(row);
        keys.forEach(key => {
            const val = row[key];
            if (val === undefined || val === null || val === '') return;

            const keyLower = String(key).toLowerCase().replace(/\s/g, '');

            // Company name
            if (keyLower.includes('公司') || keyLower.includes('企业') || keyLower.includes('单位')) {
                if (!result.company_name && String(val).length < 50) result.company_name = String(val);
            }

            // Period
            if (keyLower.includes('期间') || keyLower.includes('年度') || keyLower.includes('日期')) {
                if (!result.report_period && String(val).length < 30) result.report_period = String(val);
            }

            // Balance sheet items
            const bsItems = ['货币资金', '应收账款', '存货', '流动资产合计', '资产总计', '流动负债合计', '负债合计', '所有者权益合计'];
            bsItems.forEach(item => {
                if (keyLower.includes(item.toLowerCase().replace(/\s/g, ''))) {
                    const num = parseFloat(String(val).replace(/,/g, ''));
                    if (!isNaN(num)) result.balance_sheet[item] = num;
                }
            });

            // Income statement items
            const isItems = ['营业收入', '营业成本', '营业利润', '利润总额', '净利润', '销售费用', '管理费用', '财务费用'];
            isItems.forEach(item => {
                if (keyLower.includes(item.toLowerCase().replace(/\s/g, ''))) {
                    const num = parseFloat(String(val).replace(/,/g, ''));
                    if (!isNaN(num)) result.income_statement[item] = num;
                }
            });
        });
    });

    return result;
}

function submitFinancialAuditParse() {
    const subScene = _financialAuditSubScene;
    const parentScene = _financialAuditScene;

    if (!_financialAuditFilePath) {
        showToast('请先上传文件', 'error');
        return;
    }

    let prompt = `请解析以下财务报表文件，提取关键财务数据：\n\n`;
    prompt += `文件路径：${_financialAuditFilePath}\n`;
    prompt += `文件名称：${_financialAuditFileName}\n\n`;
    prompt += `请识别文件类型和财务软件来源，提取资产负债表、利润表、现金流量表的关键科目数据，并以结构化方式呈现。`;

    if (_financialAuditParsedData) {
        const d = _financialAuditParsedData;
        prompt += `\n\n前端已初步提取的数据：\n`;
        if (d.company_name) prompt += `公司：${d.company_name}\n`;
        if (d.report_period) prompt += `期间：${d.report_period}\n`;
        if (d.source_software) prompt += `来源：${d.source_software}\n`;
        if (Object.keys(d.balance_sheet).length > 0) {
            prompt += `\n资产负债表关键数据：\n`;
            Object.entries(d.balance_sheet).forEach(([k, v]) => { prompt += `  ${k}：${v.toLocaleString()}\n`; });
        }
        if (Object.keys(d.income_statement).length > 0) {
            prompt += `\n利润表关键数据：\n`;
            Object.entries(d.income_statement).forEach(([k, v]) => { prompt += `  ${k}：${v.toLocaleString()}\n`; });
        }
    }

    closeFinancialAuditWorkbench();
    newChat();

    fetch('/api/scenes/activate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            scene_id: subScene.id,
            session_id: sessionId,
            scene_context: {
                id: subScene.id,
                name: subScene.name,
                system_prompt: subScene.system_prompt,
                skill_name: parentScene.skill_name,
                file_path: _financialAuditFilePath || '',
                file_name: _financialAuditFileName || ''
            }
        }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            showToast(t('scenes_switched_sub').replace('{name}', subScene.name), 'success');
            setTimeout(() => sendMessage(prompt), 500);
        } else {
            showToast(data.message || '启动失败', 'error');
        }
    })
    .catch(() => showToast('网络错误', 'error'));
}

// One-click full review: run all review dimensions in a single request
function submitFinancialAuditFullReview() {
    const parentScene = _financialAuditScene;

    if (!_financialAuditFilePath) {
        showToast('请先上传文件', 'error');
        return;
    }

    let prompt = `请对以下财务报表进行全面审查，依次执行以下全部审查项目，并生成完整的审查意见书：\n\n`;
    prompt += `文件路径：${_financialAuditFilePath}\n`;
    prompt += `文件名称：${_financialAuditFileName}\n\n`;

    prompt += `审查要求（请按顺序执行并输出）：\n`;
    prompt += `1. 【勾稽关系审查】验证资产负债表、利润表、现金流量表之间的逻辑一致性，检查资产=负债+所有者权益、净利润与现金流量匹配等核心勾稽关系；\n`;
    prompt += `2. 【税务风险审查】比对纳税申报数据与财务报表数据，识别增值税、企业所得税的申报差异与合规风险；\n`;
    prompt += `3. 【收入确认审查】检查收入确认的时点和金额是否符合《企业会计准则第14号——收入》，关注Q4占比异常、退换货率虚高等问题；\n`;
    prompt += `4. 【成本费用审查】识别成本操纵迹象、费用跨期入账、费用限额超标（如招待费、广告费）等问题；\n`;
    prompt += `5. 【关联交易审查】识别关联方交易、资本弱化指标（债资比2:1）、转让定价风险；\n`;
    prompt += `6. 【汇总报告】综合以上审查结果，生成一份结构化的财务报表审查意见书，列明：\n`;
    prompt += `   - 审查概况\n`;
    prompt += `   - 发现的主要问题及风险等级（高/中/低）\n`;
    prompt += `   - 合规性评价\n`;
    prompt += `   - 整改建议\n`;

    if (_financialAuditParsedData) {
        const d = _financialAuditParsedData;
        prompt += `\n前端已初步提取的数据：\n`;
        if (d.company_name) prompt += `公司：${d.company_name}\n`;
        if (d.report_period) prompt += `期间：${d.report_period}\n`;
        if (d.source_software) prompt += `来源：${d.source_software}\n`;
        if (Object.keys(d.balance_sheet).length > 0) {
            prompt += `\n资产负债表关键数据：\n`;
            Object.entries(d.balance_sheet).forEach(([k, v]) => { prompt += `  ${k}：${v.toLocaleString()}\n`; });
        }
        if (Object.keys(d.income_statement).length > 0) {
            prompt += `\n利润表关键数据：\n`;
            Object.entries(d.income_statement).forEach(([k, v]) => { prompt += `  ${k}：${v.toLocaleString()}\n`; });
        }
    }

    closeFinancialAuditWorkbench();
    newChat();

    fetch('/api/scenes/activate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            scene_id: parentScene.id,
            session_id: sessionId,
            scene_context: {
                id: parentScene.id,
                name: parentScene.name,
                system_prompt: parentScene.system_prompt,
                skill_name: parentScene.skill_name,
                file_path: _financialAuditFilePath || '',
                file_name: _financialAuditFileName || '',
                review_mode: 'full'
            }
        }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            showToast('已启动全面审查', 'success');
            setTimeout(() => sendMessage(prompt), 500);
        } else {
            showToast(data.message || '启动失败', 'error');
        }
    })
    .catch(() => showToast('网络错误', 'error'));
}

function showFinancialAuditSubSceneSelector() {
    document.getElementById('financial-audit-wb-subscene-selector').classList.remove('hidden');
    document.getElementById('financial-audit-wb-content-area').classList.add('hidden');
    document.getElementById('financial-audit-wb-actions').classList.add('hidden');
    _financialAuditSubScene = null;
    renderFinancialAuditSubScenes(_financialAuditScene);
}

function submitFinancialAuditReview() {
    const subScene = _financialAuditSubScene;
    const parentScene = _financialAuditScene;

    if (!subScene) {
        showToast('请选择审查项目', 'error');
        return;
    }

    const supplement = document.getElementById('fa-supplement-text')?.value?.trim() || '';

    // Build prompt based on sub-scene
    let prompt = '';

    switch (subScene.id) {
        case 'reconciliation_review':
            prompt = `请对以下财务报表进行勾稽关系审查：\n\n`;
            break;
        case 'tax_risk_review':
            prompt = `请对以下财务报表进行税务风险审查：\n\n`;
            break;
        case 'revenue_recognition_review':
            prompt = `请对以下财务报表进行收入确认合规性审查（基于CAS 14）：\n\n`;
            break;
        case 'cost_expense_review':
            prompt = `请对以下财务报表进行成本费用合规性审查：\n\n`;
            break;
        case 'related_party_review':
            prompt = `请对以下财务报表进行关联交易合规性审查：\n\n`;
            break;
        case 'generate_audit_report':
            prompt = `请根据以下财务数据生成完整的财务报表审查意见书：\n\n`;
            break;
        default:
            prompt = `请对以下财务数据进行${subScene.name}：\n\n`;
    }

    if (_financialAuditFilePath) {
        prompt += `文件路径：${_financialAuditFilePath}\n`;
        prompt += `文件名称：${_financialAuditFileName}\n\n`;
    }

    if (_financialAuditParsedData) {
        const d = _financialAuditParsedData;
        if (d.company_name) prompt += `公司：${d.company_name}\n`;
        if (d.report_period) prompt += `期间：${d.report_period}\n`;
        if (d.source_software) prompt += `财务软件：${d.source_software}\n\n`;

        if (Object.keys(d.balance_sheet).length > 0) {
            prompt += `【资产负债表】\n`;
            Object.entries(d.balance_sheet).forEach(([k, v]) => { prompt += `${k}：${v.toLocaleString()}\n`; });
            prompt += `\n`;
        }
        if (Object.keys(d.income_statement).length > 0) {
            prompt += `【利润表】\n`;
            Object.entries(d.income_statement).forEach(([k, v]) => { prompt += `${k}：${v.toLocaleString()}\n`; });
            prompt += `\n`;
        }
        if (Object.keys(d.cash_flow).length > 0) {
            prompt += `【现金流量表】\n`;
            Object.entries(d.cash_flow).forEach(([k, v]) => { prompt += `${k}：${v.toLocaleString()}\n`; });
            prompt += `\n`;
        }
    } else {
        prompt += `【注意】用户未上传具体财务文件，请基于一般性审查框架进行分析，并提示用户上传具体数据以获得更精准结果。\n\n`;
    }

    if (supplement) {
        prompt += `【补充信息】\n${supplement}\n\n`;
    }

    prompt += `审查要求：${subScene.description}\n`;
    prompt += `请严格按照${subScene.name}专家的标准，给出专业、严谨的审查意见。`;

    closeFinancialAuditWorkbench();
    newChat();

    fetch('/api/scenes/activate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            scene_id: subScene.id,
            session_id: sessionId,
            scene_context: {
                id: subScene.id,
                name: subScene.name,
                system_prompt: subScene.system_prompt,
                skill_name: parentScene.skill_name,
                file_path: _financialAuditFilePath || '',
                file_name: _financialAuditFileName || ''
            }
        }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            showToast(t('scenes_switched_sub').replace('{name}', subScene.name), 'success');
            setTimeout(() => sendMessage(prompt), 500);
        } else {
            showToast(data.message || '启动失败', 'error');
        }
    })
    .catch(() => showToast('网络错误', 'error'));
}

