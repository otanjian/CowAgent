// ERP connection picker (read-only).
//
// Connection *management* moved to the console page (change
// add-external-system-access, task 6.5). This module used to carry a second
// writable form -- its own field mapping, its own keep/replace/clear password
// rules and its own full-table POST -- against the same service the console page
// already writes through. Two implementations of one contract drift, and the
// drift here is on a credential, so the write half is gone.
//
// What is left is what the scenes actually need: the non-secret connection list
// a workbench picker offers, and a connection lookup by id. The server is the
// source of truth: GET returns the tenant's connections, and a password is
// never returned at all -- not as a value, not as a mask.

let erpConnectionsData = [];
let erpConnectionsLoaded = false;
let erpCatalogRevision = null;

//: The console page that owns connection management. Kept as an address rather
//: than a function call so a scene opened outside the app shell (a direct
//: ``/scene-assets/...`` link) still has somewhere to send the user.
const ERP_MANAGE_VIEW = 'external_connections';

function loadErpConnectionConfig() {
    if (erpConnectionsLoaded) return;
    fetch('/api/erp/connections')
        .then(r => r.json())
        .then(data => {
            if (data.status === 'success') {
                erpConnectionsData = data.connections || [];
                if (!Array.isArray(erpConnectionsData)) erpConnectionsData = [];
                erpCatalogRevision = (data.catalog_revision === undefined) ? null : data.catalog_revision;
            }
            erpConnectionsLoaded = true;
            renderErpConnectionList();
        })
        .catch(() => { erpConnectionsLoaded = false; });
}

function getErpConnectionById(id) {
    return erpConnectionsData.find(c => c.id === id);
}

//: Open the console page that manages connections.
//:
//: ``navigateTo`` is the app shell's own dispatch, so the move goes through the
//: same availability gate as a click in the sidebar -- a page this identity may
//: not read renders that page's denial rather than being bypassed by a scene.
//: The hash fallback covers a scene rendered without the shell (a standalone
//: asset preview), where the deep link still resolves.
function openErpConnectionConsole() {
    if (typeof window.navigateTo === 'function') {
        window.navigateTo(ERP_MANAGE_VIEW);
        return;
    }
    window.location.hash = '#view-' + ERP_MANAGE_VIEW.replace(/_/g, '-');
}

function renderErpConnectionList() {
    const listEl = document.getElementById('erp-connection-list');
    const emptyEl = document.getElementById('erp-connection-empty-state');
    if (!listEl || !emptyEl) return;
    if (erpConnectionsData.length === 0) {
        listEl.innerHTML = '';
        emptyEl.classList.remove('hidden');
        return;
    }
    emptyEl.classList.add('hidden');
    listEl.innerHTML = erpConnectionsData.map(conn => {
        const systemLabel = { sap: 'SAP' }[conn.system] || conn.system;
        const providerLabel = { rfc: 'RFC/BAPI', adt_sql: 'ADT/HTTP' }[conn.provider] || conn.provider;
        const detail = conn.provider === 'rfc'
            ? `${conn.ashost || ''}:${conn.sysnr || ''}`
            : (conn.base_url || '');
        const disabledBadge = conn.enabled === false
            ? '<span class="text-[10px] px-1.5 py-0.5 rounded-full bg-slate-200 dark:bg-white/10 text-slate-500" data-i18n="config_erp_disabled">已停用</span>'
            : '';
        return `
        <div class="flex items-center justify-between p-3 rounded-lg border border-slate-200 dark:border-white/10 bg-white dark:bg-[#1A1A1A] transition-colors">
            <div class="flex items-center gap-3 min-w-0">
                <div class="w-8 h-8 rounded-lg flex items-center justify-center bg-primary-500/10 text-primary-500 shrink-0">
                    <i class="fas fa-server text-xs"></i>
                </div>
                <div class="min-w-0">
                    <div class="flex items-center gap-2">
                        <span class="text-sm font-medium text-slate-800 dark:text-slate-100">${escapeHtml(conn.name || '未命名')}</span>
                        <span class="text-[10px] px-1.5 py-0.5 rounded-full bg-primary-500/10 text-primary-500">${escapeHtml(systemLabel)}</span>
                        <span class="text-[10px] px-1.5 py-0.5 rounded-full bg-slate-100 dark:bg-white/10 text-slate-500">${escapeHtml(providerLabel)}</span>
                        ${disabledBadge}
                    </div>
                    <div class="mt-0.5 text-xs text-slate-500 truncate">${escapeHtml(detail)}</div>
                </div>
            </div>
            <div class="flex items-center gap-1 shrink-0 ml-2">
                ${conn.is_default
                    ? `<span title="默认连接" class="w-7 h-7 rounded-md flex items-center justify-center text-amber-500 bg-amber-500/10">
                        <i class="fas fa-star text-xs"></i>
                    </span>`
                    : ''}
                <button onclick="testErpConnection('${conn.id}')" title="测试连接" class="w-7 h-7 rounded-md flex items-center justify-center text-slate-400 hover:text-green-500 hover:bg-green-500/10 cursor-pointer transition-colors">
                    <i class="fas fa-plug text-xs"></i>
                </button>
            </div>
        </div>`;
    }).join('');
}

function testErpConnection(id) {
    const conn = getErpConnectionById(id);
    if (!conn) return;
    showToast('正在测试连接...');
    // Only the connection id crosses the wire; the server resolves the secret.
    fetch('/api/procurement/erp-sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            action: 'test',
            connection_id: conn.id,
            system: conn.system || 'sap',
            provider: conn.provider
        })
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            showToast('连接成功: ' + (data.system || data.message || 'OK'));
        } else {
            showToast('连接失败: ' + (data.message || '未知错误'), 'error');
        }
    })
    .catch(e => showToast('连接失败: ' + e.message, 'error'));
}
