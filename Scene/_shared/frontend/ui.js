function toggleWorkbenchIndicators(btn) {
    // 从触发按钮向上找到所在工作台弹层；兜底为通用工作台
    const modal = btn
        ? btn.closest('[id$="-workbench-modal"], #workbench-modal')
        : document.getElementById('workbench-modal');
    const panel = modal ? modal.querySelector('.w-80') : null;
    if (panel) panel.classList.toggle('open');
}

// 应用内文件预览弹层（工作台 DEMO-HTML / 甘特图 / Markdown），默认全屏
function openFilePreview(url, title) {
    const modal = document.getElementById('file-preview-modal');
    const frame = document.getElementById('file-preview-iframe');
    const mdBox = document.getElementById('file-preview-md');
    const titleEl = document.getElementById('file-preview-title');
    const box = document.getElementById('file-preview-box');
    if (!modal || !frame) return;
    if (titleEl && title) titleEl.textContent = title;
    // 默认全屏放大模式
    if (box) box.classList.add('preview-fullscreen');
    const maxBtn = document.getElementById('file-preview-max-btn');
    if (maxBtn) {
        const ic = maxBtn.querySelector('i');
        if (ic) ic.className = 'fas fa-compress text-slate-400';
        maxBtn.title = '还原';
    }
    frame.classList.add('hidden');
    mdBox.classList.add('hidden');
    frame.removeAttribute('src');
    if (/\.md$/i.test((url || '').split('?')[0])) {
        // Markdown 直接渲染
        mdBox.innerHTML = '<div class="p-5 text-sm text-slate-400">加载中...</div>';
        mdBox.classList.remove('hidden');
        fetch(url)
            .then(r => r.text())
            .then(txt => { mdBox.innerHTML = `<div class="p-5">${renderMarkdown(txt)}</div>`; })
            .catch(() => { mdBox.innerHTML = '<div class="p-5 text-sm text-red-400">加载失败</div>'; });
    } else {
        frame.src = url;
        frame.classList.remove('hidden');
    }
    modal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
}
function closeFilePreview() {
    const modal = document.getElementById('file-preview-modal');
    if (modal) modal.classList.add('hidden');
    const frame = document.getElementById('file-preview-iframe');
    if (frame) frame.removeAttribute('src');
    document.body.style.overflow = '';
}

// 文件预览弹层：全屏 / 窗口模式切换
function toggleFilePreviewMaximize(btn) {
    const box = document.getElementById('file-preview-box');
    if (!box) return;
    const isFullscreen = box.classList.contains('preview-fullscreen');
    box.classList.toggle('preview-fullscreen', !isFullscreen);
    const icon = btn ? btn.querySelector('i') : null;
    if (icon) icon.className = isFullscreen ? 'fas fa-expand text-slate-400' : 'fas fa-compress text-slate-400';
    if (btn) btn.title = isFullscreen ? '放大全屏' : '还原';
}

// 场景详情底部抽屉（触屏设备）
const _sceneDetailCache = {}; // renderScenesView 按卡片索引缓存 {scene, color}
