function _buildSceneGreeting(systemPrompt) {
    // Extract the role description from system_prompt and convert to first-person greeting
    // e.g. "你是一位资深财税专家..." -> "我是一位资深财税专家..."
    if (!systemPrompt) return '';
    // Replace "你是" with "我是" at the beginning
    let greeting = systemPrompt.trim();
    greeting = greeting.replace(/^你(是|作为|一位)/, '我$1');
    greeting = greeting.replace(/你(能够|可以|会|擅长)/g, '我$1');
    return greeting;
}

function activateScene(scene) {
    // Create a new chat session FIRST so the new session gets the scene context
    newChat();
    // Activate scene on backend for the new session
    fetch('/api/scenes/activate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scene_id: scene.id, session_id: sessionId }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            showToast(t('scenes_switched').replace('{name}', scene.name), 'success');
            // Store active scene context for this session
            activeSceneContext = scene;
            // Auto-send a greeting message from the AI in the chat
            const greeting = scene.system_prompt
                ? _buildSceneGreeting(scene.system_prompt)
                : t('scene_greeting_default').replace('{name}', scene.name);
            _addSceneGreetingBubble(greeting, scene.name);
        } else {
            showToast(data.message || t('scenes_switch_failed'), 'error');
        }
    })
    .catch(() => {
        showToast(t('scenes_switch_network_error'), 'error');
    });
}

// 保留当前会话的场景激活（/scene 弹层用）：与 activateScene 的区别是不 newChat
function activateSceneKeepSession(scene) {
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
                : t('scene_greeting_default').replace('{name}', scene.name);
            _addSceneGreetingBubble(greeting, scene.name);
        } else {
            showToast(data.message || t('scenes_switch_failed'), 'error');
        }
    })
    .catch(() => {
        showToast(t('scenes_switch_network_error'), 'error');
    });
}
