let _voucherWorkbenchScene = null;
let _voucherWorkbenchSubScene = null;
let _voucherWorkbenchFormData = {};

// Common account codes for quick reference
const COMMON_ACCOUNT_CODES = [
    { code: '1001', name: '库存现金', category: '资产' },
    { code: '1002', name: '银行存款', category: '资产' },
    { code: '1012', name: '其他货币资金', category: '资产' },
    { code: '1122', name: '应收账款', category: '资产' },
    { code: '1123', name: '预付账款', category: '资产' },
    { code: '1221', name: '其他应收款', category: '资产' },
    { code: '1405', name: '库存商品', category: '资产' },
    { code: '1601', name: '固定资产', category: '资产' },
    { code: '1602', name: '累计折旧', category: '资产' },
    { code: '1701', name: '无形资产', category: '资产' },
    { code: '2202', name: '应付账款', category: '负债' },
    { code: '2211', name: '应付职工薪酬', category: '负债' },
    { code: '2221', name: '应交税费', category: '负债' },
    { code: '4001', name: '实收资本', category: '权益' },
    { code: '5001', name: '生产成本', category: '成本' },
    { code: '6001', name: '主营业务收入', category: '损益' },
    { code: '6401', name: '主营业务成本', category: '损益' },
    { code: '6601', name: '销售费用', category: '损益' },
    { code: '6602', name: '管理费用', category: '损益' },
    { code: '6603', name: '财务费用', category: '损益' }
];

function openVoucherWorkbench(scene) {
    _voucherWorkbenchScene = scene;
    _voucherWorkbenchSubScene = null;
    _voucherWorkbenchFormData = {};

    const modal = document.getElementById('voucher-workbench-modal');
    // 移动端：重置「功能说明」抽屉为收起状态
    const wbLeftPanel = modal ? modal.querySelector('.w-80') : null;
    if (wbLeftPanel) wbLeftPanel.classList.remove('open');
    const title = document.getElementById('voucher-wb-title');
    const subtitle = document.getElementById('voucher-wb-subtitle');

    title.textContent = scene.workbench_title || scene.name + '工作台';
    subtitle.textContent = '选择功能模块，快速处理凭证业务';

    // Reset UI
    document.getElementById('voucher-wb-subscene-selector').classList.remove('hidden');
    document.getElementById('voucher-wb-form-area').classList.add('hidden');
    document.getElementById('voucher-wb-actions').classList.add('hidden');

    // 清空上次打开时选中功能模块后残留的内容
    const voucherLeftContent = document.getElementById('voucher-wb-left-content');
    if (voucherLeftContent) voucherLeftContent.innerHTML = '';
    const voucherFormArea = document.getElementById('voucher-wb-form-area');
    if (voucherFormArea) voucherFormArea.innerHTML = '';
    const voucherIconBox = document.getElementById('voucher-wb-icon');
    if (voucherIconBox) voucherIconBox.style.background = '#64748b';

    // Render sub-scene cards
    renderVoucherWorkbenchSubScenes(scene);

    // Render account code list
    renderAccountCodeList();

    // Setup search
    setupAccountCodeSearch();

    modal.classList.remove('hidden');
}

function renderVoucherWorkbenchSubScenes(scene) {
    const grid = document.getElementById('voucher-wb-subscene-grid');
    grid.innerHTML = '';

    (scene.sub_scenes || []).forEach(sub => {
        const card = document.createElement('div');
        card.className = 'bg-white dark:bg-[#1A1A1A] rounded-xl border border-slate-200 dark:border-white/10 p-4 cursor-pointer hover:shadow-md hover:-translate-y-0.5 transition-all duration-200 group';
        card.innerHTML = `
            <div class="flex items-center gap-3 mb-2">
                <div class="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0" style="background:${sub.color || '#64748b'}15">
                    <i class="fas ${sub.icon || 'fa-file-invoice'}" style="color:${sub.color || '#64748b'}"></i>
                </div>
                <div class="min-w-0">
                    <h5 class="font-semibold text-slate-800 dark:text-slate-100 text-sm">${escapeHtml(sub.name)}</h5>
                </div>
            </div>
            <p class="text-xs text-slate-500 dark:text-slate-400 line-clamp-2">${escapeHtml(sub.description || '')}</p>
        `;
        card.onclick = () => selectVoucherWorkbenchSubScene(sub, card, grid);
        grid.appendChild(card);
    });
}

function selectVoucherWorkbenchSubScene(sub, card, grid) {
    _voucherWorkbenchSubScene = sub;

    // Highlight selected
    grid.querySelectorAll('div').forEach(c => {
        c.classList.remove('ring-2', 'ring-primary-500');
    });
    card.classList.add('ring-2', 'ring-primary-500');

    // Update header
    document.getElementById('voucher-wb-title').textContent = sub.name;
    document.getElementById('voucher-wb-subtitle').textContent = sub.description || '';
    document.getElementById('voucher-wb-icon').style.background = sub.color || '#64748b';

    // Show form area and actions
    document.getElementById('voucher-wb-form-area').classList.remove('hidden');
    document.getElementById('voucher-wb-actions').classList.remove('hidden');

    // Render form based on sub-scene params
    renderVoucherWorkbenchForm(sub);

    // Update left panel content based on sub-scene
    updateVoucherWorkbenchLeftPanel(sub);
}

function renderVoucherWorkbenchForm(sub) {
    const formArea = document.getElementById('voucher-wb-form-area');
    formArea.innerHTML = '';

    const params = sub.params || [];
    if (params.length === 0) {
        formArea.innerHTML = '<p class="text-sm text-slate-500 dark:text-slate-400">本功能无需额外参数，点击"发送到对话"即可开始。</p>';
        return;
    }

    const form = document.createElement('div');
    form.className = 'space-y-4';

    params.forEach(param => {
        const fieldWrapper = document.createElement('div');
        fieldWrapper.className = 'flex flex-col gap-1.5';

        const label = document.createElement('label');
        label.className = 'text-sm font-medium text-slate-700 dark:text-slate-200';
        label.textContent = param.label + (param.required ? ' *' : '');
        fieldWrapper.appendChild(label);

        let input;
        switch (param.type) {
            case 'textarea':
                input = document.createElement('textarea');
                input.className = 'w-full px-3 py-2 rounded-lg border border-slate-200 dark:border-white/10 bg-white dark:bg-slate-800 text-sm text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-primary-500 resize-none';
                input.rows = 4;
                input.placeholder = param.placeholder || '';
                break;
            case 'select':
                input = document.createElement('select');
                input.className = 'w-full px-3 py-2 rounded-lg border border-slate-200 dark:border-white/10 bg-white dark:bg-slate-800 text-sm text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-primary-500';
                const emptyOption = document.createElement('option');
                emptyOption.value = '';
                emptyOption.textContent = '请选择';
                input.appendChild(emptyOption);
                (param.options || []).forEach(opt => {
                    const option = document.createElement('option');
                    option.value = opt.value;
                    option.textContent = opt.label;
                    input.appendChild(option);
                });
                break;
            case 'number':
                input = document.createElement('input');
                input.type = 'number';
                input.className = 'w-full px-3 py-2 rounded-lg border border-slate-200 dark:border-white/10 bg-white dark:bg-slate-800 text-sm text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-primary-500';
                input.placeholder = param.placeholder || '';
                break;
            case 'date':
                input = document.createElement('input');
                input.type = 'date';
                input.className = 'w-full px-3 py-2 rounded-lg border border-slate-200 dark:border-white/10 bg-white dark:bg-slate-800 text-sm text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-primary-500';
                break;
            default:
                input = document.createElement('input');
                input.type = 'text';
                input.className = 'w-full px-3 py-2 rounded-lg border border-slate-200 dark:border-white/10 bg-white dark:bg-slate-800 text-sm text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-primary-500';
                input.placeholder = param.placeholder || '';
        }

        input.id = `voucher-param-${param.name}`;
        input.dataset.paramName = param.name;
        input.dataset.required = param.required ? 'true' : 'false';

        // Restore value if exists
        if (_voucherWorkbenchFormData[param.name] !== undefined) {
            input.value = _voucherWorkbenchFormData[param.name];
        }

        // Track changes
        input.addEventListener('change', () => {
            _voucherWorkbenchFormData[param.name] = input.value;
        });
        input.addEventListener('input', () => {
            _voucherWorkbenchFormData[param.name] = input.value;
        });

        fieldWrapper.appendChild(input);
        form.appendChild(fieldWrapper);
    });

    formArea.appendChild(form);

    // Setup submit button
    const submitBtn = document.getElementById('voucher-wb-submit-btn');
    const submitText = document.getElementById('voucher-wb-submit-text');

    // Update submit button based on sub-scene
    if (sub.id === 'voucher_template_download') {
        submitText.textContent = '生成模板';
        submitBtn.onclick = () => submitVoucherTemplateDownload();
    } else if (sub.id === 'account_mapping_config') {
        submitText.textContent = '发送到对话';
        submitBtn.onclick = () => submitVoucherAccountMapping();
    } else if (sub.id === 'auxiliary_data_management') {
        submitText.textContent = '发送到对话';
        submitBtn.onclick = () => submitVoucherAuxiliaryData();
    } else if (sub.id === 'exchange_rate_maintenance') {
        submitText.textContent = '发送到对话';
        submitBtn.onclick = () => submitVoucherExchangeRate();
    } else if (sub.id === 'voucher_history_query') {
        submitText.textContent = '发送到对话';
        submitBtn.onclick = () => submitVoucherHistoryQuery();
    } else {
        submitText.textContent = '发送到对话';
        submitBtn.onclick = () => submitVoucherEntryGeneration();
    }
}

function updateVoucherWorkbenchLeftPanel(sub) {
    const leftContent = document.getElementById('voucher-wb-left-content');

    if (sub.id === 'voucher_entry_generation') {
        leftContent.innerHTML = `
            <h4 class="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-4 flex items-center gap-2">
                <i class="fas fa-lightbulb text-amber-500"></i>
                分录生成提示
            </h4>
            <div class="space-y-3 text-xs text-slate-600 dark:text-slate-300">
                <div class="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-3">
                    <p class="font-medium mb-1">填写规范</p>
                    <p class="leading-relaxed">请清晰描述经济业务，包含：业务类型、涉及金额、收付款方式、往来对象等关键信息。</p>
                </div>
                <div class="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-3">
                    <p class="font-medium mb-1">示例</p>
                    <p class="leading-relaxed text-slate-500 dark:text-slate-400">"收到客户XX公司货款50,000元，款项已存入银行基本户"</p>
                </div>
            </div>
        `;
    } else if (sub.id === 'voucher_template_download') {
        leftContent.innerHTML = `
            <h4 class="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-4 flex items-center gap-2">
                <i class="fas fa-circle-info text-blue-500"></i>
                模板说明
            </h4>
            <div class="space-y-3 text-xs text-slate-600 dark:text-slate-300">
                <div class="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-3">
                    <p class="font-medium mb-1">支持格式</p>
                    <p class="leading-relaxed">金蝶K/3、用友U8/U9、SAP、Oracle Fusion 四种主流财务软件导入格式。</p>
                </div>
                <div class="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-3">
                    <p class="font-medium mb-1">使用流程</p>
                    <p class="leading-relaxed">1. 选择目标软件<br>2. 设置凭证字和日期范围<br>3. 生成并下载模板<br>4. 填写后导入财务系统</p>
                </div>
            </div>
        `;
    } else if (sub.id === 'account_mapping_config') {
        leftContent.innerHTML = `
            <h4 class="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-4 flex items-center gap-2">
                <i class="fas fa-book text-primary-500"></i>
                常用科目编码
            </h4>
            <div class="relative mb-3">
                <i class="fas fa-search absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-xs"></i>
                <input type="text" id="voucher-wb-account-search" placeholder="搜索科目编码或名称" class="w-full pl-8 pr-3 py-2 rounded-lg border border-slate-200 dark:border-white/10 bg-white dark:bg-slate-800 text-xs text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-primary-500">
            </div>
            <div id="voucher-wb-account-list" class="space-y-1 max-h-80 overflow-y-auto"></div>
        `;
        renderAccountCodeList();
        setupAccountCodeSearch();
    } else if (sub.id === 'auxiliary_data_management') {
        leftContent.innerHTML = `
            <h4 class="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-4 flex items-center gap-2">
                <i class="fas fa-address-book text-primary-500"></i>
                辅助核算类型
            </h4>
            <div class="space-y-2 text-xs text-slate-600 dark:text-slate-300">
                <div class="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-3">
                    <p class="font-medium mb-1">客户核算</p>
                    <p class="text-slate-500 dark:text-slate-400">用于应收账款、预收账款等科目</p>
                </div>
                <div class="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-3">
                    <p class="font-medium mb-1">供应商核算</p>
                    <p class="text-slate-500 dark:text-slate-400">用于应付账款、预付账款等科目</p>
                </div>
                <div class="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-3">
                    <p class="font-medium mb-1">员工核算</p>
                    <p class="text-slate-500 dark:text-slate-400">用于其他应收款、应付职工薪酬等科目</p>
                </div>
                <div class="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-3">
                    <p class="font-medium mb-1">项目/部门核算</p>
                    <p class="text-slate-500 dark:text-slate-400">用于成本、费用类科目</p>
                </div>
            </div>
        `;
    } else {
        // Default: account code reference
        leftContent.innerHTML = `
            <h4 class="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-4 flex items-center gap-2">
                <i class="fas fa-book-open text-primary-500"></i>
                常用科目速查
            </h4>
            <div class="relative mb-3">
                <i class="fas fa-search absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-xs"></i>
                <input type="text" id="voucher-wb-account-search" placeholder="搜索科目编码或名称" class="w-full pl-8 pr-3 py-2 rounded-lg border border-slate-200 dark:border-white/10 bg-white dark:bg-slate-800 text-xs text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-primary-500">
            </div>
            <div id="voucher-wb-account-list" class="space-y-1 max-h-80 overflow-y-auto"></div>
        `;
        renderAccountCodeList();
        setupAccountCodeSearch();
    }
}

function renderAccountCodeList(filter = '') {
    const list = document.getElementById('voucher-wb-account-list');
    if (!list) return;

    list.innerHTML = '';
    const filtered = COMMON_ACCOUNT_CODES.filter(acc =>
        acc.code.includes(filter) || acc.name.includes(filter) || acc.category.includes(filter)
    );

    filtered.forEach(acc => {
        const item = document.createElement('div');
        item.className = 'flex items-center justify-between px-2 py-1.5 rounded hover:bg-slate-100 dark:hover:bg-white/5 cursor-pointer group';
        item.innerHTML = `
            <div class="flex items-center gap-2">
                <span class="text-xs font-mono text-slate-500 dark:text-slate-400 w-10">${acc.code}</span>
                <span class="text-xs text-slate-700 dark:text-slate-200">${acc.name}</span>
            </div>
            <span class="text-[10px] px-1.5 py-0.5 rounded-full bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-400">${acc.category}</span>
        `;
        item.onclick = () => {
            navigator.clipboard.writeText(acc.code).then(() => {
                showToast(`已复制科目编码 ${acc.code}`, 'success');
            });
        };
        list.appendChild(item);
    });
}

function setupAccountCodeSearch() {
    const search = document.getElementById('voucher-wb-account-search');
    if (!search) return;
    search.oninput = (e) => renderAccountCodeList(e.target.value.trim());
}

function closeVoucherWorkbench() {
    document.getElementById('voucher-workbench-modal').classList.add('hidden');
    _voucherWorkbenchScene = null;
    _voucherWorkbenchSubScene = null;
    _voucherWorkbenchFormData = {};
}

function validateVoucherForm() {
    const params = _voucherWorkbenchSubScene.params || [];
    for (const param of params) {
        if (param.required) {
            const value = _voucherWorkbenchFormData[param.name];
            if (!value || value.trim() === '') {
                showToast(`请填写 ${param.label}`, 'error');
                return false;
            }
        }
    }
    return true;
}

// =====================================================================
// Voucher Workbench Submit Functions
// =====================================================================

function submitVoucherEntryGeneration() {
    if (!validateVoucherForm()) return;

    const businessDesc = _voucherWorkbenchFormData.business_desc || '';
    const software = _voucherWorkbenchFormData.software || '';
    const amount = _voucherWorkbenchFormData.amount || '';
    const auxiliary = _voucherWorkbenchFormData.auxiliary || '';

    const softwareMap = {
        'kingdee': '金蝶K/3',
        'yonyou': '用友U8/U9',
        'sap': 'SAP',
        'oracle': 'Oracle Fusion'
    };

    let prompt = `请根据以下业务描述生成会计分录：\n\n`;
    prompt += `业务描述：${businessDesc}\n`;
    if (amount) prompt += `金额：${amount} 元\n`;
    if (auxiliary) prompt += `辅助核算信息：${auxiliary}\n`;
    prompt += `目标财务软件：${softwareMap[software] || software}\n\n`;
    prompt += `要求：\n`;
    prompt += `1. 生成标准的借贷分录，确保借贷平衡\n`;
    prompt += `2. 使用标准会计科目编码和名称\n`;
    prompt += `3. 如有辅助核算需求，请标注辅助核算类型和对象\n`;
    prompt += `4. 以清晰的表格形式输出分录（包含：摘要、科目编码、科目名称、借方金额、贷方金额）\n`;
    prompt += `5. 简要说明该业务的会计处理要点\n\n`;
    prompt += `请直接生成会计分录。`;

    sendVoucherPrompt(prompt);
}

function submitVoucherTemplateDownload() {
    if (!validateVoucherForm()) return;

    const software = _voucherWorkbenchFormData.software || '';
    const voucherWord = _voucherWorkbenchFormData.voucher_word || '';
    const startDate = _voucherWorkbenchFormData.start_date || '';
    const endDate = _voucherWorkbenchFormData.end_date || '';
    const ledgerCode = _voucherWorkbenchFormData.ledger_code || '';

    const softwareMap = {
        'kingdee': '金蝶K/3',
        'yonyou': '用友U8/U9',
        'sap': 'SAP',
        'oracle': 'Oracle Fusion'
    };

    // Build template generation request
    const templateData = {
        software: software,
        voucher_word: voucherWord,
        start_date: startDate,
        end_date: endDate,
        ledger_code: ledgerCode
    };

    fetch('/api/voucher/generate-template', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(templateData)
    })
    .then(r => r.json())
    .then(result => {
        if (result.status !== 'success') {
            showToast(result.message || '模板生成失败', 'error');
            return;
        }

        const prompt = `我已生成 ${softwareMap[software] || software} 凭证导入模板，文件路径：${result.file_path}\n\n`;
        prompt += `模板信息：\n`;
        prompt += `- 凭证字：${voucherWord}\n`;
        prompt += `- 日期范围：${startDate} 至 ${endDate}\n`;
        if (ledgerCode) prompt += `- 账簿编码：${ledgerCode}\n`;
        prompt += `\n请：\n`;
        prompt += `1. 告知用户模板已生成，提供下载链接：${result.download_url}\n`;
        prompt += `2. 说明该模板的填写规范和注意事项\n`;
        prompt += `3. 给出一个填写示例\n`;

        sendVoucherPrompt(prompt);
    })
    .catch(err => {
        showToast('网络错误：' + err.message, 'error');
    });
}

function submitVoucherAccountMapping() {
    const action = _voucherWorkbenchFormData.action || 'view';

    let prompt = `请协助我进行科目映射配置管理。\n\n`;
    prompt += `操作类型：${action === 'view' ? '查看现有规则' : action === 'add' ? '新增规则' : '从Excel导入'}\n\n`;

    if (action === 'view') {
        prompt += `请列出当前已配置的科目映射规则，并说明每条规则的业务场景和适用条件。`;
    } else if (action === 'add') {
        prompt += `请指导我如何新增一条科目映射规则，包括：\n`;
        prompt += `1. 如何确定原始业务字段\n`;
        prompt += `2. 如何选择对应的目标会计科目\n`;
        prompt += `3. 如何设置映射逻辑的优先级\n`;
        prompt += `4. 如何测试新规则的正确性\n`;
        prompt += `\n请提供新增规则的步骤说明和示例。`;
    } else {
        prompt += `请说明从Excel导入科目映射规则的流程：\n`;
        prompt += `1. Excel文件的格式要求\n`;
        prompt += `2. 必需的字段和可选字段\n`;
        prompt += `3. 导入后的验证步骤\n`;
        prompt += `4. 常见错误及解决方法\n`;
        prompt += `\n请提供导入模板示例。`;
    }

    sendVoucherPrompt(prompt);
}

function submitVoucherAuxiliaryData() {
    const dataType = _voucherWorkbenchFormData.data_type || 'customer';

    const typeMap = {
        'customer': '客户',
        'supplier': '供应商',
        'employee': '员工',
        'project': '项目',
        'department': '部门'
    };

    let prompt = `请协助我管理${typeMap[dataType]}辅助核算资料。\n\n`;
    prompt += `资料类型：${typeMap[dataType]}\n\n`;
    prompt += `请：\n`;
    prompt += `1. 说明${typeMap[dataType]}档案的标准字段和填写规范\n`;
    prompt += `2. 提供档案维护的最佳实践建议\n`;
    prompt += `3. 说明该类型辅助核算在凭证中的应用场景\n`;
    prompt += `4. 如有常见问题，请提供解决方案\n`;

    sendVoucherPrompt(prompt);
}

function submitVoucherExchangeRate() {
    const action = _voucherWorkbenchFormData.action || 'view';

    const actionMap = {
        'view': '查看汇率',
        'add': '新增汇率',
        'update': '更新汇率'
    };

    let prompt = `请协助我进行汇率维护管理。\n\n`;
    prompt += `操作类型：${actionMap[action]}\n\n`;

    if (action === 'view') {
        prompt += `请说明汇率表的查看方法，包括：\n`;
        prompt += `1. 如何查看当前生效的汇率\n`;
        prompt += `2. 如何查看汇率历史变动\n`;
        prompt += `3. 汇率在凭证生成中的应用方式\n`;
    } else if (action === 'add') {
        prompt += `请指导我新增汇率记录，包括：\n`;
        prompt += `1. 需要填写的字段说明\n`;
        prompt += `2. 币种代码的标准（ISO 4217）\n`;
        prompt += `3. 生效日期的设置规则\n`;
        prompt += `4. 汇率精度的要求\n`;
    } else {
        prompt += `请指导我更新汇率记录，包括：\n`;
        prompt += `1. 更新汇率的触发条件\n`;
        prompt += `2. 更新时的注意事项\n`;
        prompt += `3. 历史汇率的保留策略\n`;
    }

    sendVoucherPrompt(prompt);
}

function submitVoucherHistoryQuery() {
    const queryType = _voucherWorkbenchFormData.query_type || 'all';

    const queryMap = {
        'all': '全部记录',
        'by_date': '按日期',
        'by_software': '按财务软件',
        'by_business': '按业务类型'
    };

    let prompt = `请协助我查询历史凭证记录。\n\n`;
    prompt += `查询方式：${queryMap[queryType]}\n\n`;
    prompt += `请：\n`;
    prompt += `1. 说明如何查看历史凭证记录\n`;
    prompt += `2. 介绍凭证记录的筛选和排序功能\n`;
    prompt += `3. 说明如何导出或重新下载已生成的凭证\n`;
    prompt += `4. 提供凭证审核的要点和注意事项\n`;

    sendVoucherPrompt(prompt);
}

function sendVoucherPrompt(prompt) {
    const subScene = _voucherWorkbenchSubScene;
    const parentScene = _voucherWorkbenchScene;

    // Close workbench
    closeVoucherWorkbench();

    // Create new chat session
    newChat();

    // Activate sub-scene
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
                skill_name: parentScene.skill_name
            }
        }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            showToast(t('scenes_switched_sub').replace('{name}', subScene.name), 'success');
            // Send prompt as user message
            setTimeout(() => {
                sendMessage(prompt);
            }, 500);
        } else {
            showToast(data.message || '启动失败', 'error');
        }
    })
    .catch(() => {
        showToast('网络错误，启动失败', 'error');
    });
}

