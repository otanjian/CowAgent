function _addSceneGreetingBubble(content, sceneName) {
    const ws = document.getElementById('welcome-screen');
    if (ws) ws.remove();
    const timestamp = new Date();
    const el = document.createElement('div');
    el.className = 'flex gap-3 px-4 sm:px-6 py-3';
    el.innerHTML = `
        <img src="assets/logo.png" alt="OneAgent" class="h-8 w-auto object-contain rounded-lg flex-shrink-0">
        <div class="min-w-0 flex-1 max-w-[85%]">
            <div class="bg-white dark:bg-[#1A1A1A] border border-slate-200 dark:border-white/10 rounded-2xl px-4 py-3 text-sm leading-relaxed msg-content text-slate-700 dark:text-slate-200">
                <div class="answer-content">${renderMarkdown(content)}</div>
                <div class="bot-audio-slot"></div>
            </div>
            <div class="flex items-center gap-2 mt-1.5">
                <span class="text-xs text-slate-400 dark:text-slate-500">${formatTime(timestamp)}</span>
                <span class="text-[10px] px-1.5 py-0.5 rounded bg-primary-50 dark:bg-primary-900/20 text-primary-600 dark:text-primary-400 font-medium">${escapeHtml(sceneName || '')}</span>
                <button class="copy-msg-btn text-xs text-slate-300 dark:text-slate-600 hover:text-slate-500 dark:hover:text-slate-400 transition-colors cursor-pointer" title="${currentLang === 'zh' ? '复制' : 'Copy'}">
                    <i class="fas fa-copy"></i>
                </button>
                <button class="speak-msg-btn text-xs text-slate-300 dark:text-slate-600 hover:text-slate-500 dark:hover:text-slate-400 transition-colors cursor-pointer" title="${t('speak_msg')}" style="display:none;">
                    <i class="fas fa-volume-up"></i>
                </button>
            </div>
        </div>
    `;
    const answerContent = el.querySelector('.answer-content');
    if (answerContent) answerContent.dataset.rawMd = content;
    const copyBtn = el.querySelector('.copy-msg-btn');
    if (copyBtn) {
        copyBtn.addEventListener('click', () => {
            navigator.clipboard.writeText(content).then(() => {
                const icon = copyBtn.querySelector('i');
                if (icon) {
                    icon.className = 'fas fa-check';
                    setTimeout(() => icon.className = 'fas fa-copy', 1500);
                }
            });
        });
    }
    messagesDiv.appendChild(el);
    scrollChatToBottom(true);
}

