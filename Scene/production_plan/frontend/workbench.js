/* =====================================================================
   Scheduling Workbench - Production Planning Module
   ===================================================================== */

// Utility: escape HTML to prevent XSS
function escapeHtml(str) {
    if (str == null) return '';
    const div = document.createElement('div');
    div.appendChild(document.createTextNode(String(str)));
    return div.innerHTML;
}

// Utility: extract time_per_unit from row with fuzzy column matching
function getTimePerUnit(row) {
    for (const key of Object.keys(row)) {
        const lower = key.toLowerCase().replace(/\s/g, '');
        if (lower.includes('工时') || lower.includes('耗时') || lower.includes('time')) {
            const val = parseFloat(row[key]);
            if (!isNaN(val) && val > 0) {
                // If unit is hours, convert to minutes
                if (lower.includes('小时') || lower.includes('hr') || lower.includes('/h')) {
                    return val * 60;
                }
                return val;
            }
        }
    }
    return 0;
}

// Scheduling workbench state
let _schedulingScene = null;
let _schedulingSubScene = null;
let _schedulingDataSource = 'manual';
let _schedulingFileData = null;
let _schedulingFileHeaders = [];
let _schedulingFileMapping = {};
let _schedulingErpConfig = null;
let _schedulingErpData = null;
let _schedulingOrders = [];
let _schedulingResult = null;
let _schedulingHistoryVisible = false;

// Multi-sheet Excel data cache
let _schedulingFileSheets = {};

// Standard scheduling fields for single-sheet fallback
const SCHEDULING_STANDARD_FIELDS = [
    '工单号', '产品名称', '数量', '交期', '优先级',
    '设备需求', '换线时间(分钟)', '班组需求', '备注'
];

// Helper: build orders from multi-sheet data
// Supports old template (排产测试数据2.xlsx) and new template (排产测试数据3.xlsx)
// Returns { orders, machines, materials, templateVersion }
function buildOrdersFromSheets(sheets) {
    // Detect template version
    // v3 (new customer): BOM结构 has '父级' column; 生产订单信息 has '父级' column
    // v2 (old customer): BOM结构 has '母件料号' column
    const hasParentCol = !!(sheets['BOM结构'] && sheets['BOM结构'].length > 0 &&
        (sheets['BOM结构'][0]['父级'] !== undefined || sheets['BOM结构'][0]['父级'] !== undefined));
    const hasParentOrder = !!(sheets['生产订单信息'] && sheets['生产订单信息'].length > 0 &&
        sheets['生产订单信息'][0]['父级'] !== undefined);
    const templateVersion = (hasParentCol || hasParentOrder) ? 'v3' : 'v2';
    const isV3 = templateVersion === 'v3';

    const ordersSheet = sheets['生产订单信息'] || sheets['工单主数据'] || sheets['排产测试数据'] || [];
    const processesSheet = sheets['工艺路线'] || sheets['工序路线'] || [];
    const bomsSheet = sheets['BOM结构'] || sheets['BOM物料'] || [];
    const machinesSheet = sheets['工作中心'] || sheets['设备资源'] || [];
    const inventorySheet = sheets['物料库存'] || sheets['库存'] || sheets['库存数据'] || sheets['物料数据'] || [];
    const onTheWaySheet = sheets['在途物料'] || [];

    // Build material inventory lookup (merge inventory + on-the-way)
    const materialInventory = {};

    inventorySheet.forEach(row => {
        const code = String(row['料号'] || row['物料名称'] || '').trim();
        const name = String(row['品名'] || row['物料名称'] || '').trim();
        const key = code || name;
        if (!key) return;
        materialInventory[key] = {
            material_code: code,
            material_name: name || code,
            stock: parseFloat(row['库存数量'] || row['数量'] || row['当前库存'] || row['库存'] || 0) || 0,
            safety_stock: parseFloat(row['安全库存'] || 0) || 0,
            on_the_way: 0
        };
    });

    onTheWaySheet.forEach(row => {
        const code = String(row['料号'] || '').trim();
        const name = String(row['品名'] || '').trim();
        const key = code || name;
        if (!key) return;
        if (!materialInventory[key]) {
            materialInventory[key] = {
                material_code: code,
                material_name: name || code,
                stock: 0,
                safety_stock: 0,
                on_the_way: 0
            };
        }
        materialInventory[key].on_the_way += parseFloat(row['在途数量'] || row['数量'] || row['在途'] || 0) || 0;
    });

    // Group processes by 料号/工单号
    const processMap = {};
    processesSheet.forEach(row => {
        const key = String(row['料号'] || row['工单号'] || '').trim();
        if (!key) return;
        if (!processMap[key]) processMap[key] = [];
        processMap[key].push({
            process_name: String(row['工序名称'] || row['工序名'] || '').trim(),
            time_per_unit: getTimePerUnit(row),
            machine_id: String(row['工作中心'] || row['设备编号'] || '').trim() || undefined,
            mold_id: String(row['模具'] || '').trim() || undefined,
            sequence: parseInt(row['行号'] || row['工序序号'] || 0) || processMap[key].length + 1
        });
    });
    // Sort processes by sequence
    Object.keys(processMap).forEach(key => {
        processMap[key].sort((a, b) => a.sequence - b.sequence);
    });

    // Group BOM by 母件料号/父级/工单号
    const bomMap = {};
    bomsSheet.forEach(row => {
        const parentKey = String(row['母件料号'] || row['父级'] || row['工单号'] || '').trim();
        if (!parentKey) return;
        if (!bomMap[parentKey]) bomMap[parentKey] = [];
        bomMap[parentKey].push({
            material_code: String(row['料号'] || '').trim(),
            material_name: String(row['品名'] || row['物料名称'] || '').trim(),
            quantity: parseFloat(row['用量'] || 0) || 0,
            unit: String(row['单位'] || '个').trim(),
            item_type: String(row['类型'] || 'raw_material').trim(),
            base_qty: parseFloat(row['底数'] || 1) || 1
        });
    });

    // Parse machines from sheet
    let machines = [];
    if (machinesSheet.length > 0) {
        // Check if sheet has date columns (v3 format)
        const firstRow = machinesSheet[0] || {};
        const dateCols = Object.keys(firstRow).filter(k => /^\d{4}-\d{2}-\d{2}$/.test(k.trim()));
        const hasDateCols = dateCols.length > 0;
        if (hasDateCols) {
            // v3 format: 工作中心 + date columns with hour capacity
            machines = machinesSheet.map((row) => {
                const wcName = String(row['工作中心'] || '').trim();
                const daily_capacity = {};
                dateCols.forEach(col => {
                    const val = parseFloat(row[col]);
                    if (!isNaN(val)) {
                        daily_capacity[col.trim()] = val;
                    }
                });
                return {
                    machine_id: wcName,
                    machine_name: wcName,
                    capacity_per_day: 8,  // fallback
                    daily_capacity: daily_capacity
                };
            }).filter(m => m.machine_name);
        } else {
            machines = machinesSheet.map(row => ({
                machine_id: String(row['设备编号'] || row['设备ID'] || row['工作中心'] || '').trim(),
                machine_name: String(row['设备名称'] || row['工作中心'] || '').trim(),
                capacity_per_day: parseInt(row['日产能(件)'] || row['日产能'] || 0) || 500
            })).filter(m => m.machine_id);
        }
    }

    const materials = Object.values(materialInventory).filter(m => m.material_name);

    // Build orders
    const orders = ordersSheet.map(row => {
        const wo = String(row['生产订单号'] || row['工单号'] || '').trim();
        const productCode = String(row['料号'] || '').trim();
        const productName = String(row['品名'] || row['产品名称'] || '').trim();
        const rawParent = row['父级'];
        const parentId = rawParent !== undefined && rawParent !== null ? String(rawParent).trim() : '';
        return {
            工单号: wo,
            料号: productCode,
            产品名称: productName,
            数量: parseFloat(row['计划数量'] || row['数量']) || 0,
            交期: String(row['需求时间'] || row['交期'] || '').trim(),
            优先级: String(row['优先级'] || '中').trim(),
            设备需求: String(row['设备需求'] || '').trim(),
            换线时间: parseFloat(row['换线时间(分钟)'] || row['换线时间'] || 60),
            班组需求: String(row['班组需求'] || '白班').trim(),
            备注: String(row['备注'] || '').trim(),
            销售单号: String(row['销售单号'] || '').trim() || undefined,
            父级: parentId === '0' ? '0' : (parentId || undefined),
            批次: parseFloat(row['批次'] || row['批 次'] || 0) || undefined,
            批次时间: String(row['批次时间'] || '').trim() || undefined,
            工序路线: processMap[productCode] || processMap[wo] || [],
            BOM物料清单: bomMap[productCode] || bomMap[wo] || []
        };
    }).filter(o => o.工单号);

    return { orders, machines, materials, templateVersion };
}

function openSchedulingWorkbench(scene) {
    _schedulingScene = scene;
    _schedulingSubScene = scene.sub_scenes ? scene.sub_scenes[0] : null;
    _schedulingDataSource = 'manual';
    _schedulingFileData = null;
    _schedulingFileHeaders = [];
    _schedulingFileMapping = {};
    _schedulingErpData = null;
    _schedulingOrders = [];
    _schedulingResult = null;
    _schedulingHistoryVisible = false;

    const modal = document.getElementById('scheduling-workbench-modal');
    // 移动端：重置「功能说明」抽屉为收起状态
    const wbLeftPanel = modal ? modal.querySelector('.w-80') : null;
    if (wbLeftPanel) wbLeftPanel.classList.remove('open');
    const title = document.getElementById('scheduling-wb-title');
    const subtitle = document.getElementById('scheduling-wb-subtitle');

    title.textContent = scene.workbench_title || scene.name + '工作台';
    subtitle.textContent = '基于有限能力约束的多工序智能排产，支持物料齐套分析、瓶颈识别、甘特图可视化';

    // Reset UI
    document.getElementById('scheduling-wb-history-panel').classList.add('hidden');
    document.getElementById('scheduling-wb-main-content').classList.remove('hidden');
    document.getElementById('scheduling-wb-result-area').classList.add('hidden');
    document.getElementById('scheduling-wb-params-panel').classList.remove('hidden');

    // Render indicators
    renderSchedulingIndicators();

    // Render demo links
    renderSchedulingDemoLinks();

    // Reset data source
    switchSchedulingDataSource('manual');

    // Setup file upload
    setupSchedulingFileUpload();

    modal.classList.remove('hidden');
}

function closeSchedulingWorkbench() {
    document.getElementById('scheduling-workbench-modal').classList.add('hidden');
    _schedulingScene = null;
    _schedulingSubScene = null;
}

function renderSchedulingIndicators() {
    const container = document.getElementById('scheduling-wb-indicators');
    if (!container || !_schedulingSubScene || !_schedulingSubScene.indicators) return;

    container.innerHTML = _schedulingSubScene.indicators.map(ind => `
        <div class="p-3 bg-slate-50 dark:bg-slate-800/50 rounded-lg">
            <div class="flex items-center gap-2 mb-1">
                <span class="text-xs font-semibold text-slate-700 dark:text-slate-200">${escapeHtml(ind.name)}</span>
                <span class="text-[10px] px-1.5 py-0.5 rounded bg-primary-100 dark:bg-primary-900/30 text-primary-600 dark:text-primary-400">${escapeHtml(ind.formula)}</span>
            </div>
            <p class="text-xs text-slate-500 dark:text-slate-400 leading-relaxed">${escapeHtml(ind.meaning)}</p>
        </div>
    `).join('');
}

function renderSchedulingDemoLinks() {
    const container = document.getElementById('scheduling-wb-demo-links');
    if (!container || !_schedulingSubScene) return;

    container.innerHTML = '';
    const sub = _schedulingSubScene;

    // DEMO 链接改为应用内预览弹层打开，不再跳转新网页
    const makeLink = (url, label, cls) => {
        const a = document.createElement('a');
        a.href = (typeof _toWebUrl === 'function') ? _toWebUrl(url) : url;
        a.className = `text-[10px] px-2 py-0.5 rounded ${cls} transition-colors font-medium cursor-pointer`;
        a.textContent = label;
        a.addEventListener('click', (e) => {
            e.preventDefault();
            if (typeof openFilePreview === 'function') openFilePreview(a.href, label);
        });
        return a;
    };

    if (sub.demo_md) {
        container.appendChild(makeLink(sub.demo_md, 'DEMO-Markdown', 'bg-emerald-50 text-emerald-600 hover:bg-emerald-100'));
    }
    if (sub.demo_html) {
        container.appendChild(makeLink(sub.demo_html, 'DEMO-HTML', 'bg-blue-50 text-blue-600 hover:bg-blue-100'));
    }
    if (sub.demo_url) {
        const label = sub.demo_url.includes('gantt') ? 'DEMO-甘特图' : 'DEMO-HTML';
        container.appendChild(makeLink(sub.demo_url, label, 'bg-blue-50 text-blue-600 hover:bg-blue-100'));
    }
}

function switchSchedulingDataSource(source) {
    _schedulingDataSource = source;

    // Update tab styles
    ['manual', 'file', 'erp'].forEach(s => {
        const btn = document.getElementById('scheduling-source-' + s);
        if (!btn) return;
        if (s === source) {
            btn.classList.remove('bg-slate-100', 'dark:bg-slate-800', 'text-slate-600', 'dark:text-slate-300');
            btn.classList.add('bg-primary-500', 'text-white');
        } else {
            btn.classList.remove('bg-primary-500', 'text-white');
            btn.classList.add('bg-slate-100', 'dark:bg-slate-800', 'text-slate-600', 'dark:text-slate-300');
        }
    });

    // Show/hide areas
    document.getElementById('scheduling-wb-form-area').classList.toggle('hidden', source !== 'manual');
    document.getElementById('scheduling-wb-file-area').classList.toggle('hidden', source !== 'file');
    document.getElementById('scheduling-wb-erp-area').classList.toggle('hidden', source !== 'erp');

    if (source === 'manual') {
        renderSchedulingForm();
    }
}

function renderSchedulingForm() {
    const container = document.getElementById('scheduling-wb-form');
    if (!container || !_schedulingSubScene) return;

    const fieldTypes = _schedulingSubScene.field_types || {};
    const requiredFields = _schedulingSubScene.required_fields || [];
    const optionalFields = _schedulingSubScene.optional_fields || [];

    function renderField(field, isRequired, rowIndex) {
        const fieldConfig = fieldTypes[field];
        const requiredMark = isRequired ? '<span class="text-red-500">*</span>' : '';
        const labelClass = isRequired ? 'text-slate-700 dark:text-slate-200' : 'text-slate-500 dark:text-slate-400';
        const name = `scheduling_${rowIndex}_${escapeHtml(field)}`;
        const placeholder = isRequired ? `请输入${field}` : `请输入${field}（选填）`;

        if (fieldConfig && fieldConfig.type === 'select') {
            const options = fieldConfig.options || [];
            const optionsHtml = options.map(opt => `<option value="${escapeHtml(opt)}">${escapeHtml(opt)}</option>`).join('');
            return `
                <div>
                    <label class="block text-xs font-medium ${labelClass} mb-1">${escapeHtml(field)} ${requiredMark}</label>
                    <select name="${name}" class="w-full px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-primary-500">
                        <option value="">请选择${escapeHtml(field)}</option>
                        ${optionsHtml}
                    </select>
                </div>
            `;
        } else {
            return `
                <div>
                    <label class="block text-xs font-medium ${labelClass} mb-1">${escapeHtml(field)} ${requiredMark}</label>
                    <input type="text" name="${name}" placeholder="${placeholder}" class="w-full px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-primary-500">
                </div>
            `;
        }
    }

    // If no orders, add one empty row
    if (_schedulingOrders.length === 0) {
        _schedulingOrders.push({});
    }

    container.innerHTML = _schedulingOrders.map((order, idx) => {
        const fieldsHtml = requiredFields.map(f => renderField(f, true, idx)).join('') +
            optionalFields.map(f => renderField(f, false, idx)).join('');
        return `
            <div class="scheduling-order-row p-4 bg-slate-50 dark:bg-slate-800/30 rounded-xl border border-slate-200 dark:border-white/5 relative" data-index="${idx}">
                <div class="flex items-center justify-between mb-3">
                    <span class="text-xs font-semibold text-slate-700 dark:text-slate-200">工单 #${idx + 1}</span>
                    ${idx > 0 ? `<button onclick="removeSchedulingOrderRow(${idx})" class="text-xs text-red-500 hover:text-red-600 transition-colors"><i class="fas fa-trash-can"></i> 删除</button>` : ''}
                </div>
                <div class="grid grid-cols-2 gap-3">${fieldsHtml}</div>
            </div>
        `;
    }).join('');
}

function addSchedulingOrderRow() {
    _schedulingOrders.push({});
    renderSchedulingForm();
}

function removeSchedulingOrderRow(index) {
    _schedulingOrders.splice(index, 1);
    renderSchedulingForm();
}

function collectSchedulingFormData() {
    const rows = document.querySelectorAll('.scheduling-order-row');
    const orders = [];
    const requiredFields = _schedulingSubScene.required_fields || [];

    rows.forEach(row => {
        const order = {};
        const inputs = row.querySelectorAll('input, select');
        inputs.forEach(input => {
            const name = input.name;
            const fieldName = name.replace(/^scheduling_\d+_/, '');
            order[fieldName] = input.value.trim();
        });
        orders.push(order);
    });

    // Validate
    for (let i = 0; i < orders.length; i++) {
        for (const field of requiredFields) {
            if (!orders[i][field]) {
                showToast(`工单 #${i + 1} 的「${field}」为必填项`, 'error');
                return null;
            }
        }
    }

    return orders;
}

async function _submitSchedulingPrompt(prompt) {
    const subScene = _schedulingSubScene;
    const parentScene = _schedulingScene;

    closeSchedulingWorkbench();

    // Create new chat session with sub-scene context
    newChat();

    // Activate sub-scene
    try {
        const activateResp = await fetch('/api/scenes/activate', {
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
        });
        const activateData = await activateResp.json();
        if (activateData.status !== 'success') {
            showToast(activateData.message || '排产启动失败', 'error');
            return;
        }
        showToast(`已切换到：${subScene.name}`, 'success');

        // Small delay to let scene activation settle
        await new Promise(r => setTimeout(r, 500));
        sendMessage(prompt);
    } catch (err) {
        showToast('网络错误：' + err.message, 'error');
    }
}

function buildSchedulingDataPackage(orders, extraMachines, extraMaterials) {
    const mode = document.getElementById('scheduling-param-mode').value;
    const objective = document.getElementById('scheduling-param-objective').value;
    const iterations = parseInt(document.getElementById('scheduling-param-iterations').value) || 500;

    // Default machines / materials as fallback
    const defaultMachines = [
        {"machine_id": "M001", "machine_name": "注塑机-1", "capacity_per_day": 500},
        {"machine_id": "M002", "machine_name": "注塑机-2", "capacity_per_day": 500},
        {"machine_id": "M003", "machine_name": "CNC-1", "capacity_per_day": 200},
        {"machine_id": "M004", "machine_name": "CNC-2", "capacity_per_day": 200},
        {"machine_id": "M005", "machine_name": "装配线-1", "capacity_per_day": 1000}
    ];
    const defaultMaterials = [
        {"material_name": "原材料A", "stock": 1000, "on_the_way": 500, "lead_time": 3},
        {"material_name": "原材料B", "stock": 500, "on_the_way": 0, "lead_time": 5}
    ];

    return {
        version: "1.0",
        generated_at: new Date().toISOString(),
        params: {
            mode: mode,
            objective: objective,
            iterations: iterations,
            mode_label: mode === 'forward' ? '正向排产' : '逆向排产',
            objective_label: objective === 'tardiness' ? '最小化延期' : objective === 'makespan' ? '最小化总工期' : objective === 'cost' ? '最小化成本' : '平衡模式'
        },
        orders: orders,
        machines: (extraMachines && extraMachines.length > 0) ? extraMachines : defaultMachines,
        teams: [
            {"team_name": "白班A组", "headcount": 12, "shift": "day"},
            {"team_name": "夜班B组", "headcount": 8, "shift": "night"}
        ],
        materials: (extraMaterials && extraMaterials.length > 0) ? extraMaterials : defaultMaterials
    };
}

async function uploadSchedulingDataAsFile(dataObj, fileName) {
    const blob = new Blob([JSON.stringify(dataObj, null, 2)], { type: 'application/json' });
    const file = new File([blob], fileName, { type: 'application/json' });

    const formData = new FormData();
    formData.append('file', file);
    formData.append('session_id', sessionId);

    const resp = await fetch('/upload', { method: 'POST', body: formData });
    const data = await resp.json();
    if (data.status !== 'success') {
        throw new Error(data.message || '文件上传失败');
    }
    return data;
}

function buildSchedulingPrompt(subScene, formData, rows, fileAttachment) {
    const mode = document.getElementById('scheduling-param-mode').value;
    const objective = document.getElementById('scheduling-param-objective').value;
    const iterations = parseInt(document.getElementById('scheduling-param-iterations').value) || 500;

    let prompt = `请作为${subScene.name}专家，根据以下信息执行智能排产：\n\n`;
    prompt += `【功能】${subScene.name}\n`;
    prompt += `【说明】${subScene.description}\n\n`;

    prompt += `【排产参数】\n`;
    prompt += `排产模式：${mode === 'forward' ? '正向排产' : '逆向排产'}\n`;
    prompt += `优化目标：${objective === 'tardiness' ? '最小化延期' : objective === 'makespan' ? '最小化总工期' : objective === 'cost' ? '最小化成本' : '平衡模式'}\n`;
    prompt += `迭代次数：${iterations}\n`;
    prompt += `工单数量：${rows ? rows.length : (formData ? 1 : 0)}\n\n`;

    if (fileAttachment) {
        prompt += `【数据文件】\n`;
        prompt += `完整的排产数据（含工单、设备、班组、物料库存）已作为附件上传，文件名为「${fileAttachment.file_name}」。\n`;
        prompt += `请使用文件读取工具读取该JSON文件，获取完整的排产数据进行分析和排程。\n\n`;
    } else if (rows && rows.length > 0) {
        prompt += `【工单数据】共 ${rows.length} 条记录\n`;
        rows.forEach((row, idx) => {
            prompt += `--- 工单 ${idx + 1} ---\n`;
            Object.entries(row).forEach(([key, value]) => {
                if (value !== '' && value !== undefined && value !== null) {
                    if (Array.isArray(value)) {
                        if (value.length > 0) {
                            prompt += `${key}：${JSON.stringify(value)}\n`;
                        }
                    } else {
                        prompt += `${key}：${value}\n`;
                    }
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

    prompt += `\n请基于以上信息，调用pmc-scheduler技能执行智能排产，提供：\n`;
    prompt += `1. 排产结果摘要（工单总数、准时交付率、延期工单、总工期）\n`;
    prompt += `2. 交互式甘特图HTML（支持按设备和按订单两种视图切换，含物料需求计划表格）\n`;
    prompt += `3. 物料需求预警和采购建议\n`;
    prompt += `4. 瓶颈分析和关键建议\n`;
    return prompt;
}

async function executeScheduling() {
    const orders = collectSchedulingFormData();
    if (!orders) return;

    const mode = document.getElementById('scheduling-param-mode').value;
    if (mode === 'bom_tree') {
        await executeBOMTreeScheduling(orders);
        return;
    }

    showToast('正在准备排产数据...', 'info');
    try {
        const dataPackage = buildSchedulingDataPackage(orders);
        const fileInfo = await uploadSchedulingDataAsFile(dataPackage, 'scheduling_data.json');
        pendingAttachments.push(fileInfo);
        const prompt = buildSchedulingPrompt(_schedulingSubScene, null, orders, fileInfo);
        _submitSchedulingPrompt(prompt);
    } catch (err) {
        showToast('数据上传失败: ' + err.message, 'error');
    }
}

async function executeBOMTreeScheduling(orders) {
    showToast('正在执行BOM树排程...', 'info');
    try {
        // 优先使用Excel多sheet解析的完整数据（含工序路线、BOM等）
        let finalOrders = orders;
        let extraMachines = [];
        let extraMaterials = [];
        let extraTeams = [];
        if (_schedulingFileSheets && Object.keys(_schedulingFileSheets).length > 0) {
            const parsed = buildOrdersFromSheets(_schedulingFileSheets);
            if (parsed.orders && parsed.orders.length > 0) {
                finalOrders = parsed.orders;
                extraMachines = parsed.machines || [];
                extraMaterials = parsed.materials || [];
                extraTeams = parsed.teams || [];
            }
        }

        const dataPackage = buildSchedulingDataPackage(finalOrders);
        const transferTime = parseFloat(document.getElementById('scheduling-param-transfer').value) || 5.0;

        // Build bom_db and process_db from multi-sheet data if available
        let bomDb = {};
        let processDb = {};

        if (_schedulingFileSheets) {
            const bomsSheet = _schedulingFileSheets['BOM结构'] || _schedulingFileSheets['BOM物料'] || [];
            const processesSheet = _schedulingFileSheets['工艺路线'] || _schedulingFileSheets['工序路线'] || [];

            bomsSheet.forEach(row => {
                const product = String(row['母件料号'] || row['产品名称'] || row['父件'] || '').trim();
                if (!product) return;
                if (!bomDb[product]) bomDb[product] = [];
                bomDb[product].push({
                    material_code: String(row['料号'] || '').trim(),
                    material_name: String(row['品名'] || row['物料名称'] || '').trim(),
                    quantity: parseFloat(row['用量'] || 0) || 0,
                    unit: String(row['单位'] || '个').trim(),
                    item_type: String(row['类型'] || 'raw_material').trim(),
                    base_qty: parseFloat(row['底数'] || 1) || 1
                });
            });

            processesSheet.forEach(row => {
                const product = String(row['料号'] || row['产品名称'] || row['工单号'] || '').trim();
                if (!product) return;
                if (!processDb[product]) processDb[product] = [];
                processDb[product].push({
                    process_name: String(row['工序名称'] || row['工序名'] || '').trim(),
                    time_per_unit: getTimePerUnit(row),
                    machine_id: String(row['工作中心'] || row['设备编号'] || '').trim() || undefined,
                    mold_id: String(row['模具'] || '').trim() || undefined,
                    setup_time: parseFloat(row['换线时间'] || 30),
                    sequence: parseInt(row['行号'] || row['工序序号'] || 0) || processDb[product].length + 1
                });
            });

            Object.keys(processDb).forEach(product => {
                processDb[product].sort((a, b) => a.sequence - b.sequence);
            });
        }

        // 合并Excel中的工作中心和团队到config
        const mergedMachines = dataPackage.machines.concat(extraMachines);
        const mergedTeams = dataPackage.teams.concat(extraTeams);

        const resp = await fetch('/api/scheduling/bom-tree', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                orders: finalOrders,
                config: {
                    machines: mergedMachines,
                    teams: mergedTeams,
                    materials: dataPackage.materials.concat(extraMaterials)
                },
                params: {
                    mode: 'backward',
                    objective: dataPackage.params.objective,
                    iterations: dataPackage.params.iterations,
                    transfer_time_hours: transferTime
                },
                bom: bomDb,
                process_db: processDb
            })
        });

        const result = await resp.json();
        if (result.status === 'success') {
            showToast('BOM树排程完成', 'success');
            // Send result to main chat like other modes
            try {
                const resultFile = await uploadSchedulingDataAsFile(result, 'bom_tree_result.json');
                pendingAttachments.push(resultFile);
                const summary = result.summary || {};
                const prompt = `BOM树排程已完成，结果摘要如下：\n\n**排产概况**\n- 工单总数: ${summary.total_orders || 0}\n- 准时交付率: ${summary.on_time_rate || '0%'}\n- 总工期: ${summary.makespan || '-'}\n- 延期工单: ${summary.late_orders || 0}\n- 齐套工单: ${summary.kit_ok_orders || 0}\n- 缺料工单: ${summary.kit_nok_orders || 0}\n\n请基于附件中的完整排产结果，生成详细的排产报告和可视化图表。`;
                await _submitSchedulingPrompt(prompt);
            } catch (submitErr) {
                console.error('[Scheduling] Failed to submit BOM tree result:', submitErr);
                showToast('结果提交失败: ' + submitErr.message, 'error');
            }
        } else {
            showToast('排产失败: ' + (result.message || '未知错误'), 'error');
        }
    } catch (err) {
        showToast('BOM树排程失败: ' + err.message, 'error');
    }
}

async function executeSchedulingFromFile() {
    const sheets = _schedulingFileSheets || {};
    const hasMultiSheet = Object.keys(sheets).length > 0;

    let orders = [];
    let extraMachines = [];
    let extraMaterials = [];

    if (hasMultiSheet) {
        const parsed = buildOrdersFromSheets(sheets);
        orders = parsed.orders;
        extraMachines = parsed.machines;
        extraMaterials = parsed.materials;
        if (orders.length === 0) {
            showToast('未能从文件中解析出有效工单数据', 'error');
            return;
        }
    } else if (_schedulingFileData && _schedulingFileData.length > 0) {
        orders = _schedulingFileData.map(row => ({
            工单号: String(row['工单号'] || '').trim(),
            产品名称: String(row['产品名称'] || '').trim(),
            数量: parseFloat(row['数量']) || 0,
            交期: String(row['交期'] || '').trim(),
            优先级: String(row['优先级'] || '中').trim(),
            设备需求: String(row['设备需求'] || '').trim(),
            换线时间: parseFloat(row['换线时间(分钟)'] || row['换线时间'] || 60),
            班组需求: String(row['班组需求'] || '白班').trim(),
            备注: String(row['备注'] || '').trim(),
            工序路线: [],
            BOM物料清单: []
        })).filter(o => o.工单号);
    } else {
        showToast('请先上传文件', 'error');
        return;
    }

    const mode = document.getElementById('scheduling-param-mode').value;
    if (mode === 'bom_tree') {
        await executeBOMTreeSchedulingFromFile(orders, extraMachines, extraMaterials);
        return;
    }

    showToast('正在准备排产数据...', 'info');
    try {
        const dataPackage = buildSchedulingDataPackage(orders, extraMachines, extraMaterials);
        const fileInfo = await uploadSchedulingDataAsFile(dataPackage, 'scheduling_data.json');
        pendingAttachments.push(fileInfo);
        const prompt = buildSchedulingPrompt(_schedulingSubScene, null, orders, fileInfo);
        _submitSchedulingPrompt(prompt);
    } catch (err) {
        showToast('数据上传失败: ' + err.message, 'error');
    }
}

async function executeBOMTreeSchedulingFromFile(orders, extraMachines, extraMaterials) {
    showToast('正在执行BOM树排程...', 'info');
    try {
        const dataPackage = buildSchedulingDataPackage(orders, extraMachines, extraMaterials);
        const transferTime = parseFloat(document.getElementById('scheduling-param-transfer').value) || 5.0;

        let bomDb = {};
        let processDb = {};

        if (_schedulingFileSheets) {
            // Support both old and new template sheet names
            const bomsSheet = _schedulingFileSheets['BOM结构'] || _schedulingFileSheets['BOM物料'] || [];
            const processesSheet = _schedulingFileSheets['工艺路线'] || _schedulingFileSheets['工序路线'] || [];

            bomsSheet.forEach(row => {
                const product = String(row['母件料号'] || row['产品名称'] || row['父件'] || '').trim();
                if (!product) return;
                if (!bomDb[product]) bomDb[product] = [];
                bomDb[product].push({
                    material_code: String(row['料号'] || '').trim(),
                    material_name: String(row['品名'] || row['物料名称'] || '').trim(),
                    quantity: parseFloat(row['用量'] || 0) || 0,
                    unit: String(row['单位'] || '个').trim(),
                    item_type: String(row['类型'] || 'raw_material').trim(),
                    base_qty: parseFloat(row['底数'] || 1) || 1
                });
            });

            processesSheet.forEach(row => {
                const product = String(row['料号'] || row['产品名称'] || row['工单号'] || '').trim();
                if (!product) return;
                if (!processDb[product]) processDb[product] = [];
                processDb[product].push({
                    process_name: String(row['工序名称'] || row['工序名'] || '').trim(),
                    time_per_unit: getTimePerUnit(row),
                    machine_id: String(row['工作中心'] || row['设备编号'] || '').trim() || undefined,
                    setup_time: parseFloat(row['换线时间'] || 30),
                    sequence: parseInt(row['行号'] || row['工序序号'] || 0) || processDb[product].length + 1
                });
            });

            // Sort processes by sequence
            Object.keys(processDb).forEach(product => {
                processDb[product].sort((a, b) => a.sequence - b.sequence);
            });
        }

        const resp = await fetch('/api/scheduling/bom-tree', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                orders: orders,
                config: {
                    machines: dataPackage.machines,
                    teams: dataPackage.teams,
                    materials: dataPackage.materials
                },
                params: {
                    mode: 'backward',
                    objective: dataPackage.params.objective,
                    iterations: dataPackage.params.iterations,
                    transfer_time_hours: transferTime
                },
                bom: bomDb,
                process_db: processDb
            })
        });

        const result = await resp.json();
        if (result.status === 'success') {
            showToast('BOM树排程完成', 'success');
            // Send result to main chat like other modes
            try {
                const resultFile = await uploadSchedulingDataAsFile(result, 'bom_tree_result.json');
                pendingAttachments.push(resultFile);
                const summary = result.summary || {};
                const prompt = `BOM树排程已完成，结果摘要如下：\n\n**排产概况**\n- 工单总数: ${summary.total_orders || 0}\n- 准时交付率: ${summary.on_time_rate || '0%'}\n- 总工期: ${summary.makespan || '-'}\n- 延期工单: ${summary.late_orders || 0}\n- 齐套工单: ${summary.kit_ok_orders || 0}\n- 缺料工单: ${summary.kit_nok_orders || 0}\n\n请基于附件中的完整排产结果，生成详细的排产报告和可视化图表。`;
                await _submitSchedulingPrompt(prompt);
            } catch (submitErr) {
                console.error('[Scheduling] Failed to submit BOM tree result:', submitErr);
                showToast('结果提交失败: ' + submitErr.message, 'error');
            }
        } else {
            showToast('排产失败: ' + (result.message || '未知错误'), 'error');
        }
    } catch (err) {
        showToast('BOM树排程失败: ' + err.message, 'error');
    }
}

async function executeSchedulingFromErp() {
    if (!_schedulingErpData || _schedulingErpData.length === 0) {
        showToast('请先同步ERP数据', 'error');
        return;
    }

    const mode = document.getElementById('scheduling-param-mode').value;
    if (mode === 'bom_tree') {
        await executeBOMTreeSchedulingFromErp();
        return;
    }

    showToast('正在准备排产数据...', 'info');
    try {
        const dataPackage = buildSchedulingDataPackage(_schedulingErpData);
        const fileInfo = await uploadSchedulingDataAsFile(dataPackage, 'scheduling_data.json');
        pendingAttachments.push(fileInfo);
        const prompt = buildSchedulingPrompt(_schedulingSubScene, null, _schedulingErpData, fileInfo);
        _submitSchedulingPrompt(prompt);
    } catch (err) {
        showToast('数据上传失败: ' + err.message, 'error');
    }
}

async function executeBOMTreeSchedulingFromErp() {
    if (!_schedulingErpData || _schedulingErpData.length === 0) {
        showToast('请先同步ERP数据', 'error');
        return;
    }

    showToast('正在执行BOM树排程...', 'info');
    try {
        const dataPackage = buildSchedulingDataPackage(_schedulingErpData);
        const transferTime = parseFloat(document.getElementById('scheduling-param-transfer').value) || 5.0;

        // Build bom_db and process_db from multi-sheet data if available
        let bomDb = {};
        let processDb = {};

        if (_schedulingFileSheets) {
            // Support both old and new template sheet names
            const bomsSheet = _schedulingFileSheets['BOM结构'] || _schedulingFileSheets['BOM物料'] || [];
            const processesSheet = _schedulingFileSheets['工艺路线'] || _schedulingFileSheets['工序路线'] || [];

            bomsSheet.forEach(row => {
                const product = String(row['母件料号'] || row['产品名称'] || row['父件'] || '').trim();
                if (!product) return;
                if (!bomDb[product]) bomDb[product] = [];
                bomDb[product].push({
                    material_code: String(row['料号'] || '').trim(),
                    material_name: String(row['品名'] || row['物料名称'] || '').trim(),
                    quantity: parseFloat(row['用量'] || 0) || 0,
                    unit: String(row['单位'] || '个').trim(),
                    item_type: String(row['类型'] || 'raw_material').trim(),
                    base_qty: parseFloat(row['底数'] || 1) || 1
                });
            });

            processesSheet.forEach(row => {
                const product = String(row['料号'] || row['产品名称'] || row['工单号'] || '').trim();
                if (!product) return;
                if (!processDb[product]) processDb[product] = [];
                processDb[product].push({
                    process_name: String(row['工序名称'] || row['工序名'] || '').trim(),
                    time_per_unit: getTimePerUnit(row),
                    machine_id: String(row['工作中心'] || row['设备编号'] || '').trim() || undefined,
                    setup_time: parseFloat(row['换线时间'] || 30),
                    sequence: parseInt(row['行号'] || row['工序序号'] || 0) || processDb[product].length + 1
                });
            });

            // Sort processes by sequence
            Object.keys(processDb).forEach(product => {
                processDb[product].sort((a, b) => a.sequence - b.sequence);
            });
        }

        const resp = await fetch('/api/scheduling/bom-tree', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                orders: _schedulingErpData,
                config: {
                    machines: dataPackage.machines,
                    teams: dataPackage.teams,
                    materials: dataPackage.materials
                },
                params: {
                    mode: 'backward',
                    objective: dataPackage.params.objective,
                    iterations: dataPackage.params.iterations,
                    transfer_time_hours: transferTime
                },
                bom: bomDb,
                process_db: processDb
            })
        });

        const result = await resp.json();
        if (result.status === 'success') {
            showToast('BOM树排程完成', 'success');
            // Send result to main chat like other modes
            try {
                const resultFile = await uploadSchedulingDataAsFile(result, 'bom_tree_result.json');
                pendingAttachments.push(resultFile);
                const summary = result.summary || {};
                const prompt = `BOM树排程已完成，结果摘要如下：\n\n**排产概况**\n- 工单总数: ${summary.total_orders || 0}\n- 准时交付率: ${summary.on_time_rate || '0%'}\n- 总工期: ${summary.makespan || '-'}\n- 延期工单: ${summary.late_orders || 0}\n- 齐套工单: ${summary.kit_ok_orders || 0}\n- 缺料工单: ${summary.kit_nok_orders || 0}\n\n请基于附件中的完整排产结果，生成详细的排产报告和可视化图表。`;
                await _submitSchedulingPrompt(prompt);
            } catch (submitErr) {
                console.error('[Scheduling] Failed to submit BOM tree result:', submitErr);
                showToast('结果提交失败: ' + submitErr.message, 'error');
            }
        } else {
            showToast('排产失败: ' + (result.message || '未知错误'), 'error');
        }
    } catch (err) {
        showToast('BOM树排程失败: ' + err.message, 'error');
    }
}

function showSchedulingResult(data) {
    try {
        const mainContent = document.getElementById('scheduling-wb-main-content');
        const resultArea = document.getElementById('scheduling-wb-result-area');

        if (!mainContent || !resultArea) {
            console.error('[Scheduling] Result elements not found');
            return;
        }

        mainContent.classList.add('hidden');
        resultArea.classList.remove('hidden');
        console.log('[Scheduling] Showing result area');

        // Store result globally
        _schedulingResult = data;

        // Summary
        const summary = data.summary || {};
        const summaryEl = document.getElementById('scheduling-result-summary');
        if (summaryEl) {
            summaryEl.innerHTML = `
                <div class="grid grid-cols-6 gap-4">
                    <div class="text-center">
                        <div class="text-lg font-bold text-primary-500">${summary.total_orders || 0}</div>
                        <div class="text-[10px] text-slate-400">工单总数</div>
                    </div>
                    <div class="text-center">
                        <div class="text-lg font-bold text-emerald-500">${summary.on_time_rate || '0%'}</div>
                        <div class="text-[10px] text-slate-400">准时交付率</div>
                    </div>
                    <div class="text-center">
                        <div class="text-lg font-bold text-amber-500">${summary.makespan || '-'}</div>
                        <div class="text-[10px] text-slate-400">总工期</div>
                    </div>
                    <div class="text-center">
                        <div class="text-lg font-bold text-red-500">${summary.late_orders || 0}</div>
                        <div class="text-[10px] text-slate-400">延期工单</div>
                    </div>
                    <div class="text-center">
                        <div class="text-lg font-bold text-purple-500">${summary.kit_ok_orders || 0}</div>
                        <div class="text-[10px] text-slate-400">齐套工单</div>
                    </div>
                    <div class="text-center">
                        <div class="text-lg font-bold text-orange-500">${summary.kit_nok_orders || 0}</div>
                        <div class="text-[10px] text-slate-400">缺料工单</div>
                    </div>
                </div>
            `;
        }

        // Render unified gantt HTML (single HTML with machine/order views + detail table)
        const html = data.gantt_html || (data.views && data.views.gantt) || null;
        const container = document.getElementById('scheduling-view-gantt');
        console.log('[Scheduling] Rendering unified gantt, container found:', !!container, 'html length:', html ? html.length : 0);
        if (container) {
            if (html) {
                // Use iframe for full HTML documents to preserve <head> scripts/styles
                if (typeof html === 'string' && html.includes('<html')) {
                    container.innerHTML = '<iframe style="width:100%;height:600px;border:none;"></iframe>';
                    const iframe = container.querySelector('iframe');
                    if (iframe) {
                        if ('srcdoc' in iframe) {
                            iframe.srcdoc = html;
                            console.log('[Scheduling] iframe loaded via srcdoc');
                        } else {
                            // Fallback for very old browsers
                            const doc = iframe.contentDocument || iframe.contentWindow.document;
                            doc.open();
                            doc.write(html);
                            doc.close();
                            console.log('[Scheduling] iframe loaded via document.write');
                        }
                    }
                } else {
                    container.innerHTML = html;
                }
            } else {
                container.innerHTML = '<div class="p-8 text-center text-slate-400 text-sm">暂无数据</div>';
            }
        }

        // Alerts
        const alertsContainer = document.getElementById('scheduling-result-alerts');
        const alerts = data.alerts || [];
        if (alertsContainer) {
            if (alerts.length > 0) {
                alertsContainer.innerHTML = alerts.map(alert => `
                    <div class="flex items-start gap-2 p-3 rounded-lg ${alert.level === 'error' ? 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300' : alert.level === 'warning' ? 'bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-300' : 'bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300'}">
                        <i class="fas ${alert.level === 'error' ? 'fa-circle-exclamation' : alert.level === 'warning' ? 'fa-triangle-exclamation' : 'fa-circle-info'} mt-0.5"></i>
                        <div class="text-xs leading-relaxed">${escapeHtml(alert.message)}</div>
                    </div>
                `).join('');
            } else {
                alertsContainer.innerHTML = '';
            }
        }
    } catch (err) {
        console.error('[Scheduling] showSchedulingResult error:', err);
        showToast('结果显示失败: ' + err.message, 'error');
    }
}

function switchSchedulingView(viewName) {
    // Hide all contents
    document.querySelectorAll('.scheduling-view-content').forEach(el => el.classList.add('hidden'));
    // Reset all tabs
    document.querySelectorAll('.scheduling-view-tab').forEach(el => {
        el.classList.remove('bg-primary-500', 'text-white');
        el.classList.add('bg-slate-100', 'dark:bg-slate-800', 'text-slate-600', 'dark:text-slate-300');
    });

    // Show selected content
    const content = document.getElementById('scheduling-view-' + viewName);
    if (content) content.classList.remove('hidden');

    // Highlight selected tab
    const tab = document.getElementById('scheduling-view-tab-' + viewName);
    if (tab) {
        tab.classList.remove('bg-slate-100', 'dark:bg-slate-800', 'text-slate-600', 'dark:text-slate-300');
        tab.classList.add('bg-primary-500', 'text-white');
    }
}

function closeSchedulingResult() {
    document.getElementById('scheduling-wb-result-area').classList.add('hidden');
    document.getElementById('scheduling-wb-main-content').classList.remove('hidden');
    _schedulingResult = null;
}

function saveSchedulingResult() {
    if (!_schedulingResult || !_schedulingResult.schedule_id) {
        showToast('没有可保存的结果', 'error');
        return;
    }
    showToast('排产结果已保存', 'success');
}

// =====================================================================
// File Upload
// =====================================================================
function setupSchedulingFileUpload() {
    const dropZone = document.getElementById('scheduling-drop-zone');
    const fileInput = document.getElementById('scheduling-file-input');
    if (!dropZone || !fileInput) return;

    dropZone.onclick = () => fileInput.click();

    dropZone.ondragover = (e) => {
        e.preventDefault();
        dropZone.classList.add('border-primary-400', 'dark:border-primary-500');
    };
    dropZone.ondragleave = () => {
        dropZone.classList.remove('border-primary-400', 'dark:border-primary-500');
    };
    dropZone.ondrop = (e) => {
        e.preventDefault();
        dropZone.classList.remove('border-primary-400', 'dark:border-primary-500');
        const files = e.dataTransfer.files;
        if (files.length > 0) {
            handleSchedulingFile(files[0]);
        }
    };

    fileInput.onchange = () => {
        if (fileInput.files.length > 0) {
            handleSchedulingFile(fileInput.files[0]);
        }
    };
}

function handleSchedulingFile(file) {
    const info = document.getElementById('scheduling-file-info');
    const nameEl = document.getElementById('scheduling-file-name');
    const sizeEl = document.getElementById('scheduling-file-size');

    nameEl.textContent = file.name;
    sizeEl.textContent = (file.size / 1024).toFixed(1) + ' KB';
    info.classList.remove('hidden');

    const fileName = file.name.toLowerCase();
    if (fileName.endsWith('.csv')) {
        const reader = new FileReader();
        reader.onload = (e) => {
            try {
                const text = e.target.result;
                const rows = parseCSV(text);
                if (rows.length === 0) {
                    showToast('文件为空或格式不正确', 'error');
                    return;
                }
                _schedulingFileData = rows;
                _schedulingFileHeaders = Object.keys(rows[0]);
                renderSchedulingFileMapping();
                renderSchedulingFilePreview();
                document.getElementById('scheduling-file-mapping').classList.remove('hidden');
                document.getElementById('scheduling-file-preview').classList.remove('hidden');
                document.getElementById('scheduling-file-actions').classList.remove('hidden');
            } catch (err) {
                console.error('[Scheduling] File parse error:', err);
                showToast('文件解析失败', 'error');
            }
        };
        reader.readAsText(file);
    } else if (fileName.endsWith('.xlsx') || fileName.endsWith('.xls')) {
        parseExcelFile(file);
    } else {
        showToast('不支持的文件格式，请上传 CSV 或 Excel 文件', 'error');
    }
}

function parseCSV(text) {
    const lines = text.trim().split('\n');
    if (lines.length < 2) return [];
    const headers = lines[0].split(',').map(h => h.trim().replace(/^"|"$/g, ''));
    return lines.slice(1).map(line => {
        const values = line.split(',').map(v => v.trim().replace(/^"|"$/g, ''));
        const row = {};
        headers.forEach((h, i) => { row[h] = values[i] || ''; });
        return row;
    });
}

// Parse Excel file using SheetJS (xlsx library)
function parseExcelFile(file) {
    if (typeof XLSX === 'undefined') {
        showToast('正在加载 Excel 解析库，请稍后再试', 'error');
        return;
    }
    const reader = new FileReader();
    reader.onload = (e) => {
        try {
            const data = new Uint8Array(e.target.result);
            const workbook = XLSX.read(data, { type: 'array' });

            // Parse all sheets
            _schedulingFileSheets = {};
            workbook.SheetNames.forEach(sheetName => {
                const worksheet = workbook.Sheets[sheetName];
                const rows = XLSX.utils.sheet_to_json(worksheet, { header: 1 });
                if (rows.length >= 2) {
                    const headers = rows[0].map(h => String(h || '').trim());
                    const parsedRows = rows.slice(1).map(row => {
                        const obj = {};
                        headers.forEach((h, i) => { obj[h] = row[i] !== undefined ? row[i] : ''; });
                        return obj;
                    });
                    _schedulingFileSheets[sheetName] = parsedRows;
                }
            });

            // Check if multi-sheet format (support both old and new template)
            const hasOrdersSheet = _schedulingFileSheets['生产订单信息'] || _schedulingFileSheets['工单主数据'] || _schedulingFileSheets['排产测试数据'];
            const hasProcessSheet = _schedulingFileSheets['工艺路线'] || _schedulingFileSheets['工序路线'];
            const hasBomSheet = _schedulingFileSheets['BOM结构'] || _schedulingFileSheets['BOM物料'];

            if (hasOrdersSheet) {
                // Multi-sheet format: show preview of orders sheet
                const ordersSheet = _schedulingFileSheets['生产订单信息'] || _schedulingFileSheets['工单主数据'] || _schedulingFileSheets['排产测试数据'];
                _schedulingFileData = ordersSheet;
                _schedulingFileHeaders = Object.keys(ordersSheet[0] || {});

                // Show sheet info in preview
                renderMultiSheetPreview();
                document.getElementById('scheduling-file-mapping').classList.add('hidden');
                document.getElementById('scheduling-file-preview').classList.remove('hidden');
                document.getElementById('scheduling-file-actions').classList.remove('hidden');
                const processSheet = _schedulingFileSheets['工艺路线'] || _schedulingFileSheets['工序路线'];
                const bomSheet = _schedulingFileSheets['BOM结构'] || _schedulingFileSheets['BOM物料'];
                showToast(`已读取多Sheet文件：工单${ordersSheet.length}条${processSheet ? '、工序' + processSheet.length + '条' : ''}${bomSheet ? '、BOM' + bomSheet.length + '条' : ''}`, 'success');
            } else {
                // Single-sheet fallback
                const firstSheetName = workbook.SheetNames[0];
                const rows = XLSX.utils.sheet_to_json(workbook.Sheets[firstSheetName], { header: 1 });
                if (rows.length < 2) {
                    showToast('Excel 文件为空或格式不正确', 'error');
                    return;
                }
                const headers = rows[0].map(h => String(h || '').trim());
                const parsedRows = rows.slice(1).map(row => {
                    const obj = {};
                    headers.forEach((h, i) => { obj[h] = row[i] !== undefined ? row[i] : ''; });
                    return obj;
                });
                _schedulingFileData = parsedRows;
                _schedulingFileHeaders = headers;
                renderSchedulingFileMapping();
                renderSchedulingFilePreview();
                document.getElementById('scheduling-file-mapping').classList.remove('hidden');
                document.getElementById('scheduling-file-preview').classList.remove('hidden');
                document.getElementById('scheduling-file-actions').classList.remove('hidden');
            }
        } catch (err) {
            console.error('[Scheduling] Excel parse error:', err);
            showToast('Excel 文件解析失败', 'error');
        }
    };
    reader.readAsArrayBuffer(file);
}

function renderMultiSheetPreview() {
    const thead = document.getElementById('scheduling-preview-head');
    const tbody = document.getElementById('scheduling-preview-body');
    if (!_schedulingFileData || _schedulingFileData.length === 0) return;

    const headers = Object.keys(_schedulingFileData[0]);
    thead.innerHTML = headers.map(h => `<th class="px-3 py-2 text-left font-medium whitespace-nowrap">${escapeHtml(h)}</th>`).join('');
    tbody.innerHTML = _schedulingFileData.slice(0, 5).map(row => `
        <tr class="border-t border-slate-200 dark:border-white/5">
            ${headers.map(h => `<td class="px-3 py-2 whitespace-nowrap">${escapeHtml(String(row[h] || ''))}</td>`).join('')}
        </tr>
    `).join('');
}

function renderSchedulingFileMapping() {
    const container = document.getElementById('scheduling-file-mapping-list');
    const standardFields = _schedulingSubScene ? (_schedulingSubScene.required_fields || []).concat(_schedulingSubScene.optional_fields || []) : SCHEDULING_STANDARD_FIELDS;

    // Auto-map by name similarity
    _schedulingFileMapping = {};
    standardFields.forEach(stdField => {
        const match = _schedulingFileHeaders.find(h => h === stdField || h.includes(stdField) || stdField.includes(h));
        if (match) _schedulingFileMapping[stdField] = match;
    });

    container.innerHTML = standardFields.map(stdField => {
        const options = ['<option value="">-- 不映射 --</option>']
            .concat(_schedulingFileHeaders.map(h => `<option value="${escapeHtml(h)}" ${h === _schedulingFileMapping[stdField] ? 'selected' : ''}>${escapeHtml(h)}</option>`))
            .join('');
        return `
            <div class="flex items-center gap-3">
                <span class="w-24 text-xs text-slate-600 dark:text-slate-400 text-right">${escapeHtml(stdField)}</span>
                <i class="fas fa-arrow-right text-slate-300 text-[10px]"></i>
                <select onchange="updateSchedulingMapping('${escapeHtml(stdField)}', this.value)" class="flex-1 px-2 py-1 rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-xs text-slate-700 dark:text-slate-200">
                    ${options}
                </select>
            </div>
        `;
    }).join('');
}

function updateSchedulingMapping(stdField, fileField) {
    if (fileField) {
        _schedulingFileMapping[stdField] = fileField;
    } else {
        delete _schedulingFileMapping[stdField];
    }
}

function renderSchedulingFilePreview() {
    const thead = document.getElementById('scheduling-preview-head');
    const tbody = document.getElementById('scheduling-preview-body');
    if (!_schedulingFileData || _schedulingFileData.length === 0) return;

    const headers = Object.keys(_schedulingFileData[0]);
    thead.innerHTML = headers.map(h => `<th class="px-3 py-2 text-left font-medium whitespace-nowrap">${escapeHtml(h)}</th>`).join('');
    tbody.innerHTML = _schedulingFileData.slice(0, 5).map(row => `
        <tr class="border-t border-slate-200 dark:border-white/5">
            ${headers.map(h => `<td class="px-3 py-2 whitespace-nowrap">${escapeHtml(String(row[h] || ''))}</td>`).join('')}
        </tr>
    `).join('');
}

function clearSchedulingFile() {
    _schedulingFileData = null;
    _schedulingFileHeaders = [];
    _schedulingFileMapping = {};
    _schedulingFileSheets = {};
    document.getElementById('scheduling-file-input').value = '';
    document.getElementById('scheduling-file-info').classList.add('hidden');
    document.getElementById('scheduling-file-mapping').classList.add('hidden');
    document.getElementById('scheduling-file-preview').classList.add('hidden');
    document.getElementById('scheduling-file-actions').classList.add('hidden');
}

// =====================================================================
// ERP Integration
// =====================================================================
function onSchedulingErpSystemChange() {
    const system = document.getElementById('scheduling-erp-system').value;
    const configFields = document.getElementById('scheduling-erp-config-fields');
    const kingdeeFields = document.getElementById('scheduling-erp-kingdee-fields');

    if (system) {
        configFields.classList.remove('hidden');
        if (system === 'kingdee') {
            kingdeeFields.classList.remove('hidden');
        } else {
            kingdeeFields.classList.add('hidden');
        }
    } else {
        configFields.classList.add('hidden');
    }
}

function testSchedulingErpConnection() {
    showToast('ERP连接测试功能开发中', 'info');
}

function syncSchedulingErpData() {
    showToast('ERP数据同步功能开发中', 'info');
}

function clearSchedulingErpData() {
    _schedulingErpData = null;
    document.getElementById('scheduling-erp-preview-area').classList.add('hidden');
    document.getElementById('scheduling-erp-sync-btn').classList.add('hidden');
}

// =====================================================================
// History
// =====================================================================
function toggleSchedulingHistory() {
    _schedulingHistoryVisible = !_schedulingHistoryVisible;
    const panel = document.getElementById('scheduling-wb-history-panel');
    const main = document.getElementById('scheduling-wb-main-content');

    if (_schedulingHistoryVisible) {
        panel.classList.remove('hidden');
        main.classList.add('hidden');
        loadSchedulingHistory();
    } else {
        panel.classList.add('hidden');
        main.classList.remove('hidden');
    }
}

function loadSchedulingHistory() {
    const container = document.getElementById('scheduling-wb-history-list');
    fetch('/api/scheduling/history')
        .then(r => r.json())
        .then(data => {
            if (data.status === 'success' && data.records && data.records.length > 0) {
                container.innerHTML = data.records.map(rec => `
                    <div class="p-3 bg-slate-50 dark:bg-slate-800/50 rounded-lg cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors" onclick="viewSchedulingHistory('${rec.id}')">
                        <div class="flex items-center justify-between">
                            <span class="text-xs font-semibold text-slate-700 dark:text-slate-200">${escapeHtml(rec.name || rec.id)}</span>
                            <span class="text-[10px] text-slate-400">${escapeHtml(rec.created_at || '')}</span>
                        </div>
                        <div class="text-[10px] text-slate-500 dark:text-slate-400 mt-1">${escapeHtml(rec.summary || '')}</div>
                    </div>
                `).join('');
            } else {
                container.innerHTML = '<div class="text-center text-xs text-slate-400 py-8">暂无历史记录</div>';
            }
        })
        .catch(() => {
            container.innerHTML = '<div class="text-center text-xs text-slate-400 py-8">加载失败</div>';
        });
}

function viewSchedulingHistory(id) {
    fetch('/api/scheduling/history/' + id)
        .then(r => r.json())
        .then(data => {
            if (data.status === 'success' && data.record) {
                _schedulingResult = data.record;
                showSchedulingResult(data.record);
                toggleSchedulingHistory();
            } else {
                showToast('加载历史记录失败', 'error');
            }
        })
        .catch(() => showToast('加载历史记录失败', 'error'));
}

function downloadSchedulingTemplate() {
    fetch('/assets/scheduling_template.xlsx')
        .then(r => {
            if (!r.ok) throw new Error('下载失败');
            return r.blob();
        })
        .then(blob => {
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = '排产导入模板.xlsx';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
        })
        .catch(() => showToast('模板下载失败', 'error'));
}

function downloadSchedulingTestData() {
    fetch('/assets/scheduling_test_data.xlsx')
        .then(r => {
            if (!r.ok) throw new Error('下载失败');
            return r.blob();
        })
        .then(blob => {
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = '排产测试数据.xlsx';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
        })
        .catch(() => showToast('测试数据下载失败', 'error'));
}
