function openQualityTraceScene(scene) {
    // 创建新会话
    newChat();
    // 显示左侧子场景面板
    const panel = document.getElementById('scene-subpanel');
    if (panel) panel.classList.remove('hidden');
    // 渲染子场景列表
    renderSceneSubpanel(scene);
    // 激活父场景
    fetch('/api/scenes/activate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scene_id: scene.id, session_id: sessionId }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            showToast(t('scenes_switched').replace('{name}', scene.name), 'success');
            activeSceneContext = scene;
            const greeting = scene.system_prompt
                ? _buildSceneGreeting(scene.system_prompt)
                : t('scene_greeting_subpanel').replace('{name}', scene.name);
            _addSceneGreetingBubble(greeting, scene.name);
        } else {
            showToast(data.message || t('scenes_switch_failed'), 'error');
        }
    })
    .catch(() => {
        showToast(t('scenes_switch_network_error'), 'error');
    });
}

// 渲染左侧子场景列表
function renderSceneSubpanel(scene) {
    const list = document.getElementById('scene-subpanel-list');
    if (!list) return;
    const subs = (scene.sub_scenes || []).filter(s => s.visible !== false);
    list.innerHTML = subs.map((sub, idx) => `
        <div class="scene-sub-item group flex items-center gap-2.5 px-3 py-2.5 rounded-lg cursor-pointer
                    border border-transparent hover:border-primary-300 dark:hover:border-primary-500
                    hover:bg-primary-50 dark:hover:bg-primary-900/20 transition-all duration-150"
             data-sub-id="${escapeHtml(sub.id)}"
             data-sub-name="${escapeHtml(sub.name)}"
             data-sub-icon="${escapeHtml(sub.icon || 'fa-robot')}"
             data-sub-color="${escapeHtml(sub.color || '#3b82f6')}"
             data-sub-prompt="${escapeHtml(sub.system_prompt || '')}"
             data-sub-skill="${escapeHtml(sub.skill_name || '')}"
             title="${escapeHtml(sub.description || sub.name)}">
            <div class="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0"
                 style="background:${escapeHtml(sub.color || '#3b82f6')}15;color:${escapeHtml(sub.color || '#3b82f6')}">
                <i class="fas ${escapeHtml(sub.icon || 'fa-robot')} text-xs"></i>
            </div>
            <span class="font-medium text-xs text-slate-700 dark:text-slate-200 truncate">${escapeHtml(sub.name)}</span>
        </div>
    `).join('');

    // 移动端子场景下拉选择（与左侧列表共享同一份子场景数据）
    const select = document.getElementById('scene-subpanel-select');
    if (select) {
        select.innerHTML = '';
        subs.forEach((sub, idx) => {
            const opt = document.createElement('option');
            opt.value = String(idx);
            opt.dataset.subId = sub.id;
            opt.textContent = sub.name;
            if (idx === 0) opt.selected = true;
            select.appendChild(opt);
        });
        select.onchange = () => {
            const sub = subs[parseInt(select.value, 10)];
            if (sub) activateSubScene(sub, scene);
        };
    }

    list.querySelectorAll('.scene-sub-item').forEach(item => {
        item.addEventListener('click', () => {
            const sub = {
                id: item.dataset.subId,
                name: item.dataset.subName,
                icon: item.dataset.subIcon,
                color: item.dataset.subColor,
                system_prompt: item.dataset.subPrompt,
                skill_name: item.dataset.subSkill
            };
            activateSubScene(sub, scene);
        });
    });
}

// 激活子场景（切换 skill 和 system_prompt）
function activateSubScene(sub, parentScene) {
    // 高亮当前选中的子场景
    document.querySelectorAll('#scene-subpanel-list .scene-sub-item').forEach(el => {
        el.classList.remove('bg-primary-50', 'dark:bg-primary-900/20', 'border-primary-300', 'dark:border-primary-500');
    });
    const activeItem = document.querySelector(`#scene-subpanel-list .scene-sub-item[data-sub-id="${sub.id}"]`);
    if (activeItem) activeItem.classList.add('bg-primary-50', 'dark:bg-primary-900/20', 'border-primary-300', 'dark:border-primary-500');

    // 同步移动端子场景下拉的选中项
    const subSelect = document.getElementById('scene-subpanel-select');
    if (subSelect) {
        for (let i = 0; i < subSelect.options.length; i++) {
            if (subSelect.options[i].dataset.subId === sub.id) {
                subSelect.selectedIndex = i;
                break;
            }
        }
    }

    // 激活子场景
    fetch('/api/scenes/activate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            scene_id: sub.id,
            session_id: sessionId,
            scene_context: {
                id: sub.id,
                name: sub.name,
                system_prompt: sub.system_prompt || parentScene.system_prompt,
                skill_name: sub.skill_name || parentScene.skill_name
            }
        }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            showToast(t('scenes_switched_sub').replace('{name}', sub.name), 'success');
            activeSceneContext = sub;
            const greeting = sub.system_prompt
                ? _buildSceneGreeting(sub.system_prompt)
                : t('scene_greeting_barcode').replace('{name}', sub.name);
            _addSceneGreetingBubble(greeting, sub.name);
        } else {
            showToast(data.message || t('scenes_switch_failed'), 'error');
        }
    })
    .catch(() => {
        showToast(t('scenes_switch_network_error'), 'error');
    });
}
