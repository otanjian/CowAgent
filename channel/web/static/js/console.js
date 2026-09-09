/* =====================================================================
   容大AI Console - Main Application Script
   ===================================================================== */

// =====================================================================
// Version — fetched from backend (single source: /VERSION file)
// =====================================================================
const PRODUCT_NAME = '容大AI';
let APP_VERSION = '';

// Startup reads must not stop the UI when browser storage is unavailable.
// Business writes and authentication storage keep their existing behavior.
function readStartupPreference(key) {
    try { return localStorage.getItem(key); } catch (_) { return null; }
}

// Task 3.7 — user/tenant-scoped storage partition for the Agent/session
// selection keys. In database identity mode the keys are namespaced by the
// confirmed user and tenant so a different account or tenant on the same
// browser never restores the wrong context. Legacy mode keeps the original
// keys untouched (no migration, no bulk rewrite). The namespace is only
// computable once the identity and the effective tenant are known, so callers
// that restore these identifiers must wait for authentication + tenant select.
function _cowUserTenantKey(key) {
    if (_identityMode() !== 'database') return key;
    const uid = (_accountState && _accountState.username) ? _accountState.username : '';
    const tid = sessionStorage.getItem('cow_tenant_id') || '';
    if (!uid && !tid) return key;      // not confirmed yet -> legacy fallback
    return `${key}::u=${encodeURIComponent(uid)}::t=${encodeURIComponent(tid)}`;
}

// Read a user/tenant-scoped selection key, falling back to the legacy key when
// the current context is not yet confirmed (database) or in legacy mode.
function readScopedPreference(key) {
    try {
        const scoped = _cowUserTenantKey(key);
        const v = localStorage.getItem(scoped);
        if (v !== null) return v;
        // Database mode: do NOT carry the old unpartitioned value into a new
        // context (spec: 旧未分区的 database 选择不得带入). But before the
        // context is confirmed we may still be reading for the very first time,
        // in which case the legacy key is allowed as the initial value.
        if (_identityMode() === 'database' && _accountState && _accountState.authenticated) {
            return null;
        }
        return localStorage.getItem(key);
    } catch (_) { return null; }
}

function writeScopedPreference(key, value) {
    const scoped = _cowUserTenantKey(key);
    try { localStorage.setItem(scoped, value); } catch (_) {}
}

function removeScopedPreference(key) {
    const scoped = _cowUserTenantKey(key);
    try { localStorage.removeItem(scoped); } catch (_) {}
}

// Sidebar account state
// UI-only identity: never retain the login response (which contains a token).
let _identityModeState = 'unknown';
let _accountState = { phase: 'loading', mode: 'unknown', authRequired: null,
    authenticated: null, username: '', displayName: '', mustChangePassword: false };
let _authEpoch = 0;
let _accountCheckSeq = 0;
let _accountCheckRequest = null;
let _accountWritePending = null;
let _accountIdentityKey = null;
let _accountAppVisible = false;
let _accountEntryRequest = null;
let _pendingTenantPicker = false;
let _accountMenuOpen = false;
let _forcedPassword = false;   // must_change_password: block tenant/business until set

function _accountText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
}

function _accountHidden(id, hidden) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle('hidden', hidden);
}

function _emptyAccount(phase) {
    return { phase, mode: _identityModeState, authRequired: null,
        authenticated: null, username: '', displayName: '', mustChangePassword: false };
}

function renderAccountVersion() {
    _accountText('sidebar-version', [effectiveBrandName(), APP_VERSION].filter(Boolean).join(' '));
}

function _renderSidebarAccount() {
    const active = document.activeElement;
    const state = _accountState;
    const hasUser = state.phase !== 'loading' && state.authenticated === true && !!state.username;
    const local = state.phase === 'ready' && state.mode === 'legacy';
    const leaving = state.phase === 'logout_pending';
    const logoutError = state.phase === 'logout_error';
    const canLogout = (state.authRequired === true && state.authenticated === true) || logoutError || leaving;
    let name = t('account_loading'), subtitle = '';
    if (hasUser) {
        name = state.displayName || state.username;
        subtitle = '@' + state.username;
        // Database identity: prefer the *member* display name for the
        // currently-selected tenant (per the "edit member" field) over the
        // account-level display name. Falls back to the account name when the
        // member projection is not yet loaded or the account has no active
        // membership in the selected tenant.
        if (state.mode === 'database' && _baseAccountSelf()) {
            const memberName = _currentMemberDisplayName();
            if (memberName) name = memberName;
        }
    } else if (local) {
        name = t('account_local');
        subtitle = t(state.authRequired ? 'account_password_mode' : 'account_public_mode');
    } else if (leaving || logoutError) {
        name = t(leaving ? 'account_logging_out' : 'account_logout_unconfirmed');
        subtitle = logoutError ? t('account_retry_hint') : '';
    } else if (state.phase === 'error') {
        name = t('account_unavailable');
        subtitle = t('account_retry_hint');
    } else if (state.phase === 'unauthenticated') {
        name = t('account_login');
    }
    _accountText('sidebar-account-name', name);
    _accountText('sidebar-account-subtitle', subtitle);
    const trigger = document.getElementById('sidebar-account-toggle');
    if (trigger) trigger.title = [name, subtitle].filter(Boolean).join('\n');
    _accountText('sidebar-account-avatar', hasUser ? Array.from(name.trim())[0] || '' : '');
    _accountText('account-menu-avatar', hasUser ? Array.from(name.trim())[0] || '' : '');
    _accountHidden('sidebar-account-avatar', !hasUser);
    _accountHidden('sidebar-account-avatar-icon', hasUser);
    _accountText('account-menu-name', hasUser ? name : '');
    _accountText('account-menu-username', hasUser ? '@' + state.username : '');
    _accountHidden('account-menu-identity', !hasUser);
    document.getElementById('account-menu-status')?.classList.remove('opacity-0');
    _accountText('account-menu-status', hasUser || local ? '' : name);
    _accountHidden('account-menu-status', hasUser || local || state.phase === 'unauthenticated');
    _accountHidden('account-menu-retry', !['error', 'logout_error', 'loading'].includes(state.phase));
    _accountHidden('account-menu-logout', !canLogout);
    _accountText('account-menu-logout-label', t(leaving ? 'account_logging_out' : logoutError ? 'account_retry_logout' : 'account_logout'));
    _accountHidden('logout-btn-header', !canLogout);
    // Six-item menu (database identity mode, authenticated account). The items
    // are only available to a real database user; legacy/error/logout states
    // hide the whole group.
    const dbUser = hasUser && state.mode === 'database';
    _accountHidden('account-menu-settings', !(dbUser || local));
    ['account-menu-profile', 'account-menu-password', 'account-menu-tenant'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.disabled = !!_accountWritePending;
        _accountHidden(id, !dbUser);
    });
    ['account-menu-prefs', 'account-menu-about'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.disabled = !!_accountWritePending;
        _accountHidden(id, !(dbUser || local));
    });
    ['account-menu-logout', 'logout-btn-header'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.disabled = !!_accountWritePending;
    });
    ['account-menu-retry', 'auth-check-retry'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.disabled = state.phase === 'loading' || !!_accountWritePending;
    });
    if (!_accountAppVisible && state.phase !== 'unauthenticated' && !_pendingTenantPicker) {
        _accountText('login-subtitle', '');
        _accountText('auth-check-message', t(state.phase === 'error' ? 'account_unavailable' : 'account_loading'));
        _accountHidden('auth-check-retry', state.phase !== 'error');
    } else if (!_accountAppVisible) {
        _accountText('login-subtitle', t('account_login_hint'));
        _accountText('login-btn', t(_pendingTenantPicker ? 'login_enter_tenant' : 'account_login'));
    }
    renderAccountVersion();
    // A retry may disappear once data arrives. Keep focus inside the open
    // popover, instead of losing it to the page or focusing the chat input.
    if (_accountMenuOpen && active && ['account-menu-retry', 'account-menu-logout'].includes(active.id)
            && (active.disabled || active.classList.contains('hidden'))) {
        document.getElementById('sidebar-version')?.focus();
    }
}

function _accountMenuOutside(event) {
    if (!document.getElementById('sidebar-account-footer')?.contains(event.target)) closeAccountMenu();
}

function _accountMenuKey(event) {
    if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        closeAccountMenu(true);
    }
}

function closeAccountMenu(returnFocus = false) {
    if (!_accountMenuOpen) return;
    _accountMenuOpen = false;
    _accountHidden('sidebar-account-menu', true);
    document.getElementById('sidebar-account-toggle')?.setAttribute('aria-expanded', 'false');
    document.removeEventListener('pointerdown', _accountMenuOutside, true);
    document.removeEventListener('focusin', _accountMenuOutside);
    document.removeEventListener('keydown', _accountMenuKey, true);
    if (returnFocus && _accountAppVisible) document.getElementById('sidebar-account-toggle')?.focus();
}

function toggleAccountMenu() {
    if (_accountMenuOpen) { closeAccountMenu(); return; }
    if (!_accountAppVisible) return;
    const menu = document.getElementById('sidebar-account-menu');
    if (!menu) return;
    ['lang-menu', 'tenant-menu'].forEach(id => _accountHidden(id, true));
    _renderSidebarAccount();
    _accountMenuOpen = true;
    _accountHidden('sidebar-account-menu', false);
    document.getElementById('sidebar-account-toggle')?.setAttribute('aria-expanded', 'true');
    const footer = document.getElementById('sidebar-account-footer');
    if (footer && menu.style) menu.style.maxHeight = Math.max(0, footer.getBoundingClientRect().top - 12) + 'px';
    document.addEventListener('pointerdown', _accountMenuOutside, true);
    document.addEventListener('focusin', _accountMenuOutside);
    document.addEventListener('keydown', _accountMenuKey, true);
    const first = Array.from(menu.querySelectorAll('button, a')).find(el => !el.disabled && !el.classList.contains('hidden'));
    if (first) first.focus();
}

function _clearTenantPicker() {
    _pendingTenantPicker = false;
    const btn = document.getElementById('login-btn');
    if (btn) { btn.onclick = null; btn.type = 'submit'; }
    const select = document.getElementById('login-tenant-select');
    if (select) select.replaceChildren();
    _accountHidden('login-tenant-group', true);
    const form = document.getElementById('login-form');
    if (form) form.onsubmit = _submitAccountLogin;
}

function _invalidateAccountIdentity(phase) {
    ++_authEpoch;
    ++_accountCheckSeq;
    _accountCheckRequest = null;
    _accountIdentityKey = null;
    _accountEntryRequest = null;
    _accountState = _emptyAccount(phase);
    if (_forcedPassword) _closeForcedPasswordModal();
    _clearTenantPicker();
    closeAccountMenu();
}

function _normalizeAccountCheck(data) {
    if (!data || typeof data !== 'object' || Array.isArray(data) || data.status !== 'success'
            || typeof data.auth_required !== 'boolean') throw new Error('Invalid authentication response');
    const mode = data.identity_mode === undefined ? 'legacy' : data.identity_mode;
    if (!['legacy', 'database'].includes(mode)
            || (data.identity_mode === undefined && _identityModeState === 'database')
            || (mode === 'database' && !data.auth_required)
            || (data.auth_required && typeof data.authenticated !== 'boolean')) {
        throw new Error('Invalid authentication mode');
    }
    const authenticated = data.auth_required ? data.authenticated : false;
    const user = mode === 'database' && authenticated && data.user;
    const username = user && typeof user.username === 'string' && user.username.trim() ? user.username : '';
    const displayName = user && typeof user.display_name === 'string' && user.display_name.trim() ? user.display_name : '';
    const mustChangePassword = mode === 'database' && authenticated
        ? Boolean(data.must_change_password) : false;
    return { mode, authRequired: data.auth_required, authenticated, username, displayName,
        mustChangePassword,
        phase: data.auth_required && !authenticated ? 'unauthenticated'
            : mode === 'database' && !username ? 'error' : 'ready' };
}

function _acceptAccountIdentity(next, newLogin = false) {
    const previous = _accountIdentityKey;
    if (!previous || newLogin || previous.mode !== next.mode || previous.authRequired !== next.authRequired
            || (previous.username && next.username && previous.username !== next.username)) ++_authEpoch;
    // A missing profile is not a new session: in-flight current-session 401s
    // must still take effect after a profile-only retry.
    _accountIdentityKey = { mode: next.mode, authRequired: next.authRequired,
        username: next.username || previous?.username || '' };
    _identityModeState = next.mode;
    _accountState = next;
    _renderSidebarAccount();
}

function _showAccountCheckGate() {
    _accountHidden('login-overlay', false);
    _accountHidden('app', true);
    _accountHidden('login-form', true);
    _accountHidden('auth-check-panel', false);
    _renderSidebarAccount();
}

function _enterAccountApp() {
    if (_accountAppVisible) return Promise.resolve();
    if (_accountEntryRequest) return _accountEntryRequest;
    const epoch = _authEpoch;
    const current = () => epoch === _authEpoch && !_forcedPassword;
    _showAccountCheckGate();
    const request = Promise.resolve()
        // Authentication has established the mode before a tenant switch is
        // resolved. No business request may run until membership is confirmed.
        .then(() => current() ? _resolveOneShotTenantSwitch() : false)
        .then(() => current() ? _ensureTenantSelected() : false)
        .then(ready => {
            if (!current() || ready === false) return;
            // A platform administrator with no active tenant membership is a
            // pending-assignment account: per the member-tenant continuity rule
            // it must NOT enter the workbench or a normal management page, and
            // must NOT initialize any tenant consumer. Show the restricted
            // recovery gate ("待分配说明 + 重试") instead of the old no-tenant
            // platform direct branch. The administrator completes assignment
            // and retries; the server independently denies normal APIs.
            if (ready === 'platform') {
                _accountState = { ..._accountState, phase: 'error' };
                _showAccountCheckGate();
                _accountText('auth-check-message', t('account_assign_pending'));
                _accountHidden('auth-check-retry', false);
                _accountHidden('login-form', true);
                return;
            }
            // Platform administration does not require tenant membership.
            // Keep its navigation available without starting tenant consumers.
            return Promise.resolve(initApp()).then(() => {
                if (!current()) return;
                _accountHidden('login-overlay', true);
                _accountHidden('auth-check-panel', true);
                _accountHidden('app', false);
                // Reflect the validated layout-only navigation presentation
                // switch (classic|split) on the app root. This is a CSS/layout
                // hook only; it never changes authorization or consumer state.
                const appEl = document.getElementById('app');
                if (appEl) appEl.setAttribute('data-nav-mode', _navigationMode());
                _applyNavAreaAttribute();
                _accountAppVisible = true;
                _renderSidebarAccount();
                // Gate permission-sensitive sidebar entries (platform/audit)
                // once the self profile AND the current tenant's authoritative
                // capability projection are known. The tenant admin qualification
                // and per-page availability now come from /auth/context, not from
                // a client-side role array.
                fetchAccountSelf().then(function (self) {
                    if (!current()) return;
                    _applySidebarPermissions(self);
                    // The member-level display name arrives with /auth/me. Refresh
                    // the sidebar account label once so it can swap from the
                    // account-level name to the current tenant's member name.
                    _renderSidebarAccount();
                    return _fetchTenantAuthorization();
                }).then(function (ctx) {
                    if (!current()) return;
                    // Re-apply with the authoritative projection when it arrives.
                    _applySidebarPermissions(_baseAccountSelf());
                    if (ctx) _applySidebarPermissions(_baseAccountSelf());
                    if (typeof _bootAreaDefaultView === 'function') _bootAreaDefaultView();
                    if (typeof loadSidebarRecentSessions === 'function') loadSidebarRecentSessions();
                });
                if (_identityMode() === 'database') _setupHeaderTenantSelector();
                chatInput.focus();
            });
        })
        .catch(error => {
            if (!current()) return;
            _accountState = { ..._accountState, phase: 'error' };
            _showAccountCheckGate();
            if (error.code === 'no_tenants') {
                _accountText('auth-check-message', t('account_tenant_no_available'));
            }
        })
        .finally(() => {
            if (_accountEntryRequest === request) _accountEntryRequest = null;
        });
    _accountEntryRequest = request;
    return request;
}

// --- forced password change gate ---------------------------------------
// A database account flagged must_change_password must set a new password
// before tenant selection or any tenant-scoped business load. Show the change
// password modal as a full-screen gate; the restricted user may only complete
// the password change or log out. Closing/closing without setting a password is
// disallowed after a forced prompt.
function _enterForcedPassword() {
    _forcedPassword = true;
    _accountHidden('app', true);
    _accountHidden('auth-check-panel', true);
    // Keep the login overlay as a backdrop; the password modal is elevated so
    // it sits above the overlay (network gating remains until password is set).
    _accountHidden('login-overlay', false);
    _renderSidebarAccount();
    _openForcedPasswordModal();
}

function _openForcedPasswordModal() {
    _setAccountPanel('password');
    const modal = document.getElementById('account-password-modal');
    if (modal) {
        modal.classList.remove('hidden');
        // Elevate above the login overlay backdrop so the gate is interactable.
        modal.style.zIndex = '210';
    }
    // Repurpose the modal title/note for the forced flow; restore on close.
    const title = document.getElementById('account-password-title');
    if (title) title.textContent = t('account_password_forced_title');
    const note = document.querySelector('#account-password-modal .account-password-note');
    if (note) note.textContent = t('account_password_forced_note');
    // A restricted account cannot dismiss the gate: hide the close X and change
    // the cancel action to offer logout instead.
    const closeBtn = document.getElementById('account-password-close');
    if (closeBtn) closeBtn.classList.add('hidden');
    const cancelBtn = document.querySelector('#account-password-modal .agent-modal-foot button[type="button"]');
    if (cancelBtn) cancelBtn.textContent = t('account_logout');
    const status = document.getElementById('account-password-status');
    if (status) status.classList.add('hidden');
    // Clear any leftover password input.
    ['ap-old-password', 'ap-new-password', 'ap-confirm-password'].forEach(id => {
        const el = document.getElementById(id);
        if (el) { el.value = ''; delete el.dataset.dirty; }
    });
    focusAccountPanel('account-password-modal');
}

function _closeForcedPasswordModal() {
    _forcedPassword = false;
    // Restore normal modal semantics.
    const title = document.getElementById('account-password-title');
    if (title) title.textContent = t('account_password_title');
    const note = document.querySelector('#account-password-modal .account-password-note');
    if (note) note.textContent = t('account_password_note');
    const closeBtn = document.getElementById('account-password-close');
    if (closeBtn) closeBtn.classList.remove('hidden');
    const cancelBtn = document.querySelector('#account-password-modal .agent-modal-foot button[type="button"]');
    if (cancelBtn) cancelBtn.textContent = t('cancel');
    const modal = document.getElementById('account-password-modal');
    if (modal) modal.style.zIndex = '';
    _accountHidden('account-password-modal', true);
    _setAccountPanel(null);
}

function refreshAccountIdentity() {
    if (_accountWritePending || _pendingTenantPicker) return Promise.resolve();
    if (_accountCheckRequest) return _accountCheckRequest;
    const epoch = _authEpoch, seq = ++_accountCheckSeq;
    const current = () => epoch === _authEpoch && seq === _accountCheckSeq;
    _accountState = _emptyAccount('loading');
    if (!_accountAppVisible) _showAccountCheckGate();
    else _renderSidebarAccount();
    const request = Promise.resolve().then(async () => {
        try {
            // Global identity is independent of the selected tenant. Do not
            // use the tenant-admin request helper or cache a credentials body.
            const response = await fetch('/auth/check', { credentials: 'same-origin', cache: 'no-store' });
            if (!current()) return;
            if (response.status === 401 && _identityModeState !== 'unknown') { showLoginScreen(); return; }
            if (!response.ok) throw new Error('Authentication check failed');
            const data = await response.json();
            if (!current()) return;
            const next = _normalizeAccountCheck(data);
            if (next.phase === 'unauthenticated') {
                _identityModeState = next.mode;
                showLoginScreen();
                return;
            }
            _acceptAccountIdentity(next);
            if (next.mustChangePassword) _enterForcedPassword();
            else if (!_accountAppVisible) _enterAccountApp();
        } catch (_) {
            if (!current()) return;
            _accountState = _emptyAccount('error');
            if (!_accountAppVisible) _showAccountCheckGate();
            else _renderSidebarAccount();
        } finally {
            if (_accountCheckRequest === request) _accountCheckRequest = null;
        }
    });
    _accountCheckRequest = request;
    return request;
}
// End sidebar account state

// Normalize the previous default while an existing backend is still running.
// Instance-specific titles remain intact.
function productTitle(title) {
    if (title && /^cowagent$/i.test(title.trim())) return 'RongAI';
    return !title || /^ai assistant$/i.test(title.trim()) ? PRODUCT_NAME : title;
}

function productTitleHTML(title) {
    const name = productTitle(title);
    return name === PRODUCT_NAME ? '容大<span class="brand-ai">AI</span>' : escapeHtml(name);
}

/* ---- Instance brand (branding) state --------------------------------------
   Single source for the console brand. Populated from /api/branding/public.
   Every painted brand position (sidebar, login, welcome, browser title,
   favicon, default agent avatar) consumes the SAME snapshot so a save updates
   them all without a reload. Falls back to the bundled default brand. */
const DEFAULT_BRAND = {
    enabled: false,
    revision: 0,
    brand_name: '容大AI',
    logo_description: '工作台',
    logo_url: '/assets/rongda-ai-mark.svg',
    favicon_url: '/assets/favicon.ico',
};
let brandState = { ...DEFAULT_BRAND };
let brandLoaded = false;
// Monotonic fetch generation + an explicit "save epoch" so a late read response
// can never overwrite a newer published version shown by a save.
let brandFetchSeq = 0;
let brandSaveEpoch = 0;
// Per-tab guard list used by navigateTo / beforeunload.
let brandingDirty = false;

function publicBrand() {
    return brandState;
}

function isBrandEnabled() {
    return !!brandState.enabled;
}

function effectiveBrandName() {
    return brandState.brand_name || DEFAULT_BRAND.brand_name;
}

function effectiveLogoUrl() {
    return brandState.logo_url || DEFAULT_BRAND.logo_url;
}

function effectiveFaviconUrl() {
    return brandState.favicon_url || DEFAULT_BRAND.favicon_url;
}

function effectiveLogoDescription() {
    return brandState.logo_description || '';
}

function _isDefaultLogoDescription(desc) {
    const value = String(desc || '').trim();
    if (!value) return true;
    // Treat the current default and the legacy caption as built-in defaults so
    // path-based sidebar captions (工作台 / 管理控制台) can replace them.
    return value === DEFAULT_BRAND.logo_description
        || value === '工作台'
        || value === '控制台'
        || value === 'Workbench'
        || value === 'Console';
}

function sidebarBrandCaption(desc) {
    if (!_isDefaultLogoDescription(desc)) return String(desc || '').trim();
    const area = (typeof _navAreaFromPath === 'function')
        ? _navAreaFromPath(location.pathname)
        : 'workbench';
    return area === 'admin' ? t('nav_admin_console') : t('nav_workbench');
}

/* Description allowed on the welcome hero. The built-in default ("工作台") is
   still styled into the sidebar caption and (historically) the login brand
   area, but on the welcome hero it's redundant with the eyebrow
   ("容大AI · 你的工作助手"), so we only surface a customized description. */
function welcomeHeroDescription() {
    const desc = effectiveLogoDescription();
    const isDefault = _isDefaultLogoDescription(desc);
    return (desc && desc.trim() && !isDefault) ? desc : '';
}

/* Render the brand name into a brand-styled wordmark. When the brand name is
   the built-in default we keep the special "容大<span>AI</span>" mark; any
   other name is escaped as plain text. */
function brandWordmarkHTML(name) {
    const value = productTitle(name || DEFAULT_BRAND.brand_name);
    return value === PRODUCT_NAME ? '容大<span class="brand-ai">AI</span>' : escapeHtml(value);
}

/* One-shot image fallback: if a brand logo/favicon fails to load, swap it for
   the built-in default ONCE so a transient asset error doesn't leave an empty
   slot and doesn't loop. Attached at set-src time; `{ once: true }` removes the
   listener after the first failure so a bad network blip is a single swap. */
function _brandArmFallback(img, url) {
    if (!img) return;
    img.dataset.brandFallbackArmed = '1';
    img.src = url;
    img.addEventListener('error', function onErr() {
        img.removeEventListener('error', onErr);
        if (img.dataset.brandFallbackArmed === '1') {
            img.dataset.brandFallbackArmed = '0';
            img.src = '/assets/rongda-ai-mark.svg';
        }
    }, { once: true });
}

/* Apply the brand snapshot to every static DOM position. Re-painted on save,
   on public revalidation and on view entry. idempotent w.r.t. brandState. */
function applyBrandToDocument() {
    renderAccountVersion();
    const name = effectiveBrandName();
    const logoUrl = effectiveLogoUrl();
    const desc = effectiveLogoDescription();
    const logoAlt = (desc || '').trim() || name;

    // Sidebar brand mark + wordmark + caption
    document.querySelectorAll('.sidebar-brand .brand-mark[src]').forEach(img => {
        _brandArmFallback(img, logoUrl);
    });
    const sidebarWordmark = document.getElementById('sidebar-brand-name');
    if (sidebarWordmark) { sidebarWordmark.innerHTML = brandWordmarkHTML(name); sidebarWordmark.title = name; }
    const sidebarCaption = document.getElementById('sidebar-brand-caption');
    if (sidebarCaption) {
        const captionHelper = (typeof window !== 'undefined' && window
            && typeof window.sidebarBrandCaption === 'function')
            ? window.sidebarBrandCaption
            : null;
        const caption = captionHelper
            ? captionHelper(desc)
            : ((desc && desc.trim()) || '');
        const hasDesc = !!(caption && String(caption).trim());
        sidebarCaption.textContent = hasDesc ? caption : '';
        sidebarCaption.classList.toggle('hidden', !hasDesc);
        sidebarCaption.title = hasDesc ? caption : '';
    }

    // Login brand area
    document.querySelectorAll('#login-overlay .brand-mark[src], .login-brand-mark').forEach(img => {
        _brandArmFallback(img, logoUrl);
    });
    const loginWordmark = document.getElementById('login-brand-name');
    if (loginWordmark) loginWordmark.innerHTML = brandWordmarkHTML(name);

    // Welcome screen (both the static initial hero and any rebuilt new-chat DOM)
    document.querySelectorAll('#welcome-screen .brand-mark[src]').forEach(img => {
        _brandArmFallback(img, logoUrl);
    });
    const welcomeTitle = document.getElementById('welcome-title');
    if (welcomeTitle) welcomeTitle.innerHTML = brandWordmarkHTML(name);
    // Welcome hero only surfaces a customized description; the built-in default
    // stays hidden here (redundant with the eyebrow) but still shows elsewhere.
    const heroDesc = welcomeHeroDescription();
    document.querySelectorAll('#welcome-screen [data-brand-desc]').forEach(el => {
        const hasDesc = !!(heroDesc && heroDesc.trim());
        el.textContent = hasDesc ? heroDesc : '';
        el.classList.toggle('hidden', !hasDesc);
    });
    // Rebuilt welcome DOM inside other containers uses these hooks.
    document.querySelectorAll('[data-brand-slot="name"]').forEach(el => {
        el.innerHTML = brandWordmarkHTML(name);
    });
    document.querySelectorAll('[data-brand-slot="logo"]').forEach(img => {
        _brandArmFallback(img, logoUrl);
    });
    document.querySelectorAll('[data-brand-slot="desc"]').forEach(el => {
        const isWelcomePreview = !!el.closest('[data-preview="welcome"]');
        const isDefault = (desc || '').trim() === DEFAULT_BRAND.logo_description;
        const hasDesc = isWelcomePreview
            ? !!(desc && desc.trim() && !isDefault)
            : !!(desc && desc.trim());
        el.textContent = hasDesc ? desc : '';
        el.classList.toggle('hidden', !hasDesc);
        el.title = hasDesc ? desc : '';
    });
    document.querySelectorAll('[data-brand-slot="caption"]').forEach(el => {
        const hasDesc = !!(desc && desc.trim());
        el.textContent = hasDesc ? desc : '';
        el.classList.toggle('hidden', !hasDesc);
        el.title = hasDesc ? desc : '';
    });

    // Browser title: "<brand_name> 控制台" (控制台 localized)
    const consoleLabel = t('branding_browser_title') || '控制台';
    document.title = `${name} ${consoleLabel}`.trim();

    // Favicon (derived PNG served by the backend; falls back to the built-in)
    const favUrl = effectiveFaviconUrl();
    document.querySelectorAll('link[rel="icon"]').forEach(link => {
        link.href = `${favUrl}?v=${brandFetchSeq}`;
    });
}

/* Refresh the default-agent avatar fallback (uses the product logo). */
function applyBrandToAgentAvatars() {
    document.querySelectorAll('.agent-avatar-brand').forEach(img => {
        _brandArmFallback(img, effectiveLogoUrl());
    });
}

// =====================================================================
// i18n
// =====================================================================
const I18N = {
    zh: {
        console: '控制台',
        nav_chat: '工作台', nav_manage: '管理', nav_monitor: '监控', nav_system: '系统设置',
        nav_workbench: '工作台', nav_admin_console: '管理控制台',
        nav_return_workbench: '返回工作台',
        admin_home_title: '管理控制台',
        admin_home_hint: '选择左侧菜单管理智能体、组织与平台配置。',
        nav_admin_denied: '当前账号无权进入管理控制台。',
        sidebar_history_records: '会话历史',
        sidebar_history_view_all: '查看全部',
        sidebar_history_empty: '暂无历史会话',
        nav_group_agent_dev: '智能体开发', nav_group_model_access: '模型与接入',
        nav_group_org_perm: '组织与权限', nav_group_platform_ops: '平台运维',
        menu_chat: '对话', menu_agents: '智能体', menu_config: '模型服务', menu_agent_config: '智能体管理', menu_skills: '工具与技能',
        menu_platform: '系统设置', menu_tenant: '租户管理', menu_system_user: '成员管理',
        menu_roles: '角色权限', menu_org: '组织架构', menu_branding: '品牌设置',
        menu_audit: '审计', menu_backup: '备份升级', menu_open_api: '开放 API',
        nav_unavailable: '功能尚未开放',
        nav_unavailable_hint: '该功能正在筹备或尚未在当前配置开放，请返回其他可用页面。',
        nav_denied: '无权访问',
        nav_denied_hint: '您的账号无权访问该页面或当前未开通对应能力，请返回其他可用页面，或联系管理员开通权限。',
        nav_go_back: '返回可用页面',
        branding_title: '品牌设置',
        branding_subtitle: '设置控制台的品牌标识与说明',
        branding_instance: '当前实例',
        branding_brand_info: '品牌信息',
        branding_preview: '实时预览',
        branding_logo: 'Logo',
        branding_upload_logo: '上传 Logo',
        branding_use_default_logo: '使用默认 Logo',
        branding_logo_hint: 'PNG / JPG / WebP，最大 2MB',
        branding_brand_name: '品牌名称',
        branding_logo_desc: 'Logo 描述',
        branding_logo_desc_placeholder: '品牌标识旁或下方展示的短说明，可留空',
        branding_logo_desc_hint: '可留空，最多 100 字',
        branding_name_placeholder: '品牌名称，1～32 字',
        branding_sidebar: '侧边栏',
        branding_login: '登录页',
        branding_welcome: '对话欢迎页',
        branding_light: '浅色',
        branding_dark: '深色',
        branding_preview_hint: '预览效果，保存后生效',
        branding_cancel: '取消更改',
        branding_save: '保存设置',
        branding_reset_all: '恢复全部默认',
        branding_reset_logo: '恢复默认 Logo',
        branding_saved: '品牌设置已保存',
        branding_unsaved: '有未保存更改',
        branding_saved_state: '已保存',
        branding_readonly: '只读',
        branding_readonly_reason: '请先设置访问密码后方可编辑品牌',
        branding_enterprise_unavailable: '企业品牌授权与审计尚未接入，暂不可修改',
        branding_storage_corrupt: '品牌配置损坏，当前显示恢复预览。请重新读取，或明确恢复全部默认；原件将保留。',
        branding_loading: '加载中…',
        branding_retry: '重试',
        branding_load_failed: '加载失败',
        branding_conflict: '品牌设置已被其他人修改',
        branding_reload: '重新读取已发布值',
        branding_save_failed: '保存失败',
        login_select_tenant: '选择租户',
        login_enter_tenant: '进入',
        // identity-admin views (task 3.6)
        tenant_title: '租户管理',
        tenant_create: '创建租户',
        tenant_search_placeholder: '搜索租户名称 / 编码',
        tenant_loading: '加载中…',
        tenant_empty: '暂无租户',
        tenant_version_label: '版本',
        users_title: '用户管理',
        member_create: '新建成员',
        member_section_account: '账号信息',
        member_section_role: '角色与状态',
        member_section_tenants: '所属租户',
        member_tenants: '租户',
        member_tenants_title: '调整所属租户',
        admin_field_tenants: '目标租户', admin_field_tenants_hint: '可选择多个租户；新建账号将创建于所选租户',
        admin_field_tenants_edit_hint: '勾选/取消以增删所属租户（仅限你有管理资格的租户）',
        member_search_placeholder: '搜索账号 / 姓名',
        member_empty: '暂无成员',
        roles_title: '角色权限',
        role_create: '新建角色',
        role_empty: '暂无角色',
        role_permissions_label: '权限',
        org_title: '组织架构',
        dept_create: '新建部门',
        org_empty: '暂无部门',
        load_error: '加载失败',
        active: '启用',
        inactive: '停用',
        unsaved_changes_warning: '有未保存的更改，确定离开？',
        create_not_available: '该操作暂未开放',
        // identity-admin CRUD forms (task: wire create/edit/delete)
        admin_save: '保存', admin_create: '创建', admin_edit: '编辑', admin_delete: '删除', admin_saved: '已保存',
        admin_deleted: '已删除', admin_save_failed: '保存失败',
        admin_permissions_load_failed: '权限目录加载失败，请重新打开重试',
        admin_required_field: '请填写必填字段',
        admin_conflict: '已被他人修改，已刷新列表，请重试',
        admin_field_code: '编码', admin_field_code_hint: '小写字母/数字/连字符，创建后不可改',
        admin_field_name: '名称',
        admin_field_shared_root: '共享根目录', admin_field_shared_root_hint: '租户数据目录的绝对路径',
        admin_field_admin_username: '管理员账号',
        admin_field_admin_display: '管理员显示名',
        admin_field_admin_password: '管理员密码', admin_field_password_hint: '至少 8 位，避免使用弱密码',
        admin_field_recent_password: '当前密码确认', admin_field_recent_password_hint: '重新输入你的登录密码以授权本次操作',
        admin_field_active: '启用',
        admin_field_username: '账号', admin_field_username_hint: '3-64 位字母/数字/._-',
        admin_field_display_name: '显示名',
        admin_field_temp_password: '临时密码',
        admin_field_roles: '角色', admin_field_roles_hint: '默认勾选成员',
        admin_field_roles_edit_hint: '未勾选时将重置为默认成员（当前角色未加载）',
        admin_field_department: '部门', admin_field_department_hint: '可选', admin_field_department_none: '无部门',
        admin_field_position: '职位',
        admin_field_parent: '上级部门', admin_field_parent_none: '无（根目录）',
        admin_field_sort_order: '排序',
        admin_field_permissions: '权限', admin_field_permissions_hint: '从目录中选择权限点',
        admin_field_resource_grants: '资源授权',
        admin_field_resource_grants_hint: '为角色分配菜单 / 技能 / 工具 / 模型 / 智能体的可访问资源',
        admin_field_model_defaults: '模型默认值',
        admin_field_model_defaults_hint: '为已接通能力选择角色默认模型，须属于已授权的模型',
        admin_field_model_grants: '模型授权',
        admin_field_model_grants_hint: '为租户分配可分配角色/会话使用的模型',
        admin_resources_selected: '已选 {n} 项',
        admin_resources_none: '未选择资源',
        admin_resource_manage: '管理资源',
        admin_resource_search_placeholder: '搜索资源…',
        admin_resource_selectall: '全选当前页',
        admin_resource_clear: '清空',
        admin_resource_model_capabilities: '能力',
        admin_resource_kind_menu: '菜单',
        admin_resource_kind_skill: '技能',
        admin_resource_kind_tool: '工具',
        admin_resource_kind_model: '模型',
        admin_resource_kind_agent: '智能体',
        admin_field_platform_admin: '平台管理员',
        admin_field_admin_user_id: '管理员账号 ID', admin_field_admin_user_id_hint: '已有有效账号的用户 ID',
        admin_tenant_admin: '管理员',
        admin_tenant_roles: '角色',
        admin_tenant_admin_edit: '配置租户管理员',
        admin_reset: '重置密码', admin_reset_do: '确认重置', admin_reset_confirm: '确定重置「{name}」的密码？',
        admin_reset_temp_result: '一次性临时密码', admin_forbidden: '无权限',
        admin_prev: '上一页', admin_next: '下一页',
        admin_total_label: '共', admin_page_label: '第',
        filter_all: '全部', filter_restricted: '待改密',
        platform_title: '平台账号', platform_admin_only: '仅平台管理员可见',
        platform_search_placeholder: '搜索平台账号',
        platform_user_empty: '暂无平台账号',
        platform_user_edit_title: '编辑平台账号',
        platform_admin_badge: '平台管理员',
        admin_tab_members: '租户成员', admin_tab_platform: '平台账号',
        tenant_edit_title: '编辑租户',
        member_edit_title: '编辑成员',
        role_edit_title: '编辑角色',
        role_tab_basic: '基本信息',
        role_editor_back: '返回角色列表',
        role_dirty_pill: '有未保存更改',
        role_section_basic: '基本信息',
        role_basic_hint: '填写角色标识；下方配置功能权限。资源与模型在其他 Tab，保存时一并提交。',
        role_perm_search_placeholder: '搜索权限…',
        role_perm_select_group: '全选本组',
        role_perm_clear_group: '清空本组',
        role_section_model_assign: '可分配模型',
        role_model_assign_hint: '勾选该角色可使用的模型；默认模型只能从已选项中选择。',
        role_editor_foot_hint: '切换 Tab 不丢草稿 · 离开前若有改动会确认',
        role_create_sub: '创建后编码不可修改',
        role_copy_from: '从 {name} 复制',
        role_copy_suffix: '（副本）',
        dept_edit_title: '编辑部门',
        admin_delete_confirm_role: '确定删除角色「{name}」吗？',
        admin_delete_confirm_dept: '确定删除部门「{name}」吗？',
        role_builtin: '内置',
        role_members_label: '成员',
        role_view_members: '查看成员',
        role_copy: '复制', role_copy_title: '复制角色',
        org_cycle_rejected: '组织关系存在回环，操作被拒绝',
        audit_title: '身份审计',
        audit_scope_hint: '平台管理员查看全部，租户管理员仅当前租户',
        audit_actor_placeholder: '操作者',
        audit_action_placeholder: '动作',
        audit_apply: '筛选',
        audit_empty: '暂无审计记录',
        audit_result_success: '成功',
        audit_result_denied: '拒绝',
        branding_reset_confirm_title: '恢复全部默认设置',
        branding_reset_confirm_body: '将重置 Logo、品牌名称和 Logo 描述为内置默认值，确认操作？',
        branding_reset_confirm_ok: '确认恢复',
        branding_reset_confirm_cancel: '取消',
        branding_dirty_leave_title: '存在未保存更改',
        branding_dirty_leave_body: '品牌设置尚未保存，将要离开此页，是否放弃更改？',
        branding_dirty_leave_ok: '放弃更改并离开',
        branding_dirty_leave_cancel: '留在此页',
        branding_unsaved_warn: '品牌设置还有未保存的更改',
        branding_save_pending: '保存结果未确认',
        branding_save_pending_body: '未能确认服务端是否已发布，请重新读取已发布品牌后重试。',
        branding_save_pending_ok: '重新读取',
        branding_added: '已添加新品牌',
        branding_image_too_large: '图片不能超过 2MB',
        branding_invalid_image: '图片无效',
        branding_asset_fallback: '品牌图片无法加载',
        branding_browser_title: '控制台',
        branding_logo_alt: '品牌 Logo',
        agents_page_title: '智能体配置', agents_page_desc: '管理团队中的智能体成员',
        agents_create: '创建智能体',
        agents_name_placeholder: '智能体名称',
        agents_name_required: '请填写名称',
        agents_stale: '列表已更新，请刷新后重试',
        agents_id_placeholder: '留空则自动生成',
        agents_id_tip: '智能体的唯一标识，创建后不可修改。仅支持小写英文、数字和连字符（-），如 coding-agent。留空则根据名称自动生成。',
        agents_id_invalid: 'ID 需以字母或数字开头，仅支持字母、数字、下划线和连字符，最长 64 位',
        agents_avatar: '头像',
        agents_tab_profile: '概况',
        agents_tab_skills: '能力',
        agents_tab_tasks: '任务',
        agents_tab_files: '核心文件',
        agents_tasks_label: '本智能体的定时任务',
        tasks_empty_agent: '该智能体暂无定时任务。将在对话中通过 scheduler 工具创建的任务归属到此员工。',
        agents_core_edit: '编辑',
        agents_core_preview: '预览',
        agents_core_file_agent: '智能体设定',
        agents_core_file_user: '用户信息',
        agents_core_file_rule: '工作空间规则',
        agents_core_file_memory: '长期记忆',
        agents_default: '默认',
        agents_archived: '已归档',
        agents_chat: '开始对话',
        start_chat: '开始对话',
        agent_workbench_title: '智能体',
        agent_workbench_desc: '选择已配置的智能体开始对话',
        agent_workbench_refresh: '刷新',
        agent_workbench_loading: '加载中…',
        agent_workbench_empty: '暂无可用智能体',
        agent_workbench_failed: '加载失败',
        agent_workbench_retry: '重试',
        agent_unavailable: '暂不可用',
        agent_cannot_run: '当前不可运行',
        agent_target_unavailable: '该智能体已不可用，请刷新列表后重试',
        agent_starting: '正在进入…',
        agent_start_failed: '暂时无法开始对话，请重试',
        agent_runtime_not_enabled: '当前版本尚未开放对话',
        agent_permission_denied: '暂无对话权限，请联系管理员开通',
        agents_delete: '删除',
        agents_delete_title: '删除智能体',
        agents_delete_confirm: '确定删除智能体「{name}」吗？其工作空间和会话将一并移除，且无法恢复。',
        agents_pick_hint: '选择智能体',
        agents_clone_label: '从已有智能体复制',
        agents_clone_hint: '复制其配置、技能与知识作为起点',
        agents_avatar_upload: '上传图片',
        agents_clone_none: '空白',
        agents_clone_from: '{name}',
        agents_name: '名称',
        agents_saved: '已保存',
        agents_save_failed: '保存失败',
        agents_no_desc: '暂无职责',
        agents_description: '职责',
        agents_description_placeholder: '该智能体负责哪些工作、在什么场景被使用',
        agents_description_hint: '用于多智能体协作时的任务分配',
        agents_model: '默认模型',
        agents_model_follows_global: '跟随全局配置',
        agents_model_default_hint: '默认使用主模型，在「模型配置」中修改。',
        agents_position: '职位',
        agents_position_placeholder: '如：采购专员',
        agents_category: '分类',
        agents_category_none: '未分类',
        agents_tags: '标签',
        agents_tags_placeholder: '标签以逗号分隔，如：供应商, 招标',
        agents_greeting: '问候语',
        agents_persona: '人设摘要',
        agents_persona_hint: '注入系统提示的员工人设说明',
        agents_scene: '关联场景',
        agents_scene_none: '无关联场景',
        agents_skills_all: '使用全部已安装技能',
        agents_skills_pick: '只启用勾选的技能',
        agents_skills_label: '技能',
        agents_sops_label: 'SOP 流程',
        agents_sops_hint: '为员工绑定可观测的 SOP 执行流程 ID（仅作能力清单展示，不驱动状态机）。',
        agents_sops_placeholder: 'SOP ID',
        agents_sops_add: '添加',
        agents_tools_label: '工具目录',
        agents_tools_hint: '勾选「允许」仅启用白名单；勾选「拒绝」从可用工具中剔除。两者互斥。',
        agents_tools_none_hint: '未启用白名单/黑名单时，该员工可使用全部已安装工具。',
        agents_allow: '允许',
        agents_deny: '拒绝',
        agents_knowledge: '知识库',
        agents_knowledge_shared: '共享',
        agents_knowledge_own: '独立',
        agents_knowledge_hint: '共享：与团队读写同一个知识库\n独立：拥有专属知识库，互不影响',
        agents_knowledge_working: '处理中…',
        agents_knowledge_failed: '切换失败',
        agents_empty: '还没有智能体。创建一个，开始组团队。',
        agents_select_hint: '从左侧选择一个智能体进行配置',
        agents_pick_tip: '切换当前智能体',
        team_members: '当前会话成员',
        team_invite: '添加到当前会话',
        team_remove: '移出这个会话',
        composer_agent_owner: '主',
        channel_bound_agent: '绑定智能体',
        channel_bound_default: '默认',
        channel_bound_agent_hint: '第一个为默认智能体，负责接收消息并可委派给其他成员',
        channel_team_none: '未选择',
        channel_team_no_candidates: '暂无可选的智能体',
        settings_tab_basic: '基础配置',
        settings_tab_models: '模型配置',
        knowledge_shared_hint: '知识库默认全员共享，在侧栏「知识」查看和编辑。',
        menu_memory: '记忆管理', menu_knowledge: '知识库', menu_scenes: '场景应用', menu_channels: '消息渠道', menu_tasks: '定时任务',
        menu_logs: '运行日志', menu_todo: '我的待办', menu_scenarios: '场景应用',
        models_title: '模型管理',
        models_desc: '统一管理对话、图像、语音、向量、搜索能力',
        models_section_vendors: '厂商凭据',
        models_section_vendors_desc: '一处配置，多个模型能力共享',
        models_section_capabilities: '模型能力',
        models_add_vendor: '添加厂商',
        models_provider: '厂商',
        models_model: '模型',
        models_voice: '音色',
        models_configured: '已配置',
        models_not_configured: '未配置',
        models_pick_to_configure: '选择以配置',
        models_clear_credential: '清除凭据',
        models_base_default_hint: '留空将使用官方默认地址',
        models_base_default: '默认',
        models_custom_vendor_label: '自定义',
        models_custom_name: '名称',
        models_custom_delete: '删除',
        models_custom_delete_confirm_title: '删除自定义厂商',
        models_custom_delete_confirm_msg: '确定删除该自定义厂商吗？此操作无法撤销。',
        models_custom_name_required: '请填写名称',
        models_custom_base_required: '请填写 API Base',
        models_custom_edit_title: '编辑自定义厂商',
        models_custom_add_title: '添加自定义厂商',
        models_capability_chat: '主模型',
        models_capability_chat_desc: '用于基础对话和 Agent 推理',
        models_capability_chat_fallback: '主模型兜底',
        models_capability_chat_fallback_desc: '仅在主模型彻底失败（重试耗尽）后接管',
        models_fallback_enable: '启用兜底模型',
        models_fallback_config: '兜底模型',
        models_fallback_config_tip: '配置主模型兜底：主模型彻底失败后接管',
        models_fallback_modal_title: '主模型兜底',
        models_fallback_modal_desc: '当主模型重试次数用尽仍然失败时，自动切换到兜底模型完成本轮回复',
        models_fallback_badge_on: '兜底已启用',
        models_capability_vision: '图像理解',
        models_capability_vision_desc: '识别图片内容，用于图像识别工具',
        models_capability_image: '图像生成',
        models_capability_image_desc: '生成图片，用于图像生成技能',
        models_auto_using: '当前优先使用',
        models_capability_asr: '语音识别',
        models_capability_asr_desc: '语音转文字',
        models_capability_tts: '语音合成',
        models_capability_tts_desc: '文字转语音',
        models_capability_embedding: '向量',
        models_capability_embedding_desc: '用于记忆与知识的向量化检索',
        models_capability_search: '联网搜索',
        models_capability_search_desc: '实时网页检索能力，用于搜索工具',
        models_strategy_auto: '自动',
        models_search_strategy_label: '策略',
        models_search_strategy_fixed: '指定',
        models_search_strategy_auto_hint: '从已配置厂商中自动选择',
        models_search_strategy_fixed_hint: '指定使用搜索厂商',
        models_pending_config: '待配置',
        models_search_available_label: '可用搜索厂商：',
        models_search_none_configured: '暂未启用任何搜索厂商，点击添加',
        models_search_add_provider: '添加厂商',
        models_search_add_desc: '选择一个搜索厂商进行配置',
        models_search_bocha_title: '配置博查 API Key',
        models_search_bocha_desc: '前往博查开放平台创建 API Key',
        models_search_anysearch_title: '配置 AnySearch API Key',
        models_search_anysearch_desc: '前往 anysearch.com 控制台创建 API Key。',
        models_search_serply_title: '配置 Serply API Key',
        models_search_serply_desc: '前往 serply.io 控制台创建 API Key。',
        models_search_edit_hint: '点击修改配置',
        models_unavailable: '不可用',
        models_set_via_env: '通过环境变量启用',
        models_dim_label: '维度',
        models_save_success: '已保存',
        models_save_failed: '保存失败',
        models_cleared: '已清除',
        models_clear_failed: '清除失败',
        models_embedding_change_title: '更改向量模型',
        models_embedding_change_msg: '切换向量模型后，已有索引将失效，需要重建。是否继续？',
        models_embedding_saved_title: '向量模型已更新',
        models_embedding_saved_msg: '请在聊天框输入 /memory rebuild-index 重建索引。',
        models_embedding_saved_ok: '去执行',
        models_pick_provider: '待选择',
        models_manage_api_key: '管理 API Key',
        models_clear_confirm_title: '清除厂商凭据',
        models_clear_confirm_msg: '确认清除该厂商的 API Key 与 Base URL 吗？相关能力将不再可用。',
        cancel: '取消',
        save: '保存',
        ok: '确定',
        knowledge_title: '知识库', knowledge_desc: '浏览和探索你的知识库',
        knowledge_tab_docs: '文档', knowledge_tab_graph: '图谱',
        knowledge_loading: '加载知识库中...', knowledge_loading_desc: '知识页面将显示在这里',
        knowledge_select_hint: '选择一个文档查看', knowledge_empty_hint: '暂无知识页面',
        knowledge_empty_guide: '在对话中发送文档、链接或主题给 Agent，它会自动整理到你的知识库中。',
        knowledge_go_chat: '开始对话',
        knowledge_new: '新建',
        knowledge_new_category: '新建分类',
        knowledge_new_document: '新建文档',
        knowledge_import_documents: '导入文档',
        welcome_subtitle: '我可以帮你解答问题、管理计算机、创造和执行技能，并通过<br>长期记忆和知识库不断成长',
        example_sys_title: '系统管理', example_sys_text: '查看工作空间里有哪些文件',
        example_task_title: '定时任务', example_task_text: '1分钟后提醒我检查服务器',
        example_code_title: '编程助手', example_code_text: '搜索AI资讯并生成可视化网页报告',
        example_knowledge_title: '知识库', example_knowledge_text: '查看知识库当前文档情况',
        example_skill_title: '技能系统', example_skill_text: '查看所有支持的工具和技能',
        example_web_title: '指令中心', example_web_text: '查看全部命令',
        slash_help: '显示命令帮助',
        slash_status: '查看运行状态',
        slash_context: '查看对话上下文',
        slash_context_clear: '清除对话上下文',
        slash_compact: '压缩较早的对话以释放上下文',
        slash_skill_list: '查看已安装技能',
        slash_skill_list_remote: '浏览技能广场',
        slash_skill_search: '搜索技能',
        slash_skill_install: '安装技能 (名称或 GitHub URL)',
        slash_skill_uninstall: '卸载技能',
        slash_skill_info: '查看技能详情',
        slash_skill_enable: '启用技能',
        slash_skill_disable: '禁用技能',
        slash_memory_dream: '手动触发记忆蒸馏 (可指定天数, 默认3)',
        slash_knowledge: '查看知识库统计',
        slash_knowledge_list: '查看知识库文件树',
        slash_knowledge_on: '开启知识库',
        slash_knowledge_off: '关闭知识库',
        slash_config: '查看当前配置',
        slash_cancel: '中止当前正在运行的 Agent 任务',
        slash_steer: '向当前正在运行的 Agent 任务注入引导指令',
        steer_active: '引导当前任务',
        slash_logs: '查看最近日志',
        slash_version: '查看版本',
        input_placeholder: '输入消息，/ 使用指令，@ 引用智能体或文件',
        config_title: '配置管理', config_desc: '管理模型和 Agent 配置',
        config_model: '模型配置', config_agent: 'Agent 配置',
        config_language: '语言', config_language_hint: '界面展示、命令文案、系统提示词等使用的语言（与右上角切换同步）',
        config_system: '系统',
        config_task_notify: '任务通知', config_task_notify_hint: '窗口在后台且任务完成或失败时发送浏览器通知，点击可跳转会话',
        config_task_notify_sound: '通知声音', config_task_notify_sound_hint: '通知开启时可单独关闭提示音',
        config_task_notify_blocked: '系统通知已被浏览器屏蔽，请点击地址栏左侧图标 → 通知 → 允许后刷新页面',
        notify_task_done: '任务完成',
        notify_task_error: '任务失败',
        config_model_advanced: '高级配置',
        config_channel: '通道配置',
        config_agent_enabled: 'Agent 模式',
        config_max_tokens: '最大上下文 Token', config_max_tokens_hint: '对话中 Agent 能输入的最大 Token 长度，超过后会智能压缩处理',
        config_max_turns: '最大记忆轮次', config_max_turns_hint: '一问一答为一轮，超过后会智能压缩处理',
        config_max_steps: '最大执行步数', config_max_steps_hint: '单次对话中 Agent 最多调用工具的次数',
        config_enable_thinking: '深度思考', config_enable_thinking_hint: '是否启用深度思考模式',
        config_reasoning_effort: '思考强度', config_reasoning_effort_hint: '按当前模型厂商支持的原生枚举发送',
        config_subagent: '子 Agent', config_subagent_hint: '把可独立完成的任务交给子 Agent，多个任务并行执行，只把结论带回主对话',
        config_self_evolution: '自主进化', config_self_evolution_hint: '会话空闲后自动复盘，沉淀记忆、优化技能、处理未完成事项',
        evolution_badge: '自主学习',
        config_channel_type: '通道类型',
        config_provider: '模型厂商', config_model_name: '模型',
        config_custom_model_hint: '输入自定义模型名称',
        config_save: '保存', config_saved: '已保存',
        config_save_error: '保存失败',
        config_custom_option: '自定义',
        config_custom_tip: '接口需遵循 OpenAI API 协议',
        config_security: '安全设置', config_password: '访问密码',
        config_password_hint: '留空则不启用密码保护',
        config_permission: '默认权限',
        config_permission_hint: '新会话的默认权限范围，决定 Agent 能修改哪些文件、能执行哪些命令',
        config_permission_desc: '新会话默认使用该权限；单个会话可在输入框下方单独调整',
        config_password_changed: '密码已更新',
        config_password_cleared: '密码已清除',
        config_password_security_warning: '⚠️ 警告：目前密码为空且对外连接埠开放，建议重启服务，或检查是否调整监听位址绑定。',
        skills_title: '技能管理', skills_desc: '查看、启用或禁用 Agent 工具和技能', skills_hub_btn: '探索技能广场',
        skills_loading: '加载技能中...', skills_loading_desc: '技能加载后将显示在此处',
        tools_section_title: '内置工具', tools_loading: '加载工具中...',
        skills_section_title: '技能', skill_enable: '启用', skill_disable: '禁用',
        skill_toggle_error: '操作失败，请稍后再试',
        skill_open_hint: '点击查看技能内容',
        skill_back: '返回列表',
        skill_load_failed: '读取技能内容失败',
        skill_builtin_readonly: '内置技能不可编辑（重启会覆盖）',
        memory_title: '记忆管理', memory_desc: '查看 Agent 记忆文件和内容',
        memory_tab_files: '记忆文件', memory_tab_dreams: '自主进化',
        memory_loading: '加载记忆文件中...', memory_loading_desc: '记忆文件将显示在此处',
        memory_back: '返回列表',
        memory_col_name: '文件名', memory_col_type: '类型', memory_col_size: '大小', memory_col_updated: '更新时间',
        channels_title: '消息渠道', channels_desc: '管理已接入的消息通道',
        channels_add: '接入通道', channels_disconnect: '断开',
        channels_save: '保存配置', channels_saved: '已保存', channels_save_error: '保存失败',
        channels_restarted: '已保存并重启',
        channels_connect_btn: '接入', channels_cancel: '取消',
        channels_select_placeholder: '选择要接入的通道...',
        channels_empty: '暂未接入任何通道', channels_empty_desc: '点击右上角「接入通道」按钮开始配置',
        channels_disconnect_confirm: '确认断开该通道？配置将保留但通道会停止运行。',
        channels_connected: '已接入', channels_connecting: '接入中...',
        weixin_scan_title: '微信扫码登录', weixin_scan_desc: '请使用微信扫描下方二维码',
        weixin_scan_loading: '正在获取二维码...', weixin_scan_waiting: '等待扫码...',
        weixin_scan_scanned: '已扫码，请在手机上确认', weixin_scan_expired: '二维码已过期，正在刷新...',
        weixin_scan_success: '登录成功，正在启动通道...', weixin_scan_fail: '获取二维码失败',
        weixin_qr_tip: '二维码约2分钟后过期',
        wecom_scan_btn: '扫码创建企微机器人', wecom_scan_desc: '使用企业微信扫码，一键创建智能机器人',
        wecom_scan_success: '创建成功，正在启动通道...',
        wecom_scan_fail: '创建失败',
        wecom_mode_scan: '扫码接入', wecom_mode_manual: '手动填写',
        feishu_scan_btn: '一键创建飞书应用',
        feishu_scan_desc: '使用飞书 App 扫码，自动创建应用并预置全部权限与事件订阅',
        feishu_scan_replace_desc: '使用飞书 App 扫码创建新机器人，将覆盖当前的 App ID / Secret',
        feishu_scan_loading: '正在向飞书申请二维码...',
        feishu_scan_waiting: '等待扫码...',
        feishu_scan_tip: '二维码 10 分钟内有效，仅供一次扫描',
        feishu_scan_open_link: '或点击此处在浏览器中打开',
        feishu_scan_success: '应用创建成功，正在启动通道...',
        feishu_scan_expired: '二维码已过期，请重试',
        feishu_scan_denied: '已取消授权',
        feishu_scan_fail: '创建失败',
        feishu_scan_retry: '重试',
        feishu_sdk_downloading: '正在下载飞书组件...',
        feishu_sdk_downloading_tip: '首次启用需要下载，约 1MB，稍后自动继续',
        feishu_mode_scan: '扫码创建', feishu_mode_manual: '手动填写',
        tasks_title: '定时任务', tasks_desc: '查看和管理定时任务',
        todo_title: '待办事项', todo_subtitle: '处理需要你跟进的事项',
        todo_add_btn: '新建待办', todo_save: '保存', todo_cancel: '取消',
        todo_filter_all: '全部', todo_filter_open: '未完成', todo_filter_pending: '待处理', todo_filter_progress: '处理中', todo_filter_done: '已完成', todo_filter_cancel: '已取消',
        todo_only_overdue: '只看逾期', todo_search_placeholder: '搜索待办…', todo_clear_due: '清空',
        todo_status_all: '全部', todo_status_open: '未完成', todo_status_pending: '待处理', todo_status_in_progress: '处理中', todo_status_completed: '已完成', todo_status_cancelled: '已取消',
        todo_kind_general: '普通事项', todo_kind_input_required: '补充资料', todo_kind_confirmation: '方案确认', todo_kind_review: '结果验收',
        todo_priority_low: '低', todo_priority_normal: '普通', todo_priority_high: '高',
        todo_edit_title: '编辑待办', todo_create_title: '新建待办',
        todo_field_title: '标题', todo_field_desc: '说明', todo_field_kind: '分类', todo_field_priority: '优先级', todo_field_due: '截止时间',
        todo_by: '创建于', todo_updated: '更新于', todo_completed: '完成于', todo_created_by: '来源',
        todo_empty_open: '暂无未完成的待办', todo_empty_pending: '暂无待处理事项', todo_empty_progress: '暂无处理中的事项',
        todo_empty_done: '暂无已完成事项', todo_empty_cancel: '暂无已取消事项', todo_empty_all: '还没有待办事项',
        todo_empty_search: '没有匹配的待办', todo_empty_overdue: '暂无逾期事项',
        todo_disabled_banner: '待办功能未开启，可在配置中启用 todo_enabled。',
        todo_unauthorized_banner: '请先登录后再使用待办功能。',
        todo_load_failed: '加载失败，请稍后重试。',
        todo_banner_disabled: '待办功能未开启', todo_banner_unauthorized: '未认证',
        todo_empty_title: '待办标题', todo_empty_subtitle: '为空时自动填充',
        todo_confirm_discard: '有未保存的修改，确定要放弃吗？',
        todo_confirm_complete: '确定标记为已完成？', todo_confirm_cancel: '确定取消该待办？', todo_confirm_reopen: '确定重新打开？',
        todo_saved: '已保存', todo_save_failed: '保存失败', todo_created: '已创建',
        todo_action_start: '开始处理', todo_action_complete: '完成', todo_action_cancel: '取消', todo_action_reopen: '重新打开', todo_action_edit: '编辑', todo_action_events: '处理历史',
        todo_detail_title: '待办详情', todo_detail_source: '来源', todo_detail_history: '处理历史', todo_detail_history_empty: '暂无处理记录',
        todo_detail_note: '处理说明', todo_detail_note_empty: '无说明',
        todo_due_overdue: '已逾期', todo_due_soon: '即将到期', todo_due_none: '无截止时间',
        todo_pagination_prev: '上一页', todo_pagination_next: '下一页',
        todo_source_manual: '手动创建', todo_source_conversation: '会话来源',
        todo_load_error: '加载失败', todo_retry: '重试',
        tasks_coming: '即将推出', tasks_coming_desc: '定时任务管理功能即将在此提供',
        tasks_unavailable: '定时任务暂不可用', tasks_unavailable_desc: '当前身份模式未开放定时任务，请在 legacy 模式下使用，或由管理员适配后开启。',
        task_add_btn: '新增任务',
        task_edit_title: '编辑定时任务',
        task_add_title: '新增定时任务',
        task_name: '任务名称',
        task_enabled: '启用任务',
        task_schedule_type: '调度类型',
        task_schedule_cron: 'Cron 表达式',
        task_schedule_interval: '固定间隔',
        task_schedule_once: '一次性任务',
        task_cron_expression: 'Cron 表达式',
        task_cron_hint: '格式: 分 时 日 月 周，例如 "0 9 * * *" 表示每天 9:00',
        task_interval_seconds: '间隔秒数',
        task_interval_hint: '最小 60 秒，例如 3600 表示每小时执行一次',
        task_once_time: '执行时间',
        task_action_type: '动作类型',
        task_action_send_message: '发送消息',
        task_action_agent_task: 'AI 任务',
        task_channel_type: '通道类型',
        task_channel_hint: '选择定时消息发送的通道',
        task_message_content: '消息内容',
        task_task_description: '任务描述',
        task_delete_btn: '删除任务',
        task_delete_confirm_title: '删除定时任务',
        task_delete_confirm_msg: '确定删除该定时任务吗？此操作无法撤销。',
        task_run_now: '立即执行',
        task_next_run: '下次执行',
        task_run_confirm_title: '立即执行任务',
        task_run_confirm_msg: '该任务会立即向已配置的通道和接收者发送内容。是否继续？',
        task_run_started: '已开始执行',
        task_run_failed: '执行失败',
        logs_title: '运行日志', logs_desc: '实时日志输出 (run.log)',
        logs_live: '实时', logs_coming_msg: '日志流即将在此提供。将连接 run.log 实现类似 tail -f 的实时输出。',
        new_chat: '新对话',
        new_team_chat: '多智能体对话',
        new_team_chat_hint: '选择参与本次对话的智能体，第一个为会话的默认智能体。',
        new_team_chat_owner: '默认',
        new_team_chat_start: '开始对话',
        new_team_chat_min: '至少选择两个智能体',
        session_history: '历史对话',
        history_desc: '找到之前的对话，接着聊',
        history_search_placeholder: '搜索会话标题',
        history_search_clear: '清空搜索',
        history_search_loading: '正在搜索…',
        history_search_empty: '没有匹配的会话，试试其他标题关键词',
        history_search_count: '找到 {count} 个会话',
        history_search_limit: '搜索词最多 100 个字符',
        history_search_unsupported: '当前服务暂不支持标题搜索，请更新服务后重试',
        history_refresh: '刷新历史会话',
        history_current: '当前会话',
        history_more: '更多操作',
        session_history_loading: '加载历史会话中…',
        session_history_empty: '暂无历史会话',
        session_history_failed: '加载失败，请重试',
        session_history_retry: '重试',
        session_history_not_enabled: '历史会话功能当前未开放',
        ws_toggle: '工作空间', ws_tab_preview: '预览', ws_tab_files: '文件',
        ws_default_workspace: '默认空间', ws_sel_title: '选择工作空间',
        ws_sel_default_hint: '使用默认工作空间（~/cow）', ws_sel_recents: '最近使用',
        ws_sel_open: '打开项目…', ws_sel_new: '新建项目', ws_sel_new_placeholder: '项目名称',
        ws_sel_create: '创建', ws_sel_up: '上一级',
        ws_sel_new_subtitle: '将在 {root} 下创建新项目目录', ws_sel_new_hint: '仅填写项目名称，不含路径分隔符',
        ws_sel_name_required: '请输入项目名称', ws_sel_name_no_slash: '项目名称不能包含 / 或 \\',
        ws_sel_open_here: '打开此目录', ws_sel_dblclick_hint: '双击进入子目录，单击选中',
        ws_sel_no_subdirs: '此目录下没有子文件夹', ws_sel_drives: '此电脑',
        ws_open_external: '在新标签页打开', ws_download: '下载', ws_copy_path: '复制路径',
        ws_close: '关闭', ws_refresh: '刷新', ws_preview: '预览',
        ws_search_placeholder: '搜索文件',
        ws_preview_empty: '选择一个文件进行预览',
        ws_preview_failed: '预览失败',
        ws_link_not_found: '工作空间中找不到该文件',
        ws_no_inline_preview: '该类型不支持内嵌预览',
        ws_empty_dir: '空目录', ws_no_results: '没有匹配的文件',
        ws_truncated: '文件过多，仅显示部分',
        ws_edit: '编辑', ws_edit_save: '保存 (Ctrl+S)', ws_edit_cancel: '退出编辑',
        ws_edit_saved: '已保存',
        ws_edit_load_failed: '打开编辑器失败',
        ws_edit_save_failed: '保存失败',
        ws_edit_too_large: '文件过大，无法在面板中编辑',
        ws_edit_unsupported: '该类型不支持编辑',
        ws_edit_encoding: '该文件不是 UTF-8 编码，编辑会损坏内容',
        ws_edit_conflict_title: '文件已被改动',
        ws_edit_conflict_msg: '这个文件在你编辑期间被改动过（可能是 Agent 写入的）。覆盖保存会丢弃磁盘上的新内容。',
        ws_edit_overwrite: '覆盖保存',
        ws_edit_discard_title: '放弃未保存的修改？',
        ws_edit_discard_msg: '当前文件有未保存的修改，继续操作会丢失这些内容。',
        ws_edit_discard_ok: '放弃修改',
        today: '今天', yesterday: '昨天', earlier: '更早',
        session_pinned_group: '置顶',
        pin_session: '置顶',
        unpin_session: '取消置顶',
        project_rename: '重命名项目',
        project_delete: '删除项目',
        project_rename_title: '重命名项目',
        project_delete_title: '删除项目',
        project_delete_confirm: '确认删除项目「{name}」？仅移除项目记录，磁盘上的文件不会被删除，其下会话将回到默认空间。',
        perm_menu_title: '本次会话权限',
        perm_read_only: '只读',
        perm_workspace_write: '工作区可写',
        perm_full_access: '全部可访问',
        perm_read_only_desc: '只能查看和分析，不修改任何文件',
        perm_workspace_write_desc: '在当前工作空间内自由读写，空间之外的写入会被拒绝',
        perm_full_access_desc: '不加限制，可修改任意位置（当前默认）',
        perm_follow_global: '跟随全局设置',
        perm_tip: '权限：{name}',
        perm_denied_hint: '当前权限为「{name}」，此操作被拒绝。',
        perm_denied_action: '调整权限',
        model_menu_title: '本次会话模型',
        model_follow_global: '跟随全局设置',
        model_follow_agent: '跟随智能体默认模型',
        model_tip: '模型：{name}',
        model_unset: '未配置',
        session_settings_failed: '设置失败，请重试',
        delete_session_confirm: '确认删除该会话？所有消息将被清除。',
        delete_session_title: '删除会话',
        rename_session: '重命名',
        delete_message_confirm: '确认删除这条消息？',
        delete_message_title: '删除消息',
        edit_disabled_reply_active: '正在生成回复，暂时无法编辑。',
        delete_disabled_reply_active: '正在生成回复，暂时无法删除。',
        untitled_session: '新对话',
        context_cleared: '— 以上内容已从上下文中移除 —',
        tip_new_chat: '新建对话',
        tip_clear_context: '清除上下文',
        tip_attach: '添加附件',
        tip_cancel: '中止',
        tip_cancelled: '已中止',
        attach_menu_file: '上传文件',
        mic_idle_title: '点击录音 / 再按一次结束',
        mic_recording_title: '录音中，再次点击结束',
        mic_busy_title: '识别中…',
        mic_permission_denied: '无法访问麦克风，请检查浏览器权限',
        mic_too_short: '录音太短，请重试',
        mic_error: '语音识别失败',
        optimize_idle_title: '智能优化输入',
        optimize_busy_title: '优化中…',
        optimize_error: '指令优化失败',
        optimize_empty: '输入为空，无法优化',
        speak_msg: '朗读这段回复',
        voice_reply_mode_label: '语音回复策略',
        voice_reply_off: '关闭',
        voice_reply_if_voice: '仅语音问/语音答',
        voice_reply_always: '总是语音回复',
        attach_menu_folder: '上传文件夹',
        confirm_yes: '确认',
        confirm_cancel: '取消',
        error_send: '发送失败，请稍后再试。', error_timeout: '请求超时，请再试一次。',
        error_login_required: '登录已失效，请重新登录后发送。',
        error_tenant_required: '请先选择当前账号所属的租户，再发送消息。',
        error_chat_forbidden: '当前账号没有此租户的对话权限，请联系管理员。',
        error_password_required: '请先修改初始密码，再发送消息。',
        thinking_in_progress: '思考中...', thinking_done: '已深度思考', thinking_duration: '耗时',
        edit_message: '编辑消息',
        regenerate_response: '重新生成',
        edit_save: '保存并发送',
        edit_cancel: '取消',
        account_loading: '账号信息加载中', account_unavailable: '账号信息暂不可用',
        account_local: '本地访问', account_password_mode: '密码保护', account_public_mode: '免登录模式',
        account_retry: '重新检查', account_retry_hint: '请重新检查', account_logout: '退出登录',
        account_logging_out: '正在退出…', account_logout_unconfirmed: '退出未确认', account_retry_logout: '重试退出',
        account_login: '登录', account_login_hint: '请输入登录信息以访问控制台',
        account_credentials_error: '登录信息有误，请重试', account_login_failed: '登录未完成，请重试',
        account_menu_profile: '个人资料', account_menu_password: '账号安全', account_menu_prefs: '个人偏好',
        account_menu_about: '帮助与关于',
        account_profile_title: '个人资料', account_profile_global: '全局账号',
        account_profile_member: '当前租户成员', account_profile_display_name: '姓名',
        account_profile_username: '登录账号', account_profile_platform: '平台身份',
        account_profile_tenant: '当前租户', account_profile_member_name: '成员姓名',
        account_profile_role: '实际角色', account_profile_department: '部门',
        account_profile_position: '岗位', account_profile_empty: '未设置',
        account_profile_no_tenant: '未加入任何租户', account_profile_error: '资料读取失败',
        account_profile_edit: '编辑资料', account_profile_avatar_hint: '点击可更换头像',
        account_profile_error_required: '该项不能为空',
        saved: '已保存',
        account_password_title: '修改密码', account_password_note: '修改成功后，其他已登录会话也会失效，需要重新登录。',
        account_password_old: '原密码', account_password_new: '新密码', account_password_confirm: '确认新密码',
        account_password_submit: '提交修改', account_password_invalid_old: '原密码错误',
        account_password_weak: '新密码不符合要求，请重试', account_password_mismatch: '两次输入的新密码不一致',
        account_password_unknown: '操作未完成，请重试', account_password_done: '密码已修改，请重新登录',
        account_password_forced_title: '请设置新密码', account_password_forced_note: '首次登录/临时密码需先设置新密码，完成后才能使用其他功能。',
        account_password_forced_required: '请先完成密码设置', account_password_forced_logout: '需先设置新密码才能继续。确定退出登录吗？',
        account_prefs_title: '界面偏好', account_prefs_note: '偏好仅在当前浏览器生效，不写入实例配置。',
        account_prefs_theme: '主题', account_prefs_lang: '语言', account_prefs_light: '浅色',
        account_prefs_dark: '深色', account_prefs_zh: '简体', account_prefs_hant: '繁體', account_prefs_en: 'EN',
        account_prefs_storage_fail: '浏览器存储不可用，本次修改仅在本页生效',
        account_tenant_title: '切换租户', account_tenant_none: '未加入', account_tenant_current: '当前',
        account_tenant_no_available: '未加入任何租户', account_tenant_invalid: '目标租户已失效，请重新选择',
        account_assign_pending: '账号尚未分配到任何启用的租户，暂时无法进入工作台或管理页面。请联系管理员完成租户分配后点击重试。',
        account_tenant_single: '当前租户', account_about_title: '关于', account_about_version_unknown: '版本未获取',
        logout: '退出',
        close: '关闭',
    },
    'zh-Hant': {

        console: '控制台',
        nav_chat: '工作台', nav_manage: '管理', nav_monitor: '監控', nav_system: '系統設定',
        nav_workbench: '工作台', nav_admin_console: '管理控制台',
        nav_return_workbench: '返回工作台',
        admin_home_title: '管理控制台',
        admin_home_hint: '選擇左側選單管理智慧體、組織與平台設定。',
        nav_admin_denied: '目前帳號無權進入管理控制台。',
        sidebar_history_records: '會話歷史',
        sidebar_history_view_all: '查看全部',
        sidebar_history_empty: '暫無歷史會話',
        nav_group_agent_dev: '智能體開發', nav_group_model_access: '模型與接入',
        nav_group_org_perm: '組織與權限', nav_group_platform_ops: '平台維運',
        menu_chat: '對話', menu_agents: '智慧體', menu_config: '模型服務', menu_agent_config: '智慧體管理', menu_skills: '工具與技能',
        menu_platform: '系統設定', menu_tenant: '租戶管理', menu_system_user: '成員管理',
        menu_roles: '角色權限', menu_org: '組織架構', menu_branding: '品牌設定',
        menu_audit: '稽核', menu_backup: '備份升級', menu_open_api: '開放 API',
        nav_unavailable: '功能尚未開放',
        nav_unavailable_hint: '該功能正在籌備或尚未在當前配置開放，請返回其他可用頁面。',
        nav_denied: '無權存取',
        nav_denied_hint: '您的帳號無權存取該頁面或目前未開通對應能力，請返回其他可用頁面，或聯絡管理員開通權限。',
        nav_go_back: '返回可用頁面',
        branding_title: '品牌設定',
        branding_subtitle: '設定控制台的品牌標識與說明',
        branding_instance: '目前實例',
        branding_brand_info: '品牌資訊',
        branding_preview: '即時預覽',
        branding_logo: 'Logo',
        branding_upload_logo: '上傳 Logo',
        branding_use_default_logo: '使用預設 Logo',
        branding_logo_hint: 'PNG / JPG / WebP，最大 2MB',
        branding_brand_name: '品牌名稱',
        branding_logo_desc: 'Logo 描述',
        branding_logo_desc_placeholder: '品牌標識旁或下方展示的簡短說明，可留空',
        branding_logo_desc_hint: '可留空，最多 100 字',
        branding_name_placeholder: '品牌名稱，1～32 字',
        branding_sidebar: '側邊欄',
        branding_login: '登入頁',
        branding_welcome: '對話歡迎頁',
        branding_light: '淺色',
        branding_dark: '深色',
        branding_preview_hint: '預覽效果，儲存後生效',
        branding_cancel: '取消變更',
        branding_save: '儲存設定',
        branding_reset_all: '恢復全部預設',
        branding_reset_logo: '恢復預設 Logo',
        branding_saved: '品牌設定已儲存',
        branding_unsaved: '有未儲存變更',
        branding_saved_state: '已儲存',
        branding_readonly: '唯讀',
        branding_readonly_reason: '請先設定存取密碼後方可編輯品牌',
        branding_enterprise_unavailable: '企業品牌授權與審計尚未接入，暫不可修改',
        branding_storage_corrupt: '品牌設定損壞，目前顯示復原預覽。請重新讀取，或明確恢復全部預設；原件將保留。',
        branding_loading: '載入中…',
        branding_retry: '重試',
        branding_load_failed: '載入失敗',
        branding_conflict: '品牌設定已被其他人修改',
        branding_reload: '重新讀取已發佈值',
        branding_save_failed: '儲存失敗',
        login_select_tenant: '選擇租戶',
        login_enter_tenant: '進入',
        tenant_title: '租戶管理',
        tenant_create: '建立租戶',
        tenant_search_placeholder: '搜尋租戶名稱 / 編碼',
        tenant_loading: '載入中…',
        tenant_empty: '暫無租戶',
        tenant_version_label: '版本',
        users_title: '用戶管理',
        member_create: '新增成員',
        member_section_account: '帳號資訊',
        member_section_role: '角色與狀態',
        member_section_tenants: '所屬租戶',
        member_tenants: '租戶',
        member_tenants_title: '調整所屬租戶',
        admin_field_tenants: '目標租戶', admin_field_tenants_hint: '可選擇多個租戶；新帳號將建立於所選租戶',
        admin_field_tenants_edit_hint: '勾選/取消以增刪所屬租戶（僅限你有管理資格的租戶）',
        member_search_placeholder: '搜尋帳號 / 姓名',
        member_empty: '暫無成員',
        roles_title: '角色權限',
        role_create: '新增角色',
        role_empty: '暫無角色',
        role_permissions_label: '權限',
        org_title: '組織架構',
        dept_create: '新增部門',
        org_empty: '暫無部門',
        load_error: '載入失敗',
        active: '啟用',
        inactive: '停用',
        unsaved_changes_warning: '有未儲存的變更，確定離開？',
        create_not_available: '該操作暫未開放',
        // identity-admin CRUD forms (task: wire create/edit/delete)
        admin_save: '儲存', admin_create: '建立', admin_edit: '編輯', admin_delete: '刪除', admin_saved: '已儲存',
        admin_deleted: '已刪除', admin_save_failed: '儲存失敗',
        admin_permissions_load_failed: '權限目錄載入失敗，請重新開啟重試',
        admin_required_field: '請填寫必填欄位',
        admin_conflict: '已被他人修改，已重新整理清單，請重試',
        admin_field_code: '編碼', admin_field_code_hint: '小寫字母/數字/連字號，建立後不可改',
        admin_field_name: '名稱',
        admin_field_shared_root: '共用根目錄', admin_field_shared_root_hint: '租戶資料目錄的絕對路徑',
        admin_field_admin_username: '管理員帳號',
        admin_field_admin_display: '管理員顯示名稱',
        admin_field_admin_password: '管理員密碼', admin_field_password_hint: '至少 8 位，避免使用弱密碼',
        admin_field_recent_password: '目前密碼確認', admin_field_recent_password_hint: '重新輸入你的登入密碼以授權本次操作',
        admin_field_active: '啟用',
        admin_field_username: '帳號', admin_field_username_hint: '3-64 位字母/數字/._-',
        admin_field_display_name: '顯示名稱',
        admin_field_temp_password: '臨時密碼',
        admin_field_roles: '角色', admin_field_roles_hint: '預設勾選成員',
        admin_field_roles_edit_hint: '未勾選時將重設為預設成員（目前角色未載入）',
        admin_field_department: '部門', admin_field_department_hint: '可選', admin_field_department_none: '無部門',
        admin_field_position: '職位',
        admin_field_parent: '上級部門', admin_field_parent_none: '無（根目錄）',
        admin_field_sort_order: '排序',
        admin_field_permissions: '權限', admin_field_permissions_hint: '從目錄中選擇權限點',
        admin_field_resource_grants: '資源授權',
        admin_field_resource_grants_hint: '為角色分配選單 / 技能 / 工具 / 模型 / 智能體的可存取資源',
        admin_field_model_defaults: '模型預設值',
        admin_field_model_defaults_hint: '為已接通能力選擇角色預設模型，須屬於已授權的模型',
        admin_field_model_grants: '模型授權',
        admin_field_model_grants_hint: '為租戶分配可供角色/會話使用的模型',
        admin_resources_selected: '已選 {n} 項',
        admin_resources_none: '未選擇資源',
        admin_resource_manage: '管理資源',
        admin_resource_search_placeholder: '搜尋資源…',
        admin_resource_selectall: '全選當前頁',
        admin_resource_clear: '清空',
        admin_resource_model_capabilities: '能力',
        admin_resource_kind_menu: '選單',
        admin_resource_kind_skill: '技能',
        admin_resource_kind_tool: '工具',
        admin_resource_kind_model: '模型',
        admin_resource_kind_agent: '智慧體',
        admin_field_platform_admin: '平台管理員',
        admin_field_admin_user_id: '管理員帳號 ID', admin_field_admin_user_id_hint: '已有有效帳號的使用者 ID',
        admin_tenant_admin: '管理員',
        admin_tenant_roles: '角色',
        admin_tenant_admin_edit: '設定租戶管理員',
        admin_reset: '重設密碼', admin_reset_do: '確認重設', admin_reset_confirm: '確定重設「{name}」的密碼？',
        admin_reset_temp_result: '一次性臨時密碼', admin_forbidden: '無權限',
        admin_prev: '上一頁', admin_next: '下一頁',
        admin_total_label: '共', admin_page_label: '第',
        filter_all: '全部', filter_restricted: '待改密',
        platform_title: '平台帳號', platform_admin_only: '僅平台管理員可見',
        platform_search_placeholder: '搜尋平台帳號',
        platform_user_empty: '暫無平台帳號',
        platform_user_edit_title: '編輯平台帳號',
        platform_admin_badge: '平台管理員',
        admin_tab_members: '租戶成員', admin_tab_platform: '平台帳號',
        tenant_edit_title: '編輯租戶',
        member_edit_title: '編輯成員',
        role_edit_title: '編輯角色',
        role_tab_basic: '基本資訊',
        role_editor_back: '返回角色列表',
        role_dirty_pill: '有未儲存變更',
        role_section_basic: '基本資訊',
        role_basic_hint: '填寫角色標識；下方設定功能權限。資源與模型在其他 Tab，儲存時一併提交。',
        role_perm_search_placeholder: '搜尋權限…',
        role_perm_select_group: '全選本組',
        role_perm_clear_group: '清空本組',
        role_section_model_assign: '可分配模型',
        role_model_assign_hint: '勾選該角色可使用的模型；預設模型只能從已選項中選擇。',
        role_editor_foot_hint: '切換 Tab 不丟草稿 · 離開前若有改動會確認',
        role_create_sub: '建立後編碼不可修改',
        role_copy_from: '從 {name} 複製',
        role_copy_suffix: '（副本）',
        dept_edit_title: '編輯部門',
        admin_delete_confirm_role: '確定刪除角色「{name}」嗎？',
        admin_delete_confirm_dept: '確定刪除部門「{name}」嗎？',
        role_builtin: '內建',
        role_members_label: '成員',
        role_view_members: '查看成員',
        role_copy: '複製', role_copy_title: '複製角色',
        org_cycle_rejected: '組織關係存在迴圈，操作被拒絕',
        audit_title: '身分稽核',
        audit_scope_hint: '平台管理員查看全部，租戶管理員僅目前租戶',
        audit_actor_placeholder: '操作者',
        audit_action_placeholder: '動作',
        audit_apply: '篩選',
        audit_empty: '暫無稽核記錄',
        audit_result_success: '成功',
        audit_result_denied: '拒絕',
        branding_reset_confirm_title: '恢復全部預設設定',
        branding_reset_confirm_body: '將重設 Logo、品牌名稱和 Logo 描述為內建預設值，確認操作？',
        branding_reset_confirm_ok: '確認恢復',
        branding_reset_confirm_cancel: '取消',
        branding_dirty_leave_title: '存在未儲存變更',
        branding_dirty_leave_body: '品牌設定尚未儲存，將要離開此頁，是否放棄變更？',
        branding_dirty_leave_ok: '放棄變更並離開',
        branding_dirty_leave_cancel: '留在本頁',
        branding_unsaved_warn: '品牌設定還有未儲存的變更',
        branding_save_pending: '儲存結果未確認',
        branding_save_pending_body: '無法確認伺服器是否已發佈，請重新讀取已發佈品牌後重試。',
        branding_save_pending_ok: '重新讀取',
        branding_added: '已新增新品牌',
        branding_image_too_large: '圖片不能超過 2MB',
        branding_invalid_image: '圖片無效',
        branding_asset_fallback: '品牌圖片無法載入',
        branding_browser_title: '控制台',
        branding_logo_alt: '品牌 Logo',
        agents_page_title: '智慧體設定', agents_page_desc: '管理團隊中的智慧體成員',
        agents_create: '建立智慧體',
        agents_name_placeholder: '智慧體名稱',
        agents_name_required: '請填寫名稱',
        agents_stale: '列表已更新，請重新整理後再試',
        agents_id_placeholder: '留空則自動產生',
        agents_id_tip: '智慧體的唯一識別碼，建立後不可修改。僅支援小寫英文、數字與連字號（-），如 coding-agent。留空則依名稱自動產生。',
        agents_id_invalid: 'ID 需以字母或數字開頭，僅支援字母、數字、底線與連字號，最長 64 位',
        agents_avatar: '頭像',
        agents_tab_profile: '概況',
        agents_tab_skills: '能力',
        agents_tab_files: '核心檔案',
        agents_core_edit: '編輯',
        agents_core_preview: '預覽',
        agents_core_file_agent: '智慧體設定',
        agents_core_file_user: '使用者資訊',
        agents_core_file_rule: '工作空間規則',
        agents_core_file_memory: '長期記憶',
        agents_default: '預設',
        agents_archived: '已封存',
        agents_chat: '開始對話',
        start_chat: '開始對話',
        agent_workbench_title: '智慧體',
        agent_workbench_desc: '選擇已設定的智慧體開始對話',
        agent_workbench_refresh: '重新整理',
        agent_workbench_loading: '載入中…',
        agent_workbench_empty: '暫無可用智慧體',
        agent_workbench_failed: '載入失敗',
        agent_workbench_retry: '重試',
        agent_unavailable: '暫不可用',
        agent_cannot_run: '目前不可運行',
        agent_target_unavailable: '該智慧體已不可用，請重新整理清單後重試',
        agent_starting: '正在進入…',
        agent_start_failed: '暫時無法開始對話，請重試',
        agent_runtime_not_enabled: '目前版本尚未開放對話',
        agent_permission_denied: '暫無對話權限，請聯絡管理員開通',
        agents_delete: '刪除',
        agents_delete_title: '刪除智慧體',
        agents_delete_confirm: '確定刪除智慧體「{name}」嗎？其工作空間與會話將一併移除，且無法復原。',
        agents_pick_hint: '選擇智慧體',
        agents_clone_label: '從已有智慧體複製',
        agents_clone_hint: '複製其設定、技能與知識作為起點',
        agents_avatar_upload: '上傳圖片',
        agents_clone_none: '空白',
        agents_clone_from: '{name}',
        agents_name: '名稱',
        agents_saved: '已儲存',
        agents_save_failed: '儲存失敗',
        agents_no_desc: '暫無職責',
        agents_description: '職責',
        agents_description_placeholder: '該智慧體負責哪些工作、在什麼場景被使用',
        agents_description_hint: '用於多智慧體協作時的任務分配',
        agents_model: '預設模型',
        agents_model_follows_global: '跟隨全域設定',
        agents_model_default_hint: '預設使用主模型，於「模型設定」中修改。',
        agents_position: '職位',
        agents_position_placeholder: '如：採購專員',
        agents_category: '分類',
        agents_category_none: '未分類',
        agents_tags: '標籤',
        agents_tags_placeholder: '標籤以逗號分隔，如：供應商, 招標',
        agents_greeting: '問候語',
        agents_persona: '人設摘要',
        agents_persona_hint: '注入系統提示的員工人設說明',
        agents_scene: '關聯場景',
        agents_scene_none: '無關聯場景',
        agents_skills_all: '使用全部已安裝技能',
        agents_skills_pick: '只啟用勾選的技能',
        agents_skills_label: '技能',
        agents_sops_label: 'SOP 流程',
        agents_sops_hint: '為員工綁定可觀測的 SOP 執行流程 ID（僅作能力清單展示，不驅動狀態機）。',
        agents_sops_placeholder: 'SOP ID',
        agents_sops_add: '新增',
        agents_tools_label: '工具目錄',
        agents_tools_hint: '勾選「允許」僅啟用白名單；勾選「拒絕」從可用工具中剔除。兩者互斥。',
        agents_tools_none_hint: '未啟用白名單/黑名單時，該員工可使用全部已安裝工具。',
        agents_allow: '允許',
        agents_deny: '拒絕',
        agents_knowledge: '知識庫',
        agents_knowledge_shared: '共享',
        agents_knowledge_own: '獨立',
        agents_knowledge_hint: '共享：與團隊讀寫同一個知識庫\n獨立：擁有專屬知識庫，互不影響',
        agents_knowledge_working: '處理中…',
        agents_knowledge_failed: '切換失敗',
        agents_empty: '還沒有智慧體。建立一個，開始組團隊。',
        agents_select_hint: '從左側選擇一個智能體進行設定',
        agents_pick_tip: '切換當前智能體',
        team_members: '當前會話成員',
        team_invite: '新增到目前會話',
        team_remove: '移出這個會話',
        composer_agent_owner: '主',
        channel_bound_agent: '綁定智慧體',
        channel_bound_default: '預設',
        channel_bound_agent_hint: '第一個為預設智慧體，負責接收訊息並可委派給其他成員',
        channel_team_none: '未選擇',
        channel_team_no_candidates: '暫無可選的智慧體',
        settings_tab_basic: '基礎設定',
        settings_tab_models: '模型設定',
        knowledge_shared_hint: '知識庫預設全員共享，在側欄「知識」查看和編輯。',
        menu_memory: '記憶管理', menu_knowledge: '知識庫', menu_scenes: '場景應用', menu_channels: '訊息管道', menu_tasks: '定時任務',
        menu_logs: '執行日誌', menu_todo: '我的待辦', menu_scenarios: '場景應用',
        models_title: '模型管理',
        models_desc: '統一管理對話、影像、語音、向量、搜尋能力',
        models_section_vendors: '廠商憑據',
        models_section_vendors_desc: '一處設定，多個模型能力共享',
        models_section_capabilities: '模型能力',
        models_add_vendor: '新增廠商',
        models_provider: '廠商',
        models_model: '模型',
        models_voice: '音色',
        models_configured: '已設定',
        models_not_configured: '未設定',
        models_pick_to_configure: '選擇以設定',
        models_clear_credential: '清除憑據',
        models_base_default_hint: '留空將使用官方預設地址',
        models_base_default: '預設',
        models_custom_vendor_label: '自定義',
        models_custom_name: '名稱',
        models_custom_delete: '刪除',
        models_custom_delete_confirm_title: '刪除自定義廠商',
        models_custom_delete_confirm_msg: '確定刪除該自定義廠商嗎？此操作無法撤銷。',
        models_custom_name_required: '請填寫名稱',
        models_custom_base_required: '請填寫 API Base',
        models_custom_edit_title: '編輯自定義廠商',
        models_custom_add_title: '新增自定義廠商',
        models_capability_chat: '主模型',
        models_capability_chat_desc: '用於基礎對話和 Agent 推理',
        models_capability_chat_fallback: '主模型兜底',
        models_capability_chat_fallback_desc: '僅在主模型徹底失敗（重試耗盡）後接管',
        models_fallback_enable: '啟用兜底模型',
        models_fallback_config: '兜底模型',
        models_fallback_config_tip: '設定主模型兜底：主模型徹底失敗後接管',
        models_fallback_modal_title: '主模型兜底',
        models_fallback_modal_desc: '當主模型重試次數用盡仍然失敗時，自動切換到兜底模型完成本輪回覆',
        models_fallback_badge_on: '兜底已啟用',
        models_capability_vision: '影像理解',
        models_capability_vision_desc: '識別圖片內容，用於影像識別工具',
        models_capability_image: '影像生成',
        models_capability_image_desc: '生成圖片，用於影像生成技能',
        models_auto_using: '當前優先使用',
        models_capability_asr: '語音識別',
        models_capability_asr_desc: '語音轉文字',
        models_capability_tts: '語音合成',
        models_capability_tts_desc: '文字轉語音',
        models_capability_embedding: '向量',
        models_capability_embedding_desc: '用於記憶與知識的向量化檢索',
        models_capability_search: '聯網搜尋',
        models_capability_search_desc: '實時網頁檢索能力，用於搜尋工具',
        models_strategy_auto: '自動',
        models_search_strategy_label: '策略',
        models_search_strategy_fixed: '指定',
        models_search_strategy_auto_hint: '從已設定廠商中自動選擇',
        models_search_strategy_fixed_hint: '指定使用搜尋廠商',
        models_pending_config: '待設定',
        models_search_available_label: '可用搜尋廠商：',
        models_search_none_configured: '暫未啟用任何搜尋廠商，點選新增',
        models_search_add_provider: '新增廠商',
        models_search_add_desc: '選擇一個搜尋廠商進行設定',
        models_search_bocha_title: '設定博查 API Key',
        models_search_bocha_desc: '前往博查開放平臺建立 API Key',
        models_search_anysearch_title: '設定 AnySearch API Key',
        models_search_anysearch_desc: '前往 anysearch.com 控制台建立 API Key',
        models_search_serply_title: '設定 Serply API Key',
        models_search_serply_desc: '前往 serply.io 控制台建立 API Key',
        models_search_edit_hint: '點選修改設定',
        models_unavailable: '不可用',
        models_set_via_env: '透過環境變數啟用',
        models_dim_label: '維度',
        models_save_success: '已儲存',
        models_save_failed: '儲存失敗',
        models_cleared: '已清除',
        models_clear_failed: '清除失敗',
        models_embedding_change_title: '更改向量模型',
        models_embedding_change_msg: '切換向量模型後，已有索引將失效，需要重建。是否繼續？',
        models_embedding_saved_title: '向量模型已更新',
        models_embedding_saved_msg: '請在聊天框輸入 /memory rebuild-index 重建索引。',
        models_embedding_saved_ok: '去執行',
        models_pick_provider: '待選擇',
        models_manage_api_key: '管理 API Key',
        models_clear_confirm_title: '清除廠商憑據',
        models_clear_confirm_msg: '確認清除該廠商的 API Key 與 Base URL 嗎？相關能力將不再可用。',
        cancel: '取消',
        save: '儲存',
        ok: '確定',
        knowledge_title: '知識庫', knowledge_desc: '瀏覽和探索你的知識庫',
        knowledge_tab_docs: '檔案', knowledge_tab_graph: '圖譜',
        knowledge_loading: '載入知識庫中...', knowledge_loading_desc: '知識頁面將顯示在這裡',
        knowledge_select_hint: '選擇一個檔案檢視', knowledge_empty_hint: '暫無知識頁面',
        knowledge_empty_guide: '在對話中傳送檔案、連結或主題給 Agent，它會自動整理到你的知識庫中。',
        knowledge_go_chat: '開始對話',
        knowledge_new: '新建',
        knowledge_new_category: '新建分類',
        knowledge_new_document: '新建檔案',
        knowledge_import_documents: '匯入檔案',
        welcome_subtitle: '我可以幫你解答問題、管理電腦、創造和執行技能，並透過<br>長期記憶和知識庫不斷成長',
        example_sys_title: '系統管理', example_sys_text: '檢視工作空間裡有哪些檔案',
        example_task_title: '定時任務', example_task_text: '1分鐘後提醒我檢查伺服器',
        example_code_title: '程式設計助手', example_code_text: '搜尋AI資訊並生成視覺化網頁報告',
        example_knowledge_title: '知識庫', example_knowledge_text: '檢視知識庫當前檔案情況',
        example_skill_title: '技能系統', example_skill_text: '檢視所有支援的工具和技能',
        example_web_title: '指令中心', example_web_text: '檢視全部命令',
        slash_help: '顯示命令幫助',
        slash_status: '檢視執行狀態',
        slash_context: '檢視對話上下文',
        slash_context_clear: '清除對話上下文',
        slash_compact: '壓縮較早的對話以釋放上下文',
        slash_skill_list: '檢視已安裝技能',
        slash_skill_list_remote: '瀏覽技能廣場',
        slash_skill_search: '搜尋技能',
        slash_skill_install: '安裝技能 (名稱或 GitHub URL)',
        slash_skill_uninstall: '解除安裝技能',
        slash_skill_info: '檢視技能詳情',
        slash_skill_enable: '啟用技能',
        slash_skill_disable: '禁用技能',
        slash_memory_dream: '手動觸發記憶蒸餾 (可指定天數, 預設3)',
        slash_knowledge: '檢視知識庫統計',
        slash_knowledge_list: '檢視知識庫檔案樹',
        slash_knowledge_on: '開啟知識庫',
        slash_knowledge_off: '關閉知識庫',
        slash_config: '檢視當前設定',
        slash_cancel: '中止當前正在執行的 Agent 任務',
        slash_steer: '向當前正在執行的 Agent 任務注入引導指令',
        steer_active: '引導當前任務',
        slash_logs: '檢視最近日誌',
        slash_version: '檢視版本',
        input_placeholder: '輸入訊息，/ 使用指令，@ 引用智慧體或檔案',
        config_title: '設定管理', config_desc: '管理模型和 Agent 設定',
        config_model: '模型設定', config_agent: 'Agent 設定',
        config_language: '語言', config_language_hint: '介面展示、命令文案、系統提示詞等使用的語言（與右上角切換同步）',
        config_system: '系統',
        config_task_notify: '任務通知', config_task_notify_hint: '視窗在背景且任務完成或失敗時發送瀏覽器通知，點擊可跳轉會話',
        config_task_notify_sound: '通知聲音', config_task_notify_sound_hint: '通知開啟時可單獨關閉提示音',
        config_task_notify_blocked: '系統通知已被瀏覽器封鎖，請點擊網址列左側圖示 → 通知 → 允許後重新整理頁面',
        notify_task_done: '任務完成',
        notify_task_error: '任務失敗',
        config_model_advanced: '高階設定',
        config_channel: '管道設定',
        config_agent_enabled: 'Agent 模式',
        config_max_tokens: '最大上下文 Token', config_max_tokens_hint: '對話中 Agent 能輸入的最大 Token 長度，超過後會智慧壓縮處理',
        config_max_turns: '最大記憶輪次', config_max_turns_hint: '一問一答為一輪，超過後會智慧壓縮處理',
        config_max_steps: '最大執行步數', config_max_steps_hint: '單次對話中 Agent 最多呼叫工具的次數',
        config_enable_thinking: '深度思考', config_enable_thinking_hint: '是否啟用深度思考模式',
        config_reasoning_effort: '思考強度', config_reasoning_effort_hint: '按目前模型廠商支援的原生枚舉傳送',
        config_subagent: '子 Agent', config_subagent_hint: '把可獨立完成的任務交給子 Agent，多個任務並行執行，只把結論帶回主對話',
        config_self_evolution: '自主進化', config_self_evolution_hint: '會話空閒後自動覆盤，沉澱記憶、最佳化技能、處理未完成事項',
        evolution_badge: '自主學習',
        config_channel_type: '管道型別',
        config_provider: '模型廠商', config_model_name: '模型',
        config_custom_model_hint: '輸入自定義模型名稱',
        config_save: '儲存', config_saved: '已儲存',
        config_save_error: '儲存失敗',
        config_custom_option: '自定義',
        config_custom_tip: '介面需遵循 OpenAI API 協議',
        config_security: '安全設定', config_password: '訪問密碼',
        config_password_hint: '留空則不啟用密碼保護',
        config_permission: '預設權限',
        config_permission_hint: '新會話的預設權限範圍，決定 Agent 能修改哪些檔案、能執行哪些命令',
        config_permission_desc: '新會話預設使用該權限；單個會話可在輸入框下方單獨調整',
        config_password_changed: '密碼已更新',
        config_password_cleared: '密碼已清除',
        config_password_security_warning: '⚠️ 警告：目前密碼為空且對外連接埠開放，建議重啟服務，或檢查是否調整監聽位址綁定。',
        skills_title: '技能管理', skills_desc: '檢視、啟用或禁用 Agent 工具和技能', skills_hub_btn: '探索技能廣場',
        skills_loading: '載入技能中...', skills_loading_desc: '技能載入後將顯示在此處',
        tools_section_title: '內建工具', tools_loading: '載入工具中...',
        skills_section_title: '技能', skill_enable: '啟用', skill_disable: '禁用',
        skill_toggle_error: '操作失敗，請稍後再試',
        skill_open_hint: '點擊檢視技能內容',
        skill_back: '返回列表',
        skill_load_failed: '讀取技能內容失敗',
        skill_builtin_readonly: '內建技能不可編輯（重啟會覆蓋）',
        memory_title: '記憶管理', memory_desc: '檢視 Agent 記憶檔案和內容',
        memory_tab_files: '記憶檔案', memory_tab_dreams: '自主進化',
        memory_loading: '載入記憶檔案中...', memory_loading_desc: '記憶檔案將顯示在此處',
        memory_back: '返回列表',
        memory_col_name: '檔名', memory_col_type: '型別', memory_col_size: '大小', memory_col_updated: '更新時間',
        channels_title: '訊息管道', channels_desc: '管理已接入的訊息管道',
        channels_add: '接入管道', channels_disconnect: '斷開',
        channels_save: '儲存設定', channels_saved: '已儲存', channels_save_error: '儲存失敗',
        channels_restarted: '已儲存並重啟',
        channels_connect_btn: '接入', channels_cancel: '取消',
        channels_select_placeholder: '選擇要接入的管道...',
        channels_empty: '暫未接入任何管道', channels_empty_desc: '點選右上角「接入管道」按鈕開始設定',
        channels_disconnect_confirm: '確認斷開該管道？設定將保留但管道會停止執行。',
        channels_connected: '已接入', channels_connecting: '接入中...',
        weixin_scan_title: '微信掃碼登入', weixin_scan_desc: '請使用微信掃描下方二維碼',
        weixin_scan_loading: '正在獲取二維碼...', weixin_scan_waiting: '等待掃碼...',
        weixin_scan_scanned: '已掃碼，請在手機上確認', weixin_scan_expired: '二維碼已過期，正在重新整理...',
        weixin_scan_success: '登入成功，正在啟動管道...', weixin_scan_fail: '獲取二維碼失敗',
        weixin_qr_tip: '二維碼約2分鐘後過期',
        wecom_scan_btn: '掃碼建立企微機器人', wecom_scan_desc: '使用企業微信掃碼，一鍵建立智慧機器人',
        wecom_scan_success: '建立成功，正在啟動管道...',
        wecom_scan_fail: '建立失敗',
        wecom_mode_scan: '掃碼接入', wecom_mode_manual: '手動填寫',
        feishu_scan_btn: '一鍵建立飛書應用',
        feishu_scan_desc: '使用飛書 App 掃碼，自動建立應用並預置全部許可權與事件訂閱',
        feishu_scan_replace_desc: '使用飛書 App 掃碼建立新機器人，將覆蓋當前的 App ID / Secret',
        feishu_scan_loading: '正在向飛書申請二維碼...',
        feishu_scan_waiting: '等待掃碼...',
        feishu_scan_tip: '二維碼 10 分鐘內有效，僅供一次掃描',
        feishu_scan_open_link: '或點選此處在瀏覽器中開啟',
        feishu_scan_success: '應用建立成功，正在啟動管道...',
        feishu_scan_expired: '二維碼已過期，請重試',
        feishu_scan_denied: '已取消授權',
        feishu_scan_fail: '建立失敗',
        feishu_scan_retry: '重試',
        feishu_sdk_downloading: '正在下載飛書元件...',
        feishu_sdk_downloading_tip: '首次啟用需要下載，約 1MB，稍後自動繼續',
        feishu_mode_scan: '掃碼建立', feishu_mode_manual: '手動填寫',
        tasks_title: '定時任務', tasks_desc: '檢視和管理定時任務',
        todo_title: '待辦事項', todo_subtitle: '處理需要你跟進的事項',
        todo_add_btn: '新增待辦', todo_save: '儲存', todo_cancel: '取消',
        todo_filter_all: '全部', todo_filter_open: '未完成', todo_filter_pending: '待處理', todo_filter_progress: '處理中', todo_filter_done: '已完成', todo_filter_cancel: '已取消',
        todo_only_overdue: '只看逾期', todo_search_placeholder: '搜尋待辦…', todo_clear_due: '清空',
        todo_status_all: '全部', todo_status_open: '未完成', todo_status_pending: '待處理', todo_status_in_progress: '處理中', todo_status_completed: '已完成', todo_status_cancelled: '已取消',
        todo_kind_general: '普通事項', todo_kind_input_required: '補充資料', todo_kind_confirmation: '方案確認', todo_kind_review: '結果驗收',
        todo_priority_low: '低', todo_priority_normal: '普通', todo_priority_high: '高',
        todo_edit_title: '編輯待辦', todo_create_title: '新增待辦',
        todo_field_title: '標題', todo_field_desc: '說明', todo_field_kind: '分類', todo_field_priority: '優先級', todo_field_due: '截止時間',
        todo_by: '建立於', todo_updated: '更新於', todo_completed: '完成於', todo_created_by: '來源',
        todo_empty_open: '暫無未完成的待辦', todo_empty_pending: '暫無待處理事項', todo_empty_progress: '暫無處理中的事項',
        todo_empty_done: '暫無已完成事項', todo_empty_cancel: '暫無已取消事項', todo_empty_all: '還沒有待辦事項',
        todo_empty_search: '沒有符合的待辦', todo_empty_overdue: '暫無逾期事項',
        todo_disabled_banner: '待辦功能未開啟，可在設定中啟用 todo_enabled。',
        todo_unauthorized_banner: '請先登入後再使用待辦功能。',
        todo_load_failed: '載入失敗，請稍後重試。',
        todo_banner_disabled: '待辦功能未開啟', todo_banner_unauthorized: '未認證',
        todo_empty_title: '待辦標題', todo_empty_subtitle: '為空時自動填充',
        todo_confirm_discard: '有未儲存的修改，確定要放棄嗎？',
        todo_confirm_complete: '確定標記為已完成？', todo_confirm_cancel: '確定取消該待辦？', todo_confirm_reopen: '確定重新開啟？',
        todo_saved: '已儲存', todo_save_failed: '儲存失敗', todo_created: '已建立',
        todo_action_start: '開始處理', todo_action_complete: '完成', todo_action_cancel: '取消', todo_action_reopen: '重新開啟', todo_action_edit: '編輯', todo_action_events: '處理歷史',
        todo_detail_title: '待辦詳情', todo_detail_source: '來源', todo_detail_history: '處理歷史', todo_detail_history_empty: '暫無處理記錄',
        todo_detail_note: '處理說明', todo_detail_note_empty: '無說明',
        todo_due_overdue: '已逾期', todo_due_soon: '即將到期', todo_due_none: '無截止時間',
        todo_pagination_prev: '上一頁', todo_pagination_next: '下一頁',
        todo_source_manual: '手動建立', todo_source_conversation: '會話來源',
        todo_load_error: '載入失敗', todo_retry: '重試',
        tasks_coming: '即將推出', tasks_coming_desc: '定時任務管理功能即將在此提供',
        tasks_unavailable: '定時任務暫不可用', tasks_unavailable_desc: '當前身分模式未開放定時任務，請在 legacy 模式下使用，或由管理員適配後開啟。',
        task_add_btn: '新增任務',
        task_edit_title: '編輯定時任務',
        task_add_title: '新增定時任務',
        task_name: '任務名稱',
        task_enabled: '啟用任務',
        task_schedule_type: '排程型別',
        task_schedule_cron: 'Cron 表示式',
        task_schedule_interval: '固定間隔',
        task_schedule_once: '一次性任務',
        task_cron_expression: 'Cron 表示式',
        task_cron_hint: '格式: 分 時 日 月 周，例如 "0 9 * * *" 表示每天 9:00',
        task_interval_seconds: '間隔秒數',
        task_interval_hint: '最小 60 秒，例如 3600 表示每小時執行一次',
        task_once_time: '執行時間',
        task_action_type: '動作型別',
        task_action_send_message: '傳送訊息',
        task_action_agent_task: 'AI 任務',
        task_channel_type: '管道型別',
        task_channel_hint: '選擇定時訊息傳送的管道',
        task_message_content: '訊息內容',
        task_task_description: '任務描述',
        task_delete_btn: '刪除任務',
        task_delete_confirm_title: '刪除定時任務',
        task_delete_confirm_msg: '確定刪除該定時任務嗎？此操作無法撤銷。',
        task_run_now: '立即執行',
        task_next_run: '下次執行',
        task_run_confirm_title: '立即執行任務',
        task_run_confirm_msg: '該任務會立即向已設定的通道和接收者傳送內容。是否繼續？',
        task_run_started: '已開始執行',
        task_run_failed: '執行失敗',
        logs_title: '執行日誌', logs_desc: '實時日誌輸出 (run.log)',
        logs_live: '實時', logs_coming_msg: '日誌流即將在此提供。將連線 run.log 實現類似 tail -f 的實時輸出。',
        new_chat: '新對話',
        new_team_chat: '多智慧體對話',
        new_team_chat_hint: '選擇參與本次對話的智慧體，第一個為會話的預設智慧體。',
        new_team_chat_owner: '預設',
        new_team_chat_start: '開始對話',
        new_team_chat_min: '至少選擇兩個智慧體',
        session_history: '歷史對話',
        history_desc: '找到之前的對話，接著聊',
        history_search_placeholder: '搜尋會話標題',
        history_search_clear: '清空搜尋',
        history_search_loading: '正在搜尋…',
        history_search_empty: '沒有符合的會話，試試其他標題關鍵詞',
        history_search_count: '找到 {count} 個會話',
        history_search_limit: '搜尋詞最多 100 個字元',
        history_search_unsupported: '目前服務尚未支援標題搜尋，請更新服務後重試',
        history_refresh: '重新整理歷史會話',
        history_current: '目前會話',
        history_more: '更多操作',
        session_history_loading: '載入歷史會話中…',
        session_history_empty: '暫無歷史會話',
        session_history_failed: '載入失敗，請重試',
        session_history_retry: '重試',
        session_history_not_enabled: '歷史會話功能目前未開放',
        ws_toggle: '工作空間', ws_tab_preview: '預覽', ws_tab_files: '檔案',
        ws_default_workspace: '預設空間', ws_sel_title: '選擇工作空間',
        ws_sel_default_hint: '使用預設工作空間（~/cow）', ws_sel_recents: '最近使用',
        ws_sel_open: '開啟專案…', ws_sel_new: '新建專案', ws_sel_new_placeholder: '專案名稱',
        ws_sel_create: '建立', ws_sel_up: '上一層',
        ws_sel_new_subtitle: '將在 {root} 下建立新專案目錄', ws_sel_new_hint: '僅填寫專案名稱，不含路徑分隔符',
        ws_sel_name_required: '請輸入專案名稱', ws_sel_name_no_slash: '專案名稱不能包含 / 或 \\',
        ws_sel_open_here: '開啟此目錄', ws_sel_dblclick_hint: '雙擊進入子目錄，單擊選中',
        ws_sel_no_subdirs: '此目錄下沒有子資料夾', ws_sel_drives: '本機',
        ws_open_external: '在新分頁開啟', ws_download: '下載', ws_copy_path: '複製路徑',
        ws_close: '關閉', ws_refresh: '重新整理', ws_preview: '預覽',
        ws_search_placeholder: '搜尋檔案',
        ws_preview_empty: '選擇一個檔案進行預覽',
        ws_preview_failed: '預覽失敗',
        ws_link_not_found: '工作空間中找不到該檔案',
        ws_no_inline_preview: '該類型不支援內嵌預覽',
        ws_empty_dir: '空目錄', ws_no_results: '沒有符合的檔案',
        ws_truncated: '檔案過多，僅顯示部分',
        ws_edit: '編輯', ws_edit_save: '儲存 (Ctrl+S)', ws_edit_cancel: '離開編輯',
        ws_edit_saved: '已儲存',
        ws_edit_load_failed: '開啟編輯器失敗',
        ws_edit_save_failed: '儲存失敗',
        ws_edit_too_large: '檔案過大，無法在面板中編輯',
        ws_edit_unsupported: '該類型不支援編輯',
        ws_edit_encoding: '該檔案不是 UTF-8 編碼，編輯會損壞內容',
        ws_edit_conflict_title: '檔案已被變更',
        ws_edit_conflict_msg: '這個檔案在你編輯期間被變更過（可能是 Agent 寫入的）。覆寫儲存會丟棄磁碟上的新內容。',
        ws_edit_overwrite: '覆寫儲存',
        ws_edit_discard_title: '放棄未儲存的變更？',
        ws_edit_discard_msg: '目前檔案有未儲存的變更，繼續操作會遺失這些內容。',
        ws_edit_discard_ok: '放棄變更',
        today: '今天', yesterday: '昨天', earlier: '更早',
        session_pinned_group: '置頂',
        pin_session: '置頂',
        unpin_session: '取消置頂',
        project_rename: '重新命名專案',
        project_delete: '刪除專案',
        project_rename_title: '重新命名專案',
        project_delete_title: '刪除專案',
        project_delete_confirm: '確認刪除專案「{name}」？僅移除專案記錄，磁碟上的檔案不會被刪除，其下會話將回到預設空間。',
        perm_menu_title: '本次會話權限',
        perm_read_only: '唯讀',
        perm_workspace_write: '工作區可寫',
        perm_full_access: '全部可存取',
        perm_read_only_desc: '只能查看和分析，不修改任何檔案',
        perm_workspace_write_desc: '在目前工作空間內自由讀寫，空間之外的寫入會被拒絕',
        perm_full_access_desc: '不加限制，可修改任意位置（目前預設）',
        perm_follow_global: '跟隨全域設定',
        perm_tip: '權限：{name}',
        perm_denied_hint: '目前權限為「{name}」，此操作被拒絕。',
        perm_denied_action: '調整權限',
        model_menu_title: '本次會話模型',
        model_follow_global: '跟隨全域設定',
        model_follow_agent: '跟隨智慧體預設模型',
        model_tip: '模型：{name}',
        model_unset: '未設定',
        session_settings_failed: '設定失敗，請重試',
        delete_session_confirm: '確認刪除該會話？所有訊息將被清除。',
        delete_session_title: '刪除會話',
        rename_session: '重新命名',
        delete_message_confirm: '確認刪除這條訊息？',
        delete_message_title: '刪除訊息',
        edit_disabled_reply_active: '正在生成回覆，暫時無法編輯。',
        delete_disabled_reply_active: '正在生成回覆，暫時無法刪除。',
        untitled_session: '新對話',
        context_cleared: '— 以上內容已從上下文中移除 —',
        tip_new_chat: '新建對話',
        tip_clear_context: '清除上下文',
        tip_attach: '新增附件',
        tip_cancel: '中止',
        tip_cancelled: '已中止',
        attach_menu_file: '上傳檔案',
        mic_idle_title: '點選錄音 / 再按一次結束',
        mic_recording_title: '錄音中，再次點選結束',
        mic_busy_title: '識別中…',
        mic_permission_denied: '無法訪問麥克風，請檢查瀏覽器許可權',
        mic_too_short: '錄音太短，請重試',
        mic_error: '語音識別失敗',
        speak_msg: '朗讀這段回覆',
        voice_reply_mode_label: '語音回覆策略',
        voice_reply_off: '關閉',
        voice_reply_if_voice: '僅語音問/語音答',
        voice_reply_always: '總是語音回覆',
        attach_menu_folder: '上傳資料夾',
        confirm_yes: '確認',
        confirm_cancel: '取消',
        error_send: '傳送失敗，請稍後再試。', error_timeout: '請求超時，請再試一次。',
        error_login_required: '登入已失效，請重新登入後傳送。',
        error_tenant_required: '請先選擇目前帳號所屬的租戶，再傳送訊息。',
        error_chat_forbidden: '目前帳號沒有此租戶的對話權限，請聯絡管理員。',
        error_password_required: '請先修改初始密碼，再傳送訊息。',
        thinking_in_progress: '思考中...', thinking_done: '已深度思考', thinking_duration: '耗時',
        edit_message: '編輯訊息',
        regenerate_response: '重新生成',
        edit_save: '儲存併傳送',
        edit_cancel: '取消',
        account_loading: '帳號資訊載入中', account_unavailable: '帳號資訊暫不可用',
        account_local: '本機存取', account_password_mode: '密碼保護', account_public_mode: '免登入模式',
        account_retry: '重新檢查', account_retry_hint: '請重新檢查', account_logout: '登出',
        account_logging_out: '正在登出…', account_logout_unconfirmed: '登出未確認', account_retry_logout: '重試登出',
        account_login: '登入', account_login_hint: '請輸入登入資訊以存取控制台',
        account_credentials_error: '登入資訊有誤，請重試', account_login_failed: '登入未完成，請重試',
        account_menu_profile: '個人資料', account_menu_password: '帳號安全', account_menu_prefs: '個人偏好',
        account_menu_about: '幫助與關於',
        account_profile_title: '個人資料', account_profile_global: '全域帳號',
        account_profile_member: '目前租戶成員', account_profile_display_name: '姓名',
        account_profile_username: '登入帳號', account_profile_platform: '平台身分',
        account_profile_tenant: '目前租戶', account_profile_member_name: '成員姓名',
        account_profile_role: '實際角色', account_profile_department: '部門',
        account_profile_position: '崗位', account_profile_empty: '未設定',
        account_profile_no_tenant: '未加入任何租戶', account_profile_error: '資料讀取失敗',
        account_profile_edit: '編輯資料', account_profile_avatar_hint: '點擊可更換頭像',
        account_profile_error_required: '該項不能為空',
        saved: '已儲存',
        account_password_title: '修改密碼', account_password_note: '修改成功後，其他已登入會話也會失效，需要重新登入。',
        account_password_old: '原密碼', account_password_new: '新密碼', account_password_confirm: '確認新密碼',
        account_password_submit: '提交修改', account_password_invalid_old: '原密碼錯誤',
        account_password_weak: '新密碼不符合要求，請重試', account_password_mismatch: '兩次輸入的新密碼不一致',
        account_password_unknown: '操作未完成，請重試', account_password_done: '密碼已修改，請重新登入',
        account_password_forced_title: '請設定新密碼', account_password_forced_note: '首次登入/臨時密碼需先設定新密碼，完成後才能使用其他功能。',
        account_password_forced_required: '請先完成密碼設定', account_password_forced_logout: '需先設定新密碼才能繼續。確定登出嗎？',
        account_prefs_title: '介面偏好', account_prefs_note: '偏好僅在目前瀏覽器生效，不寫入實例設定。',
        account_prefs_theme: '主題', account_prefs_lang: '語言', account_prefs_light: '淺色',
        account_prefs_dark: '深色', account_prefs_zh: '簡體', account_prefs_hant: '繁體', account_prefs_en: 'EN',
        account_prefs_storage_fail: '瀏覽器儲存不可用，本次修改僅在本頁生效',
        account_tenant_title: '切換租戶', account_tenant_none: '未加入', account_tenant_current: '目前',
        account_tenant_no_available: '未加入任何租戶', account_tenant_invalid: '目標租戶已失效，請重新選擇',
        account_assign_pending: '帳號尚未分配到任何啟用的租戶，暫時無法進入工作台或管理頁面。請聯絡管理員完成租戶分配後點擊重試。',
        account_tenant_single: '目前租戶', account_about_title: '關於', account_about_version_unknown: '版本未取得',
        logout: '登出',
        close: '關閉',
        },
    en: {
        console: 'Console',
        nav_chat: 'Workbench', nav_manage: 'Management', nav_monitor: 'Monitor', nav_system: 'System Settings',
        nav_workbench: 'Workbench', nav_admin_console: 'Admin Console',
        nav_return_workbench: 'Back to Workbench',
        admin_home_title: 'Admin Console',
        admin_home_hint: 'Use the sidebar to manage agents, organization, and platform settings.',
        nav_admin_denied: 'Your account cannot open the admin console.',
        sidebar_history_records: 'Session History',
        sidebar_history_view_all: 'View all',
        sidebar_history_empty: 'No history sessions yet',
        nav_group_agent_dev: 'Agent Development', nav_group_model_access: 'Model & Access',
        nav_group_org_perm: 'Org & Permissions', nav_group_platform_ops: 'Platform Ops',
        menu_chat: 'Chat', menu_agents: 'Agents', menu_config: 'Model Services', menu_agent_config: 'Agent Management', menu_skills: 'Tools & Skills',
        menu_platform: 'System Settings', menu_tenant: 'Tenant Management', menu_system_user: 'Members',
        menu_roles: 'Roles & Permissions', menu_org: 'Organization', menu_branding: 'Branding',
        menu_audit: 'Audit', menu_backup: 'Backup & Upgrade', menu_open_api: 'Open API',
        nav_unavailable: 'Feature not available yet',
        nav_unavailable_hint: 'This feature is in preparation or is not enabled in the current configuration. Please return to another available page.',
        nav_denied: 'Access denied',
        nav_denied_hint: 'Your account does not have access to this page, or the capabilities it relies on are not enabled. Please return to another available page or contact an administrator to grant access.',
        nav_go_back: 'Back to available pages',
        branding_title: 'Branding',
        branding_subtitle: 'Customize the console brand identity and description',
        branding_instance: 'Current instance',
        branding_brand_info: 'Brand Info',
        branding_preview: 'Live Preview',
        branding_logo: 'Logo',
        branding_upload_logo: 'Upload Logo',
        branding_use_default_logo: 'Use default logo',
        branding_logo_hint: 'PNG / JPG / WebP, max 2MB',
        branding_brand_name: 'Brand name',
        branding_logo_desc: 'Logo description',
        branding_logo_desc_placeholder: 'A short caption shown next to or below the logo, optional',
        branding_logo_desc_hint: 'Optional, up to 100 characters',
        branding_name_placeholder: 'Brand name, 1-32 characters',
        branding_sidebar: 'Sidebar',
        branding_login: 'Login page',
        branding_welcome: 'Chat welcome',
        branding_light: 'Light',
        branding_dark: 'Dark',
        branding_preview_hint: 'Preview only; takes effect after saving',
        branding_cancel: 'Discard changes',
        branding_save: 'Save settings',
        branding_reset_all: 'Restore all defaults',
        branding_reset_logo: 'Restore default logo',
        branding_saved: 'Branding saved',
        branding_unsaved: 'Unsaved changes',
        branding_saved_state: 'Saved',
        branding_readonly: 'Read-only',
        branding_readonly_reason: 'Set a console password before editing the brand',
        branding_enterprise_unavailable: 'Brand editing is unavailable until enterprise authorization and auditing are connected',
        branding_storage_corrupt: 'Brand settings are damaged. This is a recovery preview. Reload or explicitly restore all defaults; the original file will be preserved.',
        branding_loading: 'Loading…',
        branding_retry: 'Retry',
        branding_load_failed: 'Failed to load',
        branding_conflict: 'Branding was changed by someone else',
        branding_reload: 'Reload published values',
        branding_save_failed: 'Save failed',
        login_select_tenant: 'Select tenant',
        login_enter_tenant: 'Enter',
        tenant_title: 'Tenants',
        tenant_create: 'Create tenant',
        tenant_search_placeholder: 'Search tenant name / code',
        tenant_loading: 'Loading…',
        tenant_empty: 'No tenants',
        tenant_version_label: 'Version',
        users_title: 'Users',
        member_create: 'New member',
        member_section_account: 'Account Information',
        member_section_role: 'Roles & Status',
        member_section_tenants: 'Tenants',
        member_tenants: 'Tenants',
        member_tenants_title: 'Adjust member tenants',
        admin_field_tenants: 'Target tenants', admin_field_tenants_hint: 'Select multiple; a new account is created in the selected tenants',
        admin_field_tenants_edit_hint: 'Check/uncheck to add/remove membership (only tenants you administer)',
        member_search_placeholder: 'Search account / name',
        member_empty: 'No members',
        roles_title: 'Roles & Permissions',
        role_create: 'New role',
        role_empty: 'No roles',
        role_permissions_label: 'Permissions',
        org_title: 'Organization',
        dept_create: 'New department',
        org_empty: 'No departments',
        load_error: 'Load failed',
        active: 'Active',
        inactive: 'Inactive',
        unsaved_changes_warning: 'You have unsaved changes. Leave anyway?',
        create_not_available: 'This action is not available yet',
        // identity-admin CRUD forms (task: wire create/edit/delete)
        admin_save: 'Save', admin_create: 'Create', admin_edit: 'Edit', admin_delete: 'Delete', admin_saved: 'Saved',
        admin_deleted: 'Deleted', admin_save_failed: 'Save failed',
        admin_permissions_load_failed: 'Could not load permissions. Please reopen to retry.',
        admin_required_field: 'Please fill in the required fields',
        admin_conflict: 'Changed by someone else. List refreshed, please retry.',
        admin_field_code: 'Code', admin_field_code_hint: 'Lowercase letters/digits/hyphens; fixed once created',
        admin_field_name: 'Name',
        admin_field_shared_root: 'Shared root', admin_field_shared_root_hint: 'Absolute path to the tenant data directory',
        admin_field_admin_username: 'Admin username',
        admin_field_admin_display: 'Admin display name',
        admin_field_admin_password: 'Admin password', admin_field_password_hint: 'At least 8 chars; avoid weak passwords',
        admin_field_recent_password: 'Confirm current password', admin_field_recent_password_hint: 'Re-enter your login password to authorize this action',
        admin_field_active: 'Active',
        admin_field_username: 'Username', admin_field_username_hint: '3-64 letters/digits/._-',
        admin_field_display_name: 'Display name',
        admin_field_temp_password: 'Temporary password',
        admin_field_roles: 'Roles', admin_field_roles_hint: 'Member selected by default',
        admin_field_roles_edit_hint: 'Nothing checked resets to the default member role (current roles not loaded)',
        admin_field_department: 'Department', admin_field_department_hint: 'Optional', admin_field_department_none: 'No department',
        admin_field_position: 'Position',
        admin_field_parent: 'Parent department', admin_field_parent_none: 'None (root)',
        admin_field_sort_order: 'Sort order',
        admin_field_permissions: 'Permissions', admin_field_permissions_hint: 'Pick permission points from the catalog',
        admin_field_resource_grants: 'Resource grants',
        admin_field_resource_grants_hint: 'Grant the role access to menus / skills / tools / models / agents',
        admin_field_model_defaults: 'Model defaults',
        admin_field_model_defaults_hint: 'Pick a default model per connected capability; must be an authorized model',
        admin_field_model_grants: 'Model grants',
        admin_field_model_grants_hint: 'Allocate models this tenant may assign to roles / sessions',
        admin_resources_selected: '{n} selected',
        admin_resources_none: 'No resources selected',
        admin_resource_manage: 'Manage resources',
        admin_resource_search_placeholder: 'Search resources…',
        admin_resource_selectall: 'Select this page',
        admin_resource_clear: 'Clear',
        admin_resource_model_capabilities: 'Capabilities',
        admin_resource_kind_menu: 'Menus',
        admin_resource_kind_skill: 'Skills',
        admin_resource_kind_tool: 'Tools',
        admin_resource_kind_model: 'Models',
        admin_resource_kind_agent: 'Agents',
        admin_field_platform_admin: 'Platform admin',
        admin_field_admin_user_id: 'Admin account ID', admin_field_admin_user_id_hint: 'User ID of an existing active account',
        admin_tenant_admin: 'Admin',
        admin_tenant_roles: 'Roles',
        admin_tenant_admin_edit: 'Configure tenant admin',
        admin_reset: 'Reset password', admin_reset_do: 'Reset', admin_reset_confirm: 'Reset password for "{name}"?',
        admin_reset_temp_result: 'One-time temporary password', admin_forbidden: 'Forbidden',
        admin_prev: 'Prev', admin_next: 'Next',
        admin_total_label: 'Total', admin_page_label: 'Page',
        filter_all: 'All', filter_restricted: 'Needs change',
        platform_title: 'Platform Accounts', platform_admin_only: 'Visible to platform admins only',
        platform_search_placeholder: 'Search platform accounts',
        platform_user_empty: 'No platform accounts',
        platform_user_edit_title: 'Edit platform account',
        platform_admin_badge: 'Platform admin',
        admin_tab_members: 'Tenant members', admin_tab_platform: 'Platform accounts',
        tenant_edit_title: 'Edit tenant',
        member_edit_title: 'Edit member',
        role_edit_title: 'Edit role',
        role_tab_basic: 'Basics',
        role_editor_back: 'Back to roles',
        role_dirty_pill: 'Unsaved changes',
        role_section_basic: 'Basic info',
        role_basic_hint: 'Set the role identity, then configure functional permissions below.',
        role_perm_search_placeholder: 'Search permissions…',
        role_perm_select_group: 'Select group',
        role_perm_clear_group: 'Clear group',
        role_section_model_assign: 'Assignable models',
        role_model_assign_hint: 'Pick models this role may use. Defaults must come from the selected set.',
        role_editor_foot_hint: 'Tab switches keep the draft. Leaving with changes asks for confirmation.',
        role_create_sub: 'Code cannot be changed after create',
        role_copy_from: 'Copied from {name}',
        role_copy_suffix: ' (copy)',
        dept_edit_title: 'Edit department',
        admin_delete_confirm_role: 'Delete role "{name}"?',
        admin_delete_confirm_dept: 'Delete department "{name}"?',
        role_builtin: 'Built-in',
        role_members_label: 'Members',
        role_view_members: 'Members',
        role_copy: 'Copy', role_copy_title: 'Copy role',
        org_cycle_rejected: 'Organization tree would create a cycle; action rejected',
        audit_title: 'Identity Audit',
        audit_scope_hint: 'Platform admin sees all; tenant admin only current tenant',
        audit_actor_placeholder: 'Actor',
        audit_action_placeholder: 'Action',
        audit_apply: 'Filter',
        audit_empty: 'No audit records',
        audit_result_success: 'Success',
        audit_result_denied: 'Denied',
        branding_reset_confirm_title: 'Restore all defaults',
        branding_reset_confirm_body: 'This resets the logo, brand name and logo description to the built-in defaults. Continue?',
        branding_reset_confirm_ok: 'Restore',
        branding_reset_confirm_cancel: 'Cancel',
        branding_dirty_leave_title: 'Unsaved changes',
        branding_dirty_leave_body: 'Branding has unsaved changes. Leave this page and discard them?',
        branding_dirty_leave_ok: 'Discard and leave',
        branding_dirty_leave_cancel: 'Stay on this page',
        branding_unsaved_warn: 'Branding has unsaved changes',
        branding_save_pending: 'Save result unconfirmed',
        branding_save_pending_body: 'Could not confirm whether the server published. Reload the published brand and retry.',
        branding_save_pending_ok: 'Reload',
        branding_added: 'New brand applied',
        branding_image_too_large: 'Image must be under 2MB',
        branding_invalid_image: 'Invalid image',
        branding_asset_fallback: 'Brand image unavailable',
        branding_browser_title: 'Console',
        branding_logo_alt: 'Brand logo',
        agents_page_title: 'Agent Config', agents_page_desc: 'Manage the Agents on your team',
        agents_create: 'New Agent',
        agents_name_placeholder: 'Agent name',
        agents_name_required: 'Please enter a name',
        agents_stale: 'The list changed; please refresh and try again',
        agents_id_placeholder: 'Auto-generated if left blank',
        agents_id_tip: 'A unique identifier, fixed once created. Lowercase letters, digits and hyphens (-) only, e.g. ops-agent. Left blank, it is derived from the name.',
        agents_id_invalid: 'The id must start with a letter or digit and use only letters, digits, underscores and hyphens (max 64)',
        agents_avatar: 'Avatar',
        agents_tab_profile: 'Profile',
        agents_tab_skills: 'Skills',
        agents_tab_tasks: 'Tasks',
        agents_tab_files: 'Core files',
        agents_tasks_label: "This employee's scheduled tasks",
        tasks_empty_agent: 'No scheduled tasks for this employee. Tasks created via the scheduler tool in chat are owned by this employee.',
        agents_core_edit: 'Edit',
        agents_core_preview: 'Preview',
        agents_core_file_agent: 'Persona',
        agents_core_file_user: 'User info',
        agents_core_file_rule: 'Workspace rules',
        agents_core_file_memory: 'Long-term memory',
        agents_default: 'Default',
        agents_archived: 'Archived',
        agents_chat: 'Start chat',
        start_chat: 'Start chat',
        agent_workbench_title: 'Agents',
        agent_workbench_desc: 'Choose a configured Agent to start a chat',
        agent_workbench_refresh: 'Refresh',
        agent_workbench_loading: 'Loading…',
        agent_workbench_empty: 'No Agents available',
        agent_workbench_failed: 'Failed to load',
        agent_workbench_retry: 'Retry',
        agent_unavailable: 'Unavailable',
        agent_cannot_run: 'Cannot run currently',
        agent_target_unavailable: 'This Agent is no longer available. Refresh the list and try again.',
        agent_starting: 'Opening…',
        agent_start_failed: 'Could not start the chat. Please try again.',
        agent_runtime_not_enabled: 'Chat is not enabled in this version',
        agent_permission_denied: 'No permission to chat yet — ask an administrator to grant access',
        agents_delete: 'Delete',
        agents_delete_title: 'Delete Agent',
        agents_delete_confirm: 'Delete Agent "{name}"? Its workspace and conversations will be removed for good.',
        agents_pick_hint: 'Pick Agents',
        agents_clone_label: 'Copy from an existing agent',
        agents_clone_hint: 'Copy its config, skills and knowledge as a starting point',
        agents_avatar_upload: 'Upload image',
        agents_clone_none: 'Blank',
        agents_clone_from: '{name}',
        agents_name: 'Name',
        agents_saved: 'Saved',
        agents_save_failed: 'Save failed',
        agents_no_desc: 'No responsibilities yet',
        agents_description: 'Responsibilities',
        agents_description_placeholder: 'What this Agent handles and when it should be used',
        agents_description_hint: 'Used for task assignment when Agents collaborate',
        agents_model: 'Default model',
        agents_model_follows_global: 'Follow the configured model',
        agents_model_default_hint: 'Uses the primary model. Change it under Model config.',
        agents_position: 'Position',
        agents_position_placeholder: 'e.g. Procurement specialist',
        agents_category: 'Category',
        agents_category_none: 'Uncategorized',
        agents_tags: 'Tags',
        agents_tags_placeholder: 'Tags separated by commas, e.g. supplier, bid',
        agents_greeting: 'Greeting',
        agents_persona: 'Persona summary',
        agents_persona_hint: 'Employee persona injected into the system prompt',
        agents_scene: 'Linked scene',
        agents_scene_none: 'No linked scene',
        agents_skills_all: 'Use every installed skill',
        agents_skills_label: 'Skills',
        agents_sops_label: 'SOPs',
        agents_sops_hint: 'Bind observable SOP execution-flow IDs to this employee (listed as capability metadata only; does not drive a state machine).',
        agents_sops_placeholder: 'SOP ID',
        agents_sops_add: 'Add',
        agents_tools_label: 'Tool catalog',
        agents_tools_hint: 'Checking "allow" enables only the allowlist; checking "deny" removes it from available tools. The two are mutually exclusive.',
        agents_tools_none_hint: 'With no allowlist/denylist, the employee may use every installed tool.',
        agents_allow: 'Allow',
        agents_deny: 'Deny',
        agents_knowledge: 'Knowledge base',
        agents_knowledge_shared: 'Shared',
        agents_knowledge_own: 'Own',
        agents_knowledge_hint: 'Shared: read and write the same knowledge base as the team\nOwn: a private base, isolated from others',
        agents_knowledge_working: 'Working…',
        agents_knowledge_failed: 'Switch failed',
        agents_skills_pick: 'Only the skills checked below',
        agents_empty: 'No Agents yet. Create one to start a team.',
        agents_select_hint: 'Pick an Agent on the left to configure it',
        agents_pick_tip: 'Switch current Agent',
        team_members: 'In this conversation',
        team_invite: 'Add to current chat',
        team_remove: 'Remove from this chat',
        composer_agent_owner: 'Owner',
        channel_bound_agent: 'Bind agent',
        channel_bound_default: 'default',
        channel_bound_agent_hint: 'first pick is the default agent: it receives messages and can delegate to the rest',
        channel_team_none: 'None',
        channel_team_no_candidates: 'No agents available',
        settings_tab_basic: 'General',
        settings_tab_models: 'Models',
        knowledge_shared_hint: 'Knowledge is shared by every Agent. Open it from the Knowledge page.',
        menu_memory: 'Memory Management', menu_knowledge: 'Knowledge Base', menu_scenes: 'Scenario Apps', menu_channels: 'Channels', menu_tasks: 'Scheduled Tasks',
        menu_logs: 'Runtime Logs', menu_todo: 'My Todos', menu_scenarios: 'Scenarios',
        models_title: 'Models',
        models_desc: 'Manage chat, image, voice, embedding and search capabilities in one place',
        models_section_vendors: 'Provider Credentials',
        models_section_vendors_desc: 'Configured once, shared by multiple model capabilities',
        models_section_capabilities: 'Capabilities',
        models_add_vendor: 'Add Provider',
        models_provider: 'Provider',
        models_model: 'Model',
        models_voice: 'Voice',
        models_configured: 'configured',
        models_not_configured: 'not configured',
        models_pick_to_configure: 'pick to configure',
        models_clear_credential: 'Clear credentials',
        models_base_default_hint: 'Leave blank to use the official default base URL',
        models_base_default: 'Default',
        models_custom_vendor_label: 'Custom',
        models_custom_name: 'Name',
        models_custom_delete: 'Delete',
        models_custom_delete_confirm_title: 'Delete custom provider',
        models_custom_delete_confirm_msg: 'Delete this custom provider? This cannot be undone.',
        models_custom_name_required: 'Name is required',
        models_custom_base_required: 'API Base is required',
        models_custom_edit_title: 'Edit custom provider',
        models_custom_add_title: 'Add custom provider',
        models_capability_chat: 'Main Model',
        models_capability_chat_desc: 'Used for basic chat and agent reasoning',
        models_capability_chat_fallback: 'Main Model Fallback',
        models_capability_chat_fallback_desc: 'Takes over only after the main model fails for good',
        models_fallback_enable: 'Enable the fallback model',
        models_fallback_config: 'Fallback',
        models_fallback_config_tip: 'Configure the main-model fallback (takes over after the main model fails)',
        models_fallback_modal_title: 'Main Model Fallback',
        models_fallback_modal_desc: 'When the main model still fails after exhausting its retries, automatically switch to the fallback model to finish the reply.',
        models_fallback_badge_on: 'Fallback on',
        models_capability_vision: 'Image Understanding',
        models_capability_vision_desc: 'Recognizes image content, used by image recognition tools',
        models_capability_image: 'Image Generation',
        models_capability_image_desc: 'Generates images, used by image generation skills',
        models_auto_using: 'Preferred',
        models_capability_asr: 'Speech Recognition',
        models_capability_asr_desc: 'Voice to text',
        models_capability_tts: 'Speech Synthesis',
        models_capability_tts_desc: 'Text to voice',
        models_capability_embedding: 'Embedding',
        models_capability_embedding_desc: 'Used for vectorized retrieval of memory and knowledge',
        models_capability_search: 'Web Search',
        models_capability_search_desc: 'Real-time web retrieval, used by search tools',
        models_strategy_auto: 'auto',
        models_search_strategy_label: 'Strategy',
        models_search_strategy_fixed: 'Pinned',
        models_search_strategy_auto_hint: 'Auto-pick from configured providers',
        models_search_strategy_fixed_hint: 'Always use a specific provider',
        models_pending_config: 'Pending setup',
        models_search_available_label: 'Available:',
        models_search_none_configured: 'No search provider enabled yet — click add.',
        models_search_add_provider: 'Add provider',
        models_search_add_desc: 'Pick a search provider to configure',
        models_search_bocha_title: 'Configure Bocha API Key',
        models_search_bocha_desc: 'Create a key at the Bocha open platform.',
        models_search_anysearch_title: 'Configure AnySearch API Key',
        models_search_anysearch_desc: 'Create a key at the AnySearch console (anysearch.com).',
        models_search_serply_title: 'Configure Serply API Key',
        models_search_serply_desc: 'Create a key at the Serply console (serply.io).',
        models_search_edit_hint: 'Click to edit',
        models_unavailable: 'unavailable',
        models_set_via_env: 'enable via environment variable',
        models_dim_label: 'dim',
        models_save_success: 'Saved',
        models_save_failed: 'Save failed',
        models_cleared: 'Cleared',
        models_clear_failed: 'Clear failed',
        models_embedding_change_title: 'Change embedding model',
        models_embedding_change_msg: 'Switching the embedding model invalidates the existing index — a rebuild will be needed. Continue?',
        models_embedding_saved_title: 'Embedding model updated',
        models_embedding_saved_msg: 'Send /memory rebuild-index in the chat to rebuild the index.',
        models_embedding_saved_ok: 'Go',
        models_pick_provider: 'Pick a provider',
        models_manage_api_key: 'Manage API keys',
        models_clear_confirm_title: 'Clear provider credentials',
        models_clear_confirm_msg: 'Remove this provider\'s API Key and Base URL? Capabilities relying on it will stop working.',
        cancel: 'Cancel',
        save: 'Save',
        ok: 'OK',
        knowledge_title: 'Knowledge', knowledge_desc: 'Browse and explore your knowledge base',
        knowledge_tab_docs: 'Documents', knowledge_tab_graph: 'Graph',
        knowledge_loading: 'Loading knowledge base...', knowledge_loading_desc: 'Knowledge pages will be displayed here',
        knowledge_select_hint: 'Select a document to view', knowledge_empty_hint: 'No knowledge pages yet',
        knowledge_empty_guide: 'Send documents, links or topics to the agent in chat, and it will automatically organize them into your knowledge base.',
        knowledge_go_chat: 'Start a conversation',
        knowledge_new: 'New',
        knowledge_new_category: 'New category',
        knowledge_new_document: 'New document',
        knowledge_import_documents: 'Import documents',
        welcome_subtitle: 'I can help you answer questions, manage your computer, create and execute skills, and keep growing through <br> long-term memory and a personal knowledge base.',
        example_sys_title: 'System', example_sys_text: 'Show me the files in the workspace',
        example_task_title: 'Scheduler', example_task_text: 'Remind me to check the server in 5 minutes',
        example_code_title: 'Coding', example_code_text: 'Search today\'s AI news and generate a visual report webpage',
        example_knowledge_title: 'Knowledge', example_knowledge_text: 'Show me the current knowledge base',
        example_skill_title: 'Skills', example_skill_text: 'Show current tools and skills',
        example_web_title: 'Commands', example_web_text: 'Show all commands',
        slash_help: 'Show this help',
        slash_status: 'Show running status',
        slash_context: 'Show conversation context',
        slash_context_clear: 'Clear conversation context',
        slash_compact: 'Summarize older turns to free up context',
        slash_skill_list: 'List installed skills',
        slash_skill_list_remote: 'Browse Skill Hub',
        slash_skill_search: 'Search skills',
        slash_skill_install: 'Install a skill (name or GitHub URL)',
        slash_skill_uninstall: 'Uninstall a skill',
        slash_skill_info: 'Show skill details',
        slash_skill_enable: 'Enable a skill',
        slash_skill_disable: 'Disable a skill',
        slash_memory_dream: 'Trigger memory distillation (optional days, default 3)',
        slash_knowledge: 'Show knowledge base stats',
        slash_knowledge_list: 'Show knowledge base file tree',
        slash_knowledge_on: 'Enable knowledge base',
        slash_knowledge_off: 'Disable knowledge base',
        slash_config: 'Show current config',
        slash_cancel: 'Abort the running Agent task',
        slash_steer: 'Inject guidance into the running Agent task',
        steer_active: 'Steer active task',
        slash_logs: 'Show recent logs',
        slash_version: 'Show version',
        input_placeholder: 'Type a message, / for commands, @ to mention an Agent or a file',
        config_title: 'Configuration', config_desc: 'Manage model and agent settings',
        config_model: 'Model Configuration', config_agent: 'Agent Configuration',
        config_language: 'Language', config_language_hint: 'Language for the UI, command text, system prompts and more (synced with the top-right switch)',
        config_system: 'System',
        config_task_notify: 'Task Notifications', config_task_notify_hint: 'Show a browser notification when a task finishes or fails while the window is in the background; click to open the session',
        config_task_notify_sound: 'Notification Sound', config_task_notify_sound_hint: 'Turn off the alert sound while keeping notifications',
        config_task_notify_blocked: 'Notifications are blocked by the browser. Click the icon on the left of the address bar → Notifications → Allow, then reload.',
        notify_task_done: 'Task finished',
        notify_task_error: 'Task failed',
        config_model_advanced: 'Advanced',
        config_channel: 'Channel Configuration',
        config_agent_enabled: 'Agent Mode',
        config_max_tokens: 'Max Context Tokens', config_max_tokens_hint: 'Max tokens the Agent can input per conversation, auto-compressed when exceeded',
        config_max_turns: 'Max Memory Turns', config_max_turns_hint: 'One Q&A pair = one turn, auto-compressed when exceeded',
        config_max_steps: 'Max Steps', config_max_steps_hint: 'Max tool calls the Agent can make in a single conversation',
        config_enable_thinking: 'Deep Thinking', config_enable_thinking_hint: 'Enable deep thinking mode',
        config_reasoning_effort: 'Reasoning Effort', config_reasoning_effort_hint: 'Sent as the active provider\'s native enum value',
        config_subagent: 'Sub Agents', config_subagent_hint: 'Hand self-contained tasks to sub agents, which run in parallel and report back only their conclusions',
        config_self_evolution: 'Self-Evolution', config_self_evolution_hint: 'Auto-review idle conversations to consolidate memory, improve skills, and follow up on unfinished tasks',
        evolution_badge: 'Self-learned',
        config_channel_type: 'Channel Type',
        config_provider: 'Provider', config_model_name: 'Model',
        config_custom_model_hint: 'Enter custom model name',
        config_save: 'Save', config_saved: 'Saved',
        config_save_error: 'Save failed',
        config_custom_option: 'Custom',
        config_custom_tip: 'API must follow OpenAI protocol.',
        config_security: 'Security', config_password: 'Password',
        config_password_hint: 'Leave empty to disable password protection',
        config_permission: 'Default permissions',
        config_permission_hint: 'The default scope for new chats: which files the agent may change and which commands it may run',
        config_permission_desc: 'New chats start with this; each chat can be changed under the input box',
        config_password_changed: 'Password updated',
        config_password_cleared: 'Password cleared',
        config_password_security_warning: '⚠️ Warning: Password is now empty and the port is exposed. Consider restarting the service or adjusting the listening address binding.',
        skills_title: 'Skills', skills_desc: 'View, enable, or disable agent tools and skills', skills_hub_btn: 'Skill Hub',
        skills_loading: 'Loading skills...', skills_loading_desc: 'Skills will be displayed here after loading',
        tools_section_title: 'Built-in Tools', tools_loading: 'Loading tools...',
        skills_section_title: 'Skills', skill_enable: 'Enable', skill_disable: 'Disable',
        skill_toggle_error: 'Operation failed, please try again',
        skill_open_hint: 'Click to view this skill',
        skill_back: 'Back to list',
        skill_load_failed: 'Could not read the skill',
        skill_builtin_readonly: 'Built-in skill, read-only (replaced on restart)',
        memory_title: 'Memory', memory_desc: 'View agent memory files and contents',
        memory_tab_files: 'Memory Files', memory_tab_dreams: 'Self-Evolution',
        memory_loading: 'Loading memory files...', memory_loading_desc: 'Memory files will be displayed here',
        memory_back: 'Back to list',
        memory_col_name: 'Filename', memory_col_type: 'Type', memory_col_size: 'Size', memory_col_updated: 'Updated',
        channels_title: 'Channels', channels_desc: 'Manage connected messaging channels',
        channels_add: 'Connect', channels_disconnect: 'Disconnect',
        channels_save: 'Save', channels_saved: 'Saved', channels_save_error: 'Save failed',
        channels_restarted: 'Saved & Restarted',
        channels_connect_btn: 'Connect', channels_cancel: 'Cancel',
        channels_select_placeholder: 'Select a channel to connect...',
        channels_empty: 'No channels connected', channels_empty_desc: 'Click the "Connect" button above to get started',
        channels_disconnect_confirm: 'Disconnect this channel? Config will be preserved but the channel will stop.',
        channels_connected: 'Connected', channels_connecting: 'Connecting...',
        weixin_scan_title: 'WeChat QR Login', weixin_scan_desc: 'Scan the QR code below with WeChat',
        weixin_scan_loading: 'Loading QR code...', weixin_scan_waiting: 'Waiting for scan...',
        weixin_scan_scanned: 'Scanned, please confirm on your phone', weixin_scan_expired: 'QR code expired, refreshing...',
        weixin_scan_success: 'Login successful, starting channel...', weixin_scan_fail: 'Failed to load QR code',
        weixin_qr_tip: 'QR code expires in ~2 minutes',
        wecom_scan_btn: 'Scan to Create WeCom Bot', wecom_scan_desc: 'Scan with WeCom to create a bot instantly',
        wecom_scan_success: 'Bot created, starting channel...',
        wecom_scan_fail: 'Bot creation failed',
        wecom_mode_scan: 'Scan QR', wecom_mode_manual: 'Manual',
        feishu_scan_btn: 'One-click Create Feishu App',
        feishu_scan_desc: 'Scan with Feishu App to create an app with all required permissions pre-configured',
        feishu_scan_replace_desc: 'Scan with Feishu App to create a new bot — will overwrite the current App ID / Secret',
        feishu_scan_loading: 'Requesting QR code from Feishu...',
        feishu_scan_waiting: 'Waiting for scan...',
        feishu_scan_tip: 'QR code expires in 10 minutes, single use only',
        feishu_scan_open_link: 'Or click here to open in browser',
        feishu_scan_success: 'App created, starting channel...',
        feishu_scan_expired: 'QR code expired, please retry',
        feishu_scan_denied: 'Authorization cancelled',
        feishu_scan_fail: 'App creation failed',
        feishu_scan_retry: 'Retry',
        feishu_sdk_downloading: 'Downloading Feishu components...',
        feishu_sdk_downloading_tip: 'A one-time ~1MB download; this will continue automatically',
        feishu_mode_scan: 'Scan QR', feishu_mode_manual: 'Manual',
        tasks_title: 'Scheduled Tasks', tasks_desc: 'View and manage scheduled tasks',
        todo_title: 'Todo', todo_subtitle: 'Manage the items that need your follow-up',
        todo_add_btn: 'New Todo', todo_save: 'Save', todo_cancel: 'Cancel',
        todo_filter_all: 'All', todo_filter_open: 'Open', todo_filter_pending: 'Pending', todo_filter_progress: 'In Progress', todo_filter_done: 'Completed', todo_filter_cancel: 'Cancelled',
        todo_only_overdue: 'Only Overdue', todo_search_placeholder: 'Search todos…', todo_clear_due: 'Clear',
        todo_status_all: 'All', todo_status_open: 'Open', todo_status_pending: 'Pending', todo_status_in_progress: 'In Progress', todo_status_completed: 'Completed', todo_status_cancelled: 'Cancelled',
        todo_kind_general: 'General', todo_kind_input_required: 'Input Required', todo_kind_confirmation: 'Confirmation', todo_kind_review: 'Review',
        todo_priority_low: 'Low', todo_priority_normal: 'Normal', todo_priority_high: 'High',
        todo_edit_title: 'Edit Todo', todo_create_title: 'New Todo',
        todo_field_title: 'Title', todo_field_desc: 'Description', todo_field_kind: 'Kind', todo_field_priority: 'Priority', todo_field_due: 'Due',
        todo_by: 'Created', todo_updated: 'Updated', todo_completed: 'Completed', todo_created_by: 'Source',
        todo_empty_open: 'No open todos', todo_empty_pending: 'No pending items', todo_empty_progress: 'No items in progress',
        todo_empty_done: 'No completed items', todo_empty_cancel: 'No cancelled items', todo_empty_all: 'No todos yet',
        todo_empty_search: 'No matching todos', todo_empty_overdue: 'No overdue items',
        todo_disabled_banner: 'Todo feature is disabled. Enable todo_enabled in settings.',
        todo_unauthorized_banner: 'Please sign in to use todos.',
        todo_load_failed: 'Failed to load. Please try again.',
        todo_banner_disabled: 'Todos disabled', todo_banner_unauthorized: 'Not authenticated',
        todo_empty_title: 'Todo title', todo_empty_subtitle: 'Auto-fill when empty',
        todo_confirm_discard: 'You have unsaved changes. Discard them?',
        todo_confirm_complete: 'Mark as completed?', todo_confirm_cancel: 'Cancel this todo?', todo_confirm_reopen: 'Reopen this item?',
        todo_saved: 'Saved', todo_save_failed: 'Failed to save', todo_created: 'Created',
        todo_action_start: 'Start', todo_action_complete: 'Complete', todo_action_cancel: 'Cancel', todo_action_reopen: 'Reopen', todo_action_edit: 'Edit', todo_action_events: 'History',
        todo_detail_title: 'Todo Detail', todo_detail_source: 'Source', todo_detail_history: 'History', todo_detail_history_empty: 'No processing records',
        todo_detail_note: 'Note', todo_detail_note_empty: 'No note',
        todo_due_overdue: 'Overdue', todo_due_soon: 'Due soon', todo_due_none: 'No due',
        todo_pagination_prev: 'Previous', todo_pagination_next: 'Next',
        todo_source_manual: 'Manual', todo_source_conversation: 'Conversation',
        todo_load_error: 'Failed to load', todo_retry: 'Retry',
        tasks_coming: 'Coming Soon', tasks_coming_desc: 'Scheduled task management will be available here',
        tasks_unavailable: 'Scheduled tasks unavailable', tasks_unavailable_desc: 'Scheduled tasks are not enabled in the current identity mode. Use legacy mode or ask an administrator to adapt them.',
        task_add_btn: 'Add Task',
        task_edit_title: 'Edit Task',
        task_add_title: 'Add Task',
        task_name: 'Task Name',
        task_enabled: 'Enable Task',
        task_schedule_type: 'Schedule Type',
        task_schedule_cron: 'Cron Expression',
        task_schedule_interval: 'Fixed Interval',
        task_schedule_once: 'One-time Task',
        task_cron_expression: 'Cron Expression',
        task_cron_hint: 'Format: minute hour day month weekday, e.g. "0 9 * * *" means daily at 9:00',
        task_interval_seconds: 'Interval (seconds)',
        task_interval_hint: 'Minimum 60 seconds, e.g. 3600 means once per hour',
        task_once_time: 'Execution Time',
        task_action_type: 'Action Type',
        task_action_send_message: 'Send Message',
        task_action_agent_task: 'AI Task',
        task_channel_type: 'Channel Type',
        task_channel_hint: 'Select the channel to send scheduled messages',
        task_message_content: 'Message Content',
        task_task_description: 'Task Description',
        task_delete_btn: 'Delete Task',
        task_delete_confirm_title: 'Delete Task',
        task_delete_confirm_msg: 'Delete this scheduled task? This action cannot be undone.',
        task_run_now: 'Run now',
        task_next_run: 'Next run',
        task_run_confirm_title: 'Run task now',
        task_run_confirm_msg: 'This task will immediately send to its configured channel and receiver. Continue?',
        task_run_started: 'Run started',
        task_run_failed: 'Run failed',
        logs_title: 'Logs', logs_desc: 'Real-time log output (run.log)',
        logs_live: 'Live', logs_coming_msg: 'Log streaming will be available here. Connects to run.log for real-time output similar to tail -f.',
        new_chat: 'New Chat',
        new_team_chat: 'Group chat',
        new_team_chat_hint: 'Pick the Agents for this conversation; the first is its default Agent.',
        new_team_chat_owner: 'Default',
        new_team_chat_start: 'Start chat',
        new_team_chat_min: 'Pick at least two Agents',
        session_history: 'History',
        history_desc: 'Find a previous conversation and pick up where you left off',
        history_search_placeholder: 'Search conversation titles',
        history_search_clear: 'Clear search',
        history_search_loading: 'Searching…',
        history_search_empty: 'No matching conversations. Try another title keyword.',
        history_search_count: 'Matching conversations: {count}',
        history_search_limit: 'Search terms must be 100 characters or fewer',
        history_search_unsupported: 'Title search is not supported by this server yet. Update the server and retry.',
        history_refresh: 'Refresh conversation history',
        history_current: 'Current',
        history_more: 'More actions',
        session_history_loading: 'Loading history…',
        session_history_empty: 'No history sessions yet',
        session_history_failed: 'Failed to load, please retry',
        session_history_retry: 'Retry',
        session_history_not_enabled: 'Session history is not currently enabled',
        ws_toggle: 'Workspace', ws_tab_preview: 'Preview', ws_tab_files: 'Files',
        ws_default_workspace: 'Default', ws_sel_title: 'Select workspace',
        ws_sel_default_hint: 'Use the default workspace (~/cow)', ws_sel_recents: 'Recent',
        ws_sel_open: 'Open project…', ws_sel_new: 'New project', ws_sel_new_placeholder: 'Project name',
        ws_sel_create: 'Create', ws_sel_up: 'Up',
        ws_sel_new_subtitle: 'Creates a new project directory under {root}', ws_sel_new_hint: 'Project name only, no path separators',
        ws_sel_name_required: 'Please enter a project name', ws_sel_name_no_slash: 'Project name must not contain / or \\',
        ws_sel_open_here: 'Open this folder', ws_sel_dblclick_hint: 'Double-click to enter, single-click to select',
        ws_sel_no_subdirs: 'No sub-folders here', ws_sel_drives: 'This PC',
        ws_open_external: 'Open in new tab', ws_download: 'Download', ws_copy_path: 'Copy path',
        ws_close: 'Close', ws_refresh: 'Refresh', ws_preview: 'Preview',
        ws_search_placeholder: 'Search files',
        ws_preview_empty: 'Select a file to preview',
        ws_preview_failed: 'Preview failed',
        ws_link_not_found: 'File not found in the workspace',
        ws_no_inline_preview: 'No inline preview for this file type',
        ws_empty_dir: 'Empty directory', ws_no_results: 'No matching files',
        ws_truncated: 'Too many files, showing a subset',
        ws_edit: 'Edit', ws_edit_save: 'Save (Ctrl+S)', ws_edit_cancel: 'Leave editor',
        ws_edit_saved: 'Saved',
        ws_edit_load_failed: 'Could not open the editor',
        ws_edit_save_failed: 'Save failed',
        ws_edit_too_large: 'File is too large to edit in the panel',
        ws_edit_unsupported: 'This file type cannot be edited',
        ws_edit_encoding: 'This file is not UTF-8; editing would corrupt it',
        ws_edit_conflict_title: 'File changed on disk',
        ws_edit_conflict_msg: 'This file changed while you were editing it, most likely written by the agent. Overwriting discards the newer content on disk.',
        ws_edit_overwrite: 'Overwrite',
        ws_edit_discard_title: 'Discard unsaved changes?',
        ws_edit_discard_msg: 'This file has unsaved changes and continuing will lose them.',
        ws_edit_discard_ok: 'Discard',
        today: 'Today', yesterday: 'Yesterday', earlier: 'Earlier',
        session_pinned_group: 'Pinned',
        pin_session: 'Pin',
        unpin_session: 'Unpin',
        project_rename: 'Rename project',
        project_delete: 'Delete project',
        project_rename_title: 'Rename project',
        project_delete_title: 'Delete project',
        project_delete_confirm: 'Delete project “{name}”? Only the project record is removed — files on disk are kept, and its chats revert to the default workspace.',
        perm_menu_title: 'Permissions for this chat',
        perm_read_only: 'Read-only',
        perm_workspace_write: 'Workspace write',
        perm_full_access: 'Full access',
        perm_read_only_desc: 'Read and analyse only; no file is modified',
        perm_workspace_write_desc: 'Write freely inside this workspace; writes outside it are refused',
        perm_full_access_desc: 'No limits, anywhere on the machine (current default)',
        perm_follow_global: 'Follow global setting',
        perm_tip: 'Permissions: {name}',
        perm_denied_hint: 'This session is “{name}”, so the action was refused.',
        perm_denied_action: 'Adjust permissions',
        model_menu_title: 'Model for this chat',
        model_follow_global: 'Follow global setting',
        model_follow_agent: 'Follow the agent\u2019s default model',
        model_tip: 'Model: {name}',
        model_unset: 'Not set',
        session_settings_failed: 'Could not apply, please retry',
        delete_session_confirm: 'Delete this session? All messages will be removed.',
        delete_session_title: 'Delete Session',
        rename_session: 'Rename',
        delete_message_confirm: 'Delete this message?',
        delete_message_title: 'Delete Message',
        edit_disabled_reply_active: 'Reply is being generated; editing is temporarily unavailable.',
        delete_disabled_reply_active: 'Reply is being generated; deletion is temporarily unavailable.',
        untitled_session: 'New Chat',
        context_cleared: '— Context above has been cleared —',
        tip_new_chat: 'New Chat',
        tip_clear_context: 'Clear Context',
        tip_attach: 'Add Attachment',
        tip_cancel: 'Cancel',
        tip_cancelled: 'Cancelled',
        attach_menu_file: 'Upload File',
        mic_idle_title: 'Click to record, click again to stop',
        mic_recording_title: 'Recording, click to stop',
        mic_busy_title: 'Transcribing…',
        mic_permission_denied: 'Cannot access microphone — check browser permissions',
        mic_too_short: 'Recording too short, please retry',
        mic_error: 'Speech recognition failed',
        optimize_idle_title: 'Optimize prompt',
        optimize_busy_title: 'Optimizing…',
        optimize_error: 'Prompt optimization failed',
        optimize_empty: 'Input is empty, nothing to optimize',
        speak_msg: 'Read this reply aloud',
        voice_reply_mode_label: 'Voice reply policy',
        voice_reply_off: 'Off',
        voice_reply_if_voice: 'Voice only if voice input',
        voice_reply_always: 'Always reply with voice',
        attach_menu_folder: 'Upload Folder',
        confirm_yes: 'Confirm',
        confirm_cancel: 'Cancel',
        error_send: 'Failed to send. Please try again.', error_timeout: 'Request timeout. Please try again.',
        error_login_required: 'Your session has expired. Sign in again to send messages.',
        error_tenant_required: 'Select a tenant you belong to before sending a message.',
        error_chat_forbidden: 'Your account cannot chat in this tenant. Contact an administrator.',
        error_password_required: 'Change your initial password before sending messages.',
        thinking_in_progress: 'Thinking...', thinking_done: 'Thought', thinking_duration: 'Duration',
        edit_message: 'Edit message',
        regenerate_response: 'Regenerate',
        edit_save: 'Save and send',
        edit_cancel: 'Cancel',
        account_loading: 'Loading account…', account_unavailable: 'Account information unavailable',
        account_local: 'Local access', account_password_mode: 'Password protected', account_public_mode: 'No login required',
        account_retry: 'Check again', account_retry_hint: 'Please check again', account_logout: 'Log out',
        account_logging_out: 'Logging out…', account_logout_unconfirmed: 'Logout not confirmed', account_retry_logout: 'Retry logout',
        account_login: 'Log in', account_login_hint: 'Enter your login details to access the console',
        account_credentials_error: 'Incorrect login details. Please try again.', account_login_failed: 'Login did not complete. Please try again.',
        account_menu_profile: 'Profile', account_menu_password: 'Account security', account_menu_prefs: 'Preferences',
        account_menu_about: 'Help & About',
        account_profile_title: 'Profile', account_profile_global: 'Global account',
        account_profile_member: 'Current tenant member', account_profile_display_name: 'Name',
        account_profile_username: 'Username', account_profile_platform: 'Platform role',
        account_profile_tenant: 'Current tenant', account_profile_member_name: 'Member name',
        account_profile_role: 'Role', account_profile_department: 'Department',
        account_profile_position: 'Position', account_profile_empty: 'Not set',
        account_profile_no_tenant: 'Not a member of any tenant', account_profile_error: 'Failed to load profile',
        account_profile_edit: 'Edit profile', account_profile_avatar_hint: 'Click to change avatar',
        account_profile_error_required: 'This field is required',
        saved: 'Saved',
        account_password_title: 'Change password', account_password_note: 'After changing your password, other logged-in sessions will also expire. Please log in again.',
        account_password_old: 'Current password', account_password_new: 'New password', account_password_confirm: 'Confirm new password',
        account_password_submit: 'Submit', account_password_invalid_old: 'Current password is incorrect',
        account_password_weak: 'New password does not meet requirements. Please try again.', account_password_mismatch: 'New passwords do not match',
        account_password_unknown: 'The operation did not complete. Please try again.', account_password_done: 'Password changed. Please log in again',
        account_password_forced_title: 'Set a new password', account_password_forced_note: 'First login / temporary password requires setting a new password before accessing other features.',
        account_password_forced_required: 'Please set your password to continue', account_password_forced_logout: 'You must set a new password to continue. Log out now?',
        account_prefs_title: 'Preferences', account_prefs_note: 'Preferences apply to this browser only and are not written to the instance.',
        account_prefs_theme: 'Theme', account_prefs_lang: 'Language', account_prefs_light: 'Light',
        account_prefs_dark: 'Dark', account_prefs_zh: '简体', account_prefs_hant: '繁體', account_prefs_en: 'EN',
        account_prefs_storage_fail: 'Browser storage is unavailable; this change applies to this page only',
        account_tenant_title: 'Switch tenant', account_tenant_none: 'Not a member', account_tenant_current: 'Current',
        account_tenant_no_available: 'Not a member of any tenant', account_tenant_invalid: 'The target tenant is no longer available. Please choose again.',
        account_assign_pending: 'This account is not yet assigned to an active tenant, so the workbench and management pages are temporarily unavailable. Ask an administrator to assign a tenant, then retry.',
        account_tenant_single: 'Current tenant', account_about_title: 'About', account_about_version_unknown: 'Version unavailable',
        logout: 'Logout',
        close: 'Close',
    }
};

// Appearance labels share the console's existing language catalogs.
Object.assign(I18N.zh, {
    appearance_title: '外观', appearance_close: '关闭外观设置', appearance_palette: '配色方案',
    appearance_business: '商务青蓝', appearance_slate: '深蓝侧栏', appearance_classic: '经典配色',
    appearance_recommended: '推荐', appearance_mode: '明暗模式', appearance_light: '浅色',
    appearance_dark: '深色', appearance_system: '跟随系统', appearance_reset: '恢复默认',
    appearance_instant: '选择后立即生效', appearance_resolved_light: '当前显示：浅色',
    appearance_resolved_dark: '当前显示：深色',
    appearance_scope: '仅在当前浏览器生效，此浏览器中的不同账号共用外观设置。',
    appearance_storage_failed: '仅本页生效，刷新后可能恢复之前设置。',
});
Object.assign(I18N['zh-Hant'], {
    appearance_title: '外觀', appearance_close: '關閉外觀設定', appearance_palette: '配色方案',
    appearance_business: '商務青藍', appearance_slate: '深藍側欄', appearance_classic: '經典配色',
    appearance_recommended: '推薦', appearance_mode: '明暗模式', appearance_light: '淺色',
    appearance_dark: '深色', appearance_system: '跟隨系統', appearance_reset: '恢復預設',
    appearance_instant: '選擇後立即生效', appearance_resolved_light: '目前顯示：淺色',
    appearance_resolved_dark: '目前顯示：深色',
    appearance_scope: '僅在目前瀏覽器生效，此瀏覽器中的不同帳號共用外觀設定。',
    appearance_storage_failed: '僅本頁生效，重新整理後可能恢復先前設定。',
});
Object.assign(I18N.en, {
    appearance_title: 'Appearance', appearance_close: 'Close appearance settings', appearance_palette: 'Color palette',
    appearance_business: 'Business blue', appearance_slate: 'Slate sidebar', appearance_classic: 'Classic',
    appearance_recommended: 'Recommended', appearance_mode: 'Appearance mode', appearance_light: 'Light',
    appearance_dark: 'Dark', appearance_system: 'System', appearance_reset: 'Restore defaults',
    appearance_instant: 'Changes apply immediately', appearance_resolved_light: 'Currently using light mode',
    appearance_resolved_dark: 'Currently using dark mode',
    appearance_scope: 'Saved in this browser only. Accounts using this browser share appearance settings.',
    appearance_storage_failed: 'Applied to this page only. Reloading may restore previous settings.',
});

// Resolve language by priority: user choice (localStorage) -> backend-detected
// (cow_lang) -> browser language -> 'zh'. Shares __cowResolveLang__ defined in
// chat.html; falls back to a local resolver if loaded standalone.
let currentLang = (typeof window.__cowResolveLang__ === 'function')
    ? window.__cowResolveLang__()
    : (function () {
        const norm = (raw) => {
            if (!raw) return '';
            const v = String(raw).trim().toLowerCase();
            if (v === 'auto') return '';
            // Handle Traditional Chinese variants first (more specific)
            if (v === 'zh-hant' || v.startsWith('zh-hant-') || v === 'zh-tw' || v === 'zh-hk') return 'zh-Hant';
            // Then Simplified Chinese
            if (v.indexOf('zh') === 0) return 'zh';
            if (v.indexOf('en') === 0) return 'en';
            return '';
        };
        return norm(readStartupPreference('cow_lang'))
            || norm(window.__COW_DEFAULT_LANG__)
            || norm(navigator.language)
            || 'zh';
    })();

// Expose for sibling scripts (e.g. identity-admin.js) that use the same catalogs.
window.I18N = I18N;
window.__cowLang__ = currentLang;

function t(key) {
    return (I18N[currentLang] && I18N[currentLang][key]) || (I18N.en[key]) || key;
}

// Resolve a localized label that may be either a plain string or
// a {zh, en} object returned by the backend.
function localizedLabel(label) {
    if (label && typeof label === 'object') {
        return label[currentLang] || label.en || label.zh || '';
    }
    return label || '';
}

function applyI18n() {
    document.querySelectorAll('[data-i18n]').forEach(el => {
        el.textContent = t(el.dataset.i18n);
    });
    document.querySelectorAll('[data-i18n-html]').forEach(el => {
        el.innerHTML = t(el.dataset.i18nHtml);
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
        el.placeholder = t(el.dataset['i18nPlaceholder']);
    });
    document.querySelectorAll('[data-i18n-title]').forEach(el => {
        el.title = t(el.dataset['i18nTitle']);
    });
    document.querySelectorAll('[data-i18n-aria-label]').forEach(el => {
        el.setAttribute('aria-label', t(el.dataset['i18nAriaLabel']));
    });
    document.querySelectorAll('[data-i18n-tip]').forEach(el => {
        el.setAttribute('data-tip', t(el.dataset['i18nTip']));
    });
    document.querySelectorAll('[data-tip-key]').forEach(el => {
        el.setAttribute('data-tooltip', t(el.dataset.tipKey));
    });
    installCfgTipPortal();
    
    // Clear any status messages when language changes
    document.querySelectorAll('[id$="-status"]').forEach(el => {
        el.classList.add('opacity-0');
    });
    
    _syncLangControls();
    // Point the docs link to the locale-specific documentation site.
    const docsLink = document.getElementById('docs-link');
    if (docsLink) docsLink.href = currentLang === 'zh' ? 'https://docs.cowagent.ai/zh' : 'https://docs.cowagent.ai';
    // Workspace panel content is rendered by JS, not data-i18n attributes.
    if (typeof relocalizeWorkspacePanel === 'function') relocalizeWorkspacePanel();
    _renderSidebarAccount();
    renderAppearancePreferences();
}

// Single entry point for switching language.
//
// Two call paths share this:
//   * Personal change (the top-right header toggle and the account Preferences
//     modal): browser-local only (``cow_lang``). MUST NOT write instance config.
//   * Config-page language picker (``cfg-lang-select``): preserves the original
//     permission and still persists the instance default ``cow_lang``.
// They are split so a personal switch never changes the instance (logs / CLI /
// agent replies) default.
function setLanguage(lang) {
    const next = (lang === 'en' || lang === 'zh' || lang === 'zh-Hant') ? lang : 'zh';
    if (next === currentLang) {
        // Still persist + sync in case storage/backend drifted from the UI.
        syncLanguageToBackend(next);
        return;
    }
    applyLanguage(next, /* writeToBackend */ true);
}

// Personal / browser-local switch (header toggle + preferences modal). Applies
// the language locally and NEVER writes the instance ``cow_lang`` config.
function setLanguageLocal(lang) {
    const next = (lang === 'en' || lang === 'zh' || lang === 'zh-Hant') ? lang : 'zh';
    applyLanguage(next, /* writeToBackend */ false);
}

let languageStorageFailed = false;

// Shared application of a new language. ``writeToBackend`` selects whether the
// instance config default is updated (config page) or left untouched (personal).
function applyLanguage(next, writeToBackend) {
    currentLang = next;
    window.__cowLang__ = currentLang;
    try {
        localStorage.setItem('cow_lang', currentLang);
        languageStorageFailed = localStorage.getItem('cow_lang') !== currentLang;
    } catch (_) { languageStorageFailed = true; }
    applyI18n();
    _applyInputTooltips();
    // Keep the language switch button and config selector visually in sync.
    try { updateLangControls(); } catch (e) {}

    if (writeToBackend) {
        // Sync language choice to backend first, then trigger dynamic views
        // reload to avoid race conditions on API endpoints.
        syncLanguageToBackend(currentLang, () => {
            try { rerenderDynamicViews(); } catch (e) {}
        });
    } else {
        try { rerenderDynamicViews(); } catch (e) {}
    }
}

// Persist the language to the backend `cow_lang` config (best-effort; the UI
// has already switched locally, so a network failure is non-blocking).
function syncLanguageToBackend(lang, callback) {
    try {
        fetch('/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ updates: { cow_lang: lang } })
        })
        .then(() => { if (callback) callback(); })
        .catch(() => { if (callback) callback(); });
    } catch (e) {
        if (callback) callback();
    }
}

// Reflect the current language on both the top-right toggle and the config
// selector (if present), so the two entry points stay synchronized.
function updateLangControls() {
    _syncLangControls();
    // The config language picker is the custom .cfg-dropdown component. Only
    // sync it once it has been initialized (i.e. the config panel was opened).
    const sel = document.getElementById('cfg-lang-select');
    if (sel && sel._ddValue !== undefined && sel._ddValue !== currentLang) {
        sel._ddValue = currentLang;
        const textEl = sel.querySelector('.cfg-dropdown-text');
        if (textEl) {
            if (currentLang === 'zh-Hant') textEl.textContent = '繁體中文';
            else if (currentLang === 'zh') textEl.textContent = '简体中文';
            else textEl.textContent = 'English';
        }
        sel.querySelectorAll('.cfg-dropdown-item').forEach(i => {
            i.classList.toggle('active', i.dataset.value === currentLang);
        });
    }
}

// Keep the full language name on desktop and a compact label on narrow screens.
function _syncLangControls() {
    const langLabel = document.getElementById('lang-label');
    const shortLabel = document.getElementById('lang-label-short');
    if (langLabel) langLabel.textContent = currentLang === 'zh-Hant' ? '繁體中文' : currentLang === 'zh' ? '简体中文' : 'English';
    if (shortLabel) shortLabel.textContent = currentLang === 'zh-Hant' ? '繁' : currentLang === 'zh' ? '简' : 'EN';
    document.querySelectorAll('#lang-menu .lang-menu-item').forEach(item => {
        const active = item.dataset.lang === currentLang;
        item.classList.toggle('text-blue-600', active);
        item.classList.toggle('dark:text-blue-400', active);
        item.classList.toggle('font-medium', active);
    });
}

// Toggle the header language dropdown menu open/closed.
function toggleLangMenu(event) {
    closeAccountMenu();
    if (event) event.stopPropagation();
    const menu = document.getElementById('lang-menu');
    if (menu) menu.classList.toggle('hidden');
}

// Pick a language from the dropdown, then close the menu.
function selectLanguage(lang) {
    const menu = document.getElementById('lang-menu');
    if (menu) menu.classList.add('hidden');
    // Top-right header toggle: personal, browser-local, never writes instance.
    setLanguageLocal(lang);
}
window.toggleLangMenu = toggleLangMenu;
window.selectLanguage = selectLanguage;

// Close the language menu when clicking outside of it.
document.addEventListener('click', (e) => {
    const selector = document.getElementById('lang-selector');
    const menu = document.getElementById('lang-menu');
    if (menu && !menu.classList.contains('hidden') && selector && !selector.contains(e.target)) {
        menu.classList.add('hidden');
    }
});

// Refresh JS-rendered views after a language switch. Each branch uses the
// lightweight in-memory re-render path (no extra network round-trips).
function rerenderDynamicViews() {
    if (currentView === 'history') {
        _closeSessionActionMenu();
        _renderSessionList();
        _updateHistorySearchControls();
        _renderHistoryStatus();
    }
    if (currentView === 'agent-workbench') renderAgentWorkbench();
    // Models are a tab of the config view, not a view of their own.
    if (currentView === 'config' && typeof renderModelsView === 'function'
            && modelsState && (modelsState.providers || modelsState.capabilities)) {
        renderModelsView();
    }
    // Reload task list after language switch
    if (currentView === 'tasks') {
        tasksLoaded = false;
        loadTasksView();
    }
    // Reload skills and tools after language switch
    if (currentView === 'skills') {
        toolsLoaded = false;
        loadSkillsView();
    }
    // Reload channels after language switch
    if (currentView === 'channels') {
        loadChannelsView();
    }
    // Reload config after language switch
    if (currentView === 'config') {
        loadConfigView();
    }
    // Reload identity/admin views after language switch
    if (currentView === 'platform') loadPlatformUsersView();
    if (currentView === 'tenant') loadTenantView();
    if (currentView === 'system_user') loadMembersView();
    if (currentView === 'roles') loadRolesView();
    if (currentView === 'org') loadOrgView();
    if (currentView === 'audit') loadAuditView();
}

// Floating tooltip portal for [data-tip-key] elements. Tooltip nodes are
// appended to <body> so they aren't clipped by overflow:hidden ancestors
// (e.g. the config panel's scroll container).
let _cfgTipPortalEl = null;
let _cfgTipPortalInstalled = false;
function installCfgTipPortal() {
    if (_cfgTipPortalInstalled) return;
    _cfgTipPortalInstalled = true;

    const showTip = (target) => {
        const text = target.getAttribute('data-tooltip');
        if (!text) return;
        if (!_cfgTipPortalEl) {
            _cfgTipPortalEl = document.createElement('div');
            _cfgTipPortalEl.className = 'cfg-tip-floating';
            document.body.appendChild(_cfgTipPortalEl);
        }
        _cfgTipPortalEl.textContent = text;
        const rect = target.getBoundingClientRect();
        // Render once to measure, then position relative to the target.
        _cfgTipPortalEl.style.left = '0px';
        _cfgTipPortalEl.style.top = '0px';
        _cfgTipPortalEl.classList.add('show');
        const tipRect = _cfgTipPortalEl.getBoundingClientRect();
        let left = rect.left + rect.width / 2 - tipRect.width / 2;
        // Clamp horizontally to the viewport with an 8px gutter.
        left = Math.max(8, Math.min(left, window.innerWidth - tipRect.width - 8));
        // Default above the target; place below when data-tooltip-pos="bottom".
        const below = target.getAttribute('data-tooltip-pos') === 'bottom';
        const top = below ? rect.bottom + 6 : rect.top - tipRect.height - 6;
        _cfgTipPortalEl.style.left = left + 'px';
        _cfgTipPortalEl.style.top = top + 'px';
    };
    const hideTip = () => {
        if (_cfgTipPortalEl) _cfgTipPortalEl.classList.remove('show');
    };

    // Matches config keys and any element opting into the floating tooltip via
    // [data-tip-float] (used for dynamic tooltips like the workspace selector,
    // whose data-tooltip is set at runtime rather than from a translation key).
    const _tipSel = '[data-tip-key],[data-tip-float]';
    document.addEventListener('mouseover', (e) => {
        const target = e.target.closest(_tipSel);
        if (target) showTip(target);
    });
    document.addEventListener('mouseout', (e) => {
        const target = e.target.closest(_tipSel);
        if (target) hideTip();
    });
    // Hide on scroll/resize so the tooltip doesn't drift away from its anchor.
    window.addEventListener('scroll', hideTip, true);
    window.addEventListener('resize', hideTip);
}

// =====================================================================
// Theme
// =====================================================================
// The pre-paint controller owns state; this is a resolved-mode projection for
// existing console consumers, not a second persisted preference.
let currentTheme = window.CowAppearance.getState().resolved;
let appearanceTrigger = null;

function renderAppearancePreferences() {
    const state = window.CowAppearance.getState();
    document.querySelectorAll('input[name="web-palette"]').forEach(input => {
        input.checked = input.value === state.palette;
    });
    document.querySelectorAll('input[name="web-mode"]').forEach(input => {
        input.checked = input.value === state.mode;
    });
    document.querySelectorAll('input[name="web-language"]').forEach(input => {
        input.checked = input.value === currentLang;
    });
    const warning = document.getElementById('appearance-storage-warning');
    if (warning) { warning.hidden = !state.storageFailed; warning.textContent = t('appearance_storage_failed'); }
    const languageWarning = document.getElementById('appearance-language-warning');
    if (languageWarning) {
        languageWarning.hidden = !languageStorageFailed;
        languageWarning.textContent = t('account_prefs_storage_fail');
    }
    const resolved = document.getElementById('appearance-resolved');
    if (resolved) {
        resolved.hidden = state.mode !== 'system';
        resolved.textContent = t('appearance_resolved_' + state.resolved);
    }
}

window.CowAppearance.subscribe(state => {
    currentTheme = state.resolved;
    const icon = document.getElementById('theme-icon');
    if (icon) icon.className = 'fas fa-palette';
    const light = document.getElementById('hljs-light');
    const dark = document.getElementById('hljs-dark');
    if (light) light.disabled = state.resolved === 'dark';
    if (dark) dark.disabled = state.resolved !== 'dark';
    renderAppearancePreferences();
});

function applyTheme() { window.CowAppearance.apply(); }

// Compatibility for existing direct light/dark actions, without another store.
function toggleTheme() {
    window.CowAppearance.setMode(currentTheme === 'dark' ? 'light' : 'dark');
}

function openAppearancePreferences(trigger) {
    const dialog = document.getElementById('appearance-dialog');
    if (!dialog || dialog.open) return;
    if (!closeAccountPanels(false)) return;
    appearanceTrigger = trigger || document.activeElement;
    closeAccountMenu();
    ['lang-menu', 'tenant-menu'].forEach(id => _accountHidden(id, true));
    renderAppearancePreferences();
    dialog.showModal();
    _setAccountPanel('prefs');
    dialog.querySelector('input[name="web-palette"]:checked')?.focus();
}

function closeAppearancePreferences(returnFocus = true) {
    const dialog = document.getElementById('appearance-dialog');
    if (dialog?.open) {
        if (!returnFocus) appearanceTrigger = null;
        if (_activeAccountPanel === 'prefs') _setAccountPanel(null);
        dialog.close();
    }
}

const appearanceDialog = document.getElementById('appearance-dialog');
if (appearanceDialog) {
    appearanceDialog.addEventListener('close', () => {
        if (_activeAccountPanel === 'prefs') _setAccountPanel(null);
        // A login transition has its own focus target; do not focus the
        // now-hidden workbench after its modal is dismissed.
        if (!appearanceTrigger) return;
        if (appearanceTrigger?.isConnected && appearanceTrigger.getClientRects().length) appearanceTrigger.focus();
        else document.getElementById('theme-toggle')?.focus();
        appearanceTrigger = null;
    });
    appearanceDialog.addEventListener('click', event => {
        if (event.target !== appearanceDialog) return;
        const rect = appearanceDialog.getBoundingClientRect();
        if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) {
            closeAppearancePreferences();
        }
    });
}

// =====================================================================
// Task completion notification (client-side preference)
// =====================================================================
const TASK_NOTIFY_KEY = 'cow_task_notify';
const TASK_NOTIFY_SOUND_KEY = 'cow_task_notify_sound';
let taskNotifyEnabled = readStartupPreference(TASK_NOTIFY_KEY) !== '0';
let taskNotifySound = readStartupPreference(TASK_NOTIFY_SOUND_KEY) !== '0';
let notifyAudioCtx = null;
let unreadCount = 0;
const baseDocTitle = document.title;

// Unlock audio on the first user gesture; browsers block autoplay otherwise.
document.addEventListener('pointerdown', function() {
    if (window.AudioContext) notifyAudioCtx = notifyAudioCtx || new AudioContext();
    if (notifyAudioCtx && notifyAudioCtx.state === 'suspended') {
        notifyAudioCtx.resume().catch(function() {});
    }
}, { once: true });

function playNotifyBeep() {
    if (!taskNotifySound) return;
    try {
        if (!notifyAudioCtx) {
            const Ctx = window.AudioContext || window.webkitAudioContext;
            if (!Ctx) return;
            notifyAudioCtx = new Ctx();
        }
        if (notifyAudioCtx.state === 'suspended') {
            notifyAudioCtx.resume().catch(function() {});
        }
        // Two short sine tones (A5 → D6); no audio asset needed.
        const t0 = notifyAudioCtx.currentTime;
        [880, 1174.66].forEach(function(freq, i) {
            const at = t0 + i * 0.09;
            const osc = notifyAudioCtx.createOscillator();
            const gain = notifyAudioCtx.createGain();
            osc.type = 'sine';
            osc.frequency.value = freq;
            gain.gain.setValueAtTime(0.001, at);
            gain.gain.exponentialRampToValueAtTime(0.12, at + 0.01);
            gain.gain.exponentialRampToValueAtTime(0.001, at + 0.09);
            osc.connect(gain).connect(notifyAudioCtx.destination);
            osc.start(at);
            osc.stop(at + 0.1);
        });
    } catch (_) {
        // Autoplay still blocked or AudioContext unavailable; stay silent.
    }
}

function firstLineSnippet(text) {
    return (text || '').split('\n')[0].trim().slice(0, 80);
}

function sessionTitleOf(sid) {
    const el = document.querySelector(`.session-item[data-session-id="${sid}"] .session-title`);
    return el ? el.textContent.trim() : '';
}

function popNotification(title, body, sid) {
    if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
    try {
        const n = new Notification(title, { body: body || title });
        n.onclick = function() {
            window.focus();
            if (sid && sid !== sessionId) switchSession(sid);
            n.close();
        };
    } catch (_) {
        // Notification API unavailable; beep + title badge still applied.
    }
}

function showTaskNotification(title, body, sid) {
    if (!taskNotifyEnabled) return;
    // Only notify when the window is not focused. If the user is actively
    // watching the tab, the reply is already on screen — a notification/beep
    // would just be noise (especially for short tasks).
    if (document.hasFocus()) return;
    playNotifyBeep();
    if (document.hidden) {
        unreadCount += 1;
        document.title = `(${unreadCount}) ${baseDocTitle}`;
    }
    if (typeof Notification === 'undefined') return;
    // First time we actually need to notify (window is in the background):
    // request permission now, then show this notification once granted. This
    // is more contextual than prompting on page load.
    if (Notification.permission === 'default') {
        Notification.requestPermission()
            .then(function(perm) {
                if (perm === 'granted') popNotification(title, body, sid);
                else refreshNotifyBlockedHint();
            })
            .catch(function() {});
        return;
    }
    if (Notification.permission === 'denied') {
        // Can't notify; surface the hint in settings so the user knows why.
        refreshNotifyBlockedHint();
        return;
    }
    popNotification(title, body, sid);
}

function notifyTaskFinished(sid, kind, text) {
    const label = t(kind === 'error' ? 'notify_task_error' : 'notify_task_done');
    const snippet = firstLineSnippet(text);
    showTaskNotification(sessionTitleOf(sid) || label, snippet ? `${label}: ${snippet}` : label, sid);
}

document.addEventListener('visibilitychange', function() {
    if (!document.hidden) {
        unreadCount = 0;
        document.title = baseDocTitle;
    }
});

// Request OS notification permission when notifications are enabled and the
// browser hasn't decided yet. Safe to call repeatedly.
function ensureNotifyPermission() {
    if (taskNotifyEnabled
        && typeof Notification !== 'undefined'
        && Notification.permission === 'default') {
        Notification.requestPermission().catch(function() {});
    }
}

// Show the "blocked by browser" hint only when notifications are enabled but
// the browser permission is denied (nothing the app can do about it in code).
function refreshNotifyBlockedHint() {
    const el = document.getElementById('cfg-task-notify-blocked');
    if (!el) return;
    const blocked = taskNotifyEnabled
        && typeof Notification !== 'undefined'
        && Notification.permission === 'denied';
    el.classList.toggle('hidden', !blocked);
}

function initTaskNotifyToggles() {
    const notifyEl = document.getElementById('cfg-task-notify');
    if (notifyEl) {
        notifyEl.checked = taskNotifyEnabled;
        notifyEl.addEventListener('change', function() {
            taskNotifyEnabled = notifyEl.checked;
            localStorage.setItem(TASK_NOTIFY_KEY, taskNotifyEnabled ? '1' : '0');
            ensureNotifyPermission();
            refreshNotifyBlockedHint();
        });
    }
    const soundEl = document.getElementById('cfg-task-notify-sound');
    if (soundEl) {
        soundEl.checked = taskNotifySound;
        soundEl.addEventListener('change', function() {
            taskNotifySound = soundEl.checked;
            localStorage.setItem(TASK_NOTIFY_SOUND_KEY, taskNotifySound ? '1' : '0');
        });
    }
    refreshNotifyBlockedHint();
}

document.addEventListener('DOMContentLoaded', initTaskNotifyToggles);

// Homepage labels share the existing locale catalog.
Object.assign(I18N["zh"], {
    "home_new_chat": "新建对话",
    "home_toggle_sidebar": "展开或收起侧栏",
    "home_manage_monitor": "管理与监控",
    "home_resources": "资源管理",
    "home_assistant": "你的工作助手",
    "home_greeting": "你好，今天想完成什么？",
    "home_description": "从一个问题开始，让 AI 帮你查资料、处理文件、安排任务。",
    "home_suggestions": "也可以从这里开始",
    "home_workspace_title": "查看工作空间",
    "home_workspace_text": "快速了解文件与目录",
    "home_workspace_prompt": "查看当前工作空间的文件与目录，帮我整理一份概览。",
    "home_reminder_title": "设置定时提醒",
    "home_reminder_text": "把待办交给 AI 安排",
    "home_reminder_prompt": "帮我设置一个定时提醒，请先询问我要提醒的事项和时间。",
    "home_research_title": "搜索并整理资料",
    "home_research_text": "汇总信息，生成报告",
    "home_research_prompt": "帮我搜索并整理资料，请先询问研究主题和报告要求。",
    "home_knowledge_title": "让 AI 梳理知识库",
    "home_knowledge_text": "填写草稿，整理知识库内容",
    "home_knowledge_prompt": "查看知识库当前收录的文档，帮我整理一份概览。",
    "home_skills_title": "探索工具与技能",
    "home_skills_text": "发现 AI 可以帮你做什么",
    "home_skills_prompt": "查看所有支持的工具和技能，并介绍它们适合处理哪些任务。",
    "home_commands_title": "指令中心",
    "home_commands_text": "查看可用命令与使用方法",
    "home_commands_prompt": "/help",
    "home_footer": "清晰描述目标，让每一次对话更有成果",
    "home_input_placeholder": "描述你的任务，或直接提问…",
    "home_composer_hint": "/ 使用指令 · @ 引用智能体或文件",
    "scenes_title": "场景应用",
    "scenes_subtitle": "选择一个业务场景，进入专用工作台或对话上下文。",
    "scenes_loading": "加载场景中...",
    "scenes_empty": "当前分类没有可用的场景。",
    "scenes_no_category": "当前分类没有可用的场景。",
    "scenes_go_chat": "开始对话",
    "scenes_all": "全部",
    "scenes_workbench": "工作台",
    "scenes_activate_failed": "场景激活失败，请稍后重试。",
    "scenes_picker_title": "选择场景",
    "scenes_picker_placeholder": "搜索场景...",
    "scenes_picker_empty": "没有匹配的场景",
    "scenes_greeting": "已进入「{name}」场景。",
    "slash_scenes": "打开场景选择器"
});
Object.assign(I18N["zh-Hant"], {
    "home_new_chat": "新增對話",
    "home_toggle_sidebar": "展開或收起側欄",
    "home_manage_monitor": "管理與監控",
    "home_resources": "資源管理",
    "home_assistant": "你的工作助手",
    "home_greeting": "你好，今天想完成什麼？",
    "home_description": "從一個問題開始，讓 AI 幫你查資料、處理檔案、安排任務。",
    "home_suggestions": "也可以從這裡開始",
    "home_workspace_title": "查看工作空間",
    "home_workspace_text": "快速了解檔案與目錄",
    "home_workspace_prompt": "查看目前工作空間的檔案與目錄，幫我整理一份概覽。",
    "home_reminder_title": "設定定時提醒",
    "home_reminder_text": "把待辦交給 AI 安排",
    "home_reminder_prompt": "幫我設定一個定時提醒，請先詢問我要提醒的事項和時間。",
    "home_research_title": "搜尋並整理資料",
    "home_research_text": "彙整資訊，產生報告",
    "home_research_prompt": "幫我搜尋並整理資料，請先詢問研究主題和報告要求。",
    "home_knowledge_title": "讓 AI 梳理知識庫",
    "home_knowledge_text": "填入草稿，整理知識庫內容",
    "home_knowledge_prompt": "查看知識庫目前收錄的文件，幫我整理一份概覽。",
    "home_skills_title": "探索工具與技能",
    "home_skills_text": "發現 AI 可以幫你做什麼",
    "home_skills_prompt": "查看所有支援的工具和技能，並介紹它們適合處理哪些任務。",
    "home_commands_title": "指令中心",
    "home_commands_text": "查看可用命令與使用方法",
    "home_commands_prompt": "/help",
    "home_footer": "清晰描述目標，讓每一次對話更有成果",
    "home_input_placeholder": "描述你的任務，或直接提問…",
    "home_composer_hint": "/ 使用指令 · @ 引用智慧體或檔案",
    "scenes_title": "場景應用",
    "scenes_subtitle": "選擇一個業務場景，進入專用工作台或對話上下文。",
    "scenes_loading": "載入場景中...",
    "scenes_empty": "目前分類沒有可用的場景。",
    "scenes_no_category": "目前分類沒有可用的場景。",
    "scenes_go_chat": "開始對話",
    "scenes_all": "全部",
    "scenes_workbench": "工作台",
    "scenes_activate_failed": "場景啟用失敗，請稍後重試。",
    "scenes_picker_title": "選擇場景",
    "scenes_picker_placeholder": "搜尋場景...",
    "scenes_picker_empty": "沒有符合的場景",
    "scenes_greeting": "已進入「{name}」場景。",
    "slash_scenes": "開啟場景選擇器"
});
Object.assign(I18N["en"], {
    "home_new_chat": "New chat",
    "home_toggle_sidebar": "Expand or collapse sidebar",
    "home_manage_monitor": "Manage & monitor",
    "home_resources": "Resources",
    "home_assistant": "Your work assistant",
    "home_greeting": "What would you like to do today?",
    "home_description": "Start with a question. Let AI help you research, work with files, and plan tasks.",
    "home_suggestions": "You can also start here",
    "home_workspace_title": "Explore your workspace",
    "home_workspace_text": "Get an overview of files and folders",
    "home_workspace_prompt": "Review the files and folders in my current workspace and give me an overview.",
    "home_reminder_title": "Set a reminder",
    "home_reminder_text": "Let AI help organize your to-dos",
    "home_reminder_prompt": "Help me set a reminder. First ask what to remind me about and when.",
    "home_research_title": "Research and organize",
    "home_research_text": "Gather information and create a report",
    "home_research_prompt": "Help me research and organize information. First ask for the topic and report requirements.",
    "home_knowledge_title": "Let AI summarize the knowledge base",
    "home_knowledge_text": "Fill a draft to summarize knowledge content",
    "home_knowledge_prompt": "Review the documents in my knowledge base and give me an overview.",
    "home_skills_title": "Discover tools and skills",
    "home_skills_text": "Find out what AI can help you do",
    "home_skills_prompt": "Show all available tools and skills, and explain which tasks they can help with.",
    "home_commands_title": "Command center",
    "home_commands_text": "See available commands and how to use them",
    "home_commands_prompt": "/help",
    "home_footer": "Describe your goal clearly to get more from every conversation.",
    "home_input_placeholder": "Describe your task, or ask a question…",
    "home_composer_hint": "/ Commands · @ Reference agents or files",
    "scenes_title": "Scenario Apps",
    "scenes_subtitle": "Pick a business scenario to open its workbench or conversation context.",
    "scenes_loading": "Loading scenarios...",
    "scenes_empty": "No scenarios available in this category.",
    "scenes_no_category": "No scenarios available in this category.",
    "scenes_go_chat": "Start chatting",
    "scenes_all": "All",
    "scenes_workbench": "Workbench",
    "scenes_activate_failed": "Failed to activate the scenario, please try again.",
    "scenes_picker_title": "Choose a scenario",
    "scenes_picker_placeholder": "Search scenarios...",
    "scenes_picker_empty": "No matching scenarios",
    "scenes_greeting": "Switched to the \"{name}\" scenario.",
    "slash_scenes": "Open the scenario picker"
});

// =====================================================================
// Sidebar & Navigation
// =====================================================================
const VIEW_META = {
    chat:     { group: 'nav_workbench', page: 'menu_chat', console: 'workbench.chat' },
    history:  { group: 'nav_workbench', page: 'session_history', console: 'workbench.history' },
    'agent-workbench': { group: 'nav_workbench', page: 'menu_agents', console: 'workbench.agents' },
    todo:     { group: 'nav_workbench', page: 'menu_todo', console: 'workbench.todos' },
    tasks:    { group: 'nav_workbench', page: 'menu_tasks', console: 'workbench.schedules' },
    knowledge:{ group: 'nav_workbench', page: 'menu_knowledge', console: 'workbench.knowledge' },
    scenes:   { group: 'nav_workbench', page: 'menu_scenes', console: 'workbench.scenes' },
    agents:   { group: 'nav_group_agent_dev', page: 'menu_agent_config', console: 'admin.agents' },
    skills:   { group: 'nav_group_agent_dev', page: 'menu_skills', console: 'admin.skills' },
    memory:   { group: 'nav_group_agent_dev', page: 'menu_memory', console: 'admin.memory' },
    config:   { group: 'nav_group_model_access', page: 'menu_config', console: 'admin.models' },
    channels: { group: 'nav_group_model_access', page: 'menu_channels', console: 'admin.channels' },
    system_user: { group: 'nav_group_org_perm', page: 'menu_system_user', console: 'admin.roles' },
    roles:       { group: 'nav_group_org_perm', page: 'menu_roles', console: 'admin.roles' },
    org:         { group: 'nav_group_org_perm', page: 'menu_org', console: 'admin.organization' },
    tenant:      { group: 'nav_group_platform_ops', page: 'menu_tenant', console: 'admin.tenants' },
    platform:    { group: 'nav_group_platform_ops', page: 'menu_platform', console: 'admin.tenants' },
    branding:    { group: 'nav_group_platform_ops', page: 'menu_branding', console: 'admin.branding' },
    logs:        { group: 'nav_group_platform_ops', page: 'menu_logs', console: 'admin.logs' },
    audit:       { group: 'nav_group_platform_ops', page: 'menu_audit', console: 'admin.settings' },
    'admin-home': { group: 'nav_admin_console', page: 'admin_home_title', console: null },
};

function _viewTargetArea(viewId) {
    if (viewId === 'admin-home') return 'admin';
    const meta = VIEW_META[viewId];
    const key = meta && meta.console;
    if (key && String(key).indexOf('admin.') === 0) return 'admin';
    return 'workbench';
}

function _bootAreaDefaultView() {
    const area = _navAreaFromPath(location.pathname);
    if (area === 'admin') {
        let pending = null;
        try {
            pending = sessionStorage.getItem('cow_admin_pending_view');
            sessionStorage.removeItem('cow_admin_pending_view');
        } catch (_) {}
        if (pending && VIEW_META[pending]) navigateTo(pending);
        else navigateTo('admin-home');
        return;
    }
    let pendingWb = null;
    try {
        pendingWb = sessionStorage.getItem('cow_workbench_pending_view');
        sessionStorage.removeItem('cow_workbench_pending_view');
        if (sessionStorage.getItem('cow_nav_admin_denied') === '1') {
            sessionStorage.removeItem('cow_nav_admin_denied');
            const status = document.getElementById('account-menu-status');
            if (status) {
                status.textContent = t('nav_admin_denied');
                status.classList.remove('hidden');
            }
        }
    } catch (_) {}
    if (pendingWb && VIEW_META[pendingWb]) navigateTo(pendingWb);
}

function initAdminHomeView() {
    const box = document.getElementById('admin-home-shortcuts');
    if (!box) return;
    box.innerHTML = '';
    document.querySelectorAll('[data-nav-shell="admin"] .sidebar-item[data-view]').forEach(item => {
        if (item.classList.contains('hidden')) return;
        const viewId = item.dataset.view;
        if (!viewId || !VIEW_META[viewId]) return;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'admin-home-shortcut';
        const label = item.querySelector('[data-i18n]');
        const text = label ? label.textContent : t(VIEW_META[viewId].page);
        const icon = item.querySelector('i');
        const iconHtml = icon ? ('<i class="' + icon.className + '" aria-hidden="true"></i>') : '';
        const span = document.createElement('span');
        span.textContent = text;
        btn.innerHTML = iconHtml;
        btn.appendChild(span);
        btn.addEventListener('click', () => navigateTo(viewId));
        box.appendChild(btn);
    });
}

// Known previously-visible targets whose feature is not yet enabled. These are
// removed from the normal sidebar, but old internal IDs and direct links must
// not silently no-op: route them to a clear "not available" view with a return.
// 场景应用（scenes）已由本变更转为真实入口，其旧占位 id「scenarios」在
// navigateTo 中重定向到 scenes，不再走「功能尚未开放」分支。
const UNAVAILABLE_VIEWS = new Set(['backup', 'open_api']);

function showUnavailableView(viewId, reason) {
    // reason: undefined/'' -> feature not yet open (nav_unavailable);
    // 'denied' -> the identity lacks read access (nav_denied, distinct copy).
    // A denied target does NOT silently switch scope: we render the denial
    // explanation and only offer a way back.
    const denied = reason === 'denied';
    currentView = viewId;
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    const target = document.getElementById('view-unavailable');
    if (target) target.classList.add('active');
    document.querySelectorAll('.sidebar-item').forEach(item => {
        item.classList.remove('active');
        item.removeAttribute('aria-current');
    });
    document.getElementById('breadcrumb-group').textContent = t('nav_system');
    document.getElementById('breadcrumb-group').dataset.i18n = 'nav_system';
    const pageKey = denied ? 'nav_denied' : 'nav_unavailable';
    document.getElementById('breadcrumb-page').textContent = t(pageKey);
    document.getElementById('breadcrumb-page').dataset.i18n = pageKey;
    // Swap the title/hint copy and icon for the denied case.
    const title = document.getElementById('nav-unavailable-title');
    const hint = document.getElementById('nav-unavailable-hint');
    const icon = target.querySelector('i.fas');
    if (denied) {
        if (title) { title.textContent = t('nav_denied'); title.dataset.i18n = 'nav_denied'; }
        if (hint) { hint.textContent = t('nav_denied_hint'); hint.dataset.i18n = 'nav_denied_hint'; }
        if (icon) icon.classList.replace('fa-hourglass-half', 'fa-lock');
    } else {
        if (title) { title.textContent = t('nav_unavailable'); title.dataset.i18n = 'nav_unavailable'; }
        if (hint) { hint.textContent = t('nav_unavailable_hint'); hint.dataset.i18n = 'nav_unavailable_hint'; }
        if (icon) icon.classList.replace('fa-lock', 'fa-hourglass-half');
    }
    document.getElementById('chat-agent-identity')?.classList.toggle('hidden', true);
    document.getElementById('workspace-toggle-btn')?.classList.toggle('hidden', true);
    if (window.innerWidth < 1024) closeSidebar();
}

let currentView = 'chat';
let agentNavigationVersion = 0;

function navigateTo(viewId) {
    // 旧「场景应用」占位 id 重定向到真实 scenes 视图（收藏/直链不失效）。
    if (viewId === 'scenarios') viewId = 'scenes';
    if (UNAVAILABLE_VIEWS.has(viewId)) {
        showUnavailableView(viewId);
        return;
    }
    if (!VIEW_META[viewId]) return;
    // Cross-area: open the other named window instead of rendering the wrong shell.
    const here = _navAreaFromPath(location.pathname);
    const want = _viewTargetArea(viewId);
    if (want !== here) {
        try {
            if (want === 'admin') sessionStorage.setItem('cow_admin_pending_view', viewId);
            else sessionStorage.setItem('cow_workbench_pending_view', viewId);
        } catch (_) {}
        _openNavArea(want);
        return;
    }
    // Authoritative availability gate (database mode only). A target the
    // identity may not read and that is not open is rendered as a denial, NOT
    // silently switched to another scope. Works only once the /auth/context
    // projection is known; until then navigation is not spuriously blocked.
    const deny = _viewNavDenied(viewId);
    if (deny) {
        showUnavailableView(viewId, deny.reason);
        return;
    }
    // Leaving the branding page with unsaved changes: ask to discard first.
    if (currentView === 'branding' && viewId !== 'branding' && brandingDirty) {
        brandingConfirmDiscard(() => {
            _brandingResetDraftToBaseline();
            navigateTo(viewId);
        });
        return;
    }
    // Leaving an identity-admin view with an unsaved create/edit form open:
    // ask to discard before switching (see identity-admin.js).
    const _adminLeaving = (currentView === 'tenant' || currentView === 'system_user'
        || currentView === 'roles' || currentView === 'org'
        || currentView === 'platform' || currentView === 'audit');
    if (_adminLeaving && viewId !== currentView
        && typeof window.__identityAdminDirtyGuard__ === 'function'
        && !window.__identityAdminDirtyGuard__()) {
        return;
    }
    if (viewId !== currentView) {
        agentNavigationVersion++;
        cancelAgentStart();
    }
    // Entering any functional view re-validates the public brand snapshot so
    // another tab's published change is picked up (unless the branding page
    // itself has a live draft, which is protected separately).
    if (viewId !== 'branding') fetchPublicBrand();

    // Leaving the history page: mark it dirty so a later re-entry re-reads the
    // newest list (titles/activity may have changed while we were elsewhere).
    if (currentView === 'history' && viewId !== 'history') {
        _historyDirty = true;
        _cancelHistoryRequest();
        _closeSessionActionMenu();
    }

    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    const target = document.getElementById('view-' + viewId);
    if (target) target.classList.add('active');
    document.querySelectorAll('.sidebar-item').forEach(item => {
        const selected = item.dataset.view === viewId;
        item.classList.toggle('active', selected);
        if (selected) item.setAttribute('aria-current', 'page');
        else item.removeAttribute('aria-current');
        if (selected) {
            const resources = item.closest('details');
            if (resources) resources.open = true;
            const group = item.closest('.menu-group');
            if (group) { group.classList.add('open'); group.querySelector('button')?.setAttribute('aria-expanded', 'true'); }
        }
    });
    const meta = VIEW_META[viewId];
    document.getElementById('breadcrumb-group').textContent = t(meta.group);
    document.getElementById('breadcrumb-group').dataset.i18n = meta.group;
    document.getElementById('breadcrumb-page').textContent = t(meta.page);
    document.getElementById('breadcrumb-page').dataset.i18n = meta.page;
    currentView = viewId;
    document.getElementById('chat-agent-identity')?.classList.toggle('hidden', viewId !== 'chat');
    document.getElementById('workspace-toggle-btn')?.classList.toggle('hidden', viewId !== 'chat');
    if (viewId === 'branding') initBrandingView();
    if (viewId === 'platform') loadPlatformUsersView();
    if (viewId === 'tenant') loadTenantView();
    if (viewId === 'system_user') loadMembersView();
    if (viewId === 'roles') loadRolesView();
    if (viewId === 'org') loadOrgView();
    if (viewId === 'audit') loadAuditView();
    if (viewId === 'admin-home') initAdminHomeView();
    // The Agent detail is a fixed drawer, so it would otherwise hang over
    // whatever view you navigate to. It only belongs to the Agent Config page.
    if (viewId !== 'agents') closeAgentDetail();

    // Entering the history page: it is now the active consumer, so (re)load its
    // list. Re-reading only happens when dirty or the list is empty, so a plain
    // in-page refresh is not spuriously overwritten by a stale read.
    if (viewId === 'history') {
        _historyVisible = true;
        if (_historyDirty || !_sessionItems.length) {
            _historyDirty = false;
            loadSessionList();
        }
    } else {
        _historyVisible = false;
    }

    if (viewId === 'agents') {
        loadAgentCatalog();
    } else if (viewId === 'agent-workbench') {
        loadAgentWorkbench();
    } else if (viewId === 'scenes') {
        if (typeof window.loadScenesView === 'function') window.loadScenesView();
    }

    // Clear status messages when navigating away
    document.querySelectorAll('[id$="-status"]').forEach(el => {
        el.classList.add('opacity-0');
    });

    if (viewId === 'history') _renderHistoryStatus();

    if (window.innerWidth < 1024) closeSidebar();
}

function toggleSidebar() {
    if (window.innerWidth >= 1024) {
        closeAccountMenu();
        const collapsed = document.getElementById('app').classList.toggle('sidebar-collapsed');
        document.getElementById('menu-toggle')?.setAttribute('aria-expanded', String(!collapsed));
        return;
    }
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    const isOpen = !sidebar.classList.contains('-translate-x-full');
    if (isOpen) {
        closeSidebar();
    } else {
        sidebar.classList.remove('-translate-x-full');
        overlay.classList.remove('hidden');
        document.getElementById('menu-toggle')?.setAttribute('aria-expanded', 'true');
    }
}

function closeSidebar() {
    closeAccountMenu();
    document.getElementById('sidebar').classList.add('-translate-x-full');
    document.getElementById('sidebar-overlay').classList.add('hidden');
    if (window.innerWidth < 1024) document.getElementById('menu-toggle')?.setAttribute('aria-expanded', 'false');
}

function startSidebarNewChat() {
    if (typeof wsGuardUnsaved === 'function' && !wsGuardUnsaved(startSidebarNewChat)) return;
    if (currentView === 'branding' && brandingDirty) {
        brandingConfirmDiscard(() => { _brandingResetDraftToBaseline(); startSidebarNewChat(); });
        return;
    }
    navigateTo('chat');
    if (currentView !== 'chat') return;
    newChat();
    focusChatComposer();
}

// Keep closed groups out of the keyboard focus order and move focus to the
// group trigger when a group that currently holds focus is collapsed. This
// fixes the "collapsed group still Tab-focusable" accessibility problem.
function _syncMenuGroupFocusability() {
    document.querySelectorAll('.menu-group').forEach(group => {
        const open = group.classList.contains('open');
        group.querySelectorAll('.sidebar-item').forEach(item => {
            item.tabIndex = open ? 0 : -1;
        });
    });
    // The resources <details> is a native disclosure; only its open children
    // should be focusable.
    const resources = document.getElementById('sidebar-resources');
    if (resources) {
        const open = resources.open;
        resources.querySelectorAll('.sidebar-item').forEach(item => {
            item.tabIndex = open ? 0 : -1;
        });
    }
}

function _collapseMenuGroup(group, trigger) {
    group.querySelectorAll('.sidebar-item').forEach(item => { item.tabIndex = -1; });
    const focusedInGroup = group.contains(document.activeElement);
    group.classList.remove('open');
    trigger.setAttribute('aria-expanded', 'false');
    if (focusedInGroup) trigger.focus();
}

document.querySelectorAll('.menu-group > button').forEach(btn => {
    const label = btn.querySelector('[data-i18n]');
    if (label) { btn.dataset.i18nTitle = label.dataset.i18n; btn.title = t(label.dataset.i18n); }
    btn.setAttribute('aria-expanded', String(btn.parentElement.classList.contains('open')));
    btn.addEventListener('click', () => {
        if (window.innerWidth >= 1024 && document.getElementById('app').classList.contains('sidebar-collapsed')) toggleSidebar();
        const group = btn.parentElement;
        const opening = !group.classList.contains('open');
        group.classList.toggle('open', opening);
        btn.setAttribute('aria-expanded', String(opening));
        if (opening) _syncMenuGroupFocusability();
        else _collapseMenuGroup(group, btn);
    });
});
document.querySelector('#sidebar-resources summary')?.addEventListener('click', () => {
    if (window.innerWidth >= 1024 && document.getElementById('app').classList.contains('sidebar-collapsed')) toggleSidebar();
    const resources = document.getElementById('sidebar-resources');
    // The browser toggles `open` on the native <details>; re-sync focusability.
    setTimeout(_syncMenuGroupFocusability, 0);
});

document.querySelectorAll('.sidebar-item').forEach(item => {
    item.setAttribute('role', 'link');
    item.tabIndex = 0;
    const label = item.querySelector('[data-i18n]');
    if (label) { item.dataset.i18nTitle = label.dataset.i18n; item.title = t(label.dataset.i18n); }
    if (item.classList.contains('active')) item.setAttribute('aria-current', 'page');
    item.addEventListener('click', (event) => {
        if (item.id === 'nav-open-admin') {
            event.preventDefault();
            _openNavArea('admin');
            return;
        }
        if (item.id === 'nav-return-workbench') {
            event.preventDefault();
            _openNavArea('workbench');
            return;
        }
        if (!item.dataset.view) return;
        navigateTo(item.dataset.view);
    });
    item.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            if (item.id === 'nav-open-admin') { _openNavArea('admin'); return; }
            if (item.id === 'nav-return-workbench') { _openNavArea('workbench'); return; }
            if (!item.dataset.view) return;
            navigateTo(item.dataset.view);
        }
    });
});

// Ensure closed groups are excluded from the tab order (run after the items
// get their default tabIndex so it is authoritative).
_syncMenuGroupFocusability();

// Return to an available page from the "not available" view (unreachable).
document.getElementById('nav-unavailable-back')?.addEventListener('click', () => {
    const next = ['chat', 'history', 'agent-workbench', 'todo', 'tasks', 'knowledge', 'agents']
        .find(v => VIEW_META[v] && document.getElementById('view-' + v));
    if (next) navigateTo(next);
});

function syncSidebarToggleState() {
    const expanded = window.innerWidth >= 1024
        ? !document.getElementById('app').classList.contains('sidebar-collapsed')
        : !document.getElementById('sidebar').classList.contains('-translate-x-full');
    document.getElementById('menu-toggle')?.setAttribute('aria-expanded', String(expanded));
}
syncSidebarToggleState();
window.addEventListener('resize', syncSidebarToggleState);

window.addEventListener('resize', () => {
    closeAccountMenu();
    if (window.innerWidth >= 1024) {
        document.getElementById('sidebar').classList.remove('-translate-x-full');
        document.getElementById('sidebar-overlay').classList.add('hidden');
    } else {
        if (!document.getElementById('sidebar').classList.contains('-translate-x-full')) {
            closeSidebar();
        }
    }
});

// =====================================================================
// Agents
// =====================================================================
let agentCatalog = [];
let channelInstances = [];
let rosterRevision = '';
let defaultAgentId = readScopedPreference('cow_default_agent') || 'default';
let selectedAdminAgentId = '';
let selectedCoreRevision = '';
let installedSkills = [];
let installedTools = [];
function findAgent(agentId) {
    return agentCatalog.find(a => a.id === agentId) || null;
}

function normalizeAgentCatalogEntry(agent) {
    // Database mode returns only enabled, tenant-visible Agents, with can_chat
    // rather than the management snapshot's enabled flag. Keep runtime chat
    // readiness separate from configuration state and never infer permissions
    // from an unknown/missing capability.
    return {
        ...agent,
        name: /^cowagent$/i.test((agent.name || '').trim()) ? 'RongAI' : agent.name,
        enabled: typeof agent.enabled === 'boolean' ? agent.enabled : typeof agent.can_chat === 'boolean',
    };
}

function enabledAgents() {
    return agentCatalog.filter(a => a.enabled === true);
}

function availableChatAgents() {
    return enabledAgents().filter(a => a.can_chat === undefined || a.can_chat === true);
}

/* An uploaded avatar reuses the same URL every time, so the browser would keep
   serving the stale bytes. The roster revision only moves when the roster's
   *content* changes, and re-uploading over an existing image leaves the field as
   the same "image" token — so we stamp each successful upload with a fresh token
   here and prefer it, which forces the one re-fetch that shows the new picture. */
const avatarVersions = {};

/* How many muted discs the initials fallback cycles through. */
const AVATAR_TONES = 6;

/* Which disc an Agent gets. Keyed off the id alone, so a face never changes
   colour once the Agent exists, and so a draft in the create modal (no id yet)
   sits on the neutral tone instead of shifting as its name is typed. */
function avatarTone(agentId) {
    const key = String(agentId || '');
    let hash = 0;
    for (let i = 0; i < key.length; i++) hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
    return hash % AVATAR_TONES;
}

/* The character an Agent is shown by when it has no picture. Array.from rather
   than [0] so an astral-plane character is taken whole instead of as half a
   surrogate pair; uppercased for latin, left alone for scripts without case. */
function avatarInitial(name) {
    return (Array.from(String(name || '').trim())[0] || '').toUpperCase();
}

/* Every Agent wears its own face: the image its owner uploaded, or a muted disc
   carrying the first character of its name. Initials rather than the product
   logo so a team is distinguishable at a glance, and low-saturation tones so a
   roster of them stays quiet.

   A null agent means the id no longer resolves - a conversation pinned to a
   since-deleted Agent. Fall back to the default Agent's face rather than an
   empty disc, so the deleted Agent visibly degrades to the default one. */
function agentAvatarHTML(agent, size) {
    const cls = `agent-avatar agent-avatar-${size || 32}`;
    if (!agent && defaultAgentId) {
        agent = findAgent(defaultAgentId);
    }
    if (agent && agent.avatar === 'image') {
        const v = avatarVersions[agent.id] || rosterRevision || agent.id;
        return `<img class="${cls}" src="/api/agents/${encodeURIComponent(agent.id)}/avatar?v=${encodeURIComponent(v)}" alt="">`;
    }
    // The default (first) Agent falls back to the product logo when it has no
    // uploaded picture, so the instance's own Agent wears the product mark.
    // Added Agents keep the initial-disc fallback so a team stays distinguishable.
    if (agent && agent.id && (agent.is_default || agent.id === defaultAgentId)) {
        return `<img class="${cls} agent-avatar-brand" src="${effectiveLogoUrl()}" alt="">`;
    }
    const initial = avatarInitial(agent && (agent.name || agent.id));
    return `<span class="${cls} agent-avatar-tone-${avatarTone(agent && agent.id)}">${escapeHtml(initial)}</span>`;
}

/* Repaint the faces on bubbles already on screen. Bubbles are rendered once and
   left alone, so an avatar changed in Settings would otherwise keep showing the
   old picture in the open conversation until reload. Each bot bubble remembers
   its speaker; the loading indicator follows the active Agent. */
function refreshBubbleAvatars() {
    const container = document.getElementById('chat-messages');
    if (!container) return;
    container.querySelectorAll('.bot-face').forEach(face => {
        const group = face.closest('.bot-message-group');
        // A bubble knows its speaker; the loading indicator (no group) tracks the
        // active Agent, the only one that can be mid-reply in a solo chat.
        const id = (group && group.dataset.speakerAgent) || activeAgentId;
        face.innerHTML = agentAvatarHTML(findAgent(id), 32);
    });
}

// Derive the ascii slug from a name, or '' when there is no ascii to work with
// (e.g. a name written in Chinese). Callers fall back to randomAgentId().
function slugAgentId(name) {
    return String(name || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 32);
}

// An id for a name that yields no slug.
function randomAgentId() {
    return 'agent-' + Math.random().toString(36).slice(2, 8);
}

function loadAgentCatalog() {
    const epoch = _authEpoch, tenant = sessionStorage.getItem('cow_tenant_id');
    const current = () => epoch === _authEpoch && tenant === sessionStorage.getItem('cow_tenant_id');
    return fetch('/api/agents')
        .then(r => r.json())
        .then(data => {
            if (!current()) return;
            if (data.status !== 'success') throw new Error(data.message || 'Failed to load Agents');
            // Also handle a still-running backend or an older team file.
            agentCatalog = (data.agents || []).map(normalizeAgentCatalogEntry);
            channelInstances = data.channel_instances || [];
            rosterRevision = data.revision || '';
            defaultAgentId = data.default_agent_id || agentCatalog.find(agent => agent.is_default === true)?.id
                || (agentCatalog[0] && agentCatalog[0].id) || 'default';
            writeScopedPreference('cow_default_agent', defaultAgentId);
            // The default Agent leads every list it appears in — menus, the grid,
            // the memory picker — so its position never depends on load order.
            agentCatalog.sort((a, b) => (b.id === defaultAgentId) - (a.id === defaultAgentId));
            // Reading configuration must not replace a bound session's owner.
            // A deleted owner remains explicit and the server rejects its run.
            if (!activeAgentId) {
                activeAgentId = defaultAgentId;
                writeScopedPreference('cow_active_agent', activeAgentId);
            }
            if (!selectedAdminAgentId || !agentCatalog.some(a => a.id === selectedAdminAgentId)) {
                selectedAdminAgentId = '';
            }
            // Two-pane workbench: on a wide screen, land on the first Agent so the
            // right pane is never a blank placeholder. On a phone the list shows
            // first (the detail is a sheet), so leave nothing selected there.
            if (!selectedAdminAgentId && currentView === 'agents'
                    && agentCatalog.length && window.innerWidth > 900) {
                openAgentDetail((enabledAgents()[0] || agentCatalog[0]).id);
                return data;
            }
            renderAgentsGrid();
            if (selectedAdminAgentId) renderAgentDetail();
            else closeAgentDetail();
            renderComposerIdentity();
            renderMemoryAgentSelect();
            // The new-chat button only sprouts a menu (and its caret) once there
            // is more than one Agent to choose between.
            document.getElementById('new-chat-caret')?.classList.toggle('hidden', !multiAgentMode());
            // A name or avatar may have changed; keep faces already on screen in
            // sync with the roster instead of only new bubbles.
            refreshBubbleAvatars();
            return data;
        })
        .catch(err => {
            if (!current()) return;
            const status = document.getElementById('agent-editor-status');
            if (status) status.textContent = err.message;
        });
}

function renderAgentsGrid() {
    const grid = document.getElementById('agents-grid');
    if (!grid) return;
    if (!agentCatalog.length) {
        grid.innerHTML = `<div class="col-span-full text-sm text-slate-400 py-16 text-center">${escapeHtml(t('agents_empty'))}</div>`;
        return;
    }
    grid.innerHTML = agentCatalog.map(agent => {
        const selected = agent.id === selectedAdminAgentId;
        const desc = (agent.description || '').trim();
        // Status chips float in the top-right corner so a "default" or
        // "archived" card is exactly as tall as every other card.
        const corner = agent.id === defaultAgentId
            ? `<span class="agent-card-badge agent-chip-on">${escapeHtml(t('agents_default'))}</span>`
            : (!agent.enabled ? `<span class="agent-card-badge">${escapeHtml(t('agents_archived'))}</span>` : '');
        return `<div class="agent-card${selected ? ' selected' : ''}${agent.enabled ? '' : ' archived'}" onclick="openAgentDetail('${escapeHtml(agent.id)}')">
            ${corner}
            <div class="agent-card-top">
                ${agentAvatarHTML(agent, 32)}
                <div class="min-w-0 flex-1">
                    <div class="agent-card-name truncate">${escapeHtml(agent.name)}</div>
                    <div class="agent-card-desc">${desc ? escapeHtml(desc) : `<span class="agent-card-desc-empty">${escapeHtml(t('agents_no_desc'))}</span>`}</div>
                </div>
            </div>
        </div>`;
    }).join('');
}

// =====================================================================
// Agent Workbench (use agents)
// =====================================================================
// The usage page is a read-only card gallery. It deliberately does NOT reuse
// loadAgentCatalog(), which refreshes management data and the composer roster.
// A workbench load must be
// side-effect free: it only fetches /api/agents?view=workbench and paints the
// card grid from the whitelisted projection.
let agentWorkbench = [];
let agentWorkbenchLoading = false;
let agentWorkbenchSeq = 0;
let _wbNoticeKey = '';

function _wbContext() {
    return [activeAgentId, sessionId, currentView, agentNavigationVersion,
        sessionStorage.getItem('cow_tenant_id'), _identityMode()].join('|');
}

async function fetchAgentWorkbench() {
    const res = await fetch('/api/agents?view=workbench', { cache: 'no-store' });
    const data = await res.json();
    // An old backend may ignore `view` and return the management snapshot.
    // Fail visibly instead of treating missing capability flags as an empty list.
    if (!res.ok || data.status !== 'success' || !Array.isArray(data.agents)
        || 'channel_instances' in data || 'revision' in data
        || data.agents.some(a => typeof a.id !== 'string' || !a.id
            || typeof a.can_chat !== 'boolean' || typeof a.is_default !== 'boolean')) {
        throw new Error(t('agent_workbench_failed'));
    }
    return data.agents.map(a => ({
        id: a.id,
        name: /^cowagent$/i.test((a.name || '').trim()) ? 'RongAI' : (a.name || a.id),
        description: a.description || '', avatar: a.avatar || null,
        is_default: a.is_default, can_chat: a.can_chat,
        unavailable_reason: a.unavailable_reason || null,
        // Digital-employee projection fields (positioned to render on cards).
        position: a.position || '', category: a.category || '', tags: a.tags || [],
    })).sort((a, b) => Number(b.is_default) - Number(a.is_default));
}

function applyAgentWorkbench(agents) {
    agentWorkbench = agents;
    // Avatars can be replaced without changing their URL or roster revision.
    const version = String(Date.now());
    agents.forEach(a => { if (a.avatar === 'image') avatarVersions[a.id] = version; });
}

function agentUnavailableLabel(reason) {
    if (reason === 'permission_denied') return t('agent_permission_denied');
    return t(reason === 'runtime_not_enabled' ? 'agent_runtime_not_enabled' : 'agent_cannot_run');
}

function agentWorkbenchCardHTML(agent, canChat, unavailableReason) {
    const desc = (agent.description || '').trim();
    const badge = agent.is_default
        ? `<span class="agent-card-badge agent-chip-on">${escapeHtml(t('agents_default'))}</span>`
        : '';
    const starting = _agentStartAgentId === agent.id;
    const disabled = !canChat || agentWorkbenchLoading || !!_agentStartInFlight;
    const actionLabel = starting ? t('agent_starting')
        : canChat ? t('start_chat') : agentUnavailableLabel(unavailableReason);
    // The card body and its button share one flow, so the whole card is a
    // keyboard-accessible button-ish element. Use a single <button> to avoid a
    // nested-button accessibility violation and to make one Tab stop per card.
    const actionIcon = starting ? 'fa-spinner fa-spin' : canChat ? 'fa-comment' : 'fa-circle-exclamation';
    // Digital-employee projection: position / category on one meta line, tags as
    // small chips under the description. Rendered only when present.
    const metaBits = [];
    if (agent.position) metaBits.push(escapeHtml(agent.position));
    else if (agent.category) metaBits.push(escapeHtml(agent.category));
    const metaLine = metaBits.length
        ? `<div class="agent-wb-card-meta truncate">${metaBits.join(' · ')}</div>`
        : '';
    const tagChips = (agent.tags || []).length
        ? `<div class="agent-wb-card-tags">${(agent.tags || []).map(tag => `<span class="agent-wb-tag">${escapeHtml(tag)}</span>`).join('')}</div>`
        : '';
    return `<button type="button" class="agent-wb-card${canChat ? '' : ' agent-wb-card-disabled'}"
            ${disabled ? 'disabled aria-disabled="true"' : `onclick="startChatWithAgent('${escapeHtml(agent.id)}')"`}
            aria-busy="${starting || agentWorkbenchLoading}"
            data-agent-id="${escapeHtml(agent.id)}">
        <div class="agent-wb-card-top">
            ${agentAvatarHTML(agent, 44)}
            <div class="min-w-0 flex-1">
                <div class="agent-wb-card-name truncate" title="${escapeHtml(agent.name)}">${escapeHtml(agent.name)}</div>
                <div class="agent-wb-card-id truncate font-mono">${escapeHtml(agent.id)}</div>
            </div>
            ${badge}
        </div>
        <div class="agent-wb-card-desc">${desc ? escapeHtml(desc) : `<span class="agent-card-desc-empty">${escapeHtml(t('agents_no_desc'))}</span>`}</div>
        ${metaLine}
        ${tagChips}
        <div class="agent-wb-card-foot">
            <span class="agent-wb-action${canChat ? '' : ' agent-wb-action-disabled'}">
                <i class="fas ${actionIcon} mr-1.5"></i>${escapeHtml(actionLabel)}
            </span>
        </div>
    </button>`;
}

function renderAgentWorkbench() {
    const grid = document.getElementById('agent-workbench-grid');
    const status = document.getElementById('agent-workbench-status');
    if (!grid) return;
    // State: loading / empty / error / cards. The status line carries the
    // non-card states (loading, empty, error), the grid carries the cards.
    if (agentWorkbenchLoading && !agentWorkbench.length) {
        setWbStatus(t('agent_workbench_loading'));
        grid.innerHTML = '';
        return;
    }
    if (_wbLoadedError) {
        setWbError(t('agent_workbench_failed'));
        grid.innerHTML = `<div class="col-span-full text-sm text-slate-400 py-16 text-center">
            <p>${escapeHtml(t('agent_workbench_failed'))}</p>
            <button type="button" class="agent-wb-retry"
                onclick="loadAgentWorkbench(true)">${escapeHtml(t('agent_workbench_retry'))}</button>
        </div>`;
        return;
    }
    if (!agentWorkbench.length) {
        if (_wbNoticeKey) setWbError(t(_wbNoticeKey));
        else setWbStatus(t('agent_workbench_empty'));
        grid.innerHTML = '';
        return;
    }
    if (_wbNoticeKey) setWbError(t(_wbNoticeKey));
    else setWbStatus(agentWorkbenchLoading ? t('agent_workbench_loading') : '');
    grid.innerHTML = agentWorkbench.map(a =>
        agentWorkbenchCardHTML(a, a.can_chat, a.unavailable_reason)
    ).join('');
}

function setWbStatus(text) {
    const status = document.getElementById('agent-workbench-status');
    if (!status) return;
    status.textContent = text || '';
    status.classList.remove('opacity-0');
    status.classList.toggle('agent-workbench-status-hidden', !text);
    status.classList.remove('agent-workbench-status-error');
}

function setWbError(text) {
    const status = document.getElementById('agent-workbench-status');
    if (!status) return;
    status.textContent = text || '';
    status.classList.remove('opacity-0');
    status.classList.remove('agent-workbench-status-hidden');
    status.classList.add('agent-workbench-status-error');
}

let _wbLoadedError = false;

function loadAgentWorkbench(manualRefresh = false) {
    const grid = document.getElementById('agent-workbench-grid');
    if (!grid) return Promise.resolve();
    // Suppress a spurious "loading" flash when returning to a filled list.
    agentWorkbenchLoading = true;
    _wbLoadedError = false;
    _wbNoticeKey = '';
    renderAgentWorkbench();
    // A request-seq + context guard so a late response from an earlier read
    // (or one started under a different Agent / view) is dropped instead of
    // repainting stale cards over a fresher result.
    const seq = ++agentWorkbenchSeq;
    const ctx = _wbContext();
    return fetchAgentWorkbench()
        .then(agents => {
            if (seq !== agentWorkbenchSeq || ctx !== _wbContext()) return null;
            applyAgentWorkbench(agents);
            agentWorkbenchLoading = false;
            _wbLoadedError = false;
            renderAgentWorkbench();
            return agents;
        })
        .catch(err => {
            if (seq !== agentWorkbenchSeq || ctx !== _wbContext()) return null;
            agentWorkbenchLoading = false;
            _wbLoadedError = true;
            renderAgentWorkbench();
        });
}

function openAgentDetail(agentId) {
    selectedAdminAgentId = agentId;
    document.getElementById('agent-detail')?.classList.remove('hidden');
    renderAgentsGrid();
    renderAgentDetail();
    // Reset the core-file picker to a clean state per Agent, rather than
    // carrying over whichever file/view mode was left selected for the
    // previous one.
    const fileDd = document.getElementById('agent-core-file');
    if (fileDd) fileDd._ddValue = 'AGENT.md';
    setAgentCoreViewMode('edit');
    loadAgentCoreFile();
    // The model picker is drawn from the same catalog the composer uses, which
    // depends on which providers have keys. Re-render once it has arrived.
    if (!_sessCfg) refreshSessionSettings().then(() => {
        if (selectedAdminAgentId === agentId) renderAgentDetail();
    });
}

function closeAgentDetail() {
    selectedAdminAgentId = '';
    const detail = document.getElementById('agent-detail');
    if (detail) {
        detail.classList.add('hidden');
        // The empty pane's placeholder text (desktop two-pane layout).
        detail.setAttribute('data-empty-label', t('agents_select_hint'));
    }
    renderAgentsGrid();
}

function selectAgentDetailTab(tab) {
    document.querySelectorAll('.agent-detail-tab').forEach(el => {
        el.classList.toggle('active', el.dataset.tab === tab);
    });
    ['profile', 'skills', 'tasks', 'files'].forEach(name => {
        document.getElementById(`agent-detail-${name}`)?.classList.toggle('hidden', name !== tab);
    });
    if (tab === 'skills') renderAgentCapabilitiesPane();
    if (tab === 'tasks') renderAgentTasksPane();
    if (tab === 'files') loadAgentCoreFile();
}

// A field label followed by a small info icon whose help shows on hover, so a
// form stays compact instead of carrying a paragraph of hint under every field.
// The tip text may contain \n to force a line break (e.g. one line per option
// of a shared/own choice) — rendered via the popup's `white-space: pre-line`.
function fieldLabelWithTip(label, tip) {
    return `<div class="agent-field-label-row">
        <label class="agent-field-label">${escapeHtml(label)}</label>
        <span class="agent-field-tip" data-tip="${escapeHtml(tip)}"><i class="fas fa-circle-info"></i></span>
    </div>`;
}

// Single popup instance fixed to <body>, positioned relative to whichever
// .agent-field-tip is hovered. Living outside every drawer/modal means it is
// never clipped by an ancestor's `overflow: auto` (unlike a CSS ::after would
// be inside the scrolling Agent detail pane).
let _fieldTipEl = null;
let _fieldTipIcon = null;  // which icon the popup currently belongs to
function _ensureFieldTipEl() {
    if (!_fieldTipEl) {
        _fieldTipEl = document.createElement('div');
        _fieldTipEl.className = 'agent-tip-popup';
        document.body.appendChild(_fieldTipEl);
    }
    return _fieldTipEl;
}

function _showFieldTip(iconEl) {
    const tip = iconEl.dataset.tip;
    if (!tip) return;
    // Already showing for this icon: don't re-measure/re-animate. Moving the
    // cursor from the <span> onto its own <i> would otherwise re-trigger the
    // whole show sequence and make the tip visibly flicker.
    if (_fieldTipIcon === iconEl && _fieldTipEl && _fieldTipEl.classList.contains('show')) return;
    _fieldTipIcon = iconEl;
    const popup = _ensureFieldTipEl();
    popup.textContent = tip;
    popup.classList.remove('show');
    popup.style.left = '0px';
    popup.style.top = '0px';
    // Measure after layout so width/height reflect the actual (possibly
    // multi-line) content before we clamp it into the viewport.
    requestAnimationFrame(() => {
        const rect = iconEl.getBoundingClientRect();
        const pw = popup.offsetWidth, ph = popup.offsetHeight;
        let left = rect.left + rect.width / 2 - pw / 2;
        const margin = 8;
        left = Math.max(margin, Math.min(left, window.innerWidth - pw - margin));
        let top = rect.top - ph - 8;
        let arrowTop = false;
        if (top < margin) { top = rect.bottom + 8; arrowTop = true; } // flip below if clipped above
        popup.style.left = `${left}px`;
        popup.style.top = `${top}px`;
        popup.style.setProperty('--tip-arrow-x', `${rect.left + rect.width / 2 - left}px`);
        popup.classList.toggle('tip-arrow-top', arrowTop);
        popup.classList.add('show');
    });
}

function _hideFieldTip() {
    if (_fieldTipEl) _fieldTipEl.classList.remove('show');
    _fieldTipIcon = null;
}

document.addEventListener('mouseover', (e) => {
    const icon = e.target.closest ? e.target.closest('.agent-field-tip') : null;
    if (icon) _showFieldTip(icon);
});
document.addEventListener('mouseout', (e) => {
    const icon = e.target.closest ? e.target.closest('.agent-field-tip') : null;
    if (!icon) return;
    // mouseout fires when moving between the icon's own children (span -> <i>).
    // Only hide when the cursor actually leaves this icon's subtree, i.e. the
    // element it moved to isn't inside the same .agent-field-tip.
    const to = e.relatedTarget;
    if (to && icon.contains(to)) return;
    _hideFieldTip();
});
document.addEventListener('scroll', _hideFieldTip, true);

function renderAgentDetail() {
    const agent = findAgent(selectedAdminAgentId);
    const identity = document.getElementById('agent-detail-identity');
    const profile = document.getElementById('agent-detail-profile');
    if (!agent || !identity || !profile) return;
    identity.innerHTML = `
        ${agentAvatarHTML(agent, 56)}
        <div class="min-w-0">
            <div class="text-lg font-semibold text-slate-800 dark:text-slate-100 truncate">${escapeHtml(agent.name)}</div>
            <div class="text-xs text-slate-400 font-mono truncate">${escapeHtml(agent.id)}</div>
        </div>`;
    const isDefault = agent.id === defaultAgentId;
    profile.innerHTML = `
        <div class="agent-field">
            <label class="agent-field-label">${escapeHtml(t('agents_avatar'))}</label>
            <div id="agent-edit-avatar" class="agent-avatar-picker"></div>
        </div>
        <div class="agent-field">
            <label class="agent-field-label">${escapeHtml(t('agents_name'))}</label>
            <input id="agent-edit-name" value="${escapeHtml(agent.name)}" class="agent-input">
        </div>
        <div class="agent-field">
            ${fieldLabelWithTip(t('agents_description'), t('agents_description_hint'))}
            <textarea id="agent-edit-description" rows="4"
                   placeholder="${escapeHtml(t('agents_description_placeholder'))}"
                   class="agent-input agent-textarea">${escapeHtml(agent.description || '')}</textarea>
        </div>
        <div class="agent-field">
            <label class="agent-field-label">${escapeHtml(t('agents_position'))}</label>
            <input id="agent-edit-position" value="${escapeHtml(agent.position || '')}" class="agent-input"
                   placeholder="${escapeHtml(t('agents_position_placeholder'))}">
        </div>
        <div class="agent-field">
            <label class="agent-field-label">${escapeHtml(t('agents_category'))}</label>
            <div id="agent-edit-category" class="cfg-dropdown" tabindex="0">
                <div class="cfg-dropdown-selected">
                    <span class="cfg-dropdown-text">${escapeHtml(agent.category || t('agents_category_none'))}</span>
                    <i class="fas fa-chevron-down cfg-dropdown-arrow"></i>
                </div>
                <div class="cfg-dropdown-menu"></div>
            </div>
        </div>
        <div class="agent-field">
            <label class="agent-field-label">${escapeHtml(t('agents_tags'))}</label>
            <input id="agent-edit-tags" value="${escapeHtml((agent.tags || []).join(', '))}" class="agent-input"
                   placeholder="${escapeHtml(t('agents_tags_placeholder'))}">
        </div>
        <div class="agent-field">
            <label class="agent-field-label">${escapeHtml(t('agents_greeting'))}</label>
            <input id="agent-edit-greeting" value="${escapeHtml(agent.greeting || '')}" class="agent-input">
        </div>
        <div class="agent-field">
            ${fieldLabelWithTip(t('agents_persona'), t('agents_persona_hint'))}
            <textarea id="agent-edit-persona" rows="3"
                   class="agent-input agent-textarea">${escapeHtml(agent.persona_summary || '')}</textarea>
        </div>
        <div class="agent-field">
            <label class="agent-field-label">${escapeHtml(t('agents_scene'))}</label>
            <div id="agent-edit-scene" class="cfg-dropdown" tabindex="0">
                <div class="cfg-dropdown-selected">
                    <span class="cfg-dropdown-text">${escapeHtml(agent.scene_id || t('agents_scene_none'))}</span>
                    <i class="fas fa-chevron-down cfg-dropdown-arrow"></i>
                </div>
                <div class="cfg-dropdown-menu"></div>
            </div>
        </div>
        <div class="agent-field">
            <label class="agent-field-label">${escapeHtml(t('agents_model'))}</label>
            ${isDefault
                ? `<div class="agent-input-locked">${escapeHtml(t('agents_model_follows_global'))}</div>
                   <p class="agent-field-hint">${escapeHtml(t('agents_model_default_hint'))}</p>`
                : `<div id="agent-edit-model" class="cfg-dropdown" tabindex="0">
                       <div class="cfg-dropdown-selected">
                           <span class="cfg-dropdown-text">--</span>
                           <i class="fas fa-chevron-down cfg-dropdown-arrow"></i>
                       </div>
                       <div class="cfg-dropdown-menu"></div>
                   </div>`}
        </div>
        ${isDefault ? '' : `
        <div class="agent-field">
            ${fieldLabelWithTip(t('agents_knowledge'), t('agents_knowledge_hint'))}
            <div class="flex items-center gap-3">
                <div id="agent-knowledge-toggle" class="agent-seg" role="group">
                    <button type="button" class="agent-seg-btn ${agent.knowledge_mode !== 'own' ? 'active' : ''}" data-mode="shared" onclick="setAgentKnowledgeMode('${escapeHtml(agent.id)}','shared')">
                        <i class="fas fa-users mr-1"></i>${escapeHtml(t('agents_knowledge_shared'))}
                    </button>
                    <button type="button" class="agent-seg-btn ${agent.knowledge_mode === 'own' ? 'active' : ''}" data-mode="own" onclick="setAgentKnowledgeMode('${escapeHtml(agent.id)}','own')">
                        <i class="fas fa-box-archive mr-1"></i>${escapeHtml(t('agents_knowledge_own'))}
                    </button>
                </div>
                <span id="agent-knowledge-status" class="agent-field-hint" style="margin-top:0"></span>
            </div>
        </div>`}
        <div class="agent-detail-actions">
            <button type="button" onclick="saveAgentProfile()" class="agent-btn agent-btn-primary">${escapeHtml(t('save'))}</button>
            <button type="button" onclick="startChatWithAgent('${escapeHtml(agent.id)}')" class="agent-btn agent-btn-ghost">${escapeHtml(t('agents_chat'))}</button>
            ${isDefault ? '' : `<button type="button" onclick="deleteAgent('${escapeHtml(agent.id)}')" class="agent-btn agent-btn-danger agent-detail-delete">${escapeHtml(t('agents_delete'))}</button>`}
        </div>
        <div id="agent-profile-status" class="agent-field-hint mt-3"></div>`;

    renderAvatarPicker('agent-edit-avatar', agent, (file) => uploadAgentAvatar(agent.id, file));

    if (!isDefault) {
        const dd = document.getElementById('agent-edit-model');
        const opts = agentModelDropdownOptions();
        const current = agent.model ? `${agent.bot_type || ''}|${agent.model}` : '';
        initDropdown(dd, opts, current, () => {}, { placeholder: t('agents_model_follows_global') });
    }
    // A save may re-render this pane several times; re-apply an in-flight
    // "saved" confirmation so it survives instead of being wiped.
    paintAgentSavedFlash();
    // Populate the scene + category dropdowns from the scene catalog, then
    // re-init so the current selection is preserved against the wide option set.
    refreshAgentCategoryDropdown();
    if (!isDefault) refreshAgentSceneDropdown();
}

/* A live preview beside an upload button, in the page's own styling rather than
   a raw file input. The default is the Agent's initial; uploading swaps it for
   the chosen image. `onUpload` may be null when the Agent does not exist yet
   (the create modal), leaving just the preview. */
function renderAvatarPicker(containerId, agent, onUpload) {
    const box = document.getElementById(containerId);
    if (!box) return;
    box.innerHTML = `
        <div class="agent-avatar-picker-preview">${agentAvatarHTML(agent, 56)}</div>
        <div class="agent-avatar-picker-body">
            ${onUpload ? `<button type="button" class="agent-avatar-upload">
                <i class="fas fa-arrow-up-from-bracket"></i><span>${escapeHtml(t('agents_avatar_upload'))}</span>
                <input type="file" accept="image/png,image/jpeg,image/webp,image/gif" hidden>
            </button>` : ''}
        </div>`;
    const upload = box.querySelector('.agent-avatar-upload');
    if (upload && onUpload) {
        const input = upload.querySelector('input');
        upload.addEventListener('click', () => input.click());
        input.addEventListener('change', () => onUpload(input.files && input.files[0]));
    }
}

/* Flattened for the styled dropdown: one row per model, its provider carried in
   the value (a model asked of the wrong vendor is an error), its brand shown as
   a dim hint. The first row clears the choice back to the configured model. */
function agentModelDropdownOptions() {
    const opts = [{ value: '', label: t('agents_model_follows_global') }];
    const providers = (_sessCfg && _sessCfg.model && _sessCfg.model.providers) || [];
    providers.forEach(p => {
        (p.models || []).forEach(m => {
            opts.push({ value: `${p.id}|${m}`, label: m, hint: localizedLabel(p.label) });
        });
    });
    return opts;
}

// Scene catalog options for the Agent detail pane. Fetched lazily and cached;
// a missing scene module yields no options, so the selector simply offers "none".
let _sceneCatalogCache = null;
function sceneCatalog() {
    if (_sceneCatalogCache !== null) return Promise.resolve(_sceneCatalogCache);
    return fetch('/api/scenes').then(r => r.json()).then(d => {
        _sceneCatalogCache = d && d.scenes ? d.scenes : [];
        return _sceneCatalogCache;
    }).catch(() => { _sceneCatalogCache = []; return []; });
}
function sceneCatalogOptions() {
    return [{ value: '', label: t('agents_scene_none') }];
}
function refreshAgentSceneDropdown() {
    const dd = document.getElementById('agent-edit-scene');
    if (!dd) return;
    const agent = findAgent(selectedAdminAgentId);
    sceneCatalog().then(scenes => {
        const opts = [{ value: '', label: t('agents_scene_none') }].concat(
            scenes.map(s => ({ value: s.id, label: (s.name || s.id) }))
        );
        const current = (agent && agent.scene_id) || '';
        initDropdown(dd, opts, current, () => {}, { placeholder: t('agents_scene_none') });
    });
}
function sceneCategoryOptions() {
    // Category may be typed freely; the dropdown offers the scene categories
    // (empty allowed). The first row clears back to no category.
    return [{ value: '', label: t('agents_category_none') }];
}
function refreshAgentCategoryDropdown() {
    const dd = document.getElementById('agent-edit-category');
    if (!dd) return;
    const agent = findAgent(selectedAdminAgentId);
    sceneCatalog().then(scenes => {
        const seen = [];
        scenes.forEach(s => { const c = s.category; if (c && seen.indexOf(c) === -1) seen.push(c); });
        const opts = [{ value: '', label: t('agents_category_none') }].concat(
            seen.map(c => ({ value: c, label: c }))
        );
        const current = (agent && agent.category) || '';
        initDropdown(dd, opts, current, () => {}, { placeholder: t('agents_category_none') });
    });
}

// Persist an Agent's skill selection. Writes are serialized per Agent and
// coalesce to the latest desired state, so ticking several boxes quickly sends
// them in order (each with the revision the previous one returned) instead of
// racing and tripping the stale-roster guard. No catalog reload happens, so the
// grid, composer and avatars never flicker and the checkboxes never jump.
//   null  -> use every installed skill (the "use all" master toggle)
//   [...] -> exactly this subset ([] means none)
const _skillSaveState = {};  // agentId -> { inflight: bool, pending: skills|undefined }

function saveAgentSkills(agent, skills) {
    agent.skills = skills;  // optimistic; the pane already reflects it
    const st = _skillSaveState[agent.id] || (_skillSaveState[agent.id] = { inflight: false, pending: undefined });
    if (st.inflight) { st.pending = skills; return; }  // newest wins; drop stale intermediate
    st.inflight = true;
    fetch('/api/agents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'update', id: agent.id, revision: rosterRevision, skills }),
    }).then(r => r.json()).then(data => {
        if (data.status === 'success') {
            if (data.revision) rosterRevision = data.revision;
        } else {
            const status = document.getElementById('agent-editor-status');
            if (status) status.textContent = data.message || 'Update failed';
        }
    }).catch(() => {}).then(() => {
        st.inflight = false;
        if (st.pending !== undefined) {
            const next = st.pending;
            st.pending = undefined;
            saveAgentSkills(agent, next);  // flush the latest queued state
        }
    });
}

// Persist capabilities (skills / sops / tools allow+deny) in one update, so
// toggling related controls does not send several racing writes. The roster
// revision is carried on each call; on success we adopt the returned revision.
function saveAgentCapabilities(agent, fields) {
    // Apply optimistically so the pane reflects the new state immediately.
    if ('skills' in fields) agent.skills = fields.skills;
    if ('sops' in fields) agent.sops = fields.sops;
    if ('tools_allowlist' in fields) agent.tools_allowlist = fields.tools_allowlist;
    if ('tools_denylist' in fields) agent.tools_denylist = fields.tools_denylist;
    const body = Object.assign({ action: 'update', id: agent.id, revision: rosterRevision }, fields);
    fetch('/api/agents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    }).then(r => r.json()).then(data => {
        if (data.status === 'success') {
            if (data.revision) rosterRevision = data.revision;
        } else {
            const status = document.getElementById('agent-editor-status');
            if (status) status.textContent = data.message || 'Update failed';
        }
    }).catch(() => {});
}

// Switch an Agent between the shared knowledge base and its own. This is a
// filesystem toggle (symlink vs a real knowledge/ dir), so it applies at once
// rather than waiting for the profile "save".
async function setAgentKnowledgeMode(agentId, mode) {
    const agent = findAgent(agentId);
    if (!agent || agent.knowledge_mode === mode) return;
    const status = document.getElementById('agent-knowledge-status');
    const paintActive = (m) => document.querySelectorAll('#agent-knowledge-toggle .agent-seg-btn')
        .forEach(b => b.classList.toggle('active', b.dataset.mode === m));
    const prev = agent.knowledge_mode || 'shared';
    agent.knowledge_mode = mode;  // optimistic
    paintActive(mode);
    if (status) status.textContent = t('agents_knowledge_working') || '...';
    try {
        const res = await fetch('/api/agents', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'set_knowledge_mode', id: agentId, mode }),
        });
        const data = await res.json();
        if (data.status === 'success') {
            agent.knowledge_mode = (data.mode || mode);
            paintActive(agent.knowledge_mode);
            if (status) status.textContent = '';
        } else {
            agent.knowledge_mode = prev;  // roll back
            paintActive(prev);
            if (status) status.textContent = data.message || t('agents_knowledge_failed') || 'Failed';
        }
    } catch (e) {
        agent.knowledge_mode = prev;
        paintActive(prev);
        if (status) status.textContent = t('agents_knowledge_failed') || 'Failed';
    }
}

function renderAgentCapabilitiesPane() {
    const pane = document.getElementById('agent-detail-skills');
    const agent = findAgent(selectedAdminAgentId);
    if (!pane || !agent) return;
    const toolsLoaded = agent.tools_allowlist != null || agent.tools_denylist.length > 0;
    const render = () => {
        const all = agent.skills == null;
        const picked = new Set(all ? [] : agent.skills);
        const sops = agent.sops || [];
        const allowlist = new Set(agent.tools_allowlist || []);
        const denylist = new Set(agent.tools_denylist || []);
        pane.innerHTML = `
            <div class="agent-cap-section">
                <div class="agent-cap-title">${escapeHtml(t('agents_skills_label'))}</div>
                <label class="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300 mb-3">
                    <input type="checkbox" id="agent-skills-all" ${all ? 'checked' : ''}>
                    <span>${escapeHtml(t('agents_skills_all'))}</span>
                </label>
                <p class="text-xs text-slate-400 mb-3">${escapeHtml(t('agents_skills_pick'))}</p>
                ${(installedSkills || []).map(skill => {
                    const name = skill.name || skill.id;
                    const checked = all || picked.has(name);
                    return `<label class="agent-skill-row">
                        <input type="checkbox" class="agent-skill-item" value="${escapeHtml(name)}" ${checked ? 'checked' : ''} ${all ? 'disabled' : ''}>
                        <div>
                            <div class="text-sm text-slate-700 dark:text-slate-200">${escapeHtml(skill.display_name || name)}</div>
                            <div class="text-xs text-slate-400">${escapeHtml(skill.description || '')}</div>
                        </div>
                    </label>`;
                }).join('')}
            </div>
            <div class="agent-cap-section">
                <div class="agent-cap-title">${escapeHtml(t('agents_sops_label'))}</div>
                <p class="text-xs text-slate-400 mb-2">${escapeHtml(t('agents_sops_hint'))}</p>
                <div class="flex flex-wrap gap-2 mb-2" id="agent-sops-list">
                    ${sops.map(id => `<span class="agent-tag">${escapeHtml(id)}<button type="button" class="agent-tag-x" data-sop="${escapeHtml(id)}">&times;</button></span>`).join('')}
                </div>
                <div class="flex gap-2">
                    <input id="agent-sop-input" placeholder="${escapeHtml(t('agents_sops_placeholder'))}" class="agent-input" style="max-width: 240px;">
                    <button type="button" id="agent-sop-add" class="agent-btn agent-btn-ghost">${escapeHtml(t('agents_sops_add'))}</button>
                </div>
            </div>
            <div class="agent-cap-section">
                <div class="agent-cap-title">${escapeHtml(t('agents_tools_label'))}</div>
                <p class="text-xs text-slate-400 mb-2">${escapeHtml(t('agents_tools_hint'))}</p>
                <div class="agent-tool-grid" id="agent-tools-allow">
                    ${(installedTools || []).map(tool => {
                        const a = allowlist.has(tool.name);
                        const d = denylist.has(tool.name);
                        return `<div class="agent-tool-row">
                            <span class="agent-tool-chip"><input type="checkbox" class="agent-tool-allow" value="${escapeHtml(tool.name)}" ${a ? 'checked' : ''} ${d ? 'disabled' : ''}>${escapeHtml(t('agents_allow'))}</span>
                            <span class="agent-tool-chip"><input type="checkbox" class="agent-tool-deny" value="${escapeHtml(tool.name)}" ${d ? 'checked' : ''} ${a ? 'disabled' : ''}>${escapeHtml(t('agents_deny'))}</span>
                            <div class="min-w-0 flex-1">
                                <div class="text-sm text-slate-700 dark:text-slate-200 font-mono">${escapeHtml(tool.name)}</div>
                                <div class="text-xs text-slate-400 truncate">${escapeHtml((tool.description || '').split('\n')[0])}</div>
                            </div>
                        </div>`;
                    }).join('')}
                </div>
                <p class="text-xs text-slate-400 mt-2">${escapeHtml(t('agents_tools_none_hint'))}</p>
            </div>`;
        document.getElementById('agent-skills-all')?.addEventListener('change', (e) => {
            const next = e.target.checked ? null : [];
            saveAgentCapabilities(agent, { skills: next });
            render();
        });
        pane.querySelectorAll('.agent-skill-item').forEach(box => {
            box.addEventListener('change', () => {
                const names = Array.from(pane.querySelectorAll('.agent-skill-item:checked')).map(el => el.value);
                saveAgentCapabilities(agent, { skills: names });
            });
        });
        // SOP add/remove
        document.getElementById('agent-sop-add')?.addEventListener('click', () => {
            const input = document.getElementById('agent-sop-input');
            const val = (input && input.value || '').trim();
            if (!val) return;
            const next = Array.from(new Set([...sops, val]));
            saveAgentCapabilities(agent, { sops: next });
            if (input) input.value = '';
            render();
        });
        pane.querySelectorAll('.agent-tag-x[data-sop]').forEach(btn => {
            btn.addEventListener('click', () => {
                const id = btn.getAttribute('data-sop');
                const next = sops.filter(x => x !== id);
                saveAgentCapabilities(agent, { sops: next });
                render();
            });
        });
        // Tool allow/deny toggles
        pane.querySelectorAll('.agent-tool-allow').forEach(box => {
            box.addEventListener('change', () => {
                const name = box.value;
                const nextAllow = new Set(agent.tools_allowlist || []);
                if (box.checked) nextAllow.add(name);
                else if (agent.tools_allowlist != null) nextAllow.delete(name);
                saveAgentCapabilities(agent, { tools_allowlist: agent.tools_allowlist == null ? [name] : Array.from(nextAllow) });
                render();
            });
        });
        pane.querySelectorAll('.agent-tool-deny').forEach(box => {
            box.addEventListener('change', () => {
                const name = box.value;
                const nextDeny = new Set(agent.tools_denylist || []);
                if (box.checked) nextDeny.add(name); else nextDeny.delete(name);
                saveAgentCapabilities(agent, { tools_denylist: Array.from(nextDeny) });
                render();
            });
        });
    };
    const ready = () => {
        if (installedSkills.length && installedTools.length) { render(); return; }
        Promise.all([
            installedSkills.length ? Promise.resolve() : fetch('/api/skills').then(r => r.json()).then(d => { installedSkills = d.skills || []; }),
            installedTools.length ? Promise.resolve() : fetch('/api/tools').then(r => r.json()).then(d => { installedTools = d.tools || []; }),
        ]).then(() => render()).catch(() => {
            pane.innerHTML = `<p class="text-sm text-slate-400">${escapeHtml(t('agents_skills_all'))}</p>`;
        });
    };
    ready();
}

function renderAgentTasksPane() {
    const pane = document.getElementById('agent-detail-tasks');
    const agent = findAgent(selectedAdminAgentId);
    if (!pane || !agent) return;
    const render = (tasks) => {
        if (!tasks.length) {
            pane.innerHTML = `<p class="text-sm text-slate-400">${escapeHtml(t('tasks_empty_agent'))}</p>`;
            return;
        }
        pane.innerHTML = `<div class="agent-cap-title">${escapeHtml(t('agents_tasks_label'))}</div>`;
        const wrap = document.createElement('div');
        wrap.className = 'agent-task-list';
        tasks.forEach(task => {
            const isEnabled = task.enabled !== false;
            const schedule = task.schedule || {};
            let typeLabel = '';
            if (schedule.type === 'cron') {
                typeLabel = `<span class="text-xs font-mono text-slate-400">${escapeHtml(schedule.expression || '')}</span>`;
            } else if (schedule.type === 'interval') {
                const seconds = schedule.seconds || 0;
                const hours = Math.floor(seconds / 3600);
                const mins = Math.floor((seconds % 3600) / 60);
                typeLabel = hours ? `${hours}h${mins ? ` ${mins}m` : ''}` : `${mins}m`;
                typeLabel = `<span class="text-xs text-slate-400">${escapeHtml(typeLabel)}</span>`;
            } else {
                typeLabel = `<span class="text-xs text-slate-400">${escapeHtml(schedule.type || 'once')}</span>`;
            }
            let nextRun = '--';
            if (task.next_run_at) {
                const d = new Date(task.next_run_at);
                if (!isNaN(d.getTime())) nextRun = d.toLocaleString();
            }
            const action = task.action || {};
            const taskContent = action.content || action.task_description || '';
            const toggleId = 'agent-task-toggle-' + task.id;
            const card = document.createElement('div');
            card.className = 'agent-task-card' + (isEnabled ? '' : ' agent-task-card-disabled');
            card.dataset.taskId = task.id;
            card.innerHTML = `
                <div class="flex items-center gap-2 mb-1">
                    <span class="w-2 h-2 rounded-full ${isEnabled ? 'bg-primary-400' : 'bg-slate-300 dark:bg-slate-600'}"></span>
                    <span class="font-medium text-sm text-slate-700 dark:text-slate-200">${escapeHtml(task.name || task.id || '--')}</span>
                    <div class="flex-1"></div>
                    ${typeLabel}
                </div>
                <p class="text-xs text-slate-500 dark:text-slate-400 mb-2 line-clamp-2">${escapeHtml(taskContent)}</p>
                <div class="flex items-center gap-4 text-xs text-slate-400 dark:text-slate-500">
                    <span><i class="fas fa-clock mr-1"></i>${escapeHtml(t('task_next_run'))}: ${nextRun}</span>
                    <div class="flex-1"></div>
                    <button type="button" class="task-run-now px-2 py-1 rounded-md text-primary-500 hover:bg-primary-50 dark:hover:bg-primary-500/10 transition-colors">
                        <i class="fas fa-play mr-1"></i>${escapeHtml(t('task_run_now'))}
                    </button>
                    <label class="relative inline-flex items-center cursor-pointer" for="${toggleId}">
                        <input type="checkbox" id="${toggleId}" class="sr-only peer" ${isEnabled ? 'checked' : ''}>
                        <div class="w-9 h-5 bg-slate-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-primary-500 dark:bg-slate-600 dark:peer-checked:bg-primary-500"></div>
                    </label>
                </div>`;
            card.querySelector('.task-run-now').addEventListener('click', (e) => {
                e.stopPropagation();
                runTaskNow(task, e.currentTarget);
            });
            const checkbox = card.querySelector('#' + toggleId);
            checkbox.addEventListener('change', function() {
                const newEnabled = this.checked;
                fetch('/api/scheduler/toggle', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ task_id: task.id, enabled: newEnabled, agent_id: task.agent_id || agent.id })
                }).then(r => r.json()).then(res => { if (res.status === 'success') renderAgentTasksPane(); });
            });
            wrap.appendChild(card);
        });
        pane.appendChild(wrap);
    };
    return fetch('/api/scheduler?agent_id=' + encodeURIComponent(agent.id))
        .then(r => r.json())
        .then(data => { render(data.tasks || []); })
        .catch(() => { pane.innerHTML = `<p class="text-sm text-slate-400">${escapeHtml(t('tasks_unavailable'))}</p>`; });
}

// Held between opening the create modal and a successful create: the chosen
// avatar has nowhere to live server-side until the Agent exists, so we keep the
// File and its preview URL client-side and upload once creation returns.
let _pendingCreateAvatar = null;
let _createKnowledgeMode = 'shared';

// What the Agent being filled in would look like: no id yet, so the disc is the
// neutral tone and only the initial follows the name.
function createAvatarDraft() {
    const name = document.getElementById('agent-create-name');
    return { id: '', name: (name && name.value) || '', avatar: '' };
}

// Repaint just the preview disc as the name is typed. The whole picker is not
// re-rendered because that would rebind the upload input on every keystroke.
function refreshCreateAvatarPreview() {
    if (_pendingCreateAvatar) return;
    const slot = document.querySelector('#agent-create-avatar .agent-avatar-picker-preview');
    if (slot) slot.innerHTML = agentAvatarHTML(createAvatarDraft(), 56);
}

// The create modal's avatar picker: same look as the edit one, but the upload
// is staged locally (preview from an object URL) instead of POSTed immediately.
function renderCreateAvatarPicker() {
    const box = document.getElementById('agent-create-avatar');
    if (!box) return;
    const preview = _pendingCreateAvatar
        ? `<img class="agent-avatar agent-avatar-56" src="${_pendingCreateAvatar.url}" alt="">`
        : agentAvatarHTML(createAvatarDraft(), 56);
    box.innerHTML = `
        <div class="agent-avatar-picker-preview">${preview}</div>
        <div class="agent-avatar-picker-body">
            <button type="button" class="agent-avatar-upload">
                <i class="fas fa-arrow-up-from-bracket"></i><span>${escapeHtml(t('agents_avatar_upload'))}</span>
                <input type="file" accept="image/png,image/jpeg,image/webp,image/gif" hidden>
            </button>
        </div>`;
    const upload = box.querySelector('.agent-avatar-upload');
    const input = upload.querySelector('input');
    upload.addEventListener('click', () => input.click());
    input.addEventListener('change', () => {
        const file = input.files && input.files[0];
        if (!file) return;
        if (_pendingCreateAvatar && _pendingCreateAvatar.url) URL.revokeObjectURL(_pendingCreateAvatar.url);
        _pendingCreateAvatar = { file, url: URL.createObjectURL(file) };
        renderCreateAvatarPicker();
    });
}

function openAgentCreateForm() {
    const form = document.getElementById('agent-create-form');
    if (!form) return;
    form.classList.remove('hidden');
    const name = document.getElementById('agent-create-name');
    // The id is typed by hand or left blank on purpose; nothing writes to it
    // while the form is open. A blank one is filled in once, at submit.
    const id = document.getElementById('agent-create-id');
    const description = document.getElementById('agent-create-description');
    [name, id, description].forEach(el => { if (el) el.value = ''; });
    document.getElementById('agent-create-status').textContent = '';

    // The Agent has no home to store an avatar in yet, so the upload is held in
    // memory and previewed locally; it is POSTed the moment creation succeeds.
    _pendingCreateAvatar = null;
    renderCreateAvatarPicker();
    if (name && !name.dataset.avatarBound) {
        name.dataset.avatarBound = '1';
        // Without an upload the face is the name's first character, so the
        // preview has to follow what is being typed.
        name.addEventListener('input', refreshCreateAvatarPreview);
    }

    // Knowledge defaults to shared; reset the segmented control on every open.
    _createKnowledgeMode = 'shared';
    document.querySelectorAll('#agent-create-knowledge .agent-seg-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.mode === 'shared');
        if (!b.dataset.bound) {
            b.dataset.bound = '1';
            b.addEventListener('click', () => {
                _createKnowledgeMode = b.dataset.mode;
                document.querySelectorAll('#agent-create-knowledge .agent-seg-btn')
                    .forEach(x => x.classList.toggle('active', x === b));
            });
        }
    });

    const clone = document.getElementById('agent-create-clone');
    if (clone) {
        // Options carry the agent so both the row and the trigger show its
        // avatar + name; "blank" (no clone) has no face.
        const opts = [{ value: '', label: t('agents_clone_none') }].concat(
            enabledAgents().map(a => ({
                value: a.id,
                label: a.name || a.id,
                agent: a,
            }))
        );
        initDropdown(clone, opts, '', () => {});
    }
}

function closeAgentCreateForm() {
    document.getElementById('agent-create-form')?.classList.add('hidden');
    if (_pendingCreateAvatar && _pendingCreateAvatar.url) URL.revokeObjectURL(_pendingCreateAvatar.url);
    _pendingCreateAvatar = null;
}

document.addEventListener('click', (e) => {
    const menu = document.getElementById('composer-agent-menu');
    const btn = document.getElementById('composer-agent-btn');
    if (menu && !menu.classList.contains('hidden') && !menu.contains(e.target) && btn && !btn.contains(e.target)) {
        menu.classList.add('hidden');
    }
    const modal = document.getElementById('agent-create-form');
    if (modal && !modal.classList.contains('hidden') && e.target === modal) {
        closeAgentCreateForm();
    }
    const newMenu = document.getElementById('new-chat-menu');
    if (newMenu && !newMenu.classList.contains('hidden') && !newMenu.contains(e.target)) {
        newMenu.classList.add('hidden');
    }
    const teamModal = document.getElementById('team-chat-modal');
    if (teamModal && !teamModal.classList.contains('hidden') && e.target === teamModal) {
        closeTeamChatModal();
    }
});

function createAgentWorkspace() {
    const name = document.getElementById('agent-create-name').value.trim();
    const status = document.getElementById('agent-create-status');
    if (!name) {
        status.textContent = t('agents_name_required');
        return;
    }
    // A hand-typed id is used as given; blank falls back to the name's slug,
    // and then to a random one when the name has no ascii to slug (e.g. it is
    // written in Chinese). Generated here rather than while typing so the field
    // stays exactly as the user left it.
    const typed = document.getElementById('agent-create-id').value.trim();
    const id = typed || slugAgentId(name) || randomAgentId();
    // Mirrors the server's rule, so a bad id is caught before the round trip.
    if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(id)) {
        status.textContent = t('agents_id_invalid');
        return;
    }
    fetch('/api/agents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            action: 'create',
            id,
            name,
            description: document.getElementById('agent-create-description')?.value.trim() || '',
            clone_from: getDropdownValue(document.getElementById('agent-create-clone')) || null,
            knowledge_mode: _createKnowledgeMode,
            revision: rosterRevision,
        }),
    }).then(r => r.json()).then(data => {
        if (data.status !== 'success') {
            throw new Error(data.code === 'stale_roster' ? t('agents_stale') : (data.message || 'Create failed'));
        }
        if (data.revision) rosterRevision = data.revision;
        // Now that the workspace exists, push the staged avatar (if any) before
        // reloading, so the roster arrives already carrying the new image.
        const avatarStep = _pendingCreateAvatar
            ? uploadAgentAvatar(id, _pendingCreateAvatar.file).catch(() => {})
            : Promise.resolve();
        closeAgentCreateForm();
        return avatarStep.then(() => loadAgentCatalog()).then(() => openAgentDetail(id));
    }).catch(err => { status.textContent = err.message; });
}

function saveAgentProfile() {
    const agent = findAgent(selectedAdminAgentId);
    if (!agent) return;
    const catEl = document.getElementById('agent-edit-category');
    const sceneEl = document.getElementById('agent-edit-scene');
    const payload = {
        name: document.getElementById('agent-edit-name')?.value.trim(),
        description: document.getElementById('agent-edit-description')?.value.trim() || '',
        position: document.getElementById('agent-edit-position')?.value.trim() || '',
        category: catEl ? (getDropdownValue(catEl) || '') : agent.category || '',
        tags: (document.getElementById('agent-edit-tags')?.value || '').split(',').map(s => s.trim()).filter(Boolean),
        greeting: document.getElementById('agent-edit-greeting')?.value.trim() || '',
        persona_summary: document.getElementById('agent-edit-persona')?.value.trim() || '',
        scene_id: sceneEl ? (getDropdownValue(sceneEl) || '') : agent.scene_id || '',
    };
    // Absent for the default Agent, which follows the configured model.
    const picker = document.getElementById('agent-edit-model');
    if (picker) {
        const [provider, model] = (getDropdownValue(picker) || '').split('|');
        payload.model = model || '';
        payload.bot_type = provider || '';
    }
    // The write itself is quick; the follow-up catalog reload is what's slow
    // (the default Agent carries a large skill list). Confirm optimistically so
    // the feedback is instant, and only override it if the save actually fails.
    flashAgentProfileStatus();
    updateAgentWorkspace(agent.id, payload).then(ok => {
        if (!ok) {
            _agentSavedFlashUntil = 0;
            const status = document.getElementById('agent-profile-status');
            if (status) {
                status.textContent = t('agents_save_failed');
                status.classList.remove('agent-status-ok');
            }
        }
    });
}

/* A brief inline confirmation on the detail pane's status line. A save reloads
   the catalog and can re-render this pane more than once (the model catalog
   arrives async), so the confirmation is kept as a deadline that every render
   re-applies, rather than a one-shot write a later render would wipe. */
let _agentSavedFlashUntil = 0;

function paintAgentSavedFlash() {
    const status = document.getElementById('agent-profile-status');
    if (!status) return;
    if (Date.now() < _agentSavedFlashUntil) {
        status.textContent = t('agents_saved');
        status.classList.add('agent-status-ok');
    }
}

function flashAgentProfileStatus() {
    _agentSavedFlashUntil = Date.now() + 2200;
    paintAgentSavedFlash();
    clearTimeout(flashAgentProfileStatus._t);
    flashAgentProfileStatus._t = setTimeout(() => {
        _agentSavedFlashUntil = 0;
        const status = document.getElementById('agent-profile-status');
        if (!status) return;
        status.textContent = '';
        status.classList.remove('agent-status-ok');
    }, 2200);
}

function uploadAgentAvatar(agentId, file) {
    if (!file) return;
    const picker = document.getElementById('agent-edit-avatar');
    if (picker) picker.classList.add('is-uploading');
    const status = document.getElementById('agent-profile-status');
    if (status) { status.classList.remove('agent-status-ok'); status.textContent = ''; }
    const form = new FormData();
    form.append('avatar', file);
    return fetch(`/api/agents/${encodeURIComponent(agentId)}/avatar`, { method: 'POST', body: form })
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'success') throw new Error(data.message || 'Upload failed');
            // The image already persisted server-side. Patch the local catalog in
            // place and repaint just the affected surfaces, rather than reloading
            // the whole roster (slow when the default Agent carries many skills).
            avatarVersions[agentId] = String(Date.now());
            if (data.revision) rosterRevision = data.revision;
            const agent = findAgent(agentId);
            if (agent) agent.avatar = 'image';
            renderAgentsGrid();
            if (selectedAdminAgentId === agentId) renderAgentDetail();
            renderComposerIdentity();
            refreshBubbleAvatars();
            flashAgentProfileStatus();
        })
        .catch(err => {
            const s = document.getElementById('agent-profile-status');
            if (s) { s.classList.remove('agent-status-ok'); s.textContent = err.message; }
        })
        .then(() => {
            const p = document.getElementById('agent-edit-avatar');
            if (p) p.classList.remove('is-uploading');
        });
}

function updateAgentWorkspace(agentId, updates, _retried) {
    return fetch('/api/agents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'update', id: agentId, revision: rosterRevision, ...updates }),
    }).then(r => r.json()).then(data => {
        if (data.status !== 'success') {
            // Two quick edits race: the second still carried the revision from
            // before the first landed. Re-sync and retry once, silently, so a
            // fast click just works instead of showing a lock error.
            if (data.code === 'stale_roster' && !_retried) {
                return loadAgentCatalog().then(() => updateAgentWorkspace(agentId, updates, true));
            }
            throw new Error(data.code === 'stale_roster' ? t('agents_stale') : (data.message || 'Update failed'));
        }
        return loadAgentCatalog().then(() => true);
    }).catch(err => {
        const status = document.getElementById('agent-profile-status') || document.getElementById('agent-editor-status');
        if (status) status.textContent = err.message;
        return false;
    });
}

function deleteAgent(agentId) {
    const agent = findAgent(agentId);
    if (!agent) return;
    if (agentId === defaultAgentId) return; // the default Agent is the instance
    showConfirmDialog({
        title: t('agents_delete_title'),
        message: t('agents_delete_confirm').replace('{name}', agent.name || agentId),
        okText: t('agents_delete'),
        cancelText: t('cancel'),
        onConfirm: () => _performAgentDelete(agentId),
    });
}

function _performAgentDelete(agentId, _retried) {
    return fetch('/api/agents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'delete', id: agentId, revision: rosterRevision }),
    }).then(r => r.json()).then(data => {
        if (data.status !== 'success') {
            if (data.code === 'stale_roster' && !_retried) {
                return loadAgentCatalog().then(() => _performAgentDelete(agentId, true));
            }
            throw new Error(data.code === 'stale_roster' ? t('agents_stale') : (data.message || 'Delete failed'));
        }
        // Leaving the detail open on a now-deleted Agent would show a ghost.
        if (selectedAdminAgentId === agentId) closeAgentDetail();
        // A conversation owned by the deleted Agent falls back to the default.
        if (activeAgentId === agentId) {
            activeAgentId = defaultAgentId;
            writeScopedPreference('cow_active_agent', activeAgentId);
        }
        // Drop the deleted Agent's remembered session id — its conversations
        // went with the workspace, so the pinned id would only re-pin a ghost.
        removeScopedPreference(`${SESSION_ID_KEY}:${agentId}`);
        return loadAgentCatalog().then(() => {
            renderComposerIdentity();
            // The Agent's sessions were removed server-side; refresh the open
            // list so its rows don't linger until the next unrelated reload.
            if (typeof _refreshHistoryList === 'function') _refreshHistoryList();
            return true;
        });
    }).catch(err => {
        const status = document.getElementById('agent-profile-status');
        if (status) status.textContent = err.message;
        else alert(err.message);
        return false;
    });
}

// The four core files an Agent can be edited through. BOOTSTRAP.md exists on
// disk for internal use but isn't meant for hand-editing, so it's left out of
// the picker entirely. Each option carries a short hint (rendered on the
// right of the dropdown row) so the raw filename isn't the only clue to what
// it holds.
function _agentCoreFileOptions() {
    return [
        { value: 'AGENT.md', label: 'AGENT.md', hint: t('agents_core_file_agent') },
        { value: 'USER.md', label: 'USER.md', hint: t('agents_core_file_user') },
        { value: 'RULE.md', label: 'RULE.md', hint: t('agents_core_file_rule') },
        { value: 'MEMORY.md', label: 'MEMORY.md', hint: t('agents_core_file_memory') },
    ];
}

let agentCoreViewMode = 'edit';

function initAgentCoreFileDropdown() {
    const el = document.getElementById('agent-core-file');
    if (!el) return;
    const current = el._ddValue || 'AGENT.md';
    initDropdown(el, _agentCoreFileOptions(), current, () => loadAgentCoreFile());
}

function currentAgentCoreFile() {
    const el = document.getElementById('agent-core-file');
    return (el && el._ddValue) || 'AGENT.md';
}

function setAgentCoreViewMode(mode) {
    agentCoreViewMode = mode;
    document.querySelectorAll('#agent-core-mode .agent-seg-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.mode === mode);
    });
    const editor = document.getElementById('agent-core-editor');
    const preview = document.getElementById('agent-core-preview');
    if (!editor || !preview) return;
    if (mode === 'preview') {
        preview.innerHTML = renderMarkdown(editor.value || '');
        // Same post-processing chat messages get: syntax highlighting plus the
        // language label + copy button on each code block (renderMarkdown only
        // produces the raw <pre>; the headers are added to the live DOM after).
        if (typeof applyHighlighting === 'function') applyHighlighting(preview);
        editor.classList.add('hidden');
        preview.classList.remove('hidden');
    } else {
        preview.classList.add('hidden');
        editor.classList.remove('hidden');
    }
}

function loadAgentCoreFile() {
    if (!selectedAdminAgentId) return;
    initAgentCoreFileDropdown();
    const filename = currentAgentCoreFile();
    if (!filename) return;
    _paintCoreFileStatus('pending', '…');
    fetch(`/api/agents/${encodeURIComponent(selectedAdminAgentId)}/files/${encodeURIComponent(filename)}`)
        .then(r => r.json()).then(data => {
            if (data.status !== 'success') throw new Error(data.message || t('agents_save_failed'));
            selectedCoreRevision = data.revision;
            document.getElementById('agent-core-editor').value = data.content || '';
            document.getElementById('agent-editor-label').textContent = `${selectedAdminAgentId} / ${filename}`;
            // The revision hash meant nothing to a human reader; a blank status
            // (nothing to report) reads better than a stray hex fragment.
            _paintCoreFileStatus('pending', '');
            // Refresh the preview in place if that's the active view, so
            // switching files while in preview mode doesn't show stale content.
            if (agentCoreViewMode === 'preview') setAgentCoreViewMode('preview');
        }).catch(err => { _paintCoreFileStatus('error', err.message); });
}

// Paint the save status with a colour + icon, not just bare text, so success
// and failure actually read differently at a glance. Success fades back to
// blank after a bit; failure stays until the next attempt so it isn't missed.
//
// Every other `*-status` element in this console is hidden via the shared
// `opacity-0` convention (see navigateTo/setLanguage, which blanket-fade any
// `[id$="-status"]` element on navigation). This one has the same id suffix
// so it gets caught by that same sweep — it must toggle `opacity-0` itself
// too, or a stray earlier sweep leaves it permanently invisible no matter
// what innerHTML is painted into it afterwards.
function _paintCoreFileStatus(kind, text) {
    const status = document.getElementById('agent-editor-status');
    if (!status) return;
    clearTimeout(_paintCoreFileStatus._t);
    status.classList.remove('agent-status-ok', 'agent-status-error');
    if (kind === 'ok') {
        status.innerHTML = `<i class="fas fa-check mr-1"></i>${escapeHtml(text)}`;
        status.classList.add('agent-status-ok');
        status.classList.remove('opacity-0');
        _paintCoreFileStatus._t = setTimeout(() => {
            status.textContent = '';
            status.classList.remove('agent-status-ok');
            status.classList.add('opacity-0');
        }, 2200);
    } else if (kind === 'error') {
        status.innerHTML = `<i class="fas fa-triangle-exclamation mr-1"></i>${escapeHtml(text)}`;
        status.classList.add('agent-status-error');
        status.classList.remove('opacity-0');
    } else {
        status.textContent = text || '';
        if (text) status.classList.remove('opacity-0');
    }
}

function saveAgentCoreFile() {
    if (!selectedAdminAgentId) return;
    const filename = currentAgentCoreFile();
    const btn = document.querySelector('#agent-detail-files button[onclick="saveAgentCoreFile()"]');
    _paintCoreFileStatus('pending', '…');
    if (btn) btn.disabled = true;
    fetch(`/api/agents/${encodeURIComponent(selectedAdminAgentId)}/files/${encodeURIComponent(filename)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: document.getElementById('agent-core-editor').value, revision: selectedCoreRevision }),
    }).then(async r => ({ ok: r.ok, data: await r.json() })).then(({ ok, data }) => {
        if (!ok || data.status !== 'success') throw new Error(data.message || t('agents_save_failed'));
        selectedCoreRevision = data.revision;
        _paintCoreFileStatus('ok', t('agents_saved'));
    }).catch(err => {
        _paintCoreFileStatus('error', err.message);
    }).finally(() => {
        if (btn) btn.disabled = false;
    });
}

// In-flight guard so a double-click on a card cannot spawn two session
// switches. Reset on every completed start / cancellation / error.
let _agentStartInFlight = null;
let _agentStartAgentId = null;

function cancelAgentStart() {
    _agentStartInFlight = null;
    _agentStartAgentId = null;
}

async function startChatWithAgent(agentId) {
    if (!agentId || _agentStartInFlight) return;
    if (typeof wsGuardUnsaved === 'function'
        && !wsGuardUnsaved(() => startChatWithAgent(agentId))) return;
    const attempt = { context: _wbContext() };
    _agentStartInFlight = attempt;
    _agentStartAgentId = agentId;
    _wbNoticeKey = '';
    renderAgentWorkbench();
    try {
        // The use-page roster is independent of the management cache. A card
        // created in another tab must work, and a removed target must not start.
        const agents = await fetchAgentWorkbench();
        if (_agentStartInFlight !== attempt || attempt.context !== _wbContext()) return;
        const agent = agents.find(a => a.id === agentId);
        applyAgentWorkbench(agents);
        if (!agent || !agent.can_chat) {
            refreshWorkbenchAfterUnavailable(agentId, agent?.unavailable_reason);
            return;
        }
        // The user could edit a file while validation was in flight. Settle it
        // once more before committing; no await occurs between this and newChat.
        if (typeof wsGuardUnsaved === 'function'
            && !wsGuardUnsaved(() => startChatWithAgent(agentId))) return;
        const cached = findAgent(agentId);
        if (cached) Object.assign(cached, agent, { enabled: true });
        else agentCatalog.push({ ...agent, enabled: true });
        if (agent.is_default) defaultAgentId = agent.id;
        activeAgentId = agentId;
        writeScopedPreference('cow_active_agent', activeAgentId);
        newChat(true, false);
        if (typeof resetWorkspaceToAgentRoot === 'function') resetWorkspaceToAgentRoot();
        navigateTo('chat');
        renderComposerIdentity();
        focusChatComposer();
    } catch (err) {
        if (_agentStartInFlight === attempt && attempt.context === _wbContext()) {
            showAgentStartNotice('agent_start_failed');
        }
    } finally {
        if (_agentStartInFlight === attempt) cancelAgentStart();
        if (currentView === 'agent-workbench') renderAgentWorkbench();
    }
}

// Focus the chat input once the fresh conversation is on screen, so the user
// can start typing immediately without an extra click.
function focusChatComposer() {
    const input = document.getElementById('chat-input');
    if (input) {
        requestAnimationFrame(() => { input.focus(); });
    }
}

// When a card's target is no longer usable (archived/disabled/removed), refresh
// the workbench list and surface a short notice instead of silently reusing a
// default Agent or a stale card.
function refreshWorkbenchAfterUnavailable(agentId, reason) {
    showAgentStartNotice(reason === 'permission_denied'
        ? 'agent_permission_denied'
        : reason === 'runtime_not_enabled'
            ? 'agent_runtime_not_enabled'
            : 'agent_target_unavailable');
}

function showAgentStartNotice(key) {
    _wbNoticeKey = key;
    if (currentView === 'agents') {
        const status = document.getElementById('agent-profile-status');
        if (status) {
            status.textContent = t(key);
            status.classList.remove('opacity-0', 'agent-status-ok');
        }
    } else if (currentView === 'chat') {
        showConfirmDialog({
            title: t('agents_pick_tip'), message: t(key), hideCancel: true,
        });
    } else {
        renderAgentWorkbench();
    }
}

/** Reflect the selected Agent's identity in the chat header, so a solo chat
 *  with a single Agent still clearly names who the conversation belongs to. */
function paintChatAgentIdentity(agent) {
    if (!agent) return;
    const nameEl = document.getElementById('chat-agent-name');
    const faceEl = document.getElementById('chat-agent-avatar');
    if (!nameEl) return;
    nameEl.textContent = agent.name || agent.id;
    if (faceEl) faceEl.innerHTML = agentAvatarHTML(agent, 22);
    const head = document.getElementById('chat-agent-identity');
    if (head) {
        head.classList.toggle('hidden', currentView !== 'chat');
        head.removeAttribute('hidden');
    }
}

function conversationHasMessages() {
    return !!document.querySelector('#chat-messages .user-message-group, #chat-messages .bot-message-group');
}

/** A roster of one behaves exactly like the console did before Agents existed:
 *  no face on the composer, no faces in the session list, no @ mentions. */
function multiAgentMode() {
    return availableChatAgents().length > 1;
}

/** True once this conversation holds more than its owner. Until then it is an
 *  ordinary chat and is drawn like one. */
function sharedConversation() {
    return currentTeamIds().length > 0;
}

// Who is answering each in-flight request, as reported when it was accepted.
// Lets a streaming bubble carry the right name before anything is persisted.
const _liveSpeakers = {};

function rememberLiveSpeaker(data) {
    if (data && data.request_id && data.speaker) {
        _liveSpeakers[data.request_id] = data.speaker;
    }
}

/** Repaint a still-visible loading indicator with the resolved speaker's face,
 *  once /message has said who took the turn. No-op if streaming already
 *  replaced the dots with a bubble. */
function setLoadingSpeaker(loadingEl, requestId) {
    if (!loadingEl || !loadingEl.isConnected) return;
    const face = loadingEl.querySelector('.bot-face');
    if (face) face.innerHTML = agentAvatarHTML(liveSpeakerAgent(requestId), 32);
}

/** The Agent to draw on a reply, or null to keep the product's own face. */
function botSpeakerAgent(msg, requestId) {
    if (!sharedConversation()) return null;
    const id = (msg && msg.extras && msg.extras.agent_id)
        || (requestId && _liveSpeakers[requestId])
        || activeAgentId;
    return findAgent(id) || null;
}

/** The Agent answering a live request, for the streaming bubble and the loading
 *  dots. Unlike botSpeakerAgent this also resolves in a solo chat, so a single
 *  Agent's own uploaded avatar shows while it streams instead of the logo. */
function liveSpeakerAgent(requestId) {
    const id = (requestId && _liveSpeakers[requestId]) || activeAgentId;
    return findAgent(id) || null;
}

/** Turn a written-out mention into a chip, so a name reads as a name instead
 *  of as an id someone pasted. Runs on the rendered bubble rather than on the
 *  markdown source, which keeps code spans untouched. */
function highlightMentions(root) {
    const roster = sessionRoster();
    if (!root || roster.length < 2) return;
    const byLabel = new Map();
    roster.forEach(agent => {
        [agent.name, agent.id].forEach(label => {
            if (label) byLabel.set(String(label).toLowerCase(), agent);
        });
    });
    const alternation = Array.from(byLabel.keys())
        .sort((a, b) => b.length - a.length)
        .map(label => label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
        .join('|');
    const re = new RegExp('@(' + alternation + ')(?=[\\s，,：:、]|$)', 'gi');

    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
        acceptNode: node => node.parentElement
            && node.parentElement.closest('code, pre, .mention-tag')
            ? NodeFilter.FILTER_REJECT
            : NodeFilter.FILTER_ACCEPT,
    });
    const targets = [];
    let node;
    while ((node = walker.nextNode())) {
        re.lastIndex = 0;
        if (re.test(node.nodeValue)) targets.push(node);
    }
    targets.forEach(text => {
        const value = text.nodeValue;
        const frag = document.createDocumentFragment();
        let cursor = 0;
        let match;
        re.lastIndex = 0;
        while ((match = re.exec(value))) {
            if (match.index > cursor) {
                frag.appendChild(document.createTextNode(value.slice(cursor, match.index)));
            }
            const agent = byLabel.get(match[1].toLowerCase());
            const tag = document.createElement('span');
            tag.className = 'mention-tag';
            if (agent) {
                // A chip that looks like the teammate it names: their face, then
                // their name. Falls back to plain text for an unknown label.
                tag.innerHTML = `<span class="mention-tag-face">${agentAvatarHTML(agent, 16)}</span><span class="mention-tag-name">${escapeHtml(agent.name || agent.id)}</span>`;
            } else {
                tag.textContent = '@' + match[1];
            }
            frag.appendChild(tag);
            cursor = match.index + match[0].length;
        }
        if (cursor < value.length) {
            frag.appendChild(document.createTextNode(value.slice(cursor)));
        }
        text.parentNode.replaceChild(frag, text);
    });
}

function renderComposerIdentity() {
    const agent = findAgent(activeAgentId) || { id: activeAgentId || defaultAgentId, name: activeAgentId || 'Agent' };
    paintChatAgentIdentity(agent);
    const wrap = document.getElementById('composer-identity');
    const btn = document.getElementById('composer-agent-btn');
    if (!wrap || !btn) return;
    // A solo install has no choice to offer. Existing teammates still need
    // their menu even if the available catalog shrinks after this chat began.
    if (!multiAgentMode() && !currentTeamIds().length) {
        wrap.classList.add('hidden');
        document.getElementById('composer-agent-menu')?.classList.add('hidden');
        return;
    }
    wrap.classList.remove('hidden');
    const others = currentTeamIds().length;
    btn.innerHTML = agentAvatarHTML(agent, 22)
        + (others ? `<span class="composer-agent-count">${others + 1}</span>` : '');
    const face = btn.querySelector('.agent-avatar');
    if (face) face.id = 'composer-agent-avatar';
    // The owner can only be swapped before the first turn, but joining is
    // allowed at any point, so the button itself never goes dead.
    btn.classList.toggle('locked', conversationHasMessages());
    btn.dataset.tooltip = agent.name || agent.id;
}

function toggleComposerAgentMenu(event) {
    event.stopPropagation();
    const menu = document.getElementById('composer-agent-menu');
    if (!menu) return;
    if (!menu.classList.contains('hidden')) {
        menu.classList.add('hidden');
        return;
    }
    _closeComposerMenus(menu);
    renderComposerAgentMenu();
    menu.classList.remove('hidden');
}

/** Paint the agent menu's body from the current roster / team. Kept separate
 *  from the open/close toggle so an invite or removal can refresh the list in
 *  place — the menu stays open, the +/× flips, and the user can keep going. */
function renderComposerAgentMenu() {
    const menu = document.getElementById('composer-agent-menu');
    if (!menu) return;
    const taken = new Set(currentTeamIds());
    const members = (_sessCfg && _sessCfg.team && _sessCfg.team.members) || [];
    const sections = [];

    // Once a conversation has teammates it is a group, and the only sensible
    // actions are adding and removing members - "switch the current Agent" would
    // silently abandon the group for a fresh solo chat. So the switch list only
    // appears in an ordinary (not-yet-shared) chat, where it opens a clean
    // conversation owned by the chosen Agent.
    if (!sharedConversation()) {
        sections.push(
            `<div class="composer-menu-title">${escapeHtml(t('agents_pick_tip'))}</div>`
            + availableChatAgents().map(agent => `
                <button type="button" class="composer-menu-item agent-row${agent.id === activeAgentId ? ' current' : ''}"
                        onclick="pickComposerAgent('${escapeHtml(agent.id)}')">
                    ${agentAvatarHTML(agent, 24)}
                    <span>${escapeHtml(agent.name)}</span>
                    ${agent.id === activeAgentId ? '<i class="fas fa-check ml-auto text-[11px]"></i>' : ''}
                </button>`).join('')
        );
    }

    const candidates = availableChatAgents().filter(a => a.id !== activeAgentId && !taken.has(a.id));

    // A group chat first lists the teammates already in the conversation (the
    // owner is implicit and not shown), then, in a separate section below, who
    // can still be pulled in. Splitting the two makes it obvious these rows are
    // members to remove, not options to pick.
    if (sharedConversation()) {
        const joined = members.filter(m => m.id !== activeAgentId).map(m => `
            <button type="button" class="composer-menu-item agent-row joined"
                    onclick="removeTeamMember('${escapeHtml(m.id)}')" title="${escapeHtml(t('team_remove'))}">
                ${agentAvatarHTML(m, 24)}
                <span>${escapeHtml(m.name || m.id)}</span>
                <i class="fas fa-check ml-auto text-[11px] joined-check"></i>
                <i class="fas fa-xmark ml-auto text-[11px] joined-remove"></i>
            </button>`).join('');
        if (joined) {
            sections.push(
                `<div class="composer-menu-title">${escapeHtml(t('team_members'))}</div>${joined}`
            );
        }
    }

    const invitable = candidates.map(agent => `
        <button type="button" class="composer-menu-item agent-row"
                onclick="inviteTeamMember('${escapeHtml(agent.id)}')">
            ${agentAvatarHTML(agent, 24)}
            <span>${escapeHtml(agent.name)}</span>
            <i class="fas fa-plus ml-auto text-[11px] text-slate-400"></i>
        </button>`).join('');
    if (invitable) {
        sections.push(
            `<div class="composer-menu-title">${escapeHtml(t('team_invite'))}</div>${invitable}`
        );
    }

    // Always offer a way to make a new Agent, so a single-Agent user discovers
    // the team feature straight from the composer.
    sections.push(
        `<button type="button" class="composer-menu-item agent-row composer-menu-create"
                onclick="openAgentCreateFromComposer()">
            <span class="composer-menu-create-icon"><i class="fas fa-plus"></i></span>
            <span>${escapeHtml(t('agents_create'))}</span>
        </button>`
    );

    menu.innerHTML = sections.join('<div class="composer-menu-sep"></div>');
}

/** Jump from the composer straight into agent creation: close the menu, land on
 *  the team tab, and open the create form. */
function openAgentCreateFromComposer() {
    document.getElementById('composer-agent-menu')?.classList.add('hidden');
    navigateTo('agents');
    if (typeof openAgentCreateForm === 'function') openAgentCreateForm();
}

function pickComposerAgent(agentId) {
    document.getElementById('composer-agent-menu')?.classList.add('hidden');
    if (!agentId || agentId === activeAgentId) return;
    // Use the same guarded, freshly validated start as the workbench. A cancel
    // must keep the current owner, and a switch must not inherit its project.
    return startChatWithAgent(agentId);
}

function inviteTeamMember(agentId) {
    // Keep the menu open so the invited Agent visibly moves from "+ add" to the
    // "× remove" list, and the user can invite several in a row without having
    // to reopen it each time.
    addTeamMember(agentId).then(refreshComposerAgentMenuIfOpen);
}

/** Everyone addressable in this conversation, owner first. */
function sessionRoster() {
    const owner = findAgent(activeAgentId);
    const members = (_sessCfg && _sessCfg.team && _sessCfg.team.members) || [];
    const roster = owner ? [owner] : [];
    members.forEach(m => {
        if (!roster.some(a => a.id === m.id)) roster.push(findAgent(m.id) || m);
    });
    return roster;
}

/** The teammate a message hands the turn to, or '' for nobody.
 *  Mirrors the server's rule: a leading mention only. */
function addressedAgentId(text) {
    const stripped = String(text || '').replace(/^\s+/, '');
    if (!stripped.startsWith('@')) return '';
    const labels = [];
    sessionRoster().forEach(agent => {
        [agent.name, agent.id].forEach(label => {
            if (label) labels.push([String(label), agent.id]);
        });
    });
    labels.sort((a, b) => b[0].length - a[0].length);
    for (const [label, id] of labels) {
        const re = new RegExp('^@' + label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '(?=[\\s，,：:、]|$)', 'i');
        // The owner is addressable too; the server treats "@owner" as the owner
        // simply taking the turn, so no special-casing here.
        if (re.test(stripped)) return id;
    }
    return '';
}

function mentionedAgentIds(text) {
    const id = addressedAgentId(text);
    return id ? [id] : [];
}

function currentTeamIds() {
    return ((_sessCfg && _sessCfg.team && _sessCfg.team.members) || []).map(m => m.id);
}

function setTeamMembers(ids) {
    const unique = Array.from(new Set(ids.filter(id => id && id !== activeAgentId)));
    return fetch(`/api/sessions/${encodeURIComponent(sessionId)}/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ members: unique.length ? unique : null }),
    }).then(r => r.json()).then(data => {
        if (data.status === 'success') {
            _sessCfg = { model: data.model, permission: data.permission, team: data.team };
            renderComposerIdentity();
            // Inviting or removing someone changes whether one model can speak
            // for this conversation.
            _renderModelChip();
        }
    });
}

function addTeamMember(agentId) {
    if (!agentId || agentId === activeAgentId) return Promise.resolve();
    const ids = currentTeamIds();
    if (ids.includes(agentId)) return Promise.resolve();
    return setTeamMembers([...ids, agentId]);
}

function removeTeamMember(agentId) {
    return setTeamMembers(currentTeamIds().filter(id => id !== agentId))
        .then(refreshComposerAgentMenuIfOpen);
}

/** Repaint the agent menu if it is still open, so add/remove show immediately. */
function refreshComposerAgentMenuIfOpen() {
    const menu = document.getElementById('composer-agent-menu');
    if (menu && !menu.classList.contains('hidden')) renderComposerAgentMenu();
}

async function syncTeamFromText(text) {
    const extra = mentionedAgentIds(text);
    if (!extra.length) return;
    await setTeamMembers([...currentTeamIds(), ...extra]);
}

// Point a channel instance at an Agent. Binding lives on the instance itself
// (channel_instances[].agent_id); an empty agentId means "follow the default
// Agent". instanceId defaults to the channel type for a single-instance channel.
function bindChannelAgent(channelType, agentId, instanceId, members) {
    const defaultId = defaultAgentId;
    const bound = (agentId && agentId !== defaultId) ? agentId : '';
    const iid = instanceId || channelType;
    const payload = {
        action: 'bind_channel_instance',
        channel_type: channelType,
        instance_id: iid,
        agent_id: bound,
    };
    // Only send members when we mean to set the team; omitting it leaves the
    // stored roster untouched (a plain owner-only rebind).
    if (Array.isArray(members)) payload.members = members;
    return fetch('/api/agents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    }).then(r => r.json()).then(data => {
        if (data.status !== 'success') throw new Error(data.message || 'Save failed');
        // Rebinding is a hot swap on the server (no channel restart), and the
        // dropdown already reflects the new value locally, so only the roster
        // catalog needs refreshing. Re-rendering the channels view here would
        // rebuild the cards and reset the scan/manual tab state for no reason.
        if (Array.isArray(channelInstancesView)) {
            const rec = channelInstancesView.find(i => i.instance_id === iid);
            if (rec) {
                rec.agent_id = bound;
                if (data.result && Array.isArray(data.result.members)) {
                    rec.members = data.result.members.slice();
                }
            }
        }
        return loadAgentCatalog();
    }).catch(err => _wsToast(err.message));
}

function channelBoundAgentId(channelType) {
    const inst = channelInstances.find(i =>
        (i.channel_type || '').toLowerCase() === channelType
    );
    return inst ? (inst.agent_id || '') : '';
}

let memoryAgentId = readScopedPreference('cow_memory_agent') || '';

function viewingMemoryAgentId() {
    return memoryAgentId || activeAgentId || defaultAgentId;
}

function renderMemoryAgentSelect() {
    const el = document.getElementById('memory-agent-select');
    if (!el) return;
    const current = viewingMemoryAgentId();
    const list = agentCatalog.length ? agentCatalog : enabledAgents();
    const options = list.map(a => ({ value: a.id, label: a.name || a.id, agent: a }));
    initDropdown(el, options, current, (value) => selectMemoryAgent(value), { withAvatar: true });
}

function selectMemoryAgent(agentId) {
    memoryAgentId = agentId;
    writeScopedPreference('cow_memory_agent', agentId);
    closeMemoryViewer();
    loadMemoryView(1);
}

// =====================================================================
// Markdown Renderer
// =====================================================================
const FALLBACK_HLJS = {
    getLanguage() { return false; },
    highlight(str) { return { value: escapeHtml(str) }; },
    highlightAuto(str) { return { value: escapeHtml(str) }; },
    highlightElement() {},
};

function getHljs() {
    return window.hljs || FALLBACK_HLJS;
}

// CJK ideographs, kana, Hangul and full/halfwidth forms (BMP only).
const CJK_CHAR_RE = /[\u1100-\u11FF\u2E80-\u303F\u3040-\u33FF\u3400-\u4DBF\u4E00-\u9FFF\uA960-\uA97F\uAC00-\uD7FF\uF900-\uFAFF\uFE10-\uFE19\uFE30-\uFE6F\uFF00-\uFF60\uFFE0-\uFFE6]/;

// CommonMark's flanking rules treat every Unicode punctuation alike, so
// `是**"引号"**——` never opens emphasis: the quote after `**` is punctuation
// while 是 before it is neither punctuation nor space, and the run degrades to
// literal asterisks. Apply the CJK-friendly amendment
// (github.com/tats-u/markdown-cjk-friendly): a `*` run with a CJK neighbour and
// no adjacent whitespace both opens and closes. `_` keeps the stock rules,
// whose intraword behavior depends on the original classification.
function patchCjkEmphasis(md) {
    const State = md.inline && md.inline.State;
    if (!State || !State.prototype.scanDelims || State.prototype._cjkEmphasisPatched) return;
    const utils = md.utils;
    const scanDelims = State.prototype.scanDelims;
    State.prototype.scanDelims = function(start, canSplitWord) {
        const res = scanDelims.call(this, start, canSplitWord);
        if (!canSplitWord) return res;
        const lastCode = start > 0 ? this.src.charCodeAt(start - 1) : 0x20;
        const nextPos = start + res.length;
        const nextCode = nextPos < this.posMax ? this.src.charCodeAt(nextPos) : 0x20;
        if (utils.isWhiteSpace(lastCode) || utils.isWhiteSpace(nextCode)) return res;
        if (!CJK_CHAR_RE.test(String.fromCharCode(lastCode)) &&
            !CJK_CHAR_RE.test(String.fromCharCode(nextCode))) return res;
        res.can_open = true;
        res.can_close = true;
        return res;
    };
    State.prototype._cjkEmphasisPatched = true;
}

function createMd() {
    const hljsLib = getHljs();
    const mdFactory = window.markdownit;
    if (typeof mdFactory !== 'function') {
        return {
            render(text) {
                return `<p>${escapeHtml(text || '')}</p>`;
            }
        };
    }
    const md = mdFactory({
        html: false, breaks: true, linkify: true, typographer: true,
        highlight: function(str, lang) {
            if (lang && hljsLib.getLanguage(lang)) {
                try { return hljsLib.highlight(str, { language: lang }).value; } catch (_) {}
            }
            return hljsLib.highlightAuto(str).value;
        }
    });
    patchCjkEmphasis(md);
    // Fix greedy linkify: markdown-it's linkify swallows markdown emphasis (*)
    // and CJK full-width punctuation glued to a URL (common in LLM output like
    // "**https://x**，中文"), turning the whole tail into one broken link. Cut
    // the URL at the first such char and spill the remainder back as text.
    var GREEDY_LINK_CUT = /[*\u3000-\u303F\uFF00-\uFFEF]/;
    md.core.ruler.after('linkify', 'fix_greedy_linkify', function(state) {
        for (var b = 0; b < state.tokens.length; b++) {
            var blk = state.tokens[b];
            if (blk.type !== 'inline' || !blk.children) continue;
            var ch = blk.children;
            for (var i = 0; i < ch.length; i++) {
                var open = ch[i];
                if (open.type !== 'link_open' || open.markup !== 'linkify') continue;
                var textTok = ch[i + 1], close = ch[i + 2];
                if (!textTok || textTok.type !== 'text' || !close || close.type !== 'link_close') continue;
                var idx = textTok.content.search(GREEDY_LINK_CUT);
                if (idx < 0) continue;
                var keep = textTok.content.slice(0, idx);
                var spill = textTok.content.slice(idx);
                textTok.content = keep;
                open.attrSet('href', keep);
                var spillTok = new state.Token('text', '', 0);
                spillTok.content = spill;
                ch.splice(i + 3, 0, spillTok);
            }
        }
    });
    const defaultLinkOpen = md.renderer.rules.link_open || function(tokens, idx, options, env, self) {
        return self.renderToken(tokens, idx, options);
    };
    md.renderer.rules.link_open = function(tokens, idx, options, env, self) {
        const token = tokens[idx];
        // A workspace-relative href would resolve against the console URL and
        // 404 in a new tab. Tag it instead so the click handler in
        // workspace.js opens it in the preview panel.
        const wsPath = typeof wsWorkspaceHref === 'function'
            ? wsWorkspaceHref(token.attrGet('href') || '') : null;
        if (wsPath) {
            token.attrPush(['data-ws-path', wsPath]);
            token.attrJoin('class', 'ws-link');
        } else {
            token.attrPush(['target', '_blank']);
            token.attrPush(['rel', 'noopener noreferrer']);
        }
        return defaultLinkOpen(tokens, idx, options, env, self);
    };
    // A table can't shrink below its columns' minimum content width, so a wide
    // comparison table would run past the bubble. Wrap it in a scroller: it
    // still fills the bubble when it fits and scrolls sideways when it doesn't.
    const defaultTableOpen = md.renderer.rules.table_open || function(tokens, idx, options, env, self) {
        return self.renderToken(tokens, idx, options);
    };
    const defaultTableClose = md.renderer.rules.table_close || function(tokens, idx, options, env, self) {
        return self.renderToken(tokens, idx, options);
    };
    md.renderer.rules.table_open = function(tokens, idx, options, env, self) {
        return '<div class="table-wrap">' + defaultTableOpen(tokens, idx, options, env, self);
    };
    md.renderer.rules.table_close = function(tokens, idx, options, env, self) {
        return defaultTableClose(tokens, idx, options, env, self) + '</div>';
    };
    return md;
}

const md = createMd();

const VIDEO_EXT_RE = /\.(?:mp4|webm|mov|avi|mkv)$/i;  // tested against URL without query string
const IMAGE_EXT_RE = /\.(?:jpg|jpeg|png|gif|webp|bmp|svg)$/i;  // tested against URL without query string

// Windows absolute path (D:\x.png / D:/x.png).
const WIN_ABS_PATH_RE = /^[A-Za-z]:[\\/]/;

function _toWebUrl(url) {
    if ((/^\/[A-Za-z]/.test(url) || WIN_ABS_PATH_RE.test(url)) && !url.startsWith('/api/')) {
        return '/api/file?path=' + encodeURIComponent(url);
    }
    if (/^file:\/\/\//i.test(url)) {
        // file:///home/x → /home/x, but file:///D:/x stays drive-relative.
        const p = url.replace(/^file:\/\/\//i, '');
        return '/api/file?path=' + encodeURIComponent(WIN_ABS_PATH_RE.test(p) ? p : '/' + p);
    }
    return url;
}

function _buildVideoHtml(url) {
    const webUrl = _toWebUrl(url);
    const fileName = url.split('/').pop().split('?')[0];
    return `<div style="margin:10px 0;">` +
        `<video controls preload="metadata" ` +
        `style="max-width:100%;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.15);display:block;">` +
        `<source src="${webUrl}"></video>` +
        `<a href="${webUrl}" target="_blank" ` +
        `style="display:inline-flex;align-items:center;gap:4px;margin-top:4px;font-size:12px;color:#8b8fa8;text-decoration:none;">` +
        `<i class="fas fa-download"></i> ${escapeHtml(fileName)}</a></div>`;
}

function _openImageLightbox(src) {
    let overlay = document.getElementById('cow-lightbox');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'cow-lightbox';
        overlay.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.85);display:flex;align-items:center;justify-content:center;cursor:zoom-out;opacity:0;transition:opacity .2s';
        overlay.onclick = () => { overlay.style.opacity = '0'; setTimeout(() => overlay.style.display = 'none', 200); };
        const img = document.createElement('img');
        img.id = 'cow-lightbox-img';
        img.style.cssText = 'max-width:92vw;max-height:92vh;border-radius:8px;box-shadow:0 4px 24px rgba(0,0,0,0.5);object-fit:contain;';
        img.onclick = (e) => e.stopPropagation();
        overlay.appendChild(img);
        document.body.appendChild(overlay);
    }
    overlay.querySelector('#cow-lightbox-img').src = src;
    overlay.style.display = 'flex';
    requestAnimationFrame(() => overlay.style.opacity = '1');
}

function _buildImageHtml(url) {
    const webUrl = _toWebUrl(url);
    const safeUrl = webUrl.replace(/"/g, '&quot;');
    return `<div style="margin:10px 0;">` +
        `<img src="${safeUrl}" alt="image" loading="lazy" ` +
        `onclick="_openImageLightbox(this.src)" ` +
        `style="max-width:520px;width:100%;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.15);display:block;cursor:zoom-in;">` +
        `</div>`;
}

function injectVideoPlayers(html) {
    // Step 1: replace markdown-it anchor tags whose href points to a video file.
    const step1 = html.replace(
        /<a\s+href="(https?:\/\/[^"]+)"[^>]*>[^<]*<\/a>/gi,
        (match, url) => VIDEO_EXT_RE.test(url.split('?')[0]) ? _buildVideoHtml(url) : match
    );
    // Step 2: replace any remaining bare video URLs in text nodes (not inside HTML tags).
    // Split on HTML tags to avoid touching src/href attributes already in markup.
    return step1.split(/(<[^>]+>)/).map((chunk, idx) => {
        // Even indices are text nodes; odd indices are HTML tags — leave them untouched.
        if (idx % 2 !== 0) return chunk;
        return chunk.replace(/https?:\/\/\S+/gi, (url) => {
            const bare = url.replace(/[),.\s]+$/, '');  // strip trailing punctuation
            return VIDEO_EXT_RE.test(bare.split('?')[0]) ? _buildVideoHtml(bare) : url;
        });
    }).join('');
}

// Convert image URLs into inline <img> previews. Mirrors injectVideoPlayers but for images.
// Handles three cases produced by markdown-it:
//   1. <a href="...image.jpg">...</a>  (bare URL or autolink that linkify turned into an anchor)
//   2. <img src="...">                  (markdown image syntax) — leave as-is, but normalize style
//   3. raw URL still present in a text node                    — only as a safety net
function injectImagePreviews(html) {
    // Step 1: anchor whose href points to an image file -> replace with <img> preview.
    const step1 = html.replace(
        /<a\s+href="(https?:\/\/[^"]+)"[^>]*>[^<]*<\/a>/gi,
        (match, url) => IMAGE_EXT_RE.test(url.split('?')[0]) ? _buildImageHtml(url) : match
    );
    // Step 2: bare image URLs left in text nodes (rare — markdown-it's linkify usually catches them).
    return step1.split(/(<[^>]+>)/).map((chunk, idx) => {
        if (idx % 2 !== 0) return chunk;
        return chunk.replace(/https?:\/\/\S+/gi, (url) => {
            const bare = url.replace(/[),.\s]+$/, '');
            return IMAGE_EXT_RE.test(bare.split('?')[0]) ? _buildImageHtml(bare) : url;
        });
    }).join('');
}

function _rewriteLocalImgSrc(html) {
    return html.replace(/<img\s([^>]*?)src="([^"]+)"([^>]*?)>/gi, (match, pre, src, post) => {
        const webSrc = _toWebUrl(src);
        const safeSrc = webSrc.replace(/"/g, '&quot;');
        const hasClick = /onclick/i.test(pre + post);
        const clickAttr = hasClick ? '' : ` onclick="_openImageLightbox(this.src)" style="cursor:zoom-in;"`;
        return `<img ${pre}src="${safeSrc}"${post}${clickAttr}>`;
    });
}

function renderMarkdown(text) {
    try {
        let html = md.render(text);
        html = _rewriteLocalImgSrc(html);
        // Order matters: video first (more specific), then image.
        html = injectImagePreviews(injectVideoPlayers(html));
        // Fallback for files the agent only mentions by path (workspace.js).
        if (typeof injectFileChips === 'function') html = injectFileChips(html);
        // Note: Code block headers are added via DOM manipulation after insertion
        // See addCodeBlockHeadersToElement()
        return html;
    }
    catch (e) { return text.replace(/\n/g, '<br>'); }
}

function _addCodeBlockHeaders(container) {
    // Add header with language label and copy button to each <pre> block using DOM manipulation
    const preBlocks = container.querySelectorAll('pre');
    preBlocks.forEach(pre => {
        if (pre.parentElement && pre.parentElement.classList.contains('code-block-wrapper')) return;
        
        const codeEl = pre.querySelector('code');
        if (!codeEl) return;
        
        const langClass = Array.from(codeEl.classList).find(c => c.startsWith('language-'));
        const language = langClass ? langClass.replace('language-', '') : '';
        // Hide label for unknown/empty languages (e.g. language-undefined)
        const showLang = language && language !== 'undefined' && language !== 'code';
        const langLabel = showLang ? language.charAt(0).toUpperCase() + language.slice(1) : '';
        
        const wrapper = document.createElement('div');
        wrapper.className = 'code-block-wrapper';
        
        const header = document.createElement('div');
        header.className = 'code-block-header';
        header.innerHTML = `
            <span class="code-block-lang">${langLabel}</span>
            <button class="code-copy-btn" title="Copy code">
                <i class="fas fa-copy"></i>
            </button>
        `;
        
        pre.parentNode.insertBefore(wrapper, pre);
        wrapper.appendChild(header);
        wrapper.appendChild(pre);
    });
}

// =====================================================================
// Chat Module
// =====================================================================
let isPolling = false;
let pollGeneration = 0;   // incremented on each restart to cancel stale poll loops
let loadingContainers = {};
let activeStreams = {};   // request_id -> EventSource
let sessionActiveRequest = {};   // agent_id + session_id -> request_id
const PENDING_VOICE_ATTACH_TTL_MS = 2 * 60 * 1000;
const PENDING_VOICE_ATTACH_MAX = 100;
const pendingVoiceAttachments = new Map(); // session_id:bot_seq -> pending audio

function runtimeSessionKey(sid, agentId = activeAgentId) {
    return `${agentId || defaultAgentId || 'default'}::${sid}`;
}

function isCurrentSessionConversationActive() {
    return !!sessionActiveRequest[runtimeSessionKey(sessionId)];
}

function updateEditButtonsState() {
    const active = isCurrentSessionConversationActive();
    document.querySelectorAll('.edit-msg-btn, .delete-msg-btn').forEach(btn => {
        btn.disabled = active;
        if (btn.classList.contains('edit-msg-btn')) {
            btn.title = active
                ? t('edit_disabled_reply_active')
                : t('edit_message');
        } else {
            btn.title = active
                ? t('delete_disabled_reply_active')
                : t('delete_message_title');
        }
    });
}
let streamBuffers = {};   // request_id -> { items: [event...], timestamp } for re-attach replay
let isComposing = false;
let appConfig = { use_agent: false, title: PRODUCT_NAME, subtitle: '', providers: {}, api_bases: {} };

let activeAgentId = readScopedPreference('cow_active_agent') || '';
const SESSION_ID_KEY = 'cow_session_id';

function activeSessionStorageKey() {
    return activeAgentId && activeAgentId !== defaultAgentId && activeAgentId !== 'default'
        ? `${SESSION_ID_KEY}:${activeAgentId}`
        : SESSION_ID_KEY;
}

// Carry the selected Agent through existing console requests without forcing
// every feature panel to implement its own routing glue.
const _nativeFetch = window.fetch.bind(window);
window.fetch = function(input, init) {
    init = init ? { ...init } : {};
    let url = typeof input === 'string' ? input : input.url;
    // In database identity mode the request context is tenant-scoped. The
    // selected tenant lives in sessionStorage (cow_tenant_id) but the core
    // console requests (agents / sessions / history / knowledge) do not
    // otherwise carry it, so the backend rejects them with a 400
    // "tenant selection required". Inject the header for same-origin /api
    // requests here, mirroring identity-admin.js / todos.js apiFetch. This is
    // a no-op in legacy mode (no tenant is ever stored).
    const tenantId = sessionStorage.getItem('cow_tenant_id');
    if (tenantId && typeof url === 'string' && url.startsWith('/')
            && (/^\/api\//.test(url)
                || /^\/(message|stream|poll|cancel)\b/.test(url))
            && !/^\/api\/auth\//.test(url)) {
        const headers = init.headers instanceof Headers
            ? new Headers(init.headers)
            : new Headers(init.headers || {});
        if (!headers.has('X-Tenant-ID')) headers.set('X-Tenant-ID', tenantId);
        init.headers = headers;
    }
    if (activeAgentId && typeof url === 'string' && url.startsWith('/')) {
        if (!/[?&]agent_id=/.test(url)) {
            const joiner = url.includes('?') ? '&' : '?';
            url = `${url}${joiner}agent_id=${encodeURIComponent(activeAgentId)}`;
        }
        if (typeof input !== 'string') input = new Request(url, input);
        else input = url;

        // JSON bodies read agent_id from the payload, so inject it there too.
        // Multipart (FormData) uploads must NOT get a body copy: the query
        // string above already carries it, and web.py merges query + body,
        // collapsing the duplicate into a list (agent_id=['x','x']). That list
        // then reaches handlers expecting a plain string and raises
        // "unhashable type: 'list'", silently killing every file upload.
        if (typeof init.body === 'string') {
            const contentType = new Headers(init.headers || {}).get('Content-Type') || '';
            if (contentType.includes('application/json')) {
                try {
                    const body = JSON.parse(init.body);
                    if (body && typeof body === 'object' && !Array.isArray(body) && !body.agent_id) {
                        body.agent_id = activeAgentId;
                        init.body = JSON.stringify(body);
                    }
                } catch (_) {}
            }
        }
    }
    return _nativeFetch(input, init);
};

function generateSessionId() {
    return 'session_' + ([1e7]+-1e3+-4e3+-8e3+-1e11).replace(/[018]/g, c =>
        (c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> c / 4).toString(16)
    );
}

// Restore session_id from localStorage so conversation history survives page refresh.
// A new id is only generated when the user explicitly starts a new chat.
function loadOrCreateSessionId() {
    const stored = readScopedPreference(activeSessionStorageKey());
    if (stored) return stored;
    const fresh = generateSessionId();
    writeScopedPreference(activeSessionStorageKey(), fresh);
    return fresh;
}

let sessionId = loadOrCreateSessionId();

// ---- Conversation history state ----
let historyPage = 0;       // last page fetched (0 = nothing fetched yet)
let historyHasMore = false;
let historyLoading = false;
let _historyLoadSeq = 0;
let _historyLoadContext = '';

function restoreChatState() {
    const epoch = _authEpoch, owner = activeAgentId, sid = sessionId;
    const current = () => epoch === _authEpoch && owner === activeAgentId && sid === sessionId;
    return fetch('/config').then(r => r.json()).then(data => {
        if (!current()) return;
        if (data.status === 'success') {
            appConfig = data;
            appConfig.title = productTitle(data.title);
            const welcomeTitle = document.getElementById('welcome-title');
            if (welcomeTitle) welcomeTitle.innerHTML = productTitleHTML(appConfig.title);
            initConfigView(data);
        }
        loadHistory(1);
    }).catch(() => { if (current()) loadHistory(1); });
}

// Load the public brand snapshot and apply it once the DOM is ready. This
// drives the sidebar / login / welcome / favicon / title from the SAME source
// the published brand uses, independent of the legacy /config.title projection.
function fetchPublicBrand(seq) {
    const requestSeq = (seq != null ? seq : ++brandFetchSeq);
    return fetch('/api/branding/public').then(r => r.json()).then(data => {
        // A later public read must not clobber a version published by THIS tab
        // after it was issued.
        if (requestSeq < brandSaveEpoch) return;
        if (data && data.brand_name) {
            if (requestSeq < brandFetchSeq) return; // a newer read already landed
            brandState = {
                enabled: !!data.enabled,
                revision: data.revision || 0,
                brand_name: data.brand_name || DEFAULT_BRAND.brand_name,
                logo_description: (data.logo_description != null) ? data.logo_description : '',
                logo_url: data.logo_url || DEFAULT_BRAND.logo_url,
                favicon_url: data.favicon_url || DEFAULT_BRAND.favicon_url,
            };
            brandLoaded = true;
            // Keep the legacy config title in sync so any code reading it stays
            // consistent without a second source of truth.
            if (appConfig) appConfig.title = productTitle(brandState.brand_name);
        }
        applyBrandToDocument();
        applyBrandToAgentAvatars();
    }).catch(() => { /* keep last known brand; never break the console */ });
}

// Fetch immediately and re-validate on visibility / view entry.
fetchPublicBrand();
document.addEventListener('DOMContentLoaded', applyBrandToDocument);
document.addEventListener('visibilitychange', () => {
    if (!document.hidden) fetchPublicBrand();
});

const chatInput = document.getElementById('chat-input');
const sendBtn = document.getElementById('send-btn');
const steerBtn = document.getElementById('steer-btn');
const messagesDiv = document.getElementById('chat-messages');
// Cache only welcome markup; the real composer remains a sibling for its entire
// lifetime. CSS orders it between the intro and suggestions in an empty chat.
const welcomeTemplateHTML = document.getElementById('welcome-screen')?.innerHTML || '';
function syncChatHomeLayout() {
    const main = document.getElementById('chat-main');
    if (!main) return;
    const home = !!document.getElementById('welcome-screen');
    const changed = home !== main.classList.contains('chat-home');
    main.classList.toggle('chat-home', home);
    if (changed) main.scrollTop = 0;
}
function bindWelcomeSuggestions(root) {
    root.querySelectorAll('.example-card').forEach(card => {
        card.addEventListener('click', () => {
            chatInput.value = t(card.dataset.promptKey);
            chatInput.dispatchEvent(new Event('input'));
            chatInput.focus();
        });
    });
}
function renderWelcomeScreen() {
    const welcome = document.createElement('div');
    welcome.id = 'welcome-screen';
    welcome.className = 'workbench-welcome';
    welcome.innerHTML = welcomeTemplateHTML;
    welcome.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
    messagesDiv.appendChild(welcome);
    bindWelcomeSuggestions(welcome);
    applyBrandToDocument();
    syncChatHomeLayout();
    document.getElementById('chat-main').scrollTop = 0;
}
if (typeof MutationObserver !== 'undefined') new MutationObserver(syncChatHomeLayout).observe(messagesDiv, { childList: true });
syncChatHomeLayout();
const fileInput = document.getElementById('file-input');
const folderInput = document.getElementById('folder-input');
const attachBtn = document.getElementById('attach-btn');
const attachMenu = document.getElementById('attach-menu');
const attachFolderOption = document.getElementById('attach-folder-option');
const supportsDirectoryUpload = !!folderInput && 'webkitdirectory' in folderInput;

if (!supportsDirectoryUpload && attachFolderOption) {
    attachFolderOption.classList.add('hidden');
}

// Composer textarea sizing. The empty box is deliberately tall (a few lines of
// room, like other coding agents) and grows with the text up to a cap, after
// which it scrolls.
const COMPOSER_MIN_H = 52;
const COMPOSER_MAX_H = 220;

function autoResizeComposer() {
    chatInput.style.height = COMPOSER_MIN_H + 'px';
    const scrollH = chatInput.scrollHeight;
    chatInput.style.height = Math.max(COMPOSER_MIN_H, Math.min(scrollH, COMPOSER_MAX_H)) + 'px';
    chatInput.style.overflowY = scrollH > COMPOSER_MAX_H ? 'auto' : 'hidden';
}

/** Shrink the composer back to its resting height after the text is consumed. */
function resetComposerHeight() {
    chatInput.style.height = COMPOSER_MIN_H + 'px';
    chatInput.style.overflowY = 'hidden';
}

// ---------------- Mic button: in-page voice input via the configured ASR provider ----------------
(function setupMicButton() {
    const micBtn = document.getElementById('mic-btn');
    if (!micBtn) return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia ||
        typeof window.MediaRecorder === 'undefined') {
        micBtn.style.display = 'none';
        return;
    }

    let mediaRecorder = null;
    let stream = null;
    let chunks = [];
    let recording = false;

    // Use the custom CSS tooltip (data-tooltip) instead of the native title:
    // native title has a ~1.5s hover delay and is not i18n-aware.
    const setTip = (text) => {
        micBtn.setAttribute('data-tooltip', text);
        micBtn.removeAttribute('title');
    };

    const setIdle = () => {
        recording = false;
        micBtn.classList.remove('text-red-500', 'animate-pulse');
        micBtn.classList.add('text-slate-400');
        micBtn.querySelector('i').className = 'fas fa-microphone text-sm';
        setTip(t('mic_idle_title'));
    };
    const setRecording = () => {
        recording = true;
        micBtn.classList.remove('text-slate-400');
        micBtn.classList.add('text-red-500', 'animate-pulse');
        micBtn.querySelector('i').className = 'fas fa-stop text-sm';
        setTip(t('mic_recording_title'));
    };
    const setBusy = () => {
        micBtn.classList.remove('text-red-500', 'animate-pulse', 'text-slate-400');
        micBtn.classList.add('text-primary-500');
        micBtn.querySelector('i').className = 'fas fa-spinner fa-spin text-sm';
        setTip(t('mic_busy_title'));
    };

    const pickMimeType = () => {
        const candidates = [
            'audio/webm;codecs=opus',
            'audio/webm',
            'audio/ogg;codecs=opus',
            'audio/mp4',
        ];
        for (const m of candidates) {
            if (window.MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(m)) {
                return m;
            }
        }
        return '';
    };

    const stopStream = () => {
        if (stream) {
            stream.getTracks().forEach(t => t.stop());
            stream = null;
        }
    };

    let _micTipTimer = null;
    const flashError = (msg) => {
        console.warn('[mic]', msg);
        // Pop a small bubble above the mic so the user actually notices it.
        // The mic lives inside a relatively-positioned wrapper around the
        // textarea (see chat.html), so we hang the tip off that wrapper.
        const wrapper = micBtn.parentElement;
        if (!wrapper) return;
        let tip = wrapper.querySelector('.mic-tip');
        if (!tip) {
            tip = document.createElement('div');
            tip.className = 'mic-tip absolute right-1 bottom-full mb-2 px-2 py-1 rounded-md '
                + 'text-xs text-white bg-slate-800/90 dark:bg-slate-700/90 shadow-md '
                + 'pointer-events-none whitespace-nowrap z-10';
            wrapper.appendChild(tip);
        }
        tip.textContent = msg;
        tip.style.opacity = '1';
        if (_micTipTimer) clearTimeout(_micTipTimer);
        _micTipTimer = setTimeout(() => {
            tip.style.opacity = '0';
            tip.style.transition = 'opacity 200ms';
            setTimeout(() => tip.remove(), 250);
        }, 2000);
    };

    const upload = async (blob, ext) => {
        setBusy();
        const fd = new FormData();
        fd.append('file', blob, `recording.${ext}`);
        try {
            const resp = await fetch('/api/voice/asr', { method: 'POST', body: fd });
            const data = await resp.json();
            if (data.status === 'success' && data.text) {
                // Voice-message UX: drop the recording into the conversation
                // as a playable bubble with the caption underneath, then
                // dispatch the recognised text through the regular send path.
                sendVoiceMessage(data.text, data.audio_url);
            } else {
                flashError(data.message || t('mic_error'));
            }
        } catch (e) {
            flashError(t('mic_error') + ': ' + e.message);
        } finally {
            setIdle();
        }
    };

    const start = async () => {
        try {
            stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        } catch (e) {
            flashError(t('mic_permission_denied'));
            return;
        }
        chunks = [];
        const mimeType = pickMimeType();
        try {
            mediaRecorder = mimeType
                ? new MediaRecorder(stream, { mimeType })
                : new MediaRecorder(stream);
        } catch (e) {
            stopStream();
            flashError(t('mic_error') + ': ' + e.message);
            return;
        }
        mediaRecorder.ondataavailable = (ev) => {
            if (ev.data && ev.data.size > 0) chunks.push(ev.data);
        };
        mediaRecorder.onstop = () => {
            stopStream();
            const blob = new Blob(chunks, { type: mediaRecorder.mimeType || 'audio/webm' });
            // Map mime -> extension so the server picks the right file suffix.
            const mt = (mediaRecorder.mimeType || 'audio/webm').split(';')[0];
            const extMap = {
                'audio/webm': 'webm', 'audio/ogg': 'ogg',
                'audio/mp4': 'm4a',   'audio/mpeg': 'mp3',
            };
            const ext = extMap[mt] || 'webm';
            // 256 bytes ~ container header only, no actual audio. Anything
            // below that we treat as "tapped by mistake".
            if (blob.size < 256) {
                setIdle();
                flashError(t('mic_too_short'));
                return;
            }
            upload(blob, ext);
        };
        // timeslice=250ms: force the recorder to flush a chunk every 250ms.
        // Without it some browsers wait for stop() before producing any data,
        // which loses the audio on very short taps.
        mediaRecorder.start(250);
        recordStartedAt = Date.now();
        setRecording();
    };

    let recordStartedAt = 0;

    const stopWithMinDuration = () => {
        const elapsed = Date.now() - recordStartedAt;
        const minMs = 350;
        if (elapsed < minMs) {
            // Give the recorder a moment to capture at least one chunk
            // before we tell it to stop.
            setTimeout(() => stop(), minMs - elapsed);
        } else {
            stop();
        }
    };

    const stop = () => {
        if (mediaRecorder && mediaRecorder.state !== 'inactive') {
            mediaRecorder.stop();
        }
    };

    micBtn.addEventListener('click', () => {
        if (recording) {
            stopWithMinDuration();
        } else {
            start();
        }
    });

    setIdle();
})();

// ---------------- Optimize button: prompt optimization via AI ----------------
(function setupOptimizeButton() {
    const optBtn = document.getElementById('optimize-btn');
    if (!optBtn) return;

    let busy = false;

    // Use the custom CSS tooltip (data-tooltip) instead of the native title:
    // native title has a ~1.5s hover delay and is not i18n-aware.
    const setTip = (text) => {
        optBtn.setAttribute('data-tooltip', text);
        optBtn.removeAttribute('title');
    };

    const setIdle = () => {
        busy = false;
        optBtn.classList.remove('text-primary-500', 'animate-spin');
        optBtn.classList.add('text-slate-400');
        optBtn.querySelector('i').className = 'fas fa-magic text-[13px]';
        setTip(t('optimize_idle_title'));
        optBtn.style.pointerEvents = '';
    };
    const setBusy = () => {
        busy = true;
        optBtn.classList.remove('text-slate-400');
        optBtn.classList.add('text-primary-500');
        optBtn.querySelector('i').className = 'fas fa-spinner fa-spin text-[13px]';
        setTip(t('optimize_busy_title'));
        optBtn.style.pointerEvents = 'none';
    };

    // Shared flashError from mic setup — reuse its style by injecting into the same wrapper
    const flashError = (msg) => {
        console.warn('[optimize]', msg);
        const wrapper = optBtn.parentElement;
        if (!wrapper) return;
        let tip = wrapper.querySelector('.opt-tip');
        if (!tip) {
            tip = document.createElement('div');
            tip.className = 'opt-tip absolute right-9 bottom-full mb-2 px-2 py-1 rounded-md '
                + 'text-xs text-white bg-slate-800/90 dark:bg-slate-700/90 shadow-md '
                + 'pointer-events-none whitespace-nowrap z-10';
            wrapper.appendChild(tip);
        }
        tip.textContent = msg;
        tip.style.opacity = '1';
        tip.style.transition = '';
        clearTimeout(tip._timer);
        tip._timer = setTimeout(() => {
            tip.style.transition = 'opacity 200ms';
            tip.style.opacity = '0';
        }, 2500);
    };

    optBtn.addEventListener('click', async () => {
        if (busy) return;
        const raw = chatInput.value.trim();
        if (!raw) {
            flashError(t('optimize_empty'));
            return;
        }
        setBusy();
        try {
            // Gather optional context: last few message groups visible in the chat.
            // User and bot messages are distinguished by their group class.
            const contextMessages = [];
            const groups = messagesDiv.querySelectorAll('.user-message-group, .bot-message-group');
            const recentGroups = Array.from(groups).slice(-6);
            for (const g of recentGroups) {
                const role = g.classList.contains('user-message-group') ? 'user' : 'assistant';
                // Only read the main message content, not action buttons or timestamps.
                const contentEl = g.querySelector('.msg-content');
                const text = ((contentEl || g).textContent || '').trim().slice(0, 200);
                if (text) {
                    contextMessages.push({ role: role, content: text });
                }
            }

            const resp = await fetch('/api/prompt/optimize', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ input: raw, context_messages: contextMessages }),
            });
            const data = await resp.json();
            if (data.status === 'success' && data.optimized) {
                chatInput.value = data.optimized;
                chatInput.dispatchEvent(new Event('input', { bubbles: true }));
                chatInput.focus();
                // Place cursor at end
                chatInput.setSelectionRange(chatInput.value.length, chatInput.value.length);
            } else {
                flashError(data.message || t('optimize_error'));
            }
        } catch (e) {
            flashError(t('optimize_error') + ': ' + e.message);
        } finally {
            setIdle();
        }
    });

    setIdle();
})();


// Smart auto-scroll: pause when user scrolls up, resume when near bottom
let _autoScrollEnabled = true;
const _SCROLL_THRESHOLD = 80; // px from bottom to re-enable auto-scroll

messagesDiv.addEventListener('scroll', () => {
    const distFromBottom = messagesDiv.scrollHeight - messagesDiv.scrollTop - messagesDiv.clientHeight;
    _autoScrollEnabled = distFromBottom <= _SCROLL_THRESHOLD;
    _updateScrollToBottomBtn();
});

// Intercept internal navigation links in chat messages
messagesDiv.addEventListener('click', (e) => {
    // Code block copy button
    const codeCopyBtn = e.target.closest('.code-copy-btn');
    if (codeCopyBtn) {
        e.preventDefault();
        const wrapper = codeCopyBtn.closest('.code-block-wrapper');
        const codeEl = wrapper && wrapper.querySelector('pre code');
        if (codeEl) {
            const codeText = codeEl.textContent;
            copyToClipboard(codeText).then(() => {
                const icon = codeCopyBtn.querySelector('i');
                if (icon) { icon.className = 'fas fa-check'; setTimeout(() => { icon.className = 'fas fa-copy'; }, 1500); }
            });
        }
        return;
    }

    const copyBtn = e.target.closest('.copy-msg-btn');
    if (copyBtn) {
        e.preventDefault();
        const msgRoot = copyBtn.closest('.flex.gap-3');
        const answerEl = msgRoot && msgRoot.querySelector('.answer-content');
        const rawMd = answerEl && answerEl.dataset.rawMd;
        if (rawMd) {
            copyToClipboard(rawMd).then(() => {
                const icon = copyBtn.querySelector('i');
                if (icon) { icon.className = 'fas fa-check'; setTimeout(() => { icon.className = 'fas fa-copy'; }, 1500); }
            });
        }
        return;
    }

    // Edit user message
    const editBtn = e.target.closest('.edit-msg-btn');
    if (editBtn) {
        e.preventDefault();
        if (isCurrentSessionConversationActive()) return;
        const msgRoot = editBtn.closest('.user-message-group');
        if (msgRoot) editUserMessage(msgRoot);
        return;
    }

    // Regenerate bot response
    const regenerateBtn = e.target.closest('.regenerate-msg-btn');
    if (regenerateBtn) {
        e.preventDefault();
        const botMsgRoot = regenerateBtn.closest('.flex.gap-3');
        if (botMsgRoot) regenerateResponse(botMsgRoot);
        return;
    }

    // Delete message (user bubble only; bot bubbles intentionally lack a
    // delete button — removing only the bot reply would leave an orphan
    // user message that breaks LLM context alternation).
    const deleteBtn = e.target.closest('.delete-msg-btn');
    if (deleteBtn) {
        e.preventDefault();
        if (isCurrentSessionConversationActive()) return;
        const userMsgEl = deleteBtn.closest('.user-message-group');
        if (!userMsgEl) return;

        showConfirmModal(t('delete_message_title'), t('delete_message_confirm'), () => {
            // Find the next bot reply for this turn (skip non-message nodes).
            let botReplyEl = null;
            let sibling = userMsgEl.nextElementSibling;
            while (sibling) {
                if (sibling.classList && sibling.classList.contains('bot-message-group')) {
                    botReplyEl = sibling;
                    break;
                }
                sibling = sibling.nextElementSibling;
            }
            userMsgEl.remove();
            if (botReplyEl) botReplyEl.remove();

            const userSeq = userMsgEl.dataset.seq;
            if (userSeq) {
                fetch('/api/messages/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: sessionId, user_seq: parseInt(userSeq) })
                }).then(r => r.json()).then(data => {
                    if (data.status === 'success') console.log(`Deleted ${data.deleted} messages`);
                }).catch(err => console.error('Failed to delete:', err));
            }
        });
        return;
    }

    const a = e.target.closest('a');
    if (!a) return;
    const href = a.getAttribute('href') || '';
    if (href === '/memory/dreams') {
        e.preventDefault();
        navigateTo('memory');
        setTimeout(() => switchMemoryTab('dreams'), 50);
    } else if (href === '/memory/MEMORY.md') {
        e.preventDefault();
        navigateTo('memory');
        setTimeout(() => { switchMemoryTab('files'); openMemoryFile('MEMORY.md', 'memory'); }, 50);
    }
});
const attachmentPreview = document.getElementById('attachment-preview');

// Pending attachments: [{file_path, file_name, file_type, preview_url}]
// Items with _uploading=true are still in flight.
let pendingAttachments = [];
let uploadingCount = 0;

// Input history (like terminal arrow-key recall)
const inputHistory = [];
let historyIdx = -1;
let historySavedDraft = '';

// While an SSE stream is in flight, the send button morphs into a cancel
// button. Only one in-flight request is supported at a time.
let activeRequestId = null;
let sendBtnMode = 'send'; // 'send' | 'cancel'

function setSendBtnCancelMode(requestId) {
    activeRequestId = requestId;
    sendBtnMode = 'cancel';
    sendBtn.disabled = false;
    sendBtn.classList.add('send-btn-cancel');
    _setBtnTooltip(sendBtn, t('tip_cancel'));
    sendBtn.innerHTML = '<i class="fas fa-stop text-sm"></i>';
    updateSteerBtnState();
}

function resetSendBtnSendMode() {
    activeRequestId = null;
    sendBtnMode = 'send';
    sendBtn.classList.remove('send-btn-cancel');
    _setBtnTooltip(sendBtn, '');
    sendBtn.innerHTML = '<i class="fas fa-paper-plane text-sm"></i>';
    steerBtn.classList.add('hidden');
    steerBtn.classList.remove('flex');
    steerBtn.disabled = true;
    updateSendBtnState();
}

function updateSteerBtnState() {
    // Keep the steer button enabled whenever a task is running so users can
    // fire successive guidance. Empty-input is guarded in steerActiveTask,
    // avoiding a jarring disabled/not-allowed state right after each steer.
    const active = sendBtnMode === 'cancel' && !!activeRequestId;
    steerBtn.classList.toggle('hidden', !active);
    steerBtn.classList.toggle('flex', active);
    steerBtn.disabled = !active || uploadingCount > 0;
}

function steerActiveTask() {
    const instruction = chatInput.value.trim();
    if (!instruction || sendBtnMode !== 'cancel' || !activeRequestId) return;

    inputHistory.push(instruction);
    historyIdx = -1;
    historySavedDraft = '';
    addUserMessage(`↪ ${instruction}`, new Date());

    chatInput.value = '';
    resetComposerHeight();
    updateSteerBtnState();

    fetch('/message', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            session_id: sessionId,
            message: instruction,
            steer: true,
            stream: false,
            lang: currentLang,
        }),
    })
    .then(readMessageResponse)
    .then(data => {
        if (data.status === 'success' && data.inline_reply) {
            addBotMessage(data.inline_reply, new Date());
        } else {
            addMessageError(data);
        }
    })
    .catch(err => {
        console.warn('[steer] request failed', err);
        addBotMessage(t('error_send'), new Date());
    })
    .finally(updateSteerBtnState);
}

steerBtn.addEventListener('click', steerActiveTask);

function requestCancel() {
    const reqId = activeRequestId;
    if (!reqId) return;
    fetch('/cancel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ request_id: reqId, session_id: sessionId, lang: currentLang }),
    }).catch(err => {
        console.warn('[cancel] request failed', err);
    });
    // Optimistic UI lock so the click visibly registers before the SSE
    // "cancelled" event arrives.
    sendBtn.disabled = true;
    _setBtnTooltip(sendBtn, t('tip_cancelled'));
}

// Button click is the only path to Cancel. Pressing Enter still calls
// sendMessage() so users can submit "/cancel" as a regular slash command.
sendBtn.addEventListener('click', () => {
    if (sendBtnMode === 'cancel') {
        requestCancel();
    } else {
        sendMessage();
    }
});

function updateSendBtnState() {
    if (sendBtnMode === 'cancel') {
        // Self-heal a stuck Cancel button: if there's no live stream backing
        // the current request, the cancel state leaked (e.g. a stream ended
        // without resetting). Recover to Send so the input isn't blocked.
        if (!activeRequestId || !activeStreams[activeRequestId]) {
            resetSendBtnSendMode();
        } else {
            // Don't downgrade a genuinely active Cancel button on input edits.
            updateSteerBtnState();
            return;
        }
    }
    sendBtn.disabled = uploadingCount > 0 || (!chatInput.value.trim() && pendingAttachments.length === 0);
    updateSteerBtnState();
}

function renderAttachmentPreview() {
    if (pendingAttachments.length === 0) {
        attachmentPreview.classList.add('hidden');
        attachmentPreview.innerHTML = '';
        updateSendBtnState();
        return;
    }
    attachmentPreview.classList.remove('hidden');
    attachmentPreview.innerHTML = pendingAttachments.map((att, idx) => {
        if (att._uploading) {
            const suffix = att.file_type === 'directory' && att.file_count
                ? ` (${att.file_count})`
                : '';
            return `<div class="att-chip att-uploading" data-idx="${idx}">
                <i class="fas fa-spinner fa-spin"></i>
                <span class="att-name">${escapeHtml(att.file_name)}${suffix}</span>
            </div>`;
        }
        if (att.file_type === 'image') {
            return `<div class="att-thumb" data-idx="${idx}">
                <img src="${att.preview_url}" alt="${escapeHtml(att.file_name)}">
                <button class="att-remove" onclick="removeAttachment(${idx})">&times;</button>
            </div>`;
        }
        const icon = att.file_type === 'video'
            ? 'fa-film'
            : (att.file_type === 'directory' ? 'fa-folder-tree'
            : (att.is_dir ? 'fa-folder' : 'fa-file-alt'));
        const suffix = att.file_type === 'directory' && att.file_count
            ? ` (${att.file_count})`
            : '';
        return `<div class="att-chip" data-idx="${idx}">
            <i class="fas ${icon}"></i>
            <span class="att-name">${escapeHtml(att.file_name)}${suffix}</span>
            <button class="att-remove" onclick="removeAttachment(${idx})">&times;</button>
        </div>`;
    }).join('');
    updateSendBtnState();
}

function removeAttachment(idx) {
    if (pendingAttachments[idx]?._uploading) return;
    pendingAttachments.splice(idx, 1);
    renderAttachmentPreview();
}

function isAttachMenuVisible() {
    return attachMenu && !attachMenu.classList.contains('hidden');
}

function hideAttachMenu() {
    if (attachMenu) attachMenu.classList.add('hidden');
}

function toggleAttachMenu(event) {
    if (!attachMenu) return;
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }
    attachMenu.classList.toggle('hidden');
}

function triggerFileUpload() {
    hideAttachMenu();
    fileInput?.click();
}

function triggerFolderUpload() {
    if (!supportsDirectoryUpload) return;
    hideAttachMenu();
    folderInput?.click();
}

async function handleFileSelect(files) {
    if (!files || files.length === 0) return;
    const tasks = [];
    for (const file of files) {
        const placeholder = { file_name: file.name, file_type: 'file', _uploading: true };
        pendingAttachments.push(placeholder);
        uploadingCount++;
        renderAttachmentPreview();

        tasks.push((async () => {
            const formData = new FormData();
            formData.append('file', file);
            formData.append('session_id', sessionId);
            try {
                const resp = await fetch('/upload', { method: 'POST', body: formData });
                const data = await resp.json();
                if (data.status === 'success') {
                    placeholder.file_path = data.file_path;
                    placeholder.file_name = data.file_name;
                    placeholder.file_type = data.file_type;
                    placeholder.preview_url = data.preview_url;
                    delete placeholder._uploading;
                } else {
                    const i = pendingAttachments.indexOf(placeholder);
                    if (i !== -1) pendingAttachments.splice(i, 1);
                }
            } catch (e) {
                console.error('Upload failed:', e);
                const i = pendingAttachments.indexOf(placeholder);
                if (i !== -1) pendingAttachments.splice(i, 1);
            }
            uploadingCount--;
            renderAttachmentPreview();
        })());
    }
    await Promise.all(tasks);
}

function _makeUploadId() {
    return `dir_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
}

function _groupDirectoryFiles(files) {
    const groups = new Map();
    for (const file of Array.from(files || [])) {
        const relPath = file.webkitRelativePath || file.name;
        const parts = relPath.split('/').filter(Boolean);
        const rootName = parts[0] || file.name;
        if (!groups.has(rootName)) groups.set(rootName, []);
        groups.get(rootName).push({ file, relPath });
    }
    return groups;
}

async function handleFolderSelect(files) {
    if (!files || files.length === 0) return;
    const groups = _groupDirectoryFiles(files);
    const groupTasks = [];

    for (const [rootName, entries] of groups.entries()) {
        const placeholder = {
            file_name: rootName,
            file_type: 'directory',
            file_count: entries.length,
            _uploading: true,
        };
        pendingAttachments.push(placeholder);
        uploadingCount++;
        renderAttachmentPreview();

        const uploadId = _makeUploadId();
        groupTasks.push((async () => {
            try {
                const formData = new FormData();
                formData.append('session_id', sessionId);
                formData.append('upload_id', uploadId);
                for (const { file, relPath } of entries) {
                    formData.append('files', file);
                    formData.append('relative_paths', relPath);
                }

                const resp = await fetch('/upload', { method: 'POST', body: formData });
                const data = await resp.json();
                if (data.status !== 'success') {
                    throw new Error(data.message || 'Upload failed');
                }
                if (!data.root_path) {
                    throw new Error('Directory root path missing');
                }
                placeholder.file_path = data.root_path;
                placeholder.file_name = data.root_name || rootName;
                delete placeholder._uploading;
            } catch (e) {
                console.error('Directory upload failed:', e);
                const i = pendingAttachments.indexOf(placeholder);
                if (i !== -1) pendingAttachments.splice(i, 1);
            } finally {
                uploadingCount--;
            }
            renderAttachmentPreview();
        })());
    }

    await Promise.all(groupTasks);
}

fileInput.addEventListener('change', function() {
    handleFileSelect(this.files);
    this.value = '';
});

folderInput.addEventListener('change', function() {
    handleFolderSelect(this.files);
    this.value = '';
});

document.addEventListener('click', (e) => {
    if (!isAttachMenuVisible()) return;
    if (attachMenu.contains(e.target) || attachBtn.contains(e.target)) return;
    hideAttachMenu();
});

// =====================================================================
// Workspace selector (project picker above the input)
// =====================================================================
let _wsSelState = { current: null, recents: [], defaultWorkspace: '', projectsRoot: '' };

function _wsSelBtn() { return document.getElementById('workspace-selector-btn'); }
function _wsSelMenu() { return document.getElementById('workspace-selector-menu'); }

// Minimal self-dismissing toast for selector errors (no global toast exists).
function _wsToast(msg) {
    let el = document.getElementById('ws-sel-toast');
    if (!el) {
        el = document.createElement('div');
        el.id = 'ws-sel-toast';
        el.style.cssText = 'position:fixed;bottom:80px;left:50%;transform:translateX(-50%);' +
            'background:#1e293b;color:#fff;padding:8px 14px;border-radius:8px;font-size:13px;' +
            'z-index:9999;box-shadow:0 4px 16px rgba(0,0,0,0.2);opacity:0;transition:opacity .2s;';
        document.body.appendChild(el);
    }
    el.textContent = msg;
    el.style.opacity = '1';
    clearTimeout(el._t);
    el._t = setTimeout(() => { el.style.opacity = '0'; }, 2600);
}

// Refresh the selector state + label for the current session.
async function refreshWorkspaceSelector() {
    const label = document.getElementById('workspace-selector-label');
    const requestSession = sessionId;
    const requestAgent = activeAgentId;
    try {
        // Scope the request to the active Agent so the default-workspace hint
        // matches the file panel's real root in multi-Agent setups.
        let url = `/api/projects?session=${encodeURIComponent(sessionId)}`;
        const aid = (typeof activeAgentId !== 'undefined') ? activeAgentId : '';
        if (aid) url += `&agent=${encodeURIComponent(aid)}`;
        const res = await fetch(url);
        const data = await res.json();
        if (sessionId !== requestSession || activeAgentId !== requestAgent) return;
        if (data.status !== 'success') return;
        _wsSelState = {
            current: data.current || null,
            recents: data.recents || [],
            defaultWorkspace: data.default_workspace || '',
            projectsRoot: data.projects_root || '',
        };
        _wsSelUpdateLabel();
    } catch (e) { /* keep last label */ }
}

// Sync the selector button's label and hover tooltip with the current state.
// Called after every selection so the tooltip always shows the live full path.
function _wsSelUpdateLabel() {
    const label = document.getElementById('workspace-selector-label');
    if (label) {
        label.textContent = _wsSelState.current
            ? _wsSelState.current.name
            : t('ws_default_workspace');
    }
    const btn = _wsSelBtn();
    if (btn) {
        // The default workspace is the resting state, so it collapses to just
        // the folder icon (matching the desktop composer); a picked workspace
        // shows its name so the user knows they've moved off the default.
        btn.classList.toggle('composer-chip-icon-only', !_wsSelState.current);
        const full = _wsSelState.current
            ? _wsSelState.current.path
            : _wsSelState.defaultWorkspace;
        btn.setAttribute('data-tooltip', full || t('ws_sel_title'));
        btn.setAttribute('data-tooltip-pos', 'top');
        // Route through the body-level floating tooltip so the full path isn't
        // clipped/covered by the chat history above the input bar.
        btn.setAttribute('data-tip-float', '');
    }
}

function toggleWorkspaceSelector(event) {
    if (event) { event.preventDefault(); event.stopPropagation(); }
    const menu = _wsSelMenu();
    if (!menu) return;
    if (!menu.classList.contains('hidden')) {
        _wsSelHide();
        return;
    }
    _closeComposerMenus(menu);
    refreshWorkspaceSelector().then(renderWorkspaceSelectorMenu);
    menu.classList.remove('hidden');
    _wsSelBtn()?.classList.add('open');
}

function _wsSelHide() {
    const menu = _wsSelMenu();
    if (menu) menu.classList.add('hidden');
    _wsSelBtn()?.classList.remove('open');
}

function renderWorkspaceSelectorMenu() {
    const menu = _wsSelMenu();
    if (!menu) return;

    const parts = [];
    const isDefault = !_wsSelState.current;
    parts.push(`<div class="ws-sel-section-title">${escapeHtml(t('ws_sel_title'))}</div>`);
    // Default workspace: hovering shows the full ~/cow absolute path.
    parts.push(`
        <button class="ws-sel-item ${isDefault ? 'active' : ''}" onclick="selectWorkspaceProject(null)"
                data-tip-float data-tooltip="${escapeHtml(_wsSelState.defaultWorkspace || '')}" data-tooltip-pos="bottom">
            <i class="fas fa-house"></i>
            <span class="ws-sel-name">${escapeHtml(t('ws_default_workspace'))}</span>
            ${isDefault ? '<i class="fas fa-check ws-sel-check"></i>' : ''}
        </button>`);

    if ((_wsSelState.recents || []).length) {
        parts.push(`<div class="ws-sel-divider"></div>`);
        parts.push(`<div class="ws-sel-section-title">${escapeHtml(t('ws_sel_recents'))}</div>`);
        _wsSelState.recents.forEach(r => {
            const active = _wsSelState.current && _wsSelState.current.path === r.path;
            parts.push(`
                <button class="ws-sel-item ${active ? 'active' : ''}" onclick="selectWorkspaceProject('${_wsAttr(r.path)}')"
                        data-tip-float data-tooltip="${escapeHtml(r.path)}" data-tooltip-pos="bottom">
                    <i class="fas fa-folder"></i>
                    <span class="ws-sel-name">${escapeHtml(r.name)}</span>
                    ${active ? '<i class="fas fa-check ws-sel-check"></i>' : ''}
                </button>`);
        });
    }

    parts.push(`<div class="ws-sel-divider"></div>`);
    parts.push(`
        <button class="ws-sel-item" onclick="wsSelOpenProjectDialog()">
            <i class="fas fa-folder-open"></i>
            <span class="ws-sel-name">${escapeHtml(t('ws_sel_open'))}</span>
        </button>`);
    parts.push(`
        <button class="ws-sel-item" onclick="wsSelNewProjectDialog()">
            <i class="fas fa-folder-plus"></i>
            <span class="ws-sel-name">${escapeHtml(t('ws_sel_new'))}</span>
        </button>`);

    menu.innerHTML = parts.join('');
}

// Escape a path for safe embedding inside a single-quoted inline handler.
function _wsAttr(p) { return String(p || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'"); }

// ------- Folder picker modal (open an existing project) -------
let _fpCurrent = '';   // absolute path currently listed
let _fpBound = false;  // one-time listener binding guard

function wsSelOpenProjectDialog() {
    _wsSelHide();
    _fpBindOnce();
    const overlay = document.getElementById('folder-picker-overlay');
    document.getElementById('folder-picker-cancel').textContent = t('channels_cancel') || t('ws_sel_up');
    document.getElementById('folder-picker-open').textContent = t('ws_sel_open_here');
    document.getElementById('folder-picker-hint').textContent = t('ws_sel_dblclick_hint');
    overlay.classList.remove('hidden');
    _fpBrowse('');  // '' => backend starts at ~
}

function _fpBindOnce() {
    if (_fpBound) return;
    _fpBound = true;
    const overlay = document.getElementById('folder-picker-overlay');
    const close = () => overlay.classList.add('hidden');
    document.getElementById('folder-picker-cancel').addEventListener('click', close);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
    document.getElementById('folder-picker-open').addEventListener('click', async () => {
        if (!_fpCurrent) return;
        const ok = await _wsSelApply('/api/projects/select', { session: sessionId, project_dir: _fpCurrent });
        if (ok) close();
    });
}

// Virtual path (Windows) that lists logical drives; not a real openable dir.
const _FP_DRIVES = '__DRIVES__';

async function _fpBrowse(path) {
    const list = document.getElementById('folder-picker-list');
    list.innerHTML = `<div class="fp-empty"><i class="fas fa-spinner fa-spin"></i></div>`;
    try {
        const res = await fetch(`/api/projects/browse?path=${encodeURIComponent(path || '')}`);
        const data = await res.json();
        if (data.status !== 'success') { list.innerHTML = `<div class="fp-empty">${escapeHtml(data.message || 'error')}</div>`; return; }
        const isDrives = data.path === _FP_DRIVES;
        _fpCurrent = isDrives ? null : data.path;
        // Drives view is a selector, not a real directory: show a label and
        // disable "Open here" so the sentinel can't be picked as a project.
        const label = isDrives ? (t('ws_sel_drives') || 'This PC') : data.path;
        document.getElementById('folder-picker-path').textContent = label;
        document.getElementById('folder-picker-path').setAttribute('title', label);
        document.getElementById('folder-picker-open').disabled = isDrives;
        _fpRenderToolbar(data);
        _fpRenderList(data);
    } catch (e) {
        list.innerHTML = `<div class="fp-empty">${escapeHtml(String(e.message || e))}</div>`;
    }
}

function _fpRenderToolbar(data) {
    const bar = document.getElementById('folder-picker-toolbar');
    const upDisabled = !data.parent;
    bar.innerHTML = `
        <button class="fp-btn" ${upDisabled ? 'disabled' : ''} onclick="_fpBrowse('${_wsAttr(data.parent || '')}')" data-tooltip="${escapeHtml(t('ws_sel_up'))}" data-tooltip-pos="bottom">
            <i class="fas fa-arrow-up"></i>
        </button>
        <button class="fp-btn" onclick="_fpBrowse('~')" data-tooltip="~" data-tooltip-pos="bottom">
            <i class="fas fa-house"></i>
        </button>`;
}

function _fpRenderList(data) {
    const list = document.getElementById('folder-picker-list');
    const dirs = data.dirs || [];
    if (!dirs.length) {
        list.innerHTML = `<div class="fp-empty"><i class="fas fa-folder-open"></i><span>${escapeHtml(t('ws_sel_no_subdirs'))}</span></div>`;
        return;
    }
    list.innerHTML = dirs.map(d => `
        <div class="fp-row" ondblclick="_fpBrowse('${_wsAttr(d.path)}')" onclick="_fpSelectRow(this,'${_wsAttr(d.path)}')" title="${escapeHtml(d.path)}">
            <i class="fas fa-folder"></i>
            <span class="fp-name">${escapeHtml(d.name)}</span>
            <i class="fas fa-chevron-right fp-into" onclick="event.stopPropagation();_fpBrowse('${_wsAttr(d.path)}')"></i>
        </div>`).join('');
}

// Single click selects a child folder as the target (so you can open a folder
// without navigating into it); double click / chevron navigates inside.
function _fpSelectRow(el, path) {
    document.querySelectorAll('#folder-picker-list .fp-row.selected').forEach(r => r.classList.remove('selected'));
    el.classList.add('selected');
    _fpCurrent = path;
    // Picking a row (e.g. a drive in the drives view) is a valid target again.
    document.getElementById('folder-picker-open').disabled = false;
    document.getElementById('folder-picker-path').textContent = path;
}

// Create a new project by name (lands under the projects root), then open it.
function wsSelNewProjectDialog() {
    _wsSelHide();
    openKnowledgeDialog({
        title: t('ws_sel_new'),
        subtitle: (t('ws_sel_new_subtitle') || '').replace('{root}', _wsSelState.projectsRoot || ''),
        label: t('ws_sel_new_placeholder'),
        hint: t('ws_sel_new_hint'),
        icon: 'fa-folder-plus',
        value: '',
        validate: (v) => {
            v = (v || '').trim();
            if (!v) return t('ws_sel_name_required');
            if (v.includes('/') || v.includes('\\')) return t('ws_sel_name_no_slash');
            return '';
        },
        onSubmit: async (name) => {
            const ok = await _wsSelApply('/api/projects/create', { session: sessionId, name: name.trim() });
            return ok ? true : null;
        },
    });
}

// Shared apply path for select/create: POST, update label, then reveal the
// project in the right-hand file panel so the user sees they are "inside" it.
async function _wsSelApply(url, body) {
    try {
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (data.status !== 'success') { _wsToast(data.message || 'failed'); return false; }
        _wsSelState.current = data.current || null;
        if (Array.isArray(data.recents)) _wsSelState.recents = data.recents;
        if (data.default_workspace) _wsSelState.defaultWorkspace = data.default_workspace;
        _wsSelUpdateLabel();
        _wsSelRevealFiles();
        return true;
    } catch (e) { _wsToast(String(e.message || e)); return false; }
}

// Open (or refresh) the right-hand file panel on the Files tab so the newly
// selected project's directory is visible.
function _wsSelRevealFiles() {
    try {
        if (typeof openWorkspacePanel === 'function') {
            wsAutoOpenSuppressed = false;
            // Reset to the root of the new workspace before opening.
            if (typeof wsCurrentDir !== 'undefined') wsCurrentDir = '';
            openWorkspacePanel('files');
        }
        if (typeof refreshWorkspaceTree === 'function') refreshWorkspaceTree();
    } catch (e) { /* panel not present on this view */ }
}

// Kept for callers that select without a dialog (default / recents).
async function selectWorkspaceProject(projectDir) {
    _wsSelHide();
    await _wsSelApply('/api/projects/select', { session: sessionId, project_dir: projectDir });
}

document.addEventListener('click', (e) => {
    const menu = _wsSelMenu();
    const btn = _wsSelBtn();
    if (!menu || menu.classList.contains('hidden')) return;
    if (menu.contains(e.target) || (btn && btn.contains(e.target))) return;
    _wsSelHide();
});

// =====================================================================
// Per-session settings: permission mode and model
//
// Both live next to the workspace picker under the input, because all three
// answer the same question - what this conversation is allowed to do, and with
// what. Each falls back to the global setting until the user pins one here, so
// a session that was never touched keeps following Settings.
// =====================================================================

// Icons and i18n keys per mode. Ordered most-open first so the menu reads from
// "least restricted" downward, matching how the chip colours escalate.
const PERMISSION_META = {
    'full-access':     { icon: 'fa-lock-open',     key: 'perm_full_access' },
    'workspace-write': { icon: 'fa-shield-halved', key: 'perm_workspace_write' },
    'read-only':       { icon: 'fa-eye',           key: 'perm_read_only' },
};

// Last state from GET /api/sessions/<id>/settings; null until first fetch.
let _sessCfg = null;

function _permBtn() { return document.getElementById('permission-selector-btn'); }
function _permMenu() { return document.getElementById('permission-selector-menu'); }
function _modelBtn() { return document.getElementById('model-selector-btn'); }
function _modelMenu() { return document.getElementById('model-selector-menu'); }

function _permLabel(mode) { return t((PERMISSION_META[mode] || {}).key || 'perm_full_access'); }

/** Close every composer popover except `keep` (so one chip's menu replaces another's). */
function _closeComposerMenus(keep) {
    [[_wsSelMenu(), _wsSelBtn()], [_permMenu(), _permBtn()], [_modelMenu(), _modelBtn()]]
        .forEach(([menu, btn]) => {
            if (!menu || menu === keep) return;
            menu.classList.add('hidden');
            if (btn) btn.classList.remove('open');
        });
    const agentMenu = document.getElementById('composer-agent-menu');
    if (agentMenu && agentMenu !== keep) agentMenu.classList.add('hidden');
}

// Fetch this session's effective model + permission and repaint both chips.
async function refreshSessionSettings() {
    const requestSession = sessionId;
    const requestAgent = activeAgentId;
    try {
        const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/settings`);
        const data = await res.json();
        if (sessionId !== requestSession || activeAgentId !== requestAgent) return;
        if (data.status !== 'success') return;
        _sessCfg = { model: data.model, permission: data.permission, team: data.team };
    } catch (e) {
        // Keep whatever the chips already show rather than blanking them.
        return;
    }
    _renderPermissionChip();
    _renderModelChip();
    renderComposerIdentity();
}

function _renderPermissionChip() {
    const btn = _permBtn();
    if (!btn || !_sessCfg) return;
    const state = _sessCfg.permission || {};
    const mode = state.mode || 'full-access';
    const meta = PERMISSION_META[mode] || PERMISSION_META['full-access'];

    const label = document.getElementById('permission-selector-label');
    if (label) label.textContent = _permLabel(mode);
    const icon = document.getElementById('permission-selector-icon');
    if (icon) icon.className = `fas ${meta.icon}`;

    // One colour per mode, so an unrestricted session is visibly different from
    // a read-only one without having to read the label.
    btn.classList.remove('perm-read-only', 'perm-workspace-write', 'perm-full-access');
    btn.classList.add(`perm-${mode}`);

    const tip = t('perm_tip').replace('{name}', _permLabel(mode))
        + (state.source === 'global' ? ` · ${t('perm_follow_global')}` : '');
    btn.setAttribute('data-tooltip', tip);
    btn.setAttribute('data-tooltip-pos', 'top');
    btn.setAttribute('data-tip-float', '');
}

function _renderModelChip() {
    const btn = _modelBtn();
    if (!btn || !_sessCfg) return;
    // Once a conversation has more than one Agent there is no single model to
    // show: each answers on its own. Pinning one here would silently apply to
    // whoever happens to own the conversation.
    const shared = sharedConversation();
    btn.classList.toggle('hidden', shared);
    if (shared) {
        _modelMenu()?.classList.add('hidden');
        btn.classList.remove('open');
        return;
    }
    const state = _sessCfg.model || {};
    const model = state.model || '';

    const label = document.getElementById('model-selector-label');
    if (label) label.textContent = model || t('model_unset');

    const tip = t('model_tip').replace('{name}', model || t('model_unset'))
        + (state.source === 'global' ? ` · ${t('model_follow_global')}` : '')
        + (state.source === 'agent' ? ` · ${t('model_follow_agent')}` : '');
    btn.setAttribute('data-tooltip', tip);
    btn.setAttribute('data-tooltip-pos', 'top');
    btn.setAttribute('data-tip-float', '');
}

function togglePermissionSelector(event) {
    if (event) { event.preventDefault(); event.stopPropagation(); }
    const menu = _permMenu();
    if (!menu) return;
    if (!menu.classList.contains('hidden')) {
        _closeComposerMenus();
        return;
    }
    _closeComposerMenus(menu);
    const open = () => { renderPermissionMenu(); menu.classList.remove('hidden'); _permBtn()?.classList.add('open'); };
    if (_sessCfg) open(); else refreshSessionSettings().then(open);
}

function renderPermissionMenu() {
    const menu = _permMenu();
    if (!menu) return;
    const state = (_sessCfg && _sessCfg.permission) || {};
    const modes = state.modes && state.modes.length ? state.modes : Object.keys(PERMISSION_META);
    const current = state.mode || 'full-access';
    const isGlobal = state.source === 'global';

    const parts = [`<div class="composer-menu-title">${escapeHtml(t('perm_menu_title'))}</div>`];
    // Menu order follows PERMISSION_META, not the backend tuple, so the list
    // reads consistently even if the backend reorders its modes. "Follow global"
    // is intentionally not a row of its own: picking a mode simply pins it, and
    // clicking the already-active mode clears the pin (back to global) so the
    // behaviour is still reachable without cluttering the menu.
    Object.keys(PERMISSION_META).filter(m => modes.includes(m)).forEach(mode => {
        const meta = PERMISSION_META[mode];
        const active = mode === current;
        // When this mode is the active one AND it is pinned, clicking it clears
        // the pin; otherwise clicking pins this mode.
        const arg = (active && !isGlobal) ? 'null' : `'${mode}'`;
        parts.push(`
            <button class="composer-menu-item ${active ? 'active' : ''}" onclick="selectSessionPermission(${arg})">
                <i class="fas ${meta.icon}"></i>
                <span class="composer-menu-body">
                    <span class="composer-menu-name">${escapeHtml(t(meta.key))}</span>
                    <span class="composer-menu-desc">${escapeHtml(t(meta.key + '_desc'))}</span>
                </span>
                ${active ? '<i class="fas fa-check composer-menu-check"></i>' : ''}
            </button>`);
    });

    menu.innerHTML = parts.join('');
}

/** Pin this session's permission mode, or pass null to follow the global one. */
async function selectSessionPermission(mode) {
    _closeComposerMenus();
    await _applySessionSettings({ permission: mode });
}

// Insert an actionable hint after a tool card whose call was refused by the
// permission gate. Clicking it opens the permission selector under the input so
// the user can raise the mode without hunting for the chip.
function _appendPermissionDeniedHint(toolEl, mode) {
    if (!toolEl || !toolEl.parentElement) return;
    // Avoid stacking duplicate hints if the model retries the same blocked call.
    if (toolEl.nextElementSibling
        && toolEl.nextElementSibling.classList
        && toolEl.nextElementSibling.classList.contains('perm-denied-hint')) {
        return;
    }
    const label = _permLabel(mode || (_sessCfg && _sessCfg.permission && _sessCfg.permission.mode) || 'workspace-write');
    const hint = document.createElement('div');
    hint.className = 'perm-denied-hint';
    hint.innerHTML = `
        <i class="fas fa-shield-halved"></i>
        <span class="perm-denied-text">${escapeHtml(t('perm_denied_hint').replace('{name}', label))}</span>
        <button type="button" class="perm-denied-btn">${escapeHtml(t('perm_denied_action'))}</button>`;
    hint.querySelector('.perm-denied-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        const btn = _permBtn();
        if (btn) { btn.scrollIntoView({ block: 'nearest' }); }
        togglePermissionSelector();
    });
    toolEl.parentElement.insertBefore(hint, toolEl.nextElementSibling);
}

function toggleModelSelector(event) {
    if (event) { event.preventDefault(); event.stopPropagation(); }
    const menu = _modelMenu();
    if (!menu) return;
    if (!menu.classList.contains('hidden')) {
        _closeComposerMenus();
        return;
    }
    _closeComposerMenus(menu);
    const open = () => { renderModelMenu(); menu.classList.remove('hidden'); _modelBtn()?.classList.add('open'); };
    // Always re-fetch: the catalog depends on which providers have keys, which
    // may have changed in Settings since this page loaded.
    refreshSessionSettings().then(() => { if (_sessCfg) open(); });
}

function renderModelMenu() {
    const menu = _modelMenu();
    if (!menu) return;
    const state = (_sessCfg && _sessCfg.model) || {};
    const providers = state.providers || [];
    const pinned = state.source === 'session';

    // Which model is currently effective (pinned or inherited from global), so
    // the check mark shows on it even when the session follows the global model.
    const activeModel = state.model || (state.global && state.global.model) || '';
    const activeProvider = state.provider || (state.global && state.global.provider) || '';

    const parts = [`<div class="composer-menu-title">${escapeHtml(t('model_menu_title'))}</div>`];
    providers.forEach((p, idx) => {
        if (idx > 0) parts.push('<div class="composer-menu-divider"></div>');
        parts.push(`<div class="composer-menu-title">${escapeHtml(localizedLabel(p.label))}</div>`);
        (p.models || []).forEach(m => {
            const active = m === activeModel && p.id === activeProvider;
            // Clicking the already-pinned model clears the pin (back to global);
            // "follow global" is no longer a separate row.
            const arg = (active && pinned)
                ? 'null, null'
                : `'${_wsAttr(p.id)}','${_wsAttr(m)}'`;
            parts.push(`
                <button class="composer-menu-item ${active ? 'active' : ''}"
                        onclick="selectSessionModel(${arg})">
                    <i class="fas fa-microchip"></i>
                    <span class="composer-menu-body">
                        <span class="composer-menu-name">${escapeHtml(m)}</span>
                    </span>
                    ${active ? '<i class="fas fa-check composer-menu-check"></i>' : ''}
                </button>`);
        });
    });

    menu.innerHTML = parts.join('');
}

/** Pin a model for this session; pass nulls to follow the global model again. */
async function selectSessionModel(provider, model) {
    _closeComposerMenus();
    await _applySessionSettings({ provider: provider, model: model });
}

// Single writer for both chips: POST the change, then repaint from the state the
// backend echoes back so the UI can never disagree with what was stored.
async function _applySessionSettings(body) {
    try {
        const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/settings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (data.status !== 'success') { _wsToast(data.message || t('session_settings_failed')); return; }
        _sessCfg = { model: data.model, permission: data.permission };
        _renderPermissionChip();
        _renderModelChip();
    } catch (e) {
        _wsToast(t('session_settings_failed'));
    }
}

document.addEventListener('click', (e) => {
    [[_permMenu(), _permBtn()], [_modelMenu(), _modelBtn()]].forEach(([menu, btn]) => {
        if (!menu || menu.classList.contains('hidden')) return;
        if (menu.contains(e.target) || (btn && btn.contains(e.target))) return;
        menu.classList.add('hidden');
        if (btn) btn.classList.remove('open');
    });
});

// Drag-and-drop support on entire chat view
const chatView = document.getElementById('view-chat');
const chatInputArea = document.getElementById('composer-card') || chatInput.closest('.flex-shrink-0');

// Create drag overlay for visual feedback
let dragOverlay = document.getElementById('drag-overlay');
if (!dragOverlay) {
    dragOverlay = document.createElement('div');
    dragOverlay.id = 'drag-overlay';
    dragOverlay.className = 'drag-overlay hidden';
    dragOverlay.innerHTML = `
        <div class="drag-overlay-content">
            <i class="fas fa-cloud-arrow-up"></i>
            <p>Drop files here to upload</p>
        </div>
    `;
    chatView.appendChild(dragOverlay);
}

let dragCounter = 0;

function showDragOverlay() {
    dragOverlay.classList.remove('hidden');
    dragOverlay.classList.add('active');
}

function hideDragOverlay() {
    dragOverlay.classList.remove('active');
    dragOverlay.classList.add('hidden');
}

/** Clear every drag affordance at once, whatever the drag's outcome was. */
function resetDragState() {
    dragCounter = 0;
    hideDragOverlay();
    chatInputArea.classList.remove('drag-over');
    document.getElementById('chat-main')?.classList.remove('ws-drop-active');
}

chatView.addEventListener('dragenter', (e) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounter++;
    if (e.dataTransfer.types.includes('Files')) {
        showDragOverlay();
    }
});

chatView.addEventListener('dragover', (e) => {
    e.preventDefault();
    e.stopPropagation();
    // Only external file drags upload here; workspace drags have their own target.
    if (e.dataTransfer.types.includes('Files')) {
        chatInputArea.classList.add('drag-over');
    }
});

chatView.addEventListener('dragleave', (e) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounter--;
    if (dragCounter <= 0) {
        resetDragState();
    }
});

chatView.addEventListener('drop', (e) => {
    e.preventDefault();
    e.stopPropagation();
    resetDragState();
    if (e.dataTransfer.files.length) {
        handleFileSelect(e.dataTransfer.files);
    }
});

// A drag can end without ever reaching a drop target (Esc, or released over
// another element). Clear the highlight unconditionally so it can't stay stuck
// until the next reload.
document.addEventListener('dragend', resetDragState);
window.addEventListener('drop', resetDragState);

document.body.addEventListener('dragover', (e) => {
    if (e.dataTransfer.types.includes('Files')) {
        e.preventDefault();
    }
});

document.body.addEventListener('drop', (e) => {
    if (e.dataTransfer.types.includes('Files')) {
        e.preventDefault();
    }
});

// Paste image support
chatInput.addEventListener('paste', (e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const files = [];
    for (const item of items) {
        if (item.kind === 'file') {
            files.push(item.getAsFile());
        }
    }
    if (files.length) {
        e.preventDefault();
        handleFileSelect(files);
    }
});

chatInput.addEventListener('compositionstart', () => { isComposing = true; });
chatInput.addEventListener('compositionend', () => { setTimeout(() => { isComposing = false; }, 100); });

// ── Slash Command Menu ───────────────────────────────────────
// desc holds an i18n key, resolved via t() at render time so the menu follows
// the current UI language.
const SLASH_COMMANDS = [
    { cmd: '/help',                desc: 'slash_help' },
    { cmd: '/status',              desc: 'slash_status' },
    { cmd: '/context',             desc: 'slash_context' },
    { cmd: '/clear',               desc: 'slash_context_clear' },
    { cmd: '/compact',             desc: 'slash_compact' },
    { cmd: '/skill list',          desc: 'slash_skill_list' },
    { cmd: '/skill list --remote', desc: 'slash_skill_list_remote' },
    { cmd: '/skill search ',       desc: 'slash_skill_search' },
    { cmd: '/skill install ',      desc: 'slash_skill_install' },
    { cmd: '/skill uninstall ',    desc: 'slash_skill_uninstall' },
    { cmd: '/skill info ',         desc: 'slash_skill_info' },
    { cmd: '/skill enable ',       desc: 'slash_skill_enable' },
    { cmd: '/skill disable ',      desc: 'slash_skill_disable' },
    { cmd: '/memory dream ',       desc: 'slash_memory_dream' },
    { cmd: '/knowledge',           desc: 'slash_knowledge' },
    { cmd: '/knowledge list',      desc: 'slash_knowledge_list' },
    { cmd: '/knowledge on',        desc: 'slash_knowledge_on' },
    { cmd: '/knowledge off',       desc: 'slash_knowledge_off' },
    { cmd: '/场景',                 desc: 'slash_scenes' },
    { cmd: '/scenes',              desc: 'slash_scenes' },
    { cmd: '/config',              desc: 'slash_config' },
    { cmd: '/cancel',              desc: 'slash_cancel' },
    { cmd: '/steer ',              desc: 'slash_steer' },
    { cmd: '/logs',                desc: 'slash_logs' },
    { cmd: '/version',             desc: 'slash_version' },
];

const slashMenu = document.getElementById('slash-menu');
let slashActiveIdx = 0;
let slashFiltered = [];
let slashJustSelected = false;
let slashLastFilter = '';
let slashLastMouseX = -1;
let slashLastMouseY = -1;

function showSlashMenu(filter) {
    const q = filter.toLowerCase();
    if (q === slashLastFilter && !slashMenu.classList.contains('hidden')) return;
    slashLastFilter = q;

    const newFiltered = SLASH_COMMANDS.filter(c => c.cmd.toLowerCase().startsWith(q));
    if (newFiltered.length === 0) {
        hideSlashMenu();
        return;
    }

    const changed = newFiltered.length !== slashFiltered.length ||
        newFiltered.some((c, i) => c.cmd !== slashFiltered[i]?.cmd);
    slashFiltered = newFiltered;
    if (changed) slashActiveIdx = 0;
    slashActiveIdx = Math.min(slashActiveIdx, slashFiltered.length - 1);

    slashNavByKeyboard = true;
    renderSlashItems();
    slashMenu.classList.remove('hidden');
}

function hideSlashMenu() {
    slashMenu.classList.add('hidden');
    slashMenu.innerHTML = '';
    slashFiltered = [];
    slashActiveIdx = -1;
    slashLastFilter = '';
    slashNavByKeyboard = false;
    slashLastMouseX = -1;
    slashLastMouseY = -1;
}

function isSlashMenuVisible() {
    return !slashMenu.classList.contains('hidden') && slashFiltered.length > 0;
}

function renderSlashItems() {
    slashMenu.innerHTML =
        '<div class="slash-menu-header">Commands</div>' +
        slashFiltered.map((c, i) =>
            `<div class="slash-menu-item${i === slashActiveIdx ? ' active' : ''}" data-idx="${i}">` +
            `<span class="cmd">${escapeHtml(c.cmd)}</span>` +
            `<span class="desc">${escapeHtml(t(c.desc))}</span></div>`
        ).join('');

    const activeEl = slashMenu.querySelector('.slash-menu-item.active');
    if (activeEl) activeEl.scrollIntoView({ block: 'nearest' });
}

// Delegated events on the persistent slashMenu container (not destroyed by innerHTML)
// Use coordinate comparison to distinguish real mouse movement from DOM-rebuild phantom events.
slashMenu.addEventListener('mousemove', (e) => {
    if (e.clientX === slashLastMouseX && e.clientY === slashLastMouseY) return;
    slashLastMouseX = e.clientX;
    slashLastMouseY = e.clientY;
    if (!slashNavByKeyboard) return;
    slashNavByKeyboard = false;
    const item = e.target.closest('.slash-menu-item');
    if (!item) return;
    const idx = parseInt(item.dataset.idx);
    if (idx === slashActiveIdx) return;
    slashActiveIdx = idx;
    slashMenu.querySelectorAll('.slash-menu-item').forEach(el => {
        el.classList.toggle('active', parseInt(el.dataset.idx) === idx);
    });
});

slashMenu.addEventListener('mouseover', (e) => {
    if (slashNavByKeyboard) return;
    const item = e.target.closest('.slash-menu-item');
    if (!item) return;
    const idx = parseInt(item.dataset.idx);
    if (idx === slashActiveIdx) return;
    slashActiveIdx = idx;
    slashMenu.querySelectorAll('.slash-menu-item').forEach(el => {
        el.classList.toggle('active', parseInt(el.dataset.idx) === idx);
    });
});

slashMenu.addEventListener('mousedown', (e) => {
    const item = e.target.closest('.slash-menu-item');
    if (!item) return;
    e.preventDefault();
    selectSlashCommand(parseInt(item.dataset.idx));
});

function selectSlashCommand(idx) {
    if (idx < 0 || idx >= slashFiltered.length) return;
    const chosen = slashFiltered[idx].cmd;
    slashJustSelected = true;
    chatInput.value = chosen;
    chatInput.dispatchEvent(new Event('input'));
    hideSlashMenu();
    chatInput.focus();
    chatInput.selectionStart = chatInput.selectionEnd = chosen.length;
}

chatInput.addEventListener('input', function() {
    autoResizeComposer();
    updateSendBtnState();

    const val = this.value;
    if (slashJustSelected) {
        slashJustSelected = false;
    } else if (val.startsWith('/')) {
        showSlashMenu(val);
    } else {
        hideSlashMenu();
    }
});

chatInput.addEventListener('keydown', function(e) {
    if (e.keyCode === 229 || e.isComposing || isComposing) return;

    if (e.key === 'Escape' && isAttachMenuVisible()) {
        hideAttachMenu();
        return;
    }

    if (isSlashMenuVisible()) {
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            slashNavByKeyboard = true;
            slashActiveIdx = Math.min(slashActiveIdx + 1, slashFiltered.length - 1);
            renderSlashItems();
            return;
        }
        if (e.key === 'ArrowUp') {
            e.preventDefault();
            slashNavByKeyboard = true;
            slashActiveIdx = Math.max(slashActiveIdx - 1, 0);
            renderSlashItems();
            return;
        }
        if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey) {
            e.preventDefault();
            selectSlashCommand(slashActiveIdx);
            return;
        }
        if (e.key === 'Escape') {
            e.preventDefault();
            hideSlashMenu();
            return;
        }
        if (e.key === 'Tab') {
            e.preventDefault();
            selectSlashCommand(slashActiveIdx);
            return;
        }
    }

    // Arrow-key history recall (only when input is empty or already browsing history)
    if (e.key === 'ArrowUp' && inputHistory.length > 0 && !isSlashMenuVisible()) {
        const curVal = this.value.trim();
        const isSingleLine = !this.value.includes('\n');
        if (isSingleLine && (curVal === '' || historyIdx >= 0)) {
            e.preventDefault();
            if (historyIdx < 0) {
                historySavedDraft = this.value;
                historyIdx = inputHistory.length - 1;
            } else if (historyIdx > 0) {
                historyIdx--;
            }
            this.value = inputHistory[historyIdx];
            slashJustSelected = true;
            this.dispatchEvent(new Event('input'));
            hideSlashMenu();
            this.selectionStart = this.selectionEnd = this.value.length;
            return;
        }
    }
    if (e.key === 'ArrowDown' && historyIdx >= 0 && !isSlashMenuVisible()) {
        const isSingleLine = !this.value.includes('\n');
        if (isSingleLine) {
            e.preventDefault();
            if (historyIdx < inputHistory.length - 1) {
                historyIdx++;
                this.value = inputHistory[historyIdx];
            } else {
                historyIdx = -1;
                this.value = historySavedDraft;
                historySavedDraft = '';
            }
            slashJustSelected = true;
            this.dispatchEvent(new Event('input'));
            hideSlashMenu();
            this.selectionStart = this.selectionEnd = this.value.length;
            return;
        }
    }

    if ((e.ctrlKey || e.shiftKey) && e.key === 'Enter') {
        const start = this.selectionStart;
        const end = this.selectionEnd;
        this.value = this.value.substring(0, start) + '\n' + this.value.substring(end);
        this.selectionStart = this.selectionEnd = start + 1;
        this.dispatchEvent(new Event('input'));
        e.preventDefault();
    } else if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey) {
        sendMessage();
        e.preventDefault();
    }
});

chatInput.addEventListener('blur', () => {
    setTimeout(hideSlashMenu, 150);
});

bindWelcomeSuggestions(messagesDiv);

// Voice-message variant of sendMessage(): renders a playable audio bubble
// with the ASR caption, then dispatches the recognised text to /message
// through the same SSE/loading flow as a typed message.
function sendVoiceMessage(text, audioUrl) {
    text = (text || '').trim();
    if (!text) return;

    inputHistory.push(text);
    historyIdx = -1;
    historySavedDraft = '';

    const ws = document.getElementById('welcome-screen');
    const isFirstMessage = !!ws;
    if (ws) ws.remove();

    const titleInfo = isFirstMessage ? { sid: sessionId, userMsg: text } : null;
    const timestamp = new Date();
    addUserVoiceMessage(audioUrl, text, timestamp);
    const loadingEl = addLoadingIndicator();

    const body = {
        session_id: sessionId,
        message: text,
        stream: true,
        timestamp: timestamp.toISOString(),
        is_voice: true,
        lang: currentLang,
    };

    const MAX_RETRIES = 2;
    const RETRY_DELAY_MS = 1000;
    function postWithRetry(attempt) {
        fetch('/message', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        })
        .then(readMessageResponse)
        .then(data => {
            if (data.status === 'success') {
                rememberLiveSpeaker(data);
                setLoadingSpeaker(loadingEl, data.request_id);
                if (data.inline_reply) {
                    // Synchronous fast-path reply (e.g. /cancel); skip SSE.
                    loadingEl.remove();
                    addBotMessage(data.inline_reply, new Date());
                } else if (data.stream) {
                    setSendBtnCancelMode(data.request_id);
                    startSSE(data.request_id, loadingEl, timestamp, titleInfo);
                } else {
                    loadingContainers[data.request_id] = loadingEl;
                }
            } else {
                loadingEl.remove();
                addMessageError(data);
                resetSendBtnSendMode();
            }
        })
        .catch(err => {
            if (attempt < MAX_RETRIES) {
                setTimeout(() => postWithRetry(attempt + 1), RETRY_DELAY_MS * (attempt + 1));
                return;
            }
            loadingEl.remove();
            addBotMessage(t('error_send'), new Date());
        });
    }
    postWithRetry(0);
}

function addUserVoiceMessage(audioUrl, caption, timestamp) {
    const el = document.createElement('div');
    el.className = 'flex justify-end px-4 sm:px-6 py-3';
    // Voice-message bubble: compact voice pill on top, ASR caption beneath.
    // The bubble keeps the same primary tint as a normal user message so
    // it visually slots into the conversation flow.
    el.innerHTML = `
        <div class="max-w-[75%] sm:max-w-[60%]">
            <div class="bg-slate-100 dark:bg-white/10 text-slate-700 dark:text-slate-200 rounded-2xl px-3 py-2 msg-content user-bubble">
                <div class="user-voice-slot"></div>
                ${caption ? `<div class="text-xs mt-1.5 leading-snug text-slate-500 dark:text-slate-400 whitespace-pre-wrap break-words">${escapeHtml(caption)}</div>` : ''}
            </div>
            <div class="text-xs text-slate-400 dark:text-slate-500 mt-1.5 text-right">${formatTime(timestamp)}</div>
        </div>
    `;
    el.querySelector('.user-voice-slot').appendChild(renderVoicePill(audioUrl));
    messagesDiv.appendChild(el);
    _autoScrollEnabled = true;
    scrollChatToBottom(true);
}

// Clipboard helper with fallback for non-HTTPS environments
function copyToClipboard(text) {
    if (navigator.clipboard && window.isSecureContext) {
        return navigator.clipboard.writeText(text);
    }
    // Fallback for HTTP environments
    return new Promise((resolve, reject) => {
        const textArea = document.createElement('textarea');
        textArea.value = text;
        textArea.style.position = 'fixed';
        textArea.style.left = '-999999px';
        textArea.style.top = '-999999px';
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        try {
            document.execCommand('copy') ? resolve() : reject(new Error('Copy failed'));
        } catch (err) {
            reject(err);
        } finally {
            textArea.remove();
        }
    });
}

// Edit user message: extract content, remove this and subsequent messages, fill input
async function editUserMessage(msgEl) {
    if (isCurrentSessionConversationActive()) return;
    const rawContent = msgEl.dataset.rawContent;
    if (!rawContent) return;

    // Delete this message and ALL subsequent messages from database (cascade)
    // Must await to ensure delete completes before user sends a new message
    const userSeq = msgEl.dataset.seq;
    if (userSeq) {
        try {
            const resp = await fetch('/api/messages/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    session_id: sessionId, 
                    user_seq: parseInt(userSeq),
                    delete_user: true,
                    cascade: true
                })
            });
            const data = await resp.json();
            if (data.status === 'success') console.log(`Deleted ${data.deleted} old messages`);
        } catch (err) {
            console.error('Failed to delete old messages:', err);
        }
    }

    // Remove this message bubble and every later bubble that belongs to
    // this or a subsequent turn. We mirror the backend cascade contract:
    // anything with a data-seq >= current seq, plus any live SSE bubble
    // that is still being streamed (no seq yet) after this point.
    const currentSeqNum = userSeq ? parseInt(userSeq) : null;
    const messagesToRemove = [];
    let current = msgEl;
    while (current) {
        if (current.classList && (current.classList.contains('user-message-group') || current.classList.contains('bot-message-group'))) {
            const seqAttr = current.dataset.seq;
            if (seqAttr === undefined || seqAttr === '') {
                // Live message without a persisted seq yet — treat as later.
                messagesToRemove.push(current);
            } else if (currentSeqNum === null || parseInt(seqAttr) >= currentSeqNum) {
                messagesToRemove.push(current);
            }
        }
        current = current.nextElementSibling;
    }
    messagesToRemove.forEach(el => {
        if (el && el.parentNode) el.parentNode.removeChild(el);
    });

    // Fill input with the original content
    chatInput.value = rawContent;
    chatInput.dispatchEvent(new Event("input", { bubbles: true }));
    chatInput.focus();
    chatInput.selectionStart = chatInput.selectionEnd = chatInput.value.length;
    scrollChatToBottom();
}

// Regenerate bot response: find the preceding user message and resend it
async function regenerateResponse(botMsgEl) {
    let prevEl = botMsgEl.previousElementSibling;
    while (prevEl && !prevEl.classList.contains('user-message-group')) {
        prevEl = prevEl.previousElementSibling;
    }

    if (!prevEl) {
        console.warn('No preceding user message found');
        return;
    }

    const userContent = prevEl.dataset.rawContent;
    if (!userContent) {
        console.warn('No content in preceding user message');
        return;
    }

    // Delete both the old user message AND bot reply from database
    // (because /message will create a fresh user message + new bot reply)
    // Must await to ensure delete completes before /message is sent
    const userSeq = prevEl.dataset.seq;
    if (userSeq) {
        try {
            const resp = await fetch('/api/messages/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    session_id: sessionId, 
                    user_seq: parseInt(userSeq),
                    delete_user: true
                })
            });
            const data = await resp.json();
            if (data.status === 'success') console.log(`Deleted ${data.deleted} old messages`);
        } catch (err) {
            console.error('Failed to delete old messages:', err);
        }
    }

    // Remove both the old user message and bot message from DOM
    if (prevEl.parentNode) prevEl.parentNode.removeChild(prevEl);
    if (botMsgEl.parentNode) botMsgEl.parentNode.removeChild(botMsgEl);

    // Re-add the user message to DOM (so it appears before the loading indicator)
    addUserMessage(userContent, new Date());

    // Show loading indicator
    const loadingEl = addLoadingIndicator();

    // Resend the message
    const timestamp = new Date();
    const body = { session_id: sessionId, message: userContent, stream: true, timestamp: timestamp.toISOString(), lang: currentLang };
    const regenAddressed = addressedAgentId(userContent);
    if (regenAddressed) body.speaker_agent_id = regenAddressed;

    const MAX_RETRIES = 2;
    const RETRY_DELAY_MS = 1000;

    function postWithRetry(attempt) {
        fetch('/message', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        })
        .then(readMessageResponse)
        .then(data => {
            if (data.status === 'success') {
                rememberLiveSpeaker(data);
                setLoadingSpeaker(loadingEl, data.request_id);
                if (data.inline_reply) {
                    loadingEl.remove();
                    addBotMessage(data.inline_reply, new Date());
                } else if (data.stream) {
                    setSendBtnCancelMode(data.request_id);
                    startSSE(data.request_id, loadingEl, timestamp, null);
                } else {
                    loadingContainers[data.request_id] = loadingEl;
                }
            } else {
                loadingEl.remove();
                addMessageError(data);
                resetSendBtnSendMode();
            }
        })
        .catch(err => {
            if (err.name === 'AbortError') {
                loadingEl.remove();
                addBotMessage(t('error_timeout'), new Date());
                resetSendBtnSendMode();
                return;
            }
            if (attempt < MAX_RETRIES) {
                console.warn(`[regenerateResponse] attempt ${attempt + 1} failed, retrying...`, err);
                setTimeout(() => postWithRetry(attempt + 1), RETRY_DELAY_MS * (attempt + 1));
                return;
            }
            loadingEl.remove();
            addBotMessage(t('error_send'), new Date());
            resetSendBtnSendMode();
        });
    }

    postWithRetry(0);
}

// Preserve actionable server failures without retrying an HTTP rejection.
async function readMessageResponse(response) {
    let data;
    try { data = await response.json(); } catch (_) { data = null; }
    if (!data || typeof data !== 'object' || Array.isArray(data)) data = { status: 'error' };
    return { ...data, status: response.ok ? data.status : 'error', http_status: response.status };
}

function messageFailureText(data) {
    const code = String(data?.code || '').toLowerCase();
    const detail = typeof data?.message === 'string' ? data.message.trim() : '';
    const normalized = detail.toLowerCase();
    if (data?.http_status === 401 || code === 'unauthorized' || normalized === 'unauthorized') {
        return t('error_login_required');
    }
    if (code === 'missing_tenant' || code === 'conflicting_tenant'
            || normalized === 'tenant selection required' || normalized === 'conflicting tenant selection') {
        return t('error_tenant_required');
    }
    if (code === 'password_change_required' || normalized === 'password change required') {
        return t('error_password_required');
    }
    if (data?.http_status === 403 || code === 'forbidden' || normalized === 'forbidden') {
        return t('error_chat_forbidden');
    }
    return detail ? `${t('error_send')} ${detail.slice(0, 500)}` : t('error_send');
}

function addMessageError(data) {
    const text = messageFailureText(data);
    const el = createBotMessageEl('', new Date());
    const content = el.querySelector('.answer-content');
    // Error details are plain text, even when the server returns markup or a
    // Markdown link. They must never become executable HTML or clickable UI.
    content.textContent = text;
    content.dataset.rawMd = text;
    messagesDiv.appendChild(el);
    scrollChatToBottom();
}

function sendMessage() {
    if (_identityMode() === 'database' && !sessionStorage.getItem('cow_tenant_id')) {
        addMessageError({ code: 'missing_tenant' });
        return;
    }
    // Do NOT branch on sendBtnMode here: Enter should always send (so
    // typing "/cancel" submits normally). Cancel is wired only to the
    // send button's pointer click — see send-btn listener above.

    const text = chatInput.value.trim();
    if (!text && pendingAttachments.length === 0) return;

    // `/场景`（或 `/scenes`）打开场景选择器，不发送给后端。
    if ((text === '/场景' || text === '/scenes') && typeof window.showScenePicker === 'function') {
        chatInput.value = '';
        window.showScenePicker();
        return;
    }

    if (text) {
        inputHistory.push(text);
        historyIdx = -1;
        historySavedDraft = '';
    }

    const ws = document.getElementById('welcome-screen');
    const isFirstMessage = !!ws;
    if (ws) ws.remove();

    const titleInfo = (isFirstMessage && text) ? { sid: sessionId, userMsg: text } : null;
    syncTeamFromText(text);
    renderComposerIdentity();

    const timestamp = new Date();
    const attachments = [...pendingAttachments];
    addUserMessage(text, timestamp, attachments);

    const loadingEl = addLoadingIndicator();

    chatInput.value = '';
    resetComposerHeight();
    pendingAttachments = [];
    renderAttachmentPreview();
    sendBtn.disabled = true;
    if (typeof resetTurnArtifacts === 'function') resetTurnArtifacts();

    const body = { session_id: sessionId, message: text, stream: true, timestamp: timestamp.toISOString(), lang: currentLang };
    // Naming somebody hands them the turn. Sent explicitly because the composer
    // already knows who it wrote, and the server re-checks it either way.
    const addressed = addressedAgentId(text);
    if (addressed) body.speaker_agent_id = addressed;
    if (attachments.length > 0) {
        body.attachments = attachments.map(a => ({
            file_path: a.file_path,
            file_name: a.file_name,
            file_type: a.file_type,
            file_count: a.file_count,
        }));
    }

    const MAX_RETRIES = 2;
    const RETRY_DELAY_MS = 1000;

    function postWithRetry(attempt) {
        fetch('/message', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        })
        .then(readMessageResponse)
        .then(data => {
            if (data.status === 'success') {
                rememberLiveSpeaker(data);
                setLoadingSpeaker(loadingEl, data.request_id);
                if (data.inline_reply) {
                    // Channel handled synchronously (e.g. /cancel fast-path);
                    // render as a bot bubble and skip SSE entirely.
                    loadingEl.remove();
                    addBotMessage(data.inline_reply, new Date());
                } else if (data.stream) {
                    setSendBtnCancelMode(data.request_id);
                    startSSE(data.request_id, loadingEl, timestamp, titleInfo);
                } else {
                    loadingContainers[data.request_id] = loadingEl;
                }
            } else {
                loadingEl.remove();
                addMessageError(data);
                resetSendBtnSendMode();
            }
        })
        .catch(err => {
            if (err.name === 'AbortError') {
                loadingEl.remove();
                addBotMessage(t('error_timeout'), new Date());
                resetSendBtnSendMode();
                return;
            }
            if (attempt < MAX_RETRIES) {
                console.warn(`[sendMessage] attempt ${attempt + 1} failed, retrying...`, err);
                setTimeout(() => postWithRetry(attempt + 1), RETRY_DELAY_MS * (attempt + 1));
                return;
            }
            loadingEl.remove();
            addBotMessage(t('error_send'), new Date());
            resetSendBtnSendMode();
        });
    }

    postWithRetry(0);
}

function startSSE(requestId, loadingEl, timestamp, titleInfo, replayItems) {
    let botEl = null;
    let stepsEl = null;    // .agent-steps  (thinking summaries + tool indicators)
    let contentEl = null;  // .answer-content (final streaming answer)
    let mediaEl = null;    // .media-content (images & file attachments)
    let accumulatedText = '';
    const toolElements = new Map();
    let currentReasoningEl = null;  // live reasoning bubble
    let reasoningText = '';
    let reasoningStartTime = 0;
    let done = false;
    let mainDone = false;
    let completedBotSeq = null;
    let cancelled = false;
    let lastSeq = 0;

    // A stream can end while tools are still marked in-flight (cancel, dropped
    // connection). Settle them so nothing spins forever.
    function settlePendingTools() {
        toolElements.forEach(el => {
            el.classList.remove('tool-streaming');
            const icon = el.querySelector('.tool-icon');
            if (icon) icon.className = 'fas fa-minus text-slate-400 flex-shrink-0 tool-icon';
        });
        toolElements.clear();
    }

    // The session this stream belongs to. Sessions run in parallel: the user
    // may switch to another session while this one is still streaming. The
    // stream keeps running in the background (so the reply still completes and
    // persists); when foreign it does not touch the view but still records
    // every event into a buffer, so returning to the session can rebuild the
    // bubble by replaying the buffer and then resume live rendering.
    const ownerSession = sessionId;
    const ownerAgent = activeAgentId;
    const ownerKey = runtimeSessionKey(ownerSession, ownerAgent);
    const isActive = () => ownerSession === sessionId && ownerAgent === activeAgentId;
    sessionActiveRequest[ownerKey] = requestId;
    updateEditButtonsState();
    // Per-request event buffer used to rebuild the bubble on re-attach.
    const buffer = streamBuffers[requestId] || { items: [], timestamp };
    streamBuffers[requestId] = buffer;
    const clearOwnerRequest = () => {
        if (sessionActiveRequest[ownerKey] === requestId) {
            delete sessionActiveRequest[ownerKey];
            updateEditButtonsState();
        }
        delete streamBuffers[requestId];
    };

    const MAX_RECONNECTS = 10;
    const RECONNECT_BASE_MS = 1000;
    let reconnectCount = 0;

    function ensureBotEl() {
        if (botEl) return;
        if (loadingEl) { loadingEl.remove(); loadingEl = null; }
        botEl = document.createElement('div');
        botEl.className = 'flex gap-3 px-4 sm:px-6 py-3 bot-message-group';
        botEl.dataset.requestId = requestId;
        // Regenerate button starts hidden; it's revealed in the "done"
        // event handler once seq metadata arrives from the backend.
        // The streaming face is whoever is answering this request: the addressed
        // teammate if one was named, else the conversation's own Agent. Wrapped
        // in .bot-face so a later avatar change repaints it like any bubble.
        const speaker = liveSpeakerAgent(requestId);
        if (speaker && speaker.id) botEl.dataset.speakerAgent = speaker.id;
        // In a group the bubble is labelled with its author while it streams,
        // exactly as the replayed history shows it — a solo chat stays unlabelled.
        const speakerName = (sharedConversation() && speaker)
            ? `<div class="bot-speaker">${escapeHtml(speaker.name || speaker.id)}</div>`
            : '';
        botEl.innerHTML = `
            <span class="bot-face">${agentAvatarHTML(speaker, 32)}</span>
            <div class="min-w-0 flex-1 max-w-[85%]">
                ${speakerName}
                <div class="bg-white dark:bg-[#1A1A1A] border border-slate-200 dark:border-white/10 rounded-2xl px-4 py-3 text-sm leading-relaxed msg-content text-slate-700 dark:text-slate-200">
                    <div class="agent-steps"></div>
                    <div class="answer-content sse-streaming"></div>
                    <div class="media-content"></div>
                    <div class="bot-audio-slot"></div>
                </div>
                <div class="flex items-center gap-2 mt-1.5">
                    <span class="text-xs text-slate-400 dark:text-slate-500">${formatTime(timestamp)}</span>
                    <button class="copy-msg-btn text-xs text-slate-300 dark:text-slate-600 hover:text-slate-500 dark:hover:text-slate-400 transition-colors cursor-pointer" title="${currentLang === 'zh' ? '复制' : 'Copy'}" style="display:none">
                        <i class="fas fa-copy"></i>
                    </button>
                    <button class="speak-msg-btn text-xs text-slate-300 dark:text-slate-600 hover:text-slate-500 dark:hover:text-slate-400 transition-colors cursor-pointer" title="${t('speak_msg')}" style="display:none;">
                        <i class="fas fa-volume-up"></i>
                    </button>
                    <button class="regenerate-msg-btn text-xs text-slate-300 dark:text-slate-600 hover:text-primary-400 dark:hover:text-primary-400 transition-colors cursor-pointer" title="${t('regenerate_response')}" style="display:none;">
                        <i class="fas fa-rotate-right"></i>
                    </button>
                </div>
            </div>
        `;
        messagesDiv.appendChild(botEl);
        stepsEl = botEl.querySelector('.agent-steps');
        contentEl = botEl.querySelector('.answer-content');
        mediaEl = botEl.querySelector('.media-content');
    }

    // Holds the live EventSource so terminal events (done/voice_attach/error)
    // can close it. During replay there is no live connection (null).
    let currentEs = null;

    // Render one SSE event into the bubble. Used by the live handler and by
    // re-attach replay alike, so both paths produce identical UI.
    function processSSEItem(item) {
            if (item.type === 'reasoning') {
                ensureBotEl();
                reasoningText += item.content;
                if (!currentReasoningEl) {
                    reasoningStartTime = Date.now();
                    currentReasoningEl = document.createElement('div');
                    currentReasoningEl.className = 'agent-step agent-thinking-step';
                    // During streaming, use a <pre> with a single text node and
                    // append-only updates. This avoids re-parsing markdown and
                    // re-setting innerHTML on every chunk, which is what causes
                    // the page to crash on long chains-of-thought.
                    currentReasoningEl.innerHTML = `
                        <div class="thinking-header" onclick="this.parentElement.classList.toggle('expanded')">
                            <i class="fas fa-lightbulb text-amber-400 flex-shrink-0"></i>
                            <span class="thinking-summary">${t('thinking_in_progress')}</span>
                            <i class="fas fa-chevron-right thinking-chevron"></i>
                        </div>
                        <div class="thinking-full"><pre class="thinking-stream-pre"></pre></div>`;
                    stepsEl.appendChild(currentReasoningEl);
                    const preEl = currentReasoningEl.querySelector('.thinking-stream-pre');
                    preEl.appendChild(document.createTextNode(''));
                    currentReasoningEl._streamTextNode = preEl.firstChild;
                    currentReasoningEl._streamPendingText = '';
                    currentReasoningEl._streamRafScheduled = false;
                    currentReasoningEl._streamCharsRendered = 0;
                    currentReasoningEl._streamCapped = false;
                }
                // Hard cap: once REASONING_RENDER_CAP chars are in the DOM, stop
                // appending further deltas. The full text is still kept in
                // `reasoningText` for finalize-time head+tail rendering.
                if (!currentReasoningEl._streamCapped) {
                    currentReasoningEl._streamPendingText += item.content;
                    if (!currentReasoningEl._streamRafScheduled) {
                        currentReasoningEl._streamRafScheduled = true;
                        const elRef = currentReasoningEl;
                        requestAnimationFrame(() => {
                            elRef._streamRafScheduled = false;
                            if (!elRef.isConnected || !elRef._streamTextNode) return;
                            let pending = elRef._streamPendingText;
                            elRef._streamPendingText = '';
                            if (!pending) return;
                            const remaining = REASONING_RENDER_CAP - elRef._streamCharsRendered;
                            if (remaining <= 0) {
                                elRef._streamCapped = true;
                            } else {
                                if (pending.length > remaining) {
                                    pending = pending.slice(0, remaining);
                                    elRef._streamCapped = true;
                                }
                                elRef._streamTextNode.appendData(pending);
                                elRef._streamCharsRendered += pending.length;
                                if (elRef._streamCapped) {
                                    elRef._streamTextNode.appendData(
                                        '\n\n... [reasoning truncated for display] ...'
                                    );
                                }
                            }
                            scrollChatToBottom();
                        });
                    }
                }

            } else if (item.type === 'delta') {
                ensureBotEl();
                if (currentReasoningEl) {
                    finalizeThinking(currentReasoningEl, reasoningStartTime, reasoningText);
                    currentReasoningEl = null;
                    reasoningText = '';
                }
                accumulatedText += item.content;
                contentEl.innerHTML = renderMarkdown(accumulatedText);
                scrollChatToBottom();

            } else if (item.type === 'message_end') {
                if (item.has_tool_calls && accumulatedText.trim()) {
                    ensureBotEl();
                    const frozenEl = document.createElement('div');
                    frozenEl.className = 'agent-step agent-content-step';
                    frozenEl.innerHTML = `<div class="agent-content-body">${renderMarkdown(accumulatedText.trim())}</div>`;
                    stepsEl.appendChild(frozenEl);
                    accumulatedText = '';
                    contentEl.innerHTML = '';
                    scrollChatToBottom();
                }

            } else if (item.type === 'tool_start') {
                ensureBotEl();
                if (currentReasoningEl) {
                    finalizeThinking(currentReasoningEl, reasoningStartTime, reasoningText);
                    currentReasoningEl = null;
                    reasoningText = '';
                }
                accumulatedText = '';
                contentEl.innerHTML = '';

                // Add tool execution indicator (collapsible)
                const toolEl = document.createElement('div');
                toolEl.className = 'agent-step agent-tool-step tool-streaming';
                toolEl.dataset.progressReceived = 'false';
                const argsStr = formatToolArgs(item.arguments || {});
                toolEl.innerHTML = `
                    <div class="tool-header" onclick="this.parentElement.classList.toggle('expanded')">
                        <i class="fas fa-cog fa-spin text-primary-400 flex-shrink-0 tool-icon"></i>
                        <span class="tool-name">${item.tool}</span>
                        <span class="tool-substep-count"></span>
                        <i class="fas fa-chevron-right tool-chevron"></i>
                    </div>
                    <div class="tool-detail">
                        <div class="tool-detail-section">
                            <div class="tool-detail-label">Input</div>
                            <pre class="tool-detail-content">${argsStr}</pre>
                        </div>
                        <div class="tool-detail-section tool-substeps-section hidden">
                            <div class="tool-detail-label">Steps</div>
                            <div class="tool-substeps"></div>
                        </div>
                        <div class="tool-detail-section tool-output-section">
                            <div class="tool-detail-label tool-output-label">Output</div>
                            <pre class="tool-detail-content tool-live-output"></pre>
                            <div class="tool-display-output"></div>
                        </div>
                    </div>`;
                stepsEl.appendChild(toolEl);
                toolElements.set(item.tool_call_id, toolEl);

                scrollChatToBottom();

            } else if (item.type === 'tool_progress') {
                const toolEl = toolElements.get(item.tool_call_id);
                if (toolEl) {
                    if (toolEl.dataset.progressReceived !== 'true') {
                        toolEl.classList.add('expanded');
                        toolEl.dataset.progressReceived = 'true';
                    }
                    toolEl.querySelector('.tool-live-output').textContent = String(item.content || '');
                    scrollChatToBottom();
                }

            } else if (item.type === 'tool_end') {
                const toolEl = toolElements.get(item.tool_call_id);
                if (toolEl) {
                    const isError = item.status !== 'success';
                    const icon = toolEl.querySelector('.tool-icon');
                    icon.className = isError
                        ? 'fas fa-times text-red-400 flex-shrink-0 tool-icon'
                        : 'fas fa-check text-primary-400 flex-shrink-0 tool-icon';

                    // Show execution time
                    const nameEl = toolEl.querySelector('.tool-name');
                    if (item.execution_time !== undefined) {
                        nameEl.innerHTML += ` <span class="tool-time">${item.execution_time}s</span>`;
                    }

                    // Fill output section. A tool that wrote its outcome for a
                    // person (item.display) gets rendered as markdown; the raw
                    // result is what the model reads and stays hidden then.
                    const outputLabel = toolEl.querySelector('.tool-output-label');
                    const outputEl = toolEl.querySelector('.tool-live-output');
                    const displayEl = toolEl.querySelector('.tool-display-output');
                    if (outputLabel) outputLabel.textContent = isError ? 'Error' : 'Output';
                    if (displayEl && item.display) {
                        displayEl.innerHTML = renderMarkdown(String(item.display));
                        displayEl.classList.add('has-content');
                        if (outputEl) outputEl.textContent = '';
                    } else if (outputEl) {
                        outputEl.textContent = item.result ? String(item.result) : '';
                        outputEl.classList.toggle('tool-error-text', isError);
                    }

                    toolEl.classList.remove('tool-streaming');
                    // Tools collapse once they are done; their output is a
                    // trace. A tool that wrote something for a person to read
                    // stays open — the reader just waited for it.
                    toolEl.classList.toggle('expanded', !!item.display);
                    if (!item.result && !item.display) {
                        const outputSection = toolEl.querySelector('.tool-output-section');
                        if (outputSection) outputSection.remove();
                    }
                    if (isError) toolEl.classList.add('tool-failed');
                    // A permission refusal is not an ordinary failure: surface a
                    // one-click way to raise this session's permission instead of
                    // leaving the user to decode the model's error text.
                    if (item.permission_denied) {
                        _appendPermissionDeniedHint(toolEl, item.permission_mode);
                    }
                    toolElements.delete(item.tool_call_id);
                }

            } else if (item.type === 'subagent_step') {
                // A tool call made inside a sub agent, rendered under that sub
                // agent's card so its minutes of work are followable.
                renderSubagentStep(toolElements.get(item.card_id), item);
                scrollChatToBottom();

            } else if (item.type === 'image') {
                ensureBotEl();
                const imgEl = document.createElement('img');
                imgEl.src = item.content;
                imgEl.alt = 'screenshot';
                imgEl.style.cssText = 'max-width:600px;border-radius:8px;margin:8px 0;cursor:zoom-in;box-shadow:0 1px 4px rgba(0,0,0,0.1);';
                imgEl.onclick = () => _openImageLightbox(imgEl.src);
                mediaEl.appendChild(imgEl);
                scrollChatToBottom();

            } else if (item.type === 'text') {
                // Intermediate text sent before media items; display it but keep SSE open.
                ensureBotEl();
                contentEl.classList.remove('sse-streaming');
                const textContent = item.content || accumulatedText;
                if (textContent) contentEl.innerHTML = renderMarkdown(textContent);
                applyHighlighting(botEl);
                scrollChatToBottom();

            } else if (item.type === 'video') {
                ensureBotEl();
                const wrapper = document.createElement('div');
                wrapper.innerHTML = _buildVideoHtml(item.content);
                mediaEl.appendChild(wrapper.firstElementChild || wrapper);
                scrollChatToBottom();

            } else if (item.type === 'file') {
                ensureBotEl();
                const fileName = item.file_name || item.content.split('/').pop();
                const fileEl = document.createElement('a');
                fileEl.href = item.content;
                fileEl.download = fileName;
                fileEl.target = '_blank';
                fileEl.className = 'file-attachment';
                fileEl.style.cssText = 'display:inline-flex;align-items:center;gap:6px;padding:8px 14px;margin:8px 0;border-radius:8px;background:var(--bg-secondary,#f3f4f6);color:var(--text-primary,#374151);text-decoration:none;font-size:14px;border:1px solid var(--border-color,#e5e7eb);';
                fileEl.innerHTML = `<i class="fas fa-file-download" style="color:#6b7280;"></i> ${fileName}`;
                mediaEl.appendChild(fileEl);
                scrollChatToBottom();

            } else if (item.type === 'artifact') {
                // A user-facing file the agent wrote; render a card and let the
                // workspace panel decide whether to auto-open it (workspace.js).
                ensureBotEl();
                if (typeof appendArtifactCard === 'function') {
                    appendArtifactCard(mediaEl, item);
                }
                scrollChatToBottom();

            } else if (item.type === 'phase') {
                // Coarse progress (e.g. cow install-browser); must not close SSE (unlike "done")
                ensureBotEl();
                const wrap = document.createElement('div');
                wrap.className = 'text-xs sm:text-sm text-slate-600 dark:text-slate-400 border-l-2 border-primary-400 pl-2 py-1 my-0.5';
                wrap.textContent = String(item.content || '');
                stepsEl.appendChild(wrap);
                scrollChatToBottom();

            } else if (item.type === 'cancelled') {
                // Agent acknowledged the stop; mark the bubble. A trailing
                // "done" still arrives with the partial answer.
                cancelled = true;
                ensureBotEl();
                if (currentReasoningEl) {
                    finalizeThinking(currentReasoningEl, reasoningStartTime, reasoningText);
                    currentReasoningEl = null;
                    reasoningText = '';
                }
                if (!botEl.querySelector('.agent-cancelled-tag')) {
                    const tag = document.createElement('div');
                    tag.className = 'agent-cancelled-tag text-xs text-amber-600 dark:text-amber-400 mt-1';
                    tag.textContent = (currentLang === 'zh') ? '已中止' : 'Cancelled';
                    stepsEl.appendChild(tag);
                }
                resetSendBtnSendMode();

            } else if (item.type === 'done') {
                // The answer is persisted, but async attachments may still
                // follow. Only stream_end closes the request lifecycle.
                mainDone = true;
                if (item.bot_seq !== undefined && item.bot_seq !== null) {
                    completedBotSeq = item.bot_seq;
                }
                settlePendingTools();
                resetSendBtnSendMode();

                const finalTextRaw = item.content || accumulatedText;
                const finalText = localizeCancelMarker(finalTextRaw);

                if (!botEl && finalText) {
                    if (loadingEl) { loadingEl.remove(); loadingEl = null; }
                    addBotMessage(finalText, new Date((item.timestamp || Date.now() / 1000) * 1000), requestId);
                } else if (botEl) {
                    contentEl.classList.remove('sse-streaming');
                    if (finalText) contentEl.innerHTML = renderMarkdown(finalText);
                    contentEl.dataset.rawMd = finalTextRaw || '';
                    const copyBtn = botEl.querySelector('.copy-msg-btn');
                    if (copyBtn && finalText) copyBtn.style.display = '';
                    applyHighlighting(botEl);
                }

                // Backfill seq metadata so edit/regenerate buttons can call
                // the delete API without a page refresh. Backend includes
                // user_seq / bot_seq on the done event after persistence.
                const targetBotEl = botEl || (requestId ? messagesDiv.querySelector(`[data-request-id="${requestId}"]`) : null);
                if (targetBotEl) {
                    if (item.bot_seq !== undefined && item.bot_seq !== null) {
                        targetBotEl.dataset.seq = item.bot_seq;
                    }
                    // Reveal regenerate button now that the seq is wired up.
                    const regenBtn = targetBotEl.querySelector('.regenerate-msg-btn');
                    if (regenBtn) regenBtn.style.display = '';
                    if (item.user_seq !== undefined && item.user_seq !== null) {
                        // Locate the preceding user bubble for this turn.
                        let prev = targetBotEl.previousElementSibling;
                        while (prev && !prev.classList.contains('user-message-group')) {
                            prev = prev.previousElementSibling;
                        }
                        if (prev && !prev.dataset.seq) {
                            prev.dataset.seq = item.user_seq;
                        }
                    }
                }
                renderBotSpeakerButton(botEl, finalText);
                scrollChatToBottom();

                if (typeof maybeAutoOpenArtifact === 'function') maybeAutoOpenArtifact();

                if (titleInfo) {
                    generateSessionTitle(titleInfo.sid, titleInfo.userMsg, '');
                    titleInfo = null;
                } else {
                    // A session's title may have been regenerated/re-ordered or its
                    // activity updated. Refresh the visible history list, otherwise
                    // mark it dirty so the next visit re-reads the latest state.
                    if (_historyVisible) loadSessionList();
                    else _historyDirty = true;
                }

            } else if (item.type === 'voice_attach') {
                // TTS finished — attach a playable audio element to the
                // persisted bot bubble. If history is still loading after a
                // session switch, keep the attachment until that bubble exists.
                if (item.url && completedBotSeq !== null) {
                    rememberPendingVoiceAttachment(
                        ownerSession, completedBotSeq, item.url
                    );
                    flushPendingVoiceAttachments(ownerSession, true);
                }

            } else if (item.type === 'stream_end') {
                done = true;
                if (currentEs) { currentEs.close(); }
                delete activeStreams[requestId];
                clearOwnerRequest();

            } else if (item.type === 'resync_required') {
                done = true;
                settlePendingTools();
                if (currentEs) { currentEs.close(); }
                delete activeStreams[requestId];
                clearOwnerRequest();
                resetSendBtnSendMode();
                if (isActive()) {
                    messagesDiv.innerHTML = '';
                    historyPage = 0;
                    historyHasMore = false;
                    historyLoading = false;
                    loadHistory(1);
                }

            } else if (item.type === 'error') {
                done = true;
                settlePendingTools();
                if (currentEs) { currentEs.close(); }
                delete activeStreams[requestId];
                clearOwnerRequest();
                if (loadingEl) { loadingEl.remove(); loadingEl = null; }
                // After a stop the stream is expected to end; the bubble is
                // already tagged "已中止", so don't stack a failure on top.
                if (!cancelled) addBotMessage(t('error_send'), new Date());
                resetSendBtnSendMode();
            }
    }

    function connect() {
        const es = new EventSource(
            `/stream?request_id=${encodeURIComponent(requestId)}`
            + `&after_seq=${lastSeq}`
        );
        currentEs = es;
        activeStreams[requestId] = es;

        es.onmessage = function(e) {
            let item;
            try { item = JSON.parse(e.data); } catch (_) { return; }

            const seq = Number(item.seq || 0);
            if (seq && seq <= lastSeq) return;

            // Successful data received, reset reconnect counter
            reconnectCount = 0;

            // Record every event for re-attach replay (capped to avoid
            // unbounded growth on very long streams).
            if (item.type === 'tool_progress' && item.tool_call_id) {
                const previousIndex = buffer.items.findIndex(
                    buffered => buffered.type === 'tool_progress'
                        && buffered.tool_call_id === item.tool_call_id
                );
                if (previousIndex >= 0) buffer.items.splice(previousIndex, 1);
            }
            if (buffer.items.length < 5000) buffer.items.push(item);
            if (seq) lastSeq = seq;

            // done is persisted before it is published. Remember that state
            // even while this session is in the background, where rendering
            // is intentionally skipped. Notify for both foreground and
            // background sessions, before the render guard below.
            if (item.type === 'done') {
                mainDone = true;
                if (item.bot_seq !== undefined && item.bot_seq !== null) {
                    completedBotSeq = item.bot_seq;
                }
                notifyTaskFinished(ownerSession, 'done', item.content);
            } else if (item.type === 'error') {
                if (!cancelled) notifyTaskFinished(ownerSession, 'error', '');
            } else if (
                item.type === 'voice_attach'
                && item.url
                && completedBotSeq !== null
            ) {
                // Background sessions skip rendering below. Preserve their
                // attachment so loadHistory can mount it when the user returns.
                rememberPendingVoiceAttachment(
                    ownerSession, completedBotSeq, item.url
                );
            }

            // Background session: keep the stream alive so the reply finishes
            // and persists, but skip rendering into the now-foreign view. The
            // buffer above still grows so returning to the session can rebuild
            // the bubble and resume live rendering.
            if (ownerSession !== sessionId) {
                if (item.type === 'stream_end' || item.type === 'error' || item.type === 'resync_required') {
                    done = true;
                    es.close();
                    delete activeStreams[requestId];
                    clearOwnerRequest();
                }
                return;
            }

            processSSEItem(item);
        };

        es.onerror = function() {
            es.close();
            delete activeStreams[requestId];

            if (done) {
                // stream_end or an unrecoverable event already closed it.
                return;
            }

            if (cancelled && !mainDone) {
                // The user stopped the run, so the stream ending here is the
                // expected outcome. Reconnecting would only land on a queue
                // the backend has already reclaimed.
                settlePendingTools();
                clearOwnerRequest();
                if (loadingEl) { loadingEl.remove(); loadingEl = null; }
                if (contentEl) contentEl.classList.remove('sse-streaming');
                resetSendBtnSendMode();
                return;
            }

            if (currentReasoningEl) {
                finalizeThinking(currentReasoningEl, reasoningStartTime, reasoningText);
                currentReasoningEl = null;
                reasoningText = '';
            }

            if (reconnectCount < MAX_RECONNECTS) {
                reconnectCount++;
                const delay = Math.min(RECONNECT_BASE_MS * reconnectCount, 5000);
                console.warn(`[SSE] connection lost for ${requestId}, reconnecting in ${delay}ms (attempt ${reconnectCount}/${MAX_RECONNECTS})`);
                setTimeout(connect, delay);
                return;
            }

            // Exhausted retries. Only surface the failure in the owning view —
            // a background session must not mutate the currently shown chat.
            clearOwnerRequest();
            settlePendingTools();
            if (!isActive()) return;
            if (loadingEl) { loadingEl.remove(); loadingEl = null; }
            if (!botEl) {
                addBotMessage(t('error_send'), new Date());
            } else if (accumulatedText) {
                contentEl.classList.remove('sse-streaming');
                contentEl.innerHTML = renderMarkdown(accumulatedText);
                applyHighlighting(botEl);
            }
            resetSendBtnSendMode();
        };
    }

    // Re-attach replay: rebuild the bubble from buffered events (snapshot,
    // not animated) before connecting for the live tail. `processSSEItem`
    // is the same renderer used by the live onmessage handler, so the
    // snapshot matches exactly what live rendering would have produced.
    if (replayItems && replayItems.length) {
        for (const item of replayItems) {
            const seq = Number(item.seq || 0);
            if (seq > lastSeq) lastSeq = seq;
            try { processSSEItem(item); } catch (_) {}
            if (item.type === 'stream_end' || item.type === 'error' || item.type === 'resync_required') {
                done = true;
            }
        }
        // If the buffered stream already finished, don't reconnect — the
        // reply is complete and persisted; show its final state and stop.
        if (done) {
            clearOwnerRequest();
            resetSendBtnSendMode();
            scrollChatToBottom(true);
            return;
        }
    }

    connect();
}

function startPolling() {
    const gen = ++pollGeneration;
    isPolling = true;
    let pollInFlight = false;

    function poll() {
        if (gen !== pollGeneration) return;
        if (pollInFlight) return;
        // Keep polling while hidden: push messages are exactly what the
        // notification below should deliver to a background tab.
        pollInFlight = true;
        fetch('/poll', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sessionId })
        })
        .then(r => r.json())
        .then(data => {
            pollInFlight = false;
            if (gen !== pollGeneration) return;
            if (data.status === 'success' && data.has_content) {
                const rid = data.request_id;
                if (loadingContainers[rid]) {
                    loadingContainers[rid].remove();
                    delete loadingContainers[rid];
                }
                // Skip if this reply is already on screen. Happens when a reply
                // arrives via both the SSE stream and the poll queue (e.g. the
                // user switched away mid-run, leaving the queued reply to be
                // re-fetched on return) — render it only once.
                const already = rid && messagesDiv.querySelector(
                    `[data-request-id="${rid}"]`
                );
                if (!already) {
                    const welcomeScreen = document.getElementById('welcome-screen');
                    if (welcomeScreen) welcomeScreen.remove();
                    addBotMessage(data.content, new Date(data.timestamp * 1000), rid);
                    scrollChatToBottom();
                    // Pushed message (scheduler result, missed reply): show the
                    // content itself, matching the desktop push notification.
                    showTaskNotification(
                        sessionTitleOf(sessionId) || PRODUCT_NAME,
                        firstLineSnippet(data.content),
                        sessionId
                    );
                }
            }
            const delay = (data.status === 'success' && data.has_content) ? 5000 : 10000;
            setTimeout(poll, delay);
        })
        .catch(() => { pollInFlight = false; setTimeout(poll, 10000); });
    }
    poll();
}

// Attachment markers the backend appends to the prompt, keyed by the label it
// emitted (see the workspace_ref branch in web_channel.post_message). History
// only persists the prompt text, so this is the only way back to a chip.
const ATTACHMENT_MARKER_TYPES = {
    '工作空间文件': 'workspace_ref', '工作空间檔案': 'workspace_ref', 'Workspace file': 'workspace_ref',
    '工作空间目录': 'workspace_dir', '工作空间目錄': 'workspace_dir', 'Workspace directory': 'workspace_dir',
    '图片': 'image', '圖片': 'image', 'Image': 'image',
    '视频': 'video', '影片': 'video', 'Video': 'video',
    '目录': 'directory', '目錄': 'directory', 'Directory': 'directory',
    '文件': 'file', '檔案': 'file', 'File': 'file',
};

/**
 * Split trailing `[label: path]` lines off a persisted user message.
 * Returns the remaining text plus the attachments they describe.
 */
function parseAttachmentMarkers(content) {
    const lines = (content || '').split('\n');
    const found = [];
    while (lines.length) {
        const line = lines[lines.length - 1].trim();
        if (!line) { lines.pop(); continue; }
        const m = line.match(/^\[([^\]:]+):\s*(.+)\]$/);
        const type = m && ATTACHMENT_MARKER_TYPES[m[1].trim()];
        if (!type) break;
        found.unshift({ type, path: m[2].trim() });
        lines.pop();
    }
    if (!found.length) return { text: content, attachments: null };
    return {
        text: lines.join('\n').trimEnd(),
        attachments: found.map(f => ({
            file_path: f.path,
            file_name: f.path.split(/[\\/]/).filter(Boolean).pop() || f.path,
            file_type: f.type === 'workspace_dir' ? 'workspace_ref' : f.type,
            is_dir: f.type === 'workspace_dir' || f.type === 'directory',
        })),
    };
}

function createUserMessageEl(content, timestamp, attachments) {
    const el = document.createElement('div');
    el.className = 'flex justify-end px-4 sm:px-6 py-3 user-message-group';

    // Replaying history: recover the chips from the markers left in the text.
    if (!attachments) {
        const parsed = parseAttachmentMarkers(content);
        if (parsed.attachments) {
            attachments = parsed.attachments;
            content = parsed.text;
        }
    }

    let attachHtml = '';
    if (attachments && attachments.length > 0) {
        const items = attachments.map(a => {
            if (a.file_type === 'image') {
                // History replay recovers attachments from prompt markers, which
                // carry only the local file_path — route it through /api/file.
                const src = (a.preview_url || _toWebUrl(a.file_path || '')).replace(/"/g, '&quot;');
                return `<img src="${src}" alt="${escapeHtml(a.file_name)}" class="user-msg-image" onclick="_openImageLightbox(this.src)">`;
            }
            const icon = a.file_type === 'video'
                ? 'fa-film'
                : (a.file_type === 'directory' ? 'fa-folder-tree'
                : (a.is_dir ? 'fa-folder' : 'fa-file-alt'));
            const suffix = a.file_type === 'directory' && a.file_count
                ? ` (${a.file_count})`
                : '';
            // Workspace references stay openable in the preview panel.
            const openable = a.file_type === 'workspace_ref'
                ? ` data-ws-open="${escapeHtml(a.file_path)}" title="${escapeHtml(a.file_path)}"`
                : '';
            return `<div class="user-msg-file${openable ? ' is-openable' : ''}"${openable}>` +
                `<i class="fas ${icon}"></i> ${escapeHtml(a.file_name)}${suffix}</div>`;
        }).join('');
        attachHtml = `<div class="user-msg-attachments">${items}</div>`;
    }

    const textHtml = content ? renderMarkdown(content) : '';
    el.innerHTML = `
        <div class="max-w-[75%] sm:max-w-[60%]">
            <div class="bg-primary-400 text-white rounded-2xl px-4 py-2.5 text-sm leading-relaxed msg-content user-bubble">
                ${attachHtml}${textHtml}
            </div>
            <div class="flex items-center justify-end gap-2 mt-1.5">
                <button class="edit-msg-btn text-xs text-slate-300 dark:text-slate-600 hover:text-primary-400 dark:hover:text-primary-400 transition-colors cursor-pointer" title="${t('edit_message')}">
                    <i class="fas fa-pen-to-square"></i>
                </button>
                <button class="delete-msg-btn text-xs text-slate-300 dark:text-slate-600 hover:text-red-500 dark:hover:text-red-400 transition-colors cursor-pointer" title="${t('delete_message_title')}">
                    <i class="fas fa-trash"></i>
                </button>
                <span class="text-xs text-slate-400 dark:text-slate-500">${formatTime(timestamp)}</span>
            </div>
        </div>
    `;
    // Store raw content for editing
    el.dataset.rawContent = content || '';
    highlightMentions(el.querySelector('.msg-content'));
    return el;
}

function renderToolCallsHtml(toolCalls) {
    if (!toolCalls || toolCalls.length === 0) return '';
    return toolCalls.map(tc => {
        const argsStr = formatToolArgs(tc.arguments || {});
        const resultStr = tc.result ? escapeHtml(String(tc.result)) : '';
        const hasResult = !!resultStr;
        return `
<div class="agent-step agent-tool-step">
    <div class="tool-header" onclick="this.parentElement.classList.toggle('expanded')">
        <i class="fas fa-check text-primary-400 flex-shrink-0 tool-icon"></i>
        <span class="tool-name">${escapeHtml(tc.name || '')}</span>
        <i class="fas fa-chevron-right tool-chevron"></i>
    </div>
    <div class="tool-detail">
        <div class="tool-detail-section">
            <div class="tool-detail-label">Input</div>
            <pre class="tool-detail-content">${argsStr}</pre>
        </div>
        ${hasResult ? `
        <div class="tool-detail-section tool-output-section">
            <div class="tool-detail-label">Output</div>
            <pre class="tool-detail-content">${resultStr}</pre>
        </div>` : ''}
    </div>
</div>`;
    }).join('');
}

// Cap for rendering reasoning content in the bubble. Beyond this size,
// we skip markdown rendering entirely and show plain text head + tail to
// keep the page responsive (very long chains-of-thought can otherwise
// stall or crash the browser when re-parsed by marked.js).
// Keep this in sync with backend MAX_STORED_REASONING_CHARS and
// MAX_REASONING_STREAM_CHARS so storage / SSE / display stay aligned.
const REASONING_RENDER_CAP = 4 * 1024; // 4 KB

function _truncateReasoningForDisplay(text) {
    if (!text || text.length <= REASONING_RENDER_CAP) return { text, truncated: false, omitted: 0 };
    const half = Math.floor(REASONING_RENDER_CAP / 2);
    const head = text.slice(0, half);
    const tail = text.slice(-half);
    return {
        text: head + '\n\n... [' + (text.length - head.length - tail.length) + ' chars omitted] ...\n\n' + tail,
        truncated: true,
        omitted: text.length - head.length - tail.length,
    };
}

function _renderReasoningBody(text) {
    // For short reasoning, render as markdown. For long ones, fall back to
    // an escaped <pre> block to avoid expensive markdown parsing.
    const { text: shown, truncated } = _truncateReasoningForDisplay(text);
    if (truncated || shown.length > REASONING_RENDER_CAP) {
        return '<pre class="thinking-stream-pre">' + escapeHtml(shown) + '</pre>';
    }
    return renderMarkdown(shown);
}

function finalizeThinking(el, startTime, text) {
    const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
    el.querySelector('.thinking-summary').textContent = t('thinking_done');
    const fullDiv = el.querySelector('.thinking-full');
    fullDiv.innerHTML = `<div class="thinking-duration">${t('thinking_duration')} ${elapsed}s</div>` + _renderReasoningBody(text);
}

function renderThinkingHtml(text) {
    if (!text || !text.trim()) return '';
    const full = text.trim();
    return `
<div class="agent-step agent-thinking-step">
    <div class="thinking-header" onclick="this.parentElement.classList.toggle('expanded')">
        <i class="fas fa-lightbulb text-amber-400 flex-shrink-0"></i>
        <span class="thinking-summary">${t('thinking_done')}</span>
        <i class="fas fa-chevron-right thinking-chevron"></i>
    </div>
    <div class="thinking-full">${_renderReasoningBody(full)}</div>
</div>`;
}

function renderStepsHtml(steps) {
    if (!steps || steps.length === 0) return { stepsHtml: '', finalContent: '' };

    // Find the index of the last content step — it becomes the main answer, not a step
    let lastContentIdx = -1;
    for (let i = steps.length - 1; i >= 0; i--) {
        if (steps[i].type === 'content') { lastContentIdx = i; break; }
    }

    let html = '';
    let lastContentText = '';
    for (let i = 0; i < steps.length; i++) {
        const step = steps[i];
        if (step.type === 'thinking') {
            html += renderThinkingHtml(step.content);
        } else if (step.type === 'content') {
            if (i === lastContentIdx) {
                lastContentText = step.content;
            } else {
                html += `<div class="agent-step agent-content-step"><div class="agent-content-body">${renderMarkdown(step.content)}</div></div>`;
            }
        } else if (step.type === 'tool') {
            const argsStr = formatToolArgs(step.arguments || {});
            const resultStr = step.result ? escapeHtml(String(step.result)) : '';
            const isErr = step.is_error === true;
            const iconClass = isErr
                ? 'fas fa-times text-red-400 flex-shrink-0 tool-icon'
                : 'fas fa-check text-primary-400 flex-shrink-0 tool-icon';
            // Same rule as the live stream: a tool that wrote its outcome for
            // a person shows that, not the form the model was handed.
            const outputHtml = step.display
                ? `<div class="tool-display-output has-content">${renderMarkdown(String(step.display))}</div>`
                : (resultStr
                    ? `<pre class="tool-detail-content${isErr ? ' tool-error-text' : ''}">${resultStr}</pre>`
                    : '');
            html += `
<div class="agent-step agent-tool-step${isErr ? ' tool-failed' : ''}">
    <div class="tool-header" onclick="this.parentElement.classList.toggle('expanded')">
        <i class="${iconClass}"></i>
        <span class="tool-name">${escapeHtml(step.name || '')}</span>
        <i class="fas fa-chevron-right tool-chevron"></i>
    </div>
    <div class="tool-detail">
        <div class="tool-detail-section">
            <div class="tool-detail-label">Input</div>
            <pre class="tool-detail-content">${argsStr}</pre>
        </div>
        ${outputHtml ? `
        <div class="tool-detail-section tool-output-section">
            <div class="tool-detail-label">${isErr ? 'Error' : 'Output'}</div>
            ${outputHtml}
        </div>` : ''}
    </div>
</div>`;
            // If this tool sent a file (send/read tool), render the media inline
            // so it persists across page refreshes (SSE-only file events are not stored).
            const mediaHtml = _renderSentFileFromToolResult(step);
            if (mediaHtml) html += mediaHtml;
        }
    }
    return { stepsHtml: html, lastContentText };
}

// Extract file-to-send metadata from a tool's result and render an inline preview.
// Returns '' if the result isn't a file_to_send payload.
function _renderSentFileFromToolResult(step) {
    if (!step || !step.result) return '';
    let payload;
    try {
        payload = typeof step.result === 'string' ? JSON.parse(step.result) : step.result;
    } catch (_) { return ''; }
    if (!payload || payload.type !== 'file_to_send' || !payload.path) return '';
    const webUrl = _toWebUrl(payload.path);
    const fileType = payload.file_type || 'file';
    const fileName = payload.file_name || payload.path.split('/').pop();
    if (fileType === 'image') {
        return `<div class="agent-step">${_buildImageHtml(webUrl)}</div>`;
    }
    if (fileType === 'video') {
        return `<div class="agent-step">${_buildVideoHtml(webUrl)}</div>`;
    }
    return `<div class="agent-step"><a href="${webUrl}" download="${escapeHtml(fileName)}" target="_blank" ` +
        `style="display:inline-flex;align-items:center;gap:6px;padding:8px 14px;margin:8px 0;border-radius:8px;` +
        `background:var(--bg-secondary,#f3f4f6);color:var(--text-primary,#374151);text-decoration:none;font-size:14px;` +
        `border:1px solid var(--border-color,#e5e7eb);">` +
        `<i class="fas fa-file-download" style="color:#6b7280;"></i> ${escapeHtml(fileName)}</a></div>`;
}

// Cosmetic translator for cancel markers persisted in history.
// History keeps the English canonical form for the LLM; only display is localized.
function localizeCancelMarker(text) {
    if (!text) return text;
    if (currentLang !== 'zh') return text;
    return text
        .replace(/_\(Cancelled by user\)_/g, '_(用户已中止)_')
        .replace(/_\(Cancelled\)_/g, '_(已中止)_');
}

function createBotMessageEl(content, timestamp, requestId, msg) {
    const el = document.createElement('div');
    el.className = 'flex gap-3 px-4 sm:px-6 py-3 bot-message-group';
    if (requestId) el.dataset.requestId = requestId;

    let stepsHtml = '';
    let displayContent = localizeCancelMarker(content);

    if (msg && msg.steps && msg.steps.length > 0) {
        // New format: ordered steps with interleaved content
        const result = renderStepsHtml(msg.steps);
        stepsHtml = result.stepsHtml;
        // The final content (last text after all steps) is the main answer
        displayContent = content || result.lastContentText;
    } else {
        // Legacy format: separate tool_calls + optional reasoning
        const toolCalls = msg && msg.tool_calls;
        const reasoning = msg && msg.reasoning;
        stepsHtml = renderThinkingHtml(reasoning) + renderToolCallsHtml(toolCalls);
    }

    // Files written this turn, as computed by the history API (workspace.js).
    const artifactsHtml = typeof renderArtifactCards === 'function'
        ? renderArtifactCards(msg && msg.artifacts)
        : '';

    // Self-evolution bubbles get a small badge so the user can feel the agent
    // learned something on its own (text itself stays clean). History replay
    // carries msg.kind; live pushes are identified by the evolution_ request id.
    const isEvolution = (msg && msg.kind === 'evolution')
        || (typeof requestId === 'string' && requestId.startsWith('evolution_'));
    const evolutionBadge = isEvolution
        ? `<div class="flex items-center gap-1 mb-1.5 text-xs text-slate-400 dark:text-slate-500">
                <i class="fas fa-seedling text-[11px]"></i>
                <span>${t('evolution_badge')}</span>
           </div>`
        : '';

    // The reply's face is whichever Agent spoke: its uploaded image, or the
    // product logo by default. A shared conversation also labels the bubble,
    // since consecutive bubbles can come from different Agents; a solo chat
    // stays unlabelled but still reflects that Agent's own avatar.
    const speaker = botSpeakerAgent(msg, requestId) || findAgent(activeAgentId);
    // Remember who spoke, so a later avatar change can repaint this exact face
    // without re-rendering the whole bubble.
    if (speaker && speaker.id) el.dataset.speakerAgent = speaker.id;
    const faceHtml = `<span class="bot-face">${agentAvatarHTML(speaker, 32)}</span>`;
    const speakerName = (sharedConversation() && speaker)
        ? `<div class="bot-speaker">${escapeHtml(speaker.name || speaker.id)}</div>`
        : '';

    el.innerHTML = `
        ${faceHtml}
        <div class="min-w-0 flex-1 max-w-[85%]">
            ${speakerName}
            <div class="bg-white dark:bg-[#1A1A1A] border border-slate-200 dark:border-white/10 rounded-2xl px-4 py-3 text-sm leading-relaxed msg-content text-slate-700 dark:text-slate-200">
                ${evolutionBadge}
                ${stepsHtml ? `<div class="agent-steps">${stepsHtml}</div>` : ''}
                <div class="answer-content">${renderMarkdown(displayContent)}</div>
                <div class="media-content">${artifactsHtml}</div>
                <div class="bot-audio-slot"></div>
            </div>
            <div class="flex items-center gap-2 mt-1.5">
                <span class="text-xs text-slate-400 dark:text-slate-500">${formatTime(timestamp)}</span>
                <button class="copy-msg-btn text-xs text-slate-300 dark:text-slate-600 hover:text-slate-500 dark:hover:text-slate-400 transition-colors cursor-pointer" title="${currentLang === 'zh' ? '复制' : 'Copy'}">
                    <i class="fas fa-copy"></i>
                </button>
                <button class="speak-msg-btn text-xs text-slate-300 dark:text-slate-600 hover:text-slate-500 dark:hover:text-slate-400 transition-colors cursor-pointer" title="${t('speak_msg')}" style="display:none;">
                    <i class="fas fa-volume-up"></i>
                </button>
                <button class="regenerate-msg-btn text-xs text-slate-300 dark:text-slate-600 hover:text-primary-400 dark:hover:text-primary-400 transition-colors cursor-pointer" title="${t('regenerate_response')}">
                    <i class="fas fa-rotate-right"></i>
                </button>
            </div>
        </div>
    `;
    el.querySelector('.answer-content').dataset.rawMd = displayContent;
    // Existing TTS attachment (history replay): mount the player up-front.
    const existingAudio = msg && msg.extras && msg.extras.audio && msg.extras.audio.url;
    if (existingAudio) {
        attachAudioToBotBubble(el, existingAudio, { autoplay: false });
    }
    renderBotSpeakerButton(el, displayContent);
    applyHighlighting(el);
    return el;
}

// Append (or replace) a small audio player inside a bot bubble's
// dedicated `.bot-audio-slot`. Used by both live TTS pushes and history
// replay. Silent failures: never throws.
function attachAudioToBotBubble(botEl, audioUrl, opts) {
    try {
        if (!botEl || !audioUrl) return;
        const slot = botEl.querySelector('.bot-audio-slot');
        if (!slot) return;
        slot.innerHTML = '';
        slot.style.marginTop = '6px';
        const pill = renderVoicePill(audioUrl, { autoplay: !!(opts && opts.autoplay) });
        slot.appendChild(pill);
        const speakBtn = botEl.querySelector('.speak-msg-btn');
        if (speakBtn) speakBtn.style.display = 'none';
    } catch (_) { /* silent */ }
}

function pendingVoiceAttachmentKey(sid, botSeq) {
    return `${sid}:${botSeq}`;
}

function rememberPendingVoiceAttachment(sid, botSeq, audioUrl) {
    if (!sid || botSeq === undefined || botSeq === null || !audioUrl) return;
    const key = pendingVoiceAttachmentKey(sid, botSeq);
    const pending = {
        sid,
        botSeq: String(botSeq),
        audioUrl,
        expiresAt: Date.now() + PENDING_VOICE_ATTACH_TTL_MS,
    };
    pendingVoiceAttachments.delete(key);
    pendingVoiceAttachments.set(key, pending);

    while (pendingVoiceAttachments.size > PENDING_VOICE_ATTACH_MAX) {
        pendingVoiceAttachments.delete(pendingVoiceAttachments.keys().next().value);
    }
    setTimeout(() => {
        if (pendingVoiceAttachments.get(key) === pending) {
            pendingVoiceAttachments.delete(key);
        }
    }, PENDING_VOICE_ATTACH_TTL_MS);
}

function flushPendingVoiceAttachments(sid, autoplay) {
    if (!sid || sid !== sessionId) return 0;
    const now = Date.now();
    let attached = 0;
    pendingVoiceAttachments.forEach((pending, key) => {
        if (pending.expiresAt <= now) {
            pendingVoiceAttachments.delete(key);
            return;
        }
        if (pending.sid !== sid) return;
        const botEl = Array.from(
            messagesDiv.querySelectorAll('.bot-message-group[data-seq]')
        ).find(el => el.dataset.seq === pending.botSeq);
        if (!botEl) return;
        attachAudioToBotBubble(botEl, pending.audioUrl, { autoplay: !!autoplay });
        pendingVoiceAttachments.delete(key);
        attached++;
    });
    return attached;
}

// Build a compact play/pause + progress + duration pill that wraps a
// hidden <audio>. Returns the root element; safe to embed anywhere.
function renderVoicePill(audioUrl, opts) {
    opts = opts || {};
    const wrap = document.createElement('div');
    wrap.className = 'voice-pill';
    wrap.innerHTML = `
        <button type="button" class="voice-pill-btn" data-state="play" aria-label="play">
            <i class="fas fa-play"></i>
        </button>
        <div class="voice-pill-track"><div class="voice-pill-fill"></div></div>
        <span class="voice-pill-time">0:00</span>
        <audio preload="metadata" src="${audioUrl}"></audio>
    `;
    const btn = wrap.querySelector('.voice-pill-btn');
    const fill = wrap.querySelector('.voice-pill-fill');
    const timeEl = wrap.querySelector('.voice-pill-time');
    const audio = wrap.querySelector('audio');

    const fmt = (s) => {
        if (!isFinite(s) || s < 0) s = 0;
        const m = Math.floor(s / 60);
        const r = Math.floor(s % 60);
        return `${m}:${r < 10 ? '0' : ''}${r}`;
    };
    const setIcon = (state) => {
        btn.dataset.state = state;
        btn.querySelector('i').className = state === 'pause' ? 'fas fa-pause' : 'fas fa-play';
        btn.setAttribute('aria-label', state === 'pause' ? 'pause' : 'play');
    };

    audio.addEventListener('loadedmetadata', () => {
        if (audio.duration && isFinite(audio.duration)) timeEl.textContent = fmt(audio.duration);
    });
    audio.addEventListener('timeupdate', () => {
        const dur = audio.duration || 0;
        if (dur > 0) {
            fill.style.width = `${Math.min(100, (audio.currentTime / dur) * 100)}%`;
            timeEl.textContent = fmt(dur - audio.currentTime);
        }
    });
    audio.addEventListener('ended', () => {
        setIcon('play');
        fill.style.width = '0%';
        timeEl.textContent = fmt(audio.duration || 0);
    });
    audio.addEventListener('play',  () => setIcon('pause'));
    audio.addEventListener('pause', () => setIcon('play'));

    btn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (audio.paused) {
            audio.play().catch(() => {});
        } else {
            audio.pause();
        }
    });

    if (opts.autoplay) {
        // Autoplay may be blocked by the browser; fall back silently and
        // let the user tap the play button.
        const tryPlay = () => audio.play().catch(() => {});
        if (audio.readyState >= 2) tryPlay();
        else audio.addEventListener('canplay', tryPlay, { once: true });
    }
    return wrap;
}

// Show the manual "read aloud" button when TTS is configured but the
// bubble has no audio yet. Lazily probes capability via /api/models so
// we don't expose the button when nothing can synthesize speech.
function renderBotSpeakerButton(botEl, text) {
    if (!botEl || !text || !text.trim()) return;
    const btn = botEl.querySelector('.speak-msg-btn');
    if (!btn) return;
    if (botEl.querySelector('.bot-audio-slot audio')) return;
    _isTtsReady().then(ready => {
        if (!ready) return;
        btn.style.display = '';
        btn.onclick = () => _triggerManualTts(btn, botEl, text);
    });
}

let _ttsReadyPromise = null;
let _ttsReadyTs = 0;
function _isTtsReady() {
    // Cache for 30s to avoid hammering /api/models on every bubble.
    if (_ttsReadyPromise && Date.now() - _ttsReadyTs < 30000) {
        return _ttsReadyPromise;
    }
    _ttsReadyTs = Date.now();
    _ttsReadyPromise = fetch('/api/models')
        .then(r => r.json())
        .then(data => {
            const tts = data && data.capabilities && data.capabilities.tts;
            if (!tts) return false;
            return Boolean(tts.current_provider || tts.suggested_provider);
        })
        .catch(() => false);
    return _ttsReadyPromise;
}

function _triggerManualTts(btn, botEl, text) {
    if (btn.dataset.busy === '1') return;
    btn.dataset.busy = '1';
    const icon = btn.querySelector('i');
    const prev = icon ? icon.className : '';
    if (icon) icon.className = 'fas fa-spinner fa-spin';
    fetch('/api/voice/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, session_id: sessionId }),
    })
        .then(r => r.json())
        .then(data => {
            if (data && data.status === 'success' && data.audio_url) {
                attachAudioToBotBubble(botEl, data.audio_url, { autoplay: true });
            }
        })
        .catch(() => {})
        .finally(() => {
            btn.dataset.busy = '0';
            if (icon) icon.className = prev || 'fas fa-volume-up';
        });
}

function addUserMessage(content, timestamp, attachments) {
    const el = createUserMessageEl(content, timestamp, attachments);
    messagesDiv.appendChild(el);
    _autoScrollEnabled = true;
    scrollChatToBottom(true);
}

function addBotMessage(content, timestamp, requestId) {
    const el = createBotMessageEl(content, timestamp, requestId);
    messagesDiv.appendChild(el);
    scrollChatToBottom();
}

// Load conversation history from the server (page 1 = most recent messages).
// Subsequent pages prepend older messages when the user scrolls to the top.
function loadHistory(page) {
    const historySessionId = sessionId;
    const historyAgentId = activeAgentId;
    const historyEpoch = _authEpoch;
    const historyTenantId = sessionStorage.getItem('cow_tenant_id');
    const context = JSON.stringify([historyEpoch, historyTenantId, historyAgentId, historySessionId]);
    if (historyLoading && _historyLoadContext === context) return;
    historyLoading = true;
    _historyLoadContext = context;
    const requestSeq = ++_historyLoadSeq;
    const current = () => requestSeq === _historyLoadSeq && historySessionId === sessionId
        && historyAgentId === activeAgentId && historyEpoch === _authEpoch
        && historyTenantId === sessionStorage.getItem('cow_tenant_id');

    // A shared conversation labels each bubble with its author and paints the
    // right face. That resolution needs this session's team roster (_sessCfg),
    // which loads asynchronously; without it every replayed bubble falls back
    // to the owner's avatar and loses its name. Make sure the roster is in hand
    // before rendering so a reload looks exactly like the live conversation.
    const ready = _sessCfg ? Promise.resolve() : refreshSessionSettings().catch(() => {});

    return ready.then(() => {
        if (!current()) return;
        return fetch(`/api/history?session_id=${encodeURIComponent(historySessionId)}&agent_id=${encodeURIComponent(historyAgentId)}&page=${page}&page_size=20`)
        .then(async r => {
            const data = await r.json();
            if (!r.ok || data.status !== 'success' || !Array.isArray(data.messages)) {
                throw new Error('Conversation history request failed');
            }
            return data;
        })
        .then(data => {
            // A response from a session we have since left must never render
            // into the new session's message list.
            if (!current() || data.messages.length === 0) return;

            const prevScrollHeight = messagesDiv.scrollHeight;
            const isFirstLoad = page === 1;

            // On first load, remove the welcome screen if history exists
            if (isFirstLoad) {
                const ws = document.getElementById('welcome-screen');
                if (ws) ws.remove();
            }

            // Build a fragment of history message elements in chronological order
            const fragment = document.createDocumentFragment();

            if (data.has_more && page > 1) {
                // Keep the "load more" sentinel in place (inserted below)
            }

            const ctxStartSeq = data.context_start_seq || 0;
            let dividerInserted = false;

            data.messages.forEach(msg => {
                const hasContent = msg.content && msg.content.trim();
                const hasToolCalls = msg.role === 'assistant' && msg.tool_calls && msg.tool_calls.length > 0;
                if (!hasContent && !hasToolCalls) return;

                // Insert context divider when transitioning from above to below boundary
                if (ctxStartSeq > 0 && !dividerInserted && msg._seq !== undefined && msg._seq >= ctxStartSeq) {
                    dividerInserted = true;
                    const divider = document.createElement('div');
                    divider.className = 'context-divider';
                    divider.innerHTML = `<span>${t('context_cleared')}</span>`;
                    fragment.appendChild(divider);
                }

                const ts = new Date(msg.created_at * 1000);
                const el = msg.role === 'user'
                    ? createUserMessageEl(msg.content, ts)
                    : createBotMessageEl(msg.content || '', ts, null, msg);
                // Store seq for delete functionality
                if (msg._seq !== undefined) {
                    el.dataset.seq = msg._seq;
                }
                fragment.appendChild(el);
            });

            // If context was cleared but no new messages exist yet, append divider at the end
            if (ctxStartSeq > 0 && !dividerInserted) {
                const divider = document.createElement('div');
                divider.className = 'context-divider';
                divider.innerHTML = `<span>${t('context_cleared')}</span>`;
                fragment.appendChild(divider);
            }

            // Prepend history above any existing messages
            const sentinel = document.getElementById('history-load-more');
            const insertBefore = sentinel ? sentinel.nextSibling : messagesDiv.firstChild;
            messagesDiv.insertBefore(fragment, insertBefore);
            updateEditButtonsState();
            // A background voice_attach can arrive before this history
            // fragment creates its target bubble. Retry now that seq metadata
            // is present in the DOM; do not autoplay delayed attachments.
            if (isFirstLoad) {
                flushPendingVoiceAttachments(historySessionId, false);
            }

            // Manage the "load more" sentinel at the very top
            if (data.has_more) {
                if (!document.getElementById('history-load-more')) {
                    const btn = document.createElement('div');
                    btn.id = 'history-load-more';
                    btn.className = 'flex justify-center py-3';
                    btn.innerHTML = `<button class="text-xs text-slate-400 dark:text-slate-500 hover:text-primary-400 transition-colors" onclick="loadHistory(historyPage + 1)">Load earlier messages</button>`;
                    messagesDiv.insertBefore(btn, messagesDiv.firstChild);
                }
            } else {
                const sentinel = document.getElementById('history-load-more');
                if (sentinel) sentinel.remove();
            }

            historyHasMore = data.has_more;
            historyPage = page;

            if (isFirstLoad) {
                // Scroll to the very bottom after the DOM settles. A single
                // rAF isn't enough: markdown/code-highlight/images keep growing
                // scrollHeight after the first paint, leaving the last bubble's
                // timestamp clipped. Re-pin a few times to catch late layout.
                requestAnimationFrame(() => scrollChatToBottom(true));
                [120, 350, 700].forEach(d => setTimeout(() => scrollChatToBottom(true), d));
            } else {
                // Restore scroll position so loading older messages doesn't jump the view
                messagesDiv.scrollTop = messagesDiv.scrollHeight - prevScrollHeight;
            }
        });
    })
        .catch(error => {
            if (!current()) return;
            console.warn('[history] Failed to open conversation', error);
            _wsToast(t('session_history_failed'));
        })
        .finally(() => {
            if (!current()) return;
            historyLoading = false;
            renderComposerIdentity();
        });
}

function addLoadingIndicator() {
    const el = document.createElement('div');
    el.className = 'flex gap-3 px-4 sm:px-6 py-3 loading-indicator';
    // Starts on the conversation's own Agent; setLoadingSpeaker swaps the face
    // once the server says who actually took the turn (an addressed teammate).
    el.innerHTML = `
        <span class="bot-face">${agentAvatarHTML(findAgent(activeAgentId), 32)}</span>
        <div class="bg-white dark:bg-[#1A1A1A] border border-slate-200 dark:border-white/10 rounded-2xl px-4 py-3">
            <div class="flex items-center gap-1.5">
                <span class="w-2 h-2 rounded-full bg-primary-400 animate-pulse-dot" style="animation-delay: 0s"></span>
                <span class="w-2 h-2 rounded-full bg-primary-400 animate-pulse-dot" style="animation-delay: 0.2s"></span>
                <span class="w-2 h-2 rounded-full bg-primary-400 animate-pulse-dot" style="animation-delay: 0.4s"></span>
            </div>
        </div>
    `;
    messagesDiv.appendChild(el);
    scrollChatToBottom();
    return el;
}

/* The session-panel "新对话" button. With a single Agent there is nobody to
   choose between, so it just starts a chat. With several, it opens a menu: pick
   an Agent for a solo chat, or open the team picker for a group chat. */
function onNewChatButton(event) {
    if (!multiAgentMode()) { newChat(true); return; }
    if (event) event.stopPropagation();
    const menu = document.getElementById('new-chat-menu');
    if (!menu) { newChat(true); return; }
    if (!menu.classList.contains('hidden')) { menu.classList.add('hidden'); return; }
    const rows = enabledAgents().map(agent => `
        <button type="button" class="new-chat-item" onclick="startSoloChat('${escapeHtml(agent.id)}')">
            ${agentAvatarHTML(agent, 22)}
            <span>${escapeHtml(agent.name)}</span>
        </button>`).join('');
    menu.innerHTML = `
        <div class="new-chat-section">${rows}</div>
        <div class="new-chat-sep"></div>
        <button type="button" class="new-chat-item new-chat-team" onclick="openTeamChatModal()">
            <span class="new-chat-team-ico"><i class="fas fa-user-group"></i></span>
            <span>${escapeHtml(t('new_team_chat'))}</span>
        </button>`;
    menu.classList.remove('hidden');
}

function startSoloChat(agentId) {
    document.getElementById('new-chat-menu')?.classList.add('hidden');
    if (!agentId) { newChat(true); return; }
    activeAgentId = agentId;
    writeScopedPreference('cow_active_agent', activeAgentId);
    newChat(true);
    if (typeof resetWorkspaceToAgentRoot === 'function') resetWorkspaceToAgentRoot();
    renderComposerIdentity();
}

// The first checked Agent owns the conversation; the rest are invited as guests.
let _teamChatPicks = [];

function openTeamChatModal() {
    document.getElementById('new-chat-menu')?.classList.add('hidden');
    _teamChatPicks = [activeAgentId || defaultAgentId];
    const status = document.getElementById('team-chat-status');
    if (status) status.textContent = '';
    renderTeamChatList();
    document.getElementById('team-chat-modal')?.classList.remove('hidden');
}

function closeTeamChatModal() {
    document.getElementById('team-chat-modal')?.classList.add('hidden');
}

/** From the group-chat picker, jump to creating a new Agent. */
function openAgentCreateFromModal() {
    closeTeamChatModal();
    navigateTo('agents');
    if (typeof openAgentCreateForm === 'function') openAgentCreateForm();
}

function toggleTeamChatPick(agentId) {
    const i = _teamChatPicks.indexOf(agentId);
    if (i === -1) _teamChatPicks.push(agentId);
    else _teamChatPicks.splice(i, 1);
    renderTeamChatList();
}

function renderTeamChatList() {
    const list = document.getElementById('team-chat-list');
    if (!list) return;
    list.innerHTML = enabledAgents().map(agent => {
        const rank = _teamChatPicks.indexOf(agent.id);
        const on = rank !== -1;
        const owner = rank === 0;
        return `<button type="button" class="team-chat-row${on ? ' on' : ''}" onclick="toggleTeamChatPick('${escapeHtml(agent.id)}')">
            ${agentAvatarHTML(agent, 28)}
            <span class="team-chat-name">${escapeHtml(agent.name)}</span>
            ${owner ? `<span class="team-chat-owner">${escapeHtml(t('new_team_chat_owner'))}</span>` : ''}
            <span class="team-chat-check"><i class="fas ${on ? 'fa-circle-check' : 'fa-circle'}"></i></span>
        </button>`;
    }).join('');
}

function startTeamChat() {
    const picks = _teamChatPicks.filter(id => enabledAgents().some(a => a.id === id));
    if (picks.length < 2) {
        const status = document.getElementById('team-chat-status');
        if (status) status.textContent = t('new_team_chat_min');
        return;
    }
    closeTeamChatModal();
    const [owner, ...guests] = picks;
    activeAgentId = owner;
    writeScopedPreference('cow_active_agent', activeAgentId);
    newChat(true);
    if (typeof resetWorkspaceToAgentRoot === 'function') resetWorkspaceToAgentRoot();
    // The fresh session exists client-side; invite the guests onto it so the
    // very first message already goes to a group.
    setTeamMembers(guests).then(() => renderComposerIdentity());
}

function newChat(optimistic = true, inherit = true) {
    // A fresh session resets the preview panel, discarding an open editor.
    if (typeof wsGuardUnsaved === 'function'
        && !wsGuardUnsaved(() => newChat(optimistic, inherit))) return;

    // Do NOT close active streams: other sessions keep streaming in the
    // background (each stream self-guards against the foreign view) and their
    // replies still complete and persist.

    // Generate a fresh session and persist it so the next page load also starts clean
    sessionId = generateSessionId();
    writeScopedPreference(activeSessionStorageKey(), sessionId);
    _sessCfg = null;
    if (!inherit) {
        _wsSelState = { current: null, recents: [], defaultWorkspace: '', projectsRoot: '' };
        _wsSelUpdateLabel();
    }
    refreshWorkspaceSelector();  // a fresh session starts on the default workspace
    refreshSessionSettings();    // ... and on the global model / permission
    if (typeof wsOnSessionSwitch === 'function') wsOnSessionSwitch();
    resetSendBtnSendMode();  // fresh session has no in-flight reply
    startPolling();  // bump generation so old loop self-cancels, new loop uses fresh sessionId
    messagesDiv.innerHTML = '';
    renderWelcomeScreen();
    renderComposerIdentity();
    if (currentView !== 'chat') navigateTo('chat');

    // A fresh session may not have a backend record until its first message, so
    // only prepend an optimistic item for a real new-chat action. After deleting
    // the current session it is skipped: the fresh session has no row yet, and
    // inserting one would leave an empty, undeletable item behind (deleting it
    // would just spawn another).
    const newSid = sessionId;
    if (_historyVisible) {
        if (optimistic) {
            loadSessionList(() => _addOptimisticSessionItem(newSid));
        } else {
            loadSessionList();
        }
    } else {
        // The list is hidden; mark it dirty so the next visit re-reads it.
        _historyDirty = true;
    }
}

// =====================================================================
// Session History (workbench page)
// =====================================================================

// The history page is the sole consumer of the session list now that the old
// collapsible panel is gone. `_historyVisible` tracks whether the page is the
// active view; `_historyDirty` marks a pending reload (a session changed while
// the page was hidden, or the page was left and needs a fresh read).
let _historyVisible = false;
let _historyDirty = false;

function _isMobileView() {
    return window.innerWidth <= 768;
}

// Swap the native `title` for the CSS tooltip so hints appear instantly
// instead of waiting for the browser's built-in delay.
function _setBtnTooltip(el, text) {
    if (!el) return;
    el.setAttribute('data-tooltip', text);
    el.removeAttribute('title');
}

function _applyInputTooltips() {
    const set = (id, key, pos) => {
        const el = document.getElementById(id);
        if (!el) return;
        _setBtnTooltip(el, t(key));
        if (pos) el.setAttribute('data-tooltip-pos', pos);
    };
    set('new-chat-btn', 'tip_new_chat');
    set('clear-context-btn', 'tip_clear_context');
    set('attach-btn', 'tip_attach');
    set('steer-btn', 'steer_active');
    set('workspace-toggle-btn', 'ws_toggle', 'bottom');
    // The history page's inline refresh button carries a translated tooltip.
    const historyRefresh = document.querySelector('.history-refresh-btn');
    if (historyRefresh) _setBtnTooltip(historyRefresh, t('ws_refresh'));
    // Optimize / mic buttons carry state-dependent tooltips managed in their
    // own setup, but on language switch we reset them to the idle label so the
    // tooltip follows the current locale.
    set('optimize-btn', 'optimize_idle_title');
    set('mic-btn', 'mic_idle_title');
    // Send button only carries a tooltip while it acts as the cancel button.
    _setBtnTooltip(sendBtn, sendBtnMode === 'cancel' ? t('tip_cancel') : '');
    // The permission / model chips carry translated labels and tooltips, so they
    // are repainted here too (this runs on every language switch).
    _renderPermissionChip();
    _renderModelChip();
}

// A session that exists in the browser but not yet in the database: the user
// pressed "new chat" and has not sent the first message. Rendered from the same
// path as real sessions so it lands in the right group.
function _addOptimisticSessionItem(sid) {
    if (_historyQuery) return;
    const container = document.getElementById('session-list');
    if (!container) return;
    if (_sessionItems.some(s => s.session_id === sid)) return;

    _sessionItems.unshift({
        session_id: sid,
        title: t('new_chat'),
        last_active: Math.floor(Date.now() / 1000),
        pinned: 0,
        // The fresh session inherits the workspace the selector currently shows.
        project: _wsSelState.current
            ? { path: _wsSelState.current.path, name: _wsSelState.current.name }
            : null,
    });
    _renderSessionList();
}

function _sessionTimeGroup(ts) {
    const now = new Date();
    const d = new Date(ts * 1000);
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1);
    if (d >= today) return t('today');
    if (d >= yesterday) return t('yesterday');
    return t('earlier');
}

let _sessionPage = 1;
let _sessionHasMore = false;
let _sessionLoading = false;
const _SESSION_PAGE_SIZE = 50;

// Every session loaded so far, in backend order (pinned first, then recency).
// Kept as data rather than only as DOM because grouping by project reorders the
// whole list, which cannot be done by appending page by page.
let _sessionItems = [];
// 'time' (今天/昨天/更早, the behavior before projects existed) or 'project'.
// The backend decides, based on how many spaces are in use across all sessions.
let _sessionGroupMode = 'time';
// User-chosen order of project spaces (paths + '__default__'), from the backend.
let _projectOrder = [];
// Sentinel the backend uses for the default workspace in the ordering.
const DEFAULT_SPACE_KEY = '__default__';

// Which project groups are collapsed, persisted per-browser so the choice
// survives reloads. Keyed by space key (project path or the default sentinel).
const _COLLAPSED_KEY = 'cow_collapsed_projects';
function _loadCollapsed() {
    try { return new Set(JSON.parse(localStorage.getItem(_COLLAPSED_KEY) || '[]')); }
    catch (e) { return new Set(); }
}
function _saveCollapsed(set) {
    try { localStorage.setItem(_COLLAPSED_KEY, JSON.stringify([...set])); } catch (e) {}
}
let _collapsedProjects = _loadCollapsed();

// Request-generation + context guard for the session list, so a late response
// from an earlier read (or one started under a different Agent / after a
// re-entry) is dropped instead of overwriting a fresher list.
let _sessionReqSeq = 0;
let _sessionReqAgent = '';
let _historyQuery = '';
let _historySearchTimer = null;
let _historySearchComposing = false;
let _historyTotal = null;
let _historyRequestController = null;
let _historyAuthGeneration = 0;
let _historyStatus = { key: '', message: '', error: false, retry: null };
let _historyPageFailed = false;
function _sessionListContext() {
    return JSON.stringify([_historyAuthGeneration, sessionStorage.getItem('cow_tenant_id') || '', activeAgentId, _historyQuery]);
}

function _renderHistoryStatus() {
    const status = document.getElementById('history-status');
    if (!status) return;
    const text = _historyStatus.key ? t(_historyStatus.key) : _historyStatus.message;
    status.textContent = text || '';
    status.classList.remove('opacity-0');
    status.classList.toggle('history-status-hidden', !text);
    status.classList.toggle('history-status-error', _historyStatus.error);
    status.setAttribute('role', _historyStatus.error ? 'alert' : 'status');
    if (_historyStatus.retry) {
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.className = 'history-status-actions';
        retry.textContent = t('session_history_retry');
        retry.addEventListener('click', _historyStatus.retry);
        status.appendChild(retry);
    }
}

function _setHistoryState(key, error = false, retry = null, message = '') {
    _historyStatus = { key, error, retry, message };
    _renderHistoryStatus();
}

function _setHistoryStatus(text, error) {
    _setHistoryState('', !!error, null, text || '');
}

function _setHistoryErrorWithRetry(text, retry = () => loadSessionList()) {
    _setHistoryState('', true, retry, text);
}

function _updateHistorySearchControls() {
    const input = document.getElementById('history-search-input');
    const clear = document.getElementById('history-search-clear');
    const summary = document.getElementById('history-search-summary');
    const refresh = document.getElementById('history-refresh-btn');
    if (input) input.setAttribute('aria-label', t('history_search_placeholder'));
    if (clear) {
        clear.classList.toggle('hidden', !(input && input.value));
        clear.setAttribute('aria-label', t('history_search_clear'));
    }
    if (refresh) {
        refresh.setAttribute('aria-label', t('history_refresh'));
        refresh.setAttribute('data-tooltip', t('history_refresh'));
    }
    if (summary) summary.textContent = _historyQuery && _historyTotal !== null
        ? t('history_search_count').replace('{count}', String(_historyTotal)) : '';
    const list = document.getElementById('session-list');
    if (list) list.setAttribute('aria-busy', String(_sessionLoading));
}

function _cancelHistoryRequest() {
    clearTimeout(_historySearchTimer);
    _historySearchTimer = null;
    if (_historyRequestController) _historyRequestController.abort();
    _historyRequestController = null;
    _sessionReqSeq++;
    _sessionLoading = false;
}

function _resetHistorySearch() {
    _cancelHistoryRequest();
    _historyAuthGeneration++;
    _historySearchComposing = false;
    _historyQuery = '';
    _historyTotal = null;
    _sessionItems = [];
    _sessionHasMore = false;
    _historyPageFailed = false;
    _historyDirty = true;
    const input = document.getElementById('history-search-input');
    if (input) input.value = '';
    _closeSessionActionMenu();
    _setHistoryState('');
    _renderSessionList();
    _updateHistorySearchControls();
}

function onHistorySearchCompositionStart() {
    _historySearchComposing = true;
    _cancelHistoryRequest();
}

function onHistorySearchCompositionEnd(event) {
    _historySearchComposing = false;
    onHistorySearchInput(event);
}

function _readHistorySearchQuery() {
    const input = document.getElementById('history-search-input');
    return input ? input.value.trim() : _historyQuery;
}

function _syncHistorySearchQuery() {
    const query = _readHistorySearchQuery();
    if (query === _historyQuery) return false;
    // The displayed value is authoritative. Restored/autofilled values need not
    // have emitted input, so every explicit submit and refresh also comes here.
    _cancelHistoryRequest();
    _historyQuery = query;
    _sessionItems = [];
    _historyTotal = null;
    _sessionHasMore = false;
    _historyPageFailed = false;
    _closeSessionActionMenu();
    _renderSessionList();
    _updateHistorySearchControls();
    return true;
}

function onHistorySearchInput(event) {
    if (event && event.isComposing) return;
    // A committed InputEvent can recover from a missed compositionend. Keep
    // the composition flag as a fallback only for events without this signal.
    if (event && event.isComposing === false) _historySearchComposing = false;
    if (_historySearchComposing) return;
    const changed = _syncHistorySearchQuery();
    if (!changed && (_sessionLoading || _historyTotal !== null)) {
        _updateHistorySearchControls();
        return;
    }
    if (!changed) _cancelHistoryRequest();
    const query = _historyQuery;
    if (Array.from(query).length > 100) {
        _setHistoryState('history_search_limit', true);
        return;
    }
    if (!query) return _submitHistorySearch();
    _setHistoryState('history_search_loading');
    _historySearchTimer = setTimeout(_submitHistorySearch, 300);
}

function _submitHistorySearch() {
    clearTimeout(_historySearchTimer);
    _historySearchTimer = null;
    if (_historySearchComposing) return;
    return loadSessionList();
}

function onHistorySearchChange(event) {
    if (event && event.isComposing) return;
    _historySearchComposing = false;
    const changed = _syncHistorySearchQuery();
    if (!changed && !_historySearchTimer && (_sessionLoading || _historyTotal !== null)) return;
    return _submitHistorySearch();
}

function onHistorySearchKeydown(event) {
    if (event.key === 'Enter' && event.isComposing === false && event.keyCode !== 229) {
        _historySearchComposing = false;
    }
    if (event.key === 'Enter' && !_historySearchComposing && !event.isComposing && event.keyCode !== 229) {
        event.preventDefault();
        _syncHistorySearchQuery();
        // An immediate submit consumes the debounce; repeated Enter while that
        // same query is loading must not start a duplicate request.
        if (!_sessionLoading) return _submitHistorySearch();
    } else if (event.key === 'Escape' && !_historySearchComposing) {
        event.preventDefault();
        return clearHistorySearch();
    }
}

function clearHistorySearch() {
    const input = document.getElementById('history-search-input');
    if (input) { input.value = ''; input.focus(); }
    _historySearchComposing = false;
    _cancelHistoryRequest();
    _historyQuery = '';
    _historyTotal = null;
    _sessionItems = [];
    _sessionHasMore = false;
    _renderSessionList();
    _updateHistorySearchControls();
    return _submitHistorySearch();
}

function loadSessionList(onDone) {
    const container = document.getElementById('session-list');
    if (!container || _historySearchComposing) return;
    if (container.querySelector('.session-title-input') || _dragSpaceKey !== null) {
        _historyDirty = true;
        return;
    }
    _syncHistorySearchQuery();
    if (Array.from(_historyQuery).length > 100) {
        _setHistoryState('history_search_limit', true);
        return;
    }

    // A fresh (re)load supersedes any in-flight read: reset loading so the new
    // request starts, and bump the sequence so a stale response is dropped.
    _cancelHistoryRequest();
    _sessionPage = 1;
    _sessionHasMore = false;
    _historyDirty = false;
    _historyPageFailed = false;
    _historyTotal = null;
    _sessionReqAgent = _sessionListContext();
    const seq = _sessionReqSeq;
    container.scrollTop = 0;

    return _fetchSessionPage(1, true, onDone, seq);
}

// Refresh the list for session operations that happen while the user may not be
// on the history page: reload only if it is the active view, otherwise mark it
// dirty so the next visit re-reads.
function _refreshHistoryList() {
    if (_historyVisible) loadSessionList();
    else _historyDirty = true;
    if (typeof loadSidebarRecentSessions === 'function') loadSidebarRecentSessions();
}

// === SIDEBAR_RECENT_BEGIN ===
const SIDEBAR_RECENT_LIMIT = 10;
function _sidebarRecentLimit(items) {
    return Array.isArray(items) ? items.slice(0, SIDEBAR_RECENT_LIMIT) : [];
}
// === SIDEBAR_RECENT_END ===

let _sidebarRecentItems = [];
let _sidebarRecentSeq = 0;

function renderSidebarRecentSessions() {
    const list = document.getElementById('sidebar-recent-list');
    const more = document.getElementById('sidebar-recent-more');
    if (!list) return;
    list.innerHTML = '';
    const items = _sidebarRecentLimit(_sidebarRecentItems);
    if (!items.length) {
        const empty = document.createElement('div');
        empty.className = 'sidebar-recent-empty';
        empty.textContent = t('sidebar_history_empty');
        list.appendChild(empty);
        if (more) more.classList.add('hidden');
        return;
    }
    items.forEach(s => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'sidebar-recent-item';
        btn.setAttribute('role', 'listitem');
        const ownerId = (s.agent && s.agent.id) || '';
        const title = s.title || t('untitled_session');
        btn.textContent = title;
        btn.title = title;
        btn.dataset.sessionId = s.session_id || '';
        if (ownerId) btn.dataset.agentId = ownerId;
        const isActive = s.session_id === sessionId && (!ownerId || ownerId === activeAgentId);
        btn.classList.toggle('active', isActive);
        btn.addEventListener('click', () => {
            switchSession(s.session_id, ownerId || undefined);
        });
        list.appendChild(btn);
    });
    if (more) more.classList.toggle('hidden', items.length < 1);
}

function loadSidebarRecentSessions() {
    const wrap = document.getElementById('sidebar-recent');
    if (!wrap) return;
    if (typeof _navAreaFromPath === 'function' && _navAreaFromPath(location.pathname) !== 'workbench') return;
    const seq = ++_sidebarRecentSeq;
    fetch(`/api/sessions?page=1&page_size=${SIDEBAR_RECENT_LIMIT}&scope=all`)
        .then(async r => {
            const data = await r.json().catch(() => ({}));
            return { ok: r.ok, data };
        })
        .then(({ ok, data }) => {
            if (seq !== _sidebarRecentSeq) return;
            if (!ok || !data || data.status !== 'success') {
                _sidebarRecentItems = [];
                const list = document.getElementById('sidebar-recent-list');
                if (list) {
                    list.innerHTML = '';
                    const err = document.createElement('div');
                    err.className = 'sidebar-recent-error';
                    err.textContent = t('session_history_failed');
                    list.appendChild(err);
                }
                return;
            }
            _sidebarRecentItems = _sidebarRecentLimit(data.sessions || []);
            renderSidebarRecentSessions();
        })
        .catch(() => {
            if (seq !== _sidebarRecentSeq) return;
            _sidebarRecentItems = [];
            const list = document.getElementById('sidebar-recent-list');
            if (!list) return;
            list.innerHTML = '';
            const err = document.createElement('div');
            err.className = 'sidebar-recent-error';
            err.textContent = t('session_history_failed');
            list.appendChild(err);
        });
}

function _initSidebarRecent() {
    const wrap = document.getElementById('sidebar-recent');
    const toggle = document.getElementById('sidebar-recent-toggle');
    const label = document.getElementById('sidebar-recent-label');
    const more = document.getElementById('sidebar-recent-more');
    if (!wrap || !toggle) return;
    const setOpen = (open) => {
        wrap.classList.toggle('open', open);
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    };
    toggle.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        setOpen(!wrap.classList.contains('open'));
    });
    // Double-click the label to open the full history page (search/filter),
    // same as the previous top-level「历史对话」entry.
    label?.addEventListener('dblclick', (event) => {
        event.preventDefault();
        navigateTo('history');
    });
    more?.addEventListener('click', () => navigateTo('history'));
    loadSidebarRecentSessions();
}
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _initSidebarRecent);
} else {
    _initSidebarRecent();
}

function _fetchSessionPage(page, clear, onDone, seq) {
    if (_sessionLoading) return;
    const existingList = document.getElementById('session-list');
    if (existingList && (existingList.querySelector('.session-title-input') || _dragSpaceKey !== null)) {
        _historyDirty = true;
        return;
    }
    if (!seq) seq = ++_sessionReqSeq;
    // A re-entry or identity change invalidates prior reads: drop the request so
    // a stale result cannot repaint the current list.
    if (seq !== _sessionReqSeq) return;
    _sessionLoading = true;
    _historyPageFailed = false;
    _setHistoryState(_historyQuery ? 'history_search_loading' : 'session_history_loading');
    _updateHistorySearchControls();

    const container = document.getElementById('session-list');
    if (!container) { _sessionLoading = false; return; }
    const ctx = _sessionListContext();
    const query = _historyQuery;
    const controller = new AbortController();
    _historyRequestController = controller;
    const current = () => seq === _sessionReqSeq && ctx === _sessionListContext();
    const fail = (key, message) => {
        if (!current()) return;
        _sessionLoading = false;
        _historyRequestController = null;
        if (container.querySelector('.session-title-input') || _dragSpaceKey !== null) {
            _historyDirty = true;
            _setHistoryState('');
            _updateHistorySearchControls();
            return;
        }
        _historyPageFailed = true;
        if (clear) { _sessionItems = []; _historyTotal = null; }
        _setHistoryState(key, true, () => _fetchSessionPage(page, clear, onDone, seq), message);
        _renderSessionList();
        _updateHistorySearchControls();
    };
    const url = `/api/sessions?page=${page}&page_size=${_SESSION_PAGE_SIZE}&scope=all`
        + (query ? `&q=${encodeURIComponent(query)}` : '');

    return fetch(url, { signal: controller.signal })
        .then(async r => {
            const data = await r.json();
            if (r.status === 403 || r.status === 503) data._historyUnavailable = true;
            return data;
        })
        .then(data => {
            // Late / stale result: the page changed or the identity moved on.
            if (!current()) return;
            // Editing can begin after this request was sent. Defer its result
            // rather than replacing a focused editor or a dragged project.
            if (container.querySelector('.session-title-input') || _dragSpaceKey !== null) {
                _sessionLoading = false;
                _historyRequestController = null;
                _historyDirty = true;
                _setHistoryState('');
                _updateHistorySearchControls();
                return;
            }

            if (data.status !== 'success') {
                fail(data._historyUnavailable ? 'session_history_not_enabled' : 'session_history_failed');
                return;
            }
            if (query && data.query !== query) {
                fail('history_search_unsupported');
                return;
            }
            _sessionLoading = false;
            _historyRequestController = null;

            if (clear) _sessionItems = [];

            const sessions = data.sessions || [];
            _sessionPage = page;
            _sessionHasMore = !!data.has_more;
            _historyTotal = Number.isFinite(data.total) ? data.total : null;
            _sessionGroupMode = data.group_mode === 'project' ? 'project' : 'time';
            if (Array.isArray(data.project_order)) _projectOrder = data.project_order;

            const sessionKey = s => `${(s.agent && s.agent.id) || ''}::${s.session_id}`;
            const seen = new Set(_sessionItems.map(sessionKey));
            sessions.forEach(s => {
                const key = sessionKey(s);
                if (seen.has(key)) return;
                seen.add(key);
                _sessionItems.push(s);
            });

            // First-page (full) reloads paint the list state; subsequent-page
            // loads keep whatever is already confirmed on screen.
            _setHistoryState(_sessionItems.length ? '' : query ? 'history_search_empty' : 'session_history_empty');
            _renderSessionList();
            _updateHistorySearchControls();
            if (typeof onDone === 'function') onDone();
            // A tall screen may not produce a scroll event after the first page.
            // Fill until scrolling is possible or all matching sessions arrived.
            requestAnimationFrame(() => {
                if (current() && _historyVisible && _sessionHasMore && !_sessionLoading
                        && container.clientHeight > 0 && container.scrollHeight <= container.clientHeight + 60) {
                    _fetchSessionPage(_sessionPage + 1, false, undefined, seq);
                }
            });
        })
        .catch(error => {
            if (error.name !== 'AbortError') fail('session_history_failed');
        });
}

// Split the loaded sessions into ordered, labelled groups.
//
// Time mode keeps the original today/yesterday/earlier buckets, with one
// addition: pinned conversations move into a group of their own at the top,
// because a pin that stayed inside its date bucket would not be findable.
// Project mode groups by workspace instead, and pins float to the top of their
// own project - that is where the user filed them.
function _sessionGroups() {
    const groups = [];
    const bucket = (key, label, icon, hint, isProject) => {
        let g = groups.find(x => x.key === key);
        if (!g) { g = { key, label, icon, hint, isProject, items: [] }; groups.push(g); }
        return g;
    };

    if (_sessionGroupMode === 'project') {
        // `_sessionItems` is already pinned-first / newest-first, so appending in
        // order gives each project the same ordering for free.
        _sessionItems.forEach(s => {
            const key = s.project ? s.project.path : DEFAULT_SPACE_KEY;
            const name = s.project ? s.project.name : t('ws_default_workspace');
            const icon = s.project ? 'fa-folder' : 'fa-house';
            bucket(key, name, icon, s.project ? s.project.path : '', !!s.project).items.push(s);
        });
        // Sort groups by the user's chosen order; spaces without a saved
        // position keep their natural (recency) order after the ordered ones.
        if (_projectOrder.length) {
            const rank = new Map(_projectOrder.map((k, i) => [k, i]));
            groups.sort((a, b) => {
                const ra = rank.has(a.key) ? rank.get(a.key) : Infinity;
                const rb = rank.has(b.key) ? rank.get(b.key) : Infinity;
                return ra - rb;
            });
        }
        return groups;
    }

    const pinned = _sessionItems.filter(s => s.pinned);
    if (pinned.length) {
        bucket('__pinned__', t('session_pinned_group'), 'fa-thumbtack', '', false).items.push(...pinned);
    }
    _sessionItems.filter(s => !s.pinned).forEach(s => {
        const label = _sessionTimeGroup(s.last_active);
        bucket('time:' + label, label, '', '', false).items.push(s);
    });
    return groups;
}

function _renderSessionList() {
    const container = document.getElementById('session-list');
    if (!container) return;
    _closeSessionActionMenu();

    if (!_sessionItems.length) {
        // The state (empty / error / loading) is shown in the status line above;
        // the list itself is left blank.
        container.innerHTML = '';
        return;
    }

    container.innerHTML = '';
    if (_historyQuery) {
        _sessionItems.forEach(s => container.appendChild(_sessionItemEl(s, false)));
        return;
    }
    const projectMode = _sessionGroupMode === 'project';
    // Indent sessions under their project header when several projects are
    // shown, so the list reads as a tree aligned to the folder icon above.
    const indentItems = projectMode && _sessionGroups().length > 1;
    _sessionGroups().forEach(group => {
        const collapsed = projectMode && _collapsedProjects.has(group.key);
        const header = document.createElement('div');
        header.className = 'session-group-label' + (projectMode ? ' session-group-project' : '');
        if (group.hint) header.title = group.hint;

        if (projectMode) {
            // A collapsible, draggable project header. The default space has no
            // rename/delete actions (there is no record to edit) but still drags.
            header.draggable = true;
            header.dataset.spaceKey = group.key;
            const isDefault = group.key === DEFAULT_SPACE_KEY;
            const actions = isDefault ? '' : `
                <button class="session-group-action" title="${escapeHtml(t('project_rename'))}"
                        onclick="event.stopPropagation(); renameProject('${_wsAttr(group.key)}','${_wsAttr(group.label)}')">
                    <i class="fas fa-pen"></i>
                </button>
                <button class="session-group-action" title="${escapeHtml(t('project_delete'))}"
                        onclick="event.stopPropagation(); deleteProject('${_wsAttr(group.key)}','${_wsAttr(group.label)}')">
                    <i class="fas fa-trash-can"></i>
                </button>`;
            header.innerHTML = `
                <i class="fas fa-chevron-down session-group-caret ${collapsed ? 'collapsed' : ''}"></i>
                <i class="fas ${group.icon} session-group-icon"></i>
                <span class="session-group-name">${escapeHtml(group.label)}</span>
                <span class="session-group-count">${group.items.length}</span>
                <span class="session-group-actions">${actions}</span>`;
            header.addEventListener('click', () => _toggleProjectCollapse(group.key));
            _wireGroupDrag(header, group.key);
        } else if (group.icon) {
            header.innerHTML = `<i class="fas ${group.icon}"></i><span>${escapeHtml(group.label)}</span>`;
        } else {
            header.textContent = group.label;
        }
        container.appendChild(header);

        if (!collapsed) {
            group.items.forEach(s => container.appendChild(_sessionItemEl(s, indentItems)));
        }
    });
}

function _toggleProjectCollapse(key) {
    if (_collapsedProjects.has(key)) _collapsedProjects.delete(key);
    else _collapsedProjects.add(key);
    _saveCollapsed(_collapsedProjects);
    _renderSessionList();
}

// --- Project group drag-to-reorder -------------------------------------------
let _dragSpaceKey = null;

function _wireGroupDrag(header, key) {
    header.addEventListener('dragstart', (e) => {
        _dragSpaceKey = key;
        header.classList.add('dragging');
        try { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', key); } catch (err) {}
    });
    header.addEventListener('dragend', () => {
        _dragSpaceKey = null;
        header.classList.remove('dragging');
        if (_historyDirty) { _historyDirty = false; _refreshHistoryList(); }
        document.querySelectorAll('.session-group-project.drop-target')
            .forEach(el => el.classList.remove('drop-target'));
    });
    header.addEventListener('dragover', (e) => {
        if (_dragSpaceKey === null || _dragSpaceKey === key) return;
        e.preventDefault();
        header.classList.add('drop-target');
    });
    header.addEventListener('dragleave', () => header.classList.remove('drop-target'));
    header.addEventListener('drop', (e) => {
        e.preventDefault();
        header.classList.remove('drop-target');
        if (_dragSpaceKey === null || _dragSpaceKey === key) return;
        _reorderSpace(_dragSpaceKey, key);
    });
}

// Move `fromKey` to sit just before `beforeKey`, then persist the new order.
function _reorderSpace(fromKey, beforeKey) {
    // Start from the currently displayed group order so dragging is stable even
    // when some spaces have no saved position yet.
    const current = _sessionGroups().map(g => g.key);
    const order = current.filter(k => k !== fromKey);
    const idx = order.indexOf(beforeKey);
    if (idx < 0) order.push(fromKey);
    else order.splice(idx, 0, fromKey);

    _projectOrder = order;
    _renderSessionList();

    fetch('/api/projects/order', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ order }),
    }).catch(() => {});
}

// Rename a project (display name only; the folder on disk is untouched).
function renameProject(path, currentName) {
    showPromptModal(t('project_rename_title'), currentName, (name) => {
        if (name === null) return;
        fetch('/api/projects/manage', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path, name }),
        })
            .then(r => r.json())
            .then(data => {
                if (data.status !== 'success') { _wsToast(data.message || t('session_settings_failed')); return; }
                _refreshHistoryList();
            })
            .catch(() => _wsToast(t('session_settings_failed')));
    });
}

// Delete a project record. Only the RongAI record is removed; files stay and
// bound sessions revert to the default workspace.
function deleteProject(path, name) {
    showConfirmModal(
        t('project_delete_title'),
        t('project_delete_confirm').replace('{name}', name || path),
        () => {
            fetch('/api/projects/manage', {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path }),
            })
                .then(r => r.json())
                .then(data => {
                    if (data.status !== 'success') { _wsToast(data.message || t('session_settings_failed')); return; }
                    _refreshHistoryList();
                })
                .catch(() => _wsToast(t('session_settings_failed')));
        }
    );
}

function _historyTimeLabel(timestamp) {
    const date = new Date(Number(timestamp) * 1000);
    if (!timestamp || Number.isNaN(date.getTime())) return { text: '', full: '' };
    const now = new Date();
    const locale = currentLang === 'en' ? 'en-US' : currentLang === 'zh-Hant' ? 'zh-TW' : 'zh-CN';
    const today = date.toDateString() === now.toDateString();
    return {
        text: today ? date.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit', hour12: false })
            : date.toLocaleDateString(locale, { month: '2-digit', day: '2-digit', ...(date.getFullYear() !== now.getFullYear() ? { year: 'numeric' } : {}) }),
        full: date.toLocaleString(locale),
    };
}

let _sessionActionMenu = null;
let _sessionMenuCleanup = null;
function _closeSessionActionMenu(restoreFocus = false) {
    if (_sessionMenuCleanup) _sessionMenuCleanup(restoreFocus);
    _sessionMenuCleanup = null;
    if (_sessionActionMenu) _sessionActionMenu.remove();
    _sessionActionMenu = null;
}

function _openSessionActionMenu(event, session, trigger) {
    event.stopPropagation();
    const wasOpen = trigger.getAttribute('aria-expanded') === 'true';
    _closeSessionActionMenu();
    if (wasOpen) return;
    const owner = (session.agent && session.agent.id) || activeAgentId;
    const menu = document.createElement('div');
    menu.className = 'session-action-menu';
    menu.setAttribute('role', 'menu');
    menu.setAttribute('aria-label', t('history_more'));
    const actions = [
        [session.pinned ? 'unpin_session' : 'pin_session', 'fa-thumbtack', () => toggleSessionPin(session.session_id, owner)],
        ['rename_session', 'fa-pen', () => renameSession(session.session_id, owner)],
        ['agents_delete', 'fa-trash-can', () => deleteSession(session.session_id, owner)],
    ];
    actions.forEach(([label, icon, action], index) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'session-action-menu-item' + (index === 2 ? ' danger' : '');
        button.setAttribute('role', 'menuitem');
        button.innerHTML = `<i class="fas ${icon}" aria-hidden="true"></i><span>${escapeHtml(t(label))}</span>`;
        button.addEventListener('click', e => {
            e.stopPropagation();
            _closeSessionActionMenu(index !== 1);
            action();
        });
        menu.appendChild(button);
    });
    document.body.appendChild(menu);
    _sessionActionMenu = menu;
    trigger.setAttribute('aria-expanded', 'true');
    const rect = trigger.getBoundingClientRect();
    const bounds = menu.getBoundingClientRect();
    menu.style.left = Math.max(8, Math.min(rect.right - bounds.width, window.innerWidth - bounds.width - 8)) + 'px';
    menu.style.top = Math.max(8, rect.bottom + bounds.height + 6 <= window.innerHeight
        ? rect.bottom + 6 : rect.top - bounds.height - 6) + 'px';
    const dismissOutside = e => { if (!menu.contains(e.target) && !trigger.contains(e.target)) _closeSessionActionMenu(); };
    const dismiss = () => _closeSessionActionMenu();
    const keydown = e => {
        const buttons = [...menu.querySelectorAll('button')];
        const index = buttons.indexOf(document.activeElement);
        if (e.key === 'Escape') { e.preventDefault(); _closeSessionActionMenu(true); }
        else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            buttons[(index + (e.key === 'ArrowDown' ? 1 : buttons.length - 1)) % buttons.length].focus();
        } else if (e.key === 'Tab') _closeSessionActionMenu(true);
    };
    document.addEventListener('pointerdown', dismissOutside);
    window.addEventListener('resize', dismiss);
    window.addEventListener('scroll', dismiss, true);
    menu.addEventListener('keydown', keydown);
    _sessionMenuCleanup = restore => {
        trigger.setAttribute('aria-expanded', 'false');
        document.removeEventListener('pointerdown', dismissOutside);
        window.removeEventListener('resize', dismiss);
        window.removeEventListener('scroll', dismiss, true);
        if (restore && trigger.isConnected) trigger.focus();
    };
    menu.querySelector('button').focus({ preventScroll: true });
}

function _sessionItemEl(s, indent) {
    const item = document.createElement('div');
    const ownerId = (s.agent && s.agent.id) || '';
    const isActive = s.session_id === sessionId && (!ownerId || ownerId === activeAgentId);
    item.className = 'session-item' + (isActive ? ' active' : '') + (s.pinned ? ' pinned' : '')
        + (indent ? ' session-item-indent' : '');
    item.dataset.sessionId = s.session_id;
    if (ownerId) item.dataset.agentId = ownerId;

    const title = s.title || t('untitled_session');
    // Faces mark a conversation that has several Agents in it, the way a group
    // chat is distinguishable from a direct one. A conversation with a single
    // Agent stays a plain row, whatever the roster looks like elsewhere. We show
    // at most three overlapping faces to keep the row tidy; when more took part,
    // a small "+N" caps the stack so the group's size is still legible.
    const roster = s.participants || [];
    const crowd = roster.length > 1 ? roster.slice(0, 3) : null;
    const overflow = roster.length - 3;
    const face = crowd
        ? `<span class="session-faces">${crowd.map(a => agentAvatarHTML(a, 20)).join('')}`
            + (overflow > 0 ? `<span class="session-face-more">+${overflow}</span>` : '')
            + `</span>`
        : `<i class="fas ${s.pinned ? 'fa-thumbtack' : 'fa-message'} session-icon"></i>`;
    const agentName = (s.agent && (s.agent.name || s.agent.id)) || t('agents_default');
    const projectName = s.project && s.project.name || t('ws_default_workspace');
    const time = _historyTimeLabel(s.last_active);
    item.innerHTML = `
        <button type="button" class="session-row-main">
            ${face}
            <span class="session-copy">
                <span class="session-title-line"><span class="session-title" title="${escapeHtml(title)}">${escapeHtml(title)}</span>
                    ${isActive ? `<span class="session-current-label">${escapeHtml(t('history_current'))}</span>` : ''}</span>
                <span class="session-meta">${escapeHtml(agentName)} · ${escapeHtml(projectName)}</span>
            </span>
        </button>
        <span class="session-time" title="${escapeHtml(time.full)}">${escapeHtml(time.text)}</span>
        <button type="button" class="session-more-btn" aria-haspopup="menu" aria-expanded="false"
                aria-label="${escapeHtml(t('history_more') + ': ' + title)}"><i class="fas fa-ellipsis" aria-hidden="true"></i></button>
    `;
    item.querySelector('.session-row-main').addEventListener('click', () => switchSession(s.session_id, ownerId || undefined));
    const more = item.querySelector('.session-more-btn');
    more.addEventListener('click', e => _openSessionActionMenu(e, s, more));
    return item;
}

// Pin / unpin, then re-render so the conversation moves to its new place.
// Reorder loaded sessions to match the backend's ordering (pinned first, then
// most-recently-active), so an optimistic pin/unpin lands in the right place
// without waiting for a reload. Stable within each bucket.
function _sortSessionItems() {
    _sessionItems.sort((a, b) => {
        const pa = a.pinned ? 1 : 0;
        const pb = b.pinned ? 1 : 0;
        if (pa !== pb) return pb - pa;
        return (b.last_active || 0) - (a.last_active || 0);
    });
}

function toggleSessionPin(sid, agentId) {
    const entry = _sessionItems.find(s => s.session_id === sid && (!agentId || (s.agent && s.agent.id) === agentId));
    if (!entry) return;
    const pinned = !entry.pinned;

    // Move it optimistically: the reorder is the whole point of the click, and
    // the list is re-rendered from this same data anyway. Pinning must also
    // reorder `_sessionItems` — the group renderer relies on the array already
    // being pinned-first, so flipping only the flag would leave a just-pinned
    // chat sitting in place (especially inside a project group).
    entry.pinned = pinned ? 1 : 0;
    _sortSessionItems();
    _renderSessionList();

    const owner = agentId || (entry.agent && entry.agent.id) || activeAgentId;
    fetch(`/api/sessions/${encodeURIComponent(sid)}?agent_id=${encodeURIComponent(owner || '')}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pinned, agent_id: owner }),
    })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'success') { _refreshHistoryList(); return; }
            // Most often an empty brand-new chat: it has no row to pin until the
            // first message is stored.
            _wsToast(data.message || t('session_settings_failed'));
            entry.pinned = pinned ? 0 : 1;
            _sortSessionItems();
            _renderSessionList();
        })
        .catch(() => {
            entry.pinned = pinned ? 0 : 1;
            _sortSessionItems();
            _renderSessionList();
        });
}

function _onSessionListScroll() {
    if (!_sessionHasMore || _sessionLoading || _historyPageFailed) return;
    const container = document.getElementById('session-list');
    if (!container) return;
    // Trigger when scrolled near the bottom (within 60px)
    if (container.scrollHeight - container.scrollTop - container.clientHeight < 60) {
        // Carry the current generation sequence so a page loading under a newer
        // read (or after re-entry) is dropped rather than appended to the wrong
        // list. A pagination failure is not fatal: keep what is confirmed and
        // allow the user to retry by scrolling again.
        _fetchSessionPage(_sessionPage + 1, false, undefined, _sessionReqSeq);
    }
}

// Attach scroll listener once DOM is ready
(function _initSessionScroll() {
    const el = document.getElementById('session-list');
    if (el) {
        el.addEventListener('scroll', _onSessionListScroll);
    } else {
        document.addEventListener('DOMContentLoaded', () => {
            const el2 = document.getElementById('session-list');
            if (el2) el2.addEventListener('scroll', _onSessionListScroll);
        });
    }
})();

// Returning to a session whose reply is still streaming in the background.
// Close the background EventSource, rebuild the bubble from the buffered
// events (snapshot), then resume live streaming via a fresh connection that
// reads the remaining tail from the backend replay log. Returns true if a stream
// was re-attached. The user's own bubble is already in history (persisted
// eagerly), so it was rendered by loadHistory before this runs.
function _reattachStream(sid) {
    const key = runtimeSessionKey(sid);
    const requestId = sessionActiveRequest[key];
    if (!requestId) return false;
    const buffer = streamBuffers[requestId];
    if (!buffer) return false;

    // If the buffered stream already finished, the assistant reply is already
    // persisted and rendered by loadHistory — re-attaching would duplicate it.
    // Just clean up the buffer/cursor and rely on history.
    const finished = buffer.items.some(
        it => it.type === 'stream_end' || it.type === 'error' || it.type === 'resync_required'
    );
    if (finished) {
        const oldEs = activeStreams[requestId];
        if (oldEs) { try { oldEs.close(); } catch (_) {} delete activeStreams[requestId]; }
        delete streamBuffers[requestId];
        delete sessionActiveRequest[key];
        resetSendBtnSendMode();
        return false;
    }

    // done already exists in persistent history. Keep the background tail
    // connected for voice_attach/stream_end, but do not replay the answer into
    // the freshly loaded history view or it would create a duplicate bubble.
    if (buffer.items.some(it => it.type === 'done')) {
        resetSendBtnSendMode();
        return false;
    }

    // Stop the background connection before rebuilding. Each new connection
    // resumes independently from its last accepted sequence number.
    const oldEs = activeStreams[requestId];
    if (oldEs) { try { oldEs.close(); } catch (_) {} delete activeStreams[requestId]; }

    // Snapshot the buffered events into the replay, then start a fresh stream
    // that replays them and reconnects for the live tail.
    const replay = buffer.items.slice();
    startSSE(requestId, null, buffer.timestamp || new Date(), null, replay);
    return true;
}

function switchSession(newSessionId, agentId) {
    // Carry the target across the guard: the identity/session flip must not
    // happen unless the navigation and the unsaved-editor check pass, so a
    // cancel keeps the current Agent and session untouched.
    if (newSessionId === sessionId && (!agentId || agentId === activeAgentId)) {
        if (currentView !== 'chat') navigateTo('chat');
        // Re-open a conversation whose previous history request did not load.
        if (!historyLoading && historyPage === 0) loadHistory(1);
        renderComposerIdentity();
        focusChatComposer();
        return;
    }

    // The preview panel is scoped to a session's workspace, so switching tears
    // down an open editor. Settle unsaved edits before committing to the switch.
    // Preserve the target agentId so a confirm re-runs with the same destination.
    if (typeof wsGuardUnsaved === 'function'
        && !wsGuardUnsaved(() => switchSession(newSessionId, agentId))) return;

    // Do NOT close active streams here: sessions run in parallel, so any
    // in-flight reply for another session must keep streaming in the
    // background (it self-guards against rendering into the foreign view).
    // Switching back re-attaches and resumes live streaming.

    // Commit the identity switch only after the guard passed.
    if (agentId && agentId !== activeAgentId) {
        activeAgentId = agentId;
        writeScopedPreference('cow_active_agent', activeAgentId);
    }

    sessionId = newSessionId;
    _sessCfg = null;
    _wsSelState = { current: null, recents: [], defaultWorkspace: '', projectsRoot: '' };
    _wsSelUpdateLabel();
    updateEditButtonsState();
    writeScopedPreference(activeSessionStorageKey(), sessionId);
    refreshWorkspaceSelector();
    refreshSessionSettings();
    // Reset the file/preview panel so it reflects the new session's root.
    if (typeof wsOnSessionSwitch === 'function') wsOnSessionSwitch();

    historyPage = 0;
    historyHasMore = false;
    historyLoading = false;

    messagesDiv.innerHTML = '';
    loadHistory(1);
    startPolling();

    // Restore the send button to match this session's stream state, and if a
    // reply is still streaming in the background, re-attach to resume showing
    // it live (the user turn itself comes from history above).
    const pendingReq = sessionActiveRequest[runtimeSessionKey(sessionId)];
    if (pendingReq) {
        setSendBtnCancelMode(pendingReq);
        _reattachStream(sessionId);
    } else {
        resetSendBtnSendMode();
    }

    document.querySelectorAll('.session-item').forEach(el => {
        el.classList.toggle('active', el.dataset.sessionId === sessionId
            && (!el.dataset.agentId || el.dataset.agentId === activeAgentId));
    });
    document.querySelectorAll('.sidebar-recent-item').forEach(el => {
        el.classList.toggle('active', el.dataset.sessionId === sessionId
            && (!el.dataset.agentId || el.dataset.agentId === activeAgentId));
    });

    if (currentView !== 'chat') navigateTo('chat');
    renderComposerIdentity();
    focusChatComposer();
}

// In-place rename a session title: replace the title <span> with an <input>,
// commit on Enter/blur, cancel on Escape. Persists via PUT /api/sessions/<id>.
function renameSession(sid, agentId) {
    const owner = agentId || activeAgentId;
    const same = s => s.session_id === sid && (!owner || (s.agent && s.agent.id) === owner);
    const item = [...document.querySelectorAll('.session-item')].find(el =>
        el.dataset.sessionId === sid && (!owner || el.dataset.agentId === owner));
    if (!item) return;
    const titleEl = item.querySelector('.session-title');
    if (!titleEl || item.querySelector('.session-title-input')) return;

    const oldTitle = titleEl.textContent;

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'session-title-input';
    input.value = oldTitle;
    input.maxLength = 100;
    input.setAttribute('aria-label', t('rename_session'));

    // Keep the editor outside a button while retaining the original button's
    // click listener when the edit finishes.
    const mainButton = item.querySelector('.session-row-main');
    const editWrap = document.createElement('div');
    editWrap.className = 'session-row-main';
    while (mainButton.firstChild) editWrap.appendChild(mainButton.firstChild);
    mainButton.replaceWith(editWrap);

    // Avoid switching session while interacting with the input
    const stop = e => e.stopPropagation();
    input.addEventListener('click', stop);
    input.addEventListener('mousedown', stop);

    titleEl.replaceWith(input);
    input.focus();
    input.select();

    let done = false;

    const restore = (title, refreshDeferred = true) => {
        if (done) return;
        done = true;
        const span = document.createElement('span');
        span.className = 'session-title';
        span.title = title;
        span.textContent = title;
        input.replaceWith(span);
        while (editWrap.firstChild) mainButton.appendChild(editWrap.firstChild);
        editWrap.replaceWith(mainButton);
        if (_historyDirty && refreshDeferred) { _historyDirty = false; _refreshHistoryList(); }
    };

    // Undo the optimistic rename in both the DOM and the cached entry.
    const revert = () => {
        const cachedEntry = _sessionItems.find(same);
        if (cachedEntry) cachedEntry.title = oldTitle;
        const span = item.querySelector('.session-title');
        if (span) {
            span.title = oldTitle;
            span.textContent = oldTitle;
        }
        _refreshHistoryList();
    };

    const commit = () => {
        if (done) return;
        const newTitle = input.value.trim();
        if (!newTitle || newTitle === oldTitle) {
            restore(oldTitle);
            return;
        }
        // Optimistically show the new title, then persist. The cached entry is
        // updated too, or the next re-render (a pin, say) would revive the old one.
        restore(newTitle, false);
        const cached = _sessionItems.find(same);
        if (cached) cached.title = newTitle;
        fetch(`/api/sessions/${encodeURIComponent(sid)}?agent_id=${encodeURIComponent(owner || '')}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: newTitle, agent_id: owner })
        })
            .then(r => r.json())
            .then(data => {
                if (data.status !== 'success') { revert(); _wsToast(data.message || t('session_settings_failed')); }
                else _refreshHistoryList();
            })
            .catch(revert);
    };

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); commit(); }
        else if (e.key === 'Escape') { e.preventDefault(); restore(oldTitle); }
    });
    input.addEventListener('blur', commit);
}

function deleteSession(sid, agentId) {
    showConfirmModal(t('delete_session_title'), t('delete_session_confirm'), () => {
        const owner = agentId || activeAgentId;
        const deletingCurrent = sid === sessionId && (!owner || owner === activeAgentId);
        const next = deletingCurrent ? _findNextSession(sid, owner) : null;

        fetch(`/api/sessions/${encodeURIComponent(sid)}?agent_id=${encodeURIComponent(owner || '')}`, { method: 'DELETE' })
            .then(r => r.json())
            .then(data => {
                if (data.status !== 'success') { _wsToast(data.message || t('session_settings_failed')); return; }
                if (!deletingCurrent) {
                    _refreshHistoryList();
                    return;
                }
                if (next) {
                    switchSession(next.sessionId, next.agentId);
                    _refreshHistoryList();
                } else {
                    newChat(false);
                }
            })
            .catch(() => _wsToast(t('session_settings_failed')));
    });
}

// Pick the session to show after deleting `sid` (the current session): prefer
// the next item below it in the list, otherwise the previous one. Returns null
// if no other session exists.
function _findNextSession(sid, agentId) {
    const items = Array.from(document.querySelectorAll('.session-item[data-session-id]'));
    const same = el => el.dataset.sessionId === sid && (!agentId || el.dataset.agentId === agentId);
    const idx = items.findIndex(same);
    const pick = el => el ? { sessionId: el.dataset.sessionId, agentId: el.dataset.agentId || '' } : null;
    if (idx === -1) {
        return pick(items.find(el => !same(el)));
    }
    return pick(items[idx + 1] || items[idx - 1]);
}

function showConfirmModal(title, message, onConfirm) {
    let overlay = document.getElementById('confirm-modal-overlay');
    if (overlay) overlay.remove();

    overlay = document.createElement('div');
    overlay.id = 'confirm-modal-overlay';
    overlay.className = 'confirm-overlay';

    const modal = document.createElement('div');
    modal.className = 'confirm-modal';
    modal.innerHTML = `
        <div class="confirm-title">${escapeHtml(title)}</div>
        <div class="confirm-message">${escapeHtml(message)}</div>
        <div class="confirm-actions">
            <button class="confirm-btn confirm-btn-cancel">${t('confirm_cancel')}</button>
            <button class="confirm-btn confirm-btn-ok">${t('confirm_yes')}</button>
        </div>
    `;
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    requestAnimationFrame(() => overlay.classList.add('visible'));

    const close = () => {
        overlay.classList.remove('visible');
        setTimeout(() => overlay.remove(), 200);
    };

    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
    modal.querySelector('.confirm-btn-cancel').addEventListener('click', close);
    modal.querySelector('.confirm-btn-ok').addEventListener('click', () => {
        close();
        onConfirm();
    });
}

// A confirm modal with a single text input. Calls onSubmit(value) on OK, and
// does nothing on cancel. Mirrors showConfirmModal's look and lifecycle.
function showPromptModal(title, initialValue, onSubmit) {
    let overlay = document.getElementById('confirm-modal-overlay');
    if (overlay) overlay.remove();

    overlay = document.createElement('div');
    overlay.id = 'confirm-modal-overlay';
    overlay.className = 'confirm-overlay';

    const modal = document.createElement('div');
    modal.className = 'confirm-modal';
    modal.innerHTML = `
        <div class="confirm-title">${escapeHtml(title)}</div>
        <input type="text" class="prompt-modal-input" maxlength="100" />
        <div class="confirm-actions">
            <button class="confirm-btn confirm-btn-cancel">${t('confirm_cancel')}</button>
            <button class="confirm-btn confirm-btn-ok">${t('confirm_yes')}</button>
        </div>
    `;
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    const input = modal.querySelector('.prompt-modal-input');
    input.value = initialValue || '';
    requestAnimationFrame(() => { overlay.classList.add('visible'); input.focus(); input.select(); });

    const close = () => {
        overlay.classList.remove('visible');
        setTimeout(() => overlay.remove(), 200);
    };
    const submit = () => { const v = input.value.trim(); close(); onSubmit(v); };

    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
    modal.querySelector('.confirm-btn-cancel').addEventListener('click', close);
    modal.querySelector('.confirm-btn-ok').addEventListener('click', submit);
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); submit(); }
        else if (e.key === 'Escape') { e.preventDefault(); close(); }
    });
}

function clearContext() {
    fetch(`/api/sessions/${encodeURIComponent(sessionId)}/clear_context`, { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'success') return;
            // Insert a visual divider in the chat
            const divider = document.createElement('div');
            divider.className = 'context-divider';
            divider.innerHTML = `<span>${t('context_cleared')}</span>`;
            messagesDiv.appendChild(divider);
            scrollChatToBottom();
        })
        .catch(() => {});
}

function generateSessionTitle(sid, userMsg, assistantReply) {
    fetch(`/api/sessions/${encodeURIComponent(sid)}/generate_title`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_message: userMsg, assistant_reply: assistantReply }),
    })
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'success') return;
            // The list only exists on the history page now; refresh it if it is
            // the active view, otherwise mark it dirty so the next visit re-reads
            // the freshly generated title.
            if (_historyVisible) loadSessionList();
            else _historyDirty = true;
        })
        .catch(() => {});
}

// =====================================================================
// Utilities
// =====================================================================
function formatTime(date) {
    const now = new Date();
    const sameDay = date.getFullYear() === now.getFullYear()
        && date.getMonth() === now.getMonth()
        && date.getDate() === now.getDate();
    const time = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    if (sameDay) return time;
    const m = String(date.getMonth() + 1).padStart(2, '0');
    const d = String(date.getDate()).padStart(2, '0');
    if (date.getFullYear() === now.getFullYear()) return `${m}-${d} ${time}`;
    return `${date.getFullYear()}-${m}-${d} ${time}`;
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
}

function ChannelsHandler_maskSecret(val) {
    if (!val || val.length <= 8) return val;
    return val.slice(0, 4) + '*'.repeat(val.length - 8) + val.slice(-4);
}

function formatToolArgs(args) {
    if (!args || Object.keys(args).length === 0) return '(none)';
    try {
        return escapeHtml(JSON.stringify(args, null, 2));
    } catch (_) {
        return escapeHtml(String(args));
    }
}

const SUBSTEP_ARGS_CHARS = 90;

/** Tool arguments on one line, for a step in a list of dozens. */
function summarizeToolArgs(args) {
    if (!args || typeof args !== 'object') return '';
    const parts = [];
    for (const [key, value] of Object.entries(args)) {
        const text = typeof value === 'object' ? JSON.stringify(value) : String(value);
        parts.push(`${key}=${text}`);
    }
    const joined = parts.join(', ');
    return joined.length > SUBSTEP_ARGS_CHARS
        ? joined.slice(0, SUBSTEP_ARGS_CHARS) + '…'
        : joined;
}

/**
 * Add or settle one step inside a sub agent's card.
 *
 * Silent when the card is gone: a sub agent cancelled on timeout keeps working
 * until its next checkpoint, and steps that arrive after its card closed
 * describe work nobody is waiting on any more.
 */
function renderSubagentStep(toolEl, item) {
    if (!toolEl || !item.step_id) return;
    const section = toolEl.querySelector('.tool-substeps-section');
    const list = toolEl.querySelector('.tool-substeps');
    if (!section || !list) return;

    let stepEl = list.querySelector(`[data-step-id="${CSS.escape(item.step_id)}"]`);
    if (!stepEl) {
        if (item.phase !== 'start') return;
        stepEl = document.createElement('div');
        stepEl.className = 'tool-substep';
        stepEl.dataset.stepId = item.step_id;
        stepEl.innerHTML = `
            <i class="fas fa-circle-notch fa-spin tool-substep-icon"></i>
            <span class="tool-substep-name">${escapeHtml(item.tool || 'tool')}</span>
            <span class="tool-substep-args">${escapeHtml(summarizeToolArgs(item.arguments))}</span>
            <span class="tool-substep-time"></span>`;
        list.appendChild(stepEl);
        section.classList.remove('hidden');
        // The first step is also the first sign of life from a sub agent that
        // runs for minutes, so it opens the card it belongs to.
        toolEl.classList.add('expanded');
        updateSubstepCount(toolEl, list.children.length);
        return;
    }

    if (item.phase !== 'end') return;
    const isError = item.status && item.status !== 'success';
    const icon = stepEl.querySelector('.tool-substep-icon');
    if (icon) {
        icon.className = isError
            ? 'fas fa-times tool-substep-icon tool-substep-failed'
            : 'fas fa-check tool-substep-icon';
    }
    const timeEl = stepEl.querySelector('.tool-substep-time');
    if (timeEl && item.execution_time) timeEl.textContent = `${item.execution_time}s`;
    if (item.error) {
        // A step that failed says so where it happened; the sub agent's report
        // covers what the successful ones found.
        const argsEl = stepEl.querySelector('.tool-substep-args');
        if (argsEl) {
            argsEl.textContent = String(item.error);
            argsEl.classList.add('tool-substep-failed');
        }
        stepEl.title = String(item.error);
    }
}

function updateSubstepCount(toolEl, count) {
    const countEl = toolEl.querySelector('.tool-substep-count');
    if (countEl) countEl.textContent = count === 1 ? '1 step' : `${count} steps`;
}

function scrollChatToBottom(force) {
    if (force || _autoScrollEnabled) {
        messagesDiv.scrollTop = messagesDiv.scrollHeight;
    }
}

function _updateScrollToBottomBtn() {
    const btn = document.getElementById('scroll-to-bottom-btn');
    if (!btn) return;
    const distFromBottom = messagesDiv.scrollHeight - messagesDiv.scrollTop - messagesDiv.clientHeight;
    btn.classList.toggle('hidden', distFromBottom <= _SCROLL_THRESHOLD);
}

function applyHighlighting(container) {
    const root = container || document;
    setTimeout(() => {
        const hljsLib = getHljs();
        root.querySelectorAll('pre code').forEach(block => {
            if (!block.classList.contains('hljs')) {
                hljsLib.highlightElement(block);
            }
        });
        // Add language labels and copy buttons to code blocks
        _addCodeBlockHeaders(root);
    }, 0);
}

// =====================================================================
// Config View
// =====================================================================
let configProviders = {};
let configApiBases = {};
let configApiKeys = {};
let configCurrentModel = '';
let cfgProviderValue = '';
let cfgModelValue = '';
let cfgReasoningEffortValue = 'high';
let configReasoningByModel = {};
// Remembers the custom model name the user typed per provider, so switching
// away from a provider (which rebuilds its model dropdown) and back does not
// lose an unsaved custom model. Keyed by provider id.
let configCustomModelByProvider = {};
// Same idea for the Models tab capability cards: remember the custom model the
// user typed per (capability, provider) and the provider active before the
// last switch, so switching vendors and back restores the custom model.
// Keyed by `${capabilityId}:${providerId}` -> custom model string.
let capabilityCustomModelMemory = {};
// Keyed by capabilityId -> provider id active before the current switch.
let capabilityLastProviderId = {};

// --- Custom dropdown helper ---
function initDropdown(el, options, selectedValue, onChange, opts) {
    // opts.placeholder: when set AND selectedValue is empty, render that text
    // in a dim style instead of auto-selecting options[0]. Useful for
    // "pick or empty" capabilities (asr / embedding) where we want the
    // user to make an explicit choice.
    opts = opts || {};
    const textEl = el.querySelector('.cfg-dropdown-text');
    const menuEl = el.querySelector('.cfg-dropdown-menu');
    const selEl = el.querySelector('.cfg-dropdown-selected');
    // Optional avatar face in the trigger (opts.withAvatar). Each option then
    // carries an `agent` object so both the row and the trigger can paint it.
    const faceEl = el.querySelector('.cfg-dropdown-face');

    el._ddValue = selectedValue || '';
    el._ddOnChange = onChange;

    function paintFace(opt) {
        if (!faceEl) return;
        faceEl.innerHTML = (opt && opt.agent) ? agentAvatarHTML(opt.agent, 20) : '';
    }

    function render() {
        menuEl.innerHTML = '';
        options.forEach(opt => {
            const item = document.createElement('div');
            item.className = 'cfg-dropdown-item' + (opt.value === el._ddValue ? ' active' : '');
            item.dataset.value = opt.value;
            // Hint is an optional dim secondary label rendered on the right
            // side of the row (e.g. friendly brand name next to a technical
            // model id). When absent the row degrades to the original
            // single-string layout.
            if (opt.agent) {
                const face = document.createElement('span');
                face.className = 'cfg-dropdown-item-face';
                face.innerHTML = agentAvatarHTML(opt.agent, 20);
                const labelEl = document.createElement('span');
                labelEl.className = 'cfg-dropdown-label';
                labelEl.textContent = opt.label;
                item.appendChild(face);
                item.appendChild(labelEl);
                // Optional trailing pill (e.g. a "default" marker) rendered
                // dim after the name.
                if (opt.badge) {
                    const badgeEl = document.createElement('span');
                    badgeEl.className = 'cfg-dropdown-badge';
                    badgeEl.textContent = opt.badge;
                    item.appendChild(badgeEl);
                }
            } else if (opt.hint) {
                const labelEl = document.createElement('span');
                labelEl.className = 'cfg-dropdown-label';
                labelEl.textContent = opt.label;
                const hintEl = document.createElement('span');
                hintEl.className = 'cfg-dropdown-hint';
                hintEl.textContent = opt.hint;
                item.appendChild(labelEl);
                item.appendChild(hintEl);
            } else {
                item.textContent = opt.label;
            }
            item.addEventListener('click', (e) => {
                e.stopPropagation();
                el._ddValue = opt.value;
                textEl.textContent = opt.label;
                // Now that a real option is picked, drop the muted placeholder
                // style — otherwise the chosen label stays grey (visible on
                // dropdowns that start in a placeholder state, e.g. the chat
                // fallback pickers).
                textEl.classList.remove('text-slate-400', 'dark:text-slate-500');
                paintFace(opt);
                menuEl.querySelectorAll('.cfg-dropdown-item').forEach(i => i.classList.remove('active'));
                item.classList.add('active');
                el.classList.remove('open');
                if (el._ddOnChange) el._ddOnChange(opt.value);
            });
            menuEl.appendChild(item);
        });
        const sel = options.find(o => o.value === el._ddValue);
        if (sel) {
            textEl.textContent = sel.label;
            paintFace(sel);
            textEl.classList.remove('text-slate-400', 'dark:text-slate-500');
        } else if (opts.placeholder && !el._ddValue) {
            // No selection yet — show the placeholder in muted style.
            // Do NOT write a fallback value, so the dropdown stays
            // "unsaved" until the user explicitly picks.
            textEl.textContent = opts.placeholder;
            paintFace(null);
            textEl.classList.add('text-slate-400', 'dark:text-slate-500');
        } else {
            textEl.textContent = options[0] ? options[0].label : '--';
            paintFace(options[0]);
            textEl.classList.remove('text-slate-400', 'dark:text-slate-500');
            if (options[0]) el._ddValue = options[0].value;
        }
    }

    render();

    if (!el._ddBound) {
        selEl.addEventListener('click', (e) => {
            e.stopPropagation();
            document.querySelectorAll('.cfg-dropdown.open').forEach(d => { if (d !== el) d.classList.remove('open'); });
            const willOpen = !el.classList.contains('open');
            if (willOpen) {
                // Flip the menu above the control when it would otherwise be
                // clipped against the viewport bottom (e.g. the last channel's
                // config dropdown sitting near the window edge).
                const rect = el.getBoundingClientRect();
                const below = window.innerHeight - rect.bottom;
                const menuH = Math.min(menuEl.scrollHeight || 240, 280) + 8;
                el.classList.toggle('drop-up', below < menuH && rect.top > below);
            }
            el.classList.toggle('open');
        });
        el._ddBound = true;
    }
}

document.addEventListener('click', () => {
    document.querySelectorAll('.cfg-dropdown.open').forEach(d => d.classList.remove('open'));
});

function getDropdownValue(el) { return el._ddValue || ''; }

// --- Config init ---
function initConfigView(data) {
    configProviders = data.providers || {};
    configApiBases = data.api_bases || {};
    configApiKeys = data.api_keys || {};
    configCurrentModel = data.model || '';
    configReasoningByModel = data.reasoning_effort_by_model || {};
    cfgReasoningEffortValue = data.reasoning_effort || 'high';

    const providerEl = document.getElementById('cfg-provider');
    const providerOpts = Object.entries(configProviders).map(([pid, p]) => ({ value: pid, label: localizedLabel(p.label) }));

    // if use_linkai is enabled, always select linkai as the provider
    // Otherwise prefer bot_type from config, fall back to model-based detection
    const detected = data.use_linkai ? 'linkai'
        : (data.bot_type && configProviders[data.bot_type] ? data.bot_type : detectProvider(configCurrentModel));
    cfgProviderValue = detected || (providerOpts[0] ? providerOpts[0].value : '');

    initDropdown(providerEl, providerOpts, cfgProviderValue, onProviderChange);

    onProviderChange(cfgProviderValue);
    syncModelSelection(configCurrentModel);

    document.getElementById('cfg-max-tokens').value = data.agent_max_context_tokens || 50000;
    document.getElementById('cfg-max-turns').value = data.agent_max_context_turns || 20;
    document.getElementById('cfg-max-steps').value = data.agent_max_steps || 20;
    const thinkingEl = document.getElementById('cfg-enable-thinking');
    thinkingEl.checked = data.enable_thinking === true;
    if (!thinkingEl._cfgReasoningBound) {
        thinkingEl.addEventListener('change', syncReasoningEffortOptions);
        thinkingEl._cfgReasoningBound = true;
    }
    const customModelEl = document.getElementById('cfg-model-custom');
    if (customModelEl && !customModelEl._cfgReasoningBound) {
        customModelEl.addEventListener('input', () => {
            // Remember the typed custom model for the current provider so a
            // provider switch and switch-back doesn't lose it.
            if (cfgModelValue === '__custom__') {
                configCustomModelByProvider[cfgProviderValue] = customModelEl.value.trim();
            }
            syncReasoningEffortOptions();
        });
        customModelEl._cfgReasoningBound = true;
    }
    syncReasoningEffortOptions();
    document.getElementById('cfg-subagent').checked = data.subagent_enabled !== false;
    document.getElementById('cfg-self-evolution').checked = data.self_evolution_enabled === true;

    // Reflect the current UI language (already resolved, may include the user's
    // local choice) on the selector so it stays in sync with the top-right toggle.
    const langSel = document.getElementById('cfg-lang-select');
    if (langSel) {
        initDropdown(
            langSel,
            [{ value: 'zh', label: '简体中文' }, { value: 'zh-Hant', label: '繁體中文' }, { value: 'en', label: 'English' }],
            currentLang,
            (val) => setLanguage(val)
        );
    }

    // Default permission mode for new conversations. Applied on pick, like the
    // language selector: the card's save button belongs to the password field,
    // and a security default that silently waited for a save would be worse than
    // one that takes effect immediately.
    const permEl = document.getElementById('cfg-permission');
    if (permEl) {
        const offered = data.permission_modes && data.permission_modes.length
            ? data.permission_modes
            : Object.keys(PERMISSION_META);
        const permOpts = Object.keys(PERMISSION_META)
            .filter(mode => offered.includes(mode))
            .map(mode => ({ value: mode, label: t(PERMISSION_META[mode].key) }));
        initDropdown(permEl, permOpts, data.agent_permission_mode || 'full-access', saveGlobalPermission);
    }

    const pwdInput = document.getElementById('cfg-password');
    const maskedPwd = data.web_password_masked || '';
    pwdInput.value = maskedPwd;
    pwdInput.dataset.masked = maskedPwd ? '1' : '';
    pwdInput.dataset.maskedVal = maskedPwd;
    pwdInput.classList.toggle('cfg-key-masked', !!maskedPwd);

    if (maskedPwd) {
        pwdInput.placeholder = '••••••••';
    } else {
        pwdInput.placeholder = '';
    }

    if (!pwdInput._cfgBound) {
        pwdInput.addEventListener('focus', function() {
            if (this.dataset.masked === '1') {
                this.value = '';
                this.dataset.masked = '';
                this.classList.remove('cfg-key-masked');
            }
        });
        pwdInput.addEventListener('input', function() {
            this.dataset.masked = '';
        });
        pwdInput._cfgBound = true;
    }
}

function detectProvider(model) {
    if (!model) return Object.keys(configProviders)[0] || '';
    for (const [pid, p] of Object.entries(configProviders)) {
        if (pid === 'linkai') continue;
        if (p.models && p.models.includes(model)) return pid;
    }
    return Object.keys(configProviders)[0] || '';
}

function onProviderChange(pid) {
    cfgProviderValue = pid || getDropdownValue(document.getElementById('cfg-provider'));
    const p = configProviders[cfgProviderValue];
    if (!p) return;

    const customTip = document.getElementById('cfg-custom-tip');
    if (customTip) customTip.classList.toggle('hidden', cfgProviderValue !== 'custom');

    const modelEl = document.getElementById('cfg-model-select');
    const modelOpts = (p.models || []).map(m => ({ value: m, label: m }));
    modelOpts.push({ value: '__custom__', label: t('config_custom_option') });

    // Restore a custom model the user typed for this provider earlier in the
    // session (kept in configCustomModelByProvider). Fall back to the first
    // preset. For a custom provider with no preset models the picker only has
    // the "__custom__" entry, so a remembered value is the only way its model
    // survives a provider switch.
    const rememberedCustom = configCustomModelByProvider[cfgProviderValue];
    const initialModelValue = rememberedCustom
        ? '__custom__'
        : (modelOpts[0] ? modelOpts[0].value : '');

    initDropdown(modelEl, modelOpts, initialModelValue, onModelSelectChange);

    // API Key
    const keyField = p.api_key_field;
    const keyWrap = document.getElementById('cfg-api-key-wrap');
    const keyInput = document.getElementById('cfg-api-key');

    // Only LinkAI (an aggregation platform) gets a link to its console for
    // managing the aggregated key; other providers manage keys on their sites.
    const cfgManageKey = document.getElementById('cfg-manage-key');
    if (cfgManageKey) cfgManageKey.classList.toggle('hidden', cfgProviderValue !== 'linkai');
    if (keyField) {
        keyWrap.classList.remove('hidden');
        keyInput.classList.add('cfg-key-masked');
        const maskedVal = configApiKeys[keyField] || '';
        keyInput.value = maskedVal;
        keyInput.dataset.field = keyField;
        keyInput.dataset.masked = maskedVal ? '1' : '';
        keyInput.dataset.maskedVal = maskedVal;
        const toggleIcon = document.querySelector('#cfg-api-key-toggle i');
        if (toggleIcon) toggleIcon.className = 'fas fa-eye text-xs';

        if (!keyInput._cfgBound) {
            keyInput.addEventListener('focus', function() {
                if (this.dataset.masked === '1') {
                    this.value = '';
                    this.dataset.masked = '';
                    this.classList.remove('cfg-key-masked');
                }
            });
            keyInput.addEventListener('blur', function() {
                if (!this.value.trim() && this.dataset.maskedVal) {
                    this.value = this.dataset.maskedVal;
                    this.dataset.masked = '1';
                    this.classList.add('cfg-key-masked');
                }
            });
            keyInput.addEventListener('input', function() {
                this.dataset.masked = '';
            });
            keyInput._cfgBound = true;
        }
    } else {
        keyWrap.classList.add('hidden');
        keyInput.value = '';
        keyInput.dataset.field = '';
    }

    // API Base
    const apiBaseInput = document.getElementById('cfg-api-base');
    if (p.api_base_key) {
        document.getElementById('cfg-api-base-wrap').classList.remove('hidden');
        apiBaseInput.value = configApiBases[p.api_base_key] || p.api_base_default || '';
        // Hint the version-path tail (e.g. /v1) so users are reminded to
        // include it themselves. We don't auto-rewrite anything server-side.
        apiBaseInput.placeholder = p.api_base_placeholder || 'https://...';
    } else {
        document.getElementById('cfg-api-base-wrap').classList.add('hidden');
        apiBaseInput.value = '';
        apiBaseInput.placeholder = 'https://...';
    }

    onModelSelectChange(initialModelValue, { restoredCustom: rememberedCustom });
    syncReasoningEffortOptions();
}

function onModelSelectChange(val, opts) {
    opts = opts || {};
    cfgModelValue = val || getDropdownValue(document.getElementById('cfg-model-select'));
    const customWrap = document.getElementById('cfg-model-custom-wrap');
    const customInput = document.getElementById('cfg-model-custom');
    if (cfgModelValue === '__custom__') {
        customWrap.classList.remove('hidden');
        // When switching back to a provider we restore the remembered value;
        // otherwise this is a fresh pick of "custom" and we focus for input.
        if (opts.restoredCustom) {
            customInput.value = opts.restoredCustom;
        } else {
            customInput.focus();
        }
    } else {
        customWrap.classList.add('hidden');
        customInput.value = '';
    }
    syncReasoningEffortOptions();
}

function syncModelSelection(model) {
    const p = configProviders[cfgProviderValue];
    if (!p) return;

    const modelEl = document.getElementById('cfg-model-select');
    if (p.models && p.models.includes(model)) {
        const modelOpts = (p.models || []).map(m => ({ value: m, label: m }));
        modelOpts.push({ value: '__custom__', label: t('config_custom_option') });
        initDropdown(modelEl, modelOpts, model, onModelSelectChange);
        cfgModelValue = model;
        document.getElementById('cfg-model-custom-wrap').classList.add('hidden');
    } else {
        cfgModelValue = '__custom__';
        const modelOpts = (p.models || []).map(m => ({ value: m, label: m }));
        modelOpts.push({ value: '__custom__', label: t('config_custom_option') });
        initDropdown(modelEl, modelOpts, '__custom__', onModelSelectChange);
        document.getElementById('cfg-model-custom-wrap').classList.remove('hidden');
        document.getElementById('cfg-model-custom').value = model;
        // Seed the per-provider memory so switching away and back keeps it.
        if (model) configCustomModelByProvider[cfgProviderValue] = model;
    }
    syncReasoningEffortOptions();
}

function syncReasoningEffortOptions() {
    const wrap = document.getElementById('cfg-reasoning-effort-wrap');
    const el = document.getElementById('cfg-reasoning-effort');
    if (!wrap || !el) return;

    const provider = configProviders[cfgProviderValue] || {};
    const selectedModel = getSelectedModel();
    const reasoningByModel = provider.reasoning_by_model || {};
    const reasoning = reasoningByModel[selectedModel] || provider.reasoning || {};
    const options = reasoning.supported ? (reasoning.options || []) : [];
    const thinkingEl = document.getElementById('cfg-enable-thinking');

    if (options.length) {
        const values = options.map(opt => opt.value);
        // Prefer this model's own saved effort (per-model config) so switching
        // vendors never reinterprets a value set for a different model. Key is
        // the lowercased model name, matching the backend resolve path.
        const savedForModel = configReasoningByModel[`${cfgProviderValue}:${selectedModel.trim().toLowerCase()}`]
            || configReasoningByModel[cfgProviderValue + ':' + selectedModel];
        const saved = savedForModel || cfgReasoningEffortValue;
        // Fall back to the active model's native enum when the saved value is
        // not valid here. Resolved even while hidden so a save never writes
        // another model's enum under this model's key.
        cfgReasoningEffortValue = values.includes(saved) ? saved : (reasoning.default || options[0].value);
    }

    // Effort only shapes a thinking pass, so the field follows the toggle.
    if (!thinkingEl || !thinkingEl.checked || !options.length) {
        wrap.classList.add('hidden');
        return;
    }

    wrap.classList.remove('hidden');
    initDropdown(
        el,
        options.map(opt => ({ value: opt.value, label: opt.label || opt.value })),
        cfgReasoningEffortValue,
        (val) => { cfgReasoningEffortValue = val; }
    );
}

function getSelectedModel() {
    if (cfgModelValue === '__custom__') {
        return document.getElementById('cfg-model-custom').value.trim();
    }
    return cfgModelValue;
}

function toggleApiKeyVisibility() {
    const input = document.getElementById('cfg-api-key');
    const icon = document.querySelector('#cfg-api-key-toggle i');
    if (input.classList.contains('cfg-key-masked')) {
        input.classList.remove('cfg-key-masked');
        icon.className = 'fas fa-eye-slash text-xs';
    } else {
        input.classList.add('cfg-key-masked');
        icon.className = 'fas fa-eye text-xs';
    }
}

function showStatus(elId, msgKey, isError) {
    const el = document.getElementById(elId);
    el.textContent = t(msgKey);
    el.classList.toggle('text-red-500', !!isError);
    el.classList.toggle('text-primary-500', !isError);
    el.classList.remove('opacity-0');
    // Warning messages (errors) should stay visible, success messages auto-hide
    if (!isError) {
        setTimeout(() => el.classList.add('opacity-0'), 2500);
    }
}

function saveModelConfig() {
    const model = getSelectedModel();
    if (!model) return;

    const updates = { model: model };
    const p = configProviders[cfgProviderValue];
    updates.use_linkai = (cfgProviderValue === 'linkai');
    if (cfgProviderValue === 'linkai') {
        updates.bot_type = '';
    } else {
        updates.bot_type = cfgProviderValue;
    }
    if (p && p.api_base_key) {
        const base = document.getElementById('cfg-api-base').value.trim();
        if (base) updates[p.api_base_key] = base;
    }
    if (p && p.api_key_field) {
        const keyInput = document.getElementById('cfg-api-key');
        const rawVal = keyInput.value.trim();
        if (rawVal && keyInput.dataset.masked !== '1') {
            updates[p.api_key_field] = rawVal;
        }
    }

    const btn = document.getElementById('cfg-model-save');
    btn.disabled = true;
    fetch('/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ updates })
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            configCurrentModel = model;
            if (data.applied) {
                const keyInput = document.getElementById('cfg-api-key');
                Object.entries(data.applied).forEach(([k, v]) => {
                    if (k === 'model') return;
                    if (k.includes('api_key')) {
                        const masked = v.length > 8
                            ? v.substring(0, 4) + '*'.repeat(v.length - 8) + v.substring(v.length - 4)
                            : v;
                        configApiKeys[k] = masked;
                        if (keyInput.dataset.field === k) {
                            keyInput.value = masked;
                            keyInput.dataset.masked = '1';
                            keyInput.dataset.maskedVal = masked;
                            keyInput.classList.add('cfg-key-masked');
                            const toggleIcon = document.querySelector('#cfg-api-key-toggle i');
                            if (toggleIcon) toggleIcon.className = 'fas fa-eye text-xs';
                        }
                    } else {
                        configApiBases[k] = v;
                    }
                });
            }
            showStatus('cfg-model-status', 'config_saved', false);
        } else {
            showStatus('cfg-model-status', 'config_save_error', true);
        }
    })
    .catch(() => showStatus('cfg-model-status', 'config_save_error', true))
    .finally(() => { btn.disabled = false; });
}

function saveAgentConfig() {
    const effortKey = `${cfgProviderValue}:${getSelectedModel().trim().toLowerCase()}`;
    const mergedEffortByModel = Object.assign({}, configReasoningByModel, { [effortKey]: cfgReasoningEffortValue });
    const updates = {
        agent_max_context_tokens: parseInt(document.getElementById('cfg-max-tokens').value) || 50000,
        agent_max_context_turns: parseInt(document.getElementById('cfg-max-turns').value) || 20,
        agent_max_steps: parseInt(document.getElementById('cfg-max-steps').value) || 20,
        enable_thinking: document.getElementById('cfg-enable-thinking').checked,
        // Persist effort per model (merge with the existing map so other
        // models' saved efforts survive the flat config save).
        reasoning_effort_by_model: mergedEffortByModel,
        subagent_enabled: document.getElementById('cfg-subagent').checked,
        self_evolution_enabled: document.getElementById('cfg-self-evolution').checked,
    };

    const btn = document.getElementById('cfg-agent-save');
    btn.disabled = true;
    fetch('/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ updates })
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            // Reflect the merged map so a later model switch shows/uses the
            // just-saved value instead of a stale in-memory one.
            configReasoningByModel = mergedEffortByModel;
            showStatus('cfg-agent-status', 'config_saved', false);
        } else {
            showStatus('cfg-agent-status', 'config_save_error', true);
        }
    })
    .catch(() => showStatus('cfg-agent-status', 'config_save_error', true))
    .finally(() => { btn.disabled = false; });
}

// Persist the instance-wide default permission mode. Sessions that never pinned
// their own follow it, so the composer chip is refreshed afterwards.
function saveGlobalPermission(mode) {
    fetch('/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ updates: { agent_permission_mode: mode } })
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            showStatus('cfg-password-status', 'config_saved', false);
            refreshSessionSettings();
        } else {
            showStatus('cfg-password-status', 'config_save_error', true);
        }
    })
    .catch(() => showStatus('cfg-password-status', 'config_save_error', true));
}

function savePasswordConfig() {
    const input = document.getElementById('cfg-password');
    if (input.dataset.masked === '1') {
        showStatus('cfg-password-status', 'config_saved', false);
        return;
    }
    const newPwd = input.value.trim();
    const btn = document.getElementById('cfg-password-save');
    btn.disabled = true;
    fetch('/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ updates: { web_password: newPwd } })
    })
    .then(r => r.json())
    .then(data => {
        console.log('[Password Config] Response:', data); // Debug
        if (data.status === 'success') {
            refreshAccountIdentity();
            if (newPwd) {
                showStatus('cfg-password-status', 'config_password_changed', false);
                // Mark as masked so user needs to re-enter to change again
                input.dataset.masked = '1';
                input.dataset.maskedVal = newPwd;
                input.value = '••••••••';
                input.classList.add('cfg-key-masked');
                
                // Show logout button since password is now enabled
                const logoutBtn = document.getElementById('logout-btn-header');
                if (logoutBtn) logoutBtn.classList.remove('hidden');
            } else {
                input.dataset.masked = '';
                input.dataset.maskedVal = '';
                input.classList.remove('cfg-key-masked');
                
                // Show security warning if password was cleared with public host
                if (data.warning === 'password_cleared_with_public_host') {
                    showStatus('cfg-password-status', 'config_password_security_warning', true);
                } else {
                    showStatus('cfg-password-status', 'config_password_cleared', false);
                }
                
                const logoutBtn = document.getElementById('logout-btn-header');
                if (logoutBtn) logoutBtn.classList.add('hidden');
            }
        } else {
            showStatus('cfg-password-status', 'config_save_error', true);
        }
    })
    .catch(() => showStatus('cfg-password-status', 'config_save_error', true))
    .finally(() => { btn.disabled = false; });
}

function loadConfigView() {
    fetch('/config').then(r => r.json()).then(data => {
        if (data.status !== 'success') return;
        appConfig = data;
        initConfigView(data);
    }).catch(() => {});
}

function switchConfigTab(tab) {
    ['basic', 'models'].forEach(name => {
        document.getElementById(`config-tab-${name}`)?.classList.toggle('active', name === tab);
        document.getElementById(`config-panel-${name}`)?.classList.toggle('hidden', name !== tab);
    });
    if (tab === 'models') loadModelsView();
    // Re-pull /config when returning to Basic: a provider added on the Models
    // tab must show up in the basic main-model provider picker without a manual
    // page refresh. loadConfigView re-renders from the fresh provider list.
    if (tab === 'basic') loadConfigView();
}

// =====================================================================
// Branding View (系统设置 → 品牌设置)
// =====================================================================
let brandingDraft = null;       // { brand_name, logo_description, logo_action, logoFile, logoPreviewUrl, hasLogoChange }
let brandingBaseline = null;    // last successful published snapshot (the form baseline)
let brandingLoading = false;
let brandingSaving = false;
let brandingReadonly = false;
let brandingReadonlyReason = '';
let brandingCanReset = false;
let brandingCsrfToken = '';
let brandingConflict = false;
let brandingSavePending = false;
let brandingPreviewDark = true;
let brandingImageError = false;
let brandingInitDone = false;

function brandingEl(id) {
    return document.getElementById(id);
}

function _brandingInputsEqual() {
    if (!brandingDraft || !brandingBaseline) return false;
    return brandingDraft.brand_name === brandingBaseline.brand_name
        && brandingDraft.logo_description === brandingBaseline.logo_description
        && brandingDraft.logo_action === 'keep'
        && brandingDraft.logoPreviewUrl === brandingBaseline.logoUrl;
}

function _brandingSetDirty(flag) {
    brandingDirty = flag;
    const badge = brandingEl('branding-state-badge');
    if (!badge) return;
    badge.classList.remove('hidden');
    if (flag) {
        badge.textContent = t('branding_unsaved');
        badge.classList.remove('bg-emerald-50', 'dark:bg-emerald-900/20', 'text-emerald-600', 'dark:text-emerald-300');
        badge.classList.add('bg-amber-50', 'dark:bg-amber-900/20', 'text-amber-600', 'dark:text-amber-300');
    } else {
        badge.textContent = t('branding_saved_state');
        badge.classList.remove('bg-amber-50', 'dark:bg-amber-900/20', 'text-amber-600', 'dark:text-amber-300');
        badge.classList.add('bg-emerald-50', 'dark:bg-emerald-900/20', 'text-emerald-600', 'dark:text-emerald-300');
    }
}

function _brandingRefreshDirty() {
    if (!brandingDraft) { _brandingSetDirty(false); return; }
    _brandingSetDirty(!_brandingInputsEqual());
    _brandingUpdateControls();
}

function _brandingShowBanner(msg, kind) {
    const banner = brandingEl('branding-banner');
    if (!banner) return;
    const styles = {
        warning: 'bg-amber-50 dark:bg-amber-900/20 border-amber-200 dark:border-amber-800 text-amber-700 dark:text-amber-200',
        error: 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800 text-red-700 dark:text-red-200',
        info: 'bg-blue-50 dark:bg-blue-900/20 border-blue-200 dark:border-blue-800 text-blue-700 dark:text-blue-200',
        success: 'bg-emerald-50 dark:bg-emerald-900/20 border-emerald-200 dark:border-emerald-800 text-emerald-700 dark:text-emerald-200',
    };
    banner.className = `hidden mb-4 rounded-xl border px-4 py-3 text-sm ${styles[kind] || styles.info}`;
    banner.innerHTML = `<div class="flex items-start gap-2"><span>${escapeHtml(msg)}</span></div>`;
    banner.classList.remove('hidden');
}

function _brandingHideBanner() {
    const banner = brandingEl('branding-banner');
    if (banner) banner.classList.add('hidden');
}

function _brandingShowReadonlyState() {
    _brandingUpdateControls();
    const messages = {
        branding_enterprise_unavailable: 'branding_enterprise_unavailable',
        branding_storage_corrupt: 'branding_storage_corrupt',
    };
    _brandingShowBanner(t(messages[brandingReadonlyReason] || 'branding_readonly_reason'), 'info');
    if (brandingReadonlyReason === 'branding_storage_corrupt') _brandingRenderConflictActions();
}

function _brandingRenderPreview() {
    if (!brandingDraft) return;
    const name = brandingDraft.brand_name || DEFAULT_BRAND.brand_name;
    const desc = brandingDraft.logo_description || '';
    const logoUrl = brandingDraft.logoPreviewUrl || effectiveLogoUrl();

    const canvas = brandingEl('branding-preview-canvas');
    if (!canvas) return;
    canvas.classList.toggle('dark', brandingPreviewDark);

    // sidebar slot
    canvas.querySelectorAll('[data-brand-slot="name"]').forEach(el => {
        el.innerHTML = brandWordmarkHTML(name);
        if (brandingPreviewDark) {
            el.classList.add('!text-[#f3f8fc]');
        } else {
            el.classList.remove('!text-[#f3f8fc]');
        }
    });
    canvas.querySelectorAll('[data-brand-slot="logo"]').forEach(el => {
        el.src = logoUrl;
        el.alt = '';
    });
    // Sidebar caption: default maps to 工作台 / 管理控制台 by path; custom
    // brand descriptions still paint as configured.
    canvas.querySelectorAll('[data-brand-slot="caption"]').forEach(el => {
        const captionHelper = (typeof window !== 'undefined' && window
            && typeof window.sidebarBrandCaption === 'function')
            ? window.sidebarBrandCaption
            : null;
        const caption = captionHelper
            ? captionHelper(desc)
            : ((desc && desc.trim()) || '');
        const hasDesc = !!String(caption || '').trim();
        el.textContent = hasDesc ? caption : '';
        el.classList.toggle('hidden', !hasDesc);
        el.title = hasDesc ? caption : '';
    });
    // Desc slot is used by both the login and welcome previews. The welcome
    // preview mirrors the real welcome hero and hides the built-in default
    // (redundant with the eyebrow); login preview keeps showing it.
    canvas.querySelectorAll('[data-brand-slot="desc"]').forEach(el => {
        const isWelcomePreview = !!el.closest('[data-preview="welcome"]');
        const isDefault = _isDefaultLogoDescription(desc);
        const visible = isWelcomePreview
            ? !!(desc && desc.trim() && !isDefault)
            : !!(desc && desc.trim());
        el.textContent = visible ? desc : '';
        el.classList.toggle('hidden', !visible);
        el.title = visible ? desc : '';
    });
}

function _brandingFillFormFromDraft() {
    if (!brandingDraft) return;
    const nameInput = brandingEl('branding-brand-name');
    const descInput = brandingEl('branding-logo-desc');
    if (nameInput) nameInput.value = brandingDraft.brand_name;
    if (descInput) descInput.value = brandingDraft.logo_description;
    _brandingRenderLogoThumb(brandingDraft.logoPreviewUrl, brandingDraft.logo_action === 'default');
}

function _brandingRenderLogoThumb(url, useDefault) {
    const thumb = brandingEl('branding-logo-thumb');
    if (!thumb) return;
    const src = (useDefault || !url) ? DEFAULT_BRAND.logo_url : url;
    thumb.innerHTML = `<img src="${escapeHtml(src)}" alt="" class="brand-mark w-12 h-12 object-contain" style="${brandingImageError ? 'opacity:.4' : ''}">`;
    brandingImageError = false;
}

function _brandingValidate() {
    const name = brandingEl('branding-brand-name').value.trim();
    const desc = brandingEl('branding-logo-desc').value.trim();
    const flowErr = brandingEl('branding-flow-error');
    if (!name) return '品牌名称不能为空';
    if (name.length > 32) return t('branding_save_failed');
    if (/\n|\r/.test(name) || /\n|\r/.test(desc)) return '不能包含换行字符';
    if (desc.length > 100) return 'Logo 描述不能超过 100 字';
    if (flowErr) flowErr.classList.add('hidden');
    return '';
}

function _brandingFormatFileError(err) {
    if (err && err.code === 'image_too_large') return t('branding_image_too_large');
    if (err && err.code === 'invalid_image_format') return '仅支持 PNG、JPG、WebP 图片';
    return t('branding_invalid_image');
}

function _brandingSetError(msg) {
    const flowErr = brandingEl('branding-flow-error');
    if (flowErr) {
        flowErr.textContent = msg;
        flowErr.classList.remove('hidden');
    }
}

function _brandingPublishSuccess(record) {
    brandingCsrfToken = record.csrf_token || brandingCsrfToken;
    brandingReadonly = record.can_manage === false;
    brandingCanReset = record.can_reset !== false;
    brandingReadonlyReason = record.readonly_reason || '';
    // Update the shared brand snapshot from the save response, bump the save
    // epoch so any in-flight (older) public read is ignored.
    brandSaveEpoch += 1;
    const logoUrl = record.logo_url || DEFAULT_BRAND.logo_url;
    brandState = {
        enabled: true,
        revision: record.revision || 0,
        brand_name: record.brand_name || DEFAULT_BRAND.brand_name,
        logo_description: (record.logo_description != null) ? record.logo_description : '',
        logo_url: logoUrl,
        favicon_url: record.favicon_url || DEFAULT_BRAND.favicon_url,
    };
    brandLoaded = true;
    if (appConfig) appConfig.title = productTitle(brandState.brand_name);
    applyBrandToDocument();
    applyBrandToAgentAvatars();

    // Update the baseline to the newly saved snapshot.
    brandingBaseline = {
        brand_name: brandState.brand_name,
        logo_description: brandState.logo_description,
        logo_action: 'keep',
        logoUrl: logoUrl,
        revision: brandState.revision,
    };
    brandingDraft = { ...brandingBaseline, logo_action: 'keep', logoPreviewUrl: logoUrl, logoFile: null };
    brandingDirty = false;
    brandingConflict = false;
    brandingSavePending = false;
    _brandingFillFormFromDraft();
    _brandingRefreshDirty();
    _brandingRenderPreview();
    _brandingShowBanner(t('branding_saved'), 'success');
    setTimeout(_brandingHideBanner, 3000);
}

function _brandingSetControlsState(saving) {
    brandingSaving = saving;
    _brandingUpdateControls();
}

function _brandingUpdateControls() {
    const busy = brandingLoading || brandingSaving;
    const editable = !busy && !brandingReadonly && !!brandingBaseline;
    ['branding-brand-name', 'branding-logo-desc', 'branding-logo-file', 'branding-upload-btn',
        'branding-default-logo-btn', 'branding-cancel'].forEach(id => {
        const el = brandingEl(id);
        if (el) el.disabled = !editable;
    });
    const saveBtn = brandingEl('branding-save');
    const resetBtn = brandingEl('branding-reset-all');
    if (saveBtn) saveBtn.disabled = !editable || brandingConflict || !brandingDirty || !!_brandingValidate();
    if (resetBtn) resetBtn.disabled = busy || !brandingCanReset || !brandingBaseline;
}

function _brandingSubmitSave() {
    if (brandingSaving || brandingLoading || brandingReadonly || brandingConflict || !brandingBaseline) return;
    const errMsg = _brandingValidate();
    if (errMsg) { _brandingSetError(errMsg); return; }
    const name = brandingEl('branding-brand-name').value.trim();
    const desc = brandingEl('branding-logo-desc').value.trim();
    // No-op save guard.
    if (_brandingInputsEqual()) { _brandingShowBanner(t('branding_unsaved_warn'), 'warning'); return; }

    _brandingSetControlsState(true);
    _brandingHideBanner();
    const fd = new FormData();
    fd.append('expected_revision', String(brandingBaseline ? brandingBaseline.revision : 0));
    fd.append('brand_name', name);
    fd.append('logo_description', desc);
    fd.append('logo_action', brandingDraft.logo_action || 'keep');
    if (brandingDraft.logo_action === 'replace' && brandingDraft.logoFile) fd.append('logo', brandingDraft.logoFile);

    return fetch('/api/branding', { method: 'POST', body: fd, headers: { 'X-Branding-CSRF': brandingCsrfToken } })
        .then(async (r) => {
            const data = await r.json().catch(() => ({}));
            if (r.status === 401) {
                // Session expired: re-prompt login; draft stays in memory only.
                if (typeof maybeShowLoginOverlay === 'function') maybeShowLoginOverlay();
                _brandingShowBanner(t('branding_save_failed') + ' 401', 'error');
                throw new Error('unauthorized');
            }
            if (r.status === 409) {
                brandingConflict = true;
                _brandingShowBanner(t('branding_conflict'), 'warning');
                _brandingRenderConflictActions();
                throw new Error('conflict');
            }
            if (!r.ok || data.status !== 'success') {
                _brandingShowBanner(data.message || t('branding_save_failed'), 'error');
                throw new Error('save-failed');
            }
            return data;
        })
        .then((data) => {
            _brandingPublishSuccess(data);
        })
        .catch((err) => {
            if (err && err.message === 'conflict') {
                // Keep the draft so the user can inspect / reload.
                brandingDirty = true;
                return;
            }
            if (err && err.message === 'unauthorized') return;
            _brandingSetDirty(true);
        })
        .finally(() => {
            _brandingSetControlsState(false);
            brandingSaving = false;
            _brandingRefreshDirty();
        });
}

function _brandingRenderConflictActions() {
    const banner = brandingEl('branding-banner');
    if (!banner) return;
    banner.innerHTML += `<button type="button" id="branding-reload-btn" class="ml-2 underline text-xs cursor-pointer">${escapeHtml(t('branding_reload'))}</button>`;
    const rb = brandingEl('branding-reload-btn');
    if (rb) rb.addEventListener('click', () => initBrandingView());
}

function brandingConfirmDiscard(onDiscard) {
    if (!brandingDirty) { onDiscard(); return; }
    showConfirmDialog({
        title: t('branding_dirty_leave_title'),
        message: t('branding_dirty_leave_body'),
        okText: t('branding_dirty_leave_ok'),
        cancelText: t('branding_dirty_leave_cancel'),
        onConfirm: onDiscard,
    });
}

function _brandingResetDraftToBaseline() {
    if (!brandingBaseline) return;
    brandingDraft = {
        brand_name: brandingBaseline.brand_name,
        logo_description: brandingBaseline.logo_description,
        logo_action: 'keep',
        logoFile: null,
        logoPreviewUrl: brandingBaseline.logoUrl,
        hasLogoChange: false,
    };
    _brandingFillFormFromDraft();
    _brandingRenderPreview();
    _brandingRefreshDirty();
    _brandingHideBanner();
}

function initBrandingView() {
    if (!brandingEl('view-branding')) return;
    if (brandingLoading || brandingSaving) return;
    // Re-entry and conflict reload both preserve drafts unless confirmed.
    brandingConfirmDiscard(_loadBrandingSetup);
}

function _loadBrandingSetup() {
    const view = brandingEl('view-branding');
    if (!view) return;
    brandingLoading = true;
    _brandingUpdateControls();
    const banner = brandingEl('branding-banner');
    if (banner) banner.classList.add('hidden');
    // Show a loading placeholder on the Save button.
    const saveBtn = brandingEl('branding-save');
    if (saveBtn) saveBtn.disabled = true;

    return fetch('/api/branding', { credentials: 'same-origin' })
        .then(async (r) => {
            if (r.status === 401) {
                if (typeof maybeShowLoginOverlay === 'function') maybeShowLoginOverlay();
                throw new Error('unauthorized');
            }
            const data = await r.json().catch(() => ({}));
            if (!r.ok || data.status !== 'success') throw new Error(data.message || 'load-failed');
            return data;
        })
        .then((data) => {
            brandingReadonly = !data.can_manage;
            brandingCanReset = !!data.can_reset;
            brandingCsrfToken = data.csrf_token || '';
            brandingReadonlyReason = data.readonly_reason || '';
            brandingBaseline = {
                brand_name: data.brand_name || DEFAULT_BRAND.brand_name,
                logo_description: (data.logo_description != null) ? data.logo_description : '',
                logo_action: 'keep',
                logoUrl: data.logo_url || DEFAULT_BRAND.logo_url,
                revision: data.revision || 0,
            };
            brandingDraft = { ...brandingBaseline, logo_action: 'keep', logoPreviewUrl: data.logo_url || DEFAULT_BRAND.logo_url, logoFile: null, hasLogoChange: false };
            brandingConflict = false;
            _brandingFillFormFromDraft();
            _brandingRenderPreview();
            _brandingRefreshDirty();
            brandingInitDone = true;
            brandingLoading = false;
            _brandingUpdateControls();
            if (brandingReadonly) _brandingShowReadonlyState();
        })
        .catch(() => {
            brandingLoading = false;
            brandingReadonly = true;
            brandingCanReset = false;
            brandingCsrfToken = '';
            _brandingUpdateControls();
            _brandingShowBanner(t('branding_load_failed'), 'error');
            _brandingRenderConflictActions();
        });
}

function _brandingBindEvents() {
    const view = brandingEl('view-branding');
    if (!view || brandingInitDone && brandingEl('view-branding').dataset.bound === '1') return;

    const name = brandingEl('branding-brand-name');
    const desc = brandingEl('branding-logo-desc');
    if (name) name.addEventListener('input', () => {
        if (!brandingDraft) return;
        brandingDraft.brand_name = name.value;
        _brandingRefreshDirty();
        _brandingRenderPreview();
    });
    if (desc) desc.addEventListener('input', () => {
        if (!brandingDraft) return;
        brandingDraft.logo_description = desc.value;
        _brandingRefreshDirty();
        _brandingRenderPreview();
    });

    const uploadBtn = brandingEl('branding-upload-btn');
    const fileInput = brandingEl('branding-logo-file');
    if (uploadBtn && fileInput) {
        uploadBtn.addEventListener('click', () => fileInput.click());
        fileInput.addEventListener('change', () => {
            if (brandingReadonly || brandingLoading || brandingSaving) return;
            const file = fileInput.files && fileInput.files[0];
            if (!file) return;
            if (file.size > 2 * 1024 * 1024) { _brandingSetError(t('branding_image_too_large')); return; }
            const ext = (file.name.split('.').pop() || '').toLowerCase();
            if (!['png', 'jpg', 'jpeg', 'webp'].includes(ext)) { _brandingSetError('仅支持 PNG、JPG、WebP 图片'); return; }
            _brandingSetError('');
            const objUrl = URL.createObjectURL(file);
            if (brandingDraft) { brandingDraft.logoFile = file; brandingDraft.logo_preview = objUrl; brandingDraft.logo_action = 'replace'; brandingDraft.logoPreviewUrl = objUrl; }
            _brandingRenderLogoThumb(objUrl, false);
            _brandingRenderPreview();
            _brandingRefreshDirty();
        });
    }

    const defaultLogoBtn = brandingEl('branding-default-logo-btn');
    if (defaultLogoBtn) defaultLogoBtn.addEventListener('click', () => {
        if (!brandingDraft) return;
        brandingDraft.logoFile = null;
        brandingDraft.logo_action = 'default';
        brandingDraft.logoPreviewUrl = DEFAULT_BRAND.logo_url;
        brandingDraft.hasLogoChange = true;
        // Keep a stable, non-emptied file state.
        const fileInputEl = brandingEl('branding-logo-file');
        if (fileInputEl) fileInputEl.value = '';
        _brandingRenderLogoThumb(DEFAULT_BRAND.logo_url, true);
        _brandingRenderPreview();
        _brandingRefreshDirty();
    });

    const saveBtn = brandingEl('branding-save');
    if (saveBtn) saveBtn.addEventListener('click', _brandingSubmitSave);

    const cancelBtn = brandingEl('branding-cancel');
    if (cancelBtn) cancelBtn.addEventListener('click', () => _brandingResetDraftToBaseline());

    const resetAllBtn = brandingEl('branding-reset-all');
    if (resetAllBtn) resetAllBtn.addEventListener('click', () => {
        const doReset = () => {
            if (brandingSaving || brandingLoading || !brandingCanReset || !brandingBaseline) return;
            _brandingSetControlsState(true);
            _brandingHideBanner();
            fetch('/api/branding/reset', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', 'X-Branding-CSRF': brandingCsrfToken },
                body: JSON.stringify({ expected_revision: brandingBaseline ? brandingBaseline.revision : 0 }),
            })
                .then(async (r) => {
                    if (r.status === 401) {
                        if (typeof maybeShowLoginOverlay === 'function') maybeShowLoginOverlay();
                        throw new Error('unauthorized');
                    }
                    const data = await r.json().catch(() => ({}));
                    if (r.status === 409) { brandingConflict = true; _brandingShowBanner(t('branding_conflict'), 'warning'); _brandingRenderConflictActions(); throw new Error('conflict'); }
                    if (!r.ok || data.status !== 'success') { _brandingShowBanner(data.message || t('branding_save_failed'), 'error'); throw new Error('reset-failed'); }
                    return data;
                })
                .then((data) => _brandingPublishSuccess(data))
                .catch((err) => {
                    if (err && err.message === 'conflict') { brandingDirty = true; return; }
                    if (err && err.message === 'unauthorized') return;
                    _brandingSetDirty(true);
                })
                .finally(() => _brandingSetControlsState(false));
        };
        if (typeof showConfirmDialog === 'function') {
            showConfirmDialog({
                title: t('branding_reset_confirm_title'),
                message: t('branding_reset_confirm_body'),
                okText: t('branding_reset_confirm_ok'),
                cancelText: t('branding_reset_confirm_cancel'),
                onConfirm: doReset,
            });
        } else if (window.confirm(t('branding_reset_confirm_body'))) {
            doReset();
        }
    });

    // Theme toggle for the preview
    const darkBtn = brandingEl('branding-theme-dark');
    const lightBtn = brandingEl('branding-theme-light');
    const setTheme = (dark) => {
        brandingPreviewDark = dark;
        if (darkBtn) darkBtn.classList.toggle('active', dark);
        if (lightBtn) lightBtn.classList.toggle('active', !dark);
        _brandingRenderPreview();
    };
    if (darkBtn) darkBtn.addEventListener('click', () => setTheme(true));
    if (lightBtn) lightBtn.addEventListener('click', () => setTheme(false));

    // Drag & drop onto the logo thumb.
    const thumb = brandingEl('branding-logo-thumb');
    if (thumb) {
        thumb.addEventListener('dragover', (e) => { e.preventDefault(); thumb.classList.add('border-primary-500'); });
        thumb.addEventListener('dragleave', () => thumb.classList.remove('border-primary-500'));
        thumb.addEventListener('drop', (e) => {
            e.preventDefault();
            if (brandingReadonly || brandingLoading || brandingSaving) return;
            thumb.classList.remove('border-primary-500');
            const file = e.dataTransfer.files && e.dataTransfer.files[0];
            if (file) {
                if (file.size > 2 * 1024 * 1024) { _brandingSetError(t('branding_image_too_large')); return; }
                const objUrl = URL.createObjectURL(file);
                if (brandingDraft) { brandingDraft.logoFile = file; brandingDraft.logo_action = 'replace'; brandingDraft.logoPreviewUrl = objUrl; }
                _brandingRenderLogoThumb(objUrl, false);
                _brandingRenderPreview();
                _brandingRefreshDirty();
            }
        });
    }

    brandingEl('view-branding').dataset.bound = '1';
}

document.addEventListener('DOMContentLoaded', function() {
    // Bind the branding page controls once. The page is populated lazily on
    // first entry via navigateTo -> initBrandingView.
    if (brandingEl('view-branding')) _brandingBindEvents();
});

// =====================================================================
// Skills View
// =====================================================================
let toolsLoaded = false;

const TOOL_ICONS = {
    bash: 'fa-terminal',
    edit: 'fa-pen-to-square',
    read: 'fa-file-lines',
    write: 'fa-file-pen',
    ls: 'fa-folder-open',
    send: 'fa-paper-plane',
    web_search: 'fa-magnifying-glass',
    browser: 'fa-globe',
    env_config: 'fa-key',
    scheduler: 'fa-clock',
    memory_get: 'fa-brain',
    memory_search: 'fa-brain',
};

function getToolIcon(name) {
    return TOOL_ICONS[name] || 'fa-wrench';
}

function loadSkillsView() {
    loadToolsSection();
    loadSkillsSection();
}

function loadToolsSection() {
    if (toolsLoaded) return;
    const emptyEl = document.getElementById('tools-empty');
    const listEl = document.getElementById('tools-list');
    const badge = document.getElementById('tools-count-badge');

    fetch('/api/tools').then(r => r.json()).then(data => {
        if (data.status !== 'success') return;
        const tools = data.tools || [];
        emptyEl.classList.add('hidden');
        if (tools.length === 0) {
            emptyEl.classList.remove('hidden');
            emptyEl.innerHTML = `<span class="text-sm text-slate-400 dark:text-slate-500">${currentLang === 'zh' ? '暂无内置工具' : 'No built-in tools'}</span>`;
            return;
        }
        badge.textContent = tools.length;
        badge.classList.remove('hidden');
        listEl.innerHTML = '';
        tools.forEach(tool => {
            const card = document.createElement('div');
            card.className = 'bg-white dark:bg-[#1A1A1A] rounded-xl border border-slate-200 dark:border-white/10 p-4 flex items-start gap-3';
            card.innerHTML = `
                <div class="w-9 h-9 rounded-lg bg-blue-50 dark:bg-blue-900/20 flex items-center justify-center flex-shrink-0">
                    <i class="fas ${getToolIcon(tool.name)} text-blue-500 dark:text-blue-400 text-sm"></i>
                </div>
                <div class="flex-1 min-w-0">
                    <div class="flex items-center gap-2">
                        <span class="font-medium text-sm text-slate-700 dark:text-slate-200 font-mono">${escapeHtml(tool.name)}</span>
                    </div>
                    <p class="text-xs text-slate-400 dark:text-slate-500 mt-1 line-clamp-2">${escapeHtml(tool.description || '--')}</p>
                </div>`;
            listEl.appendChild(card);
        });
        listEl.classList.remove('hidden');
        toolsLoaded = true;
    }).catch(() => {
        emptyEl.classList.remove('hidden');
        emptyEl.innerHTML = `<span class="text-sm text-slate-400 dark:text-slate-500">${currentLang === 'zh' ? '加载失败' : 'Failed to load'}</span>`;
    });
}

function loadSkillsSection() {
    const emptyEl = document.getElementById('skills-empty');
    const listEl = document.getElementById('skills-list');
    const badge = document.getElementById('skills-count-badge');

    fetch('/api/skills').then(r => r.json()).then(data => {
        if (data.status !== 'success') return;
        const skills = data.skills || [];
        if (skills.length === 0) {
            const p = emptyEl.querySelector('p');
            if (p) p.textContent = currentLang === 'zh' ? '暂无技能' : 'No skills found';
            return;
        }
        badge.textContent = skills.length;
        badge.classList.remove('hidden');
        emptyEl.classList.add('hidden');
        listEl.innerHTML = '';

        skills.forEach(sk => {
            const card = document.createElement('div');
            card.className = 'bg-white dark:bg-[#1A1A1A] rounded-xl border border-slate-200 dark:border-white/10 '
                + 'p-4 flex items-start gap-3 transition-opacity cursor-pointer '
                + 'hover:border-slate-300 dark:hover:border-white/20';
            card.dataset.skillName = sk.name;
            card.dataset.skillDesc = sk.description || '';
            card.dataset.skillDisplayName = sk.display_name || '';
            card.dataset.enabled = sk.enabled ? '1' : '0';
            renderSkillCard(card, sk);
            listEl.appendChild(card);
        });
    }).catch(() => {});
}

function renderSkillCard(card, sk) {
    const enabled = sk.enabled;
    const iconColor = enabled ? 'text-primary-400' : 'text-slate-300 dark:text-slate-600';
    const trackClass = enabled
        ? 'bg-primary-400'
        : 'bg-slate-200 dark:bg-slate-700';
    const thumbTranslate = enabled ? 'translate-x-3' : 'translate-x-0.5';
    card.innerHTML = `
        <div class="w-9 h-9 rounded-lg bg-amber-50 dark:bg-amber-900/20 flex items-center justify-center flex-shrink-0">
            <i class="fas fa-bolt ${iconColor} text-sm"></i>
        </div>
        <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2 mb-1">
                <span class="font-medium text-sm text-slate-700 dark:text-slate-200 truncate flex-1">${escapeHtml(sk.display_name || sk.name)}</span>
                <button
                    role="switch"
                    data-skill-switch
                    aria-checked="${enabled}"
                    class="relative inline-flex h-4 w-7 flex-shrink-0 cursor-pointer rounded-full transition-colors duration-200 ease-in-out focus:outline-none ${trackClass}"
                    title="${enabled ? (currentLang === 'zh' ? '点击禁用' : 'Click to disable') : (currentLang === 'zh' ? '点击启用' : 'Click to enable')}"
                >
                    <span class="inline-block h-3 w-3 mt-0.5 rounded-full bg-white shadow transform transition-transform duration-200 ease-in-out ${thumbTranslate}"></span>
                </button>
            </div>
            <p class="text-xs text-slate-400 dark:text-slate-500 line-clamp-2">${escapeHtml(sk.description || '--')}</p>
        </div>`;

    // Bound here rather than written into the markup above: a skill name comes
    // from its own frontmatter, and one containing a quote would break out of
    // an inline onclick attribute.
    card.title = t('skill_open_hint');
    card.onclick = () => openSkillFile(sk.name);
    const sw = card.querySelector('[data-skill-switch]');
    if (sw) {
        sw.onclick = (e) => {
            e.stopPropagation();
            toggleSkill(sk.name, enabled);
        };
    }
}

function toggleSkill(name, currentlyEnabled) {
    const action = currentlyEnabled ? 'close' : 'open';
    const card = document.querySelector(`[data-skill-name="${CSS.escape(name)}"]`);
    if (card) card.style.opacity = '0.5';

    fetch('/api/skills', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, name })
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            if (card) {
                card.dataset.enabled = currentlyEnabled ? '0' : '1';
                card.style.opacity = '1';
                renderSkillCard(card, {
                    name: name,
                    description: card.dataset.skillDesc || '',
                    display_name: card.dataset.skillDisplayName || '',
                    enabled: !currentlyEnabled,
                });
            }
        } else {
            if (card) card.style.opacity = '1';
            alert(currentLang === 'zh' ? '操作失败，请稍后再试' : 'Operation failed, please try again');
        }
    })
    .catch(() => {
        if (card) card.style.opacity = '1';
        alert(currentLang === 'zh' ? '操作失败，请稍后再试' : 'Operation failed, please try again');
    });
}

// ---------------------------------------------------------------------
// Skill viewer / editor
// ---------------------------------------------------------------------

/**
 * Skills are addressed by name, not by path: which file a name resolves to is
 * the loader's business, and a builtin skill lives outside the workspace that
 * the file APIs are confined to.
 */
async function skillReadContent(name) {
    const res = await fetch(`/api/skills/content?name=${encodeURIComponent(name)}`);
    const data = await res.json();
    if (data.status !== 'success') throw new Error(data.message || 'read failed');
    return data;
}

/** Save a skill's definition. Returns the raw response, a conflict included. */
async function skillWriteContent(name, content, expectedMtime) {
    const res = await fetch('/api/skills/content', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name, content: content, expected_mtime: expectedMtime }),
    });
    return res.json();
}

/** The i18n key explaining why a skill cannot be edited, or null if it can. */
function skillReadonlyReason(data) {
    if (data.editable) return null;
    // Not `source === 'builtin'`: the workspace copy of a builtin skill reads
    // back as `custom` and is refused all the same, so the server says so.
    if (data.ships_with_install) return 'skill_builtin_readonly';
    return docUneditableReason(data);
}

const skillEditor = createDocEditor({
    body: () => document.getElementById('skill-viewer-content'),
    buttons: () => ({
        edit: document.getElementById('skill-btn-edit'),
        save: document.getElementById('skill-btn-save'),
        cancel: document.getElementById('skill-btn-cancel'),
    }),
    read: (doc) => skillReadContent(doc.name),
    write: (doc, content, mtime) => skillWriteContent(doc.name, content, mtime),
    render: (doc) => docRenderBody('skill-viewer-content', doc.content),
    canEdit: (doc) => !doc.readonlyKey,
    refusal: skillReadonlyReason,
    onState: (state) => docRenderTitle('skill-viewer-title', skillEditor.current()?.name, state),
});

function openSkillFile(name) {
    skillReadContent(name).then(data => {
        const badge = document.getElementById('skill-viewer-readonly');
        const readonlyKey = skillReadonlyReason(data);
        if (badge) {
            badge.classList.toggle('hidden', !readonlyKey);
            if (readonlyKey) {
                // Keep data-i18n in step so a language switch re-translates it.
                badge.dataset.i18n = readonlyKey;
                badge.textContent = t(readonlyKey);
                badge.title = t(readonlyKey);
            }
        }
        document.getElementById('skills-panel-list').classList.add('hidden');
        document.getElementById('skills-panel-viewer').classList.remove('hidden');
        skillEditor.open({
            name: data.name || name,
            content: data.content || '',
            readonlyKey: readonlyKey,
        });
    }).catch(e => _wsToast(`${t('skill_load_failed')}: ${e.message}`));
}

function closeSkillViewer() {
    if (!skillEditor.guard(closeSkillViewer)) return;
    resetSkillViewer();
    // A saved edit can change the name and description in the frontmatter, so
    // the cards behind this panel may be out of date.
    loadSkillsSection();
}

/** Drop the viewer and show the list, without asking about unsaved edits. */
function resetSkillViewer() {
    skillEditor.forget();
    document.getElementById('skills-panel-viewer')?.classList.add('hidden');
    document.getElementById('skills-panel-list')?.classList.remove('hidden');
}

// =====================================================================
// Memory View
// =====================================================================
let memoryPage = 1;
let memoryCategory = 'memory';   // 'memory' | 'evolution'
const memoryPageSize = 10;

function switchMemoryTab(tab) {
    document.querySelectorAll('.memory-tab').forEach(el => el.classList.remove('active'));
    document.getElementById('memory-tab-' + tab).classList.add('active');
    // The "dreams" tab now surfaces self-evolution logs (merged with dream diaries).
    memoryCategory = tab === 'dreams' ? 'evolution' : 'memory';
    loadMemoryView(1);
}

function loadMemoryView(page) {
    page = page || 1;
    memoryPage = page;
    const agent = viewingMemoryAgentId();
    fetch(`/api/memory?page=${page}&page_size=${memoryPageSize}&category=${memoryCategory}&agent_id=${encodeURIComponent(agent || '')}`).then(r => r.json()).then(data => {
        if (data.status !== 'success') return;
        const emptyEl = document.getElementById('memory-empty');
        const listEl = document.getElementById('memory-list');
        const files = data.list || [];
        const total = data.total || 0;

        if (total === 0) {
            const emptyIcon = emptyEl.querySelector('i');
            const emptyTitle = emptyEl.querySelector('p');
            if (memoryCategory === 'evolution') {
                emptyIcon.className = 'fas fa-seedling text-emerald-400 text-xl';
                emptyTitle.textContent = currentLang === 'zh' ? '暂无进化记录' : 'No evolution records yet';
            } else {
                emptyIcon.className = 'fas fa-brain text-purple-400 text-xl';
                emptyTitle.textContent = currentLang === 'zh' ? '暂无记忆文件' : 'No memory files';
            }
            emptyEl.classList.remove('hidden');
            listEl.classList.add('hidden');
            return;
        }
        emptyEl.classList.add('hidden');
        listEl.classList.remove('hidden');

        const tbody = document.getElementById('memory-table-body');
        tbody.innerHTML = '';
        files.forEach(f => {
            const tr = document.createElement('tr');
            tr.className = 'border-b border-slate-100 dark:border-white/5 hover:bg-slate-50 dark:hover:bg-white/5 cursor-pointer transition-colors';
            // In the merged evolution tab, resolve each file by its own origin
            // (evolution logs vs dream diaries live in different dirs).
            const fileCategory = (f.type === 'dream' || f.type === 'evolution') ? f.type : memoryCategory;
            tr.onclick = () => openMemoryFile(f.filename, fileCategory);
            let typeLabel;
            if (f.type === 'global') {
                typeLabel = '<span class="px-2 py-0.5 rounded-full text-xs bg-primary-50 dark:bg-primary-900/30 text-primary-600 dark:text-primary-400">Global</span>';
            } else if (f.type === 'evolution') {
                typeLabel = '<span class="px-2 py-0.5 rounded-full text-xs bg-emerald-50 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400">Evolution</span>';
            } else if (f.type === 'dream') {
                typeLabel = '<span class="px-2 py-0.5 rounded-full text-xs bg-violet-50 dark:bg-violet-900/30 text-violet-600 dark:text-violet-400">Dream</span>';
            } else {
                typeLabel = '<span class="px-2 py-0.5 rounded-full text-xs bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400">Daily</span>';
            }
            const sizeStr = f.size < 1024 ? f.size + ' B' : (f.size / 1024).toFixed(1) + ' KB';
            tr.innerHTML = `
                <td class="px-4 py-3 text-sm font-mono text-slate-700 dark:text-slate-200">${escapeHtml(f.filename)}</td>
                <td class="px-4 py-3 text-sm">${typeLabel}</td>
                <td class="px-4 py-3 text-sm text-slate-500 dark:text-slate-400">${sizeStr}</td>
                <td class="px-4 py-3 text-sm text-slate-500 dark:text-slate-400">${escapeHtml(f.updated_at)}</td>`;
            tbody.appendChild(tr);
        });

        // Pagination
        const totalPages = Math.ceil(total / memoryPageSize);
        const pagEl = document.getElementById('memory-pagination');
        if (totalPages <= 1) { pagEl.innerHTML = ''; return; }
        let pagHtml = `<span>${page} / ${totalPages}</span><div class="flex gap-2">`;
        if (page > 1) pagHtml += `<button onclick="loadMemoryView(${page - 1})" class="px-3 py-1 rounded-lg border border-slate-200 dark:border-white/10 hover:bg-slate-100 dark:hover:bg-white/10 text-xs">Prev</button>`;
        if (page < totalPages) pagHtml += `<button onclick="loadMemoryView(${page + 1})" class="px-3 py-1 rounded-lg border border-slate-200 dark:border-white/10 hover:bg-slate-100 dark:hover:bg-white/10 text-xs">Next</button>`;
        pagHtml += '</div>';
        pagEl.innerHTML = pagHtml;
    }).catch(() => {});
}

// =====================================================================
// Document viewers (memory files, skill definitions)
// =====================================================================

/**
 * Read one file's text for an editor. Throws on an API error so the editor can
 * report it.
 *
 * No session is passed on purpose. Memory files are anchored to the agent's
 * state root, and a session with a project open would resolve the same relative
 * path against that project instead.
 */
async function docReadFile(relPath) {
    const res = await fetch(`/api/workspace/read?path=${encodeURIComponent(relPath)}`);
    const data = await res.json();
    if (data.status !== 'success') throw new Error(data.message || 'read failed');
    return data;
}

/** Save one file's text. Returns the raw response, a conflict included. */
async function docWriteFile(relPath, content, expectedMtime) {
    const res = await fetch('/api/workspace/write', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: relPath, content: content, expected_mtime: expectedMtime }),
    });
    return res.json();
}

/** Render Markdown into a viewer body. */
function docRenderBody(id, content) {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = renderMarkdown(content || '');
    applyHighlighting(el);
}

/** Put a document's name in a viewer title, with a dot while it is unsaved. */
function docRenderTitle(id, name, state) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = name || '';
    if (state && state.dirty) {
        el.insertAdjacentHTML('beforeend', ' <span class="doc-dirty-dot">•</span>');
    }
}

/**
 * Ask about any unsaved document edit before something tears its page down.
 *
 * @param {function} next - retried once the user agrees to lose the edits.
 * @returns {boolean} true when nothing is at stake and the caller may proceed.
 */
function docGuardUnsaved(next) {
    return memoryEditor.guard(next) && skillEditor.guard(next);
}

const memoryEditor = createDocEditor({
    body: () => document.getElementById('memory-viewer-content'),
    buttons: () => ({
        edit: document.getElementById('memory-btn-edit'),
        save: document.getElementById('memory-btn-save'),
        cancel: document.getElementById('memory-btn-cancel'),
    }),
    read: (doc) => docReadFile(doc.relPath),
    write: (doc, content, mtime) => docWriteFile(doc.relPath, content, mtime),
    render: (doc) => docRenderBody('memory-viewer-content', doc.content),
    onState: (state) => docRenderTitle('memory-viewer-title', memoryEditor.current()?.filename, state),
});

function openMemoryFile(filename, category) {
    category = category || 'memory';
    const agent = viewingMemoryAgentId();
    fetch(`/api/memory/content?filename=${encodeURIComponent(filename)}&category=${category}&agent_id=${encodeURIComponent(agent || '')}`).then(r => r.json()).then(data => {
        if (data.status !== 'success') return;
        document.getElementById('memory-panel-list').classList.add('hidden');
        document.getElementById('memory-panel-viewer').classList.remove('hidden');
        memoryEditor.open({
            filename: filename,
            // The memory API reports where the file sits under the workspace
            // root; the editor addresses it there rather than rebuilding the
            // path from filename plus category.
            relPath: data.rel_path || filename,
            content: data.content || '',
        });
    }).catch(() => {});
}

function closeMemoryViewer() {
    if (!memoryEditor.guard(closeMemoryViewer)) return;
    memoryEditor.forget();
    document.getElementById('memory-panel-viewer').classList.add('hidden');
    document.getElementById('memory-panel-list').classList.remove('hidden');
    // A save changed the size and timestamp the list shows.
    loadMemoryView(memoryPage);
}

// Reloading or closing the tab drops an unsaved edit. All the browser allows
// here is its own generic prompt, which still beats losing the text in silence.
window.addEventListener('beforeunload', (e) => {
    if (!memoryEditor.isDirty() && !skillEditor.isDirty() && !brandingDirty) return;
    e.preventDefault();
    e.returnValue = '';
});

// =====================================================================
// Custom Confirm Dialog
// =====================================================================
function showConfirmDialog({ title, message, okText, cancelText, onConfirm, hideCancel }) {
    const overlay = document.getElementById('confirm-dialog-overlay');
    document.getElementById('confirm-dialog-title').textContent = title || '';
    document.getElementById('confirm-dialog-message').textContent = message || '';
    document.getElementById('confirm-dialog-ok').textContent = okText || 'OK';
    const cancelBtn = document.getElementById('confirm-dialog-cancel');
    cancelBtn.textContent = cancelText || t('channels_cancel');
    cancelBtn.classList.toggle('hidden', !!hideCancel);

    function cleanup() {
        overlay.classList.add('hidden');
        okBtn.removeEventListener('click', onOk);
        cancelBtn.removeEventListener('click', onCancel);
        overlay.removeEventListener('click', onOverlayClick);
    }
    function onOk() { cleanup(); if (onConfirm) onConfirm(); }
    function onCancel() { cleanup(); }
    function onOverlayClick(e) { if (e.target === overlay) cleanup(); }

    const okBtn = document.getElementById('confirm-dialog-ok');
    okBtn.addEventListener('click', onOk);
    cancelBtn.addEventListener('click', onCancel);
    overlay.addEventListener('click', onOverlayClick);
    overlay.classList.remove('hidden');
}

// =====================================================================
// Models View
// =====================================================================
// Capability cards rendered on the Models page. Order matters — main model
// comes first because it transitively decides defaults for vision and image.
// Icon palette is grouped by capability family:
//   - chat                       → primary (brand green; the "main" capability)
//   - vision + image             → blue    (everything visual)
//   - asr + tts                  → amber   (everything audio)
//   - embedding                  → purple  (vectors)
//   - search                     → orange  (retrieval)
// Each card uses an explicit `iconClass` string so Tailwind's CDN JIT can
// see the literal class names — dynamic `bg-${color}-50` strings would not
// be picked up reliably.
const MODELS_CAPABILITY_DEFS = [
    { id: 'chat',      icon: 'fa-microchip',        editable: true,  needsModel: true,  toggleable: false, titleKey: 'models_capability_chat',      descKey: 'models_capability_chat_desc',
      iconChip: 'bg-primary-50 dark:bg-primary-900/30',  iconGlyph: 'text-primary-500' },
    // NOTE: the chat fallback is deliberately NOT a top-level card. It is a
    // rarely-touched safety net, so it lives behind a small gear on the main
    // model card (see renderCapabilityHeaderTag / openChatFallbackModal) and
    // is edited in a modal that reuses the same picker machinery.
    { id: 'vision',    icon: 'fa-eye',              editable: true,  needsModel: true,  titleKey: 'models_capability_vision',    descKey: 'models_capability_vision_desc',
      iconChip: 'bg-blue-50 dark:bg-blue-900/30',        iconGlyph: 'text-blue-500' },
    { id: 'image',     icon: 'fa-image',            editable: true,  needsModel: true,  titleKey: 'models_capability_image',     descKey: 'models_capability_image_desc',
      iconChip: 'bg-blue-50 dark:bg-blue-900/30',        iconGlyph: 'text-blue-500' },
    { id: 'asr',       icon: 'fa-microphone',       editable: true,  needsModel: true,  titleKey: 'models_capability_asr',       descKey: 'models_capability_asr_desc',
      iconChip: 'bg-amber-50 dark:bg-amber-900/30',      iconGlyph: 'text-amber-500' },
    { id: 'tts',       icon: 'fa-volume-high',      editable: true,  needsModel: true,  titleKey: 'models_capability_tts',       descKey: 'models_capability_tts_desc',
      iconChip: 'bg-amber-50 dark:bg-amber-900/30',      iconGlyph: 'text-amber-500' },
    { id: 'embedding', icon: 'fa-vector-square',    editable: true,  needsModel: true,  titleKey: 'models_capability_embedding', descKey: 'models_capability_embedding_desc',
      iconChip: 'bg-purple-50 dark:bg-purple-900/30',    iconGlyph: 'text-purple-500' },
    { id: 'search',    icon: 'fa-magnifying-glass', editable: true,  needsModel: false, titleKey: 'models_capability_search',    descKey: 'models_capability_search_desc',
      iconChip: 'bg-orange-50 dark:bg-orange-900/30',    iconGlyph: 'text-orange-500' },
];

// Provider logos: when a real SVG exists under static/logos/<id>.svg we use
// it; otherwise we fall back to a neutral monogram chip. SVGs are fetched
// via <img> with a hidden onerror so layout stays stable when files are
// absent. Vendors whose mark is rendered in pure (or near-pure) black are
// listed in MODELS_PROVIDER_LOGO_DARK_INVERT — for those, we apply a CSS
// invert filter in dark mode so the glyph stays visible against #1A1A1A.
const MODELS_PROVIDER_LOGO_PATH = 'assets/logos';
const MODELS_PROVIDER_LOGO_DARK_INVERT = new Set([
    'openai',     // black wordmark
    'moonshot',   // dark monogram
    'zhipu',      // dark monogram
    'custom',     // single-color slider glyph
]);

let modelsState = { providers: [], capabilities: {} };

// One-shot: { capabilityId, providerId } stashed before a Models reload,
// consumed by renderCapabilityBody to preselect a just-configured vendor.
let pendingCapabilitySelection = null;

// `opts.preserveScroll` keeps the page's vertical scroll position across the
// refresh. We capture it before unhiding the loading skeleton (which collapses
// content height to zero) and restore it after the new content is mounted.
// This matters when the user configures a vendor from inside a capability
// card's dropdown — without preservation, the post-save reload bounces them
// back to the top of the page, away from the card they were configuring.
function loadModelsView(opts) {
    const loading = document.getElementById('models-loading');
    const content = document.getElementById('models-content');
    if (!loading || !content) return;
    const preserveScroll = !!(opts && opts.preserveScroll);
    // The Models pane has its own scrollable container; capture its position
    // (not window.scrollY) so we can put the user back exactly where they were.
    const scroller = document.querySelector('#view-config .overflow-y-auto');
    const savedTop = preserveScroll && scroller ? scroller.scrollTop : null;

    loading.classList.remove('hidden');
    content.classList.add('hidden');

    fetch('/api/models').then(r => r.json()).then(data => {
        if (data.status !== 'success') {
            loading.innerHTML = `<span class="text-sm text-red-400">${escapeHtml(data.message || 'Failed to load')}</span>`;
            return;
        }
        modelsState.providers = data.providers || [];
        modelsState.capabilities = data.capabilities || {};
        renderModelsView();
        loading.classList.add('hidden');
        content.classList.remove('hidden');
        if (savedTop !== null && scroller) {
            // Wait one frame for the new layout to settle, otherwise the
            // restored scrollTop snaps to the previous (smaller) max.
            requestAnimationFrame(() => { scroller.scrollTop = savedTop; });
        }
    }).catch(err => {
        loading.innerHTML = `<span class="text-sm text-red-400">${escapeHtml(String(err))}</span>`;
    });
}

function renderModelsView() {
    const container = document.getElementById('models-content');
    container.innerHTML = '';
    container.appendChild(renderVendorsSection());
    MODELS_CAPABILITY_DEFS.forEach(def => container.appendChild(renderCapabilityCard(def)));
}

// True when a provider card is one of the expanded custom (OpenAI-compatible)
// providers (id "custom:<id>") — shown in the vendor grid alongside built-in
// vendors, but edited via the dedicated custom-provider modal.
function isCustomProviderCard(p) {
    return !!(p && p.is_custom && p.custom_name);
}

// ---------- Vendor section (Layer 1) -----------------------------------

function renderVendorsSection() {
    const wrap = document.createElement('div');
    wrap.className = 'bg-white dark:bg-[#1A1A1A] rounded-xl border border-slate-200 dark:border-white/10 p-6';

    // Custom providers always show once created (even without an api key,
    // e.g. a local vLLM/Ollama endpoint); built-in vendors show when configured.
    const configured = modelsState.providers.filter(p => p.configured || isCustomProviderCard(p));

    const header = `
        <div class="flex items-start gap-3 mb-5">
            <div class="w-9 h-9 rounded-lg bg-primary-50 dark:bg-primary-900/30 flex items-center justify-center flex-shrink-0">
                <i class="fas fa-key text-primary-500 text-sm"></i>
            </div>
            <div class="flex-1 min-w-0">
                <h3 class="font-semibold text-slate-800 dark:text-slate-100">${t('models_section_vendors')}</h3>
                <p class="text-xs text-slate-500 dark:text-slate-400 mt-0.5">${t('models_section_vendors_desc')}</p>
            </div>
        </div>`;

    let body;
    if (configured.length === 0) {
        body = `
            <div class="flex flex-col items-center justify-center py-8 px-4 rounded-lg border border-dashed border-slate-200 dark:border-white/10">
                <p class="text-sm text-slate-500 dark:text-slate-400 text-center">${t('models_not_configured')}</p>
                <button onclick="openVendorModal('')"
                        class="mt-3 px-3 py-1.5 rounded-lg text-xs font-medium bg-primary-50 dark:bg-primary-900/30 text-primary-600 dark:text-primary-400 hover:bg-primary-100 dark:hover:bg-primary-900/50 cursor-pointer transition-colors">
                    <i class="fas fa-plus text-[10px] mr-1"></i>${t('models_add_vendor')}
                </button>
            </div>`;
    } else {
        // Existing vendors as chips, plus a trailing "add" tile so a new
        // built-in or custom provider can still be added once at least one is
        // already configured (otherwise the add entry only showed on the empty
        // state). openVendorModal('') opens the picker → built-in or custom.
        const addTile = `
            <button onclick="openVendorModal('')"
                    class="flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg border border-dashed
                           border-slate-300 dark:border-white/15 text-slate-500 dark:text-slate-400
                           hover:border-primary-400 hover:text-primary-500 cursor-pointer transition-colors text-sm">
                <i class="fas fa-plus text-[11px]"></i>${t('models_add_vendor')}
            </button>`;
        body = `<div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            ${configured.map(renderVendorChip).join('')}
            ${addTile}
        </div>`;
    }

    wrap.innerHTML = header + body;
    return wrap;
}

function renderVendorChip(p) {
    // The masked API key is intentionally not surfaced here; it is shown
    // inside the edit modal so the chip stays uncluttered and scannable.
    // Custom providers open their dedicated modal (name + base + key);
    // their ids are server-generated hex, safe to inline.
    const onclick = isCustomProviderCard(p)
        ? `openCustomProviderModal('${escapeHtml(p.custom_id)}')`
        : `openVendorModal('${escapeHtml(p.id)}')`;
    return `
        <button onclick="${onclick}"
                class="group flex items-center gap-3 px-3 py-2.5 rounded-lg border border-slate-200 dark:border-white/10
                       bg-slate-50 dark:bg-white/5 hover:border-primary-300 dark:hover:border-primary-500/50
                       cursor-pointer transition-colors duration-150 text-left">
            ${renderProviderLogo(p, 28)}
            <span class="flex-1 min-w-0 text-sm font-medium text-slate-800 dark:text-slate-100 truncate">${escapeHtml(localizedLabel(p.label))}</span>
            <i class="fas fa-pen-to-square text-[11px] text-slate-400 dark:text-slate-500 group-hover:text-primary-500 transition-colors"></i>
        </button>`;
}

// Render a uniformly-styled logo for a provider. Tries an SVG asset first; if
// it 404s the <img> swaps itself for a monogram fallback via onerror.
function renderProviderLogo(p, sizePx) {
    const initial = (localizedLabel(p.label) || p.id || '?').slice(0, 1).toUpperCase();
    const sz = sizePx || 32;
    const url = `${MODELS_PROVIDER_LOGO_PATH}/${encodeURIComponent(p.id)}.svg`;
    const fallbackId = `pl-${p.id}-${Math.random().toString(36).slice(2, 8)}`;
    const imgClass = MODELS_PROVIDER_LOGO_DARK_INVERT.has(p.id)
        ? 'absolute inset-0 m-auto provider-logo-img provider-logo-invert-dark'
        : 'absolute inset-0 m-auto provider-logo-img';
    return `
        <span class="relative flex items-center justify-center rounded-lg bg-slate-100 dark:bg-white/10
                     text-slate-600 dark:text-slate-300 flex-shrink-0 overflow-hidden"
              style="width:${sz}px;height:${sz}px;">
            <span id="${fallbackId}" class="text-xs font-bold">${escapeHtml(initial)}</span>
            <img src="${url}" alt="" aria-hidden="true"
                 class="${imgClass}"
                 style="width:${Math.round(sz * 0.65)}px;height:${Math.round(sz * 0.65)}px;"
                 onload="(function(el){var f=document.getElementById('${fallbackId}');if(f)f.style.display='none';})(this)"
                 onerror="this.remove();">
        </span>`;
}

function getCustomProviderCards() {
    return modelsState.providers.filter(isCustomProviderCard);
}

// ---------- Capability cards (Layer 2) ---------------------------------

function renderCapabilityCard(def) {
    const cap = modelsState.capabilities[def.id] || {};
    const wrap = document.createElement('div');
    wrap.className = 'bg-white dark:bg-[#1A1A1A] rounded-xl border border-slate-200 dark:border-white/10 p-6';
    wrap.id = `models-card-${def.id}`;

    const headerRight = renderCapabilityHeaderTag(def, cap);

    wrap.innerHTML = `
        <div class="flex items-start gap-3 mb-5">
            <div class="w-9 h-9 rounded-lg ${def.iconChip} flex items-center justify-center flex-shrink-0">
                <i class="fas ${def.icon} ${def.iconGlyph} text-sm"></i>
            </div>
            <div class="flex-1 min-w-0">
                <h3 class="font-semibold text-slate-800 dark:text-slate-100">${t(def.titleKey)}</h3>
                <p class="text-xs text-slate-500 dark:text-slate-400 mt-0.5">${t(def.descKey)}</p>
            </div>
            ${headerRight}
        </div>
        <div class="space-y-4" data-cap-body="${def.id}"></div>`;

    const body = wrap.querySelector(`[data-cap-body="${def.id}"]`);
    renderCapabilityBody(def, cap, body);
    return wrap;
}

function renderCapabilityHeaderTag(def, cap) {
    // The main model card carries a small gear that opens the chat-fallback
    // modal. The fallback is a rarely-touched safety net, so it stays out of
    // the card body; a badge appears next to the gear only while it is on, so
    // an active fallback is still discoverable at a glance.
    if (def.id === 'chat') {
        const fb = modelsState.capabilities.chat_fallback || {};
        // A single entry point that also reflects state: green + "on" label
        // when the fallback is enabled, muted + "configure" label when off.
        const on = !!fb.enabled;
        const cls = on
            ? 'text-primary-600 dark:text-primary-300 bg-primary-50 dark:bg-primary-900/30 hover:bg-primary-100 dark:hover:bg-primary-900/50'
            : 'text-slate-500 dark:text-slate-400 hover:text-primary-600 dark:hover:text-primary-300 hover:bg-slate-100 dark:hover:bg-white/5';
        const label = on ? t('models_fallback_badge_on') : t('models_fallback_config');
        return `
            <button type="button" onclick="openChatFallbackModal()"
                    title="${escapeHtml(t('models_fallback_config_tip'))}"
                    class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs flex-shrink-0
                           cursor-pointer transition-colors ${cls}">
                <i class="fas fa-shield-halved text-[11px]"></i>${label}
            </button>`;
    }
    return '';
}

// The chat fallback is configured in a modal rather than as a top-level card
// (it is a rarely-touched safety net). The modal body reuses the exact same
// picker machinery as a capability card — `renderCapabilityBody` keys every
// element off `cap-chat_fallback-*`, so we hand it a def with that id and let
// the existing provider/model/toggle/save code run unchanged. No such card is
// registered in MODELS_CAPABILITY_DEFS, so the ids never collide.
const CHAT_FALLBACK_DEF = {
    id: 'chat_fallback', editable: true, needsModel: true, toggleable: true,
    titleKey: 'models_fallback_modal_title', descKey: 'models_capability_chat_fallback_desc',
};

// Resolve a capability def by id. The chat fallback is intentionally absent
// from MODELS_CAPABILITY_DEFS (it renders in a modal, not as a card), so the
// shared save/toggle handlers look it up here too.
function capabilityDefById(capId) {
    if (capId === 'chat_fallback') return CHAT_FALLBACK_DEF;
    return MODELS_CAPABILITY_DEFS.find(d => d.id === capId);
}

function openChatFallbackModal() {
    closeChatFallbackModal(); // never stack two

    const overlay = document.createElement('div');
    overlay.id = 'chat-fallback-modal-overlay';
    overlay.className = 'fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4';
    overlay.innerHTML = `
        <div class="w-full max-w-md rounded-2xl bg-white dark:bg-[#1A1A1A] border border-slate-200 dark:border-white/10 shadow-xl">
            <div class="flex items-start gap-3 px-6 pt-6 pb-4 border-b border-slate-100 dark:border-white/5">
                <div class="w-9 h-9 rounded-lg bg-primary-50 dark:bg-primary-900/30 flex items-center justify-center flex-shrink-0">
                    <i class="fas fa-shield-halved text-primary-500 text-sm"></i>
                </div>
                <div class="flex-1 min-w-0">
                    <h3 class="font-semibold text-slate-800 dark:text-slate-100">${t('models_fallback_modal_title')}</h3>
                    <p class="text-xs text-slate-500 dark:text-slate-400 mt-1 leading-relaxed">${t('models_fallback_modal_desc')}</p>
                </div>
                <button type="button" onclick="closeChatFallbackModal()"
                        class="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 cursor-pointer transition-colors flex-shrink-0">
                    <i class="fas fa-xmark"></i>
                </button>
            </div>
            <div class="px-6 py-5 space-y-4" data-cap-body="chat_fallback"></div>
        </div>`;

    // Close on backdrop click (but not when clicking inside the dialog).
    overlay.addEventListener('click', (e) => { if (e.target === overlay) closeChatFallbackModal(); });

    document.body.appendChild(overlay);

    const cap = modelsState.capabilities.chat_fallback || {};
    const body = overlay.querySelector('[data-cap-body="chat_fallback"]');
    renderCapabilityBody(CHAT_FALLBACK_DEF, cap, body);
}

function closeChatFallbackModal() {
    const overlay = document.getElementById('chat-fallback-modal-overlay');
    if (overlay) overlay.remove();
}

function _searchProviderLabel(cap, providerId) {
    const list = (cap && cap.providers) || [];
    const hit = list.find(p => p.id === providerId);
    return hit ? localizedLabel(hit.label) : providerId;
}

// Search card body: strategy picker + (when fixed) provider picker + a
// status row that surfaces which providers are ready and how to add the
// missing ones. Three of the four backends piggy-back on model-vendor
// credentials (zhipu / qianfan / linkai); bocha owns its own key under
// tools.web_search and gets its own minimal credential modal.
function renderSearchCapability(def, cap, body) {
    const providers = cap.providers || [];
    const configuredIds = cap.configured_providers || [];
    const hasAny = configuredIds.length > 0;
    const strategy = cap.strategy || 'auto';

    body.innerHTML = `
        <div>
            <label class="block text-sm font-medium text-slate-600 dark:text-slate-400 mb-1.5">${t('models_search_strategy_label')}</label>
            <div id="cap-search-strategy" class="cfg-dropdown" tabindex="0">
                <div class="cfg-dropdown-selected">
                    <span class="cfg-dropdown-text">--</span>
                    <i class="fas fa-chevron-down cfg-dropdown-arrow"></i>
                </div>
                <div class="cfg-dropdown-menu"></div>
            </div>
        </div>
        <div id="cap-search-provider-wrap" class="hidden">
            <label class="block text-sm font-medium text-slate-600 dark:text-slate-400 mb-1.5">${t('models_provider')}</label>
            <div id="cap-search-provider" class="cfg-dropdown" tabindex="0">
                <div class="cfg-dropdown-selected">
                    <span class="cfg-dropdown-text">--</span>
                    <i class="fas fa-chevron-down cfg-dropdown-arrow"></i>
                </div>
                <div class="cfg-dropdown-menu"></div>
            </div>
        </div>
        <div id="cap-search-summary"></div>
        <div class="flex items-center justify-end gap-3 pt-1">
            <span id="cap-search-status" class="text-xs text-primary-500 opacity-0 transition-opacity duration-300"></span>
            <button onclick="saveSearchCapability()"
                    class="px-4 py-2 rounded-lg bg-primary-500 hover:bg-primary-600 text-white text-sm font-medium
                           cursor-pointer transition-colors duration-150 disabled:opacity-50 disabled:cursor-not-allowed">
                ${t('save')}
            </button>
        </div>
    `;

    // Strategy dropdown — when no provider is configured the strategy
    // value is meaningless, so we show a "待配置" placeholder instead of
    // a default selection. Once any provider gets configured the saved
    // strategy (or "auto") becomes the active value.
    initDropdown(
        body.querySelector('#cap-search-strategy'),
        [
            { value: 'auto',  label: t('models_strategy_auto'),         hint: t('models_search_strategy_auto_hint') },
            { value: 'fixed', label: t('models_search_strategy_fixed'), hint: t('models_search_strategy_fixed_hint') },
        ],
        hasAny ? strategy : '',
        (value) => _onSearchStrategyChange(cap, value, body),
        hasAny ? null : { placeholder: t('models_pending_config') },
    );

    // Provider dropdown — populated with configured providers only;
    // unconfigured ones cannot be pinned (they'd silently fall back).
    const provOpts = configuredIds.map(id => ({
        value: id,
        label: _searchProviderLabel(cap, id),
    }));
    if (provOpts.length === 0) provOpts.push({ value: '', label: '--' });
    initDropdown(
        body.querySelector('#cap-search-provider'),
        provOpts,
        cap.fixed_provider || configuredIds[0] || '',
        () => {},
    );

    _renderSearchSummary(body, cap);
    _setSearchProviderPickerVisible(body, strategy === 'fixed' && hasAny);
}

function _onSearchStrategyChange(cap, value, body) {
    const configuredIds = cap.configured_providers || [];
    _setSearchProviderPickerVisible(body, value === 'fixed' && configuredIds.length > 0);
}

function _setSearchProviderPickerVisible(body, visible) {
    const wrap = body.querySelector('#cap-search-provider-wrap');
    if (!wrap) return;
    if (visible) wrap.classList.remove('hidden');
    else wrap.classList.add('hidden');
}

// Search summary line: just lists configured providers + a trailing "+
// add" button. Unconfigured backends are hidden — the user picks one from
// a small chooser when they click add. Empty state surfaces the same add
// button as a primary CTA.
function _renderSearchSummary(body, cap) {
    const host = body.querySelector('#cap-search-summary');
    if (!host) return;
    const providers = cap.providers || [];
    const configured = providers.filter(p => p.configured);
    const missing = providers.filter(p => !p.configured);

    const addBtn = missing.length
        ? `<button type="button" id="cap-search-add-btn"
                  class="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] rounded-md cursor-pointer
                         bg-slate-100 dark:bg-white/5 text-slate-500 dark:text-slate-400
                         hover:bg-slate-200 dark:hover:bg-white/10 transition-colors">
              <i class="fas fa-plus text-[10px]"></i>${t('models_search_add_provider')}
           </button>`
        : '';

    if (configured.length === 0) {
        host.innerHTML = `
            <div class="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
                <i class="fas fa-circle-info text-[10px] text-amber-500"></i>
                <span>${t('models_search_none_configured')}</span>
                ${addBtn}
            </div>
        `;
    } else {
        const chips = configured.map(p => `
            <button type="button" data-search-edit-provider="${p.id}"
                    title="${t('models_search_edit_hint')}"
                    class="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] rounded-md cursor-pointer
                           bg-emerald-50 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400
                           hover:bg-emerald-100 dark:hover:bg-emerald-900/50 transition-colors">
                <i class="fas fa-check text-[10px]"></i>${escapeHtml(localizedLabel(p.label))}
            </button>
        `).join('');
        host.innerHTML = `
            <div class="flex items-center flex-wrap gap-2 text-xs text-slate-500 dark:text-slate-400">
                <span>${t('models_search_available_label')}</span>
                ${chips}
                ${addBtn}
            </div>
        `;
    }

    const addBtnEl = host.querySelector('#cap-search-add-btn');
    if (addBtnEl) {
        addBtnEl.addEventListener('click', (ev) => {
            ev.preventDefault();
            openSearchAddProviderPicker(missing);
        });
    }
    host.querySelectorAll('[data-search-edit-provider]').forEach(el => {
        el.addEventListener('click', (ev) => {
            ev.preventDefault();
            const pid = el.getAttribute('data-search-edit-provider');
            const meta = (cap.providers || []).find(p => p.id === pid);
            _launchSearchProviderConfig(pid, meta);
        });
    });
}

// Two-step add flow: click "+ 添加厂商" -> chooser dialog -> per-provider
// credential editor. Bocha lands on the dedicated key modal; the others
// piggy-back on the existing vendor credential modal.
function openSearchAddProviderPicker(missingProviders) {
    if (!missingProviders || missingProviders.length === 0) return;
    if (missingProviders.length === 1) {
        _launchSearchProviderConfig(missingProviders[0].id);
        return;
    }

    const existing = document.getElementById('search-add-modal');
    if (existing) existing.remove();

    const rows = missingProviders.map(p => `
        <button type="button" data-pid="${p.id}"
                class="w-full flex items-center justify-between px-3 py-2.5 rounded-lg cursor-pointer
                       bg-slate-50 dark:bg-white/5 hover:bg-slate-100 dark:hover:bg-white/10
                       text-sm text-slate-700 dark:text-slate-200 transition-colors">
            <span>${escapeHtml(localizedLabel(p.label))}</span>
            <i class="fas fa-chevron-right text-[10px] text-slate-400"></i>
        </button>
    `).join('');

    const modal = document.createElement('div');
    modal.id = 'search-add-modal';
    modal.className = 'fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm';
    modal.innerHTML = `
        <div class="bg-white dark:bg-[#1A1A1A] rounded-xl border border-slate-200 dark:border-white/10
                    w-full max-w-md mx-4 p-6 shadow-xl">
            <h3 class="text-lg font-semibold text-slate-800 dark:text-slate-100 mb-1">${t('models_search_add_provider')}</h3>
            <p class="text-xs text-slate-500 dark:text-slate-400 mb-4">${t('models_search_add_desc')}</p>
            <div class="space-y-2">${rows}</div>
            <div class="flex items-center justify-end mt-5">
                <button type="button" onclick="document.getElementById('search-add-modal').remove()"
                        class="px-3 py-1.5 rounded-md text-sm text-slate-600 dark:text-slate-300
                               hover:bg-slate-100 dark:hover:bg-white/5 transition-colors">
                    ${t('cancel')}
                </button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    modal.querySelectorAll('[data-pid]').forEach(el => {
        el.addEventListener('click', () => {
            const pid = el.getAttribute('data-pid');
            modal.remove();
            _launchSearchProviderConfig(pid);
        });
    });
}

function _launchSearchProviderConfig(providerId, providerMeta) {
    if (providerId === 'bocha' || providerId === 'anysearch' || providerId === 'serply') {
        openSearchKeyModal(providerId, providerMeta);
    } else {
        openVendorModal(providerId, () => loadModelsView({ preserveScroll: true }));
    }
}

function saveSearchCapability() {
    const strategyDd = document.getElementById('cap-search-strategy');
    const providerDd = document.getElementById('cap-search-provider');
    const strategy = strategyDd ? getDropdownValue(strategyDd) : 'auto';
    const provider = (strategy === 'fixed' && providerDd) ? getDropdownValue(providerDd) : '';

    fetch('/api/models', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            action: 'set_capability',
            capability: 'search',
            strategy,
            provider,
        }),
    }).then(r => r.json()).then(data => {
        if (data.status === 'success') {
            showStatus('cap-search-status', 'models_save_success', false);
            setTimeout(() => loadModelsView({ preserveScroll: true }), 400);
        } else {
            showStatus('cap-search-status', 'models_save_failed', true);
        }
    }).catch(() => showStatus('cap-search-status', 'models_save_failed', true));
}

// Minimal bocha API-key modal. Reuses the existing vendor-modal markup
// helpers would be nice, but bocha isn't in PROVIDER_MODELS (it's not a
// model vendor), so we render a tiny dedicated dialog.
// For search vendors that hold their own keys.

function openSearchKeyModal(providerId, providerMeta) {
    const existing = document.getElementById('search-key-modal');
    if (existing) existing.remove();

    let masked = (providerMeta && providerMeta.api_key_masked) || '';
    if (!masked) {
        const searchCap = (modelsState && modelsState.capabilities && modelsState.capabilities.search) || {};
        const bocha = (searchCap.providers || []).find(p => p.id === providerId);
        if (bocha && bocha.api_key_masked) masked = bocha.api_key_masked;
    }
    const hasKey = !!masked;
    const clearBtnHtml = hasKey
        ? `<button type="button" id="search-key-clear"
                  class="px-3 py-1.5 rounded-md text-xs text-red-500 dark:text-red-400
                         hover:bg-red-50 dark:hover:bg-red-900/20 cursor-pointer transition-colors">
              ${t('models_clear_credential')}
           </button>`
        : '';

    const modal = document.createElement('div');
    modal.id = 'search-key-modal';
    modal.className = 'fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm';
    modal.innerHTML = `
        <div id="search-key-modal-card"
             class="bg-white dark:bg-[#1A1A1A] rounded-xl border border-slate-200 dark:border-white/10
                    w-full max-w-md mx-4 p-6 shadow-xl">
            <h3 class="text-lg font-semibold text-slate-800 dark:text-slate-100 mb-1">${t('models_search_' + providerId + '_title')}</h3>
            <p class="text-xs text-slate-500 dark:text-slate-400 mb-4">${t('models_search_' + providerId + '_desc')}</p>
            <label class="block text-sm font-medium text-slate-600 dark:text-slate-400 mb-1.5">API Key</label>
            <input id="search-key-input" type="text" autocomplete="off" data-1p-ignore data-lpignore="true"
                   class="w-full px-3 py-2 rounded-lg border border-slate-200 dark:border-slate-600
                          bg-slate-50 dark:bg-white/5 text-sm text-slate-800 dark:text-slate-100
                          focus:outline-none focus:border-primary-500 font-mono ${hasKey ? 'cfg-key-masked' : ''}"
                   value="${escapeHtml(masked)}"
                   data-masked="${hasKey ? '1' : ''}"
                   placeholder="sk-..." />
            <div class="flex items-center justify-between gap-3 mt-5">
                <div>${clearBtnHtml}</div>
                <div class="flex items-center gap-3">
                    <button type="button" onclick="document.getElementById('search-key-modal').remove()"
                            class="px-3 py-1.5 rounded-md text-sm text-slate-600 dark:text-slate-300
                                   hover:bg-slate-100 dark:hover:bg-white/5 transition-colors">
                        ${t('cancel')}
                    </button>
                    <button type="button" onclick="_saveSearchKey('${providerId}')"
                            class="px-4 py-1.5 rounded-md bg-primary-500 hover:bg-primary-600 text-white text-sm font-medium
                                   cursor-pointer transition-colors">
                        ${t('save')}
                    </button>
                </div>
            </div>
        </div>
    `;
    document.body.appendChild(modal);

    // Reset masked sentinel as soon as the user starts editing so the save
    // handler can tell apart "kept the existing key" vs "typed a new one".
    const input = document.getElementById('search-key-input');
    if (input) {
        const unmask = () => {
            if (input.dataset.masked === '1') {
                input.value = '';
                input.dataset.masked = '';
                input.classList.remove('cfg-key-masked');
            }
        };
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Tab' || e.key === 'Escape') return;
            unmask();
        });
        input.addEventListener('paste', unmask);
        if (!hasKey) setTimeout(() => input.focus(), 50);
    }
    const clearBtn = document.getElementById('search-key-clear');
    if (clearBtn) clearBtn.addEventListener('click', () => _clearSearchKey(providerId));

    modal.addEventListener('mousedown', (e) => {
        if (e.target === modal) modal.remove();
    });
    const onKey = (e) => {
        if (e.key === 'Escape') {
            modal.remove();
            document.removeEventListener('keydown', onKey);
        }
    };
    document.addEventListener('keydown', onKey);
}

function _saveSearchKey(providerId) {
    const input = document.getElementById('search-key-input');
    if (!input) return;
    // Untouched masked value => no change requested; close silently.
    if (input.dataset.masked === '1') {
        const modal = document.getElementById('search-key-modal');
        if (modal) modal.remove();
        return;
    }
    const apiKey = input.value.trim();
    if (!apiKey) {
        input.focus();
        return;
    }
    fetch('/api/models', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'set_search_credential', provider: providerId, api_key: apiKey }),
    }).then(r => r.json()).then(data => {
        if (data.status === 'success') {
            const modal = document.getElementById('search-key-modal');
            if (modal) modal.remove();
            loadModelsView({ preserveScroll: true });
        }
    });
}

function _clearSearchKey(providerId) {
    fetch('/api/models', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'set_search_credential', provider: providerId, api_key: '' }),
    }).then(r => r.json()).then(data => {
        if (data.status === 'success') {
            const modal = document.getElementById('search-key-modal');
            if (modal) modal.remove();
            loadModelsView({ preserveScroll: true });
        }
    });
}

function renderCapabilityBody(def, cap, body) {
    if (def.id === 'search') {
        renderSearchCapability(def, cap, body);
        return;
    }

    // Editable cards: provider dropdown + (optional) model dropdown + save row
    const providerOpts = buildCapabilityProviderOptions(def, cap);
    const providerHtml = `
        <div>
            <label class="block text-sm font-medium text-slate-600 dark:text-slate-400 mb-1.5">${t('models_provider')}</label>
            <div id="cap-${def.id}-provider" class="cfg-dropdown" tabindex="0">
                <div class="cfg-dropdown-selected">
                    <span class="cfg-dropdown-text">--</span>
                    <i class="fas fa-chevron-down cfg-dropdown-arrow"></i>
                </div>
                <div class="cfg-dropdown-menu"></div>
            </div>
        </div>`;

    // The model-picker container is always emitted so the provider-change
    // handler can show/hide it; for `auto` capabilities it starts hidden and
    // gets toggled by setCapabilityModelPickerVisible.
    const modelHtml = def.needsModel ? `
        <div id="cap-${def.id}-model-wrap">
            <label class="block text-sm font-medium text-slate-600 dark:text-slate-400 mb-1.5">${t('models_model')}</label>
            <div id="cap-${def.id}-model" class="cfg-dropdown" tabindex="0">
                <div class="cfg-dropdown-selected">
                    <span class="cfg-dropdown-text">--</span>
                    <i class="fas fa-chevron-down cfg-dropdown-arrow"></i>
                </div>
                <div class="cfg-dropdown-menu"></div>
            </div>
            <div id="cap-${def.id}-model-custom-wrap" class="mt-2 hidden">
                <input id="cap-${def.id}-model-custom" type="text"
                       class="w-full px-3 py-2 rounded-lg border border-slate-200 dark:border-slate-600
                              bg-slate-50 dark:bg-white/5 text-sm text-slate-800 dark:text-slate-100
                              focus:outline-none focus:border-primary-500 font-mono transition-colors"
                       placeholder="custom model name">
            </div>
        </div>` : '';

    const dimHtml = (def.id === 'embedding' && cap.current_dim) ? `
        <p class="text-xs text-slate-400 dark:text-slate-500">
            <i class="fas fa-cube text-[10px] mr-1"></i>${t('models_dim_label')}: <span class="font-mono">${cap.current_dim}</span>
        </p>` : '';

    // Opt-in capabilities get an on/off switch above the pickers. Everything
    // below it is hidden while off, so a disabled fallback never looks like an
    // unconfigured one — it is simply not part of the setup.
    const toggleHtml = def.toggleable ? `
        <div id="cap-${def.id}-toggle-wrap" class="flex items-center justify-between gap-3">
            <label class="text-sm font-medium text-slate-600 dark:text-slate-400">${t('models_fallback_enable')}</label>
            <button type="button" id="cap-${def.id}-toggle" role="switch"
                    aria-checked="${cap.enabled ? 'true' : 'false'}"
                    onclick="toggleCapabilityEnabled('${def.id}')"
                    class="relative inline-flex h-5 w-9 flex-shrink-0 items-center rounded-full transition-colors cursor-pointer ${cap.enabled ? 'bg-primary-500' : 'bg-slate-200 dark:bg-slate-700'}">
                <span class="inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform ${cap.enabled ? 'translate-x-[18px]' : 'translate-x-[3px]'}"></span>
            </button>
        </div>` : '';

    // Footer layout: a "hint slot" (filled later by renderCapabilityHints for
    // auto-mode cards) sits on the left while status + save stay anchored on
    // the right. Keeping them on the same row means the save button hugs the
    // inputs above instead of being pushed down by a separate hint line.
    const footer = `
        <div class="flex items-center justify-between gap-3 pt-1">
            <div data-cap-hint="${def.id}" class="flex-1 min-w-0"></div>
            <div class="flex items-center gap-3 flex-shrink-0">
                <span id="cap-${def.id}-status" class="text-xs text-primary-500 opacity-0 transition-opacity duration-300"></span>
                <button onclick="saveCapability('${def.id}')"
                        class="px-4 py-2 rounded-lg bg-primary-500 hover:bg-primary-600 text-white text-sm font-medium
                               cursor-pointer transition-colors duration-150 disabled:opacity-50 disabled:cursor-not-allowed">
                    ${t('save')}
                </button>
            </div>
        </div>`;

    // Pickers live in their own wrapper so a disabled opt-in capability can
    // hide them as a group (the toggle itself stays visible above). The
    // wrapper carries its own `space-y-4` because the body's `space-y-4` only
    // applies to *direct* children: without it the provider/model rows would
    // collapse against each other (and against the label above them).
    const pickersHtml = `<div id="cap-${def.id}-pickers" class="space-y-4">${providerHtml + modelHtml + dimHtml}</div>`;
    body.innerHTML = toggleHtml + pickersHtml + footer;

    // TTS: mount reply-mode above provider; defer off-mode toggle to the end.
    if (def.id === 'tts') {
        renderVoiceReplyMode(body, cap.reply_mode || 'off', { skipVisibilityToggle: true });
        // Voice-timbre picker depends on provider+model; rebuilt by callbacks.
        const modelWrap = body.querySelector(`#cap-${def.id}-model-wrap`);
        if (modelWrap) {
            const voiceWrap = document.createElement('div');
            voiceWrap.id = `cap-${def.id}-voice-wrap`;
            voiceWrap.innerHTML = `
                <label class="block text-sm font-medium text-slate-600 dark:text-slate-400 mb-1.5">${t('models_voice')}</label>
                <div id="cap-${def.id}-voice" class="cfg-dropdown" tabindex="0">
                    <div class="cfg-dropdown-selected">
                        <span class="cfg-dropdown-text">--</span>
                        <i class="fas fa-chevron-down cfg-dropdown-arrow"></i>
                    </div>
                    <div class="cfg-dropdown-menu"></div>
                </div>
                <div id="cap-${def.id}-voice-custom-wrap" class="hidden mt-2">
                    <input id="cap-${def.id}-voice-custom" type="text"
                           class="w-full px-3 py-2 text-sm rounded-md border border-slate-200 dark:border-slate-700
                                  bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-200
                                  placeholder:text-slate-400 dark:placeholder:text-slate-500
                                  focus:outline-none focus:ring-2 focus:ring-primary-500"
                           placeholder="voice id" />
                </div>
            `;
            modelWrap.parentNode.insertBefore(voiceWrap, modelWrap.nextSibling);
        }
    }

    // `body` is still detached from `document`; scope lookups locally.
    const provDd = body.querySelector(`#cap-${def.id}-provider`);
    // Strip private fields before handing to the generic initDropdown helper.
    const ddOpts = providerOpts.map(o => ({ value: o.value, label: o.label }));

    let pendingProvider = null;
    if (pendingCapabilitySelection
            && pendingCapabilitySelection.capabilityId === def.id
            && providerOpts.some(o => o.value === pendingCapabilitySelection.providerId)) {
        pendingProvider = pendingCapabilitySelection.providerId;
        pendingCapabilitySelection = null;
    }

    // Auto strategy => leave empty sentinel selected. `suggested_provider`
    // is a UI-only preselect (not persisted until the user clicks Save).
    // No current + no suggestion => leave unselected with a placeholder.
    //
    // Pending-config takes priority over both "auto" and "pick provider":
    // when no real (non-sentinel) configured option exists, surfacing
    // "auto" or "pick" misleads the user — there's nothing to auto-route
    // to or pick from. Force a "待配置" placeholder instead so all
    // capabilities behave consistently on a fresh environment.
    const hasConfiguredOpt = providerOpts.some(o => !o._isAuto && o._configured);
    const noSelectionAndNoHint = !cap.current_provider && !cap.suggested_provider;
    let initialProviderValue;
    let dropdownPlaceholder = null;
    if (!hasConfiguredOpt) {
        initialProviderValue = '';
        dropdownPlaceholder = { placeholder: t('models_pending_config') };
    } else {
        initialProviderValue = pendingProvider
            ? pendingProvider
            : ((cap.strategy === 'auto' && capabilitySupportsAuto(def.id))
                ? ''
                : (cap.current_provider
                    || cap.suggested_provider
                    || (noSelectionAndNoHint ? '' : (ddOpts[0] && ddOpts[0].value))
                    || ''));
        if (noSelectionAndNoHint) {
            dropdownPlaceholder = { placeholder: t('models_pick_provider') };
        }
    }
    // Seed the "provider active before the last switch" tracker so the very
    // first vendor switch can still stash the initial provider's custom model.
    capabilityLastProviderId[def.id] = initialProviderValue;
    // If the initially selected model is a custom one, remember it against the
    // initial provider so a switch-away-and-back keeps it too.
    if (initialProviderValue && cap.current_model) {
        const provList = (cap.provider_models && cap.provider_models[initialProviderValue])
            || (initialProviderValue.startsWith('custom:') && cap.provider_models && cap.provider_models['custom'])
            || [];
        const presetValues = provList.map(e => (typeof e === 'string' ? e : e.value));
        if (!presetValues.includes(cap.current_model)) {
            capabilityCustomModelMemory[`${def.id}:${initialProviderValue}`] = cap.current_model;
        }
    }
    initDropdown(
        provDd,
        ddOpts,
        initialProviderValue,
        (value) => onCapabilityProviderChange(def, value, body),
        dropdownPlaceholder,
    );
    decorateCapabilityProviderDropdown(def, provDd, providerOpts);

    if (def.needsModel) {
        rebuildCapabilityModelDropdown(def, initialProviderValue, cap.current_model || '', body);
        // Embedding: hide model picker when no provider is selected.
        const showModel = def.id === 'embedding' ? initialProviderValue !== '' :
            (initialProviderValue !== '' || !capabilitySupportsAuto(def.id));
        setCapabilityModelPickerVisible(def, showModel, body);
    }

    if (def.id === 'tts') {
        rebuildCapabilityVoiceDropdown(
            initialProviderValue,
            cap.current_voice || '',
            body,
            cap.current_model || ''
        );
    }

    // Inject auto/router-pending hint banners before the action footer.
    renderCapabilityHints(def, cap, body, initialProviderValue);

    // Opt-in capabilities start collapsed when disabled, so an inactive
    // fallback reads as "off" rather than as a half-configured capability.
    if (def.toggleable) {
        _setCapabilityPickersVisible(def, body, !!cap.enabled);
    }

    if (def.id === 'tts') {
        _setTtsConfigVisible(body, (cap.reply_mode || 'off') !== 'off');
    }
}

// TTS reply-policy dropdown (off / voice_if_voice / always). Persists on
// change. When off, hides the rest of the TTS card.
function renderVoiceReplyMode(host, currentMode, options) {
    options = options || {};
    const opts = [
        { value: 'off',            label: t('voice_reply_off') },
        { value: 'voice_if_voice', label: t('voice_reply_if_voice') },
        { value: 'always',         label: t('voice_reply_always') },
    ];
    const wrap = document.createElement('div');
    wrap.id = 'voice-reply-mode-wrap';
    wrap.innerHTML = `
        <label class="block text-sm font-medium text-slate-600 dark:text-slate-400 mb-1.5">${t('voice_reply_mode_label')}</label>
        <div id="voice-reply-mode-dd" class="cfg-dropdown" tabindex="0">
            <div class="cfg-dropdown-selected">
                <span class="cfg-dropdown-text">--</span>
                <i class="fas fa-chevron-down cfg-dropdown-arrow"></i>
            </div>
            <div class="cfg-dropdown-menu"></div>
        </div>
    `;
    host.prepend(wrap);

    const dd = wrap.querySelector('#voice-reply-mode-dd');
    const valid = ['off', 'voice_if_voice', 'always'];
    const initial = valid.includes(currentMode) ? currentMode : 'off';
    if (!options.skipVisibilityToggle) _setTtsConfigVisible(host, initial !== 'off');
    initDropdown(dd, opts, initial, (mode) => {
        if (!valid.includes(mode)) return;
        _setTtsConfigVisible(host, mode !== 'off');
        fetch('/api/models', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'set_voice_reply_mode', mode }),
        })
            .then(r => r.json())
            .then(data => {
                if (data && data.status === 'success') {
                    _ttsReadyPromise = null;  // force re-probe on next bubble
                }
            })
            .catch(() => {});
    });
}

// Show/hide everything in the TTS card below the reply-mode dropdown.
function _setTtsConfigVisible(host, visible) {
    if (!host) return;
    Array.from(host.children).forEach((child) => {
        if (child.id === 'voice-reply-mode-wrap') return;
        child.classList.toggle('hidden', !visible);
    });
}

// Toggle wrapper visibility instead of re-rendering so dropdown state survives.
function setCapabilityModelPickerVisible(def, visible, scope) {
    const root = scope || document;
    const wrap = root.querySelector(`#cap-${def.id}-model-wrap`);
    if (!wrap) return;
    wrap.classList.toggle('hidden', !visible);
}

function renderCapabilityHints(def, cap, body, currentProvider) {
    // Capabilities that can be in "auto" mode show a fallback hint right
    // under the inputs so users always know what'd actually be hit. The
    // image card additionally surfaces a "router pending" warning until the
    // standalone dispatcher lands.
    // The hint slot is co-located with the save button in the footer row
    // (see renderCapabilityBody) so the save button stays close to the
    // inputs above. We just rewrite the slot's innerHTML — emptying it
    // when the card leaves auto mode, or rendering a one-line hint when
    // it's in auto mode.
    const slot = body.querySelector(`[data-cap-hint="${def.id}"]`);
    if (!slot) return;
    slot.innerHTML = '';

    if (currentProvider !== '' || !capabilitySupportsAuto(def.id)) return;

    // The hint mirrors what the runtime would actually pick when in auto
    // mode. fallback_provider/model are pre-computed on the backend (see
    // _predict_vision_auto, _predict_image_auto) so we can trust them
    // here without re-implementing the provider chain.
    const fbProv = cap.fallback_provider || '';
    const fbModel = cap.fallback_model || '';
    if (!fbProv && !fbModel) return;
    // Show the vendor's display label (e.g. "LinkAI") instead of the raw
    // id ("linkai") when we know it. Falls back to the id when the
    // provider isn't in our vendor table (rare).
    const provMeta = modelsState.providers.find(p => p.id === fbProv);
    const fbProvLabel = (provMeta && localizedLabel(provMeta.label)) || fbProv;
    const fbText = fbModel ? `${fbProvLabel} / ${fbModel}` : fbProvLabel;
    slot.innerHTML = `
        <p class="flex items-center gap-1.5 text-xs text-slate-400 dark:text-slate-500 min-w-0">
            <i class="fas fa-circle-info text-[10px] flex-shrink-0"></i>
            <span class="flex-shrink-0">${t('models_auto_using')}</span>
            <span class="font-mono text-slate-500 dark:text-slate-400 truncate">${escapeHtml(fbText)}</span>
        </p>`;
}

function buildCapabilityProviderOptions(def, cap) {
    // Show ALL vendors in capability dropdowns so users can see at a glance
    // who's configured (green check) and who isn't (gray dot, click to set
    // up). The list order puts configured vendors first; clicking an
    // unconfigured row opens the vendor modal in-place. ASR/TTS engines that
    // aren't tracked by PROVIDER_MODELS (azure/baidu/google etc.) are treated
    // as "always available" — no credential gate.
    const knownProviderMap = {};
    modelsState.providers.forEach(p => { knownProviderMap[p.id] = p; });

    const explicitList = cap.providers && cap.providers.length ? cap.providers : null;
    let providerIds = explicitList ? explicitList.slice() : modelsState.providers.map(p => p.id);
    if (cap.current_provider && !providerIds.includes(cap.current_provider)) {
        providerIds = [cap.current_provider, ...providerIds];
    }

    const opts = providerIds.map(pid => {
        const meta = knownProviderMap[pid];
        const tracked = !!meta;
        const configured = !tracked || !!meta.configured;
        return {
            value: pid,
            label: (meta && localizedLabel(meta.label)) || pid,
            _tracked: tracked,
            _configured: configured,
        };
    });

    opts.sort((a, b) => {
        if (a._configured === b._configured) return 0;
        return a._configured ? -1 : 1;
    });

    // Capabilities with a fallback ("auto") strategy expose it as a sentinel
    // option pinned to the top of the list. We use empty-string as the auto
    // value so the existing save handler propagates it untouched to the
    // backend, which interprets "" as "fall back to the main model".
    // Skip the sentinel when no real vendor is configured — "auto" would
    // route to nothing useful and the renderer will show "待配置" instead.
    const hasAnyConfigured = opts.some(o => o._configured);
    if ((cap.strategy === 'auto' || cap.strategy === 'specified') && hasAnyConfigured) {
        if (capabilitySupportsAuto(def.id)) {
            opts.unshift({
                value: '',
                label: t('models_strategy_auto'),
                _tracked: false,
                _configured: true,
                _isAuto: true,
            });
        }
    }
    return opts;
}

function capabilitySupportsAuto(capId) {
    // Embedding is intentionally NOT here: runtime only auto-falls back to
    // OpenAI/LinkAI, so dressing it up as "auto" hides reality from users.
    return capId === 'image' || capId === 'vision';
}

// After initDropdown renders the capability provider menu, decorate each
// row with the right-aligned configuration cue:
//   - configured rows: nothing extra — the .active marker (a brand-green ✓)
//     already comes from initDropdown's selected-state CSS for the row the
//     user currently picked. Other configured rows show no chrome, mirroring
//     a plain "switch to this" selector.
//   - unconfigured rows: a subdued gear icon hints at "click to configure".
//     The row's whole click handler is swapped to launch the vendor modal
//     in place rather than selecting an unusable value.
function decorateCapabilityProviderDropdown(def, ddEl, opts) {
    if (!ddEl) return;
    const menu = ddEl.querySelector('.cfg-dropdown-menu');
    if (!menu) return;

    const optByValue = {};
    opts.forEach(o => { optByValue[o.value] = o; });

    menu.querySelectorAll('.cfg-dropdown-item').forEach(item => {
        const value = item.dataset.value;
        const opt = optByValue[value];
        if (!opt) return;
        item.classList.add('cap-provider-item');
        if (!opt._configured) item.classList.add('cap-provider-unconfigured');

        // Wrap the label so the trailing affordance lines up via flex:auto.
        const labelText = item.textContent;
        item.textContent = '';
        const labelEl = document.createElement('span');
        labelEl.className = 'cap-provider-label';
        labelEl.textContent = labelText;
        item.appendChild(labelEl);

        if (!opt._configured) {
            // Trailing gear icon as the "configure this vendor" affordance.
            const gear = document.createElement('i');
            gear.className = 'fas fa-gear cap-provider-gear';
            item.appendChild(gear);
        }

        if (!opt._configured && opt._tracked) {
            // Hijack the click: open the vendor modal instead of selecting
            // an unusable value, and remember which capability the user was
            // configuring so the post-save reload can preselect the vendor.
            const newItem = item.cloneNode(true);
            item.replaceWith(newItem);
            newItem.addEventListener('click', (e) => {
                e.stopPropagation();
                ddEl.classList.remove('open');
                openVendorModal(value, (savedProviderId) => {
                    pendingCapabilitySelection = {
                        capabilityId: def.id,
                        providerId: savedProviderId || value,
                    };
                    loadModelsView({ preserveScroll: true });
                });
            });
        }
    });
}

// Lightweight decorator for the "add vendor" modal's provider picker:
// every configured vendor row gets a trailing brand-green ✓ so the user can
// see at a glance who's already set up, without having to read each row.
// Unlike decorateCapabilityProviderDropdown we don't hijack clicks here —
// picking an unconfigured vendor in this modal *is* the intended action.
function decorateVendorModalPicker(ddEl, opts) {
    if (!ddEl) return;
    const menu = ddEl.querySelector('.cfg-dropdown-menu');
    if (!menu) return;

    const optByValue = {};
    opts.forEach(o => { optByValue[o.value] = o; });

    menu.querySelectorAll('.cfg-dropdown-item').forEach(item => {
        const opt = optByValue[item.dataset.value];
        if (!opt) return;
        // Tag the row so the global active-row ✓ rule is suppressed in CSS
        // (otherwise configured AND selected rows would render two checks).
        item.classList.add('vendor-picker-item');
        if (opt._isAddNew) {
            // "Custom" is an add-new action (multiple entries allowed),
            // so show a trailing + instead of the configured ✓.
            const plus = document.createElement('i');
            plus.className = 'fas fa-plus vendor-picker-add-mark';
            item.appendChild(plus);
            return;
        }
        if (!opt._configured) return;
        const check = document.createElement('i');
        check.className = 'fas fa-check vendor-picker-configured-mark';
        item.appendChild(check);
    });
}

function rebuildCapabilityModelDropdown(def, providerId, selectedModel, scope) {
    // `scope` lets the caller (renderCapabilityBody) target a still-detached
    // subtree. After the card is mounted, callers may pass `document` instead.
    const root = scope || document;
    const el = root.querySelector(`#cap-${def.id}-model`);
    if (!el) return;

    // Prefer the capability-scoped model list when the backend provides one
    // (vision / image). It reflects the models the runtime can actually
    // dispatch to for this capability, instead of the vendor's full chat-
    // model catalog. Fall back to the generic provider.models for chat /
    // embedding / tts where any vendor model is fair game.
    //
    // Entries may be plain strings or {value, hint} objects (image catalog
    // uses the latter to surface brand aliases like "Nano Banana 2" next to
    // the technical Gemini model id). We normalize to {value, label, hint}
    // before handing off to initDropdown.
    const cap = modelsState.capabilities[def.id] || {};
    const capModelMap = cap.provider_models || {};
    let rawList;
    if (capModelMap[providerId]) {
        rawList = capModelMap[providerId].slice();
    } else if (providerId.startsWith('custom:') && capModelMap['custom']) {
        // Expanded custom:<id> entries share the same preset model list
        rawList = capModelMap['custom'].slice();
    } else {
        const provider = modelsState.providers.find(p => p.id === providerId);
        rawList = (provider && provider.models) ? provider.models.slice() : [];
    }
    const modelValues = [];
    const opts = rawList.map(entry => {
        if (typeof entry === 'string') {
            modelValues.push(entry);
            return { value: entry, label: entry };
        }
        modelValues.push(entry.value);
        return { value: entry.value, label: entry.label || entry.value, hint: entry.hint || '' };
    });
    opts.push({ value: '__custom__', label: currentLang === 'zh' ? '自定义' : 'Custom' });

    let initialValue = selectedModel || '';
    if (initialValue && !modelValues.includes(initialValue)) {
        initialValue = '__custom__';
    }
    if (!initialValue && opts.length) initialValue = opts[0].value;

    initDropdown(el, opts, initialValue, (value) => {
        const customWrap = document.getElementById(`cap-${def.id}-model-custom-wrap`);
        if (customWrap) {
            if (value === '__custom__') {
                customWrap.classList.remove('hidden');
                const input = document.getElementById(`cap-${def.id}-model-custom`);
                if (input && !input.value) input.value = selectedModel || '';
            } else {
                customWrap.classList.add('hidden');
            }
        }
        // TTS voice catalog may be scoped per engine model (aggregating
        // gateways). Rebuild the voice picker whenever the model changes.
        if (def.id === 'tts') {
            const provDd = document.getElementById('cap-tts-provider');
            const provId = provDd ? getDropdownValue(provDd) : '';
            rebuildCapabilityVoiceDropdown(provId, '', null, value);
        }
    });

    const customWrap = root.querySelector(`#cap-${def.id}-model-custom-wrap`);
    if (customWrap) {
        if (initialValue === '__custom__') {
            customWrap.classList.remove('hidden');
            const input = root.querySelector(`#cap-${def.id}-model-custom`);
            if (input) input.value = selectedModel || '';
        } else {
            customWrap.classList.add('hidden');
        }
    }
}

// TTS-only: rebuild the voice timbre picker against the provider's
// curated voice list. Hidden when no provider is picked.
//
// Each voice entry may be:
//   - a bare string  (code = label)
//   - {value, label, hint?}   so we can show a friendly Chinese name
//     while persisting the raw API code that the runtime sends.
function rebuildCapabilityVoiceDropdown(providerId, selectedVoice, scope, modelId) {
    const root = scope || document;
    const wrap = root.querySelector(`#cap-tts-voice-wrap`);
    const el = root.querySelector(`#cap-tts-voice`);
    if (!wrap || !el) return;
    const cap = modelsState.capabilities.tts || {};
    const voicesByProvider = cap.provider_voices || {};
    let raw = (providerId && voicesByProvider[providerId]) || [];
    // Some providers (gateways) scope voices by engine model id.
    if (raw && !Array.isArray(raw) && typeof raw === 'object') {
        const activeModel = modelId
            || (root.querySelector(`#cap-tts-model`) ? getDropdownValue(root.querySelector(`#cap-tts-model`)) : '');
        raw = (activeModel && raw[activeModel]) || [];
    }
    if (!raw || raw.length === 0) {
        wrap.classList.add('hidden');
        return;
    }
    wrap.classList.remove('hidden');
    // Voice picker: friendly name on the left, raw API code as right-hand
    // hint. Persisted/sent value is always the raw code.
    const codes = [];
    const opts = raw.map(entry => {
        if (typeof entry === 'string') {
            codes.push(entry);
            return { value: entry, label: entry };
        }
        codes.push(entry.value);
        const code = entry.value;
        const desc = entry.hint || entry.label || code;
        return {
            value: code,
            label: desc,
            hint: desc === code ? '' : code,
        };
    });
    opts.push({ value: '__custom__', label: currentLang === 'zh' ? '自定义' : 'Custom' });

    // Off-catalog values route through the custom branch.
    let initial = selectedVoice || '';
    const isCustom = initial && !codes.includes(initial);
    if (isCustom) initial = '__custom__';
    if (!initial) initial = codes[0];

    initDropdown(el, opts, initial, (value) => {
        const customWrap = root.querySelector(`#cap-tts-voice-custom-wrap`);
        if (!customWrap) return;
        if (value === '__custom__') {
            customWrap.classList.remove('hidden');
            const input = root.querySelector(`#cap-tts-voice-custom`);
            if (input && !input.value) input.value = isCustom ? selectedVoice : '';
        } else {
            customWrap.classList.add('hidden');
        }
    });

    const customWrap = root.querySelector(`#cap-tts-voice-custom-wrap`);
    if (customWrap) {
        if (initial === '__custom__') {
            customWrap.classList.remove('hidden');
            const input = root.querySelector(`#cap-tts-voice-custom`);
            if (input) input.value = isCustom ? selectedVoice : '';
        } else {
            customWrap.classList.add('hidden');
        }
    }
}

function onCapabilityProviderChange(def, providerId, scope) {
    if (def.needsModel) {
        // Before rebuilding the model picker for the newly picked provider,
        // stash the custom model the user had typed under the *previous*
        // provider, so switching back to it later restores that value.
        const prevProvider = capabilityLastProviderId[def.id];
        if (prevProvider && prevProvider !== providerId) {
            const prevDd = document.getElementById(`cap-${def.id}-model`);
            const prevInput = document.getElementById(`cap-${def.id}-model-custom`);
            if (prevDd && prevInput && getDropdownValue(prevDd) === '__custom__') {
                const typed = prevInput.value.trim();
                if (typed) capabilityCustomModelMemory[`${def.id}:${prevProvider}`] = typed;
            }
        }
        capabilityLastProviderId[def.id] = providerId;

        // Embedding: hide model picker when no provider is selected.
        const showModel = def.id === 'embedding' ? providerId !== '' :
            !(providerId === '' && capabilitySupportsAuto(def.id));
        if (showModel) {
            // Restore a remembered custom model for this provider (if any) so
            // switching vendors and back does not drop it.
            const remembered = capabilityCustomModelMemory[`${def.id}:${providerId}`] || '';
            rebuildCapabilityModelDropdown(def, providerId, remembered, scope);
        }
        setCapabilityModelPickerVisible(def, showModel, scope);
    }
    if (def.id === 'tts') {
        rebuildCapabilityVoiceDropdown(providerId, '', scope);
    }
    const body = scope || document.querySelector(`[data-cap-body="${def.id}"]`);
    if (body) {
        const cap = modelsState.capabilities[def.id] || {};
        renderCapabilityHints(def, cap, body, providerId);
    }
}

function getCapabilityModelValue(def) {
    if (!def.needsModel) return '';
    const dd = document.getElementById(`cap-${def.id}-model`);
    if (!dd) return '';
    const v = getDropdownValue(dd);
    if (v === '__custom__') {
        const input = document.getElementById(`cap-${def.id}-model-custom`);
        return input ? input.value.trim() : '';
    }
    return v || '';
}

// Opt-in capabilities: show/hide the pickers under the toggle without
// touching config. Mirrors the TTS reply-mode pattern — the toggle itself is
// pure UI state until the user presses Save.
function _setCapabilityPickersVisible(def, body, visible) {
    const wrap = body.querySelector(`#cap-${def.id}-pickers`);
    if (wrap) wrap.classList.toggle('hidden', !visible);
}

// Clicking the toggle flips the local switch. Persisting is a separate act
// (Save), so a user can flip back without ever writing to config.
function toggleCapabilityEnabled(capId) {
    const def = capabilityDefById(capId);
    if (!def || !def.toggleable) return;
    const cap = modelsState.capabilities[capId] || {};
    cap.enabled = !cap.enabled;
    modelsState.capabilities[capId] = cap;
    const btn = document.getElementById(`cap-${capId}-toggle`);
    if (btn) {
        btn.setAttribute('aria-checked', cap.enabled ? 'true' : 'false');
        btn.classList.toggle('bg-primary-500', cap.enabled);
        btn.classList.toggle('bg-slate-200', !cap.enabled);
        btn.classList.toggle('dark:bg-slate-700', !cap.enabled);
        const knob = btn.querySelector('span');
        if (knob) {
            knob.classList.toggle('translate-x-[18px]', cap.enabled);
            knob.classList.toggle('translate-x-[3px]', !cap.enabled);
        }
    }
    // Same lookup the rest of the file uses for a capability body.
    const body = document.querySelector(`[data-cap-body="${capId}"]`);
    if (body) _setCapabilityPickersVisible(def, body, cap.enabled);
}

function saveCapability(capId) {
    const def = capabilityDefById(capId);
    if (!def || !def.editable) return;
    // Search has its own form (strategy + provider, no model picker).
    if (capId === 'search') { saveSearchCapability(); return; }
    const provDd = document.getElementById(`cap-${capId}-provider`);
    const provider = provDd ? getDropdownValue(provDd) : '';
    // When the user is in auto mode (provider == ""), the model picker is
    // hidden and any value left in it is stale; persist an empty model so
    // the backend treats this as "fall back to the runtime chain".
    const isAuto = provider === '' && capabilitySupportsAuto(capId);
    // Embedding without a provider similarly means "cleared" — don't leak
    // a stale model value into config.
    const model = (isAuto || (capId === 'embedding' && !provider)) ? '' : getCapabilityModelValue(def);
    // TTS carries an extra voice timbre (supports free-text custom ids).
    let voice = '';
    if (capId === 'tts' && !isAuto) {
        const voiceDd = document.getElementById(`cap-${capId}-voice`);
        voice = voiceDd ? getDropdownValue(voiceDd) : '';
        if (voice === '__custom__') {
            const input = document.getElementById(`cap-${capId}-voice-custom`);
            voice = input ? input.value.trim() : '';
        }
    }

    // Embedding changes invalidate any pre-existing vector index because
    // dimensions / vendor differ. Gate the save behind a confirm, and on
    // success surface a dedicated info dialog telling the user how to
    // rebuild — both via the in-app custom dialog, not the native alert.
    if (capId === 'embedding') {
        const cap = modelsState.capabilities[capId] || {};
        const before = (cap.current_provider || '').trim();
        const after = (provider || '').trim();
        if (before !== after) {
            showConfirmDialog({
                title: t('models_embedding_change_title'),
                message: t('models_embedding_change_msg'),
                okText: t('save'),
                cancelText: t('cancel'),
                onConfirm: () => _persistCapability(capId, provider, model, () => {
                    showConfirmDialog({
                        title: t('models_embedding_saved_title'),
                        message: t('models_embedding_saved_msg'),
                        okText: t('models_embedding_saved_ok'),
                        hideCancel: true,
                        onConfirm: () => {
                            navigateTo('chat');
                            // Defer focus + value set: navigateTo may
                            // re-render the chat panel; setting value before
                            // the input is mounted would be lost.
                            setTimeout(() => {
                                const input = document.getElementById('chat-input');
                                if (!input) return;
                                input.value = '/memory rebuild-index';
                                input.focus();
                                // Trigger any input listeners (autosize, send-button enable, etc.)
                                input.dispatchEvent(new Event('input', { bubbles: true }));
                            }, 60);
                        },
                    });
                }),
            });
            return;
        }
    }
    // Opt-in capabilities persist their on/off switch alongside the pickers.
    // It is sent even when turning off, so a broken entry can always be
    // cleared — and the backend refuses to enable a half-filled one.
    let enabled = undefined;
    if (def.toggleable) {
        const cap = modelsState.capabilities[capId] || {};
        enabled = !!cap.enabled;
    }
    // The chat fallback is edited inside a modal; close it once the save
    // lands so the user drops straight back to the models page (already
    // reloaded by _persistCapability, which refreshes the main-card badge).
    const onAfterSuccess = capId === 'chat_fallback' ? closeChatFallbackModal : undefined;
    _persistCapability(capId, provider, model, onAfterSuccess, { voice, enabled });
}

function _persistCapability(capId, provider, model, onAfterSuccess, extras) {
    const payload = { action: 'set_capability', capability: capId, provider_id: provider, model: model };
    if (extras && extras.voice !== undefined) payload.voice = extras.voice;
    // Opt-in capabilities (the chat fallback) carry their on/off switch.
    if (extras && extras.enabled !== undefined) payload.enabled = extras.enabled;
    fetch('/api/models', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    }).then(r => r.json()).then(data => {
        if (data.status === 'success') {
            // Flash "Saved" before reload so the status survives the rebuild.
            showStatus(`cap-${capId}-status`, 'models_save_success', false);
            setTimeout(() => {
                loadModelsView({ preserveScroll: true });
                if (onAfterSuccess) onAfterSuccess();
            }, 400);
        } else {
            showStatus(`cap-${capId}-status`, 'models_save_failed', true);
        }
    }).catch(() => showStatus(`cap-${capId}-status`, 'models_save_failed', true));
}

// ---------- Vendor credential modal ------------------------------------

let vendorModalState = { providerId: '', onSaved: null };

function openVendorModal(providerId, onSaved) {
    vendorModalState = { providerId: providerId || '', onSaved: onSaved || null };

    const overlay = document.getElementById('vendor-modal-overlay');
    const titleEl = document.getElementById('vendor-modal-title');
    const subEl = document.getElementById('vendor-modal-subtitle');
    const pickerWrap = document.getElementById('vendor-modal-picker-wrap');
    const baseWrap = document.getElementById('vendor-modal-base-wrap');
    const baseInput = document.getElementById('vendor-modal-base');
    const baseHint = document.getElementById('vendor-modal-base-hint');
    const keyInput = document.getElementById('vendor-modal-key');
    const clearBtn = document.getElementById('vendor-modal-clear');

    // Reset any leftover status (e.g. previous "Saved" message)
    const statusEl = document.getElementById('vendor-modal-status');
    if (statusEl) {
        statusEl.textContent = '';
        statusEl.classList.add('opacity-0');
    }

    if (!providerId) {
        // Add flow — show provider picker, default to the first unconfigured one.
        // We render every configured vendor with a trailing green ✓ via the
        // dropdown decorator, mirroring the visual language used by the
        // capability provider dropdowns. The .active row already shows the
        // currently selected vendor via its own background highlight, so we
        // intentionally suppress the global active-row ✓ for this picker
        // (see CSS) — otherwise configured + selected rows would show two.
        // Expanded custom provider cards ("custom:<id>") are edited via their
        // dedicated modal, so they are excluded from this picker. Picking the
        // "custom" entry creates a *new* custom provider via that modal —
        // this is how multiple OpenAI-compatible endpoints are added.
        const builtinProviders = modelsState.providers.filter(p => !isCustomProviderCard(p));
        const pickerOpts = builtinProviders.map(p => ({
            value: p.id,
            label: localizedLabel(p.label),
            _configured: !!p.configured,
        }));
        // In multi-provider mode the backend replaces the bare "custom" card
        // with the expanded ones; re-add it here so the entry stays available.
        if (!pickerOpts.some(o => o.value === 'custom')) {
            pickerOpts.push({ value: 'custom', label: t('models_custom_vendor_label'), _configured: false });
        }
        // "Custom" always behaves as an add-new action (multiple entries
        // allowed), so it shows a + mark instead of the configured ✓.
        pickerOpts.forEach(o => { if (o.value === 'custom') { o._isAddNew = true; o._configured = false; } });
        const unconfigured = builtinProviders.filter(p => !p.configured);
        const defaultId = (unconfigured[0] && unconfigured[0].id) || (builtinProviders[0] && builtinProviders[0].id) || 'custom';
        pickerWrap.classList.remove('hidden');
        const pickerEl = document.getElementById('vendor-modal-picker');
        const onPick = (val) => {
            if (val === 'custom') {
                // "Custom" in the add flow always creates a new
                // OpenAI-compatible provider entry via the dedicated modal
                // (name + base + key), supporting multiple custom endpoints.
                closeVendorModal();
                openCustomProviderModal('');
                return;
            }
            fillVendorModalForProvider(val);
        };
        initDropdown(pickerEl, pickerOpts, defaultId, onPick);
        decorateVendorModalPicker(pickerEl, pickerOpts);
        onPick(defaultId);
    } else {
        pickerWrap.classList.add('hidden');
        fillVendorModalForProvider(providerId);
    }

    overlay.classList.remove('hidden');

    document.getElementById('vendor-modal-cancel').onclick = closeVendorModal;
    document.getElementById('vendor-modal-save').onclick = saveVendorModal;
    clearBtn.onclick = clearVendorModal;

    // Once the user edits the masked value, drop the "masked sentinel" dataset
    // so the save handler treats their input as a real new key. We compare on
    // the next tick because keydown fires before the new char lands in .value.
    keyInput.oninput = function () {
        if (keyInput.dataset.masked === '1' && keyInput.value !== keyInput.dataset.maskedVal) {
            keyInput.dataset.masked = '';
        }
    };

    function onOverlayClick(e) {
        if (e.target === overlay) {
            closeVendorModal();
            overlay.removeEventListener('click', onOverlayClick);
        }
    }
    overlay.addEventListener('click', onOverlayClick);
    keyInput.focus();
}

function fillVendorModalForProvider(providerId) {
    const meta = modelsState.providers.find(p => p.id === providerId);
    if (!meta) return;
    document.getElementById('vendor-modal-title').textContent = localizedLabel(meta.label);
    document.getElementById('vendor-modal-subtitle').textContent = meta.id;

    // LinkAI aggregates many vendors, so only for it do we surface a link to its
    // console for creating/managing the aggregated key. Other providers manage
    // their keys on their own sites.
    const manageKey = document.getElementById('vendor-modal-manage-key');
    if (manageKey) manageKey.classList.toggle('hidden', meta.id !== 'linkai');

    // ----- API Base -----
    // Always reflect the *current effective* base as the input value so the
    // user can see (and edit) what's in use today. Placeholder is reserved
    // strictly for the "not yet typed anything" state and shows the official
    // default — never mixed with the actual value.
    const baseWrap = document.getElementById('vendor-modal-base-wrap');
    const baseInput = document.getElementById('vendor-modal-base');
    const baseHint = document.getElementById('vendor-modal-base-hint');
    if (meta.api_base_field) {
        baseWrap.classList.remove('hidden');
        baseInput.placeholder = meta.api_base_default || meta.api_base_placeholder || '';
        baseInput.value = meta.api_base || '';
        baseHint.classList.add('hidden');
    } else {
        baseWrap.classList.add('hidden');
        baseInput.value = '';
    }

    // ----- API Key -----
    // For configured vendors, surface the masked key as the input *value* so
    // it shows up in the same dark text as a real entry — making "configured"
    // visually unambiguous. The masked form (e.g. "sk-r***zRU") is also a
    // sentinel: the save handler treats untouched masked input as "no change".
    const keyInput = document.getElementById('vendor-modal-key');
    if (meta.configured && meta.api_key_masked) {
        keyInput.value = meta.api_key_masked;
        keyInput.dataset.masked = '1';
        keyInput.dataset.maskedVal = meta.api_key_masked;
        keyInput.placeholder = '';
    } else {
        keyInput.value = '';
        keyInput.dataset.masked = '';
        keyInput.dataset.maskedVal = '';
        keyInput.placeholder = 'sk-...';
    }

    const clearBtn = document.getElementById('vendor-modal-clear');
    clearBtn.classList.toggle('hidden', !meta.configured);

    vendorModalState.providerId = providerId;
}

function closeVendorModal() {
    document.getElementById('vendor-modal-overlay').classList.add('hidden');
}

function saveVendorModal() {
    const providerId = vendorModalState.providerId;
    if (!providerId) return;
    const keyInput = document.getElementById('vendor-modal-key');
    const apiBase = document.getElementById('vendor-modal-base').value.trim();

    // Treat "input still equals the masked value we surfaced on open" as "no
    // change" — the backend uses missing/empty api_key to skip the field.
    let apiKey = keyInput.value.trim();
    const masked = keyInput.dataset.masked === '1';
    const maskedVal = keyInput.dataset.maskedVal || '';
    if (masked && apiKey === maskedVal) {
        apiKey = '';
    }

    if (!apiKey && !masked) {
        // First-time setup with no key entered → nudge the user.
        keyInput.focus();
        return;
    }

    const btn = document.getElementById('vendor-modal-save');
    btn.disabled = true;
    const payload = { action: 'set_provider', provider_id: providerId, api_base: apiBase };
    if (apiKey) payload.api_key = apiKey;
    fetch('/api/models', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    }).then(r => r.json()).then(data => {
        btn.disabled = false;
        if (data.status === 'success') {
            closeVendorModal();
            const onSaved = vendorModalState.onSaved;
            if (onSaved) {
                try { onSaved(providerId); } catch (e) { /* noop */ }
            } else {
                loadModelsView();
            }
        } else {
            showStatus('vendor-modal-status', 'models_save_failed', true);
        }
    }).catch(() => {
        btn.disabled = false;
        showStatus('vendor-modal-status', 'models_save_failed', true);
    });
}

function clearVendorModal() {
    const providerId = vendorModalState.providerId;
    if (!providerId) return;
    showConfirmDialog({
        title: t('models_clear_confirm_title'),
        message: t('models_clear_confirm_msg'),
        okText: t('models_clear_credential'),
        cancelText: t('cancel'),
        onConfirm: () => {
            fetch('/api/models', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: 'delete_provider', provider_id: providerId }),
            }).then(r => r.json()).then(data => {
                if (data.status === 'success') {
                    closeVendorModal();
                    loadModelsView();
                } else {
                    showStatus('vendor-modal-status', 'models_clear_failed', true);
                }
            }).catch(() => showStatus('vendor-modal-status', 'models_clear_failed', true));
        }
    });
}

// =====================================================================
// Custom (OpenAI-compatible) provider modal — add / edit
// =====================================================================
// State for the dedicated custom-provider modal. `editId` is empty when
// adding and set to the provider id when editing.
let customProviderModalState = { editId: '' };

function openCustomProviderModal(providerId) {
    const editing = !!providerId;
    customProviderModalState = { editId: editing ? providerId : '' };

    const card = editing ? getCustomProviderCards().find(p => p.custom_id === providerId) : null;

    const overlay = document.getElementById('custom-provider-modal-overlay');
    if (!overlay) return;

    document.getElementById('custom-provider-modal-title').textContent =
        editing ? t('models_custom_edit_title') : t('models_custom_add_title');

    const nameInput = document.getElementById('custom-provider-name');
    const baseInput = document.getElementById('custom-provider-base');
    const keyInput = document.getElementById('custom-provider-key');

    nameInput.value = card ? (card.custom_name || '') : '';
    baseInput.value = card ? (card.api_base || '') : '';

    // Surface the masked key as the value for configured providers so the
    // "already set" state is unambiguous; an untouched masked value means
    // "keep the existing key" on save (mirrors the vendor modal contract).
    if (card && card.configured && card.api_key_masked) {
        keyInput.value = card.api_key_masked;
        keyInput.dataset.masked = '1';
        keyInput.dataset.maskedVal = card.api_key_masked;
    } else {
        keyInput.value = '';
        keyInput.dataset.masked = '';
        keyInput.dataset.maskedVal = '';
    }
    keyInput.oninput = function () {
        if (keyInput.dataset.masked === '1' && keyInput.value !== keyInput.dataset.maskedVal) {
            keyInput.dataset.masked = '';
        }
    };

    const statusEl = document.getElementById('custom-provider-modal-status');
    if (statusEl) { statusEl.textContent = ''; statusEl.classList.add('opacity-0'); }

    overlay.classList.remove('hidden');
    document.getElementById('custom-provider-modal-cancel').onclick = closeCustomProviderModal;
    document.getElementById('custom-provider-modal-save').onclick = saveCustomProviderModal;

    // Delete is only available when editing an existing provider.
    const deleteBtn = document.getElementById('custom-provider-modal-delete');
    if (deleteBtn) {
        deleteBtn.classList.toggle('hidden', !editing);
        deleteBtn.onclick = editing ? () => deleteCustomProvider(providerId) : null;
    }

    function onOverlayClick(e) {
        if (e.target === overlay) {
            closeCustomProviderModal();
            overlay.removeEventListener('click', onOverlayClick);
        }
    }
    overlay.addEventListener('click', onOverlayClick);
    nameInput.focus();
}

function closeCustomProviderModal() {
    const overlay = document.getElementById('custom-provider-modal-overlay');
    if (overlay) overlay.classList.add('hidden');
}

function saveCustomProviderModal() {
    const name = document.getElementById('custom-provider-name').value.trim();
    const apiBase = document.getElementById('custom-provider-base').value.trim();
    const keyInput = document.getElementById('custom-provider-key');

    if (!name) {
        showStatus('custom-provider-modal-status', 'models_custom_name_required', true);
        document.getElementById('custom-provider-name').focus();
        return;
    }
    const editing = !!customProviderModalState.editId;
    if (!editing && !apiBase) {
        showStatus('custom-provider-modal-status', 'models_custom_base_required', true);
        document.getElementById('custom-provider-base').focus();
        return;
    }

    // Key handling (the custom provider's key is optional):
    //  - masked + untouched  => keep existing, omit from payload
    //  - non-empty typed value => set it
    //  - explicitly cleared on edit => send "" so the backend clears it
    const untouchedMasked =
        keyInput.dataset.masked === '1' && keyInput.value.trim() === (keyInput.dataset.maskedVal || '');
    const apiKey = untouchedMasked ? '' : keyInput.value.trim();

    const payload = {
        action: 'set_custom_provider',
        name: name,
        api_base: apiBase,
    };
    if (untouchedMasked) {
        // omit api_key entirely => backend keeps the stored key
    } else {
        // Send the value (possibly "") so an explicit clear is honored.
        payload.api_key = apiKey;
    }
    if (editing) payload.id = customProviderModalState.editId;

    const btn = document.getElementById('custom-provider-modal-save');
    btn.disabled = true;
    fetch('/api/models', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    }).then(r => r.json()).then(data => {
        btn.disabled = false;
        if (data.status === 'success') {
            closeCustomProviderModal();
            loadModelsView();
        } else {
            showStatus('custom-provider-modal-status', 'models_save_failed', true);
        }
    }).catch(() => {
        btn.disabled = false;
        showStatus('custom-provider-modal-status', 'models_save_failed', true);
    });
}

function deleteCustomProvider(providerId) {
    showConfirmDialog({
        title: t('models_custom_delete_confirm_title'),
        message: t('models_custom_delete_confirm_msg'),
        okText: t('models_custom_delete'),
        cancelText: t('cancel'),
        onConfirm: () => {
            fetch('/api/models', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: 'delete_custom_provider', id: providerId }),
            }).then(r => r.json()).then(data => {
                if (data.status === 'success') {
                    closeCustomProviderModal();
                    loadModelsView();
                }
            }).catch(() => { /* noop */ });
        }
    });
}

// =====================================================================
// Channels View
// =====================================================================
let channelsData = [];
// Multi-Agent mode: the multi-instance-ready types (feishu) render one card per
// channel_instances record. These mirror the extra fields the API returns.
let channelInstancesView = [];
let multiInstanceTypes = [];
let channelsMultiAgent = false;

function isMultiInstanceType(name) {
    return channelsMultiAgent && multiInstanceTypes.indexOf(name) !== -1;
}

function loadChannelsView() {
    const container = document.getElementById('channels-content');
    if (!container) return Promise.resolve();
    container.innerHTML = `<div class="flex items-center gap-2 py-8 justify-center text-slate-400 dark:text-slate-500 text-sm">
        <i class="fas fa-spinner fa-spin text-xs"></i><span>Loading...</span></div>`;

    const roster = agentCatalog.length ? Promise.resolve() : loadAgentCatalog();
    return roster.then(() => fetch('/api/channels').then(r => r.json()).then(data => {
        if (data.status !== 'success') return;
        channelsData = data.channels || [];
        channelsMultiAgent = !!data.multi_agent;
        multiInstanceTypes = data.multi_instance_types || [];
        channelInstancesView = data.instances || [];
        renderActiveChannels();
    }).catch(() => {
        container.innerHTML = '<p class="text-sm text-red-400 py-8 text-center">Failed to load channels</p>';
    }));
}

// Build the list of cards to render. In multi-Agent mode the multi-instance
// types (feishu) contribute one card per channel_instances record (from
// data.instances); everything else contributes its single per-type card. Each
// item carries an `iid` (instance id) that keys its DOM and actions: for legacy
// per-type cards it is just the channel name.
function channelRenderList() {
    const list = [];
    channelsData.forEach(ch => {
        if (isMultiInstanceType(ch.name)) return;  // rendered from instances
        if (ch.active) list.push(Object.assign({}, ch, { iid: ch.name }));
    });
    if (channelsMultiAgent) {
        channelInstancesView.forEach(inst => {
            list.push(Object.assign({}, inst, { iid: inst.instance_id }));
        });
    }
    return list;
}

function renderActiveChannels() {
    stopWeixinQrPoll();
    stopWeixinStatusPoll();
    const container = document.getElementById('channels-content');
    container.innerHTML = '';
    closeAddChannelPanel();

    const activeChannels = channelRenderList();

    if (activeChannels.length === 0) {
        container.innerHTML = `
            <div class="flex flex-col items-center justify-center py-20">
                <div class="w-16 h-16 rounded-2xl bg-blue-50 dark:bg-blue-900/20 flex items-center justify-center mb-4">
                    <i class="fas fa-tower-broadcast text-blue-400 text-xl"></i>
                </div>
                <p class="text-slate-500 dark:text-slate-400 font-medium">${t('channels_empty')}</p>
                <p class="text-sm text-slate-400 dark:text-slate-500 mt-1">${t('channels_empty_desc')}</p>
            </div>`;
        return;
    }

    activeChannels.forEach(ch => {
        const iid = ch.iid;
        const label = (typeof ch.label === 'object') ? (ch.label[currentLang] || ch.label.en) : ch.label;
        const card = document.createElement('div');
        card.className = 'bg-white dark:bg-[#1A1A1A] rounded-xl border border-slate-200 dark:border-white/10 p-6';
        card.id = `channel-card-${iid}`;

        const fieldsHtml = buildChannelFieldsHtml(iid, ch.fields || []);
        const hasFields = (ch.fields || []).length > 0;

        const weixinWaiting = ch.name === 'weixin' && ch.login_status && ch.login_status !== 'logged_in';
        const wecomNeedsCreds = ch.name === 'wecom_bot' && !_wecomBotHasCreds(ch);
        // 飞书 active 卡片渲染带 Tab 的 panel：手动填写 + 扫码重建（覆盖现有配置）
        const isFeishu = ch.name === 'feishu';
        // An instance card (multi-Agent feishu) shows the bound agent inline and
        // uses the instance id as its subtitle instead of the bare type name.
        const isInstance = isMultiInstanceType(ch.name) && !!ch.instance_id;
        let statusDot, statusText;
        if (weixinWaiting) {
            statusDot = 'bg-amber-400 animate-pulse';
            statusText = ch.login_status === 'scanned'
                ? `<span class="text-xs text-primary-500">${t('weixin_scan_scanned')}</span>`
                : `<span class="text-xs text-amber-500">${t('weixin_scan_waiting')}</span>`;
        } else if (wecomNeedsCreds) {
            statusDot = 'bg-amber-400 animate-pulse';
            statusText = `<span class="text-xs text-amber-500">${t('channels_connecting')}</span>`;
        } else {
            statusDot = 'bg-primary-400';
            statusText = `<span class="text-xs text-primary-500">${t('channels_connected')}</span>`;
        }

        card.innerHTML = `
            <div class="flex items-center gap-4${hasFields || weixinWaiting || wecomNeedsCreds || isFeishu || multiAgentMode() ? ' mb-5' : ''}">
                <div class="w-10 h-10 rounded-xl bg-${ch.color}-50 dark:bg-${ch.color}-900/20 flex items-center justify-center flex-shrink-0">
                    <i class="fas ${ch.icon} text-${ch.color}-500 text-base"></i>
                </div>
                <div class="flex-1 min-w-0">
                    <div class="flex items-center gap-2">
                        <span class="font-semibold text-slate-800 dark:text-slate-100">${escapeHtml(label)}</span>
                        <span class="w-2 h-2 rounded-full ${statusDot}"></span>
                        ${statusText}
                    </div>
                    <p class="text-xs text-slate-500 dark:text-slate-400 mt-0.5 font-mono">${escapeHtml(iid)}</p>
                </div>
                <button onclick="disconnectChannel('${ch.name}', '${isInstance ? iid : ''}')"
                    class="px-3 py-1.5 rounded-lg text-xs font-medium
                           bg-red-50 dark:bg-red-900/20 text-red-500 dark:text-red-400
                           hover:bg-red-100 dark:hover:bg-red-900/40
                           cursor-pointer transition-colors flex-shrink-0">
                    ${t('channels_disconnect')}
                </button>
            </div>
            ${multiAgentMode() ? `<div class="channel-agent-bind">
                <span class="text-xs text-slate-500 whitespace-nowrap" title="${escapeHtml(t('channel_bound_agent_hint'))}">${escapeHtml(t('channel_bound_agent'))}</span>
                <div id="ch-members-${iid}" class="cfg-dropdown cfg-dropdown-avatar cfg-dropdown-sm cfg-dropdown-multi" tabindex="0" style="width: 200px;">
                    <div class="cfg-dropdown-selected">
                        <span class="cfg-dropdown-faces"></span>
                        <span class="cfg-dropdown-text">--</span>
                        <i class="fas fa-chevron-down cfg-dropdown-arrow"></i>
                    </div>
                    <div class="cfg-dropdown-menu"></div>
                </div>
            </div>` : ''}
            ${weixinWaiting ? `<div id="weixin-active-qr" class="flex flex-col items-center py-2">
                <button onclick="showWeixinActiveQr()"
                    class="px-4 py-2 rounded-lg bg-primary-500 hover:bg-primary-600 text-white text-sm font-medium
                           cursor-pointer transition-colors duration-150">
                    ${t('weixin_scan_title')}
                </button>
            </div>` : ''}
            ${wecomNeedsCreds ? `<div id="wecom-active-auth" class="flex flex-col items-center py-2">
                <p class="text-sm text-slate-500 dark:text-slate-400 mb-3">${t('wecom_scan_desc')}</p>
                <button onclick="startWecomBotAuthInCard()"
                    class="px-5 py-2 rounded-lg bg-emerald-500 hover:bg-emerald-600 text-white text-sm font-medium
                           cursor-pointer transition-colors duration-150">
                    <i class="fas fa-qrcode mr-2"></i>${t('wecom_scan_btn')}
                </button>
                <div id="wecom-card-scan-status" class="mt-3"></div>
            </div>` : ''}
            ${isFeishu ? buildFeishuPanel(ch, true) : (hasFields ? `<div class="space-y-4">
                ${fieldsHtml}
                <div class="flex items-center justify-end gap-3 pt-1">
                    <span id="ch-status-${iid}" class="text-xs text-primary-500 opacity-0 transition-opacity duration-300"></span>
                    <button onclick="saveChannelConfig('${ch.name}', '${isInstance ? iid : ''}')"
                        class="px-4 py-2 rounded-lg bg-primary-500 hover:bg-primary-600 text-white text-sm font-medium
                               cursor-pointer transition-colors duration-150 disabled:opacity-50 disabled:cursor-not-allowed"
                        id="ch-save-${iid}">${t('channels_save')}</button>
                </div>
            </div>` : '')}`;

        container.appendChild(card);
        bindSecretFieldEvents(card);
        initChannelTeam(ch);

        if (weixinWaiting) {
            startWeixinActiveStatusPoll();
        }
    });
}

// One multi-select per channel card, same idea as creating a team in the chat
// history: pick a set of Agents; the first pick is the owner (receives every
// message and can delegate), the rest are teammates. An ordered list, so the
// first checked stays the owner. Empty = follow the default Agent, solo.
let _channelTeam = {};  // iid -> ordered [ownerId, ...memberIds]

function initChannelTeam(ch) {
    const iid = ch.iid || ch.name;
    if (!multiAgentMode()) return;
    const box = document.getElementById(`ch-members-${iid}`);
    if (!box) return;
    // Seed the ordered team: owner first, then its members. A legacy per-type
    // card has no instance fields, so fall back to its channel-type binding.
    const owner = ch.instance_id ? (ch.agent_id || '') : (channelBoundAgentId(ch.name) || '');
    const members = Array.isArray(ch.members) ? ch.members : [];
    _channelTeam[iid] = [owner, ...members].filter((id, i, arr) => id && arr.indexOf(id) === i);
    box.dataset.channelName = ch.name;
    renderChannelTeam(iid);
    if (!box._ddBound) {
        box.querySelector('.cfg-dropdown-selected').addEventListener('click', (e) => {
            e.stopPropagation();
            document.querySelectorAll('.cfg-dropdown.open').forEach(d => { if (d !== box) d.classList.remove('open'); });
            box.classList.toggle('open');
        });
        box._ddBound = true;
    }
}

function renderChannelTeam(iid) {
    const box = document.getElementById(`ch-members-${iid}`);
    if (!box) return;
    const team = _channelTeam[iid] || [];
    const ownerId = team[0] || '';
    const agents = enabledAgents();
    const chosen = team.map(id => findAgent(id)).filter(Boolean);

    const faces = box.querySelector('.cfg-dropdown-faces');
    const textEl = box.querySelector('.cfg-dropdown-text');
    const MAX_FACES = 3;
    if (chosen.length) {
        // Trigger: up to MAX_FACES avatars; any beyond that become a "+N" pill
        // so the count always matches how many are hidden, never the total.
        const shown = chosen.slice(0, MAX_FACES);
        const extra = chosen.length - shown.length;
        faces.innerHTML = shown.map(a => agentAvatarHTML(a, 18)).join('')
            + (extra > 0 ? `<span class="cfg-dropdown-more">+${extra}</span>` : '');
        textEl.textContent = chosen[0].name || chosen[0].id;
        textEl.classList.remove('text-slate-400', 'dark:text-slate-500');
    } else {
        // Nothing picked: this channel follows the default Agent. Show it
        // (dim) rather than an empty "none", so the receiver is always clear.
        const def = findAgent(defaultAgentId);
        faces.innerHTML = def ? agentAvatarHTML(def, 18) : '';
        textEl.textContent = def ? (def.name || def.id) : t('channel_team_none');
        textEl.classList.add('text-slate-400', 'dark:text-slate-500');
    }

    // Menu: a checklist. The first-picked carries a small "default" badge so it
    // is clear which Agent receives and delegates. The selected tick is the
    // dropdown's global .active::after, so no per-row tick element is needed.
    const menu = box.querySelector('.cfg-dropdown-menu');
    if (!agents.length) {
        menu.innerHTML = `<div class="cfg-dropdown-item cfg-dropdown-empty">${escapeHtml(t('channel_team_no_candidates'))}</div>`;
        return;
    }
    menu.innerHTML = agents.map(a => {
        const on = team.includes(a.id);
        const isOwner = a.id === ownerId;
        return `<div class="cfg-dropdown-item cfg-dropdown-check${on ? ' active' : ''}"
            onclick="event.stopPropagation(); toggleChannelTeam('${iid}','${a.id}')">
            <span class="cfg-dropdown-item-face">${agentAvatarHTML(a, 20)}</span>
            <span class="cfg-dropdown-label">${escapeHtml(a.name || a.id)}</span>
            ${isOwner ? `<span class="cfg-dropdown-badge">${escapeHtml(t('channel_bound_default'))}</span>` : ''}
        </div>`;
    }).join('');
}

function toggleChannelTeam(iid, agentId) {
    const box = document.getElementById(`ch-members-${iid}`);
    const chName = box ? (box.dataset.channelName || '') : '';
    const team = _channelTeam[iid] || [];
    const i = team.indexOf(agentId);
    if (i === -1) team.push(agentId);       // append: order = pick order
    else team.splice(i, 1);                 // remove; if it was owner, next becomes owner
    _channelTeam[iid] = team;
    renderChannelTeam(iid);
    // Persist: first pick is the owner (empty -> default Agent), rest members.
    const ownerId = team[0] || '';
    const members = team.slice(1);
    bindChannelAgent(chName, ownerId, iid, members);
}

function buildChannelFieldsHtml(chName, fields) {
    let html = '';
    fields.forEach(f => {
        const inputId = `ch-${chName}-${f.key}`;
        let inputHtml = '';
        if (f.type === 'bool') {
            const checked = f.value ? 'checked' : '';
            inputHtml = `<label class="relative inline-flex items-center cursor-pointer">
                <input id="${inputId}" type="checkbox" ${checked} class="sr-only peer" data-field="${f.key}" data-ch="${chName}">
                <div class="w-9 h-5 bg-slate-200 dark:bg-slate-700 peer-checked:bg-primary-400 rounded-full
                            after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white
                            after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:after:translate-x-full"></div>
            </label>`;
        } else if (f.type === 'secret') {
            inputHtml = `<input id="${inputId}" type="text" value="${escapeHtml(String(f.value || ''))}"
                data-field="${f.key}" data-ch="${chName}" data-masked="${f.value ? '1' : ''}"
                class="w-full px-3 py-2 rounded-lg border border-slate-200 dark:border-slate-600
                       bg-slate-50 dark:bg-white/5 text-sm text-slate-800 dark:text-slate-100
                       focus:outline-none focus:border-primary-500 font-mono transition-colors
                       ${f.value ? 'cfg-key-masked' : ''}"
                placeholder="${escapeHtml(f.label)}">`;
        } else {
            const inputType = f.type === 'number' ? 'number' : 'text';
            inputHtml = `<input id="${inputId}" type="${inputType}" value="${escapeHtml(String(f.value ?? f.default ?? ''))}"
                data-field="${f.key}" data-ch="${chName}"
                class="w-full px-3 py-2 rounded-lg border border-slate-200 dark:border-slate-600
                       bg-slate-50 dark:bg-white/5 text-sm text-slate-800 dark:text-slate-100
                       focus:outline-none focus:border-primary-500 font-mono transition-colors"
                placeholder="${escapeHtml(f.label)}">`;
        }
        html += `<div>
            <label class="block text-sm font-medium text-slate-600 dark:text-slate-400 mb-1.5">${escapeHtml(f.label)}</label>
            ${inputHtml}
        </div>`;
    });
    return html;
}

function bindSecretFieldEvents(container) {
    container.querySelectorAll('input[data-masked="1"]').forEach(inp => {
        inp.addEventListener('focus', function() {
            if (this.dataset.masked === '1') {
                this.value = '';
                this.dataset.masked = '';
                this.classList.remove('cfg-key-masked');
            }
        });
    });
}

function showChannelStatus(chName, msgKey, isError) {
    const el = document.getElementById(`ch-status-${chName}`);
    if (!el) return;
    el.textContent = t(msgKey);
    el.classList.toggle('text-red-500', !!isError);
    el.classList.toggle('text-primary-500', !isError);
    el.classList.remove('opacity-0');
    setTimeout(() => el.classList.add('opacity-0'), 2500);
}

function saveChannelConfig(chName, instanceId) {
    // instanceId keys the DOM (per-instance cards); falls back to the channel
    // name for legacy single-instance cards.
    const iid = instanceId || chName;
    const card = document.getElementById(`channel-card-${iid}`);
    if (!card) return;

    const updates = {};
    card.querySelectorAll('input[data-ch="' + iid + '"]').forEach(inp => {
        const key = inp.dataset.field;
        if (inp.type === 'checkbox') {
            updates[key] = inp.checked;
        } else {
            if (inp.dataset.masked === '1') return;
            updates[key] = inp.value;
        }
    });

    const btn = document.getElementById(`ch-save-${iid}`);
    if (btn) btn.disabled = true;

    fetch('/api/channels', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'save', channel: chName, instance_id: instanceId || '', config: updates })
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            showChannelStatus(iid, data.restarted ? 'channels_restarted' : 'channels_saved', false);
        } else {
            showChannelStatus(iid, 'channels_save_error', true);
        }
    })
    .catch(() => showChannelStatus(iid, 'channels_save_error', true))
    .finally(() => { if (btn) btn.disabled = false; });
}

function disconnectChannel(chName, instanceId) {
    const ch = channelsData.find(c => c.name === chName);
    const label = ch ? ((typeof ch.label === 'object') ? (ch.label[currentLang] || ch.label.en) : ch.label) : chName;

    showConfirmDialog({
        title: t('channels_disconnect'),
        message: t('channels_disconnect_confirm'),
        okText: t('channels_disconnect'),
        cancelText: t('channels_cancel'),
        onConfirm: () => {
            fetch('/api/channels', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: 'disconnect', channel: chName, instance_id: instanceId || '' })
            })
            .then(r => r.json())
            .then(data => {
                if (data.status === 'success') {
                    // An instance removal changes the instances list; reload from
                    // the server so the card set is authoritative. Legacy per-type
                    // disconnect can flip the flag locally.
                    if (instanceId) {
                        loadChannelsView();
                    } else {
                        if (ch) ch.active = false;
                        renderActiveChannels();
                    }
                }
            })
            .catch(() => {});
        }
    });
}

// --- Add channel panel ---
function openAddChannelPanel() {
    const panel = document.getElementById('channels-add-panel');
    // A multi-instance-ready type (feishu) can always be added again — each add
    // creates a new instance. Other types disappear once active.
    const activeNames = new Set(
        channelsData.filter(c => c.active && !isMultiInstanceType(c.name)).map(c => c.name)
    );
    const available = channelsData.filter(c => !activeNames.has(c.name));

    const anyCards = channelRenderList().length > 0;
    const content = document.getElementById('channels-content');
    if (!anyCards && content) content.classList.add('hidden');

    if (available.length === 0) {
        panel.innerHTML = `<div class="bg-white dark:bg-[#1A1A1A] rounded-xl border border-slate-200 dark:border-white/10 p-6 text-center">
            <p class="text-sm text-slate-500 dark:text-slate-400">${currentLang === 'zh' ? '所有通道均已接入' : 'All channels are already connected'}</p>
            <button onclick="closeAddChannelPanel()" class="mt-3 text-xs text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 cursor-pointer">${t('channels_cancel')}</button>
        </div>`;
        panel.classList.remove('hidden');
        return;
    }

    const ddOptions = [
        { value: '', label: t('channels_select_placeholder') },
        ...available.map(ch => {
            const label = (typeof ch.label === 'object') ? (ch.label[currentLang] || ch.label.en) : ch.label;
            return { value: ch.name, label: `${label} (${ch.name})` };
        })
    ];

    panel.innerHTML = `
        <div class="bg-white dark:bg-[#1A1A1A] rounded-xl border border-primary-200 dark:border-primary-800 p-6">
            <div class="flex items-center gap-3 mb-5">
                <div class="w-9 h-9 rounded-lg bg-primary-50 dark:bg-primary-900/30 flex items-center justify-center">
                    <i class="fas fa-plus text-primary-500 text-sm"></i>
                </div>
                <h3 class="font-semibold text-slate-800 dark:text-slate-100">${t('channels_add')}</h3>
            </div>
            <div class="mb-4">
                <div id="add-channel-select" class="cfg-dropdown" tabindex="0">
                    <div class="cfg-dropdown-selected">
                        <span class="cfg-dropdown-text">--</span>
                        <i class="fas fa-chevron-down cfg-dropdown-arrow"></i>
                    </div>
                    <div class="cfg-dropdown-menu"></div>
                </div>
            </div>
            <div id="add-channel-fields" class="space-y-4"></div>
            <div id="add-channel-actions" class="hidden flex items-center justify-end gap-3 pt-4">
                <button onclick="closeAddChannelPanel()"
                    class="px-4 py-2 rounded-lg border border-slate-200 dark:border-white/10
                           text-slate-600 dark:text-slate-300 text-sm font-medium
                           hover:bg-slate-50 dark:hover:bg-white/5
                           cursor-pointer transition-colors duration-150">${t('channels_cancel')}</button>
                <button id="add-channel-submit" onclick="submitAddChannel()"
                    class="px-4 py-2 rounded-lg bg-primary-500 hover:bg-primary-600 text-white text-sm font-medium
                           cursor-pointer transition-colors duration-150 disabled:opacity-50 disabled:cursor-not-allowed">${t('channels_connect_btn')}</button>
            </div>
        </div>`;
    panel.classList.remove('hidden');
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

    const ddEl = document.getElementById('add-channel-select');
    initDropdown(ddEl, ddOptions, '', onAddChannelSelect);
}

function closeAddChannelPanel() {
    stopWeixinQrPoll();
    stopFeishuRegisterPoll();
    const panel = document.getElementById('channels-add-panel');
    if (panel) {
        panel.classList.add('hidden');
        panel.innerHTML = '';
    }
    const content = document.getElementById('channels-content');
    if (content) content.classList.remove('hidden');
}

function onAddChannelSelect(chName) {
    stopWeixinQrPoll();
    stopFeishuRegisterPoll();
    const fieldsContainer = document.getElementById('add-channel-fields');
    const actions = document.getElementById('add-channel-actions');

    if (!chName) {
        fieldsContainer.innerHTML = '';
        actions.classList.add('hidden');
        return;
    }

    if (chName === 'weixin') {
        actions.classList.add('hidden');
        fieldsContainer.innerHTML = `
            <div id="weixin-qr-panel" class="flex flex-col items-center py-4">
                <p class="text-sm text-slate-500 dark:text-slate-400 mb-4">${t('weixin_scan_loading')}</p>
            </div>`;
        startWeixinQrLogin();
        return;
    }

    if (chName === 'wecom_bot') {
        actions.classList.add('hidden');
        const ch = channelsData.find(c => c.name === chName);
        fieldsContainer.innerHTML = buildWecomBotPanel(ch);
        return;
    }

    if (chName === 'feishu') {
        actions.classList.add('hidden');
        const ch = channelsData.find(c => c.name === chName);
        fieldsContainer.innerHTML = buildFeishuPanel(ch);
        return;
    }

    const ch = channelsData.find(c => c.name === chName);
    if (!ch) return;

    fieldsContainer.innerHTML = buildChannelFieldsHtml(chName, ch.fields || []);
    bindSecretFieldEvents(fieldsContainer);
    actions.classList.remove('hidden');
}

function submitAddChannel() {
    const ddEl = document.getElementById('add-channel-select');
    const chName = getDropdownValue(ddEl);
    if (!chName) return;

    const fieldsContainer = document.getElementById('add-channel-fields');
    const updates = {};
    fieldsContainer.querySelectorAll('input[data-ch="' + chName + '"]').forEach(inp => {
        const key = inp.dataset.field;
        if (inp.type === 'checkbox') {
            updates[key] = inp.checked;
        } else {
            if (inp.dataset.masked === '1') return;
            updates[key] = inp.value;
        }
    });

    const btn = document.getElementById('add-channel-submit');
    if (btn) { btn.disabled = true; btn.textContent = t('channels_connecting'); }

    fetch('/api/channels', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'connect', channel: chName, config: updates })
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            // A new multi-instance record only shows up by reloading the
            // instances list from the server; legacy per-type add can patch
            // local state and re-render.
            if (isMultiInstanceType(chName) || data.instance_id) {
                loadChannelsView();
                return;
            }
            const ch = channelsData.find(c => c.name === chName);
            if (ch) {
                ch.active = true;
                (ch.fields || []).forEach(f => {
                    if (updates[f.key] !== undefined) {
                        f.value = f.type === 'secret' ? ChannelsHandler_maskSecret(updates[f.key]) : updates[f.key];
                    }
                });
            }
            renderActiveChannels();
        } else {
            if (btn) { btn.disabled = false; btn.textContent = t('channels_connect_btn'); }
        }
    })
    .catch(() => {
        if (btn) { btn.disabled = false; btn.textContent = t('channels_connect_btn'); }
    });
}

// =====================================================================
// WeChat QR Login
// =====================================================================
let _weixinQrPollTimer = null;
let _weixinStatusPollTimer = null;

function stopWeixinStatusPoll() {
    if (_weixinStatusPollTimer) {
        clearTimeout(_weixinStatusPollTimer);
        _weixinStatusPollTimer = null;
    }
}

function startWeixinActiveStatusPoll() {
    stopWeixinStatusPoll();
    _weixinStatusPollTimer = setTimeout(() => {
        fetch('/api/channels').then(r => r.json()).then(data => {
            if (data.status !== 'success') return;
            const wx = (data.channels || []).find(c => c.name === 'weixin');
            if (!wx || !wx.active) return;
            if (wx.login_status === 'logged_in') {
                channelsData = data.channels;
                renderActiveChannels();
            } else {
                const ch = channelsData.find(c => c.name === 'weixin');
                if (ch) ch.login_status = wx.login_status;
                startWeixinActiveStatusPoll();
            }
        }).catch(() => { startWeixinActiveStatusPoll(); });
    }, 3000);
}

function showWeixinActiveQr() {
    const container = document.getElementById('weixin-active-qr');
    if (!container) return;
    container.innerHTML = `
        <div id="weixin-qr-panel" class="flex flex-col items-center py-2">
            <p class="text-sm text-slate-500 dark:text-slate-400 mb-4">${t('weixin_scan_loading')}</p>
        </div>`;
    stopWeixinStatusPoll();
    startWeixinQrLogin();
}

function stopWeixinQrPoll() {
    if (_weixinQrPollTimer) {
        clearTimeout(_weixinQrPollTimer);
        _weixinQrPollTimer = null;
    }
}

function startWeixinQrLogin() {
    stopWeixinQrPoll();
    fetch('/api/weixin/qrlogin')
        .then(r => r.json())
        .then(data => {
            const panel = document.getElementById('weixin-qr-panel');
            if (!panel) return;
            if (data.status !== 'success') {
                panel.innerHTML = `<p class="text-sm text-red-500">${t('weixin_scan_fail')}: ${data.message || ''}</p>`;
                return;
            }
            renderWeixinQr(data.qr_image || data.qrcode_url, 'waiting');
            if (data.source === 'channel') {
                startWeixinActiveStatusPoll();
            } else {
                pollWeixinQrStatus();
            }
        })
        .catch(() => {
            const panel = document.getElementById('weixin-qr-panel');
            if (panel) panel.innerHTML = `<p class="text-sm text-red-500">${t('weixin_scan_fail')}</p>`;
        });
}

function renderWeixinQr(qrcodeUrl, status) {
    const panel = document.getElementById('weixin-qr-panel');
    if (!panel) return;

    let statusText = t('weixin_scan_waiting');
    let statusColor = 'text-slate-500 dark:text-slate-400';
    if (status === 'scanned') {
        statusText = t('weixin_scan_scanned');
        statusColor = 'text-primary-500';
    } else if (status === 'expired') {
        statusText = t('weixin_scan_expired');
        statusColor = 'text-amber-500';
    } else if (status === 'confirmed') {
        statusText = t('weixin_scan_success');
        statusColor = 'text-primary-500';
    }

    panel.innerHTML = `
        <div class="flex flex-col items-center">
            <p class="text-sm font-medium text-slate-700 dark:text-slate-200 mb-1">${t('weixin_scan_title')}</p>
            <p class="text-xs text-slate-400 dark:text-slate-500 mb-4">${t('weixin_scan_desc')}</p>
            <div class="bg-white p-3 rounded-xl shadow-sm border border-slate-100 dark:border-slate-700 mb-3">
                <img src="${escapeHtml(qrcodeUrl)}" alt="QR Code" class="w-52 h-52" style="image-rendering: pixelated;"/>
            </div>
            <p class="text-xs ${statusColor} mb-1">${statusText}</p>
            <p class="text-xs text-slate-400 dark:text-slate-500">${t('weixin_qr_tip')}</p>
        </div>`;
}

function pollWeixinQrStatus() {
    _weixinQrPollTimer = setTimeout(() => {
        fetch('/api/weixin/qrlogin', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'poll' })
        })
        .then(r => r.json())
        .then(data => {
            const panel = document.getElementById('weixin-qr-panel');
            if (!panel) { stopWeixinQrPoll(); return; }

            if (data.status !== 'success') {
                pollWeixinQrStatus();
                return;
            }

            const qrStatus = data.qr_status;
            if (qrStatus === 'confirmed') {
                renderWeixinQr('', 'confirmed');
                panel.innerHTML = `
                    <div class="flex flex-col items-center py-4">
                        <div class="w-12 h-12 rounded-full bg-primary-50 dark:bg-primary-900/30 flex items-center justify-center mb-3">
                            <i class="fas fa-check text-primary-500 text-lg"></i>
                        </div>
                        <p class="text-sm font-medium text-primary-600 dark:text-primary-400">${t('weixin_scan_success')}</p>
                    </div>`;
                connectWeixinAfterQr();
            } else if (qrStatus === 'expired' && (data.qr_image || data.qrcode_url)) {
                renderWeixinQr(data.qr_image || data.qrcode_url, 'waiting');
                pollWeixinQrStatus();
            } else if (qrStatus === 'scaned') {
                const img = panel.querySelector('img');
                const currentSrc = img ? img.src : '';
                renderWeixinQr(currentSrc, 'scanned');
                pollWeixinQrStatus();
            } else {
                pollWeixinQrStatus();
            }
        })
        .catch(() => {
            pollWeixinQrStatus();
        });
    }, 2000);
}

function connectWeixinAfterQr() {
    fetch('/api/channels', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'connect', channel: 'weixin', config: {} })
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            // Multi-Agent: the new Weixin instance only lives in the server's
            // channel_instances yet, and its card is rendered from that list —
            // so reload the channels view to make it appear. Re-rendering from
            // the stale local state would drop the freshly scanned card until a
            // manual refresh. Legacy single-instance patches local state.
            if (isMultiInstanceType('weixin') || data.instance_id) {
                setTimeout(() => loadChannelsView(), 1500);
                return;
            }
            const ch = channelsData.find(c => c.name === 'weixin');
            if (ch) ch.active = true;
            setTimeout(() => renderActiveChannels(), 1500);
        }
    })
    .catch(() => {});
}

// =====================================================================
// WeCom Bot QR Auth
// =====================================================================
// NOTE: This is the only remaining external script in the Web Console.
// Tencent's WeCom Bot SDK must be loaded from their official CDN — it
// performs runtime origin/signature checks and will not work if
// self-hosted. The SDK is fetched lazily, only when the user opens the
// "WeCom Bot" channel QR-login flow, so the rest of the console works
// fully offline.
const WECOM_BOT_SDK_URL = 'https://wwcdn.weixin.qq.com/node/wework/js/wecom-aibot-sdk@0.1.0.min.js';
const WECOM_BOT_SOURCE = 'cowagent';
let _wecomSdkLoaded = false;

function ensureWecomSdkLoaded() {
    return new Promise((resolve, reject) => {
        if (_wecomSdkLoaded && window.WecomAIBotSDK) { resolve(); return; }
        if (document.querySelector(`script[src="${WECOM_BOT_SDK_URL}"]`)) {
            _wecomSdkLoaded = true; resolve(); return;
        }
        const s = document.createElement('script');
        s.src = WECOM_BOT_SDK_URL;
        s.onload = () => { _wecomSdkLoaded = true; resolve(); };
        s.onerror = () => reject(new Error('Failed to load WecomAIBotSDK'));
        document.head.appendChild(s);
    });
}

function _wecomBotHasCreds(ch) {
    if (!ch || !ch.fields) return false;
    const idField = ch.fields.find(f => f.key === 'wecom_bot_id');
    const secretField = ch.fields.find(f => f.key === 'wecom_bot_secret');
    return !!(idField && idField.value && secretField && secretField.value);
}

function buildWecomBotPanel(ch) {
    const scanLabel = t('wecom_mode_scan');
    const manualLabel = t('wecom_mode_manual');
    const hasCreds = _wecomBotHasCreds(ch);
    const defaultMode = hasCreds ? 'manual' : 'scan';
    return `
        <div id="wecom-bot-panel" data-default-mode="${defaultMode}">
            <div class="flex items-center justify-center gap-1 mb-5 bg-slate-100 dark:bg-white/5 rounded-lg p-1">
                <button id="wecom-tab-scan" onclick="switchWecomBotMode('scan')"
                    class="flex-1 px-3 py-1.5 rounded-md text-xs font-medium transition-colors
                           bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100 shadow-sm">
                    ${scanLabel}
                </button>
                <button id="wecom-tab-manual" onclick="switchWecomBotMode('manual')"
                    class="flex-1 px-3 py-1.5 rounded-md text-xs font-medium transition-colors
                           text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200">
                    ${manualLabel}
                </button>
            </div>
            <div id="wecom-mode-content"></div>
        </div>`;
}

function switchWecomBotMode(mode) {
    const scanTab = document.getElementById('wecom-tab-scan');
    const manualTab = document.getElementById('wecom-tab-manual');
    const content = document.getElementById('wecom-mode-content');
    const actions = document.getElementById('add-channel-actions');
    if (!scanTab || !manualTab || !content) return;

    const activeClasses = 'bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100 shadow-sm';
    const inactiveClasses = 'text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200';

    if (mode === 'scan') {
        scanTab.className = scanTab.className.replace(/text-slate-500[^\s]*/g, '').replace(/hover:\S+/g, '');
        scanTab.className = `flex-1 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${activeClasses}`;
        manualTab.className = `flex-1 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${inactiveClasses}`;
        actions.classList.add('hidden');
        content.innerHTML = `
            <div class="flex flex-col items-center py-4">
                <p class="text-sm text-slate-600 dark:text-slate-300 mb-2">${t('wecom_scan_desc')}</p>
                <button onclick="startWecomBotAuth()"
                    class="mt-3 px-6 py-2.5 rounded-lg bg-emerald-500 hover:bg-emerald-600 text-white text-sm font-medium
                           cursor-pointer transition-colors duration-150">
                    <i class="fas fa-qrcode mr-2"></i>${t('wecom_scan_btn')}
                </button>
                <div id="wecom-scan-status" class="mt-3"></div>
            </div>`;
    } else {
        manualTab.className = `flex-1 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${activeClasses}`;
        scanTab.className = `flex-1 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${inactiveClasses}`;
        const ch = channelsData.find(c => c.name === 'wecom_bot');
        content.innerHTML = `<div class="space-y-4">${buildChannelFieldsHtml('wecom_bot', ch ? ch.fields || [] : [])}</div>`;
        bindSecretFieldEvents(content);
        actions.classList.remove('hidden');
    }
}

function startWecomBotAuth() {
    const statusEl = document.getElementById('wecom-scan-status');
    ensureWecomSdkLoaded().then(() => {
        WecomAIBotSDK.openBotInfoAuthWindow({
            source: WECOM_BOT_SOURCE,
            onCreated: function(bot) {
                if (statusEl) {
                    statusEl.innerHTML = `
                        <div class="flex flex-col items-center py-2">
                            <div class="w-10 h-10 rounded-full bg-emerald-50 dark:bg-emerald-900/30 flex items-center justify-center mb-2">
                                <i class="fas fa-check text-emerald-500 text-lg"></i>
                            </div>
                            <p class="text-sm font-medium text-emerald-600 dark:text-emerald-400">${t('wecom_scan_success')}</p>
                        </div>`;
                }
                connectWecomBotAfterAuth(bot.botid, bot.secret);
            },
            onError: function(err) {
                if (statusEl) {
                    statusEl.innerHTML = `<p class="text-sm text-red-500">${t('wecom_scan_fail')}: ${err.message || err.code || ''}</p>`;
                }
            }
        });
    }).catch(err => {
        if (statusEl) {
            statusEl.innerHTML = `<p class="text-sm text-red-500">SDK load failed: ${err.message}</p>`;
        }
    });
}

function connectWecomBotAfterAuth(botId, secret) {
    fetch('/api/channels', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            action: 'connect',
            channel: 'wecom_bot',
            config: { wecom_bot_id: botId, wecom_bot_secret: secret }
        })
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            const ch = channelsData.find(c => c.name === 'wecom_bot');
            if (ch) {
                ch.active = true;
                (ch.fields || []).forEach(f => {
                    if (f.key === 'wecom_bot_id') f.value = botId;
                    if (f.key === 'wecom_bot_secret') f.value = ChannelsHandler_maskSecret(secret);
                });
            }
            setTimeout(() => renderActiveChannels(), 1500);
        }
    })
    .catch(() => {});
}

function startWecomBotAuthInCard() {
    const statusEl = document.getElementById('wecom-card-scan-status');
    ensureWecomSdkLoaded().then(() => {
        WecomAIBotSDK.openBotInfoAuthWindow({
            source: WECOM_BOT_SOURCE,
            onCreated: function(bot) {
                if (statusEl) {
                    statusEl.innerHTML = `
                        <div class="flex flex-col items-center py-2">
                            <div class="w-10 h-10 rounded-full bg-emerald-50 dark:bg-emerald-900/30 flex items-center justify-center mb-2">
                                <i class="fas fa-check text-emerald-500 text-lg"></i>
                            </div>
                            <p class="text-sm font-medium text-emerald-600 dark:text-emerald-400">${t('wecom_scan_success')}</p>
                        </div>`;
                }
                connectWecomBotAfterAuth(bot.botid, bot.secret);
            },
            onError: function(err) {
                if (statusEl) {
                    statusEl.innerHTML = `<p class="text-sm text-red-500">${t('wecom_scan_fail')}: ${err.message || err.code || ''}</p>`;
                }
            }
        });
    }).catch(err => {
        if (statusEl) {
            statusEl.innerHTML = `<p class="text-sm text-red-500">SDK load failed: ${err.message}</p>`;
        }
    });
}

// Initialize wecom bot panel with correct default mode when inserted into DOM
document.addEventListener('DOMContentLoaded', function() {
    const observer = new MutationObserver(function() {
        const wecomPanel = document.getElementById('wecom-bot-panel');
        if (wecomPanel && !wecomPanel.dataset.initialized) {
            wecomPanel.dataset.initialized = '1';
            switchWecomBotMode(wecomPanel.dataset.defaultMode || 'scan');
        }
        // Init every feishu panel on screen, not just the first: multiple
        // instance cards can be present at once, each with its own id suffix.
        document.querySelectorAll('.feishu-panel').forEach(feishuPanel => {
            if (feishuPanel.dataset.initialized) return;
            feishuPanel.dataset.initialized = '1';
            switchFeishuMode(feishuPanel.dataset.iid || 'feishu', feishuPanel.dataset.defaultMode || 'scan');
        });
    });
    observer.observe(document.body, { childList: true, subtree: true });
});

// =====================================================================
// Feishu One-click App Registration (lark-oapi register_app)
// =====================================================================
let _feishuRegisterPollTimer = null;

function _feishuHasCreds(ch) {
    if (!ch || !ch.fields) return false;
    const idField = ch.fields.find(f => f.key === 'feishu_app_id');
    const secretField = ch.fields.find(f => f.key === 'feishu_app_secret');
    return !!(idField && idField.value && secretField && secretField.value);
}

function buildFeishuPanel(ch, isActive) {
    const scanLabel = t('feishu_mode_scan');
    const manualLabel = t('feishu_mode_manual');
    // 已有凭据时默认进入手动 Tab，方便修改；否则推荐扫码
    const defaultMode = _feishuHasCreds(ch) ? 'manual' : 'scan';
    const activeAttr = isActive ? 'data-active="1"' : '';
    // Every DOM id in the panel is suffixed with the instance id so two feishu
    // cards on screen at once never collide: without this, getElementById()
    // always resolves to the first card, so the second card is dead and its tab
    // clicks drive the first one. The Add panel (no instance yet) uses the bare
    // "feishu" suffix; an active instance card uses its real instance id.
    const iid = (isActive && ch && ch.iid) ? ch.iid : 'feishu';
    return `
        <div id="feishu-panel-${iid}" class="feishu-panel" data-default-mode="${defaultMode}" data-iid="${escapeHtml(iid)}" ${activeAttr}>
            <div class="flex items-center justify-center gap-1 mb-5 bg-slate-100 dark:bg-white/5 rounded-lg p-1">
                <button id="feishu-tab-scan-${iid}" onclick="switchFeishuMode('${iid}', 'scan')"
                    class="flex-1 px-3 py-1.5 rounded-md text-xs font-medium transition-colors
                           bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100 shadow-sm">
                    ${scanLabel}
                </button>
                <button id="feishu-tab-manual-${iid}" onclick="switchFeishuMode('${iid}', 'manual')"
                    class="flex-1 px-3 py-1.5 rounded-md text-xs font-medium transition-colors
                           text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200">
                    ${manualLabel}
                </button>
            </div>
            <div id="feishu-mode-content-${iid}"></div>
        </div>`;
}

function switchFeishuMode(iid, mode) {
    // Back-compat: old call sites passed only the mode. Treat a bare mode as the
    // Add panel's "feishu" instance.
    if (mode === undefined && (iid === 'scan' || iid === 'manual')) {
        mode = iid;
        iid = 'feishu';
    }
    iid = iid || 'feishu';
    const panel = document.getElementById(`feishu-panel-${iid}`);
    const scanTab = document.getElementById(`feishu-tab-scan-${iid}`);
    const manualTab = document.getElementById(`feishu-tab-manual-${iid}`);
    const content = document.getElementById(`feishu-mode-content-${iid}`);
    if (!scanTab || !manualTab || !content) return;

    // 已激活通道卡片中嵌入此 panel 时，没有 add-channel-actions（保存按钮就近渲染）
    const isActive = panel && panel.dataset.active === '1';
    const actions = isActive ? null : document.getElementById('add-channel-actions');
    const scanStatusId = `feishu-scan-status-${iid}`;

    const activeClasses = 'bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100 shadow-sm';
    const inactiveClasses = 'text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200';

    stopFeishuRegisterPoll();

    if (mode === 'scan') {
        scanTab.className = `flex-1 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${activeClasses}`;
        manualTab.className = `flex-1 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${inactiveClasses}`;
        if (actions) actions.classList.add('hidden');
        // active 卡片下扫码替换的提示文案，强调"创建新机器人会覆盖现有配置"
        const desc = isActive
            ? t('feishu_scan_replace_desc')
            : t('feishu_scan_desc');
        content.innerHTML = `
            <div class="flex flex-col items-center py-4">
                <p class="text-sm text-slate-600 dark:text-slate-300 mb-3 text-center">${desc}</p>
                <button onclick="startFeishuRegister('${scanStatusId}')"
                    class="mt-2 px-6 py-2.5 rounded-lg bg-emerald-500 hover:bg-emerald-600 text-white text-sm font-medium
                           cursor-pointer transition-colors duration-150">
                    <i class="fas fa-qrcode mr-2"></i>${t('feishu_scan_btn')}
                </button>
                <div id="${scanStatusId}" class="mt-4 w-full"></div>
            </div>`;
    } else {
        manualTab.className = `flex-1 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${activeClasses}`;
        scanTab.className = `flex-1 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${inactiveClasses}`;
        // An active instance card keys its fields by the instance id (so the
        // card's data-ch query and the save target line up); the Add panel keys
        // by the bare type since no instance exists yet.
        const ch = (isActive && iid !== 'feishu')
            ? channelInstancesView.find(c => c.instance_id === iid)
            : channelsData.find(c => c.name === 'feishu');
        const fieldsHtml = buildChannelFieldsHtml(iid, ch ? ch.fields || [] : []);
        if (isActive) {
            // 已接入卡片：内置保存按钮，复用 saveChannelConfig 走 update 流程
            content.innerHTML = `
                <div class="space-y-4">
                    ${fieldsHtml}
                    <div class="flex items-center justify-end gap-3 pt-1">
                        <span id="ch-status-${iid}" class="text-xs text-primary-500 opacity-0 transition-opacity duration-300"></span>
                        <button onclick="saveChannelConfig('feishu', '${iid === 'feishu' ? '' : iid}')"
                            class="px-4 py-2 rounded-lg bg-primary-500 hover:bg-primary-600 text-white text-sm font-medium
                                   cursor-pointer transition-colors duration-150 disabled:opacity-50 disabled:cursor-not-allowed"
                            id="ch-save-${iid}">${t('channels_save')}</button>
                    </div>
                </div>`;
        } else {
            content.innerHTML = `<div class="space-y-4">${fieldsHtml}</div>`;
            if (actions) actions.classList.remove('hidden');
        }
        bindSecretFieldEvents(content);
    }
}

function stopFeishuRegisterPoll() {
    if (_feishuRegisterPollTimer) {
        clearTimeout(_feishuRegisterPollTimer);
        _feishuRegisterPollTimer = null;
    }
}

function startFeishuRegister(targetStatusId) {
    const statusId = targetStatusId || 'feishu-scan-status';
    const statusEl = document.getElementById(statusId);
    if (statusEl) {
        statusEl.innerHTML = `<p class="text-sm text-slate-500 dark:text-slate-400 text-center">${t('feishu_scan_loading')}</p>`;
    }
    stopFeishuRegisterPoll();
    fetch('/api/feishu/register')
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'success') {
                renderFeishuRegisterError(statusId, data.message || t('feishu_scan_fail'));
                return;
            }
            if (data.register_status === 'downloading') {
                // Desktop first run: the SDK bundle lands before the QR exists.
                renderFeishuSdkDownloading(statusId);
            } else {
                renderFeishuQr(statusId, data.qr_image, data.qrcode_url);
            }
            pollFeishuRegisterStatus(statusId);
        })
        .catch(err => {
            renderFeishuRegisterError(statusId, err.message || t('feishu_scan_fail'));
        });
}

function renderFeishuQr(statusId, qrImage, qrUrl) {
    const statusEl = document.getElementById(statusId);
    if (!statusEl) return;
    const imgHtml = qrImage
        ? `<img src="${qrImage}" alt="QR" class="w-44 h-44 rounded-lg border border-slate-200 dark:border-white/10 bg-white p-2"/>`
        : `<div class="w-44 h-44 rounded-lg border border-dashed border-slate-300 flex items-center justify-center text-xs text-slate-400">QR</div>`;
    statusEl.innerHTML = `
        <div class="flex flex-col items-center gap-3">
            ${imgHtml}
            <p class="text-xs text-amber-500">${t('feishu_scan_waiting')}</p>
            <p class="text-xs text-slate-400 dark:text-slate-500">${t('feishu_scan_tip')}</p>
            ${qrUrl ? `<a href="${qrUrl}" target="_blank" rel="noopener"
                class="text-xs text-blue-500 hover:text-blue-600 underline">${t('feishu_scan_open_link')}</a>` : ''}
        </div>`;
}

function renderFeishuSdkDownloading(statusId) {
    const statusEl = document.getElementById(statusId);
    if (!statusEl) return;
    statusEl.innerHTML = `
        <div class="flex flex-col items-center gap-2 py-6">
            <i class="fas fa-spinner fa-spin text-slate-400"></i>
            <p class="text-sm text-slate-500 dark:text-slate-400">${t('feishu_sdk_downloading')}</p>
            <p class="text-xs text-slate-400 dark:text-slate-500">${t('feishu_sdk_downloading_tip')}</p>
        </div>`;
}

function renderFeishuRegisterError(statusId, message) {
    const statusEl = document.getElementById(statusId);
    if (!statusEl) return;
    statusEl.innerHTML = `
        <div class="flex flex-col items-center gap-2 py-2">
            <p class="text-sm text-red-500 text-center">${message}</p>
            <button onclick="startFeishuRegister('${statusId}')"
                class="mt-1 px-4 py-1.5 rounded-md text-xs font-medium
                       bg-slate-100 dark:bg-white/10 text-slate-700 dark:text-slate-200
                       hover:bg-slate-200 dark:hover:bg-white/20 cursor-pointer">
                <i class="fas fa-rotate-right mr-1"></i>${t('feishu_scan_retry')}
            </button>
        </div>`;
}

function pollFeishuRegisterStatus(statusId) {
    stopFeishuRegisterPoll();
    _feishuRegisterPollTimer = setTimeout(() => {
        fetch('/api/feishu/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'poll' })
        })
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'success') {
                renderFeishuRegisterError(statusId, data.message || t('feishu_scan_fail'));
                return;
            }
            const rs = data.register_status;
            if (rs === 'downloading') {
                renderFeishuSdkDownloading(statusId);
                pollFeishuRegisterStatus(statusId);
                return;
            }
            // The QR may only be generated after the bundle downloaded, in
            // which case the initial GET could not carry it. Render it once;
            // repainting on every poll would make it flicker.
            const shown = document.getElementById(statusId);
            if ((data.qr_image || data.qrcode_url) && shown && !shown.querySelector('img')) {
                renderFeishuQr(statusId, data.qr_image, data.qrcode_url);
            }
            if (rs === 'done') {
                const statusEl = document.getElementById(statusId);
                if (statusEl) {
                    statusEl.innerHTML = `
                        <div class="flex flex-col items-center py-2">
                            <div class="w-10 h-10 rounded-full bg-emerald-50 dark:bg-emerald-900/30 flex items-center justify-center mb-2">
                                <i class="fas fa-check text-emerald-500 text-lg"></i>
                            </div>
                            <p class="text-sm font-medium text-emerald-600 dark:text-emerald-400">${t('feishu_scan_success')}</p>
                        </div>`;
                }
                connectFeishuAfterRegister(data.app_id, data.app_secret);
            } else if (rs === 'expired') {
                renderFeishuRegisterError(statusId, t('feishu_scan_expired'));
            } else if (rs === 'denied') {
                renderFeishuRegisterError(statusId, t('feishu_scan_denied'));
            } else if (rs === 'error') {
                renderFeishuRegisterError(statusId, data.message || t('feishu_scan_fail'));
            } else {
                pollFeishuRegisterStatus(statusId);
            }
        })
        .catch(() => {
            pollFeishuRegisterStatus(statusId);
        });
    }, 2000);
}

function connectFeishuAfterRegister(appId, appSecret) {
    fetch('/api/channels', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            action: 'connect',
            channel: 'feishu',
            config: { feishu_app_id: appId, feishu_app_secret: appSecret }
        })
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            // Multi-Agent mode created a new feishu instance server-side; reload
            // so its card appears. Legacy mode patches local state.
            if (isMultiInstanceType('feishu') || data.instance_id) {
                setTimeout(() => loadChannelsView(), 1500);
                return;
            }
            const ch = channelsData.find(c => c.name === 'feishu');
            if (ch) {
                ch.active = true;
                (ch.fields || []).forEach(f => {
                    if (f.key === 'feishu_app_id') f.value = appId;
                    if (f.key === 'feishu_app_secret') f.value = ChannelsHandler_maskSecret(appSecret);
                });
            }
            setTimeout(() => renderActiveChannels(), 1500);
        }
    })
    .catch(() => {});
}

// =====================================================================
// Scheduler View
// =====================================================================
let tasksLoaded = false;
function refreshTasksView() {
    const btn = document.getElementById('task-refresh-btn');
    const icon = btn.querySelector('i');
    
    // Add spin animation
    icon.classList.add('fa-spin');
    btn.disabled = true;
    
    tasksLoaded = false;
    const listEl = document.getElementById('tasks-list');
    listEl.innerHTML = '';
    
    loadTasksView();
    
    // Restore button after animation ends
    setTimeout(() => {
        icon.classList.remove('fa-spin');
        btn.disabled = false;
    }, 500);
}

function runTaskNow(task, button) {
    showConfirmDialog({
        title: t('task_run_confirm_title'),
        message: `${task.name || task.id}: ${t('task_run_confirm_msg')}`,
        okText: t('task_run_now'),
        onConfirm: () => {
            const originalHtml = button.innerHTML;
            button.disabled = true;
            button.innerHTML = `<i class="fas fa-spinner fa-spin mr-1"></i>${t('task_run_now')}`;
            fetch('/api/scheduler/run', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({task_id: task.id, agent_id: task.agent_id || ''})
            }).then(r => r.json()).then(res => {
                if (res.status !== 'success') throw new Error(res.message || t('task_run_failed'));
                button.innerHTML = `<i class="fas fa-check mr-1"></i>${t('task_run_started')}`;
                setTimeout(() => {
                    button.innerHTML = originalHtml;
                    button.disabled = false;
                }, 1500);
            }).catch(() => {
                button.innerHTML = `<i class="fas fa-triangle-exclamation mr-1"></i>${t('task_run_failed')}`;
                setTimeout(() => {
                    button.innerHTML = originalHtml;
                    button.disabled = false;
                }, 2000);
            });
        }
    });
}

function loadTasksView() {
    if (tasksLoaded) return;
    // The list tags each task with an owning Agent; make sure the roster is in
    // hand first so findAgent()/multiAgentMode() can resolve the avatar + name.
    const rosterReady = agentCatalog.length ? Promise.resolve() : loadAgentCatalog();
    return rosterReady.then(() => {
    // Explicit empty agent_id so the global fetch wrapper doesn't inject the
    // active chat Agent: the task list is the whole team's schedule and must
    // NOT follow whichever Agent the conversation is currently on. The backend
    // treats an empty agent_id as "aggregate across all Agents".
    return fetch('/api/scheduler?agent_id=').then(r => r.json()).then(data => {
        const emptyEl = document.getElementById('tasks-empty');
        const listEl = document.getElementById('tasks-list');
        if (data.status !== 'success') {
            // Backend closed the consumer (e.g. database identity mode returns
            // 503 "unavailable in database identity mode"). Instead of hanging on
            // the hardcoded "Loading...", surface a readable reason so the user
            // knows the feature is off, not stalled.
            const code = data.code || data.message || '';
            const isClosed = code === 'database_unavailable'
                || /unavailable in database identity mode/i.test(String(data.message || ''));
            emptyEl.querySelector('p').textContent = isClosed
                ? t('tasks_unavailable') : (data.message || t('tasks_unavailable'));
            emptyEl.classList.remove('hidden');
            listEl.classList.add('hidden');
            tasksLoaded = true;
            return;
        }
        const allTasks = data.tasks || [];
        // Backend already sorted by enabled and next_run_at, no need to re-sort on frontend
        if (allTasks.length === 0) {
            emptyEl.querySelector('p').textContent = currentLang === 'zh' ? '暂无定时任务' : 'No scheduled tasks';
            emptyEl.classList.remove('hidden');
            listEl.classList.add('hidden');
            tasksLoaded = true;
            return;
        }
        emptyEl.classList.add('hidden');
        listEl.classList.remove('hidden');
        listEl.innerHTML = '';

        allTasks.forEach(task => {
            const isEnabled = task.enabled !== false;
            const card = document.createElement('div');
            card.className = 'bg-white dark:bg-[#1A1A1A] rounded-xl border border-slate-200 dark:border-white/10 p-4';
            card.dataset.taskId = task.id;
            if (!isEnabled) card.classList.add('opacity-50');
            const schedule = task.schedule || {};
            let typeLabel = '';
            if (schedule.type === 'cron') {
                typeLabel = `<span class="text-xs font-mono text-slate-400">${escapeHtml(schedule.expression || '')}</span>`;
            } else if (schedule.type === 'interval') {
                const seconds = schedule.seconds || 0;
                const hours = Math.floor(seconds / 3600);
                const mins = Math.floor((seconds % 3600) / 60);
                const secs = seconds % 60;
                let intervalText = [];
                if (hours > 0) intervalText.push(`${hours}h`);
                if (mins > 0) intervalText.push(`${mins}m`);
                if (secs > 0 || intervalText.length === 0) intervalText.push(`${secs}s`);
                typeLabel = `<span class="text-xs text-slate-400">${intervalText.join(' ')}</span>`;
            } else {
                typeLabel = `<span class="text-xs text-slate-400">${escapeHtml(schedule.type || 'once')}</span>`;
            }
            let nextRun = '--';
            if (task.next_run_at) {
                const d = new Date(task.next_run_at);
                if (!isNaN(d.getTime())) nextRun = d.toLocaleString();
            }
            const action = task.action || {};
            const taskContent = action.content || action.task_description || '';
            const toggleId = 'toggle-' + task.id;
            // Owner chip: only when several Agents exist (otherwise every task
            // carries the same face and it's just noise). Empty on a solo install.
            const owner = (multiAgentMode() && task.agent_id) ? findAgent(task.agent_id) : null;
            const ownerChip = owner
                ? `<span class="inline-flex items-center gap-1 ml-2 pl-1 pr-1.5 py-0.5 rounded-full bg-slate-100 dark:bg-white/10 text-[10px] leading-none text-slate-400 dark:text-slate-500">
                        ${agentAvatarHTML(owner, 15)}<span class="truncate max-w-[80px]">${escapeHtml(owner.name || owner.id)}</span>
                   </span>`
                : '';
            card.innerHTML = `
                <div class="flex items-center gap-2 mb-2">
                    <span class="w-2 h-2 rounded-full ${isEnabled ? 'bg-primary-400' : 'bg-slate-300 dark:bg-slate-600'}"></span>
                    <span class="font-medium text-sm text-slate-700 dark:text-slate-200">${escapeHtml(task.name || task.id || '--')}</span>
                    ${ownerChip}
                    <div class="flex-1"></div>
                    ${typeLabel}
                </div>
                <p class="text-xs text-slate-500 dark:text-slate-400 mb-2 line-clamp-2">${escapeHtml(taskContent)}</p>
                <div class="flex items-center gap-4 text-xs text-slate-400 dark:text-slate-500">
                    <span><i class="fas fa-clock mr-1"></i>${currentLang === 'zh' ? '下次执行' : 'Next run'}: ${nextRun}</span>
                    <div class="flex-1"></div>
                    <button type="button" class="task-run-now px-2 py-1 rounded-md text-primary-500 hover:bg-primary-50 dark:hover:bg-primary-500/10 transition-colors">
                        <i class="fas fa-play mr-1"></i>${t('task_run_now')}
                    </button>
                    <label class="relative inline-flex items-center cursor-pointer" for="${toggleId}">
                        <input type="checkbox" id="${toggleId}" class="sr-only peer" ${isEnabled ? 'checked' : ''}>
                        <div class="w-9 h-5 bg-slate-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-primary-500 dark:bg-slate-600 dark:peer-checked:bg-primary-500"></div>
                    </label>
                </div>`;
            const runButton = card.querySelector('.task-run-now');
            runButton.addEventListener('click', function(e) {
                e.stopPropagation();
                runTaskNow(task, runButton);
            });
            const checkbox = card.querySelector('#' + toggleId);
            checkbox.addEventListener('change', function() {
                const newEnabled = this.checked;
                fetch('/api/scheduler/toggle', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({task_id: task.id, enabled: newEnabled, agent_id: task.agent_id || ''})
                }).then(r => r.json()).then(res => {
                    if (res.status === 'success') {
                        const dot = card.querySelector('.rounded-full.w-2');
                        if (newEnabled) {
                            card.classList.remove('opacity-50');
                            if (dot) { dot.classList.remove('bg-slate-300','dark:bg-slate-600'); dot.classList.add('bg-primary-400'); }
                        } else {
                            card.classList.add('opacity-50');
                            if (dot) { dot.classList.remove('bg-primary-400'); dot.classList.add('bg-slate-300','dark:bg-slate-600'); }
                        }
                    } else {
                        this.checked = !newEnabled;
                    }
                }).catch(() => { this.checked = !newEnabled; });
            });
            // Card click event (excluding toggle switch clicks)
            card.addEventListener('click', function(e) {
                if (!e.target.closest('label') && !e.target.closest('input[type="checkbox"]')) {
                    openTaskEditModal(task);
                }
            });
            card.style.cursor = 'pointer';
            listEl.appendChild(card);
        });
        tasksLoaded = true;
    }).catch(() => {
        const emptyEl = document.getElementById('tasks-empty');
        const listEl = document.getElementById('tasks-list');
        if (emptyEl && listEl) {
            emptyEl.querySelector('p').textContent = t('tasks_unavailable');
            emptyEl.classList.remove('hidden');
            listEl.classList.add('hidden');
            tasksLoaded = true;
        }
    });
    });
}

// =====================================================================
// Logs View
// =====================================================================
let logEventSource = null;

function logLevelClass(line) {
    if (/\[CRITICAL\]/.test(line)) return 'log-line-critical';
    if (/\[ERROR\]/.test(line))    return 'log-line-error';
    if (/\[WARNING\]/.test(line))  return 'log-line-warning';
    if (/\[INFO\]/.test(line))     return 'log-line-info';
    if (/\[DEBUG\]/.test(line))    return 'log-line-debug';
    return '';
}

function getHiddenLevels() {
    const hidden = new Set();
    document.querySelectorAll('.log-filter-cb').forEach(function(cb) {
        if (!cb.checked) hidden.add('log-line-' + cb.dataset.level);
    });
    return hidden;
}

function applyLogFilter() {
    const hidden = getHiddenLevels();
    document.querySelectorAll('#log-output .log-line').forEach(function(span) {
        const level = span.classList[1] || '';
        span.style.display = hidden.has(level) ? 'none' : '';
    });
}

function appendLogLines(output, text) {
    const hidden = getHiddenLevels();
    let lastLevelClass = '';
    const lines = text.split('\n');
    lines.forEach(function(line, i) {
        if (i === lines.length - 1 && line === '') return;
        const span = document.createElement('span');
        const levelClass = logLevelClass(line) || lastLevelClass;
        if (logLevelClass(line)) lastLevelClass = levelClass;
        span.className = 'log-line ' + levelClass;
        span.textContent = line + '\n';
        if (hidden.has(levelClass)) span.style.display = 'none';
        output.appendChild(span);
    });
}

document.addEventListener('change', function(e) {
    if (e.target.classList.contains('log-filter-cb')) applyLogFilter();
});

function startLogStream() {
    if (logEventSource) return;
    const output = document.getElementById('log-output');
    output.innerHTML = '';

    logEventSource = new EventSource('/api/logs');
    logEventSource.onmessage = function(e) {
        let item;
        try { item = JSON.parse(e.data); } catch (_) { return; }

        if (item.type === 'init') {
            output.innerHTML = '';
            appendLogLines(output, item.content || '');
            output.scrollTop = output.scrollHeight;
        } else if (item.type === 'line') {
            appendLogLines(output, item.content);
            output.scrollTop = output.scrollHeight;
        } else if (item.type === 'error') {
            output.textContent = item.message || 'Error loading logs';
        }
    };
    logEventSource.onerror = function() {
        logEventSource.close();
        logEventSource = null;
    };
}

function stopLogStream() {
    if (logEventSource) {
        logEventSource.close();
        logEventSource = null;
    }
}

// =====================================================================
// View Navigation Hook
// =====================================================================
const _origNavigateTo = navigateTo;
navigateTo = function(viewId) {
    // Previously-visible but not-yet-enabled targets (menu placeholders) are
    // routed by the base handler to a clear "not available" view instead of a
    // silent no-op. Do not early-return here.

    // An open document editor is about to be replaced by another view, which
    // would drop the edit with nothing on screen to say so.
    if (!docGuardUnsaved(() => navigateTo(viewId))) return;

    // Stop log stream when leaving logs view
    if (currentView === 'logs' && viewId !== 'logs') stopLogStream();

    _origNavigateTo(viewId);

    // Lazy-load view data
    if (viewId === 'config') { loadConfigView(); switchConfigTab('basic'); }
    else if (viewId === 'skills') { resetSkillViewer(); loadSkillsView(); }
    else if (viewId === 'memory') {
        memoryEditor.forget();
        document.getElementById('memory-panel-viewer').classList.add('hidden');
        document.getElementById('memory-panel-list').classList.remove('hidden');
        // Keep the last viewed Agent across refreshes, but drop it if that
        // Agent has since been deleted so we don't point at a ghost.
        if (memoryAgentId && agentCatalog.length && !agentCatalog.some(a => a.id === memoryAgentId)) {
            memoryAgentId = '';
            removeScopedPreference('cow_memory_agent');
        }
        if (!memoryAgentId) memoryAgentId = activeAgentId || defaultAgentId;
        renderMemoryAgentSelect();
        switchMemoryTab('files');
    }
    else if (viewId === 'knowledge') loadKnowledgeView();
    else if (viewId === 'channels') loadChannelsView();
    else if (viewId === 'tasks') loadTasksView();
    else if (viewId === 'todo') loadTodosView();
    else if (viewId === 'logs') startLogStream();
};

// =====================================================================
// Knowledge View
// =====================================================================
let _knowledgeTreeData = [];
let _knowledgeRootFiles = [];
let _knowledgeCurrentFile = null;
let _knowledgeGraphLoaded = false;
const KNOWLEDGE_IMPORT_MAX_FILES = 100;
const KNOWLEDGE_IMPORT_MAX_FILE_SIZE = 10 * 1024 * 1024;
const KNOWLEDGE_IMPORT_MAX_TOTAL_SIZE = 200 * 1024 * 1024;

// Which Agent's knowledge base the page is viewing. Persisted like the memory
// page's selector so a refresh keeps the last choice. An Agent on "shared" mode
// resolves to the shared base on the backend, so this simply scopes the view.
let knowledgeAgentId = readScopedPreference('cow_knowledge_agent') || '';

function viewingKnowledgeAgentId() {
    return knowledgeAgentId || activeAgentId || defaultAgentId;
}

// Append the viewed Agent to a knowledge URL. The global fetch wrapper only
// injects activeAgentId when no agent_id is present, so an explicit one wins.
function _kbUrl(path) {
    const joiner = path.includes('?') ? '&' : '?';
    return `${path}${joiner}agent_id=${encodeURIComponent(viewingKnowledgeAgentId())}`;
}

function renderKnowledgeAgentSelect() {
    const el = document.getElementById('knowledge-agent-select');
    if (!el) return;
    const current = viewingKnowledgeAgentId();
    const list = agentCatalog.length ? agentCatalog : enabledAgents();
    const options = list.map(a => ({ value: a.id, label: a.name || a.id, agent: a }));
    initDropdown(el, options, current, (value) => selectKnowledgeAgent(value), { withAvatar: true });
}

function selectKnowledgeAgent(agentId) {
    knowledgeAgentId = agentId;
    writeScopedPreference('cow_knowledge_agent', agentId);
    loadKnowledgeView();
}

function loadKnowledgeView(targetPath) {
    // Reset to docs tab
    switchKnowledgeTab('docs');
    _knowledgeGraphLoaded = false;
    _knowledgeCurrentFile = null;

    // Drop a deleted Agent selection so we never point at a ghost.
    if (knowledgeAgentId && agentCatalog.length && !agentCatalog.some(a => a.id === knowledgeAgentId)) {
        knowledgeAgentId = '';
        removeScopedPreference('cow_knowledge_agent');
    }
    renderKnowledgeAgentSelect();

    fetch(_kbUrl('/api/knowledge/list')).then(r => r.json()).then(data => {
        if (data.status !== 'success') return;
        initKnowledgeImportDropZone();

        const emptyEl = document.getElementById('knowledge-empty');
        const docsPanel = document.getElementById('knowledge-panel-docs');
        const statsEl = document.getElementById('knowledge-stats');

        const tree = data.tree || [];
        const rootFiles = data.root_files || [];
        _knowledgeTreeData = tree;
        _knowledgeRootFiles = rootFiles;
        const stats = data.stats || {};
        const totalPages = stats.pages || 0;
        const sizeStr = stats.size < 1024 ? stats.size + ' B' : (stats.size / 1024).toFixed(1) + ' KB';

        statsEl.textContent = totalPages + ' pages · ' + sizeStr;

        if (totalPages === 0 && tree.length === 0 && rootFiles.length === 0) {
            emptyEl.querySelector('p').textContent = t('knowledge_empty_hint');
            const guideEl = document.getElementById('knowledge-empty-guide');
            if (guideEl) guideEl.classList.remove('hidden');
            emptyEl.classList.remove('hidden');
            docsPanel.classList.add('hidden');
            return;
        }
        emptyEl.classList.add('hidden');
        docsPanel.classList.remove('hidden');

        renderKnowledgeTree(tree, rootFiles);

        // Prefer opening the just created/imported file; ensure its group is
        // expanded so the active item is visible in the tree.
        const targetTitle = targetPath ? _findKnowledgeFileTitle(targetPath) : null;
        if (targetTitle !== null) {
            _expandKnowledgeGroupFor(targetPath);
            openKnowledgeFile(targetPath, targetTitle);
            return;
        }

        // Auto-select the first file (desktop only)
        if (window.innerWidth >= 768) {
            const firstFile = rootFiles.length > 0 ? rootFiles[0] : null;
            const firstGroup = !firstFile ? tree.find(g => g.files && g.files.length > 0) : null;
            if (firstFile) {
                openKnowledgeFile(firstFile.name, firstFile.title);
            } else if (firstGroup) {
                const gf = firstGroup.files[0];
                openKnowledgeFile(firstGroup.dir + '/' + gf.name, gf.title);
            }
        } else {
            document.getElementById('knowledge-content-placeholder').classList.add('hidden');
            document.getElementById('knowledge-content-viewer').classList.add('hidden');
        }
    }).catch(() => {});
}

// Find a file's display title by its relative path within the knowledge tree.
// Returns the title, or null when the path is not present.
function _findKnowledgeFileTitle(path) {
    if (!path) return null;
    const rootHit = (_knowledgeRootFiles || []).find(f => f.name === path);
    if (rootHit) return rootHit.title || rootHit.name;
    const walk = (groups, parentPath) => {
        for (const group of groups || []) {
            const groupPath = parentPath ? `${parentPath}/${group.dir}` : group.dir;
            const hit = (group.files || []).find(f => `${groupPath}/${f.name}` === path);
            if (hit) return hit.title || hit.name;
            const childHit = walk(group.children, groupPath);
            if (childHit !== null) return childHit;
        }
        return null;
    };
    return walk(_knowledgeTreeData, '');
}

// Open every ancestor group of the given file path so it is visible.
function _expandKnowledgeGroupFor(path) {
    if (!path || !path.includes('/')) return;
    const target = document.querySelector(`.knowledge-tree-file[data-path="${CSS.escape(path)}"]`);
    let node = target ? target.closest('.knowledge-tree-group') : null;
    while (node) {
        node.classList.add('open');
        node = node.parentElement ? node.parentElement.closest('.knowledge-tree-group') : null;
    }
}

function renderKnowledgeTree(tree, rootFilesOrFilter, filter) {
    const container = document.getElementById('knowledge-tree');
    container.innerHTML = '';
    let rootFiles, lowerFilter;
    if (typeof rootFilesOrFilter === 'string') {
        rootFiles = _knowledgeRootFiles;
        lowerFilter = (rootFilesOrFilter || '').toLowerCase();
    } else {
        rootFiles = rootFilesOrFilter || _knowledgeRootFiles;
        lowerFilter = (filter || '').toLowerCase();
    }
    (rootFiles || []).forEach(f => {
        if (lowerFilter && !f.title.toLowerCase().includes(lowerFilter) && !f.name.toLowerCase().includes(lowerFilter)) return;
        const fbtn = document.createElement('button');
        fbtn.className = 'knowledge-tree-file' + (_knowledgeCurrentFile === f.name ? ' active' : '');
        fbtn.dataset.path = f.name;
        fbtn.innerHTML = `<i class="fas fa-file-lines text-[10px] text-slate-400"></i><span class="truncate">${escapeHtml(f.title)}</span>${_knowledgeFileActions(f.name)}`;
        fbtn.onclick = () => openKnowledgeFile(f.name, f.title);
        container.appendChild(fbtn);
    });
    _renderKnowledgeGroups(container, tree, '', lowerFilter, 0);
}

function _renderKnowledgeGroups(container, groups, parentPath, lowerFilter, depth) {
    const indent = depth * 12;
    groups.forEach(group => {
        const groupPath = parentPath ? parentPath + '/' + group.dir : group.dir;
        const files = (group.files || []).filter(f =>
            !lowerFilter || f.title.toLowerCase().includes(lowerFilter) || f.name.toLowerCase().includes(lowerFilter)
        );
        const children = group.children || [];
        const hasMatchingChildren = lowerFilter ? _hasFilterMatch(children, lowerFilter) : children.length > 0;
        if (files.length === 0 && !hasMatchingChildren && lowerFilter) return;

        const div = document.createElement('div');
        div.className = 'knowledge-tree-group open';

        const fileCount = _countFiles(group);
        const btn = document.createElement('button');
        btn.className = 'knowledge-tree-group-btn';
        btn.style.paddingLeft = (8 + indent) + 'px';
        btn.innerHTML = `<i class="fas fa-chevron-right chevron"></i><i class="fas fa-folder text-amber-400 text-[11px]"></i><span>${escapeHtml(group.dir)}</span><span class="ml-auto text-[10px] text-slate-400">${fileCount}</span>${_knowledgeCategoryActions(groupPath)}`;
        btn.onclick = () => div.classList.toggle('open');
        div.appendChild(btn);

        const items = document.createElement('div');
        items.className = 'knowledge-tree-group-items';
        files.forEach(f => {
            const fbtn = document.createElement('button');
            const fpath = groupPath + '/' + f.name;
            fbtn.className = 'knowledge-tree-file' + (_knowledgeCurrentFile === fpath ? ' active' : '');
            fbtn.dataset.path = fpath;
            fbtn.style.paddingLeft = (24 + indent) + 'px';
            fbtn.innerHTML = `<i class="fas fa-file-lines text-[10px] text-slate-400"></i><span class="truncate">${escapeHtml(f.title)}</span>${_knowledgeFileActions(fpath)}`;
            fbtn.onclick = () => openKnowledgeFile(fpath, f.title);
            items.appendChild(fbtn);
        });
        if (children.length > 0) {
            _renderKnowledgeGroups(items, children, groupPath, lowerFilter, depth + 1);
        }
        div.appendChild(items);
        container.appendChild(div);
    });
}

function _knowledgeActionButton(icon, title, handler) {
    const danger = icon === 'fa-trash' ? ' danger' : '';
    return `<span role="button" tabindex="0" title="${escapeHtml(title)}" onclick="event.stopPropagation();${handler}" class="knowledge-action${danger}"><i class="fas ${icon}"></i></span>`;
}

function _knowledgeFileActions(path) {
    if (path === 'index.md' || path === 'log.md') return '';
    const value = JSON.stringify(path).replace(/"/g, '&quot;');
    return `<span class="knowledge-actions">${_knowledgeActionButton('fa-arrow-right-arrow-left', '移动', `moveKnowledgeDocument(${value})`)}${_knowledgeActionButton('fa-trash', '删除', `deleteKnowledgeDocument(${value})`)}</span>`;
}

function _knowledgeCategoryActions(path) {
    const value = JSON.stringify(path).replace(/"/g, '&quot;');
    return `<span class="knowledge-actions">${_knowledgeActionButton('fa-pen', '重命名', `renameKnowledgeCategory(${value})`)}${_knowledgeActionButton('fa-trash', '删除', `deleteKnowledgeCategory(${value})`)}</span>`;
}

async function dispatchKnowledgeAction(action, payload, openPathResolver) {
    _setKnowledgeStatus(currentLang === 'zh' ? '处理中...' : 'Working...', false, true);
    try {
        const response = await fetch('/api/knowledge/action', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({action, payload, agent_id: viewingKnowledgeAgentId()}),
        });
        const result = await response.json();
        if (result.status !== 'success') {
            _setKnowledgeStatus(result.message || (currentLang === 'zh' ? '操作失败' : 'Operation failed'), true);
            loadKnowledgeView();
            return null;
        }
        _setKnowledgeStatus(_knowledgeResultMessage(action, result.payload), false);
        // Optionally auto-open the affected file after the tree refreshes.
        const openPath = openPathResolver ? openPathResolver(result.payload) : null;
        loadKnowledgeView(openPath || undefined);
        return result.payload;
    } catch (error) {
        _setKnowledgeStatus(currentLang === 'zh' ? '请求失败，请稍后重试' : 'Request failed, please try again', true);
        return null;
    }
}

function _setKnowledgeStatus(message, isError, persistent) {
    const el = document.getElementById('knowledge-action-status');
    el.textContent = message;
    el.className = `text-xs transition-opacity duration-200 ${isError ? 'text-red-500' : 'text-primary-500'}`;
    el.classList.remove('opacity-0');
    clearTimeout(el._hideTimer);
    if (!persistent) el._hideTimer = setTimeout(() => el.classList.add('opacity-0'), 3500);
}

function _knowledgeResultMessage(action, payload) {
    if (currentLang !== 'zh') {
        return action === 'create_category' ? 'Category created' :
            action === 'create_document' ? 'Document created' :
            action === 'rename_category' ? 'Category renamed' :
            action === 'delete_category' ? 'Category deleted' :
            action === 'import_documents' ? `${payload?.imported || 0} imported · ${payload?.skipped || 0} skipped · ${payload?.failed || 0} failed` :
            action === 'move_documents' ? `${payload?.moved || 0} document moved` :
            `${payload?.deleted || 0} document deleted`;
    }
    return action === 'create_category' ? '分类已创建' :
        action === 'create_document' ? '文档已创建' :
        action === 'rename_category' ? '分类已重命名' :
        action === 'delete_category' ? '分类已删除' :
        action === 'import_documents' ? `导入 ${payload?.imported || 0} 个，跳过 ${payload?.skipped || 0} 个，失败 ${payload?.failed || 0} 个` :
        action === 'move_documents' ? `已移动 ${payload?.moved || 0} 个文档` :
        `已删除 ${payload?.deleted || 0} 个文档`;
}

function _knowledgeCategoryPaths(groups, parent = '') {
    const paths = [];
    for (const group of groups || []) {
        const path = parent ? `${parent}/${group.dir}` : group.dir;
        paths.push(path, ..._knowledgeCategoryPaths(group.children || [], path));
    }
    return paths;
}

function openKnowledgeDialog(options) {
    const overlay = document.getElementById('knowledge-dialog-overlay');
    const card = document.getElementById('knowledge-dialog-card');
    const input = document.getElementById('knowledge-dialog-input');
    const select = document.getElementById('knowledge-dialog-select');
    const textarea = document.getElementById('knowledge-dialog-textarea');
    const documentForm = document.getElementById('knowledge-document-form');
    const documentFilename = document.getElementById('knowledge-document-filename');
    const documentContent = document.getElementById('knowledge-document-content');
    const templateBtn = document.getElementById('knowledge-document-template');
    const documentPathPreview = document.getElementById('knowledge-document-path-preview');
    const submit = document.getElementById('knowledge-dialog-submit');
    const cancel = document.getElementById('knowledge-dialog-cancel');
    document.getElementById('knowledge-dialog-title').textContent = options.title;
    document.getElementById('knowledge-dialog-subtitle').textContent = options.subtitle || '';
    document.getElementById('knowledge-dialog-label').textContent = options.label;
    document.getElementById('knowledge-dialog-hint').textContent = options.hint || '';
    document.getElementById('knowledge-dialog-error').classList.add('hidden');
    document.getElementById('knowledge-dialog-icon').className = `fas ${options.icon || 'fa-folder'} text-emerald-500`;
    card.classList.toggle('knowledge-document-dialog', options.type === 'document');
    input.classList.toggle('hidden', options.type === 'select' || options.type === 'textarea' || options.type === 'document');
    select.classList.toggle('hidden', options.type !== 'select');
    textarea.classList.toggle('hidden', options.type !== 'textarea');
    documentForm.classList.toggle('hidden', options.type !== 'document');
    input.value = options.value || '';
    textarea.value = options.value || '';
    documentFilename.value = options.filename || '';
    documentContent.value = options.content || '';
    document.getElementById('knowledge-document-category-label').textContent = currentLang === 'zh' ? '目标分类' : 'Destination category';
    documentPathPreview.textContent = options.category
        ? `knowledge/${options.category}/`
        : 'knowledge/';
    documentFilename.oninput = null;
    document.getElementById('knowledge-document-filename-label').textContent = currentLang === 'zh' ? '文件名' : 'Filename';
    document.getElementById('knowledge-document-content-label').textContent = currentLang === 'zh' ? 'Markdown 内容' : 'Markdown content';
    templateBtn.textContent = currentLang === 'zh' ? '插入模板' : 'Insert template';
    templateBtn.onclick = () => {
        if (documentContent.value.trim()) return;
        const title = (documentFilename.value || 'untitled').replace(/\.md$/i, '');
        documentContent.value = currentLang === 'zh'
            ? `# ${title}\n\n## 摘要\n\n\n## 关键点\n\n- \n\n## 参考\n\n`
            : `# ${title}\n\n## Summary\n\n\n## Key points\n\n- \n\n## References\n\n`;
        documentContent.focus();
    };
    if (options.type === 'select') {
        // Use the shared custom dropdown component instead of a native
        // <select> so the arrow / menu match the rest of the console.
        const ddOptions = (options.choices || []).map(value => ({ value, label: value }));
        initDropdown(select, ddOptions, (options.choices || [])[0] || '', null);
    }
    submit.textContent = currentLang === 'zh' ? '确定' : 'Confirm';
    cancel.textContent = currentLang === 'zh' ? '取消' : 'Cancel';
    submit.disabled = options.type === 'select' && !(options.choices || []).length;

    const close = () => overlay.classList.add('hidden');
    const submitAction = async () => {
        const rawValue = options.type === 'select' ? getDropdownValue(select) :
            (options.type === 'textarea' ? textarea.value :
            (options.type === 'document' ? {
                filename: documentFilename.value.trim(),
                content: documentContent.value,
            } : input.value));
        const value = options.type === 'textarea' || options.type === 'document' ? rawValue : rawValue.trim();
        const error = options.validate ? options.validate(value) : (!value ? (currentLang === 'zh' ? '此项不能为空' : 'This field is required') : '');
        if (error) {
            const errorEl = document.getElementById('knowledge-dialog-error');
            errorEl.textContent = error;
            errorEl.classList.remove('hidden');
            return;
        }
        submit.disabled = true;
        const ok = await options.onSubmit(value);
        submit.disabled = false;
        if (ok !== null) close();
    };
    submit.onclick = submitAction;
    cancel.onclick = close;
    overlay.onclick = event => { if (event.target === overlay) close(); };
    input.onkeydown = event => { if (event.key === 'Enter') submitAction(); };
    overlay.classList.remove('hidden');
    setTimeout(() => (options.type === 'select' ? select : (options.type === 'textarea' ? textarea : (options.type === 'document' ? documentFilename : input))).focus(), 0);
}

function closeKnowledgeNewMenu() {
    const list = document.getElementById('knowledge-new-menu-list');
    if (list) list.classList.add('hidden');
    document.removeEventListener('click', _knowledgeNewMenuOutside, true);
}

function _knowledgeNewMenuOutside(event) {
    const menu = document.getElementById('knowledge-new-menu');
    if (menu && !menu.contains(event.target)) closeKnowledgeNewMenu();
}

function toggleKnowledgeNewMenu(event) {
    if (event) event.stopPropagation();
    const list = document.getElementById('knowledge-new-menu-list');
    if (!list) return;
    const willOpen = list.classList.contains('hidden');
    list.classList.toggle('hidden');
    if (willOpen) {
        document.addEventListener('click', _knowledgeNewMenuOutside, true);
    } else {
        document.removeEventListener('click', _knowledgeNewMenuOutside, true);
    }
}

function createKnowledgeCategory() {
    openKnowledgeDialog({
        title: currentLang === 'zh' ? '新建分类' : 'New category',
        subtitle: currentLang === 'zh' ? '分类会创建为 knowledge/ 下的目录' : 'Creates a directory under knowledge/',
        label: currentLang === 'zh' ? '分类路径' : 'Category path',
        hint: currentLang === 'zh' ? '支持嵌套路径，例如 research/ai' : 'Nested paths are supported, e.g. research/ai',
        icon: 'fa-folder-plus',
        onSubmit: path => dispatchKnowledgeAction('create_category', {path}),
    });
}

function createKnowledgeDocument() {
    const categories = _knowledgeCategoryPaths(_knowledgeTreeData);
    if (!categories.length) {
        _setKnowledgeStatus(currentLang === 'zh' ? '请先创建分类' : 'Create a category first', true);
        return;
    }
    openKnowledgeDialog({
        title: currentLang === 'zh' ? '新建文档' : 'New document',
        subtitle: currentLang === 'zh' ? '先选择分类，然后输入文件名' : 'Choose a category, then enter a filename',
        label: currentLang === 'zh' ? '目标分类' : 'Destination category',
        type: 'select',
        choices: categories,
        icon: 'fa-file-circle-plus',
        onSubmit: category => {
            openKnowledgeDocumentEditor(category);
            return null;
        },
    });
}

function openKnowledgeDocumentEditor(category) {
    openKnowledgeDialog({
        title: currentLang === 'zh' ? '新建文档' : 'New document',
        subtitle: currentLang === 'zh' ? `保存到 ${category}` : `Save to ${category}`,
        label: '',
        hint: currentLang === 'zh' ? '文件名可省略 .md 后缀；保存后会自动同步索引。' : 'The .md suffix is optional. Index sync runs after saving.',
        type: 'document',
        category,
        filename: '',
        content: '',
        icon: 'fa-file-circle-plus',
        validate: value => {
            if (!value.filename) return currentLang === 'zh' ? '文件名不能为空' : 'Filename is required';
            if (/\.[^.]+$/i.test(value.filename) && !/\.md$/i.test(value.filename)) {
                return currentLang === 'zh' ? '新建文档仅支持 .md 文件名' : 'New documents must be .md files';
            }
            if (!value.content.trim()) return currentLang === 'zh' ? '内容不能为空' : 'Content is required';
            if (new Blob([value.content]).size > KNOWLEDGE_IMPORT_MAX_FILE_SIZE) {
                return currentLang === 'zh' ? '内容不能超过 10MB' : 'Content cannot exceed 10MB';
            }
            return '';
        },
        onSubmit: value => {
            const safeName = value.filename.endsWith('.md') ? value.filename : `${value.filename}.md`;
            return dispatchKnowledgeAction('create_document', {
                path: `${category}/${safeName}`,
                content: value.content,
                overwrite: false,
            }, payload => payload?.path || `${category}/${safeName}`);
        },
    });
}

function selectKnowledgeImportFiles() {
    const input = document.getElementById('knowledge-import-input');
    input.value = '';
    input.onchange = () => {
        if (input.files && input.files.length) openKnowledgeImportDialog(Array.from(input.files));
    };
    input.click();
}

function openKnowledgeImportDialog(files) {
    const validationError = validateKnowledgeImportFiles(files);
    if (validationError) {
        _setKnowledgeStatus(validationError, true);
        return;
    }
    const choices = _knowledgeCategoryPaths(_knowledgeTreeData);
    openKnowledgeDialog({
        title: currentLang === 'zh' ? '导入文档' : 'Import documents',
        subtitle: currentLang === 'zh' ? `已选择 ${files.length} 个文件` : `${files.length} file(s) selected`,
        label: currentLang === 'zh' ? '目标分类' : 'Destination category',
        hint: choices.length ? (currentLang === 'zh' ? '支持 Markdown 和 TXT，TXT 会转成 Markdown 文档' : 'Markdown and TXT are supported. TXT is converted to Markdown.') :
            (currentLang === 'zh' ? '请先创建一个分类' : 'Create a category first'),
        type: 'select',
        choices,
        icon: 'fa-file-arrow-up',
        onSubmit: target => importKnowledgeDocuments(files, target),
    });
}

async function importKnowledgeDocuments(files, targetCategory) {
    const validationError = validateKnowledgeImportFiles(files);
    if (validationError) {
        _setKnowledgeStatus(validationError, true);
        return null;
    }
    const supported = files.filter(file => /\.(md|txt)$/i.test(file.name || ''));
    if (!supported.length) {
        _setKnowledgeStatus(currentLang === 'zh' ? '请选择 .md 或 .txt 文件' : 'Choose .md or .txt files', true);
        return null;
    }
    const formData = new FormData();
    formData.append('target_category', targetCategory);
    formData.append('conflict_strategy', 'rename');
    supported.forEach(file => formData.append('files', file, file.name));
    _setKnowledgeStatus(currentLang === 'zh' ? '正在导入...' : 'Importing...', false, true);
    try {
        const response = await fetch(_kbUrl('/api/knowledge/import'), { method: 'POST', body: formData });
        const result = await response.json();
        if (result.status !== 'success') {
            _setKnowledgeStatus(result.message || (currentLang === 'zh' ? '导入失败' : 'Import failed'), true);
            loadKnowledgeView();
            return null;
        }
        _setKnowledgeStatus(_knowledgeResultMessage('import_documents', result.payload), false);
        // Auto-open the first successfully imported document.
        const firstImported = (result.payload?.results || []).find(item => item.status === 'imported');
        loadKnowledgeView(firstImported ? firstImported.path : undefined);
        return result.payload;
    } catch (error) {
        _setKnowledgeStatus(currentLang === 'zh' ? '导入请求失败' : 'Import request failed', true);
        return null;
    }
}

function validateKnowledgeImportFiles(files) {
    if (!files || !files.length) return currentLang === 'zh' ? '请选择文件' : 'Choose files';
    if (files.length > KNOWLEDGE_IMPORT_MAX_FILES) {
        return currentLang === 'zh' ? `一次最多导入 ${KNOWLEDGE_IMPORT_MAX_FILES} 个文件` : `Import at most ${KNOWLEDGE_IMPORT_MAX_FILES} files at a time`;
    }
    let total = 0;
    for (const file of files) {
        total += file.size || 0;
        if ((file.size || 0) > KNOWLEDGE_IMPORT_MAX_FILE_SIZE) {
            return currentLang === 'zh' ? `${file.name} 超过 10MB` : `${file.name} exceeds 10MB`;
        }
    }
    if (total > KNOWLEDGE_IMPORT_MAX_TOTAL_SIZE) {
        return currentLang === 'zh' ? '单次导入总大小不能超过 200MB' : 'Total import size cannot exceed 200MB';
    }
    return '';
}

let _knowledgeImportDropReady = false;
function initKnowledgeImportDropZone() {
    if (_knowledgeImportDropReady) return;
    const panel = document.getElementById('knowledge-panel-docs');
    if (!panel) return;
    _knowledgeImportDropReady = true;
    ['dragenter', 'dragover'].forEach(name => {
        panel.addEventListener(name, event => {
            if (!event.dataTransfer || !event.dataTransfer.types.includes('Files')) return;
            event.preventDefault();
            panel.classList.add('knowledge-import-drag-over');
        });
    });
    ['dragleave', 'drop'].forEach(name => {
        panel.addEventListener(name, event => {
            if (event.type === 'drop') {
                event.preventDefault();
                const files = Array.from(event.dataTransfer?.files || []);
                if (files.length) openKnowledgeImportDialog(files);
            }
            panel.classList.remove('knowledge-import-drag-over');
        });
    });
}

function renameKnowledgeCategory(path) {
    openKnowledgeDialog({
        title: currentLang === 'zh' ? '重命名分类' : 'Rename category',
        subtitle: path,
        label: currentLang === 'zh' ? '新的分类路径' : 'New category path',
        value: path,
        icon: 'fa-pen',
        validate: value => value === path ? (currentLang === 'zh' ? '请输入不同的分类路径' : 'Enter a different category path') : '',
        onSubmit: newPath => dispatchKnowledgeAction('rename_category', {path, new_path: newPath}),
    });
}

function deleteKnowledgeCategory(path) {
    showConfirmDialog({
        title: '删除分类',
        message: `确认删除“${path}”及其中全部文档？`,
        okText: t('confirm_yes'),
        cancelText: t('confirm_cancel'),
        onConfirm: () => dispatchKnowledgeAction('delete_category', {path, confirm: true}),
    });
}

function deleteKnowledgeDocument(path) {
    showConfirmDialog({
        title: '删除文档',
        message: `确认删除“${path}”？`,
        okText: t('confirm_yes'),
        cancelText: t('confirm_cancel'),
        onConfirm: () => dispatchKnowledgeAction('delete_documents', {paths: [path]}),
    });
}

function moveKnowledgeDocument(path) {
    const currentCategory = path.includes('/') ? path.split('/').slice(0, -1).join('/') : '';
    const choices = _knowledgeCategoryPaths(_knowledgeTreeData).filter(value => value !== currentCategory);
    openKnowledgeDialog({
        title: currentLang === 'zh' ? '移动文档' : 'Move document',
        subtitle: path,
        label: currentLang === 'zh' ? '目标分类' : 'Destination category',
        hint: choices.length ? '' : (currentLang === 'zh' ? '请先创建其他分类' : 'Create another category first'),
        type: 'select',
        choices,
        icon: 'fa-arrow-right-arrow-left',
        onSubmit: target => dispatchKnowledgeAction('move_documents', {paths: [path], target_category: target}),
    });
}

function _hasFilterMatch(groups, lowerFilter) {
    for (const g of groups) {
        for (const f of (g.files || [])) {
            if (f.title.toLowerCase().includes(lowerFilter) || f.name.toLowerCase().includes(lowerFilter)) return true;
        }
        if (_hasFilterMatch(g.children || [], lowerFilter)) return true;
    }
    return false;
}

function _countFiles(group) {
    let count = (group.files || []).length;
    for (const child of (group.children || [])) {
        count += _countFiles(child);
    }
    return count;
}

function filterKnowledgeTree(query) {
    renderKnowledgeTree(_knowledgeTreeData, _knowledgeRootFiles, query);
}

function resolveKnowledgePath(currentFilePath, relativeHref) {
    // currentFilePath: e.g. "concepts/mcp-protocol.md"
    // relativeHref: e.g. "../entities/openai.md"
    const parts = currentFilePath.split('/');
    parts.pop(); // remove filename, keep directory
    const segments = [...parts, ...relativeHref.split('/')];
    const resolved = [];
    for (const seg of segments) {
        if (seg === '..') resolved.pop();
        else if (seg !== '.' && seg !== '') resolved.push(seg);
    }
    return resolved.join('/');
}

function bindKnowledgeLinks(container, currentFilePath) {
    container.querySelectorAll('a').forEach(a => {
        const href = a.getAttribute('href');
        if (!href || !href.endsWith('.md')) return;
        // Skip absolute URLs
        if (/^https?:\/\//.test(href)) return;

        a.addEventListener('click', (e) => {
            e.preventDefault();
            const resolved = resolveKnowledgePath(currentFilePath, href);
            const linkTitle = a.textContent.trim() || resolved.replace(/\.md$/, '').split('/').pop();
            openKnowledgeFile(resolved, linkTitle);
        });
        a.style.cursor = 'pointer';
        a.classList.add('text-primary-500', 'hover:underline');
    });
}

// Rewrite <img> srcs that are relative to the knowledge doc's directory into
// /api/file URLs, mirroring bindKnowledgeLinks for links. Runs on rendered
// DOM, so markdown syntax quoted inside code blocks is never touched. The
// lightbox onclick that renderMarkdown attached reads this.src at click time,
// so rewriting src alone keeps zoom working.
function bindKnowledgeImages(container, baseDir) {
    if (!baseDir) return;
    container.querySelectorAll('img').forEach(img => {
        const src = img.getAttribute('src');
        // Remote / data / site-absolute srcs resolve on their own.
        if (!src || /^(?:[a-z][\w+.-]*:|\/)/i.test(src)) return;
        const combined = `${baseDir}/${src.split('?')[0]}`;
        const segments = [];
        for (const seg of combined.split('/')) {
            if (seg === '..') segments.pop();
            else if (seg !== '.' && seg !== '') segments.push(seg);
        }
        // baseDir is an absolute posix path, so restore the leading slash the
        // split() dropped — /api/file rejects non-absolute paths.
        const resolved = (combined.startsWith('/') ? '/' : '') + segments.join('/');
        img.src = '/api/file?path=' + encodeURIComponent(resolved);
    });
}

function openKnowledgeFile(path, title) {
    _knowledgeCurrentFile = path;
    // Update active state in tree via data-path
    document.querySelectorAll('.knowledge-tree-file').forEach(el => {
        el.classList.toggle('active', el.dataset.path === path);
    });

    // Immediately hide placeholder
    document.getElementById('knowledge-content-placeholder').classList.add('hidden');

    fetch(_kbUrl(`/api/knowledge/read?path=${encodeURIComponent(path)}`)).then(r => r.json()).then(data => {
        if (data.status !== 'success') return;
        const viewer = document.getElementById('knowledge-content-viewer');
        document.getElementById('knowledge-viewer-title').textContent = title;
        document.getElementById('knowledge-viewer-path').textContent = path;
        const bodyEl = document.getElementById('knowledge-viewer-body');
        bodyEl.innerHTML = renderMarkdown(data.content || '');
        viewer.classList.remove('hidden');
        applyHighlighting(viewer);
        bindKnowledgeLinks(bodyEl, path);
        bindKnowledgeImages(bodyEl, data.dir);

        // Mobile: hide sidebar, show content
        if (window.innerWidth < 768) {
            document.getElementById('knowledge-sidebar').classList.add('hidden');
        }
    }).catch(() => {});
}

function knowledgeMobileBack() {
    document.getElementById('knowledge-sidebar').classList.remove('hidden');
    document.getElementById('knowledge-content-viewer').classList.add('hidden');
}

function switchKnowledgeTab(tab) {
    document.querySelectorAll('.knowledge-tab').forEach(el => el.classList.remove('active'));
    document.getElementById('knowledge-tab-' + tab).classList.add('active');

    const docsPanel = document.getElementById('knowledge-panel-docs');
    const graphPanel = document.getElementById('knowledge-panel-graph');

    if (tab === 'docs') {
        docsPanel.classList.remove('hidden');
        graphPanel.classList.add('hidden');
    } else {
        docsPanel.classList.add('hidden');
        graphPanel.classList.remove('hidden');
        if (!_knowledgeGraphLoaded) {
            loadKnowledgeGraph();
        }
    }
}

let _d3LoadPromise = null;

function ensureD3Loaded() {
    if (window.d3) return Promise.resolve(window.d3);
    if (_d3LoadPromise) return _d3LoadPromise;
    _d3LoadPromise = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = 'assets/vendor/d3/d3.min.js';
        script.async = true;
        script.onload = () => resolve(window.d3);
        script.onerror = () => reject(new Error('Failed to load d3'));
        document.head.appendChild(script);
    });
    return _d3LoadPromise;
}

function loadKnowledgeGraph() {
    _knowledgeGraphLoaded = true;
    const container = document.getElementById('knowledge-graph-container');
    container.innerHTML = '<div class="flex items-center justify-center h-full text-slate-400 text-sm"><i class="fas fa-spinner fa-spin mr-2"></i>Loading graph...</div>';

    Promise.all([
        ensureD3Loaded(),
        fetch(_kbUrl('/api/knowledge/graph')).then(r => r.json()),
    ]).then(([, data]) => {
        const nodes = data.nodes || [];
        const links = data.links || [];
        if (nodes.length === 0) {
            container.innerHTML = `<div class="flex flex-col items-center justify-center h-full text-slate-400"><i class="fas fa-diagram-project text-3xl mb-3 opacity-40"></i><p class="text-sm">${t('knowledge_empty_hint')}</p></div>`;
            return;
        }
        container.innerHTML = '';
        renderKnowledgeGraph(container, nodes, links);
    }).catch(() => {
        container.innerHTML = '<div class="flex items-center justify-center h-full text-slate-400 text-sm">Failed to load graph</div>';
    });
}

function renderKnowledgeGraph(container, nodes, links) {
    const width = container.clientWidth;
    const height = container.clientHeight || 600;

    // Order categories by node count so the dominant cluster gets the most
    // salient palette entry. Ties break by name to keep colors stable.
    const catCount = {};
    nodes.forEach(n => { catCount[n.category] = (catCount[n.category] || 0) + 1; });
    const categories = Object.keys(catCount).sort(
        (a, b) => catCount[b] - catCount[a] || a.localeCompare(b)
    );
    const colorScale = d3.scaleOrdinal(d3.schemeTableau10).domain(categories);

    // Connection count for sizing
    const connCount = {};
    nodes.forEach(n => connCount[n.id] = 0);
    links.forEach(l => {
        connCount[l.source] = (connCount[l.source] || 0) + 1;
        connCount[l.target] = (connCount[l.target] || 0) + 1;
    });

    const svg = d3.select(container)
        .append('svg')
        .attr('width', width)
        .attr('height', height);

    const g = svg.append('g');

    // Zoom with adaptive label visibility
    let currentZoomScale = 1;
    // Set once the graph is fitted to the viewport. Labels hide below it, so
    // zooming out past the default view still declutters.
    let fittedScale = 1;
    const zoom = d3.zoom()
        .scaleExtent([0.2, 5])
        .on('zoom', (event) => {
            g.attr('transform', event.transform);
            currentZoomScale = event.transform.k;
            updateLabelVisibility();
        });
    svg.call(zoom);

    function updateLabelVisibility() {
        if (!label) return;
        // Fitting a graph of any size into the panel lands well below scale 1,
        // so a fixed threshold would hide every label in the default view.
        // Compare against the fitted scale instead, and keep the text a
        // constant size on screen — inside the zoomed <g>, that means dividing
        // by the scale.
        if (currentZoomScale < fittedScale * 0.9) {
            label.attr('opacity', 0);
            return;
        }
        label.attr('opacity', 1)
            .attr('font-size', 10 / currentZoomScale)
            .attr('dx', d => getNodeRadius(d) + 4 / currentZoomScale)
            .attr('dy', 3 / currentZoomScale);
    }

    const simulation = d3.forceSimulation(nodes)
        .force('link', d3.forceLink(links).id(d => d.id).distance(90))
        .force('charge', d3.forceManyBody().strength(-180))
        .force('center', d3.forceCenter(width / 2, height / 2))
        .force('x', d3.forceX(width / 2).strength(0.06))
        .force('y', d3.forceY(height / 2).strength(0.06))
        .force('collision', d3.forceCollide().radius(d => getNodeRadius(d) + 30));

    function getNodeRadius(d) {
        return Math.max(5, Math.min(16, 5 + (connCount[d.id] || 0) * 2));
    }

    const link = g.append('g')
        .selectAll('line')
        .data(links)
        .join('line')
        .attr('stroke', '#94a3b8')
        .attr('stroke-opacity', 0.3)
        .attr('stroke-width', 1);

    const node = g.append('g')
        .selectAll('circle')
        .data(nodes)
        .join('circle')
        .attr('r', d => getNodeRadius(d))
        .attr('fill', d => colorScale(d.category))
        .attr('stroke', '#fff')
        .attr('stroke-width', 1.5)
        .style('cursor', 'pointer')
        .call(d3.drag()
            .on('start', (event, d) => { if (!event.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
            .on('drag', (event, d) => { d.fx = event.x; d.fy = event.y; })
            .on('end', (event, d) => { if (!event.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; })
        );

    const label = g.append('g')
        .selectAll('text')
        .data(nodes)
        .join('text')
        .text(d => d.label.length > 15 ? d.label.slice(0, 14) + '…' : d.label)
        .attr('font-size', 9)
        .attr('dx', d => getNodeRadius(d) + 4)
        .attr('dy', 3)
        .attr('fill', '#64748b')
        .style('pointer-events', 'none');

    // Tooltip
    const tooltip = document.createElement('div');
    tooltip.className = 'knowledge-graph-tooltip';
    container.style.position = 'relative';
    container.appendChild(tooltip);

    node.on('mouseover', (event, d) => {
        tooltip.textContent = d.label + ' (' + d.category + ')';
        tooltip.style.opacity = '1';
        tooltip.style.left = (event.offsetX + 12) + 'px';
        tooltip.style.top = (event.offsetY - 8) + 'px';
        // Highlight connections
        link.attr('stroke-opacity', l => (l.source.id === d.id || l.target.id === d.id) ? 0.8 : 0.1);
        node.attr('opacity', n => n.id === d.id || links.some(l => (l.source.id === d.id && l.target.id === n.id) || (l.target.id === d.id && l.source.id === n.id)) ? 1 : 0.2);
        label.attr('opacity', n => n.id === d.id || links.some(l => (l.source.id === d.id && l.target.id === n.id) || (l.target.id === d.id && l.source.id === n.id)) ? 1 : 0.1);
    }).on('mousemove', (event) => {
        tooltip.style.left = (event.offsetX + 12) + 'px';
        tooltip.style.top = (event.offsetY - 8) + 'px';
    }).on('mouseout', () => {
        tooltip.style.opacity = '0';
        link.attr('stroke-opacity', 0.3);
        node.attr('opacity', 1);
        label.attr('opacity', 1);
    }).on('click', (event, d) => {
        // Switch to docs tab and open the file
        switchKnowledgeTab('docs');
        openKnowledgeFile(d.id, d.label);
    });

    simulation.on('tick', () => {
        link.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
            .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
        node.attr('cx', d => d.x).attr('cy', d => d.y);
        label.attr('x', d => d.x).attr('y', d => d.y);
    });

    // Auto fit-to-view when simulation settles
    simulation.on('end', () => {
        const pad = 16;
        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        nodes.forEach(n => {
            if (n.x < x0) x0 = n.x;
            if (n.y < y0) y0 = n.y;
            if (n.x > x1) x1 = n.x;
            if (n.y > y1) y1 = n.y;
        });
        const bw = x1 - x0 + pad * 2;
        const bh = y1 - y0 + pad * 2;
        if (bw > 0 && bh > 0) {
            const scale = Math.min(width / bw, height / bh, 4);
            fittedScale = scale;
            const tx = width / 2 - (x0 + x1) / 2 * scale;
            const ty = height / 2 - (y0 + y1) / 2 * scale;
            svg.transition().duration(500).call(
                zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale)
            );
        }
    });

    // Legend
    const legendDiv = document.createElement('div');
    legendDiv.className = 'knowledge-graph-legend';
    categories.forEach(cat => {
        const item = document.createElement('span');
        item.className = 'knowledge-graph-legend-item';
        item.innerHTML = `<span class="knowledge-graph-legend-dot" style="background:${colorScale(cat)}"></span>${escapeHtml(cat)}`;
        legendDiv.appendChild(item);
    });
    container.appendChild(legendDiv);
}

// =====================================================================
// Authentication
// =====================================================================
function _identityMode() {
    return _identityModeState;
}

// Console navigation presentation switch, injected by the backend as a validated
// value in { "classic", "split" }. It is layout-only and never changes
// authorization, the identity mode, or any consumer open/closed state. Invalid
// or missing values fall back to "classic".
const _NAVIGATION_MODES = ['classic', 'split'];
function _navigationMode() {
    const raw = String(window.__COW_NAVIGATION_MODE__ || 'classic').trim().toLowerCase();
    return _NAVIGATION_MODES.indexOf(raw) >= 0 ? raw : 'classic';
}

// === NAV_AREA_BEGIN ===
function _navAreaFromPath(pathname) {
    const p = String(pathname || '');
    return p === '/admin' || p.startsWith('/admin/') ? 'admin' : 'workbench';
}
const NAV_WINDOW_WORKBENCH = 'cow-workbench';
const NAV_WINDOW_ADMIN = 'cow-admin';
function _openNavArea(area, path) {
    const name = area === 'admin' ? NAV_WINDOW_ADMIN : NAV_WINDOW_WORKBENCH;
    const target = path || (area === 'admin' ? '/admin' : '/chat');
    return window.open(target, name);
}
function _qualifyAdminConsoleEntry(opts) {
    // opts: { identityMode, isPlatformAdmin, isTenantAdmin }
    if (!opts || opts.identityMode !== 'database') return true;
    return !!(opts.isPlatformAdmin || opts.isTenantAdmin);
}
function _applyNavAreaAttribute() {
    const appEl = document.getElementById('app');
    const area = _navAreaFromPath(typeof location !== 'undefined' ? location.pathname : '');
    if (appEl) appEl.setAttribute('data-nav-area', area);
    const sidebarCaption = document.getElementById('sidebar-brand-caption');
    if (sidebarCaption && typeof sidebarBrandCaption === 'function') {
        const caption = sidebarBrandCaption(
            typeof effectiveLogoDescription === 'function' ? effectiveLogoDescription() : ''
        );
        const hasDesc = !!(caption && String(caption).trim());
        sidebarCaption.textContent = hasDesc ? caption : '';
        sidebarCaption.classList.toggle('hidden', !hasDesc);
        sidebarCaption.title = hasDesc ? caption : '';
    }
    return area;
}
// === NAV_AREA_END ===

function _setupHeaderTenantSelector() {
    const sel = document.getElementById('tenant-selector');
    if (!sel) return;
    const label = document.getElementById('tenant-selector-label');
    const menu = document.getElementById('tenant-menu');
    if (!menu) return;
    // Only meaningful in database mode with a selected tenant.
    const tid = sessionStorage.getItem('cow_tenant_id');
    if (!tid) { sel.classList.add('hidden'); return; }
    sel.classList.remove('hidden');
    menu.innerHTML = '';
    // The picker lists the SELF's effective tenants (via /auth/me), not the
    // platform-admin tenant list — a platform admin is not given tenant members
    // for tenants they are not on. The list is not an authorization grant.
    fetch('/auth/me').then(r => r.json()).then(data => {
        const tenants = (data && data.status === 'success' && Array.isArray(data.tenants))
            ? data.tenants.map(tn => ({ id: tn.id, code: tn.code, name: tn.name })) : [];
        if (!tenants.length) return;
        const current = tenants.find(t => t.id === tid);
        if (current) label.textContent = current.name || current.code;
        tenants.forEach(tn => {
            const item = document.createElement('button');
            item.className = 'tenant-menu-item w-full text-left px-3 py-1.5 text-sm text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/10 cursor-pointer';
            item.dataset.tenant = tn.id;
            item.textContent = (tn.name || tn.code) + (tn.id === tid ? ' ✓' : '');
            item.addEventListener('click', () => {
                menu.classList.add('hidden');
                if (sessionStorage.getItem('cow_tenant_id') === tn.id) return;
                // Refresh-based switch (task 3.6): navigate with a one-shot
                // switch_tenant param; the new page validates before committing,
                // so the old page and its requests are never left half-switched.
                if (typeof bumpTenantGeneration === 'function') bumpTenantGeneration();
                const url = new URL(window.location.href);
                url.searchParams.set('switch_tenant', tn.id);
                window.location.assign(url.toString());
            });
            menu.appendChild(item);
        });
    }).catch(() => {});
}

// A platform administrator may see tenants they do not belong to. Resolve
// business context only from the authenticated account's effective memberships,
// including on reload when sessionStorage is empty or contains a stale tenant.
async function _ensureTenantSelected() {
    if (_identityMode() !== 'database') return true;
    const epoch = _authEpoch;
    const response = await fetch('/auth/me', { credentials: 'same-origin', cache: 'no-store' });
    if (epoch !== _authEpoch) return false;
    if (!response.ok) throw new Error('Tenant membership unavailable');
    const data = await response.json();
    if (epoch !== _authEpoch) return false;
    if (!data || data.status !== 'success' || !Array.isArray(data.tenants)) {
        throw new Error('Invalid tenant membership response');
    }
    const tenants = data.tenants.filter(tn => tn && typeof tn.id === 'string' && tn.id);
    const stored = sessionStorage.getItem('cow_tenant_id');
    if (tenants.some(tn => tn.id === stored)) return true;
    sessionStorage.removeItem('cow_tenant_id');
    if (tenants.length === 1) {
        sessionStorage.setItem('cow_tenant_id', tenants[0].id);
        if (typeof bumpTenantGeneration === 'function') bumpTenantGeneration();
        return true;
    }
    if (tenants.length > 1) {
        _showTenantPicker(tenants, null);
        return false;
    }
    if (data.user?.is_platform_admin === true) return 'platform';
    const error = new Error('No available tenant membership');
    error.code = 'no_tenants';
    throw error;
}

function toggleTenantMenu(event) {
    closeAccountMenu();
    event.stopPropagation();
    const menu = document.getElementById('tenant-menu');
    if (menu) menu.classList.toggle('hidden');
}

function toggleLoginPassword() {
    const input = document.getElementById('login-password');
    const icon = document.querySelector('#login-toggle-pwd i');
    if (input.type === 'password') {
        input.type = 'text';
        icon.classList.replace('fa-eye', 'fa-eye-slash');
    } else {
        input.type = 'password';
        icon.classList.replace('fa-eye-slash', 'fa-eye');
    }
}
window.toggleLoginPassword = toggleLoginPassword;

function showLoginScreen() {
    if (typeof closeAppearancePreferences === 'function') closeAppearancePreferences(false);
    _invalidateAccountIdentity(_identityModeState === 'unknown' ? 'error' : 'unauthenticated');
    _accountAppVisible = false;
    _resetHistorySearch();
    if (_identityModeState === 'unknown') { _showAccountCheckGate(); return; }
    _accountState.authRequired = true;
    _accountState.authenticated = false;
    _accountHidden('login-overlay', false);
    _accountHidden('app', true);
    _accountHidden('auth-check-panel', true);
    _accountHidden('login-form', false);
    _accountHidden('login-error', true);
    _accountHidden('login-username-wrap', _identityMode() !== 'database');
    const password = document.getElementById('login-password');
    if (password) { password.value = ''; password.type = 'password'; }
    const icon = document.querySelector('#login-toggle-pwd i');
    if (icon) icon.classList.replace('fa-eye-slash', 'fa-eye');
    const btn = document.getElementById('login-btn');
    if (btn) btn.disabled = !!_accountWritePending;
    _renderSidebarAccount();
    document.getElementById(_identityMode() === 'database' ? 'login-username' : 'login-password')?.focus();
}

async function _submitAccountLogin(event) {
    event.preventDefault();
    if (_accountWritePending || _pendingTenantPicker || _identityMode() === 'unknown') return false;
    const pwdInput = document.getElementById('login-password');
    const userInput = document.getElementById('login-username');
    if (!pwdInput?.value) return false;
    const dbMode = _identityMode() === 'database';
    const epoch = _authEpoch;
    const btn = document.getElementById('login-btn');
    const body = dbMode ? { username: userInput?.value || '', password: pwdInput.value } : { password: pwdInput.value };
    _accountWritePending = 'login';
    ++_accountCheckSeq;
    _accountCheckRequest = null;
    btn.disabled = true;
    _accountHidden('login-error', true);
    try {
        const response = await fetch('/auth/login', {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
        });
        const data = await response.json();
        if (epoch !== _authEpoch) return false;
        if (!response.ok || !data || data.status !== 'success') {
            _accountText('login-error', t('account_credentials_error'));
            _accountHidden('login-error', false);
            pwdInput.value = '';
            (dbMode ? userInput : pwdInput)?.focus();
            return false;
        }
        if (dbMode && data.identity_mode !== 'database') throw new Error('Invalid login mode');
        const loginNext = _normalizeAccountCheck({
            status: 'success', identity_mode: dbMode ? 'database' : 'legacy',
            auth_required: true, authenticated: true, user: data.user,
            must_change_password: dbMode ? Boolean(data.must_change_password) : false
        });
        _acceptAccountIdentity(loginNext, true);
        pwdInput.value = '';
        // A forced change blocks tenant selection and business load: show the
        // change-password gate and stay there until set.
        if (dbMode && loginNext.mustChangePassword) {
            if (typeof bumpTenantGeneration === 'function') bumpTenantGeneration();
            _enterForcedPassword();
            return false;
        }
        // Keep only the sanitized user above, before entering the tenant step.
        const tenants = dbMode && Array.isArray(data.tenants)
            ? data.tenants.filter(tn => tn && typeof tn.id === 'string' && tn.id) : [];
        if (dbMode) sessionStorage.removeItem('cow_tenant_id');
        if (tenants.length > 1) {
            _showTenantPicker(tenants, null);
        } else {
            if (tenants.length === 1) sessionStorage.setItem('cow_tenant_id', tenants[0].id);
            _afterLogin(dbMode);
        }
    } catch (_) {
        if (epoch === _authEpoch) {
            _accountText('login-error', t('account_login_failed'));
            _accountHidden('login-error', false);
        }
    } finally {
        _accountWritePending = null;
        btn.disabled = false;
        _renderSidebarAccount();
    }
    return false;
}

function _afterLogin(dbMode) {
    _clearTenantPicker();
    _resetHistorySearch();
    _enterAccountApp();
}

function _showTenantPicker(tenants, currentId) {
    _clearTenantPicker();
    const tenantGroup = document.getElementById('login-tenant-group');
    const tenantSelect = document.getElementById('login-tenant-select');
    const nextBtn = document.getElementById('login-btn');
    if (!tenantGroup || !tenantSelect || !nextBtn) throw new Error('Tenant picker unavailable');
    _pendingTenantPicker = true;
    _accountHidden('login-overlay', false);
    _accountHidden('app', true);
    _accountHidden('auth-check-panel', true);
    _accountHidden('login-form', false);
    const epoch = _authEpoch;
    const allowed = new Set(tenants.map(tn => tn.id));
    tenantGroup.classList.remove('hidden');
    tenants.forEach(tn => {
        const opt = document.createElement('option');
        opt.value = tn.id;
        opt.textContent = tn.name || tn.code || tn.id;
        if (tn.id === currentId) opt.selected = true;
        tenantSelect.appendChild(opt);
    });
    const enter = event => {
        event.preventDefault();
        if (epoch !== _authEpoch || !_pendingTenantPicker || _accountWritePending) return false;
        const chosen = tenantSelect.value;
        if (!allowed.has(chosen)) return false;
        sessionStorage.setItem('cow_tenant_id', chosen);
        if (typeof bumpTenantGeneration === 'function') bumpTenantGeneration();
        _afterLogin(true);
        return false;
    };
    nextBtn.onclick = enter;
    document.getElementById('login-form').onsubmit = enter;
    _renderSidebarAccount();
    tenantSelect.focus();
}

async function handleLogout() {
    if (_accountWritePending || !((_accountState.authRequired && _accountState.authenticated)
            || _accountState.phase === 'logout_error')) return;
    _accountWritePending = 'logout';
    _invalidateAccountIdentity('logout_pending');
    _resetHistorySearch();
    const epoch = _authEpoch;
    _renderSidebarAccount();
    try {
        const response = await fetch('/auth/logout', { method: 'POST', credentials: 'same-origin' });
        const data = await response.json();
        if (epoch !== _authEpoch) return;
        if (!response.ok || !data || data.status !== 'success') throw new Error('Logout unconfirmed');
        window.location.reload();
    } catch (_) {
        if (epoch === _authEpoch) _accountState = _emptyAccount('logout_error');
    } finally {
        _accountWritePending = null;
        _renderSidebarAccount();
    }
}
window.handleLogout = handleLogout;

// Only a 401 from the current identity can show the login screen. Preserve
// the earlier Agent-routing fetch wrapper and return the original response.
const _originalFetch = window.fetch;
window.fetch = function(...args) {
    const epoch = _authEpoch;
    return _originalFetch.apply(this, args).then(response => {
        if (response.status === 401 && epoch === _authEpoch
                && !['unauthenticated', 'logout_pending'].includes(_accountState.phase)
                && _accountWritePending !== 'login') {
            const input = args[0];
            const raw = typeof input === 'string' ? input : input?.url;
            let url;
            try { url = new URL(raw, window.location.href); } catch (_) { return response; }
            if (url.origin === window.location.origin && !url.pathname.startsWith('/auth/')) {
                showLoginScreen();
            }
        }
        return response;
    });
};

function initApp() {
    applyI18n();
    _applyInputTooltips();
    const epoch = _authEpoch;
    // Top-level variables were read before authentication. Restore the actual
    // account/tenant selections only now, then resolve the Agent before any
    // session/history request can capture an old owner or default.
    if (_identityMode() === 'database') {
        activeAgentId = readScopedPreference('cow_active_agent') || '';
        defaultAgentId = readScopedPreference('cow_default_agent') || 'default';
        memoryAgentId = readScopedPreference('cow_memory_agent') || '';
        knowledgeAgentId = readScopedPreference('cow_knowledge_agent') || '';
    }
    const chatReady = Promise.resolve(loadAgentCatalog()).then(() => {
        if (epoch !== _authEpoch) return;
        sessionId = loadOrCreateSessionId();
        refreshWorkspaceSelector();
        refreshSessionSettings();
        restoreChatState();
        startPolling();
        fetch('/api/knowledge/list').then(r => r.json()).then(data => {
            if (epoch === _authEpoch && data.status === 'success') {
                _knowledgeTreeData = data.tree || [];
                _knowledgeRootFiles = data.root_files || [];
            }
        }).catch(() => {});
    });

    fetch('/api/version').then(async response => {
        if (!response.ok) throw new Error('Version unavailable');
        const data = await response.json();
        if (!data || (data.status && data.status !== 'success')
                || typeof data.version !== 'string' || !data.version.trim()) throw new Error('Invalid version');
        APP_VERSION = 'v' + data.version.trim().replace(/^v/i, '');
        renderAccountVersion();
    }).catch(() => renderAccountVersion());
    return chatReady;
}

// =====================================================================
// Account self-context: /auth/me, six-item menu, password, prefs, tenant switch
// =====================================================================
// Read-only self profile cached for the current account/epoch. Cleared on
// account switch/logout so a previous account cannot restore into a new one.
let _accountSelf = null;
let _accountSelfSeq = 0;
let _accountSelfRequest = null;
// Bump on each successful self-avatar upload so the hero image refetches.
let _accountAvatarVersion = '';
// Current tenant's authoritative /auth/context capability summary, cached per
// account/epoch. This is the *display* projection used to gate the sidebar and
// navigation availability (console_pages / authorization_mode / is_tenant_admin);
// the server still independently authorizes every API call.
let _authContext = null;
let _authContextSeq = 0;
let _authContextRequest = null;
let _activeAccountPanel = null;  // 'profile' | 'password' | 'prefs' | 'tenant' | 'about'

function _db() { return _identityMode() === 'database'; }

function _setAccountPanel(panel) {
    _activeAccountPanel = panel;
    ['account-password-form', 'account-password-status', 'ap-old-password',
     'ap-new-password', 'ap-confirm-password'].forEach(id => {
        const el = document.getElementById(id);
        if (el && panel !== 'password') delete el.dataset.dirty;
    });
}

// Close account surfaces before opening another, preserving the existing
// password form's dismissal guard. The native preferences dialog owns focus.
function closeAccountPanels(returnFocus = false) {
    if (_forcedPassword) return false;
    if (_activeAccountPanel === 'password') {
        cancelAccountPassword();
        if (_activeAccountPanel === 'password') return false;
    }
    closeAppearancePreferences(returnFocus);
    _accountHidden('account-profile-drawer', true);
    _setAccountPanel(null);
    return true;
}

// --- profile ------------------------------------------------------------

const _ACCOUNT_PROFILE_SEL = {
    displayName: 'ap-display-name', username: 'ap-username', platform: 'ap-platform',
    tenant: 'ap-tenant', memberName: 'ap-member-name', role: 'ap-role',
    department: 'ap-department', position: 'ap-position',
};

async function fetchAccountSelf() {
    // Refreshes /auth/me once per sequence; dedupes concurrent calls.
    if (_accountSelfRequest) return _accountSelfRequest;
    const seq = ++_accountSelfSeq;
    const epoch = _authEpoch;
    const request = Promise.resolve().then(async () => {
        try {
            const resp = await fetch('/auth/me', { credentials: 'same-origin', cache: 'no-store' });
            const data = await resp.json();
            if (seq !== _accountSelfSeq) return null;
            if (resp.status === 401 || data.status !== 'success') return null;
            if (epoch !== _authEpoch) return null;
            _accountSelf = data;
            return data;
        } catch (_) {
            if (seq === _accountSelfSeq && epoch === _authEpoch) _accountSelf = null;
            return null;
        } finally {
            if (_accountSelfRequest === request) _accountSelfRequest = null;
        }
    });
    _accountSelfRequest = request;
    return request;
}

// Best-effort sync view of the last successful /auth/me. Returns null until the
// first fetch resolves; callers must treat null as "unknown" (leave menus as-is)
// rather than as a privilege denial.
function _baseAccountSelf() {
    return _accountSelf && _accountSelf.status === 'success' ? _accountSelf : null;
}

// Resolve the *member-level* display name for the currently-selected tenant from
// the cached /auth/me projection. This is the per-tenant "display name" edited on
// the member record (memberships.display_name), which may differ from the
// account-level display name. Returns '' when there is no self projection, no
// selected tenant, the account is not an active member of that tenant, or the
// member has no display name.
function _currentMemberDisplayName() {
    const self = _baseAccountSelf();
    if (!self) return '';
    const tid = sessionStorage.getItem('cow_tenant_id') || '';
    if (!tid) return '';
    const tenants = (self.tenants || []).filter(tn => tn && tn.id === tid);
    if (!tenants.length) return '';
    const membership = tenants[0].membership || {};
    const displayName = (typeof membership.display_name === 'string')
        ? membership.display_name.trim() : '';
    return displayName;
}

// Best-effort sync view of the last successful /auth/context. Returns null
// until the first fetch resolves; callers must treat null as "unknown", not as
// a privilege denial (menus stay as-is until the projection is known).
function _baseAuthContext() {
    return _authContext && _authContext.status === 'success' ? _authContext : null;
}

// Fetch the current tenant's authoritative capability summary (/auth/context)
// for the *display* projection only. Dedupes concurrent calls and bails on any
// epoch change (account switch/logout). No X-Tenant-ID present -> null (unknown);
// this function never throws.
async function _fetchTenantAuthorization() {
    if (_identityMode() !== 'database') return null;
    if (_authContextRequest) return _authContextRequest;
    const tenantId = sessionStorage.getItem('cow_tenant_id') || '';
    if (!tenantId) return null;
    const seq = ++_authContextSeq;
    const epoch = _authEpoch;
    const request = Promise.resolve().then(async () => {
        try {
            const resp = await fetch('/auth/context', {
                credentials: 'same-origin', cache: 'no-store',
                headers: { 'X-Tenant-ID': tenantId },
            });
            const data = await resp.json();
            if (seq !== _authContextSeq) return null;
            if (epoch !== _authEpoch) return null;
            if (resp.status === 401 || resp.status === 403 || data.status !== 'success') {
                _authContext = null;
                return null;
            }
            _authContext = data;
            return data;
        } catch (_) {
            if (seq === _authContextSeq && epoch === _authEpoch) _authContext = null;
            return null;
        } finally {
            if (_authContextRequest === request) _authContextRequest = null;
        }
    });
    _authContextRequest = request;
    return request;
}

// Reset the cached /auth/context when the tenant selection changes, so stale
// capability data from a previous tenant is never used to gate navigation.
function _invalidateAuthContext() {
    _authContext = null;
}

// Map a view id to its authoritative console_pages key (or '' if none). Server
// returns the projection; unknown views fall back to "available" so navigation
// is never spuriously blocked for pages the backend does not sign.
function _consolePageForView(viewId) {
    const meta = VIEW_META[viewId];
    return meta && meta.console ? meta.console : '';
}

// Return {reason:'denied'} when the target view is genuinely unavailable to the
// current identity (no read grant and page not open), in database mode with a
// known projection. Returns falsy to allow navigation. Platform "all" mode and
// any view the backend didn't sign are allowed. A denied read (but open) page is
// also a denial, since the identity cannot use it.
function _viewNavDenied(viewId) {
    if (_identityMode() !== 'database') return null;
    const ctx = _baseAuthContext();
    if (!ctx) return null; // projection unknown -> don't guess / don't block
    if (ctx.authorization_mode === 'all') return null; // platform all
    const key = _consolePageForView(viewId);
    if (!key) return null; // backend didn't sign this page -> leave as-is
    // Only the admin-management pages are gated by the projection. Workbench
    // pages (chat/history/agents/todo/tasks/knowledge) are normal business
    // entry points and are never denied — their consumer availability is
    // reported separately (e.g. "closed consumer" on the page itself).
    if (key.indexOf('admin.') !== 0) return null;
    const pages = ctx.console_pages && typeof ctx.console_pages === 'object' ? ctx.console_pages : null;
    if (!pages || !pages[key]) return null; // unknown key -> don't guess
    if (pages[key].available || pages[key].read_allowed) return null;
    return { reason: 'denied' };
}

// Gate the permission-sensitive sidebar entries (task 5.7/5.8). The "platform
// accounts" entry is visible only to a platform admin. The "identity audit"
// entry is visible only to a platform admin OR the current tenant's tenant_admin
// (or anyone holding a tenant-scoped audit privilege). Rows that fail the check
// are hidden; the authorization decision always stays server-side.
function _applySidebarPermissions(self) {
    // is_platform_admin still comes from the verified /auth/me self profile
    // (it is NOT tenant-scoped, so /auth/context cannot report it). The current
    // tenant's admin qualification and page availability come from the
    // authoritative /auth/context projection — never from a client role array.
    const gotSelf = self || _baseAccountSelf();
    const user = gotSelf && gotSelf.user ? gotSelf.user : null;
    const isPlatformAdmin = !!(user && user.is_platform_admin);
    const ctx = _baseAuthContext();
    const isTenantAdmin = !!(ctx && (ctx.is_tenant_admin === true));
    const mode = (ctx && ctx.authorization_mode) || 'role';
    const pages = (ctx && ctx.console_pages && typeof ctx.console_pages === 'object')
        ? ctx.console_pages : null;

    const isDb = _identityMode() === 'database';
    // Entry to /admin: platform admin OR current-tenant tenant_admin only.
    // Do not use "any readable admin page" for this entry (stricter than area menus).
    const showAdminEntry = _qualifyAdminConsoleEntry({
        identityMode: _identityMode(),
        isPlatformAdmin,
        isTenantAdmin,
    });
    const openAdminEl = document.getElementById('nav-open-admin');
    if (openAdminEl) openAdminEl.classList.toggle('hidden', !showAdminEntry);

    // Inside /admin, show admin groups when the entry is qualified; per-item
    // console_pages filtering below still applies.
    const canAdmin = showAdminEntry;

    if (isDb && _navAreaFromPath(location.pathname) === 'admin') {
        // Redirect only once qualification is known (platform from /auth/me,
        // otherwise wait for /auth/context).
        if (isPlatformAdmin || ctx) {
            if (!showAdminEntry) {
                try { sessionStorage.setItem('cow_nav_admin_denied', '1'); } catch (_) {}
                location.replace('/chat');
                return;
            }
        }
    }

    if (isDb) {
        document.querySelectorAll('#sidebar-nav .sidebar-hidden-admin-area')
            .forEach(el => el.classList.toggle('hidden', !canAdmin));
        document.querySelectorAll('#sidebar-nav .sidebar-hidden-platform-scope')
            .forEach(el => el.classList.toggle('hidden', !isPlatformAdmin));
    } else {
        document.querySelectorAll('#sidebar-nav .sidebar-hidden-admin-area')
            .forEach(el => el.classList.toggle('hidden', false));
        document.querySelectorAll('#sidebar-nav .sidebar-hidden-platform-scope')
            .forEach(el => el.classList.toggle('hidden', false));
    }

    // Per-item availability from the authoritative projection. In "all" mode a
    // page is available if the backend signed it (available flag) regardless of
    // a read grant. When the projection is unknown (not yet loaded / legacy),
    // leave items as-is rather than hiding a page on a guess. Only admin.* pages
    // are gated; workbench pages are normal business entries and stay visible.
    const allMode = (mode === 'all');
    if (isDb && ctx) {
        document.querySelectorAll('#sidebar-nav .sidebar-item[data-view]').forEach(item => {
            const viewId = item.getAttribute('data-view');
            const key = _consolePageForView(viewId);
            if (!key || key.indexOf('admin.') !== 0) return; // not admin page -> leave as-is
            const pageInfo = pages && pages[key];
            if (!pageInfo) return; // unknown key -> don't guess
            const available = allMode ? true : !!(pageInfo.available);
            const readOk = allMode ? true : !!(pageInfo.read_allowed);
            // A page is shown when it is available; if the identity may read it
            // but the consumer is closed, still show it as read-only/explained
            // rather than hiding a granted page (spec: keep consumer states
            // separate). Hide only when it is genuinely unavailable/denied.
            item.classList.toggle('hidden', !(available || readOk));
        });
    }

    // Per-item: platform entries only for a platform admin.
    const platformEl = document.querySelector('.sidebar-item[data-view="platform"]');
    if (platformEl) platformEl.classList.toggle('hidden', !isPlatformAdmin);
}

function openAccountProfile() {
    if (!closeAccountPanels(false)) return;
    closeAccountMenu();
    _setAccountPanel('profile');
    _accountHidden('account-profile-drawer', false);
    _accountHidden('account-profile-content', true);
    _accountHidden('account-profile-status', true);
    resetAccountProfileEditor();
    fetchAccountSelf().then(() => {
        renderAccountProfile();
        focusAccountPanel('account-profile-drawer');
    }).catch(() => {
        _accountText('account-profile-status', t('account_profile_error'));
        _accountHidden('account-profile-status', false);
    });
}

function renderAccountProfile() {
    const box = document.getElementById('account-profile-content');
    if (!box) return;
    const ctx = _accountSelf;
    box.classList.remove('hidden');
    _accountHidden('account-profile-status', true);
    if (!ctx || !ctx.user) {
        _accountText('account-profile-status', t('account_profile_error'));
        _accountHidden('account-profile-status', false);
        return;
    }
    const user = ctx.user;
    const displayName = user.display_name || user.username || '—';

    // Hero header (avatar + name + username)
    renderAccountProfileAvatar(user);
    _accountText('account-profile-hero-name', displayName);
    _accountText('account-profile-hero-username', '@' + (user.username || ''));

    // Read mode values
    _accountText(_ACCOUNT_PROFILE_SEL.displayName, displayName);
    _accountText(_ACCOUNT_PROFILE_SEL.username, user.username || '—');
    _accountText(_ACCOUNT_PROFILE_SEL.platform, user.is_platform_admin ? t('platform_admin_badge') : t('account_public_mode'));

    // Resolve the selected tenant's membership from the self list.
    const tid = sessionStorage.getItem('cow_tenant_id');
    const entry = (ctx.tenants || []).find(tn => tn.id === tid) || (ctx.tenants || [])[0];
    if (!entry) {
        _accountText(_ACCOUNT_PROFILE_SEL.tenant, t('account_profile_no_tenant'));
        _accountText(_ACCOUNT_PROFILE_SEL.memberName, t('account_profile_empty'));
        _accountText(_ACCOUNT_PROFILE_SEL.role, t('account_profile_empty'));
        _accountText(_ACCOUNT_PROFILE_SEL.department, t('account_profile_empty'));
        _accountText(_ACCOUNT_PROFILE_SEL.position, t('account_profile_empty'));
        _accountHidden('account-profile-member-section', true);
        return;
    }
    _accountHidden('account-profile-member-section', false);
    const m = entry.membership || {};
    _accountText(_ACCOUNT_PROFILE_SEL.tenant, entry.name || entry.code || entry.id);
    _accountText(_ACCOUNT_PROFILE_SEL.memberName, m.display_name || t('account_profile_empty'));
    _accountText(_ACCOUNT_PROFILE_SEL.role, (m.roles || []).map(r => r.name || r.code).join(', ') || t('account_profile_empty'));
    _accountText(_ACCOUNT_PROFILE_SEL.department, m.department ? (m.department.name || m.department.id) : t('account_profile_empty'));
    _accountText(_ACCOUNT_PROFILE_SEL.position, m.position_text || t('account_profile_empty'));
}

function closeAccountProfile() {
    resetAccountProfileEditor();
    _accountHidden('account-profile-drawer', true);
    if (_activeAccountPanel === 'profile') _setAccountPanel(null);
    if (_accountAppVisible) document.getElementById('sidebar-account-toggle')?.focus();
}

// --- profile edit (read -> edit state) -----------------------------------

let _accountProfileEditing = false;
let _accountProfileAvatarUploading = false;

// Render the caller's own avatar (or an initial disc) in the hero + menu.
function renderAccountProfileAvatar(user) {
    const box = document.getElementById('account-profile-avatar');
    if (!box) return;
    if (user && user.avatar === 'image') {
        const v = _accountAvatarVersion || user.id || Date.now();
        box.innerHTML = `<img src="/auth/profile/avatar?v=${encodeURIComponent(v)}" alt="">`;
    } else {
        const initial = avatarInitial(user && (user.display_name || user.id));
        box.innerHTML = `<span class="account-profile-avatar-initial">${escapeHtml(initial)}</span>`;
    }
}

// Reset to the read-only state (clean inputs, exit edit mode).
function resetAccountProfileEditor() {
    _accountProfileEditing = false;
    _accountProfileAvatarUploading = false;
    const rows = document.querySelectorAll('#account-profile-content .account-profile-row');
    rows.forEach(r => r.classList.remove('editing'));
    _accountHidden('account-profile-edit-btn', false);
    _accountHidden('account-profile-save-btn', true);
    _accountHidden('account-profile-cancel-btn', true);
    _accountHidden('account-profile-close-btn', false);
    const hint = document.getElementById('account-profile-action-status');
    if (hint) { hint.classList.add('hidden'); hint.textContent = ''; }
    ['ap-display-name-input', 'ap-member-name-input', 'ap-position-input'].forEach(id => {
        const el = document.getElementById(id);
        if (el) { el.value = ''; el.classList.remove('invalid'); el.removeAttribute('aria-invalid'); }
        const err = document.getElementById(id + '-error');
        if (err) err.classList.add('hidden');
    });
}

// Enter edit mode: swap read rows for editable inputs and show save/cancel.
function startAccountProfileEdit() {
    const ctx = _accountSelf;
    if (!ctx || !ctx.user) return;
    if (_accountProfileEditing) return;
    _accountProfileEditing = true;

    // Populate inputs from the current projection.
    const user = ctx.user;
    const tid = sessionStorage.getItem('cow_tenant_id');
    const entry = (ctx.tenants || []).find(tn => tn.id === tid) || (ctx.tenants || [])[0];
    const m = (entry && entry.membership) || {};
    setInputValue('ap-display-name-input', user.display_name || '');
    setInputValue('ap-member-name-input', m.display_name || '');
    setInputValue('ap-position-input', m.position_text || '');

    // Only the editable rows are toggled; role/dept/tenant/username stay read.
    document.querySelectorAll('#account-profile-content .account-profile-row[data-field]')
        .forEach(r => r.classList.add('editing'));

    _accountHidden('account-profile-edit-btn', true);
    _accountHidden('account-profile-save-btn', false);
    _accountHidden('account-profile-cancel-btn', false);
    _accountHidden('account-profile-close-btn', true);
    const hint = document.getElementById('account-profile-action-status');
    if (hint) hint.classList.add('hidden');
    // Clear a field's inline error the moment the user starts typing in it.
    ['ap-display-name-input', 'ap-member-name-input', 'ap-position-input'].forEach(id => {
        const input = document.getElementById(id);
        if (!input || input.dataset.profileErrBound) return;
        input.dataset.profileErrBound = '1';
        input.addEventListener('input', () => {
            input.classList.remove('invalid');
            input.removeAttribute('aria-invalid');
            const err = document.getElementById(id + '-error');
            if (err) err.classList.add('hidden');
        });
    });
    // Move focus to the first editable input so the user can type immediately
    // (focusAccountPanel would land on the modal close/avatar button instead).
    const firstInput = document.getElementById('ap-display-name-input');
    if (firstInput) window.setTimeout(() => { firstInput.focus(); firstInput.select?.(); }, 0);
}

function setInputValue(id, value) {
    const el = document.getElementById(id);
    if (el) el.value = value || '';
}

// Leave edit mode without saving (same as cancel).
function cancelAccountProfileEdit() {
    if (_accountProfileAvatarUploading) return;
    resetAccountProfileEditor();
    renderAccountProfile();
}

// Highlight a profile input as invalid and show its inline error message.
function markFieldError(inputId, message) {
    const input = document.getElementById(inputId);
    if (input) {
        input.classList.add('invalid');
        input.setAttribute('aria-invalid', 'true');
        input.focus();
    }
    const err = document.getElementById(inputId + '-error');
    if (err) {
        err.textContent = message;
        err.classList.remove('hidden');
    }
}

// Persist the edited fields (display_name + member name/position).
function submitAccountProfile() {
    if (_accountProfileAvatarUploading || _accountWritePending) return;
    const ctx = _accountSelf;
    if (!ctx || !ctx.user) return;
    const displayName = document.getElementById('ap-display-name-input')?.value || '';
    const memberName = document.getElementById('ap-member-name-input')?.value || '';
    const position = document.getElementById('ap-position-input')?.value || '';
    const hint = document.getElementById('account-profile-action-status');
    const saveBtn = document.getElementById('account-profile-save-btn');

    // Client-side validation mirroring the backend guards.
    const displayNameVal = displayName.trim();
    const memberNameVal = memberName.trim();
    if (!displayNameVal) {
        markFieldError('ap-display-name-input', t('account_profile_error_required'));
        return;
    }
    // member name is optional only when no tenant; when editing a tenant member it
    // is required just like display name (mirrors backend guard).
    const tid = sessionStorage.getItem('cow_tenant_id');
    const hasTenant = (ctx.tenants || []).some(tn => tn.id === tid);
    if (hasTenant && !memberNameVal) {
        markFieldError('ap-member-name-input', t('account_profile_error_required'));
        return;
    }

    _accountWritePending = 'profile';
    if (saveBtn) saveBtn.disabled = true;
    if (hint) { hint.textContent = ''; hint.classList.add('hidden'); }
    const body = { display_name: displayNameVal };
    // Send member fields only when the caller belongs to a tenant.
    if (hasTenant) {
        body.member_display_name = memberNameVal;
        body.position_text = position.trim();
    }
    fetch('/auth/profile', {
        method: 'PATCH',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', ...(tid ? { 'X-Tenant-ID': tid } : {}) },
        body: JSON.stringify(body),
    }).then(r => r.json()).then(data => {
        if (data && data.status === 'success') {
            _accountSelf = data;
            resetAccountProfileEditor();
            renderAccountProfile();
            if (hint) { hint.textContent = t('saved'); hint.classList.remove('hidden'); }
        } else if (data && data.code === 'password_change_required') {
            if (hint) { hint.textContent = t('account_password_note'); hint.classList.remove('hidden'); }
        } else {
            if (hint) { hint.textContent = (data && data.message) || t('account_profile_error'); hint.classList.remove('hidden'); }
        }
    }).catch(() => {
        if (hint) { hint.textContent = t('account_profile_error'); hint.classList.remove('hidden'); }
    }).finally(() => {
        _accountWritePending = null;
        if (saveBtn) saveBtn.disabled = false;
        // Refresh the sidebar identity now that the write is no longer pending,
        // so the account button picks up the new display name.
        refreshAccountIdentity();
    });
}

// Upload a new avatar for the caller.
function uploadAccountProfileAvatar(file) {
    if (!file) return;
    if (_accountProfileAvatarUploading) return;
    _accountProfileAvatarUploading = true;
    const hint = document.getElementById('account-profile-action-status');
    const loader = document.getElementById('account-profile-avatar');
    if (loader) loader.classList.add('is-uploading');
    const form = new FormData();
    form.append('avatar', file);
    fetch('/auth/profile/avatar', { method: 'POST', credentials: 'same-origin', body: form })
        .then(r => r.json())
        .then(data => {
            if (data && data.status === 'success') {
                _accountAvatarVersion = String(Date.now());
                _accountSelf = data;
                renderAccountProfileAvatar(data.user || {});
                refreshAccountIdentity();
                if (hint) { hint.textContent = t('saved'); hint.classList.remove('hidden'); }
            } else {
                if (hint) { hint.textContent = (data && data.message) || t('account_profile_error'); hint.classList.remove('hidden'); }
            }
        })
        .catch(() => {
            if (hint) { hint.textContent = t('account_profile_error'); hint.classList.remove('hidden'); }
        })
        .then(() => {
            _accountProfileAvatarUploading = false;
            if (loader) loader.classList.remove('is-uploading');
        });
}

// Render the profile avatar picker (preview + upload trigger) in the hero.


// --- change password ----------------------------------------------------

function openAccountPassword() {
    if (!closeAccountPanels(false)) return;
    closeAccountMenu();
    _setAccountPanel('password');
    _accountHidden('account-password-modal', false);
    _accountHidden('account-password-status', true);
    ['ap-old-password', 'ap-new-password', 'ap-confirm-password'].forEach(id => {
        const el = document.getElementById(id);
        if (el) { el.value = ''; delete el.dataset.dirty; }
    });
    focusAccountPanel('account-password-modal');
}

function cancelAccountPassword() {
    if (_forcedPassword) {
        // No dismissal: offer logout as the only way out. If confirmed, log out.
        if (window.confirm(t('account_password_forced_logout'))) handleLogout();
        return;
    }
    const dirty = ['ap-old-password', 'ap-new-password', 'ap-confirm-password']
        .some(id => document.getElementById(id)?.dataset.dirty);
    if (dirty && !window.confirm(t('unsaved_changes_warning'))) return;
    _clearPasswordInputs();
    closeAccountPassword();
}

function closeAccountPassword() {
    // Forced change must not be bypassed by closing the modal: the restricted
    // account can only set a new password or log out. Keep the gate open.
    if (_forcedPassword) return;
    _accountHidden('account-password-modal', true);
    _setAccountPanel(null);
    if (_accountAppVisible) document.getElementById('sidebar-account-toggle')?.focus();
}

function _clearPasswordInputs() {
    ['ap-old-password', 'ap-new-password', 'ap-confirm-password'].forEach(id => {
        const el = document.getElementById(id);
        if (el) { el.value = ''; delete el.dataset.dirty; }
    });
}

async function submitAccountPassword(event) {
    if (event) event.preventDefault();
    const oldPw = document.getElementById('ap-old-password')?.value || '';
    const newPw = document.getElementById('ap-new-password')?.value || '';
    const confirmPw = document.getElementById('ap-confirm-password')?.value || '';
    const status = document.getElementById('account-password-status');
    const submitBtn = document.getElementById('ap-submit-btn');
    if (!oldPw || !newPw || !confirmPw) {
        _accountPasswordStatus(t('account_password_unknown'), true);
        return;
    }
    if (newPw !== confirmPw) {
        _accountPasswordStatus(t('account_password_mismatch'), true);
        return;
    }
    if (_accountWritePending) return;
    _accountWritePending = 'password';
    if (submitBtn) submitBtn.disabled = true;
    _accountHidden('account-password-status', true);
    try {
        const resp = await fetch('/auth/password', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ old_password: oldPw, new_password: newPw }),
        });
        let data = null;
        try { data = await resp.json(); } catch (_) {}
        if (resp.status === 401 && data && data.code === 'invalid_old') {
            _accountPasswordStatus(t('account_password_invalid_old'), true);
            return;
        }
        if (resp.status === 400 && data && data.code === 'weak_password') {
            _accountPasswordStatus(t('account_password_weak'), true);
            return;
        }
        if (resp.status === 401) {
            // unauthorized -> session invalid; return to login
            showLoginScreen();
            return;
        }
        if (!resp.ok || !data || data.status !== 'success') {
            _accountPasswordStatus(t('account_password_unknown'), true);
            return;
        }
        // Success: session revoked & cookie cleared; force re-login.
        _accountPasswordStatus(t('account_password_done'), false);
        _clearPasswordInputs();
        _accountSelf = null;
        const wasForced = _forcedPassword;
        if (wasForced) _closeForcedPasswordModal();
        window.setTimeout(() => { showLoginScreen(); }, wasForced ? 700 : 900);
    } catch (_) {
        _accountPasswordStatus(t('account_password_unknown'), true);
    } finally {
        _accountWritePending = null;
        if (submitBtn) submitBtn.disabled = false;
    }
}

function _accountPasswordStatus(text, isError) {
    const status = document.getElementById('account-password-status');
    if (!status) return;
    status.textContent = text || '';
    status.classList.toggle('error', !!isError);
    status.classList.remove('hidden');
}

// --- preferences (theme + language, browser-local) -----------------------

function openAccountPrefs() {
    openAppearancePreferences(document.getElementById('sidebar-account-toggle'));
}

function closeAccountPrefs() {
    closeAppearancePreferences();
}

function _syncAccountPrefButtons() {
    renderAppearancePreferences();
}

function setAccountTheme(theme) {
    window.CowAppearance.setMode(theme);
}

function setAccountLang(lang) {
    if (lang !== 'zh' && lang !== 'zh-Hant' && lang !== 'en') return;
    // Personal preference: browser-local only, MUST NOT write instance config.
    setLanguageLocal(lang);
    _syncAccountPrefButtons();
    if (typeof window.__cowLang__ !== 'undefined') window.__cowLang__ = lang;
}

function _accountPrefStorageWarn() {
    languageStorageFailed = true;
    renderAppearancePreferences();
}

// --- about ---------------------------------------------------------------

function openAccountAbout() {
    closeAccountMenu();
    // Reuse the version link row: navigate to the release changelog, which is
    // the existing "original update log" entry.
    const version = document.getElementById('sidebar-version');
    if (version && version.href) window.open(version.href, '_blank', 'noopener');
    _setAccountPanel(null);
}

// --- tenant switching (refresh-based) ------------------------------------

async function openAccountTenant() {
    closeAccountMenu();
    const epoch = _authEpoch;
    const self = await fetchAccountSelf();
    if (epoch !== _authEpoch) return;
    if (!self) {
        window.alert(t('account_profile_error'));
        return;
    }
    const tenants = Array.isArray(self.tenants) ? self.tenants : [];
    if (tenants.length === 0) {
        window.alert(t('account_tenant_no_available'));
        return;
    }
    if (tenants.length === 1) {
        window.alert(t('account_tenant_single'));
        return;
    }
    const current = sessionStorage.getItem('cow_tenant_id');
    // Lists available tenants; clicking a non-current tenant navigates via a
    // one-shot switch_tenant query param so the old page never pre-changes the
    // stored tenant. The new page validates before committing.
    const label = (tn) => (tn.name || tn.code || tn.id) + (tn.id === current ? ' (' + t('account_tenant_current') + ')' : '');
    const choice = window.prompt(t('account_tenant_title') + '\n' + tenants.map((tn, i) => (i + 1) + '. ' + label(tn)).join('\n'));
    if (!choice) return;
    const idx = parseInt(choice, 10) - 1;
    if (!Number.isInteger(idx) || idx < 0 || idx >= tenants.length) return;
    const target = tenants[idx];
    if (target.id === current) return;
    _applyTenantSwitch(target.id);
}

function _applyTenantSwitch(targetId) {
    if (!targetId || _accountWritePending) return;
    _accountWritePending = 'tenant';
    try {
        const url = new URL(window.location.href);
        url.searchParams.set('switch_tenant', targetId);
        // The old page does NOT change cow_tenant_id; the new page validates.
        window.location.assign(url.toString());
    } catch (_) {
        _accountWritePending = null;
    }
}

// --- shared focus helper -------------------------------------------------

function focusAccountPanel(panelId) {
    const panel = document.getElementById(panelId);
    if (!panel) return;
    const focusable = panel.querySelector('input, select, button, a');
    if (focusable) window.setTimeout(() => focusable.focus(), 0);
}

// Prevent accidental navigation/close when the password form has input.
function accountBeforeUnload(e) {
    if (_forcedPassword) return;  // forced gate may be exited via logout reload
    const dirty = ['ap-old-password', 'ap-new-password', 'ap-confirm-password']
        .some(id => document.getElementById(id)?.dataset.dirty);
    if (dirty || _activeAccountPanel === 'password') {
        e.preventDefault();
        e.returnValue = true;
    }
}
window.addEventListener('beforeunload', accountBeforeUnload);

// One-shot tenant switch validation on load: read the switch_tenant query
// param, validate it against /auth/me, then commit and strip the param.
function _resolveOneShotTenantSwitch() {
    try {
        const url = new URL(window.location.href);
        const target = url.searchParams.get('switch_tenant');
        const epoch = _authEpoch;
        if (!target || _identityMode() !== 'database') return Promise.resolve(false);
        return fetch('/auth/me', { credentials: 'same-origin', cache: 'no-store' })
            .then(r => r.json())
            .then(data => {
                if (epoch !== _authEpoch) return false;
                const valid = data && data.status === 'success'
                    && Array.isArray(data.tenants)
                    && data.tenants.some(tn => tn.id === target);
                if (valid) {
                    sessionStorage.setItem('cow_tenant_id', target);
                    if (typeof bumpTenantGeneration === 'function') bumpTenantGeneration();
                    // strip the one-shot param
                    url.searchParams.delete('switch_tenant');
                    if (typeof window.history !== 'undefined' && window.history.replaceState) {
                        window.history.replaceState(null, '', url.toString());
                    }
                    return true;
                }
                // invalid target: keep global login, do not silently enter another
                window.alert(t('account_tenant_invalid'));
                if (typeof bumpTenantGeneration === 'function') bumpTenantGeneration();
                url.searchParams.delete('switch_tenant');
                if (typeof window.history !== 'undefined' && window.history.replaceState) {
                    window.history.replaceState(null, '', url.toString());
                }
                return false;
            })
            .catch(() => false);
    } catch (_) {
        return Promise.resolve(false);
    }
}

// Mark password inputs dirty on change (for the beforeunload confirmation).
document.addEventListener('input', (e) => {
    if (e.target && ['ap-old-password', 'ap-new-password', 'ap-confirm-password'].includes(e.target.id)) {
        e.target.dataset.dirty = '1';
    }
});

window.openAccountProfile = openAccountProfile;
window.openAccountPassword = openAccountPassword;
window.closeAccountProfile = closeAccountProfile;
window.startAccountProfileEdit = startAccountProfileEdit;
window.cancelAccountProfileEdit = cancelAccountProfileEdit;
window.submitAccountProfile = submitAccountProfile;
window.uploadAccountProfileAvatar = uploadAccountProfileAvatar;
window.closeAccountPassword = closeAccountPassword;
window.cancelAccountPassword = cancelAccountPassword;
window.openAccountPrefs = openAccountPrefs;
window.closeAccountPrefs = closeAccountPrefs;
window.setAccountTheme = setAccountTheme;
window.setAccountLang = setAccountLang;
window.openAccountAbout = openAccountAbout;
window.openAccountTenant = openAccountTenant;
window.submitAccountPassword = submitAccountPassword;

// =====================================================================
// Initialization
// =====================================================================
applyTheme();
applyI18n();

// Wire the change-password form submit (single submission).
(function () {
    const form = document.getElementById('account-password-form');
    if (form) form.addEventListener('submit', submitAccountPassword);
})();

refreshAccountIdentity();

requestAnimationFrame(() => {
    document.body.classList.add('transition-colors', 'duration-200');
});

// =====================================================================
// Task Edit Modal
// =====================================================================
let currentEditingTask = null;

function loadTaskChannelOptions(selectedChannelType) {
    const select = document.getElementById('task-edit-channel-type');
    select.innerHTML = '';
    fetch('/api/channels').then(r => r.json()).then(data => {
        if (data.status !== 'success') return;
        const allChannels = data.channels || [];
        // Only include currently active channels, strictly following the channel management page logic
        let channels = allChannels.filter(c => c.active).map(c => {
            const label = (typeof c.label === 'object') ? (c.label[currentLang] || c.label.en || c.name) : (c.label || c.name);
            return { name: c.name, label: label };
        });
        const channelNames = channels.map(c => c.name);
        // Always include the web console channel
        if (!channelNames.includes('web')) {
            channels.unshift({ name: 'web', label: currentLang === 'zh' ? 'Web' : 'Web' });
        }
        // If the currently selected channel is not in the active list (e.g. disabled), append it to preserve selection
        if (selectedChannelType && !channelNames.includes(selectedChannelType) && selectedChannelType !== 'web') {
            const ch = allChannels.find(c => c.name === selectedChannelType);
            const label = ch
                ? ((typeof ch.label === 'object') ? (ch.label[currentLang] || ch.label.en || ch.name) : (ch.label || ch.name))
                : selectedChannelType;
            channels.push({ name: selectedChannelType, label: label });
        }
        channels.forEach(c => {
            const opt = document.createElement('option');
            opt.value = c.name;
            opt.textContent = c.label;
            select.appendChild(opt);
        });
        // Set selected value
        if (selectedChannelType) {
            select.value = selectedChannelType;
        }
    }).catch(() => {
        // fallback: at least keep the current selection and web
        select.innerHTML = '';
        const webOpt = document.createElement('option');
        webOpt.value = 'web';
        webOpt.textContent = 'Web';
        select.appendChild(webOpt);
        
        if (selectedChannelType && selectedChannelType !== 'web') {
            const opt = document.createElement('option');
            opt.value = selectedChannelType;
            opt.textContent = selectedChannelType;
            select.appendChild(opt);
        }
        if (selectedChannelType) {
            select.value = selectedChannelType;
        }
        
        // Show error message
        console.error('Failed to load channel options');
    });
}

// The owning Agent shown (read-only) in the task edit modal header. Hidden on a
// single-Agent install, where every task belongs to the one Agent anyway.
function renderTaskOwnerChip(task) {
    const el = document.getElementById('task-edit-owner');
    if (!el) return;
    const agent = task.agent_id ? findAgent(task.agent_id) : null;
    if (!multiAgentMode() || !agent) {
        el.classList.add('hidden');
        el.innerHTML = '';
        return;
    }
    el.innerHTML = `${agentAvatarHTML(agent, 20)}
        <span class="text-xs font-medium text-slate-600 dark:text-slate-300 truncate max-w-[120px]">${escapeHtml(agent.name || agent.id)}</span>`;
    el.classList.remove('hidden');
    el.classList.add('flex');
}

function openTaskEditModal(task) {
    currentEditingTask = task;
    const overlay = document.getElementById('task-edit-modal-overlay');
    const titleEl = document.querySelector('#task-edit-modal-overlay h3');
    const subtitle = document.getElementById('task-edit-modal-subtitle');
    const deleteBtn = document.getElementById('task-edit-modal-delete');
    const nameInput = document.getElementById('task-edit-name');
    const enabledInput = document.getElementById('task-edit-enabled');
    const scheduleTypeSelect = document.getElementById('task-edit-schedule-type');
    const cronInput = document.getElementById('task-edit-cron-expression');
    const intervalInput = document.getElementById('task-edit-interval-seconds');
    const onceInput = document.getElementById('task-edit-once-time');
    const actionTypeSelect = document.getElementById('task-edit-action-type');
    const receiverInput = document.getElementById('task-edit-receiver');
    const contentInput = document.getElementById('task-edit-content');

    // Set title and subtitle
    titleEl.textContent = t('task_edit_title');
    subtitle.textContent = task.id;
    deleteBtn.classList.remove('hidden');

    // Show which Agent owns this task (read-only). Only meaningful with more
    // than one Agent; a solo install would just repeat the obvious.
    renderTaskOwnerChip(task);

    // Populate data
    nameInput.value = task.name || '';
    enabledInput.checked = task.enabled !== false;

    const schedule = task.schedule || {};
    scheduleTypeSelect.value = schedule.type || 'cron';

    // Clear all schedule type input values first to avoid stale data
    cronInput.value = '';
    intervalInput.value = '';
    onceInput.value = '';

    if (schedule.type === 'cron') {
        cronInput.value = schedule.expression || '';
    } else if (schedule.type === 'interval') {
        intervalInput.value = schedule.seconds || '';
    } else if (schedule.type === 'once') {
        if (schedule.run_at) {
            // Manually parse ISO time string to avoid cross-browser timezone issues with new Date()
            // run_at format: "YYYY-MM-DDTHH:mm:ss" or "YYYY-MM-DDTHH:mm:ss.ffffff"
            const parts = schedule.run_at.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/);
            if (parts) {
                const timeInput = document.getElementById('task-edit-once-time');
                timeInput.value = `${parts[1]}-${parts[2]}-${parts[3]}T${parts[4]}:${parts[5]}:${parts[6]}`;
            }
        }
    }

    const action = task.action || {};
    actionTypeSelect.value = action.type || 'send_message';
    receiverInput.value = action.receiver || '';
    contentInput.value = action.content || action.task_description || '';

    // Load channel options and set selected value
    loadTaskChannelOptions(action.channel_type || 'web');

    // Disable channel type selector — channel is read-only when editing.
    // Switching the channel after a task is created is problematic because:
    //   1. The WeChat (weixin/ilink) bot requires a valid context_token that is tied
    //      to a specific user-session on that channel. Changing the channel to weixin
    //      would invalidate the existing token — the new receiver on weixin may not
    //      have an active context_token, causing the scheduled push to silently fail.
    //   2. Other channels (DingTalk, Feishu, etc.) also carry channel-specific fields
    //      (e.g. dingtalk_sender_staff_id) that cannot be trivially re-populated for
    //      a different channel type without user intervention.
    //   3. The receiver identity itself is channel-bound — a weixin user-id means
    //      nothing on a Feishu channel, so changing the channel would orphan the task.
    // For these reasons, the channel type is intentionally frozen once a task exists.
    // Users who need a task on a different channel should create a new task through
    // the chat interface (by asking the bot) rather than editing an existing one.
    document.getElementById('task-edit-channel-type').disabled = true;

    // Update UI
    updateTaskScheduleFields();
    updateTaskActionLabel();

    overlay.classList.remove('hidden');
}

function closeTaskEditModal() {
    document.getElementById('task-edit-modal-overlay').classList.add('hidden');
    currentEditingTask = null;
}

function updateTaskScheduleFields() {
    const scheduleType = document.getElementById('task-edit-schedule-type').value;
    const cronWrap = document.getElementById('task-edit-cron-wrap');
    const intervalWrap = document.getElementById('task-edit-interval-wrap');
    const onceWrap = document.getElementById('task-edit-once-wrap');
    const cronHint = document.getElementById('task-edit-cron-hint');
    const intervalHint = document.getElementById('task-edit-interval-hint');
    
    cronWrap.classList.toggle('hidden', scheduleType !== 'cron');
    intervalWrap.classList.toggle('hidden', scheduleType !== 'interval');
    onceWrap.classList.toggle('hidden', scheduleType !== 'once');
    
    if (cronHint) cronHint.classList.toggle('hidden', scheduleType !== 'cron');
    if (intervalHint) intervalHint.classList.toggle('hidden', scheduleType !== 'interval');
}

function updateTaskActionLabel() {
    const actionType = document.getElementById('task-edit-action-type').value;
    const label = document.getElementById('task-edit-content-label');
    const content = document.getElementById('task-edit-content');
    
    if (actionType === 'send_message') {
        label.textContent = t('task_message_content');
        content.placeholder = t('task_message_content');
    } else {
        label.textContent = t('task_task_description');
        content.placeholder = t('task_task_description');
    }
}

function saveTaskEdit() {
    const nameInput = document.getElementById('task-edit-name');
    const enabledInput = document.getElementById('task-edit-enabled');
    const scheduleTypeSelect = document.getElementById('task-edit-schedule-type');
    const cronInput = document.getElementById('task-edit-cron-expression');
    const intervalInput = document.getElementById('task-edit-interval-seconds');
    const onceInput = document.getElementById('task-edit-once-time');
    const actionTypeSelect = document.getElementById('task-edit-action-type');
    const channelTypeSelect = document.getElementById('task-edit-channel-type');
    const receiverInput = document.getElementById('task-edit-receiver');
    const contentInput = document.getElementById('task-edit-content');
    const statusEl = document.getElementById('task-edit-modal-status');
    const saveBtn = document.getElementById('task-edit-modal-save');
    
    const name = nameInput.value.trim();
    if (!name) {
        statusEl.textContent = currentLang === 'zh' ? '请输入任务名称' : 'Please enter task name';
        statusEl.style.opacity = '1';
        setTimeout(() => { statusEl.style.opacity = '0'; }, 3000);
        return;
    }
    
    const scheduleType = scheduleTypeSelect.value;
    const schedule = { type: scheduleType };
    
    if (scheduleType === 'cron') {
        const expr = cronInput.value.trim();
        if (!expr) {
            statusEl.textContent = currentLang === 'zh' ? '请输入 Cron 表达式' : 'Please enter cron expression';
            statusEl.style.opacity = '1';
            setTimeout(() => { statusEl.style.opacity = '0'; }, 3000);
            return;
        }
        // Basic cron expression format validation: 5 or 6 fields
        const fields = expr.split(/\s+/);
        if (fields.length < 5 || fields.length > 6) {
            statusEl.textContent = currentLang === 'zh' ? 'Cron 表达式格式错误，应为 5 或 6 个字段（分 时 日 月 周）' : 'Invalid cron expression, expected 5 or 6 fields (min hour day month weekday)';
            statusEl.style.opacity = '1';
            setTimeout(() => { statusEl.style.opacity = '0'; }, 3000);
            return;
        }
        schedule.expression = expr;
        // Note: detailed cron expression validity is verified by the backend croniter library; frontend only does basic format validation
    } else if (scheduleType === 'interval') {
        const seconds = parseInt(intervalInput.value);
        if (!seconds || seconds < 60) {
            statusEl.textContent = currentLang === 'zh' ? '间隔秒数最小为 60 秒' : 'Interval must be at least 60 seconds';
            statusEl.style.opacity = '1';
            setTimeout(() => { statusEl.style.opacity = '0'; }, 3000);
            return;
        }
        schedule.seconds = seconds;
    } else if (scheduleType === 'once') {
        const time = onceInput.value;
        if (!time) {
            statusEl.textContent = currentLang === 'zh' ? '请选择执行时间' : 'Please select execution time';
            statusEl.style.opacity = '1';
            setTimeout(() => { statusEl.style.opacity = '0'; }, 3000);
            return;
        }
        // Validate execution time format
        const selectedTime = new Date(time);
        if (isNaN(selectedTime.getTime())) {
            statusEl.textContent = currentLang === 'zh' ? '执行时间格式错误' : 'Invalid execution time format';
            statusEl.style.opacity = '1';
            setTimeout(() => { statusEl.style.opacity = '0'; }, 3000);
            return;
        }
        // Validate that time is in the future for one-time tasks
        if (selectedTime <= new Date()) {
            statusEl.textContent = currentLang === 'zh' ? '执行时间必须在当前时间之后' : 'Execution time must be in the future';
            statusEl.style.opacity = '1';
            setTimeout(() => { statusEl.style.opacity = '0'; }, 3000);
            return;
        }
        // datetime-local value with step="1" is already in YYYY-MM-DDTHH:mm:ss format
        // Backend _parse_naive_local treats strings without timezone suffix as local time
        schedule.run_at = time;
    }
    
    const actionType = actionTypeSelect.value;
    const channelType = channelTypeSelect.value;
    const content = contentInput.value.trim();

    if (!content) {
        statusEl.textContent = currentLang === 'zh' ? '请输入内容' : 'Please enter content';
        statusEl.style.opacity = '1';
        setTimeout(() => { statusEl.style.opacity = '0'; }, 3000);
        return;
    }
    
    // Build action with only necessary fields to avoid stale data
    const action = {
        type: actionType,
        channel_type: channelType,
        receiver: '',
        receiver_name: '',
        is_group: false,
        notify_session_id: ''
    };
    
    if (actionType === 'send_message') {
        action.content = content;
    } else {
        action.task_description = content;
    }
    
    // Preserve the original receiver info (channel is read-only, so it never changes)
    if (currentEditingTask && currentEditingTask.action) {
        action.receiver = currentEditingTask.action.receiver || '';
        action.receiver_name = currentEditingTask.action.receiver_name || '';
        action.is_group = currentEditingTask.action.is_group || false;
        action.notify_session_id = currentEditingTask.action.notify_session_id || '';
        
        // Preserve channel-specific fields (e.g. DingTalk sender_staff_id)
        if (channelType === 'dingtalk' && currentEditingTask.action.dingtalk_sender_staff_id) {
            action.dingtalk_sender_staff_id = currentEditingTask.action.dingtalk_sender_staff_id;
        }
    }
    
    saveBtn.disabled = true;
    
    const payload = {
        task_id: currentEditingTask.id,
        agent_id: currentEditingTask.agent_id || '',
        name: name,
        enabled: enabledInput.checked,
        schedule: schedule,
        action: action
    };
    
    fetch('/api/scheduler/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    }).then(r => r.json()).then(res => {
        saveBtn.disabled = false;
        if (res.status === 'success') {
            closeTaskEditModal();
            tasksLoaded = false;
            loadTasksView();
        } else {
            statusEl.textContent = res.message || (currentLang === 'zh' ? '保存失败' : 'Save failed');
            statusEl.style.opacity = '1';
            setTimeout(() => { statusEl.style.opacity = '0'; }, 3000);
        }
    }).catch(() => {
        saveBtn.disabled = false;
        statusEl.textContent = currentLang === 'zh' ? '网络错误' : 'Network error';
        statusEl.style.opacity = '1';
        setTimeout(() => { statusEl.style.opacity = '0'; }, 3000);
    });
}

function deleteTask() {
    if (!currentEditingTask) return;
    
    const taskName = currentEditingTask.name || currentEditingTask.id || '未知任务';
    const taskId = currentEditingTask.id;  // Capture early to avoid closure race condition
    const taskAgentId = currentEditingTask.agent_id || '';  // route delete to the owner's store
    showConfirmDialog({
        title: t('task_delete_confirm_title'),
        message: (currentLang === 'zh' ? `确定要删除任务「${taskName}」吗？` : `Are you sure to delete task "${taskName}"?`),
        onConfirm: () => {
            fetch('/api/scheduler/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ task_id: taskId, agent_id: taskAgentId })
            }).then(r => r.json()).then(res => {
                if (res.status === 'success') {
                    closeTaskEditModal();
                    tasksLoaded = false;
                    loadTasksView();
                } else {
                    const statusEl = document.getElementById('task-edit-modal-status');
                    if (statusEl) {
                        statusEl.textContent = res.message || 'Delete failed';
                        statusEl.classList.remove('hidden', 'text-green-500');
                        statusEl.classList.add('text-red-500');
                        setTimeout(() => { statusEl.style.opacity = '0'; }, 3000);
                    }
                }
            }).catch(() => {
                const statusEl = document.getElementById('task-edit-modal-status');
                if (statusEl) {
                    statusEl.textContent = 'Network error';
                    statusEl.classList.remove('hidden', 'text-green-500');
                    statusEl.classList.add('text-red-500');
                    setTimeout(() => { statusEl.style.opacity = '0'; }, 3000);
                }
            });
        }
    });
}


document.getElementById('task-edit-schedule-type').addEventListener('change', updateTaskScheduleFields);
document.getElementById('task-edit-action-type').addEventListener('change', updateTaskActionLabel);
document.getElementById('task-edit-modal-cancel').addEventListener('click', closeTaskEditModal);
document.getElementById('task-edit-modal-save').addEventListener('click', saveTaskEdit);
document.getElementById('task-edit-modal-delete').addEventListener('click', deleteTask);
document.getElementById('task-edit-modal-overlay').addEventListener('click', function(e) {
    if (e.target === this) closeTaskEditModal();
});
