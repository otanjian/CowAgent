let _workbenchScene = null;
let _workbenchSubScene = null;
let _workbenchFileData = null;
let _workbenchHeaders = [];
let _workbenchMapping = {};

// Procurement workbench data source state
let _procurementDataSource = 'manual'; // 'manual' | 'file' | 'erp'
let _procurementFileData = null;
let _procurementFileHeaders = [];
let _procurementFileMapping = {};
let _procurementErpConfig = null;
let _procurementErpData = null;
let _procurementErpRawData = null;

// Skill-based multi-file upload state (for sales-quotation / procurement-comparison)
let _skillFiles = {}; // { fieldName: File | File[] }

// 招标文件解析场景文件状态
let _bidDocFile = null;

// 工作台全屏放大 / 还原（通用，效果与气袋排产工作台一致：切换 Tailwind 尺寸工具类）
function toggleWorkbenchMaximize(btn) {
    if (!btn) return;
    const modal = btn.closest('.fixed');
    const box = modal ? modal.firstElementChild : null;
    if (!box) return;
    const maximized = box.classList.contains('w-screen');
    if (!maximized) {
        // 记录原始类，移除尺寸/圆角/阴影/边框类，再切换为全屏类
        box.dataset.wbOrigClass = box.className;
        box.classList.remove('w-[90vw]', 'w-[95vw]', 'h-[85vh]', 'h-[90vh]', 'max-w-6xl', 'rounded-2xl', 'shadow-2xl', 'border');
        box.classList.add('w-screen', 'h-screen', 'max-w-none', 'rounded-none', 'shadow-none', 'border-0');
    } else {
        box.className = box.dataset.wbOrigClass || box.className;
        delete box.dataset.wbOrigClass;
    }
    const icon = btn.querySelector('i');
    if (icon) icon.className = maximized ? 'fas fa-expand text-slate-400' : 'fas fa-compress text-slate-400';
}

function openWorkbench(scene) {
    _workbenchScene = scene;
    _workbenchSubScene = null;
    _workbenchFileData = null;
    _workbenchHeaders = [];
    _workbenchMapping = {};
    _workbenchMultiFiles = {};
    _workbenchMultiData = {};
    _workbenchMultiHeaders = {};
    _procurementDataSource = 'manual';
    _procurementFileData = null;
    _procurementFileHeaders = [];
    _bidDocFile = null;
    _procurementFileMapping = {};
    _procurementErpData = null;
    _procurementErpRawData = null;
    _skillFiles = {};
    _totalRows = 0;

    const modal = document.getElementById('workbench-modal');
    // 移动端：重置「指标说明」抽屉为收起状态
    const wbLeftPanel = document.querySelector('#workbench-modal .w-80');
    if (wbLeftPanel) wbLeftPanel.classList.remove('open');
    const title = document.getElementById('workbench-title');
    const subtitle = document.getElementById('workbench-subtitle');
    const iconBox = document.getElementById('workbench-icon');
    const leftPanelTitle = document.querySelector('#workbench-modal .w-80 h4');
    const uploadModeTabs = document.getElementById('wb-upload-mode-tabs');
    const stepUploadText = document.getElementById('wb-step-upload-text');
    const mappingDesc = document.getElementById('wb-mapping-desc');
    const dataSourceTabs = document.getElementById('wb-data-source-tabs');
    const erpPanel = document.getElementById('wb-erp-panel');
    const detectEl = document.getElementById('wb-auto-detect');
    const detectResult = document.getElementById('wb-detect-result');
    const fileInfo = document.getElementById('wb-file-info');
    const dataStatus = document.getElementById('wb-data-status');

    title.textContent = scene.workbench_title || scene.name + '工作台';
    iconBox.style.background = '#64748b';

    // 重置界面状态
    if (detectEl) detectEl.classList.add('hidden');
    if (detectResult) detectResult.textContent = '';
    if (fileInfo) fileInfo.classList.add('hidden');
    if (dataStatus) dataStatus.textContent = '';

    // 清空上次打开时选中功能模块后残留的内容，避免关闭重开后显示旧缓存
    _workbenchUploadMode = 'single';
    switchUploadMode('single');
    const indicatorsEl = document.getElementById('workbench-indicators');
    if (indicatorsEl) indicatorsEl.innerHTML = '';
    const mappingListEl = document.getElementById('wb-mapping-list');
    if (mappingListEl) mappingListEl.innerHTML = '';
    const previewHeadEl = document.getElementById('wb-preview-head');
    if (previewHeadEl) previewHeadEl.innerHTML = '';
    const previewBodyEl = document.getElementById('wb-preview-body');
    if (previewBodyEl) previewBodyEl.innerHTML = '';
    const sheetListEl = document.getElementById('wb-sheet-list');
    if (sheetListEl) sheetListEl.innerHTML = '';
    const sheetSelectorEl = document.getElementById('wb-sheet-selector');
    if (sheetSelectorEl) sheetSelectorEl.classList.add('hidden');
    const requiredHintEl = document.getElementById('wb-required-hint');
    if (requiredHintEl) requiredHintEl.classList.add('hidden');
    const hintListEl = document.getElementById('wb-hint-list');
    if (hintListEl) hintListEl.innerHTML = '';
    const fileInputEl = document.getElementById('wb-file-input');
    if (fileInputEl) fileInputEl.value = '';
    // 重置 ERP 面板
    const erpSystemEl = document.getElementById('wb-erp-system');
    if (erpSystemEl) {
        erpSystemEl.value = '';
        erpSystemEl.disabled = false;
    }
    const erpSystemRowEl = document.getElementById('wb-erp-system-row');
    if (erpSystemRowEl) erpSystemRowEl.classList.remove('hidden');
    const erpConnectionEl = document.getElementById('wb-erp-connection');
    if (erpConnectionEl) {
        erpConnectionEl.innerHTML = '<option value="">请选择连接配置</option>';
        erpConnectionEl.disabled = false;
    }
    const erpConnectionRowEl = document.getElementById('wb-erp-connection-row');
    if (erpConnectionRowEl) erpConnectionRowEl.classList.add('hidden');
    const erpFiltersEl = document.getElementById('wb-erp-filters');
    if (erpFiltersEl) erpFiltersEl.classList.add('hidden');
    const erpSyncBtnEl = document.getElementById('wb-erp-sync-btn');
    if (erpSyncBtnEl) erpSyncBtnEl.classList.add('hidden');
    const erpMaterialsEl = document.getElementById('wb-erp-filter-materials');
    if (erpMaterialsEl) erpMaterialsEl.value = '';
    const erpMaterialNamesEl = document.getElementById('wb-erp-filter-material-names');
    if (erpMaterialNamesEl) erpMaterialNamesEl.value = '';
    const erpStartDateEl = document.getElementById('wb-erp-filter-start-date');
    if (erpStartDateEl) erpStartDateEl.value = '';
    const erpEndDateEl = document.getElementById('wb-erp-filter-end-date');
    if (erpEndDateEl) erpEndDateEl.value = '';
    const erpMaxRowsEl = document.getElementById('wb-erp-max-rows');
    if (erpMaxRowsEl) erpMaxRowsEl.value = '1000';

    // 根据场景类别设置不同的界面内容
    const formCategories = ['procurement', 'sales', 'production', 'hr'];
    if (formCategories.includes(scene.category)) {
        // 表单录入场景（采购、销售、生产、人事）
        subtitle.textContent = '选择功能模块，选择数据来源，生成专业方案';
        if (leftPanelTitle) {
            leftPanelTitle.innerHTML = '<i class="fas fa-book-open text-primary-500"></i>功能说明';
        }
        // 隐藏上传模式切换按钮
        if (uploadModeTabs) uploadModeTabs.classList.add('hidden');
        // 修改步骤标题
        if (stepUploadText) stepUploadText.textContent = '录入业务信息';
        // 修改字段映射说明
        if (mappingDesc) mappingDesc.textContent = '请确认以下业务信息字段是否完整';
        // 数据来源切换Tab：选中功能模块后才显示
        if (dataSourceTabs) dataSourceTabs.classList.add('hidden');
    } else {
        // 财务场景（默认）
        subtitle.textContent = '选择分析维度，上传数据，生成专业报告';
        if (leftPanelTitle) {
            leftPanelTitle.innerHTML = '<i class="fas fa-book-open text-primary-500"></i>指标说明';
        }
        // 上传模式切换按钮：选中分析维度后才显示
        if (uploadModeTabs) uploadModeTabs.classList.add('hidden');
        // 恢复步骤标题
        if (stepUploadText) stepUploadText.textContent = '上传财务数据';
        // 恢复字段映射说明
        if (mappingDesc) mappingDesc.textContent = '请将上传文件中的列名与标准财务字段进行匹配';
        // 隐藏数据来源切换Tab和ERP面板
        if (dataSourceTabs) dataSourceTabs.classList.add('hidden');
        if (erpPanel) erpPanel.classList.add('hidden');
    }

    // Reset steps：上传/操作区域等选中功能模块（或分析维度）后才显示
    document.getElementById('wb-step-upload').classList.add('hidden');
    document.getElementById('wb-step-mapping').classList.add('hidden');
    document.getElementById('wb-step-submit').classList.add('hidden');

    // Render sub-scene selector as cards in right panel (above upload)
    renderWorkbenchSubScenes(scene);

    modal.classList.remove('hidden');

    // Setup file upload
    setupWorkbenchFileUpload();
}

// 打开父场景工作台并自动定位到指定子场景（/scene 弹层选中工作台子场景用）
function openWorkbenchWithSubScene(parentScene, sub) {
    openSceneById(parentScene);
    // openWorkbench 同步渲染子场景卡片，DOM 就绪后触发目标卡片点击
    const card = document.querySelector(`#wb-subscene-selector [data-sub-id="${sub.id}"]`);
    if (card) card.click();
}

function renderWorkbenchSubScenes(scene) {
    const rightPanel = document.querySelector('#workbench-modal .flex-1.overflow-y-auto');
    if (!rightPanel) {
        console.error('renderWorkbenchSubScenes: rightPanel not found');
        return;
    }
    // Remove existing sub-scene selector if any
    const existing = document.getElementById('wb-subscene-selector');
    if (existing) existing.remove();

    const selector = document.createElement('div');
    selector.id = 'wb-subscene-selector';
    selector.className = 'mb-6';
    const formCategories = ['procurement', 'sales', 'production', 'hr'];
    const selectorTitle = formCategories.includes(scene.category) ? '选择功能模块' : '选择分析维度';
    selector.innerHTML = `<h4 class="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-3">${selectorTitle}</h4>`;

    const grid = document.createElement('div');
    grid.className = 'grid grid-cols-2 lg:grid-cols-3 gap-3';

    const cardMap = {};
    (scene.sub_scenes || []).forEach(sub => {
        // visible 为 false 时隐藏该子场景（undefined 默认显示）
        if (sub.visible === false) return;
        const card = document.createElement('div');
        card.className = 'bg-white dark:bg-[#1A1A1A] rounded-xl border border-slate-200 dark:border-white/10 p-4 cursor-pointer hover:shadow-md hover:-translate-y-0.5 transition-all duration-200 group';
        card.dataset.subId = sub.id;  // 供 /scene 弹层自动定位该子场景

        // 通用 demo 链接支持（后端已将 skill:// 解析为绝对路径），点击应用内预览（data-demo-preview 委托）
        let demoLinksHtml = '';
        const demoLinks = [];
        const demoLink = (url, label, cls) =>
            `<a href="${_toWebUrl(url)}" data-demo-preview data-title="${label}" class="text-[10px] px-2 py-0.5 rounded ${cls} transition-colors font-medium cursor-pointer" onclick="event.stopPropagation();">${label}</a>`;
        if (sub.demo_md) {
            demoLinks.push(demoLink(sub.demo_md, 'DEMO-Markdown', 'bg-emerald-50 text-emerald-600 hover:bg-emerald-100'));
        }
        if (sub.demo_html) {
            demoLinks.push(demoLink(sub.demo_html, 'DEMO-HTML', 'bg-blue-50 text-blue-600 hover:bg-blue-100'));
        }
        if (sub.demo_url) {
            const label = sub.demo_url.includes('gantt') ? 'DEMO-甘特图' : 'DEMO-HTML';
            demoLinks.push(demoLink(sub.demo_url, label, 'bg-blue-50 text-blue-600 hover:bg-blue-100'));
        }
        if (demoLinks.length > 0) {
            demoLinksHtml = `<div class="flex items-center gap-2 mt-2">${demoLinks.join('')}</div>`;
        }

        card.innerHTML = `
            <div class="flex items-center gap-3 mb-2">
                <div class="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0" style="background:${sub.color || '#64748b'}15">
                    <i class="fas ${sub.icon || 'fa-chart-bar'}" style="color:${sub.color || '#64748b'}"></i>
                </div>
                <div class="min-w-0">
                    <h5 class="font-semibold text-slate-800 dark:text-slate-100 text-sm">${escapeHtml(sub.name)}</h5>
                </div>
            </div>
            <p class="text-xs text-slate-500 dark:text-slate-400 line-clamp-2">${escapeHtml(sub.description || '')}</p>
            ${demoLinksHtml}
        `;
        card.onclick = () => selectWorkbenchSubScene(sub, card, grid);
        grid.appendChild(card);
        cardMap[sub.id] = { sub, card };
    });

    selector.appendChild(grid);
    rightPanel.insertBefore(selector, rightPanel.firstChild);

    // 默认选择第一个功能模块（若有），数据来源由用户再自行选择
    const firstSub = (scene.sub_scenes || []).find(s => s.visible !== false);
    if (firstSub && cardMap[firstSub.id]) {
        const { sub, card } = cardMap[firstSub.id];
        selectWorkbenchSubScene(sub, card, grid);
    }
}

function selectWorkbenchSubScene(sub, card, grid) {
    _workbenchSubScene = sub;
    _workbenchFileData = null;
    _workbenchHeaders = [];
    _workbenchMapping = {};
    _workbenchMultiFiles = {};
    _workbenchMultiData = {};
    _workbenchMultiHeaders = {};
    _procurementFileData = null;
    _procurementFileHeaders = [];
    _procurementFileMapping = {};
    _skillFiles = {};
    _totalRows = 0;

    // Highlight selected
    grid.querySelectorAll('div').forEach(c => {
        c.classList.remove('ring-2', 'ring-primary-500');
    });
    card.classList.add('ring-2', 'ring-primary-500');

    // 重置界面状态
    const detectEl = document.getElementById('wb-auto-detect');
    const detectResult = document.getElementById('wb-detect-result');
    const fileInfo = document.getElementById('wb-file-info');
    const dataStatus = document.getElementById('wb-data-status');
    if (detectEl) detectEl.classList.add('hidden');
    if (detectResult) detectResult.textContent = '';
    if (fileInfo) fileInfo.classList.add('hidden');
    if (dataStatus) dataStatus.textContent = '';
    document.getElementById('wb-step-upload').classList.remove('hidden');
    document.getElementById('wb-step-mapping').classList.add('hidden');
    document.getElementById('wb-step-submit').classList.add('hidden');

    // Update left panel with indicators
    const indicatorsContainer = document.getElementById('workbench-indicators');
    indicatorsContainer.innerHTML = '';

    (sub.indicators || []).forEach(ind => {
        const el = document.createElement('div');
        el.className = 'bg-slate-50 dark:bg-slate-800/50 rounded-lg p-3';
        // 根据当前场景类别决定显示样式
    const formCategories = ['procurement', 'sales', 'production', 'hr'];
    const isFormCategory = _workbenchScene && formCategories.includes(_workbenchScene.category);
    if (isFormCategory) {
            // 采购场景：显示为功能说明样式（不强调公式）
            el.innerHTML = `
                <h5 class="font-semibold text-slate-700 dark:text-slate-200 text-xs mb-1">${escapeHtml(ind.name)}</h5>
                <div class="text-[10px] text-slate-500 dark:text-slate-400 mb-1 font-medium">${escapeHtml(ind.formula)}</div>
                <p class="text-[10px] text-slate-500 dark:text-slate-400 leading-relaxed">${escapeHtml(ind.meaning)}</p>
            `;
        } else {
            // 财务场景：显示为指标说明样式（带公式）
            el.innerHTML = `
                <h5 class="font-semibold text-slate-700 dark:text-slate-200 text-xs mb-1">${escapeHtml(ind.name)}</h5>
                <div class="text-[10px] text-slate-500 dark:text-slate-400 mb-1 font-mono bg-slate-100 dark:bg-slate-700/50 px-2 py-1 rounded">${escapeHtml(ind.formula)}</div>
                <p class="text-[10px] text-slate-500 dark:text-slate-400 leading-relaxed">${escapeHtml(ind.meaning)}</p>
            `;
        }
        indicatorsContainer.appendChild(el);
    });

    // Update header color
    const iconBox = document.getElementById('workbench-icon');
    iconBox.style.background = sub.color || '#64748b';

    // Update title
    document.getElementById('workbench-title').textContent = sub.name;
    document.getElementById('workbench-subtitle').textContent = sub.description || '';

    // Update required statements hint
    updateRequiredStatementsHint(sub);

    // 根据场景类别切换上传区域显示
    const singleUpload = document.getElementById('wb-single-upload');
    const multiUpload = document.getElementById('wb-multi-upload');
    const formCategories = ['procurement', 'sales', 'production', 'hr'];
    const isFormCategory = _workbenchScene && formCategories.includes(_workbenchScene.category);

    if (isFormCategory) {
        // 表单录入场景（采购、销售、生产、人事）：根据数据来源显示不同UI
        if (singleUpload) singleUpload.classList.add('hidden');
        if (multiUpload) multiUpload.classList.add('hidden');
        // 选中功能模块后显示上传/操作区域
        const stepUploadEl = document.getElementById('wb-step-upload');
        if (stepUploadEl) stepUploadEl.classList.remove('hidden');
        // 招标文件解析子场景：隐藏数据来源切换，强制手动录入模式
        // 采购比价分析子场景：隐藏手动录入/文件导入，强制 ERP 同步模式
        const dataSourceTabs = document.getElementById('wb-data-source-tabs');
        if (sub.id === 'bid_doc_analysis') {
            if (dataSourceTabs) dataSourceTabs.classList.add('hidden');
            _procurementDataSource = 'manual';
            // 确保从 ERP 模式切换回来时隐藏 ERP 面板
            const erpPanel = document.getElementById('wb-erp-panel');
            if (erpPanel) erpPanel.classList.add('hidden');
        } else if (sub.id === 'supplier_quote_comparison') {
            if (dataSourceTabs) dataSourceTabs.classList.add('hidden');
            _procurementDataSource = 'erp';
            // 显示 ERP 面板
            const erpPanel = document.getElementById('wb-erp-panel');
            if (erpPanel) {
                erpPanel.classList.remove('hidden');
            }
            // 默认选择 SAP S/4HANA
            const systemSelect = document.getElementById('wb-erp-system');
            if (systemSelect && !systemSelect.value) {
                systemSelect.value = 'sap';
            }
            // 触发系统切换：显示连接行/过滤器/同步按钮并渲染连接选项
            onErpSystemChange();
            // 加载连接配置数据
            loadErpConnectionConfig();
            // 存在默认连接时锁定系统/连接下拉框并选中默认连接
            applyDefaultErpConnection();
            // 默认时间范围：近三年
            const endInput = document.getElementById('wb-erp-filter-end-date');
            const startInput = document.getElementById('wb-erp-filter-start-date');
            if (endInput && !endInput.value) {
                const today = new Date();
                const threeYearsAgo = new Date();
                threeYearsAgo.setFullYear(today.getFullYear() - 3);
                endInput.value = today.toISOString().split('T')[0];
                startInput.value = threeYearsAgo.toISOString().split('T')[0];
            }
        } else {
            // 常规功能模块：显示数据来源切换 Tab，但不默认选择任何数据来源，由用户点击选择
            if (dataSourceTabs) dataSourceTabs.classList.remove('hidden');
            _procurementDataSource = '';
            switchDataSource('');
        }
        // 根据当前数据来源渲染对应UI
        renderProcurementDataSourceUI(sub);
    } else {
        // 财务场景：选中分析维度后显示上传区域与上传模式切换
        const stepUploadEl = document.getElementById('wb-step-upload');
        if (stepUploadEl) stepUploadEl.classList.remove('hidden');
        const uploadModeTabsEl = document.getElementById('wb-upload-mode-tabs');
        if (uploadModeTabsEl) uploadModeTabsEl.classList.remove('hidden');
        // 财务场景：显示文件上传
        if (singleUpload) singleUpload.classList.remove('hidden');
        if (multiUpload) {
            // 根据当前模式显示
            const currentMode = _workbenchUploadMode || 'single';
            if (currentMode === 'multi') {
                multiUpload.classList.remove('hidden');
            } else {
                multiUpload.classList.add('hidden');
            }
        }
        // 移除业务表单（如果存在）
        const existingForm = document.getElementById('wb-procurement-form');
        if (existingForm) existingForm.remove();
    }
}

// 根据数据来源渲染采购场景UI
function renderProcurementDataSourceUI(sub) {
    // 移除已存在的表单和导入区域
    const existingForm = document.getElementById('wb-procurement-form');
    if (existingForm) existingForm.remove();
    const existingImport = document.getElementById('wb-procurement-import');
    if (existingImport) existingImport.remove();
    const existingSkillImport = document.getElementById('wb-skill-import');
    if (existingSkillImport) existingSkillImport.remove();
    const existingPreview = document.getElementById('wb-procurement-preview');
    if (existingPreview) existingPreview.remove();

    // 招标文件解析子场景：特殊处理，直接进入对话
    if (sub && sub.id === 'bid_doc_analysis') {
        renderBidDocAnalysisPanel(sub);
        return;
    }

    if (_procurementDataSource === 'manual') {
        renderProcurementForm(sub);
    } else if (_procurementDataSource === 'file') {
        // 销售报价 / 采购比价使用多文件上传，其他保持原单文件导入
        const skillMultiFile = ['sales-quotation', 'procurement-comparison'];
        if (sub.skill_name && skillMultiFile.includes(sub.skill_name)) {
            renderSkillFileImport(sub);
        } else {
            renderProcurementFileImport(sub);
        }
    } else if (_procurementDataSource === 'erp') {
        renderProcurementErpPreview(sub);
    }

    // 根据子场景更新 ERP 过滤器标签（物料/供应商切换）
    updateErpFilterLabels(sub);
}

// 渲染招标文件解析特殊面板
function renderBidDocAnalysisPanel(sub) {
    const existingForm = document.getElementById('wb-procurement-form');
    if (existingForm) existingForm.remove();
    const existingImport = document.getElementById('wb-procurement-import');
    if (existingImport) existingImport.remove();
    const existingSkillImport = document.getElementById('wb-skill-import');
    if (existingSkillImport) existingSkillImport.remove();
    const existingPreview = document.getElementById('wb-procurement-preview');
    if (existingPreview) existingPreview.remove();

    const stepUpload = document.getElementById('wb-step-upload');
    if (!stepUpload) return;

    const panel = document.createElement('div');
    panel.id = 'wb-procurement-form';
    panel.className = 'mt-4';
    panel.innerHTML = `
        <div class="bg-slate-50 dark:bg-slate-800/50 rounded-xl p-6">
            <div class="text-center mb-5">
                <div class="w-14 h-14 rounded-2xl bg-primary-50 dark:bg-primary-900/20 flex items-center justify-center mx-auto mb-3">
                    <i class="fas fa-file-contract text-primary-500 text-xl"></i>
                </div>
                <h5 class="font-semibold text-slate-800 dark:text-slate-100 text-sm mb-1">招标文件解析与投标辅助</h5>
                <p class="text-xs text-slate-500 dark:text-slate-400 leading-relaxed">
                    上传招标文件（PDF/Word）后自动结构化解析，<br>
                    生成 Word 分析报告和交互式 HTML 投标辅助工作台。
                </p>
            </div>
            <div id="bid-doc-upload-zone" class="border-2 border-dashed border-slate-300 dark:border-slate-600 rounded-xl p-6 text-center hover:border-primary-400 dark:hover:border-primary-500 transition-colors cursor-pointer">
                <div class="w-12 h-12 rounded-2xl bg-slate-100 dark:bg-slate-700 flex items-center justify-center mx-auto mb-2">
                    <i class="fas fa-cloud-arrow-up text-slate-400 text-lg"></i>
                </div>
                <p class="text-sm text-slate-600 dark:text-slate-300 mb-1">点击或拖拽上传招标文件</p>
                <p class="text-xs text-slate-400 dark:text-slate-500">支持 PDF、Word（.doc / .docx）</p>
                <input type="file" id="bid-doc-file-input" accept=".pdf,.doc,.docx" class="hidden">
            </div>
            <div id="bid-doc-file-info" class="hidden mt-3 flex items-center gap-3 p-3 bg-white dark:bg-[#1A1A1A] rounded-lg border border-slate-200 dark:border-white/10">
                <i class="fas fa-file-lines text-primary-500"></i>
                <div class="flex-1 min-w-0">
                    <p id="bid-doc-file-name" class="text-sm text-slate-700 dark:text-slate-200 truncate"></p>
                    <p id="bid-doc-file-size" class="text-xs text-slate-400"></p>
                </div>
                <button onclick="clearBidDocFile()" class="text-slate-400 hover:text-red-500 transition-colors cursor-pointer">
                    <i class="fas fa-trash-can"></i>
                </button>
            </div>
            <div id="bid-doc-actions" class="hidden mt-4 flex items-center gap-2">
                <button onclick="activateBidDocAnalysisScene()" class="flex-1 px-5 py-2.5 rounded-lg text-sm bg-primary-500 hover:bg-primary-600 text-white font-medium transition-colors flex items-center justify-center gap-2 cursor-pointer">
                    <i class="fas fa-wand-magic-sparkles"></i>
                    开始解析
                </button>
            </div>
        </div>
    `;
    stepUpload.appendChild(panel);

    // Setup upload handlers
    const dropZone = document.getElementById('bid-doc-upload-zone');
    const fileInput = document.getElementById('bid-doc-file-input');
    if (dropZone && fileInput) {
        dropZone.addEventListener('click', () => fileInput.click());
        dropZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropZone.classList.add('border-primary-400', 'dark:border-primary-500');
        });
        dropZone.addEventListener('dragleave', () => {
            dropZone.classList.remove('border-primary-400', 'dark:border-primary-500');
        });
        dropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropZone.classList.remove('border-primary-400', 'dark:border-primary-500');
            const files = e.dataTransfer.files;
            if (files.length > 0) handleBidDocFile(files[0]);
        });
        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) handleBidDocFile(e.target.files[0]);
        });
    }
}

function handleBidDocFile(file) {
    const allowedTypes = ['application/pdf', 'application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'];
    const allowedExts = ['.pdf', '.doc', '.docx'];
    const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
    const isValidType = allowedTypes.includes(file.type) || allowedExts.includes(ext);
    if (!isValidType) {
        showToast('请上传 PDF 或 Word 格式的招标文件', 'error');
        return;
    }
    _bidDocFile = file;
    const info = document.getElementById('bid-doc-file-info');
    const nameEl = document.getElementById('bid-doc-file-name');
    const sizeEl = document.getElementById('bid-doc-file-size');
    nameEl.textContent = file.name;
    sizeEl.textContent = formatFileSize(file.size);
    info.classList.remove('hidden');
    document.getElementById('bid-doc-actions').classList.remove('hidden');
}

function clearBidDocFile() {
    _bidDocFile = null;
    const info = document.getElementById('bid-doc-file-info');
    const actions = document.getElementById('bid-doc-actions');
    const input = document.getElementById('bid-doc-file-input');
    if (info) info.classList.add('hidden');
    if (actions) actions.classList.add('hidden');
    if (input) input.value = '';
}

// 激活招标文件解析场景并进入对话
function activateBidDocAnalysisScene() {
    const sub = _workbenchSubScene;
    if (!sub) return;
    if (!_bidDocFile) {
        showToast('请先上传招标文件', 'error');
        return;
    }

    const reader = new FileReader();
    reader.onload = (e) => {
        const arrayBuffer = e.target.result;
        const bytes = new Uint8Array(arrayBuffer);
        let binary = '';
        for (let i = 0; i < bytes.byteLength; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        const base64 = btoa(binary);

        // Upload file to server
        fetch('/api/workbench/upload', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                files: {
                    bid_doc: {
                        filename: _bidDocFile.name,
                        content: base64,
                        is_base64: true
                    }
                },
                session_id: sessionId
            })
        })
        .then(r => r.json())
        .then(uploadResult => {
            if (uploadResult.status !== 'success') {
                showToast(uploadResult.message || '文件上传失败', 'error');
                return;
            }

            const filePath = uploadResult.file_path || '';

            closeWorkbench();
            newChat();

            // Activate scene with file path in context
            return fetch('/api/scenes/activate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    scene_id: sub.id,
                    session_id: sessionId,
                    scene_context: {
                        id: sub.id,
                        name: sub.name,
                        system_prompt: sub.system_prompt,
                        skill_name: 'bid-analysis',
                        workbench_files: uploadResult.files || {}
                    }
                }),
            })
            .then(r => r.json())
            .then(data => {
                if (data.status === 'success') {
                    showToast(t('scenes_switched_sub').replace('{name}', sub.name), 'success');
                    activeSceneContext = sub;
                    // Auto-send analysis prompt
                    const prompt = `请解析以下招标文件，生成结构化分析报告和投标辅助工作台：\n\n文件路径：${filePath}\n\n请按照 bid-analysis 技能的工作流程执行：\n1. 提取文本内容\n2. 结构化解析（项目信息、评标评分、技术要求、商务条款、暗标规范、风险、资质、人员、SOP）\n3. 生成 Word 分析报告\n4. 生成 HTML 投标辅助工作台\n\n如果文件是扫描件或无法提取文本，请使用 OCR 工具处理。`;
                    setTimeout(() => sendMessage(prompt), 600);
                } else {
                    showToast(data.message || t('scenes_switch_failed'), 'error');
                }
            });
        })
        .catch(() => {
            showToast('网络错误，请重试', 'error');
        });
    };
    reader.readAsArrayBuffer(_bidDocFile);
}

// 渲染采购场景表单（手动录入）
function renderProcurementForm(sub) {
    // 移除已存在的表单
    const existingForm = document.getElementById('wb-procurement-form');
    if (existingForm) existingForm.remove();

    const stepUpload = document.getElementById('wb-step-upload');
    if (!stepUpload) return;

    const form = document.createElement('div');
    form.id = 'wb-procurement-form';
    form.className = 'space-y-3 mt-4';

    // 字段类型配置
    const fieldTypes = sub.field_types || {};

    // 渲染单个字段
    function renderField(field, isRequired) {
        const fieldConfig = fieldTypes[field];
        const requiredMark = isRequired ? '<span class="text-red-500">*</span>' : '';
        const labelClass = isRequired ? 'text-slate-700 dark:text-slate-200' : 'text-slate-500 dark:text-slate-400';
        const placeholder = isRequired ? `请输入${field}` : `请输入${field}（选填）`;

        if (fieldConfig && fieldConfig.type === 'select') {
            // 下拉框
            const options = fieldConfig.options || [];
            const optionsHtml = options.map(opt => `<option value="${escapeHtml(opt)}">${escapeHtml(opt)}</option>`).join('');
            return `
                <label class="block text-xs font-medium ${labelClass} mb-1">${escapeHtml(field)} ${requiredMark}</label>
                <select name="${escapeHtml(field)}" class="w-full px-3 py-2 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-sm text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent">
                    <option value="">请选择${escapeHtml(field)}</option>
                    ${optionsHtml}
                </select>
            `;
        } else {
            // 文本输入框
            return `
                <label class="block text-xs font-medium ${labelClass} mb-1">${escapeHtml(field)} ${requiredMark}</label>
                <input type="text" name="${escapeHtml(field)}" placeholder="${escapeHtml(placeholder)}" class="w-full px-3 py-2 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-sm text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent">
            `;
        }
    }

    // 必填字段
    const requiredFields = sub.required_fields || [];
    requiredFields.forEach(field => {
        const fieldDiv = document.createElement('div');
        fieldDiv.innerHTML = renderField(field, true);
        form.appendChild(fieldDiv);
    });

    // 选填字段
    const optionalFields = sub.optional_fields || [];
    if (optionalFields.length > 0) {
        const optionalDiv = document.createElement('div');
        optionalDiv.className = 'pt-2 border-t border-slate-200 dark:border-slate-700';
        optionalDiv.innerHTML = `<p class="text-xs text-slate-500 dark:text-slate-400 mb-2">选填信息</p>`;
        optionalFields.forEach(field => {
            const fieldDiv = document.createElement('div');
            fieldDiv.className = 'mb-2';
            fieldDiv.innerHTML = renderField(field, false);
            optionalDiv.appendChild(fieldDiv);
        });
        form.appendChild(optionalDiv);
    }

    // 提交按钮
    const submitDiv = document.createElement('div');
    submitDiv.className = 'pt-4';
    submitDiv.innerHTML = `
        <button onclick="submitProcurementForm()" class="w-full px-5 py-2.5 rounded-lg text-sm bg-primary-500 hover:bg-primary-600 text-white font-medium transition-colors flex items-center justify-center gap-2">
            <i class="fas fa-wand-magic-sparkles"></i>
            生成方案
        </button>
    `;
    form.appendChild(submitDiv);

    stepUpload.appendChild(form);
}

// 渲染技能多文件上传界面（销售报价 / 采购比价）
function renderSkillFileImport(sub) {
    const existingImport = document.getElementById('wb-skill-import');
    if (existingImport) existingImport.remove();

    const stepUpload = document.getElementById('wb-step-upload');
    if (!stepUpload) return;

    const importConfig = sub.import_config || {};
    const acceptTypes = importConfig.accept || '.pdf,.docx,.xlsx,.xls,.txt,.md';
    const multiFileFields = ['供应商报价文件'];

    const requiredFields = sub.required_fields || [];
    const optionalFields = sub.optional_fields || [];

    // Build template download links if configured
    const templates = importConfig.templates || [];
    let templatesHtml = '';
    if (templates.length > 0) {
        const links = templates.map(t => {
            const url = t.demo_url || t.url || '';
            const encodedUrl = url ? encodeURIComponent(url) : '';
            return `<a href="/api/file?path=${encodedUrl}" download="${escapeHtml(t.name)}" class="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-xs text-slate-600 dark:text-slate-300 transition-colors">
                <i class="fas fa-download text-[10px]"></i>${escapeHtml(t.name)}
            </a>`;
        }).join('');
        templatesHtml = `
            <div class="mt-4">
                <p class="text-xs text-slate-500 dark:text-slate-400 mb-2"><i class="fas fa-table mr-1"></i>下载模板，按格式填写后上传</p>
                <div class="flex flex-wrap gap-2">${links}</div>
            </div>
        `;
    }

    const buildZone = (field, isRequired, allowMultiple) => {
        const files = _skillFiles[field];
        const hasFile = allowMultiple ? (files && files.length > 0) : !!files;
        const fileNames = allowMultiple
            ? (files || []).map(f => f.name).join(', ')
            : (files ? files.name : '');
        return `
            <div class="wb-skill-file-zone relative border-2 border-dashed ${hasFile ? 'border-emerald-400 bg-emerald-50 dark:bg-emerald-500/5' : 'border-slate-300 dark:border-slate-600'} rounded-xl p-4 text-center hover:border-primary-400 dark:hover:border-primary-500 transition-colors cursor-pointer" data-field="${escapeHtml(field)}" data-multiple="${allowMultiple ? '1' : '0'}">
                <div class="w-10 h-10 rounded-xl bg-slate-50 dark:bg-slate-800 flex items-center justify-center mx-auto mb-2">
                    <i class="fas fa-cloud-arrow-up text-slate-400 text-lg"></i>
                </div>
                <p class="text-sm text-slate-700 dark:text-slate-200 mb-1">
                    ${escapeHtml(field)}
                    ${isRequired ? '<span class="text-red-500">*</span>' : ''}
                    ${allowMultiple ? '<span class="text-xs text-slate-400 ml-1">(可传多个)</span>' : ''}
                </p>
                <p class="text-xs text-slate-500 dark:text-slate-400 wb-skill-file-label truncate px-2">${hasFile ? escapeHtml(fileNames) : '点击上传或拖拽文件到此处'}</p>
                <input type="file" class="wb-skill-file-input hidden" accept="${escapeHtml(acceptTypes)}" ${allowMultiple ? 'multiple' : ''}>
                ${hasFile ? `<button class="wb-skill-clear-file absolute top-2 right-2 text-slate-400 hover:text-red-500 transition-colors" title="清除"><i class="fas fa-trash-can text-xs"></i></button>` : ''}
            </div>
        `;
    };

    const importDiv = document.createElement('div');
    importDiv.id = 'wb-skill-import';
    importDiv.className = 'mt-4 space-y-4';
    importDiv.innerHTML = `
        <div class="text-xs text-slate-500 dark:text-slate-400">
            <i class="fas fa-circle-info mr-1"></i>
            请按字段上传对应文件，系统将把原文件交给 AI 技能脚本处理
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            ${requiredFields.map(f => buildZone(f, true, multiFileFields.includes(f))).join('')}
            ${optionalFields.map(f => buildZone(f, false, multiFileFields.includes(f))).join('')}
        </div>
        ${templatesHtml}
        <div class="flex items-center justify-end gap-2 pt-2">
            <button onclick="clearSkillFiles()" class="px-4 py-2 rounded-lg text-sm text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/10 transition-colors">清除</button>
            <button onclick="submitSkillFiles()" class="px-5 py-2 rounded-lg text-sm bg-primary-500 hover:bg-primary-600 text-white font-medium transition-colors flex items-center gap-2">
                <i class="fas fa-paper-plane"></i>生成方案
            </button>
        </div>
    `;

    stepUpload.appendChild(importDiv);
    setupSkillFileUpload(sub);
}

// 绑定技能多文件上传事件
function setupSkillFileUpload(sub) {
    document.querySelectorAll('.wb-skill-file-zone').forEach(zone => {
        const field = zone.dataset.field;
        const multiple = zone.dataset.multiple === '1';
        const fileInput = zone.querySelector('.wb-skill-file-input');

        zone.addEventListener('click', (e) => {
            if (e.target.closest('.wb-skill-clear-file')) return;
            fileInput.click();
        });

        zone.addEventListener('dragover', (e) => {
            e.preventDefault();
            zone.classList.add('border-primary-400', 'dark:border-primary-500', 'bg-primary-50', 'dark:bg-primary-500/5');
        });
        zone.addEventListener('dragleave', () => {
            zone.classList.remove('border-primary-400', 'dark:border-primary-500', 'bg-primary-50', 'dark:bg-primary-500/5');
        });
        zone.addEventListener('drop', (e) => {
            e.preventDefault();
            zone.classList.remove('border-primary-400', 'dark:border-primary-500', 'bg-primary-50', 'dark:bg-primary-500/5');
            const files = Array.from(e.dataTransfer.files);
            if (files.length > 0) handleSkillFiles(field, multiple ? files : [files[0]], sub);
        });

        fileInput.addEventListener('change', (e) => {
            const files = Array.from(e.target.files);
            if (files.length > 0) handleSkillFiles(field, multiple ? files : [files[0]], sub);
        });

        const clearBtn = zone.querySelector('.wb-skill-clear-file');
        if (clearBtn) {
            clearBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                delete _skillFiles[field];
                renderSkillFileImport(sub);
            });
        }
    });
}

// 处理技能文件选择
function handleSkillFiles(field, files, sub) {
    const multiple = field === '供应商报价文件';
    if (multiple) {
        const existing = _skillFiles[field] || [];
        _skillFiles[field] = [...existing, ...files];
    } else {
        _skillFiles[field] = files[0];
    }
    renderSkillFileImport(sub);
}

// 清除所有技能文件
function clearSkillFiles() {
    _skillFiles = {};
    if (_workbenchSubScene) renderSkillFileImport(_workbenchSubScene);
}

// 提交技能文件
async function submitSkillFiles() {
    if (!_workbenchSubScene) return;
    const sub = _workbenchSubScene;
    const requiredFields = sub.required_fields || [];

    const missing = requiredFields.filter(f => {
        const files = _skillFiles[f];
        if (Array.isArray(files)) return files.length === 0;
        return !files;
    });
    if (missing.length > 0) {
        showToast('以下必填文件未上传: ' + missing.join(', '), 'error');
        return;
    }

    try {
        // Read files and upload as base64
        const filesToUpload = {};
        const fileEntries = [];
        for (const [field, files] of Object.entries(_skillFiles)) {
            const list = Array.isArray(files) ? files : [files];
            for (let i = 0; i < list.length; i++) {
                const file = list[i];
                const key = Array.isArray(files) ? `${field}_${i + 1}` : field;
                const base64 = await fileToBase64(file);
                filesToUpload[key] = {
                    filename: file.name,
                    content: base64,
                    is_base64: true
                };
                fileEntries.push({ field, key, filename: file.name, index: i });
            }
        }

        const uploadResult = await fetch('/api/workbench/upload', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                files: filesToUpload,
                session_id: sessionId
            })
        }).then(r => r.json());

        if (uploadResult.status !== 'success') {
            showToast(uploadResult.message || '文件上传失败', 'error');
            return;
        }

        // Build field -> path mapping
        const fieldPaths = {};
        fileEntries.forEach(({ field, key }) => {
            const path = uploadResult.files[key];
            if (!path) return;
            if (!fieldPaths[field]) fieldPaths[field] = [];
            fieldPaths[field].push(path);
        });

        // Activate scene
        const parentScene = _workbenchScene;
        closeWorkbench();
        newChat();

        const activateResult = await fetch('/api/scenes/activate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                scene_id: sub.id,
                session_id: sessionId,
                scene_context: {
                    id: sub.id,
                    name: sub.name,
                    system_prompt: sub.system_prompt,
                    skill_name: sub.skill_name || parentScene.skill_name,
                    workbench_files: uploadResult.files
                }
            })
        }).then(r => r.json());

        if (activateResult.status !== 'success') {
            showToast(activateResult.message || t('scenes_switch_failed'), 'error');
            return;
        }

        showToast(t('scenes_switched_sub').replace('{name}', sub.name), 'success');

        // Build and send prompt
        const prompt = buildSkillFilePrompt(sub, fieldPaths);
        sendMessage(prompt);
    } catch (err) {
        showToast('提交失败: ' + err.message, 'error');
    }
}

// 文件转 base64
function fileToBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
            const arrayBuffer = reader.result;
            const bytes = new Uint8Array(arrayBuffer);
            let binary = '';
            for (let i = 0; i < bytes.byteLength; i++) {
                binary += String.fromCharCode(bytes[i]);
            }
            resolve(btoa(binary));
        };
        reader.onerror = reject;
        reader.readAsArrayBuffer(file);
    });
}

// 构建技能文件处理提示词
function buildSkillFilePrompt(sub, fieldPaths) {
    const skillName = sub.skill_name;
    const name = sub.name;

    let prompt = `请使用 \`${skillName}\` 技能完成【${name}】。\n\n`;
    prompt += `## 已上传文件\n`;

    const buildPathList = (paths) => {
        if (!paths || paths.length === 0) return '';
        return paths.map(p => `- \`${p}\``).join('\n');
    };

    for (const [field, paths] of Object.entries(fieldPaths)) {
        prompt += `### ${field}\n${buildPathList(paths)}\n\n`;
    }

    if (skillName === 'sales-quotation') {
        prompt += `## 执行要求\n`;
        prompt += `请严格按照 \`skills/sales-quotation/SKILL.md\` 中的“AI 执行步骤”执行：\n`;
        prompt += `1. 使用 \`skills/sales-quotation/scripts/parse_inquiry.py\` 解析客户询价文件。\n`;
        prompt += `2. 使用 \`skills/sales-quotation/scripts/calculate_cost.py\` 核算成本。\n`;
        prompt += `3. 如上传了库存和产能文件，使用 \`skills/sales-quotation/scripts/evaluate_delivery.py\` 评估交期。\n`;
        prompt += `4. 使用 \`skills/sales-quotation/scripts/generate_quotation.py\` 同时生成 Excel 报价单和 HTML 可视化看板。\n`;
        prompt += `5. 向用户汇报关键信息、报价方案、毛利率和文件路径。\n`;
    } else if (skillName === 'procurement-comparison') {
        prompt += `## 执行要求\n`;
        prompt += `请严格按照 \`skills/procurement-comparison/SKILL.md\` 中的“AI 执行步骤”执行：\n`;
        prompt += `1. 使用 \`skills/procurement-comparison/scripts/parse_quote.py\` 分别解析每家供应商的报价文件。\n`;
        prompt += `2. 使用 \`skills/procurement-comparison/scripts/compare_quotes.py\` 执行比价分析。\n`;
        prompt += `3. 使用 \`skills/procurement-comparison/scripts/generate_report.py\` 同时生成 Excel 比价报告和 HTML 可视化看板。\n`;
        prompt += `4. 向用户汇报比价结果、推荐供应商、风险提示和文件路径。\n`;
    }

    prompt += `\n注意：所有文件已保存在服务器，请直接使用上述文件路径调用脚本，禁止从零编写 Python 脚本。`;
    return prompt;
}

// 渲染采购场景文件导入UI
function renderProcurementFileImport(sub) {
    const existingImport = document.getElementById('wb-procurement-import');
    if (existingImport) existingImport.remove();

    const stepUpload = document.getElementById('wb-step-upload');
    if (!stepUpload) return;

    const importDiv = document.createElement('div');
    importDiv.id = 'wb-procurement-import';
    importDiv.className = 'mt-4';

    const importConfig = sub.import_config || {};
    const acceptTypes = importConfig.accept || '.xlsx,.xls,.csv';
    const desc = importConfig.description || '上传Excel/CSV文件导入数据';

    // Build template download links if configured
    const templates = importConfig.templates || [];
    let templatesHtml = '';
    if (templates.length > 0) {
        const links = templates.map(t => {
            const url = t.demo_url || t.url || '';
            const encodedUrl = url ? encodeURIComponent(url) : '';
            return `<a href="/api/file?path=${encodedUrl}" download="${escapeHtml(t.name)}" class="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-xs text-slate-600 dark:text-slate-300 transition-colors">
                <i class="fas fa-download text-[10px]"></i>${escapeHtml(t.name)}
            </a>`;
        }).join('');
        templatesHtml = `
            <div class="mt-3">
                <p class="text-xs text-slate-500 dark:text-slate-400 mb-2"><i class="fas fa-table mr-1"></i>下载模板，按格式填写后上传</p>
                <div class="flex flex-wrap gap-2">${links}</div>
            </div>
        `;
    }

    importDiv.innerHTML = `
        <div class="border-2 border-dashed border-slate-300 dark:border-slate-600 rounded-xl p-8 text-center hover:border-primary-400 dark:hover:border-primary-500 transition-colors cursor-pointer" id="wb-proc-drop-zone">
            <div class="w-14 h-14 rounded-2xl bg-slate-50 dark:bg-slate-800 flex items-center justify-center mx-auto mb-3">
                <i class="fas fa-cloud-arrow-up text-slate-400 text-xl"></i>
            </div>
            <p class="text-sm text-slate-600 dark:text-slate-300 mb-1">${escapeHtml(desc)}</p>
            <p class="text-xs text-slate-400 dark:text-slate-500">拖拽文件到此处 或 <span class="text-primary-500">点击选择文件</span></p>
            <input type="file" id="wb-proc-file-input" accept="${escapeHtml(acceptTypes)}" class="hidden">
        </div>
        ${templatesHtml}
        <div id="wb-proc-file-info" class="hidden mt-3 flex items-center gap-3 p-3 bg-slate-50 dark:bg-slate-800/50 rounded-lg">
            <i class="fas fa-file-excel text-emerald-500"></i>
            <div class="flex-1 min-w-0">
                <p id="wb-proc-file-name" class="text-sm text-slate-700 dark:text-slate-200 truncate"></p>
                <p id="wb-proc-file-size" class="text-xs text-slate-400"></p>
            </div>
            <button onclick="clearProcurementFile()" class="text-slate-400 hover:text-red-500 transition-colors">
                <i class="fas fa-trash-can"></i>
            </button>
        </div>
        <div id="wb-proc-mapping" class="hidden mt-4">
            <h5 class="text-xs font-semibold text-slate-700 dark:text-slate-200 mb-2">字段映射</h5>
            <p class="text-xs text-slate-500 dark:text-slate-400 mb-2">请将文件列名与标准字段进行匹配</p>
            <div id="wb-proc-mapping-list" class="space-y-2 max-h-48 overflow-y-auto"></div>
        </div>
        <div id="wb-proc-preview" class="hidden mt-4">
            <h5 class="text-xs font-semibold text-slate-700 dark:text-slate-200 mb-2">数据预览</h5>
            <div class="overflow-x-auto rounded-lg border border-slate-200 dark:border-white/10 max-h-48">
                <table class="w-full text-xs">
                    <thead class="bg-slate-50 dark:bg-slate-800 text-slate-600 dark:text-slate-300 sticky top-0">
                        <tr id="wb-proc-preview-head"></tr>
                    </thead>
                    <tbody id="wb-proc-preview-body" class="text-slate-700 dark:text-slate-200"></tbody>
                </table>
            </div>
        </div>
        <div id="wb-proc-actions" class="hidden mt-4 flex items-center justify-end gap-2">
            <button onclick="clearProcurementFile()" class="px-4 py-2 rounded-lg text-sm text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/10 transition-colors">清除</button>
            <button onclick="submitProcurementFile()" class="px-5 py-2 rounded-lg text-sm bg-primary-500 hover:bg-primary-600 text-white font-medium transition-colors flex items-center gap-2">
                <i class="fas fa-paper-plane"></i>生成方案
            </button>
        </div>
    `;

    stepUpload.appendChild(importDiv);

    // Setup file upload handlers
    const dropZone = document.getElementById('wb-proc-drop-zone');
    const fileInput = document.getElementById('wb-proc-file-input');

    if (dropZone && fileInput) {
        dropZone.addEventListener('click', () => fileInput.click());
        dropZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropZone.classList.add('border-primary-400', 'dark:border-primary-500');
        });
        dropZone.addEventListener('dragleave', () => {
            dropZone.classList.remove('border-primary-400', 'dark:border-primary-500');
        });
        dropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropZone.classList.remove('border-primary-400', 'dark:border-primary-500');
            const files = e.dataTransfer.files;
            if (files.length > 0) handleProcurementFile(files[0], sub);
        });
        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) handleProcurementFile(e.target.files[0], sub);
        });
    }
}

// 处理采购文件上传
function handleProcurementFile(file, sub) {
    const fileInfo = document.getElementById('wb-proc-file-info');
    const fileName = document.getElementById('wb-proc-file-name');
    const fileSize = document.getElementById('wb-proc-file-size');

    fileName.textContent = file.name;
    fileSize.textContent = formatFileSize(file.size);
    fileInfo.classList.remove('hidden');

    const reader = new FileReader();
    reader.onload = (e) => {
        try {
            let data = [];
            if (file.name.endsWith('.csv')) {
                data = parseCSV(e.target.result);
            } else {
                // For Excel, we need to send to server for parsing
                uploadProcurementFileForParsing(file, sub);
                return;
            }
            processProcurementData(data, sub);
        } catch (err) {
            showToast('文件解析失败: ' + err.message, 'error');
        }
    };
    reader.readAsText(file);
}

// 上传文件到服务器解析（Excel）
function uploadProcurementFileForParsing(file, sub) {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('scene_id', sub.id);

    fetch('/api/procurement/import', {
        method: 'POST',
        body: formData
    })
    .then(r => r.json())
    .then(result => {
        if (result.status === 'success') {
            processProcurementData(result.data, sub);
        } else {
            showToast(result.message || '文件解析失败', 'error');
        }
    })
    .catch(err => {
        showToast('上传失败: ' + err.message, 'error');
    });
}

// 处理采购数据（字段映射+预览）
function processProcurementData(data, sub) {
    if (!data || data.length === 0) {
        showToast('文件中没有数据', 'error');
        return;
    }

    _procurementFileData = data;
    _procurementFileHeaders = Object.keys(data[0]);

    // Auto mapping
    const templateHeaders = (sub.import_config && sub.import_config.template_headers) || [];
    const allFields = [...(sub.required_fields || []), ...(sub.optional_fields || [])];
    _procurementFileMapping = {};

    allFields.forEach(field => {
        const matched = _procurementFileHeaders.find(h =>
            h === field || h.includes(field) || field.includes(h)
        );
        if (matched) _procurementFileMapping[field] = matched;
    });

    // Render mapping UI
    const mappingList = document.getElementById('wb-proc-mapping-list');
    const mappingDiv = document.getElementById('wb-proc-mapping');
    if (mappingList && mappingDiv) {
        mappingList.innerHTML = allFields.map(field => {
            const mapped = _procurementFileMapping[field] || '';
            const isRequired = (sub.required_fields || []).includes(field);
            const requiredMark = isRequired ? '<span class="text-red-500">*</span>' : '';
            return `
                <div class="flex items-center gap-2">
                    <span class="text-xs text-slate-600 dark:text-slate-300 w-24 truncate">${escapeHtml(field)} ${requiredMark}</span>
                    <i class="fas fa-arrow-right text-slate-400 text-[10px]"></i>
                    <select onchange="updateProcurementMapping('${escapeHtml(field)}', this.value)" class="flex-1 px-2 py-1 rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs text-slate-700 dark:text-slate-200">
                        <option value="">-- 不映射 --</option>
                        ${_procurementFileHeaders.map(h => `<option value="${escapeHtml(h)}" ${h === mapped ? 'selected' : ''}>${escapeHtml(h)}</option>`).join('')}
                    </select>
                </div>
            `;
        }).join('');
        mappingDiv.classList.remove('hidden');
    }

    // Render preview
    renderProcurementPreview(data, allFields);

    // Show actions
    const actions = document.getElementById('wb-proc-actions');
    if (actions) actions.classList.remove('hidden');
}

// 更新字段映射
function updateProcurementMapping(field, header) {
    if (header) {
        _procurementFileMapping[field] = header;
    } else {
        delete _procurementFileMapping[field];
    }
}

// 渲染数据预览
function renderProcurementPreview(data, fields) {
    const previewDiv = document.getElementById('wb-proc-preview');
    const previewHead = document.getElementById('wb-proc-preview-head');
    const previewBody = document.getElementById('wb-proc-preview-body');
    if (!previewDiv || !previewHead || !previewBody) return;

    const mappedFields = fields.filter(f => _procurementFileMapping[f]);
    previewHead.innerHTML = mappedFields.map(f => `<th class="px-3 py-2 text-left font-medium">${escapeHtml(f)}</th>`).join('');

    const previewRows = data.slice(0, 5);
    previewBody.innerHTML = previewRows.map(row => {
        return `<tr class="border-t border-slate-100 dark:border-white/5">` +
            mappedFields.map(f => {
                const header = _procurementFileMapping[f];
                const val = row[header] !== undefined ? row[header] : '';
                return `<td class="px-3 py-2">${escapeHtml(String(val))}</td>`;
            }).join('') +
            `</tr>`;
    }).join('');

    previewDiv.classList.remove('hidden');
}

// 清除采购文件
function clearProcurementFile() {
    _procurementFileData = null;
    _procurementFileHeaders = [];
    _procurementFileMapping = {};

    const fileInfo = document.getElementById('wb-proc-file-info');
    const fileInput = document.getElementById('wb-proc-file-input');
    const mapping = document.getElementById('wb-proc-mapping');
    const preview = document.getElementById('wb-proc-preview');
    const actions = document.getElementById('wb-proc-actions');

    if (fileInfo) fileInfo.classList.add('hidden');
    if (fileInput) fileInput.value = '';
    if (mapping) mapping.classList.add('hidden');
    if (preview) preview.classList.add('hidden');
    if (actions) actions.classList.add('hidden');
}

// 提交采购文件数据
function submitProcurementFile() {
    if (!_workbenchSubScene || !_procurementFileData) return;

    const sub = _workbenchSubScene;
    const requiredFields = sub.required_fields || [];
    const missing = requiredFields.filter(f => !_procurementFileMapping[f]);
    if (missing.length > 0) {
        showToast('以下必填字段未映射: ' + missing.join(', '), 'error');
        return;
    }

    // Build data summary
    const rows = _procurementFileData.map(row => {
        const obj = {};
        Object.entries(_procurementFileMapping).forEach(([field, header]) => {
            obj[field] = row[header] !== undefined ? row[header] : '';
        });
        return obj;
    });

    const prompt = buildProcurementPrompt(sub, null, rows);
    closeWorkbench();
    sendMessage(prompt);
}

// 根据子场景配置和实际同步数据，确定用于预览/CSV的字段列表。
// 如果 SAP 返回的字段与子场景 required_fields/optional_fields 无法对应，
// 则直接使用返回数据中的字段，避免生成空表。
function _resolveErpDataFields(sub, data) {
    const configured = [...(sub.required_fields || []), ...(sub.optional_fields || [])];
    if (!data || data.length === 0) return configured;
    const rowKeys = Object.keys(data[0]);
    const overlap = configured.filter(f => rowKeys.includes(f));
    // 修复：SAP 同步阶段（如 supplier_risk），configured 里混有 assess_risk 分析字段，
    // 仅当 overlap 覆盖率超过 50% 时才用 configured；否则用 SAP 实际返回字段。
    // 这样 ERP 预览表能正确显示 SAP 返回的 STCD1/SPERR/LOEVM/LAND1/ORT01/ERDAT 等。
    if (configured.length > 0 && overlap.length / configured.length >= 0.5) {
        return configured;
    }
    return rowKeys;
}

// 渲染ERP数据预览
function renderProcurementErpPreview(sub) {
    const existingPreview = document.getElementById('wb-procurement-preview');
    if (existingPreview) existingPreview.remove();

    const stepUpload = document.getElementById('wb-step-upload');
    if (!stepUpload) return;

    const previewDiv = document.createElement('div');
    previewDiv.id = 'wb-procurement-preview';
    previewDiv.className = 'mt-4';

    if (!_procurementErpData || _procurementErpData.length === 0) {
        previewDiv.innerHTML = `
            <div class="p-8 text-center text-slate-500 dark:text-slate-400">
                <i class="fas fa-server text-3xl mb-3 opacity-50"></i>
                <p class="text-sm">请先配置ERP连接并同步数据</p>
            </div>
        `;
    } else {
        const allFields = _resolveErpDataFields(sub, _procurementErpData);
        // 按物料编码排序（若存在该字段），便于核对查看
        const sortField = allFields.find(f => f.includes('物料编码') || f === 'MATNR');
        let displayData = _procurementErpData;
        if (sortField) {
            displayData = [..._procurementErpData].sort((a, b) => {
                const va = String(a[sortField] || '');
                const vb = String(b[sortField] || '');
                return va.localeCompare(vb, 'zh');
            });
        }
        previewDiv.innerHTML = `
            <div class="flex items-center justify-between mb-2">
                <h5 class="text-xs font-semibold text-slate-700 dark:text-slate-200">已同步数据 (${_procurementErpData.length} 条${sortField ? '，已按物料编码排序' : ''})</h5>
                <button onclick="clearProcurementErpData()" class="text-xs text-slate-400 hover:text-red-500 transition-colors">
                    <i class="fas fa-trash-can"></i> 清除
                </button>
            </div>
            <div class="overflow-auto rounded-lg border border-slate-200 dark:border-white/10 max-h-96">
                <table class="w-full text-xs">
                    <thead class="bg-slate-50 dark:bg-slate-800 text-slate-600 dark:text-slate-300 sticky top-0">
                        <tr>${allFields.map(f => `<th class="px-3 py-2 text-left font-medium whitespace-nowrap">${escapeHtml(f)}</th>`).join('')}</tr>
                    </thead>
                    <tbody class="text-slate-700 dark:text-slate-200">
                        ${displayData.map(row => `
                            <tr class="border-t border-slate-100 dark:border-white/5">
                                ${allFields.map(f => `<td class="px-3 py-2 whitespace-nowrap">${escapeHtml(String(row[f] !== undefined ? row[f] : ''))}</td>`).join('')}
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
            <div class="mt-4 flex items-center justify-end">
                <button onclick="submitProcurementErp()" class="px-5 py-2 rounded-lg text-sm bg-primary-500 hover:bg-primary-600 text-white font-medium transition-colors flex items-center gap-2">
                    <i class="fas fa-paper-plane"></i>生成方案
                </button>
            </div>
        `;
    }

    stepUpload.appendChild(previewDiv);
}

// 清除ERP数据
function clearProcurementErpData() {
    _procurementErpData = null;
    _procurementErpRawData = null;
    renderProcurementErpPreview(_workbenchSubScene);
}

// 提交ERP数据
function submitProcurementErp() {
    if (!_workbenchSubScene || !_procurementErpData || _procurementErpData.length === 0) {
        showToast('没有可提交的ERP数据', 'error');
        return;
    }

    const sub = _workbenchSubScene;
    const totalRows = _procurementErpData.length;
    const summary = `${sub.name} - 共 ${totalRows} 条记录`;

    showToast('正在上传数据文件...', 'info');

    let filesPayload = {};
    let prompt = '';

    if (sub.id === 'supplier_quote_comparison') {
        // 采购比价分析：上传原始 SAP 字段 JSON，供技能脚本直接处理
        // 物料编码上传前去掉前导零，与 Excel/HTML 显示一致，避免 agent 自行处理出错
        const rawData = (_procurementErpRawData || _procurementErpData).map(r => {
            const copy = { ...r };
            const matnr = copy['MATNR'] || copy['物料编码'];
            if (matnr) {
                const stripped = String(matnr).replace(/^0+/, '') || '0';
                if (copy['MATNR']) copy['MATNR'] = stripped;
                if (copy['物料编码']) copy['物料编码'] = stripped;
            }
            return copy;
        });
        const jsonContent = JSON.stringify({
            scene_id: sub.id,
            query_time: new Date().toISOString(),
            total_rows: rawData.length,
            records: rawData
        }, null, 2);
        filesPayload = {
            data: {
                filename: `${sub.id}_ekpo_data.json`,
                content: jsonContent
            }
        };
        prompt = `请对以下 SAP EKPO 采购订单行项目历史记录进行${sub.name}。

## 数据文件位置
{{FILE_PATH}}

## 数据概要
${summary}

## 数据格式说明
上传的 JSON 文件为扁平 records 数组结构，字段为 SAP 原始大写字段名（MATNR 物料编码、TXZ01 物料短文本、NETPR 净价、MENGE 采购数量、NETWR 净价金额、EBELN 采购订单号、EBELP 行项目、AEDAT 创建日期）。

## 已生成的报表与摘要文件
Excel 报表已生成：{{EXCEL_PATH}}
HTML 看板已生成：{{HTML_PATH}}
分析摘要已生成：{{SUMMARY_PATH}}

**重要**：上述报表文件已由后端技能脚本自动生成，已内置按物料编码排序、每物料按日期升序排序、极值颜色标注（最高价红色、最低价绿色）。你**不需要也不允许**自己用 openpyxl/pandas/代码生成 Excel 或 HTML 文件，也不需要执行 python 命令读取数据（Windows 下 python -c 打印中文会无输出）。请直接用 read 工具读取分析摘要文件 {{SUMMARY_PATH}}，其中已包含每个物料的最高价、最低价、平均价、价格列表等统计信息。

## 分析要求
${sub.system_prompt}

## 汇报要求
1. 用 read 工具读取分析摘要文件 {{SUMMARY_PATH}}，分析每个物料的最高价、最低价、平均价、价格波动情况
2. 在回复中提供已生成的文件路径，**必须原样输出完整绝对路径（不要只写文件名，不要改写路径）**：
   - Excel 报表：{{EXCEL_PATH}}
   - HTML 看板：{{HTML_PATH}}
   用户需要凭此完整路径下载或预览文件
3. 数据文件中物料编码已去掉前导零，直接使用数据中的值即可，不要自行增减字符
4. **汇报表格规范**：如用表格展示每个物料的采购价格，必须按以下规则：
   - 行：物料按编码升序排列
   - 列：日期时间轴从左到右按升序排列
   - 单元格：价格与日期一一对应，按摘要文件中"日期列表"顺序展示"价格列表"`;
    } else {
        // 其他采购场景：保持原有 CSV 方式
        const allFields = _resolveErpDataFields(sub, _procurementErpData);
        const headers = allFields;
        const rows = _procurementErpData.map(row => {
            return allFields.map(f => {
                const val = row[f] !== undefined ? row[f] : '';
                return String(val).includes(',') || String(val).includes('"')
                    ? '"' + String(val).replace(/"/g, '""') + '"'
                    : String(val);
            }).join(',');
        });
        const csvContent = [headers.join(','), ...rows].join('\n');
        filesPayload = {
            data: {
                filename: `${sub.id}_erp_data.csv`,
                content: csvContent
            }
        };
        const sk = (sub.skill_name ? `，同时调用 ${sub.skill_name} 技能处理数据` : '');
        prompt = `请对以下数据进行${sub.name}分析。${sk}

## 数据文件位置
{{FILE_PATH}}

## 数据概要
${summary}

## 分析要求
${sub.system_prompt}

请读取数据文件，生成专业的分析报告。`;
    }

    // 通过 /api/workbench/upload 保存文件到服务器
    fetch('/api/workbench/upload', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            files: filesPayload,
            summary: summary,
            session_id: sessionId
        })
    })
    .then(r => r.json())
    .then(uploadResult => {
        if (uploadResult.status !== 'success') {
            showToast('文件上传失败: ' + (uploadResult.message || '未知错误'), 'error');
            return;
        }

        const filePath = uploadResult.files?.data || '';

        // 采购比价分析场景：后端直接生成报表，避免 agent 自己生成导致格式/排序不正确
        if (sub.id === 'supplier_quote_comparison' && filePath) {
            fetch('/api/workbench/generate-report', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ data_file_path: filePath, scene_id: sub.id })
            })
            .then(r => r.json())
            .then(reportResult => {
                if (reportResult.status !== 'success') {
                    showToast('报表生成失败: ' + (reportResult.message || '未知错误'), 'error');
                    return;
                }
                // Windows 路径反斜杠转正斜杠，避免命令行/Markdown 转义问题
                const excelPath = (reportResult.excel_path || '').replace(/\\/g, '/');
                const htmlPath = (reportResult.html_path || '').replace(/\\/g, '/');
                const summaryPath = (reportResult.summary_path || '').replace(/\\/g, '/');
                // HTML 看板生成失败（如服务器缺少 plotly 依赖）时，明确提示 agent：
                // tmp 目录中已有的 procurement_report.html 是历史遗留旧文件，禁止拿它顶替本次结果
                const htmlPromptValue = htmlPath
                    ? htmlPath
                    : '【本次 HTML 看板生成失败，请忽略】tmp 目录中已有的 procurement_report.html 是历史遗留的旧文件（非本次数据生成），禁止将其作为本次结果展示或汇报';
                if (!htmlPath) {
                    showToast('HTML 看板生成失败，请检查服务器 Python 是否已安装 plotly', 'error');
                }
                let finalPrompt = prompt.replace(/\{\{FILE_PATH\}\}/g, filePath);
                finalPrompt = finalPrompt.replace(/\{\{EXCEL_PATH\}\}/g, excelPath);
                finalPrompt = finalPrompt.replace(/\{\{HTML_PATH\}\}/g, htmlPromptValue);
                finalPrompt = finalPrompt.replace(/\{\{SUMMARY_PATH\}\}/g, summaryPath);

                closeWorkbench();
                newChat();
                sendMessage(finalPrompt);
            })
            .catch(err => {
                showToast('报表生成失败: ' + (err.message || err), 'error');
            });
        } else {
            // 其他场景：原有逻辑，agent 自行处理
            const projectRoot = (uploadResult.project_root || '').replace(/\\/g, '/');
            let finalPrompt = prompt.replace(/\{\{FILE_PATH\}\}/g, filePath);
            finalPrompt = finalPrompt.replace(/\{\{PROJECT_ROOT\}\}/g, projectRoot);

            closeWorkbench();
            newChat();
            sendMessage(finalPrompt);
        }
    })
    .catch(err => {
        showToast('上传失败: ' + (err.message || err), 'error');
    });
}

// 提交采购表单
function submitProcurementForm() {
    const form = document.getElementById('wb-procurement-form');
    if (!form || !_workbenchSubScene) return;

    const inputs = form.querySelectorAll('input[type="text"]');
    const formData = {};
    let hasEmptyRequired = false;

    inputs.forEach(input => {
        const name = input.name;
        const value = input.value.trim();
        formData[name] = value;

        // 检查必填字段
        const isRequired = _workbenchSubScene.required_fields && _workbenchSubScene.required_fields.includes(name);
        if (isRequired && !value) {
            hasEmptyRequired = true;
            input.classList.add('border-red-500');
        } else {
            input.classList.remove('border-red-500');
        }
    });

    if (hasEmptyRequired) {
        alert('请填写所有必填字段');
        return;
    }

    // 构建提示词并发送
    const prompt = buildProcurementPrompt(_workbenchSubScene, formData);
    closeWorkbench();
    sendMessage(prompt);
}

// 构建采购场景提示词（支持单条formData或多条rows）
function buildProcurementPrompt(subScene, formData, rows) {
    let prompt = `请作为${subScene.name}专家，根据以下信息提供专业分析和方案：\n\n`;
    prompt += `【功能】${subScene.name}\n`;
    prompt += `【说明】${subScene.description}\n\n`;

    if (rows && rows.length > 0) {
        prompt += `【数据】共 ${rows.length} 条记录\n`;
        rows.forEach((row, idx) => {
            prompt += `--- 记录 ${idx + 1} ---\n`;
            Object.entries(row).forEach(([key, value]) => {
                if (value !== '' && value !== undefined && value !== null) {
                    prompt += `${key}：${value}\n`;
                }
            });
        });
    } else if (formData) {
        prompt += `【录入信息】\n`;
        Object.entries(formData).forEach(([key, value]) => {
            if (value) {
                prompt += `${key}：${value}\n`;
            }
        });
    }

    prompt += `\n请基于以上信息，提供详细的分析和专业建议。`;
    return prompt;
}

function updateRequiredStatementsHint(sub) {
    const hintEl = document.getElementById('wb-required-hint');
    const hintList = document.getElementById('wb-hint-list');

    if (!hintEl || !hintList) return;

    // 如果是表单录入场景（采购、销售、生产、人事），显示字段提示而非报表提示
    const formCategories = ['procurement', 'sales', 'production', 'hr'];
    if (_workbenchScene && formCategories.includes(_workbenchScene.category)) {
        const requiredFields = sub.required_fields || [];
        if (requiredFields.length === 0) {
            hintEl.classList.add('hidden');
            return;
        }
        hintEl.classList.remove('hidden');
        hintList.innerHTML = requiredFields.map(f => `
            <div class="flex items-center gap-1.5 px-2 py-1 rounded-md bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-500/20">
                <i class="fas fa-tag text-slate-500 text-[10px]"></i>
                <span class="text-xs text-slate-700 dark:text-slate-200">${escapeHtml(f)}</span>
            </div>
        `).join('');
        const hintTitle = document.getElementById('wb-hint-title');
        hintTitle.textContent = `本功能需要填写以下信息（${requiredFields.length}项）`;
        return;
    }

    // Determine required statements based on sub-scene fields
    const requiredFields = sub.required_fields || [];
    const requiredStatements = [];

    // Check which statements are needed based on field patterns
    const hasBalanceSheetFields = requiredFields.some(f =>
        ['货币资金', '应收账款', '存货', '固定资产', '资产总计', '负债合计', '所有者权益', '流动资产', '流动负债', '总资产', '净资产'].some(k => f.includes(k))
    );
    const hasIncomeStatementFields = requiredFields.some(f =>
        ['营业收入', '营业成本', '净利润', '营业利润', '利润总额', '营业费用', '管理费用', '财务费用', '所得税'].some(k => f.includes(k))
    );
    const hasCashFlowFields = requiredFields.some(f =>
        ['经营活动', '投资活动', '筹资活动', '现金流'].some(k => f.includes(k))
    );

    if (hasBalanceSheetFields) {
        requiredStatements.push({ name: '资产负债表', icon: 'fa-scale-balanced', color: 'blue', desc: '反映企业某一时点的财务状况' });
    }
    if (hasIncomeStatementFields) {
        requiredStatements.push({ name: '利润表', icon: 'fa-chart-line', color: 'emerald', desc: '反映企业一定期间的经营成果' });
    }
    if (hasCashFlowFields) {
        requiredStatements.push({ name: '现金流量表', icon: 'fa-money-bill-transfer', color: 'amber', desc: '反映企业现金的流入和流出' });
    }

    if (requiredStatements.length === 0) {
        hintEl.classList.add('hidden');
        return;
    }

    hintEl.classList.remove('hidden');
    hintList.innerHTML = requiredStatements.map(s => `
        <div class="flex items-center gap-1.5 px-2 py-1 rounded-md bg-white dark:bg-slate-800 border border-${s.color}-200 dark:border-${s.color}-500/20">
            <i class="fas ${s.icon} text-${s.color}-500 text-[10px]"></i>
            <span class="text-xs text-slate-700 dark:text-slate-200">${s.name}</span>
        </div>
    `).join('');

    // Update hint title
    const hintTitle = document.getElementById('wb-hint-title');
    if (requiredStatements.length === 1) {
        hintTitle.textContent = `本分析主要需要：${requiredStatements[0].name}`;
    } else {
        hintTitle.textContent = `本分析需要以下${requiredStatements.length}张报表`;
    }
}

function setupWorkbenchFileUpload() {
    const dropZone = document.getElementById('wb-drop-zone');
    const fileInput = document.getElementById('wb-file-input');

    if (!dropZone || !fileInput) return;

    dropZone.onclick = () => fileInput.click();

    dropZone.ondragover = (e) => {
        e.preventDefault();
        dropZone.classList.add('border-primary-400', 'dark:border-primary-500', 'bg-primary-50', 'dark:bg-primary-500/5');
    };
    dropZone.ondragleave = () => {
        dropZone.classList.remove('border-primary-400', 'dark:border-primary-500', 'bg-primary-50', 'dark:bg-primary-500/5');
    };
    dropZone.ondrop = (e) => {
        e.preventDefault();
        dropZone.classList.remove('border-primary-400', 'dark:border-primary-500', 'bg-primary-50', 'dark:bg-primary-500/5');
        const files = e.dataTransfer.files;
        if (files.length > 0) handleWorkbenchFile(files[0]);
    };

    fileInput.onchange = (e) => {
        if (e.target.files.length > 0) handleWorkbenchFile(e.target.files[0]);
    };

    // Setup multi-file upload handlers
    setupMultiFileUpload();
}

// Upload mode: 'single' or 'multi'
let _workbenchUploadMode = 'single';
let _workbenchMultiFiles = {}; // { balance_sheet: File, income_statement: File, cashflow_statement: File }
let _workbenchMultiData = {};  // { balance_sheet: {headers, rows}, ... }
let _workbenchMultiMapping = {}; // { balance_sheet: {field: header}, ... }
let _workbenchMultiHeaders = {}; // { balance_sheet: [headers], ... }

function switchUploadMode(mode) {
    _workbenchUploadMode = mode;
    const singleBtn = document.getElementById('wb-mode-single');
    const multiBtn = document.getElementById('wb-mode-multi');
    const singleUpload = document.getElementById('wb-single-upload');
    const multiUpload = document.getElementById('wb-multi-upload');

    if (mode === 'single') {
        singleBtn.classList.add('bg-primary-500', 'text-white');
        singleBtn.classList.remove('bg-slate-100', 'dark:bg-slate-800', 'text-slate-600', 'dark:text-slate-300');
        multiBtn.classList.remove('bg-primary-500', 'text-white');
        multiBtn.classList.add('bg-slate-100', 'dark:bg-slate-800', 'text-slate-600', 'dark:text-slate-300');
        singleUpload.classList.remove('hidden');
        multiUpload.classList.add('hidden');
    } else {
        multiBtn.classList.add('bg-primary-500', 'text-white');
        multiBtn.classList.remove('bg-slate-100', 'dark:bg-slate-800', 'text-slate-600', 'dark:text-slate-300');
        singleBtn.classList.remove('bg-primary-500', 'text-white');
        singleBtn.classList.add('bg-slate-100', 'dark:bg-slate-800', 'text-slate-600', 'dark:text-slate-300');
        multiUpload.classList.remove('hidden');
        singleUpload.classList.add('hidden');
    }

    // Reset steps
    document.getElementById('wb-step-mapping').classList.add('hidden');
    document.getElementById('wb-step-submit').classList.add('hidden');
}

function setupMultiFileUpload() {
    document.querySelectorAll('.wb-statement-upload').forEach(zone => {
        const type = zone.dataset.type;
        const fileInput = zone.querySelector('.wb-multi-file');
        const label = zone.querySelector('.wb-file-label');
        const clearBtn = zone.querySelector('.wb-clear-file');

        zone.onclick = (e) => {
            if (e.target.closest('.wb-clear-file')) return;
            fileInput.click();
        };

        zone.ondragover = (e) => {
            e.preventDefault();
            zone.classList.add('border-primary-400', 'dark:border-primary-500', 'bg-primary-50', 'dark:bg-primary-500/5');
        };
        zone.ondragleave = () => {
            zone.classList.remove('border-primary-400', 'dark:border-primary-500', 'bg-primary-50', 'dark:bg-primary-500/5');
        };
        zone.ondrop = (e) => {
            e.preventDefault();
            zone.classList.remove('border-primary-400', 'dark:border-primary-500', 'bg-primary-50', 'dark:bg-primary-500/5');
            const files = e.dataTransfer.files;
            if (files.length > 0) handleMultiFile(type, files[0], zone, label, clearBtn);
        };

        fileInput.onchange = (e) => {
            if (e.target.files.length > 0) handleMultiFile(type, e.target.files[0], zone, label, clearBtn);
        };

        clearBtn.onclick = (e) => {
            e.stopPropagation();
            delete _workbenchMultiFiles[type];
            delete _workbenchMultiData[type];
            delete _workbenchMultiMapping[type];
            delete _workbenchMultiHeaders[type];
            fileInput.value = '';
            label.textContent = '点击上传或拖拽文件到此处';
            clearBtn.classList.add('hidden');
            zone.classList.remove('border-emerald-400', 'bg-emerald-50', 'dark:bg-emerald-500/5');
        };
    });
}

function handleMultiFile(type, file, zone, label, clearBtn) {
    if (!file.name.match(/\.(xlsx|xls|csv)$/i)) {
        showToast('请上传 Excel 或 CSV 文件', 'error');
        return;
    }

    _workbenchMultiFiles[type] = file;
    label.textContent = file.name;
    clearBtn.classList.remove('hidden');
    zone.classList.add('border-emerald-400', 'bg-emerald-50', 'dark:bg-emerald-500/5');

    const reader = new FileReader();
    reader.onload = (e) => {
        try {
            const data = e.target.result;
            let parsed;
            if (file.name.endsWith('.csv')) {
                const parsed = parseCSVData(data);
                processParsedData(type, parsed, zone, label, clearBtn);
            } else {
                // Excel: send to backend for parsing
                parseMultiFileExcel(type, data, file.name, zone, label, clearBtn);
                return; // async, will continue in callback
            }
        } catch (err) {
            showToast('文件解析失败: ' + err.message, 'error');
        }
    };
    if (file.name.endsWith('.csv')) {
        reader.readAsText(file);
    } else {
        reader.readAsArrayBuffer(file);
    }
}

function processParsedData(type, parsed, zone, label, clearBtn) {
    if (!parsed) return;

    // Store headers and data
    _workbenchMultiHeaders[type] = parsed.headers;
    _workbenchMultiData[type] = parsed.rows;

    // Auto-detect statement type from headers
    const detectedType = autoDetectStatementType(parsed.headers);
    if (detectedType && detectedType !== type) {
        showToast(`检测到该文件可能是${getStatementName(detectedType)}，已自动调整`, 'info');
    }

    // Show auto-detect result
    const detectEl = document.getElementById('wb-auto-detect');
    const detectResult = document.getElementById('wb-detect-result');
    detectEl.classList.remove('hidden');
    detectResult.textContent = `已识别：${getStatementName(detectedType || type)}，共 ${parsed.rows.length} 行数据`;

    // Show mapping when at least 1 file uploaded (allow partial upload)
    if (Object.keys(_workbenchMultiFiles).length >= 1) {
        showMultiFileMapping();
    }
}

function parseMultiFileExcel(type, arrayBuffer, filename, zone, label, clearBtn) {
    const base64Content = btoa(String.fromCharCode(...new Uint8Array(arrayBuffer)));

    fetch('/api/workbench/parse-excel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            file_content: base64Content,
            filename: filename || 'data.xlsx'
        })
    })
    .then(r => r.json())
    .then(result => {
        if (result.status === 'success' && result.sheets && result.sheets.length > 0) {
            const sheet = result.sheets[0];
            
            // Handle transformed data from backend
            // Backend may return data in horizontal layout even if original was vertical
            const parsed = {
                headers: sheet.headers.map(h => String(h).trim()),
                rows: sheet.data.map(row => row.map(c => String(c || '').trim())),
                layout: sheet.layout || 'horizontal',
                original_headers: sheet.original_headers ? sheet.original_headers.map(h => String(h).trim()) : []
            };
            
            showToast(`已解析: ${sheet.name} (${sheet.total_rows} 行, ${parsed.layout === 'vertical' ? '纵向布局已转换' : '横向布局'})`, 'success');
            processParsedData(type, parsed, zone, label, clearBtn);
        } else {
            showToast(result.message || 'Excel 解析失败', 'error');
        }
    })
    .catch(err => {
        showToast('Excel 解析请求失败: ' + err.message, 'error');
    });
}

function getStatementName(type) {
    const names = {
        balance_sheet: '资产负债表',
        income_statement: '利润表',
        cashflow_statement: '现金流量表'
    };
    return names[type] || type;
}

function autoDetectStatementType(headers) {
    const headerStr = headers.join(',').toLowerCase();

    // Balance sheet indicators
    const bsIndicators = ['货币资金', '应收账款', '存货', '固定资产', '资产总计', '负债合计', '所有者权益'];
    const bsScore = bsIndicators.filter(ind => headerStr.includes(ind)).length;

    // Income statement indicators
    const isIndicators = ['营业收入', '营业成本', '净利润', '营业利润', '利润总额', '营业费用'];
    const isScore = isIndicators.filter(ind => headerStr.includes(ind)).length;

    // Cash flow indicators
    const cfIndicators = ['经营活动', '投资活动', '筹资活动', '现金流', '现金及现金等价物'];
    const cfScore = cfIndicators.filter(ind => headerStr.includes(ind)).length;

    if (bsScore >= 2 && bsScore >= isScore && bsScore >= cfScore) return 'balance_sheet';
    if (isScore >= 2 && isScore >= bsScore && isScore >= cfScore) return 'income_statement';
    if (cfScore >= 2 && cfScore >= bsScore && cfScore >= isScore) return 'cashflow_statement';
    return null;
}

function parseCSVData(text) {
    const lines = text.trim().split('\n');
    if (lines.length < 2) return null;
    const headers = lines[0].split(',').map(h => h.trim().replace(/^["']|["']$/g, ''));
    const rows = lines.slice(1).map(line => line.split(',').map(c => c.trim().replace(/^["']|["']$/g, '')));
    return { headers, rows };
}

function parseExcelData(arrayBuffer) {
    // Try to read as text first (some Excel files have embedded CSV)
    const text = new TextDecoder('utf-8').decode(arrayBuffer);
    const csvMatch = text.match(/([\w\u4e00-\u9fa5]+(?:,[^\n]*)+)/);
    if (csvMatch) {
        return parseCSVData(csvMatch[1]);
    }
    // Fallback: try to extract readable text lines
    const lines = text.split('\n').filter(l => l.trim() && l.includes(','));
    if (lines.length >= 2) {
        return parseCSVData(lines.join('\n'));
    }
    return null;
}

function showMultiFileMapping() {
    if (!_workbenchSubScene) {
        showToast('请先选择分析维度', 'error');
        return;
    }

    const container = document.getElementById('wb-mapping-list');
    container.innerHTML = '';

    const requiredFields = _workbenchSubScene.required_fields || [];
    const optionalFields = _workbenchSubScene.optional_fields || [];

    // Combine all headers from all files
    const allHeaders = [];
    const headerSources = {};
    Object.entries(_workbenchMultiHeaders).forEach(([type, headers]) => {
        headers.forEach(h => {
            allHeaders.push(h);
            headerSources[h] = type;
        });
    });

    // For vertical layout, field names are in the first column of data
    // Extract them from all files and add to available options
    const dataFieldNames = [];
    const dataFieldSources = {};
    Object.entries(_workbenchMultiData).forEach(([type, rows]) => {
        if (rows && rows.length > 0) {
            rows.forEach(row => {
                if (row && row.length > 0) {
                    const firstCol = String(row[0]).trim();
                    if (firstCol && firstCol !== '项目/科目' && !firstCol.match(/^20\d{2}/)) {
                        dataFieldNames.push(firstCol);
                        dataFieldSources[firstCol] = type;
                    }
                }
            });
        }
    });

    // Combine all available options: headers + data field names
    const allAvailableOptions = [...new Set([...allHeaders, ...dataFieldNames])];
    const allOptionSources = {...headerSources, ...dataFieldSources};

    // Always include time period fields for mapping
    // Check for year/period indicators in all available options
    const timeFields = allAvailableOptions.filter(h =>
        h.includes('年') || h.includes('年度') || h.includes('期间') || h.includes('日期') || h.includes('时间') ||
        h.includes('period') || h.includes('year') || h.includes('month') || /^20\d{2}$/.test(h)
    );

    // Combine all fields to map, with time fields first
    const allFields = [...new Set([...timeFields, ...requiredFields, ...optionalFields])];

    allFields.forEach(field => {
        const isRequired = requiredFields.includes(field);
        const isTimeField = timeFields.includes(field);
        const row = document.createElement('div');
        row.className = 'flex items-center gap-3 p-2 rounded-lg bg-slate-50 dark:bg-slate-800/30';

        // Find best match from all available options using aliases
        let bestMatch = '';
        let sourceType = '';
        
        // Try exact match first
        bestMatch = allAvailableOptions.find(h => h === field) || '';
        if (bestMatch) {
            sourceType = allOptionSources[bestMatch];
        } else {
            // Try alias matching
            const aliases = getFieldAliases(field);
            for (const alias of aliases) {
                bestMatch = allAvailableOptions.find(h => 
                    h === alias || h.includes(alias) || alias.includes(h)
                ) || '';
                if (bestMatch) {
                    sourceType = allOptionSources[bestMatch];
                    break;
                }
            }
        }

        let labelClass = isRequired ? 'text-red-500' : (isTimeField ? 'text-blue-500' : 'text-slate-500 dark:text-slate-400');
        let tag = isRequired ? '<span class="text-[10px] text-red-500">必填</span>' :
                  (isTimeField ? '<span class="text-[10px] text-blue-500">时间</span>' : '<span class="text-[10px] text-slate-400">可选</span>');

        // Build options grouped by file type
        const optionsHtml = Object.entries(_workbenchMultiHeaders).map(([type, headers]) => {
            const typeName = getStatementName(type);
            // Add headers
            let typeOptions = headers.map(h => {
                const selected = h === bestMatch ? 'selected' : '';
                return `<option value="${type}:${escapeHtml(h)}" ${selected}>${escapeHtml(h)}</option>`;
            }).join('');
            // Add data field names for this type
            const typeDataFields = dataFieldNames.filter(f => dataFieldSources[f] === type);
            if (typeDataFields.length > 0) {
                typeOptions += typeDataFields.map(h => {
                    const selected = h === bestMatch ? 'selected' : '';
                    return `<option value="${type}:${escapeHtml(h)}" ${selected}>${escapeHtml(h)} (数据列)</option>`;
                }).join('');
            }
            return `<optgroup label="${typeName}">${typeOptions}</optgroup>`;
        }).join('');

        row.innerHTML = `
            <span class="text-xs font-medium ${labelClass} w-24 truncate">${escapeHtml(field)}</span>
            <span class="text-xs text-slate-400">→</span>
            <select class="wb-mapping-select flex-1 text-xs bg-white dark:bg-[#1A1A1A] border border-slate-200 dark:border-white/10 rounded px-2 py-1" data-field="${escapeHtml(field)}">
                <option value="">-- 选择列 --</option>
                ${optionsHtml}
            </select>
            ${tag}
        `;
        container.appendChild(row);

        // Store initial mapping
        if (bestMatch) {
            _workbenchMapping[field] = `${sourceType}:${bestMatch}`;
        }
    });

    // Update selects on change
    container.querySelectorAll('.wb-mapping-select').forEach(sel => {
        sel.onchange = (e) => {
            _workbenchMapping[e.target.dataset.field] = e.target.value;
            showMultiFilePreview();
        };
    });

    document.getElementById('wb-step-mapping').classList.remove('hidden');
    showMultiFilePreview();
}

// Field aliases for financial statement mapping
function getFieldAliases(field) {
    const aliasMap = {
        '总资产': ['资产总计', '资产合计', '资产总额', '资产'],
        '总负债': ['负债合计', '负债总额', '负债总计', '负债'],
        '净资产': ['所有者权益', '股东权益', '权益合计', '所有者权益合计', '股东权益合计'],
        '流动资产': ['流动资产合计', '流动资产总额'],
        '流动负债': ['流动负债合计', '流动负债总额'],
        '营业收入': ['主营业务收入', '销售收入', '营业额', '收入'],
        '营业成本': ['主营业务成本', '成本', '销售成本'],
        '净利润': ['净收益', '税后利润', '归属于母公司股东的净利润', '归母净利润'],
        '营业利润': ['主营业务利润', '经营利润'],
        '利润总额': ['税前利润', '总利润'],
        '货币资金': ['现金', '库存现金', '银行存款'],
        '应收账款': ['应收票据', '应收款项', '应收'],
        '存货': ['库存商品', '原材料', '在产品'],
        '固定资产': ['固定资产合计', '固定资产净额'],
        '经营活动现金流净额': ['经营活动现金流量净额', '经营活动产生的现金流量净额'],
        '投资活动现金流净额': ['投资活动现金流量净额', '投资活动产生的现金流量净额'],
        '筹资活动现金流净额': ['筹资活动现金流量净额', '筹资活动产生的现金流量净额']
    };
    
    return aliasMap[field] || [];
}

function showMultiFilePreview() {
    const thead = document.getElementById('wb-preview-head');
    const tbody = document.getElementById('wb-preview-body');
    thead.innerHTML = '';
    tbody.innerHTML = '';

    // Show only mapped columns, with time fields first
    const mappedFields = Object.entries(_workbenchMapping).filter(([k, v]) => v);
    if (mappedFields.length === 0) return;

    // Sort: time fields first
    const allHeaders = [];
    Object.values(_workbenchMultiHeaders).forEach(hs => allHeaders.push(...hs));
    const timeFieldNames = allHeaders.filter(h =>
        h.includes('年') || h.includes('年度') || h.includes('期间') || h.includes('日期') || h.includes('时间')
    );
    mappedFields.sort((a, b) => {
        const aIsTime = timeFieldNames.includes(a[0]);
        const bIsTime = timeFieldNames.includes(b[0]);
        if (aIsTime && !bIsTime) return -1;
        if (!aIsTime && bIsTime) return 1;
        return 0;
    });

    // Use the first file's data for preview (they should have same row count)
    const firstType = Object.keys(_workbenchMultiData)[0];
    const firstData = _workbenchMultiData[firstType];
    if (!firstData) return;

    // Show first 20 rows (not all to avoid performance issues)
    const previewLimit = 20;
    const totalRows = firstData.length;

    // Determine if the first file is in vertical layout
    const firstHeaders = _workbenchMultiHeaders[firstType] || [];
    const isVerticalLayout = firstHeaders.length > 0 &&
        (firstHeaders[0] === '项目/科目' || firstHeaders[0].includes('项目'));

    if (isVerticalLayout) {
        // For vertical layout: render with original headers as columns
        // so financial indicators appear in rows (vertical position)
        firstHeaders.forEach((header, i) => {
            const th = document.createElement('th');
            th.className = 'px-3 py-2 text-left font-medium';
            const isTime = timeFieldNames.includes(header);
            const badge = isTime ? '<span class="ml-1 text-[9px] text-blue-400">时间</span>' : '';
            th.innerHTML = `<div class="text-xs">${escapeHtml(header)}${badge}</div>`;
            thead.appendChild(th);
        });

        firstData.slice(0, previewLimit).forEach(row => {
            const tr = document.createElement('tr');
            tr.className = 'border-t border-slate-100 dark:border-white/5';
            row.forEach((cell, i) => {
                const td = document.createElement('td');
                td.className = 'px-3 py-2' + (i === 0 ? ' font-medium text-xs' : ' text-xs');
                td.textContent = cell || '';
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });

        // Add row count hint if data exceeds preview limit
        if (totalRows > previewLimit) {
            const hintRow = document.createElement('tr');
            hintRow.className = 'border-t border-slate-100 dark:border-white/5';
            const hintTd = document.createElement('td');
            hintTd.colSpan = firstHeaders.length;
            hintTd.className = 'px-3 py-2 text-center text-xs text-slate-400 italic';
            hintTd.textContent = `... 还有 ${totalRows - previewLimit} 行数据未显示（共 ${totalRows} 行）`;
            hintRow.appendChild(hintTd);
            tbody.appendChild(hintRow);
        }
    } else {
        // Horizontal layout: mapped fields as columns
        mappedFields.forEach(([field, value]) => {
            const [type, header] = value.split(':');
            const th = document.createElement('th');
            th.className = 'px-3 py-2 text-left font-medium';
            const isTime = timeFieldNames.includes(field);
            const badge = isTime ? '<span class="ml-1 text-[9px] text-blue-400">时间</span>' : '';
            th.innerHTML = `<div class="text-[10px] text-slate-400">${escapeHtml(header)}</div><div class="text-xs">${escapeHtml(field)}${badge}</div>`;
            thead.appendChild(th);
        });

        firstData.slice(0, previewLimit).forEach(row => {
            const tr = document.createElement('tr');
            tr.className = 'border-t border-slate-100 dark:border-white/5';
            mappedFields.forEach(([field, value]) => {
                const [type, header] = value.split(':');
                const headers = _workbenchMultiHeaders[type];
                const idx = headers ? headers.indexOf(header) : -1;
                const td = document.createElement('td');
                td.className = 'px-3 py-2';
                td.textContent = idx >= 0 ? (row[idx] || '') : '';
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });

        // Add row count hint if data exceeds preview limit
        if (totalRows > previewLimit) {
            const hintRow = document.createElement('tr');
            hintRow.className = 'border-t border-slate-100 dark:border-white/5';
            const hintTd = document.createElement('td');
            hintTd.colSpan = mappedFields.length;
            hintTd.className = 'px-3 py-2 text-center text-xs text-slate-400 italic';
            hintTd.textContent = `... 还有 ${totalRows - previewLimit} 行数据未显示（共 ${totalRows} 行）`;
            hintRow.appendChild(hintTd);
            tbody.appendChild(hintRow);
        }
    }

    // Data status
    const requiredFields = _workbenchSubScene.required_fields || [];
    const mappedRequired = requiredFields.filter(f => _workbenchMapping[f]);
    const statusEl = document.getElementById('wb-data-status');
    const completeness = Math.round((mappedRequired.length / requiredFields.length) * 100);

    if (completeness === 100) {
        statusEl.innerHTML = `<span class="text-emerald-500"><i class="fas fa-check-circle"></i> 数据完整度 ${completeness}%（${mappedRequired.length}/${requiredFields.length} 必填字段已映射，共 ${totalRows} 行数据）</span>`;
    } else {
        statusEl.innerHTML = `<span class="text-amber-500"><i class="fas fa-exclamation-circle"></i> 数据完整度 ${completeness}%（${mappedRequired.length}/${requiredFields.length} 必填字段已映射，共 ${totalRows} 行数据）</span>`;
    }
    document.getElementById('wb-step-submit').classList.remove('hidden');
}

function clearWorkbenchFile() {
    _workbenchFileData = null;
    _workbenchHeaders = [];
    _workbenchMapping = {};
    _workbenchMultiFiles = {};
    _workbenchMultiData = {};
    _workbenchMultiMapping = {};
    _workbenchMultiHeaders = {};
    document.getElementById('wb-file-info').classList.add('hidden');
    document.getElementById('wb-step-mapping').classList.add('hidden');
    document.getElementById('wb-step-submit').classList.add('hidden');
    document.getElementById('wb-auto-detect').classList.add('hidden');
    document.getElementById('wb-sheet-selector').classList.add('hidden');
    document.getElementById('wb-file-input').value = '';
    document.querySelectorAll('.wb-multi-file').forEach(input => input.value = '');
    document.querySelectorAll('.wb-file-label').forEach(l => l.textContent = '点击上传或拖拽文件到此处');
    document.querySelectorAll('.wb-clear-file').forEach(b => b.classList.add('hidden'));
    document.querySelectorAll('.wb-statement-upload').forEach(z => {
        z.classList.remove('border-emerald-400', 'bg-emerald-50', 'dark:bg-emerald-500/5');
    });
}

function handleWorkbenchFile(file) {
    if (!file.name.match(/\.(xlsx|xls|csv)$/i)) {
        showToast('请上传 Excel 或 CSV 文件', 'error');
        return;
    }

    document.getElementById('wb-file-name').textContent = file.name;
    document.getElementById('wb-file-size').textContent = (file.size / 1024).toFixed(1) + ' KB';
    document.getElementById('wb-file-info').classList.remove('hidden');

    const reader = new FileReader();
    reader.onload = (e) => {
        try {
            const data = e.target.result;
            if (file.name.endsWith('.csv')) {
                parseWorkbenchCSV(data);
            } else {
                parseWorkbenchExcel(data, file.name);
            }
        } catch (err) {
            showToast('文件解析失败: ' + err.message, 'error');
        }
    };
    if (file.name.endsWith('.csv')) {
        reader.readAsText(file);
    } else {
        reader.readAsArrayBuffer(file);
    }
}

function parseWorkbenchCSV(text) {
    const lines = text.trim().split('\n');
    if (lines.length < 2) {
        showToast('CSV 文件数据行数不足', 'error');
        return;
    }
    _workbenchHeaders = lines[0].split(',').map(h => h.trim().replace(/^["']|["']$/g, ''));
    _workbenchFileData = lines.slice(1).map(line => line.split(',').map(c => c.trim().replace(/^["']|["']$/g, '')));
    showWorkbenchMapping();
}

function parseWorkbenchExcel(arrayBuffer, filename) {
    // Send to backend for parsing with openpyxl
    const base64Content = btoa(String.fromCharCode(...new Uint8Array(arrayBuffer)));

    fetch('/api/workbench/parse-excel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            file_content: base64Content,
            filename: filename || 'data.xlsx'
        })
    })
    .then(r => r.json())
    .then(result => {
        console.log('[Workbench] Parse result:', result);
        if (result.status === 'success' && result.sheets && result.sheets.length > 0) {
            // For single file mode, use the first sheet
            const sheet = result.sheets[0];
            console.log('[Workbench] Sheet data:', sheet);
            
            // Handle transformed data from backend
            // Backend may return data in horizontal layout even if original was vertical
            _workbenchHeaders = sheet.headers.map(h => String(h).trim());
            _workbenchFileData = sheet.data.map(row => row.map(c => String(c || '').trim()));
            
            console.log('[Workbench] Headers:', _workbenchHeaders);
            console.log('[Workbench] Data rows:', _workbenchFileData.length);

            // Show sheet info with layout info
            const layoutInfo = sheet.layout === 'vertical' ? '纵向布局已转换' : '横向布局';
            showToast(`已解析: ${sheet.name} (${sheet.total_rows} 行, ${layoutInfo})`, 'success');
            showWorkbenchMapping();
        } else {
            showToast(result.message || 'Excel 解析失败', 'error');
        }
    })
    .catch(err => {
        showToast('Excel 解析请求失败: ' + err.message, 'error');
    });
}

function showWorkbenchMapping() {
    if (!_workbenchSubScene) {
        showToast('请先选择分析维度', 'error');
        return;
    }

    const container = document.getElementById('wb-mapping-list');
    container.innerHTML = '';

    const requiredFields = _workbenchSubScene.required_fields || [];
    const optionalFields = _workbenchSubScene.optional_fields || [];

    // Always include time period fields for mapping
    // Check for year/period indicators in headers (including numeric years like 2024)
    const timeFields = _workbenchHeaders.filter(h =>
        h.includes('年') || h.includes('年度') || h.includes('期间') || h.includes('日期') || h.includes('时间') ||
        h.includes('period') || h.includes('year') || h.includes('month') || /^20\d{2}$/.test(h)
    );

    // For vertical layout, field names are in the first column of data
    // Extract them and add to available options
    const dataFieldNames = [];
    if (_workbenchFileData && _workbenchFileData.length > 0) {
        _workbenchFileData.forEach(row => {
            if (row && row.length > 0) {
                const firstCol = String(row[0]).trim();
                if (firstCol && firstCol !== '项目/科目' && !firstCol.match(/^20\d{2}/)) {
                    dataFieldNames.push(firstCol);
                }
            }
        });
    }
    
    // Combine all available options: headers + data field names
    const allAvailableOptions = [...new Set([..._workbenchHeaders, ...dataFieldNames])];

    // Combine all fields to map, with time fields first
    const allFields = [...new Set([...timeFields, ...requiredFields, ...optionalFields])];

    allFields.forEach(field => {
        const isRequired = requiredFields.includes(field);
        const isTimeField = timeFields.includes(field);
        const isOptional = optionalFields.includes(field);
        const row = document.createElement('div');
        row.className = 'flex items-center gap-3 p-2 rounded-lg bg-slate-50 dark:bg-slate-800/30';

        // Find best match from all available options using aliases
        let bestMatch = '';
        
        // Try exact match first
        bestMatch = allAvailableOptions.find(h => h === field) || '';
        
        // Try alias matching if no exact match
        if (!bestMatch) {
            const aliases = getFieldAliases(field);
            for (const alias of aliases) {
                bestMatch = allAvailableOptions.find(h => 
                    h === alias || h.includes(alias) || alias.includes(h)
                ) || '';
                if (bestMatch) break;
            }
        }
        
        // Fallback to simple contains match
        if (!bestMatch) {
            bestMatch = allAvailableOptions.find(h => h.includes(field) || field.includes(h)) || '';
        }
        
        _workbenchMapping[field] = bestMatch;

        let labelClass = isRequired ? 'text-red-500' : (isTimeField ? 'text-blue-500' : 'text-slate-500 dark:text-slate-400');
        let tag = isRequired ? '<span class="text-[10px] text-red-500">必填</span>' :
                  (isTimeField ? '<span class="text-[10px] text-blue-500">时间</span>' : '<span class="text-[10px] text-slate-400">可选</span>');

        // Separate options: time fields first, then data fields
        const timeOptions = allAvailableOptions.filter(h => 
            h.includes('年') || h.includes('年度') || h.includes('期间') || h.includes('日期') || h.includes('时间') ||
            h.includes('period') || h.includes('year') || h.includes('month') || /^20\d{2}$/.test(h)
        );
        const dataOptions = allAvailableOptions.filter(h => !timeOptions.includes(h));

        row.innerHTML = `
            <span class="text-xs font-medium ${labelClass} w-24 truncate">${escapeHtml(field)}</span>
            <span class="text-xs text-slate-400">→</span>
            <select class="wb-mapping-select flex-1 text-xs bg-white dark:bg-[#1A1A1A] border border-slate-200 dark:border-white/10 rounded px-2 py-1" data-field="${escapeHtml(field)}">
                <option value="">-- 选择列 --</option>
                ${timeOptions.length > 0 ? `<optgroup label="时间/年份">${timeOptions.map(h => `<option value="${escapeHtml(h)}" ${h === bestMatch ? 'selected' : ''}>${escapeHtml(h)}</option>`).join('')}</optgroup>` : ''}
                ${dataOptions.length > 0 ? `<optgroup label="数据字段">${dataOptions.map(h => `<option value="${escapeHtml(h)}" ${h === bestMatch ? 'selected' : ''}>${escapeHtml(h)}</option>`).join('')}</optgroup>` : ''}
            </select>
            ${tag}
        `;
        container.appendChild(row);
    });

    // Update selects on change
    container.querySelectorAll('.wb-mapping-select').forEach(sel => {
        sel.onchange = (e) => {
            _workbenchMapping[e.target.dataset.field] = e.target.value;
        };
    });

    document.getElementById('wb-step-mapping').classList.remove('hidden');
    showWorkbenchPreview();
}

function showWorkbenchPreview() {
    const thead = document.getElementById('wb-preview-head');
    const tbody = document.getElementById('wb-preview-body');
    thead.innerHTML = '';
    tbody.innerHTML = '';

    // Show only mapped columns, with time fields first
    const mappedFields = Object.entries(_workbenchMapping).filter(([k, v]) => v);
    if (mappedFields.length === 0) return;

    // Sort: time fields first (check both field name and mapped header)
    const timeIndicators = ['年', '年度', '期间', '日期', '时间', 'period', 'year', 'month'];
    const isTimeField = (field, header) => {
        const fieldStr = String(field).toLowerCase();
        const headerStr = String(header).toLowerCase();
        return timeIndicators.some(ind => fieldStr.includes(ind) || headerStr.includes(ind)) ||
               /^20\d{2}$/.test(field) || /^20\d{2}$/.test(header);
    };
    
    mappedFields.sort((a, b) => {
        const aIsTime = isTimeField(a[0], a[1]);
        const bIsTime = isTimeField(b[0], b[1]);
        if (aIsTime && !bIsTime) return -1;
        if (!aIsTime && bIsTime) return 1;
        return 0;
    });

    // Show first 20 rows (not all to avoid performance issues)
    const previewLimit = 20;
    const totalRows = _workbenchFileData.length;

    // Determine if data is in vertical layout
    // Vertical layout: first header is "项目/科目" and data rows start with field names
    const isVerticalLayout = _workbenchHeaders.length > 0 &&
        (_workbenchHeaders[0] === '项目/科目' || _workbenchHeaders[0].includes('项目'));

    if (isVerticalLayout) {
        // For vertical layout: render with original headers as columns
        // so financial indicators appear in rows (vertical position)
        _workbenchHeaders.forEach((header, i) => {
            const th = document.createElement('th');
            th.className = 'px-3 py-2 text-left font-medium';
            const isTime = isTimeField(header, header);
            const badge = isTime ? '<span class="ml-1 text-[9px] text-blue-400">时间</span>' : '';
            th.innerHTML = `<div class="text-xs">${escapeHtml(header)}${badge}</div>`;
            thead.appendChild(th);
        });

        _workbenchFileData.slice(0, previewLimit).forEach(row => {
            const tr = document.createElement('tr');
            tr.className = 'border-t border-slate-100 dark:border-white/5';
            row.forEach((cell, i) => {
                const td = document.createElement('td');
                td.className = 'px-3 py-2' + (i === 0 ? ' font-medium text-xs' : ' text-xs');
                td.textContent = cell || '';
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });

        // Add row count hint if data exceeds preview limit
        if (totalRows > previewLimit) {
            const hintRow = document.createElement('tr');
            hintRow.className = 'border-t border-slate-100 dark:border-white/5';
            const hintTd = document.createElement('td');
            hintTd.colSpan = _workbenchHeaders.length;
            hintTd.className = 'px-3 py-2 text-center text-xs text-slate-400 italic';
            hintTd.textContent = `... 还有 ${totalRows - previewLimit} 行数据未显示（共 ${totalRows} 行）`;
            hintRow.appendChild(hintTd);
            tbody.appendChild(hintRow);
        }
    } else {
        // Horizontal layout: mapped fields as columns
        mappedFields.forEach(([field, header]) => {
            const th = document.createElement('th');
            th.className = 'px-3 py-2 text-left font-medium';
            const isTime = isTimeField(field, header);
            const badge = isTime ? '<span class="ml-1 text-[9px] text-blue-400">时间</span>' : '';
            th.innerHTML = `<div class="text-[10px] text-slate-400">${escapeHtml(header)}</div><div class="text-xs">${escapeHtml(field)}${badge}</div>`;
            thead.appendChild(th);
        });

        _workbenchFileData.slice(0, previewLimit).forEach(row => {
            const tr = document.createElement('tr');
            tr.className = 'border-t border-slate-100 dark:border-white/5';
            mappedFields.forEach(([field, header]) => {
                const idx = _workbenchHeaders.indexOf(header);
                const value = idx >= 0 ? (row[idx] || '') : '';
                const td = document.createElement('td');
                td.className = 'px-3 py-2';
                td.textContent = value;
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });

        // Add row count hint if data exceeds preview limit
        if (totalRows > previewLimit) {
            const hintRow = document.createElement('tr');
            hintRow.className = 'border-t border-slate-100 dark:border-white/5';
            const hintTd = document.createElement('td');
            hintTd.colSpan = mappedFields.length;
            hintTd.className = 'px-3 py-2 text-center text-xs text-slate-400 italic';
            hintTd.textContent = `... 还有 ${totalRows - previewLimit} 行数据未显示（共 ${totalRows} 行）`;
            hintRow.appendChild(hintTd);
            tbody.appendChild(hintRow);
        }
    }

    // Data status
    const requiredFields = _workbenchSubScene.required_fields || [];
    const mappedRequired = requiredFields.filter(f => _workbenchMapping[f]);
    const statusEl = document.getElementById('wb-data-status');
    const completeness = Math.round((mappedRequired.length / requiredFields.length) * 100);

    if (completeness === 100) {
        statusEl.innerHTML = `<span class="text-emerald-500"><i class="fas fa-check-circle"></i> 数据完整度 ${completeness}%（${mappedRequired.length}/${requiredFields.length} 必填字段已映射，共 ${totalRows} 行数据）</span>`;
        document.getElementById('wb-step-submit').classList.remove('hidden');
    } else {
        statusEl.innerHTML = `<span class="text-amber-500"><i class="fas fa-exclamation-circle"></i> 数据完整度 ${completeness}%（${mappedRequired.length}/${requiredFields.length} 必填字段已映射，共 ${totalRows} 行数据）</span>`;
        document.getElementById('wb-step-submit').classList.remove('hidden');
    }
}

function clearWorkbenchFile() {
    _workbenchFileData = null;
    _workbenchHeaders = [];
    _workbenchMapping = {};
    document.getElementById('wb-file-info').classList.add('hidden');
    document.getElementById('wb-step-mapping').classList.add('hidden');
    document.getElementById('wb-step-submit').classList.add('hidden');
    document.getElementById('wb-file-input').value = '';
}

function closeWorkbench() {
    document.getElementById('workbench-modal').classList.add('hidden');
    _workbenchScene = null;
    _workbenchSubScene = null;
    clearWorkbenchFile();
    // Reset procurement state
    _procurementDataSource = 'manual';
    _procurementFileData = null;
    _procurementFileHeaders = [];
    _procurementFileMapping = {};
    _procurementErpData = null;
    _bidDocFile = null;
    _skillFiles = {};
}

// ===== Procurement Data Source Switching =====
function switchDataSource(source) {
    _procurementDataSource = source;

    // Update tab styles
    const manualBtn = document.getElementById('wb-source-manual');
    const fileBtn = document.getElementById('wb-source-file');
    const erpBtn = document.getElementById('wb-source-erp');
    const erpPanel = document.getElementById('wb-erp-panel');

    const activeClass = ['bg-primary-500', 'text-white'];
    const inactiveClass = ['bg-slate-100', 'dark:bg-slate-800', 'text-slate-600', 'dark:text-slate-300', 'hover:bg-slate-200', 'dark:hover:bg-slate-700'];

    [manualBtn, fileBtn, erpBtn].forEach(btn => {
        if (!btn) return;
        btn.classList.remove(...activeClass, ...inactiveClass);
        btn.classList.add(...inactiveClass);
    });

    let activeBtn = null;
    if (source === 'manual') activeBtn = manualBtn;
    else if (source === 'file') activeBtn = fileBtn;
    else if (source === 'erp') activeBtn = erpBtn;

    if (activeBtn) {
        activeBtn.classList.remove(...inactiveClass);
        activeBtn.classList.add(...activeClass);
    }

    // Show/hide ERP panel
    if (erpPanel) {
        if (source === 'erp') {
            erpPanel.classList.remove('hidden');
            loadErpConnectionConfig();
            // 存在默认连接时锁定系统/连接下拉框并选中默认连接
            applyDefaultErpConnection();
        } else {
            erpPanel.classList.add('hidden');
        }
    }

    // 操作内容区：选择数据来源后显示，未选择时隐藏（避免空白/残留内容）
    const stepUploadEl = document.getElementById('wb-step-upload');
    if (stepUploadEl) {
        if (source) {
            stepUploadEl.classList.remove('hidden');
        } else {
            stepUploadEl.classList.add('hidden');
        }
    }

    // Re-render UI if sub-scene is selected and a source is chosen
    if (source && _workbenchSubScene) {
        renderProcurementDataSourceUI(_workbenchSubScene);
    }
}

// 应用默认 ERP 连接：存在默认连接时隐藏系统/连接下拉框并自动选中，否则允许手动选择
function applyDefaultErpConnection() {
    const systemSelect = document.getElementById('wb-erp-system');
    const connectionSelect = document.getElementById('wb-erp-connection');
    if (!systemSelect || !connectionSelect) return;

    if (!erpConnectionsLoaded) {
        // 连接数据尚未加载，先拉取完成后再应用（工作台执行用轻量连接列表）
        fetch('/api/erp/connections/options')
            .then(r => r.json())
            .then(data => {
                if (data.status === 'success') {
                    erpConnectionsData = data.connections || [];
                    if (!Array.isArray(erpConnectionsData)) erpConnectionsData = [];
                }
                erpConnectionsLoaded = true;
                applyDefaultErpConnection();
            })
            .catch(() => {});
        return;
    }

    const defaultConn = erpConnectionsData.find(c => c.is_default === true);
    const systemRow = document.getElementById('wb-erp-system-row');
    const connectionRow = document.getElementById('wb-erp-connection-row');
    if (defaultConn) {
        // 存在默认连接：隐藏系统/连接下拉框并选中默认连接
        if (systemRow) systemRow.classList.add('hidden');
        if (connectionRow) connectionRow.classList.add('hidden');
        systemSelect.disabled = true;
        if (systemSelect.value !== defaultConn.system || !connectionSelect.querySelector(`option[value="${defaultConn.id}"]`)) {
            systemSelect.value = defaultConn.system;
            onErpSystemChange();
        }
        connectionSelect.disabled = true;
        const opt = connectionSelect.querySelector(`option[value="${defaultConn.id}"]`);
        if (opt) connectionSelect.value = defaultConn.id;
        // onErpSystemChange 会把连接行显示出来，此处重新隐藏
        if (connectionRow) connectionRow.classList.add('hidden');
        onErpConnectionSelectChange();
    } else {
        // 无默认连接：显示系统下拉框，允许手动选择
        if (systemRow) systemRow.classList.remove('hidden');
        systemSelect.disabled = false;
        connectionSelect.disabled = false;
    }
}

function onErpSystemChange() {
    const system = document.getElementById('wb-erp-system').value;
    const connectionRow = document.getElementById('wb-erp-connection-row');
    const filters = document.getElementById('wb-erp-filters');
    const syncBtn = document.getElementById('wb-erp-sync-btn');
    const info = document.getElementById('wb-erp-connection-info');

    // 重置连接选择
    document.getElementById('wb-erp-connection').value = '';
    if (info) info.classList.add('hidden');

    if (system) {
        if (connectionRow) connectionRow.classList.remove('hidden');
        if (filters) filters.classList.remove('hidden');
        if (syncBtn) syncBtn.classList.remove('hidden');
        renderErpConnectionOptions(system);
    } else {
        if (connectionRow) connectionRow.classList.add('hidden');
        if (filters) filters.classList.add('hidden');
        if (syncBtn) syncBtn.classList.add('hidden');
    }
}

function renderErpConnectionOptions(system) {
    const select = document.getElementById('wb-erp-connection');
    if (!select) return;
    // 先重新加载服务端数据，确保下拉框显示最新的全部连接
    const renderAfterLoad = () => {
        const current = select.value;
        const filtered = erpConnectionsData.filter(c => c.system === system);
        select.innerHTML = '<option value="">请选择连接配置</option>' +
            filtered.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
        if (current && filtered.some(c => c.id === current)) {
            select.value = current;
        } else {
            select.value = '';
        }
        onErpConnectionSelectChange();
    };
    // 如果数据还没加载，先从服务端拉取（工作台执行用轻量连接列表）
    if (!erpConnectionsLoaded) {
        fetch('/api/erp/connections/options')
            .then(r => r.json())
            .then(data => {
                if (data.status === 'success') {
                    erpConnectionsData = data.connections || [];
                }
                erpConnectionsLoaded = true;
                renderAfterLoad();
            })
            .catch(renderAfterLoad);
    } else {
        renderAfterLoad();
    }
}

function onErpConnectionSelectChange() {
    const id = document.getElementById('wb-erp-connection').value;
    const info = document.getElementById('wb-erp-connection-info');
    const conn = id ? getErpConnectionById(id) : null;
    if (info) {
        if (conn) {
            const providerLabel = { rfc: 'RFC/BAPI', adt_sql: 'ADT/HTTP', api: 'HTTP API' }[conn.provider] || conn.provider;
            const detail = conn.provider === 'rfc'
                ? `${conn.ashost || ''}:${conn.sysnr || ''}`
                : (conn.base_url || '');
            info.textContent = `${providerLabel} | ${detail} | Client: ${conn.client || '-'}`;
            info.classList.remove('hidden');
        } else {
            info.classList.add('hidden');
        }
    }
}

function openErpConnectionManager() {
    // The scene console has no connection form of its own any more (task 6.5);
    // this entry point now forwards to the console page that owns management.
    openErpConnectionConsole();
}

function _getSelectedErpConnection() {
    const id = document.getElementById('wb-erp-connection').value;
    return id ? getErpConnectionById(id) : null;
}

function _buildErpPayload() {
    const system = document.getElementById('wb-erp-system').value;
    const conn = _getSelectedErpConnection();
    const payload = { system: system };

    if (conn) {
        // 连接参数一律由服务端按 connection_id 从系统配置解析（凭证不经过前端）
        payload.connection_id = conn.id;
        payload.provider = conn.provider;
    }

    // 过滤条件
    const startDate = document.getElementById('wb-erp-filter-start-date').value;
    const endDate = document.getElementById('wb-erp-filter-end-date').value;
    // 默认近三年（由子场景切换时设置）
    let dateRange = null;
    if (startDate && endDate) {
        dateRange = { start: startDate, end: endDate };
    }

    payload.filters = {
        materials: document.getElementById('wb-erp-filter-materials').value.trim(),
        // 编码非空时优先精确匹配，不传名称以避免与 LIKE 的 AND/OR 优先级冲突
        material_names: document.getElementById('wb-erp-filter-materials').value.trim()
            ? ''
            : document.getElementById('wb-erp-filter-material-names').value.trim(),
        date_range: dateRange,
        max_rows: parseInt(document.getElementById('wb-erp-max-rows').value, 10) || 1000
    };

    return payload;
}

// ERP 过滤器标签映射：不同子场景使用不同标签文本
// key: scene_id, value: { label1, hint1, placeholder1, label2, hint2, placeholder2 }
const _ERP_FILTER_LABEL_MAP = {
    supplier_risk: {
        label1: '供应商编码', hint1: '多条用逗号/换行分隔', placeholder1: '如：0000100001,0000100002',
        label2: '供应商名称', hint2: '短文本模糊匹配，多条用逗号/换行分隔', placeholder2: '如：XX化工,XX科技',
    },
    supplier_profile: {
        label1: '供应商编码', hint1: '多条用逗号/换行分隔', placeholder1: '如：0000100001,0000100002',
        label2: '供应商名称', hint2: '短文本模糊匹配，多条用逗号/换行分隔', placeholder2: '如：XX化工,XX科技',
    },
    supplier_quote_comparison: {
        label1: '物料编码', hint1: '多条用逗号/换行分隔', placeholder1: '如：1000000048,1000000049',
        label2: '物料名称', hint2: '短文本模糊匹配，多条用逗号/换行分隔', placeholder2: '如：纸管,纸箱',
    },
    delivery_performance: {
        label1: '物料编码', hint1: '多条用逗号/换行分隔', placeholder1: '如：1000000048,1000000049',
        label2: '物料名称', hint2: '短文本模糊匹配，多条用逗号/换行分隔', placeholder2: '如：纸管,纸箱',
    },
    default: {
        label1: '物料编码', hint1: '多条用逗号/换行分隔', placeholder1: '如：1000000048,1000000049',
        label2: '物料名称', hint2: '短文本模糊匹配，多条用逗号/换行分隔', placeholder2: '如：纸管,纸箱',
    },
};

function updateErpFilterLabels(sub) {
    if (!sub) return;
    const cfg = _ERP_FILTER_LABEL_MAP[sub.id] || _ERP_FILTER_LABEL_MAP.default;

    const label1 = document.getElementById('wb-erp-filter-label-1');
    const hint1 = document.getElementById('wb-erp-filter-hint-1');
    const input1 = document.getElementById('wb-erp-filter-materials');
    if (label1) label1.textContent = cfg.label1;
    if (hint1) hint1.textContent = cfg.hint1;
    if (input1) input1.placeholder = cfg.placeholder1;

    const label2 = document.getElementById('wb-erp-filter-label-2');
    const hint2 = document.getElementById('wb-erp-filter-hint-2');
    const input2 = document.getElementById('wb-erp-filter-material-names');
    if (label2) label2.textContent = cfg.label2;
    if (hint2) hint2.textContent = cfg.hint2;
    if (input2) input2.placeholder = cfg.placeholder2;
}

function _validateErpPayload(payload) {
    if (!payload.system) return '请选择 ERP 系统';
    const conn = _getSelectedErpConnection();
    if (!conn) return '请选择已有连接配置，或到「ERP 连接配置」中添加';
    // 需要物料/供应商过滤条件的子场景校验
    const filterRequiredScenes = ['supplier_quote_comparison', 'supplier_risk', 'supplier_profile'];
    if (_workbenchSubScene && filterRequiredScenes.includes(_workbenchSubScene.id)) {
        const filter1Label = _workbenchSubScene.id.startsWith('supplier_risk') || _workbenchSubScene.id === 'supplier_profile' ? '供应商编码' : '物料编码';
        const filter2Label = filter1Label === '供应商编码' ? '供应商名称' : '物料名称';
        if (!payload.filters || (!payload.filters.materials && !payload.filters.material_names)) {
            return `请输入至少一个${filter1Label}或${filter2Label}`;
        }
    }
    return null;
}

function testErpConnectionWorkbench() {
    const payload = _buildErpPayload();
    const error = _validateErpPayload(payload);
    if (error) {
        showToast(error, 'error');
        return;
    }

    showToast('正在测试连接...', 'info');
    fetch('/api/procurement/erp-sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'test', ...payload })
    })
    .then(r => r.json())
    .then(result => {
        if (result.status === 'success') {
            showToast('连接成功！', 'success');
        } else {
            showToast('连接失败: ' + (result.message || '未知错误'), 'error');
        }
    })
    .catch(err => {
        showToast('连接失败: ' + err.message, 'error');
    });
}

function syncErpData() {
    const payload = _buildErpPayload();
    const error = _validateErpPayload(payload);
    if (error) {
        showToast(error, 'error');
        return;
    }

    payload.action = 'sync';
    payload.scene_id = _workbenchSubScene ? _workbenchSubScene.id : '';

    showToast('正在同步数据...', 'info');
    fetch('/api/procurement/erp-sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(r => r.json())
    .then(result => {
        if (result.status === 'success') {
            // 采购比价分析：过滤掉物料号为空的记录，并按物料编码+创建日期排序
            if (_workbenchSubScene && _workbenchSubScene.id === 'supplier_quote_comparison') {
                const filterEmptyMatnr = (rows) => (rows || []).filter(r => {
                    const v = r['MATNR'] || r['物料编码'];
                    return v !== null && v !== undefined && String(v).trim() !== '';
                });
                const sortErpData = (rows) => rows.slice().sort((a, b) => {
                    const matnrA = a['MATNR'] || a['物料编码'] || '';
                    const matnrB = b['MATNR'] || b['物料编码'] || '';
                    if (matnrA !== matnrB) return matnrA.localeCompare(matnrB);
                    const dateA = a['AEDAT'] || a['创建日期'] || '';
                    const dateB = b['AEDAT'] || b['创建日期'] || '';
                    return dateA.localeCompare(dateB);
                });
                _procurementErpData = sortErpData(filterEmptyMatnr(result.data));
                _procurementErpRawData = result.raw_data ? sortErpData(filterEmptyMatnr(result.raw_data)) : null;
            } else {
                _procurementErpData = result.data || [];
                _procurementErpRawData = result.raw_data || null;
            }
            showToast(`同步成功，共 ${_procurementErpData.length} 条数据`, 'success');
            renderProcurementErpPreview(_workbenchSubScene);
        } else {
            showToast('同步失败: ' + (result.message || '未知错误'), 'error');
        }
    })
    .catch(err => {
        showToast('同步失败: ' + err.message, 'error');
    });
}

function submitWorkbenchAnalysis() {
    if (!_workbenchSubScene) {
        showToast('请先选择分析维度', 'error');
        return;
    }

    // Check data availability based on upload mode
    let hasData = false;
    if (_workbenchUploadMode === 'single') {
        hasData = _workbenchFileData && _workbenchFileData.length > 0;
    } else {
        hasData = Object.keys(_workbenchMultiData).length > 0;
    }
    if (!hasData) {
        showToast('请先上传数据文件', 'error');
        return;
    }

    // Build summary statistics from data (not sending raw data)
    const summary = buildDataSummary();

    // Prepare files for server upload
    const filesToUpload = {};
    if (_workbenchUploadMode === 'single') {
        const mappedFields = Object.entries(_workbenchMapping).filter(([k, v]) => v);
        const headers = mappedFields.map(([f, h]) => f);
        const rows = _workbenchFileData.map(row => {
            return mappedFields.map(([field, header]) => {
                const idx = _workbenchHeaders.indexOf(header);
                return idx >= 0 ? (row[idx] || '') : '';
            });
        });
        const csvContent = [headers.join(','), ...rows.map(r => r.join(','))].join('\n');
        filesToUpload['data'] = {
            filename: 'financial_data.csv',
            content: csvContent
        };
    } else {
        Object.entries(_workbenchMultiData).forEach(([type, data]) => {
            const headers = _workbenchMultiHeaders[type];
            const csvContent = [headers.join(','), ...data.map(r => r.join(','))].join('\n');
            filesToUpload[type] = {
                filename: `${type}.csv`,
                content: csvContent
            };
        });
    }

    // Upload files to server
    fetch('/api/workbench/upload', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            files: filesToUpload,
            summary: summary,
            session_id: sessionId
        })
    })
    .then(r => r.json())
    .then(uploadResult => {
        if (uploadResult.status !== 'success') {
            showToast(uploadResult.message || '文件上传失败', 'error');
            return;
        }

        // Build analysis prompt with file paths and summary only
        const filePaths = Object.entries(uploadResult.files).map(([type, path]) => {
            return `- ${getStatementName(type) || '数据文件'}: ${path}`;
        }).join('\n');

        const analysisPrompt = `请对以下财务数据进行${_workbenchSubScene.name}分析。

## 数据文件位置
${filePaths}

## 数据汇总统计
${summary}

## 分析要求
${_workbenchSubScene.system_prompt}

请读取数据文件，生成专业的分析报告，包含：
1. 核心指标计算结果（基于原始数据精确计算）
2. 趋势分析（同比/环比变化）
3. 与行业基准的对比
4. 主要发现与风险点
5. 改善建议

注意：数据文件已保存在服务器，请使用文件读取工具获取完整数据进行分析。`;

        // Capture sub-scene data before closing workbench
        const subScene = _workbenchSubScene;
        const parentScene = _workbenchScene;

        // Close workbench and start chat
        closeWorkbench();

        // Create new chat session with sub-scene context
        newChat();

        // Activate sub-scene
        return fetch('/api/scenes/activate', {
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
                    workbench_files: uploadResult.files
                }
            }),
        })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'success') {
                showToast(t('scenes_switched_sub').replace('{name}', subScene.name), 'success');
                // Send analysis prompt as user message
                setTimeout(() => {
                    sendMessage(analysisPrompt);
                }, 500);
            } else {
                showToast(data.message || '分析启动失败', 'error');
            }
        });
    })
    .catch(err => {
        showToast('网络错误：' + err.message, 'error');
    });
}

function buildDataSummary() {
    const summary = [];

    if (_workbenchUploadMode === 'single') {
        // Find year/period column
        const yearIdx = _workbenchHeaders.findIndex(h => h.includes('年') || h.includes('年度') || h.includes('期间') || h.includes('日期'));
        const numericFields = _workbenchHeaders.filter((h, i) => i !== yearIdx && _workbenchFileData.some(r => !isNaN(parseFloat(r[i]))));

        summary.push(`数据期间：${_workbenchFileData.length} 期`);
        if (yearIdx >= 0) {
            const years = _workbenchFileData.map(r => r[yearIdx]).filter(y => y);
            summary.push(`时间范围：${years[0]} 至 ${years[years.length - 1]}`);
        }
        summary.push('');

        numericFields.forEach(field => {
            const idx = _workbenchHeaders.indexOf(field);
            const values = _workbenchFileData.map(r => parseFloat(r[idx])).filter(v => !isNaN(v));
            if (values.length === 0) return;

            const sum = values.reduce((a, b) => a + b, 0);
            const avg = sum / values.length;
            const min = Math.min(...values);
            const max = Math.max(...values);
            const first = values[0];
            const last = values[values.length - 1];
            const growth = first !== 0 ? ((last - first) / first * 100).toFixed(1) : 0;

            summary.push(`${field}：`);
            summary.push(`  平均值：${avg.toLocaleString('zh-CN', {maximumFractionDigits: 0})}`);
            summary.push(`  最小值：${min.toLocaleString('zh-CN', {maximumFractionDigits: 0})}`);
            summary.push(`  最大值：${max.toLocaleString('zh-CN', {maximumFractionDigits: 0})}`);
            summary.push(`  总增长：${growth}%`);
            summary.push('');
        });
    } else {
        // Multi-file summary
        Object.entries(_workbenchMultiData).forEach(([type, data]) => {
            const headers = _workbenchMultiHeaders[type];
            const yearIdx = headers.findIndex(h => h.includes('年') || h.includes('年度') || h.includes('期间'));
            summary.push(`【${getStatementName(type)}】`);
            summary.push(`  数据期间：${data.length} 期`);
            if (yearIdx >= 0) {
                const years = data.map(r => r[yearIdx]).filter(y => y);
                summary.push(`  时间范围：${years[0]} 至 ${years[years.length - 1]}`);
            }
            summary.push('');
        });
    }

    return summary.join('\n');
}

function showToast(message, type) {
    const toast = document.createElement('div');
    const bgClass = type === 'error' ? 'bg-red-500' : 'bg-green-500';
    toast.className = `fixed top-4 right-4 ${bgClass} text-white px-4 py-2 rounded-lg shadow-lg z-[300] text-sm`;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => {
        toast.classList.add('opacity-0', 'transition-opacity', 'duration-300');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

