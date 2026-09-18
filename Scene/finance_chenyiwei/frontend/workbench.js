let _casWorkbenchScene = null;
let _casWorkbenchSubScene = null;
let _casWorkbenchFormData = {};

function openCasWorkbench(scene) {
    _casWorkbenchScene = scene;
    _casWorkbenchSubScene = null;
    _casWorkbenchFormData = {};

    const modal = document.getElementById('cas-workbench-modal');
    // 移动端：重置「权威依据」抽屉为收起状态
    const wbLeftPanel = modal ? modal.querySelector('.w-80') : null;
    if (wbLeftPanel) wbLeftPanel.classList.remove('open');
    const title = document.getElementById('cas-wb-title');
    const subtitle = document.getElementById('cas-wb-subtitle');

    title.textContent = scene.workbench_title || scene.name + '工作台';
    subtitle.textContent = '准则解读、实务判断、审计咨询，24年3000万字答疑经验';

    // Reset UI
    document.getElementById('cas-wb-subscene-selector').classList.remove('hidden');
    document.getElementById('cas-wb-form-area').classList.add('hidden');
    document.getElementById('cas-wb-actions').classList.add('hidden');

    // 清空上次打开时选中功能模块后残留的内容
    const casFormArea = document.getElementById('cas-wb-form-area');
    if (casFormArea) casFormArea.innerHTML = '';
    const casIconBox = document.getElementById('cas-wb-icon');
    if (casIconBox) casIconBox.style.background = '#64748b';

    // Render sub-scene cards
    renderCasWorkbenchSubScenes(scene);

    modal.classList.remove('hidden');
}

function renderCasWorkbenchSubScenes(scene) {
    const grid = document.getElementById('cas-wb-subscene-grid');
    grid.innerHTML = '';

    (scene.sub_scenes || []).forEach(sub => {
        const card = document.createElement('div');
        card.className = 'bg-white dark:bg-[#1A1A1A] rounded-xl border border-slate-200 dark:border-white/10 p-4 cursor-pointer hover:shadow-md hover:-translate-y-0.5 transition-all duration-200 group';
        card.innerHTML = `
            <div class="flex items-center gap-3 mb-2">
                <div class="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0" style="background:${sub.color || '#64748b'}15">
                    <i class="fas ${sub.icon || 'fa-scale-balanced'}" style="color:${sub.color || '#64748b'}"></i>
                </div>
                <div class="min-w-0">
                    <h5 class="font-semibold text-slate-800 dark:text-slate-100 text-sm">${escapeHtml(sub.name)}</h5>
                </div>
            </div>
            <p class="text-xs text-slate-500 dark:text-slate-400 line-clamp-2">${escapeHtml(sub.description || '')}</p>
        `;
        card.onclick = () => selectCasWorkbenchSubScene(sub, card, grid);
        grid.appendChild(card);
    });
}

function selectCasWorkbenchSubScene(sub, card, grid) {
    _casWorkbenchSubScene = sub;

    // Highlight selected
    grid.querySelectorAll('div').forEach(c => {
        c.classList.remove('ring-2', 'ring-primary-500');
    });
    card.classList.add('ring-2', 'ring-primary-500');

    // Update header
    document.getElementById('cas-wb-title').textContent = sub.name;
    document.getElementById('cas-wb-subtitle').textContent = sub.description || '';
    document.getElementById('cas-wb-icon').style.background = sub.color || '#ef4444';

    // Show form area and actions
    document.getElementById('cas-wb-form-area').classList.remove('hidden');
    document.getElementById('cas-wb-actions').classList.remove('hidden');

    // Render form based on sub-scene params
    renderCasWorkbenchForm(sub);
}

function renderCasWorkbenchForm(sub) {
    const formArea = document.getElementById('cas-wb-form-area');
    formArea.innerHTML = '';

    const params = sub.params || [];
    if (params.length === 0) {
        formArea.innerHTML = '<p class="text-sm text-slate-500 dark:text-slate-400">本功能无需额外参数，点击"开始咨询"即可。</p>';
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
            default:
                input = document.createElement('input');
                input.type = 'text';
                input.className = 'w-full px-3 py-2 rounded-lg border border-slate-200 dark:border-white/10 bg-white dark:bg-slate-800 text-sm text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-primary-500';
                input.placeholder = param.placeholder || '';
        }

        input.id = `cas-param-${param.name}`;
        input.dataset.paramName = param.name;
        input.dataset.required = param.required ? 'true' : 'false';

        // Restore value if exists
        if (_casWorkbenchFormData[param.name] !== undefined) {
            input.value = _casWorkbenchFormData[param.name];
        }

        // Track changes
        input.addEventListener('change', () => {
            _casWorkbenchFormData[param.name] = input.value;
        });
        input.addEventListener('input', () => {
            _casWorkbenchFormData[param.name] = input.value;
        });

        fieldWrapper.appendChild(input);
        form.appendChild(fieldWrapper);
    });

    formArea.appendChild(form);

    // Setup submit button
    const submitBtn = document.getElementById('cas-wb-submit-btn');
    submitBtn.onclick = () => submitCasConsultation();
}

function closeCasWorkbench() {
    document.getElementById('cas-workbench-modal').classList.add('hidden');
    _casWorkbenchScene = null;
    _casWorkbenchSubScene = null;
    _casWorkbenchFormData = {};
}

function validateCasForm() {
    const params = _casWorkbenchSubScene.params || [];
    for (const param of params) {
        if (param.required) {
            const value = _casWorkbenchFormData[param.name];
            if (!value || value.trim() === '') {
                showToast(`请填写 ${param.label}`, 'error');
                return false;
            }
        }
    }
    return true;
}

function submitCasConsultation() {
    if (!validateCasForm()) return;

    const subScene = _casWorkbenchSubScene;
    const parentScene = _casWorkbenchScene;
    const formData = _casWorkbenchFormData;

    // Build prompt based on sub-scene
    let prompt = '';

    switch (subScene.id) {
        case 'standard_consultation':
            prompt = `请解答以下会计准则适用问题：\n\n`;
            prompt += `问题：${formData.question || ''}\n`;
            if (formData.standard && formData.standard !== '') {
                const standardMap = { revenue: '收入准则（CAS 14）', lease: '租赁准则（CAS 21）', financial: '金融工具准则（CAS 22/23）', consolidation: '合并报表准则（CAS 33）', other: '其他/不确定' };
                prompt += `涉及准则：${standardMap[formData.standard] || formData.standard}\n`;
            }
            if (formData.context) prompt += `交易背景：${formData.context}\n`;
            prompt += `\n请严格依据《企业会计准则》原文及监管指引进行解答，要求结论先行、引用具体准则文号、给出具体账务处理方案。`;
            break;

        case 'practical_judgment':
            prompt = `请对以下复杂交易进行会计处理判断：\n\n`;
            if (formData.transaction_type && formData.transaction_type !== '') {
                const typeMap = { revenue: '收入确认判断', equity: '权益性交易判断', restructuring: '债务重组处理', merger: '企业合并处理', other: '其他复杂交易' };
                prompt += `交易类型：${typeMap[formData.transaction_type] || formData.transaction_type}\n`;
            }
            prompt += `交易描述：${formData.transaction_desc || ''}\n`;
            if (formData.conditions) prompt += `已知条件：${formData.conditions}\n`;
            prompt += `\n请运用实质重于形式原则进行穿透分析，判断经济实质，识别是否属于权益性交易，给出具体会计分录和监管关注要点。`;
            break;

        case 'consolidation':
            prompt = `请解答以下合并报表问题：\n\n`;
            if (formData.issue_type && formData.issue_type !== '') {
                const issueMap = { scope: '合并范围判断', elimination: '内部交易抵消', minority: '少数股东权益', same_control: '同一控制合并', other: '其他合并问题' };
                prompt += `问题类型：${issueMap[formData.issue_type] || formData.issue_type}\n`;
            }
            if (formData.group_structure) prompt += `集团结构描述：${formData.group_structure}\n`;
            prompt += `\n请依据CAS 33及监管指引，分析控制三要素，给出抵消分录，提示实务常见错误。`;
            break;

        case 'revenue_recognition':
            prompt = `请解答以下收入确认问题：\n\n`;
            if (formData.business_scene && formData.business_scene !== '') {
                const sceneMap = { goods: '销售商品', service: '提供服务', ip: '知识产权许可', construction: '建造合同', other: '其他' };
                prompt += `业务场景：${sceneMap[formData.business_scene] || formData.business_scene}\n`;
            }
            if (formData.contract_terms) prompt += `合同关键条款：${formData.contract_terms}\n`;
            prompt += `\n请以控制权转移为核心判断标准，结合合同条款和商业实质分析，引用《监管规则适用指引——会计类第1号》等规定，给出具体会计处理方案。`;
            break;

        case 'audit_consultation':
            prompt = `请解答以下审计问题：\n\n`;
            if (formData.audit_area && formData.audit_area !== '') {
                const areaMap = { risk: '风险评估', control: '控制测试', substantive: '实质性程序', going_concern: '持续经营', other: '其他' };
                prompt += `审计领域：${areaMap[formData.audit_area] || formData.audit_area}\n`;
            }
            if (formData.entity_info) prompt += `被审计单位情况：${formData.entity_info}\n`;
            prompt += `\n请依据中国注册会计师执业准则，强调职业怀疑和谨慎性，给出可操作的审计程序建议。`;
            break;

        case 'policy_interpretation_cas':
            prompt = `请解读以下会计准则政策：\n\n`;
            prompt += `政策文号/名称：${formData.policy || ''}\n`;
            if (formData.focus && formData.focus !== '') {
                const focusMap = { general: '全面解读', impact: '影响分析', operation: '实操指引', comparison: '新旧对比' };
                prompt += `关注重点：${focusMap[formData.focus] || formData.focus}\n`;
            }
            if (formData.enterprise_bg) prompt += `企业背景：${formData.enterprise_bg}\n`;
            prompt += `\n请准确引用政策文号和生效日期，分析变化点及背景，对比新旧规定差异，给出企业应对建议和实务操作要点。`;
            break;

        default:
            prompt = `请协助处理以下会计准则问题：\n\n`;
            Object.keys(formData).forEach(key => {
                if (formData[key]) prompt += `${key}：${formData[key]}\n`;
            });
    }

    // Close workbench
    closeCasWorkbench();

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
