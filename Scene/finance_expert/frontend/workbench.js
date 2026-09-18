let _taxationWorkbenchScene = null;
let _taxationWorkbenchSubScene = null;
let _taxationWorkbenchFormData = {};

function openTaxationWorkbench(scene) {
    _taxationWorkbenchScene = scene;
    _taxationWorkbenchSubScene = null;
    _taxationWorkbenchFormData = {};

    const modal = document.getElementById('taxation-workbench-modal');
    // 移动端：重置「权威依据」抽屉为收起状态
    const wbLeftPanel = modal ? modal.querySelector('.w-80') : null;
    if (wbLeftPanel) wbLeftPanel.classList.remove('open');
    const title = document.getElementById('taxation-wb-title');
    const subtitle = document.getElementById('taxation-wb-subtitle');

    title.textContent = scene.workbench_title || scene.name + '工作台';
    subtitle.textContent = '权威税务咨询，专业政策解读，合规风险防控';

    // Reset UI
    document.getElementById('taxation-wb-subscene-selector').classList.remove('hidden');
    document.getElementById('taxation-wb-form-area').classList.add('hidden');
    document.getElementById('taxation-wb-actions').classList.add('hidden');

    // 清空上次打开时选中功能模块后残留的内容
    const taxationFormArea = document.getElementById('taxation-wb-form-area');
    if (taxationFormArea) taxationFormArea.innerHTML = '';
    const taxationIconBox = document.getElementById('taxation-wb-icon');
    if (taxationIconBox) taxationIconBox.style.background = '#64748b';

    // Render sub-scene cards
    renderTaxationWorkbenchSubScenes(scene);

    modal.classList.remove('hidden');
}

function renderTaxationWorkbenchSubScenes(scene) {
    const grid = document.getElementById('taxation-wb-subscene-grid');
    grid.innerHTML = '';

    (scene.sub_scenes || []).forEach(sub => {
        const card = document.createElement('div');
        card.className = 'bg-white dark:bg-[#1A1A1A] rounded-xl border border-slate-200 dark:border-white/10 p-4 cursor-pointer hover:shadow-md hover:-translate-y-0.5 transition-all duration-200 group';
        card.innerHTML = `
            <div class="flex items-center gap-3 mb-2">
                <div class="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0" style="background:${sub.color || '#64748b'}15">
                    <i class="fas ${sub.icon || 'fa-calculator'}" style="color:${sub.color || '#64748b'}"></i>
                </div>
                <div class="min-w-0">
                    <h5 class="font-semibold text-slate-800 dark:text-slate-100 text-sm">${escapeHtml(sub.name)}</h5>
                </div>
            </div>
            <p class="text-xs text-slate-500 dark:text-slate-400 line-clamp-2">${escapeHtml(sub.description || '')}</p>
        `;
        card.onclick = () => selectTaxationWorkbenchSubScene(sub, card, grid);
        grid.appendChild(card);
    });
}

function selectTaxationWorkbenchSubScene(sub, card, grid) {
    _taxationWorkbenchSubScene = sub;

    // Highlight selected
    grid.querySelectorAll('div').forEach(c => {
        c.classList.remove('ring-2', 'ring-primary-500');
    });
    card.classList.add('ring-2', 'ring-primary-500');

    // Update header
    document.getElementById('taxation-wb-title').textContent = sub.name;
    document.getElementById('taxation-wb-subtitle').textContent = sub.description || '';
    document.getElementById('taxation-wb-icon').style.background = sub.color || '#ef4444';

    // Show form area and actions
    document.getElementById('taxation-wb-form-area').classList.remove('hidden');
    document.getElementById('taxation-wb-actions').classList.remove('hidden');

    // Render form based on sub-scene params
    renderTaxationWorkbenchForm(sub);
}

function renderTaxationWorkbenchForm(sub) {
    const formArea = document.getElementById('taxation-wb-form-area');
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

        input.id = `taxation-param-${param.name}`;
        input.dataset.paramName = param.name;
        input.dataset.required = param.required ? 'true' : 'false';

        // Restore value if exists
        if (_taxationWorkbenchFormData[param.name] !== undefined) {
            input.value = _taxationWorkbenchFormData[param.name];
        }

        // Track changes
        input.addEventListener('change', () => {
            _taxationWorkbenchFormData[param.name] = input.value;
        });
        input.addEventListener('input', () => {
            _taxationWorkbenchFormData[param.name] = input.value;
        });

        fieldWrapper.appendChild(input);
        form.appendChild(fieldWrapper);
    });

    formArea.appendChild(form);

    // Setup submit button
    const submitBtn = document.getElementById('taxation-wb-submit-btn');
    submitBtn.onclick = () => submitTaxationConsultation();
}

function closeTaxationWorkbench() {
    document.getElementById('taxation-workbench-modal').classList.add('hidden');
    _taxationWorkbenchScene = null;
    _taxationWorkbenchSubScene = null;
    _taxationWorkbenchFormData = {};
}

function validateTaxationForm() {
    const params = _taxationWorkbenchSubScene.params || [];
    for (const param of params) {
        if (param.required) {
            const value = _taxationWorkbenchFormData[param.name];
            if (!value || value.trim() === '') {
                showToast(`请填写 ${param.label}`, 'error');
                return false;
            }
        }
    }
    return true;
}

function submitTaxationConsultation() {
    if (!validateTaxationForm()) return;

    const subScene = _taxationWorkbenchSubScene;
    const parentScene = _taxationWorkbenchScene;
    const formData = _taxationWorkbenchFormData;

    // Build prompt based on sub-scene
    let prompt = '';

    switch (subScene.id) {
        case 'tax_consultation':
            prompt = `请解答以下税务问题：\n\n`;
            prompt += `问题：${formData.question || ''}\n`;
            if (formData.taxpayer_type && formData.taxpayer_type !== 'unknown') {
                const typeMap = { general: '一般纳税人', small: '小规模纳税人', individual: '个体工商户' };
                prompt += `纳税人类型：${typeMap[formData.taxpayer_type] || formData.taxpayer_type}\n`;
            }
            if (formData.industry) prompt += `所属行业：${formData.industry}\n`;
            prompt += `\n请严格依据法律法规及国家税务总局官方口径进行解答，要求结论先行、多观点对比、政策依据可追溯。`;
            break;

        case 'policy_interpretation':
            prompt = `请解读以下财税政策：\n\n`;
            prompt += `政策名称/文号：${formData.policy || ''}\n`;
            if (formData.focus && formData.focus !== 'general') {
                const focusMap = { impact: '影响分析', operation: '实操指引', comparison: '新旧对比' };
                prompt += `关注重点：${focusMap[formData.focus] || formData.focus}\n`;
            }
            if (formData.context) prompt += `企业背景：${formData.context}\n`;
            prompt += `\n请分析政策背景、核心内容、适用条件、执行时间，并评估对企业的影响。`;
            break;

        case 'tax_planning':
            prompt = `请设计税务筹划方案：\n\n`;
            prompt += `筹划目标：${formData.goal || ''}\n`;
            if (formData.business_scale) {
                const scaleMap = { small: '小型企业', medium: '中型企业', large: '大型企业', group: '集团企业' };
                prompt += `企业规模：${scaleMap[formData.business_scale] || formData.business_scale}\n`;
            }
            if (formData.current_tax) prompt += `当前税负情况：${formData.current_tax}\n`;
            prompt += `\n请在合法合规前提下设计方案，要求严格基于现行有效税法，量化筹划收益，提示潜在风险。`;
            break;

        case 'risk_diagnosis':
            prompt = `请进行财税合规风险诊断：\n\n`;
            if (formData.risk_area && formData.risk_area !== 'general') {
                const areaMap = { vat: '增值税风险', cit: '企业所得税风险', iit: '个人所得税风险', invoice: '发票管理风险', filing: '申报缴纳风险' };
                prompt += `风险领域：${areaMap[formData.risk_area] || formData.risk_area}\n`;
            } else {
                prompt += `风险领域：全面风险扫描\n`;
            }
            if (formData.business_desc) prompt += `企业业务描述：${formData.business_desc}\n`;
            if (formData.concern) prompt += `具体担忧：${formData.concern}\n`;
            prompt += `\n请系统梳理风险点，评估风险等级（高/中/低），给出整改建议和预防措施。`;
            break;

        case 'filing_guidance':
            prompt = `请指导纳税申报：\n\n`;
            if (formData.tax_type) {
                const taxMap = { vat: '增值税', cit: '企业所得税', iit: '个人所得税', stamp: '印花税', land: '土地增值税', other: '其他' };
                prompt += `申报税种：${taxMap[formData.tax_type] || formData.tax_type}\n`;
            }
            if (formData.period) {
                const periodMap = { monthly: '月报', quarterly: '季报', annual: '年报', periodic: '按次申报' };
                prompt += `申报期：${periodMap[formData.period] || formData.period}\n`;
            }
            if (formData.situation) prompt += `具体情况：${formData.situation}\n`;
            prompt += `\n请说明申报期限和渠道、各栏次填报规则、常见错误提示、所需附报资料。`;
            break;

        case 'preferential_policy':
            prompt = `请梳理税收优惠政策：\n\n`;
            if (formData.preferential_type && formData.preferential_type !== 'general') {
                const prefMap = { small: '小微企业优惠', high_tech: '高新技术企业', rd: '研发费用加计扣除', deduction: '固定资产加速折旧', refund: '即征即退/留抵退税', exemption: '免税/减税' };
                prompt += `优惠类型：${prefMap[formData.preferential_type] || formData.preferential_type}\n`;
            } else {
                prompt += `优惠类型：全面梳理\n`;
            }
            if (formData.enterprise_info) prompt += `企业信息：${formData.enterprise_info}\n`;
            if (formData.current_policy) prompt += `已享受的优惠：${formData.current_policy}\n`;
            prompt += `\n请梳理可适用的优惠政策，包括优惠内容、适用条件、申请流程、留存备查资料，并提醒时效性风险。`;
            break;

        default:
            prompt = `请协助处理以下财税问题：\n\n`;
            Object.keys(formData).forEach(key => {
                if (formData[key]) prompt += `${key}：${formData[key]}\n`;
            });
    }

    // Close workbench
    closeTaxationWorkbench();

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

