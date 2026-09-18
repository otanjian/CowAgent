/* =====================================================================
 * Console page 「外部系统接入」 (External System Access)
 *
 * Change add-external-system-access, task group 10. Loaded lazily by
 * console.js (`LAZY_VIEW_MODULES.external_connections`) the first time the
 * view is entered; chat.html does not reference this file.
 *
 * It is the production page for the control plane in
 * `channel/web/external_connection_handlers.py` /
 * `integrations/external/service.py`, and ports the structure, states and
 * interaction model of `docs/design/demos/external-system-access.html`.
 *
 * Two rules that shape the code below:
 *
 * 1. The server is the only authority. `test`/`execute` are not declared as
 *    capability classes yet, so the `types` projection reports them closed
 *    *with a reason*; the page renders that exact reason on a
 *    present-but-disabled control. It never simulates a test result (the
 *    prototype did, in memory — that simulation is deliberately not ported).
 * 2. Secrets live only in the open form. They are never placed in a URL, in
 *    localStorage/sessionStorage, in a draft that survives the drawer, or in
 *    telemetry; the request bodies that carry them are built at submit time
 *    from the live inputs.
 * ===================================================================== */
(function () {
    'use strict';

    if (typeof window === 'undefined') return;

    // =================================================================
    // i18n + escaping
    // =================================================================
    function ecT(key, vars) {
        var text = (typeof window.t === 'function') ? window.t(key) : key;
        if (text === undefined || text === null || text === '') text = key;
        if (vars) {
            Object.keys(vars).forEach(function (name) {
                text = text.split('{' + name + '}').join(String(vars[name]));
            });
        }
        return text;
    }

    function ecEsc(value) {
        return String(value === null || value === undefined ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    // =================================================================
    // Constants
    // =================================================================
    var KIND_ORDER = ['mcp', 'erp', 'oa', 'email'];
    var SCOPES = ['platform', 'tenant', 'personal'];

    var TYPE_LABEL_KEY = {
        mcp: 'ext_conn_type_mcp', erp: 'ext_conn_type_erp',
        oa: 'ext_conn_type_oa', email: 'ext_conn_type_email',
    };
    var TYPE_DESC_KEY = {
        mcp: 'ec_type_desc_mcp', erp: 'ec_type_desc_erp',
        oa: 'ec_type_desc_oa', email: 'ec_type_desc_email',
    };
    var TYPE_ICON = { mcp: 'fa-plug', erp: 'fa-server', oa: 'fa-building', email: 'fa-envelope' };

    var SCOPE_LABEL_KEY = {
        platform: 'ec_scope_platform', tenant: 'ec_scope_tenant', personal: 'ec_scope_personal',
    };
    var SOURCE_LABEL_KEY = {
        created: 'ec_source_created', override: 'ec_source_override',
        inherited: 'ec_source_inherited', overridden: 'ec_source_overridden',
        platform: 'ec_source_platform', personal: 'ec_source_personal',
    };
    var TEST_STATUS_KEY = {
        untested: 'ec_status_untested', ok: 'ec_status_ok', success: 'ec_status_ok',
        partial: 'ec_status_partial', failed: 'ec_status_failed', expired: 'ec_status_expired',
    };

    // Reason codes the server can report, mapped onto the sentence the page
    // shows. Any unknown code still renders *something* rather than nothing.
    var REASON_KEY = {
        awaiting_mcp_test_environment: 'ec_reason_awaiting_mcp_test_environment',
        awaiting_sap_test_environment: 'ec_reason_awaiting_sap_test_environment',
        awaiting_oa_test_environment: 'ec_reason_awaiting_oa_test_environment',
        awaiting_mail_test_environment: 'ec_reason_awaiting_mail_test_environment',
        not_supported_by_type: 'ec_reason_not_supported_by_type',
        withheld_pending_acceptance: 'ec_reason_withheld_pending_acceptance',
        adapter_not_installed: 'ec_reason_adapter_not_installed',
        secret_missing: 'ec_reason_secret_missing',
        not_configured: 'ec_reason_not_configured',
        platform_admin_required: 'ec_type_reason_platform_admin_required',
        management_required: 'ec_type_reason_management_required',
        membership_required: 'ec_type_reason_membership_required',
        singleton_exists: 'ec_type_reason_singleton_exists',
    };
    var SECRET_SLOT_KEY = {
        header: 'ec_secret_slot_header', env: 'ec_secret_slot_env', oauth: 'ec_secret_slot_oauth',
        password: 'ec_secret_slot_password', app_secret: 'ec_secret_slot_app_secret',
        imap_password: 'ec_secret_slot_imap_password', smtp_password: 'ec_secret_slot_smtp_password',
    };
    var FIELD_ERROR_KEY = {
        required: 'ec_validate_required', masked: 'ec_validate_secret_masked',
        secret_in_config: 'ec_validate_secret_masked', range: 'ec_validate_timeout',
        too_long: 'ec_validate_invalid', not_allowed: 'ec_validate_invalid',
        unknown: 'ec_validate_invalid', type: 'ec_validate_invalid', invalid: 'ec_validate_invalid',
    };
    var FIELD_ERROR_OVERRIDE = {
        url: 'ec_validate_url', base_url: 'ec_validate_url', imap_host: 'ec_validate_required',
        smtp_host: 'ec_validate_required', sysnr: 'ec_validate_sysnr',
        client: 'ec_validate_client_erp', timeout: 'ec_validate_timeout',
        env_keys: 'ec_validate_env_key', env: 'ec_validate_env_key',
        imap_user: 'ec_validate_email', smtp_user: 'ec_validate_email',
        smtp_from_addr: 'ec_validate_email', from_addr: 'ec_validate_email',
        imap_port: 'ec_validate_port', smtp_port: 'ec_validate_port', port: 'ec_validate_port',
        attachment_dirs: 'ec_validate_attachment_dirs',
    };

    // =================================================================
    // HTTP layer
    //
    // The server answers a successful write with `{"status":"success", ...}`
    // and a refusal through the project's error contract with the HTTP status
    // carrying the class (409 = version conflict or a live reference). A
    // transport failure is returned as `{transport:true}` and is never
    // retried automatically — the caller shows the "outcome unknown" state.
    // =================================================================
    function ecJsonHeaders(extra) {
        var headers = { 'Accept': 'application/json' };
        if (extra) Object.keys(extra).forEach(function (k) { headers[k] = extra[k]; });
        return headers;
    }

    function ecRequest(path, options) {
        var opts = options || {};
        var init = {
            method: opts.method || 'GET',
            credentials: 'same-origin',
            cache: 'no-store',
            headers: ecJsonHeaders(opts.headers),
        };
        if (opts.body !== undefined) {
            init.headers['Content-Type'] = 'application/json';
            init.body = JSON.stringify(opts.body);
        }
        // `Promise.resolve().then(...)` keeps a synchronous throw from `fetch`
        // (a blocked request, a shim without fetch) on the same "outcome
        // unknown" path as a rejected request instead of escaping the caller.
        return Promise.resolve().then(function () {
            return window.fetch(path, init);
        }).then(function (res) {
            return res.json().catch(function () { return null; }).then(function (json) {
                return { ok: res.ok, status: res.status, json: json };
            });
        }).catch(function () {
            return { ok: false, status: 0, json: null, transport: true };
        });
    }

    function ecPayloadOf(res) {
        var json = (res && res.json) || {};
        if (json && typeof json.data === 'object' && json.data !== null) return json.data;
        return json || {};
    }

    function ecOk(res) {
        if (!res || !res.ok) return false;
        var json = res.json;
        if (json && typeof json === 'object' && json.status === 'error') return false;
        return true;
    }

    function ecErrorOf(res) {
        var json = (res && res.json) || {};
        var err = (json && json.error) || json || {};
        return {
            status: (res && res.status) || 0,
            transport: !!(res && res.transport),
            code: err.code || (json && json.code) || '',
            message: err.message || (json && json.message) || '',
            fields: (err.fields) || (json && json.fields) || {},
            references: (json && json.references) || err.references || [],
        };
    }

    function ecPathFor(scope, suffix) {
        var base = '/api/external-connections/' + scope;
        return suffix ? base + '/' + encodeURIComponent(suffix) : base;
    }

    function ecNewIdempotencyKey() {
        try {
            if (window.crypto && typeof window.crypto.randomUUID === 'function') {
                return window.crypto.randomUUID();
            }
        } catch (_) { /* fall through */ }
        return 'ec-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12);
    }

    // =================================================================
    // Identity / epoch isolation
    //
    // Every async result is bound to the identity that started it. A late
    // response can therefore never write into a new tenant, a new account or
    // a re-opened drawer: it is dropped on arrival.
    // =================================================================
    function ecTenantId() {
        try { return sessionStorage.getItem('cow_tenant_id') || ''; } catch (_) { return ''; }
    }

    function ecIdentityMode() {
        try {
            if (typeof _identityMode === 'function') return String(_identityMode() || '');
        } catch (_) { /* not in the console shell (tests) */ }
        return '';
    }

    function ecAuthEpoch() {
        try {
            if (typeof _authEpoch === 'number') return _authEpoch;
        } catch (_) { /* not in the console shell (tests) */ }
        return 0;
    }

    function ecIdentityToken() {
        return [ecTenantId(), ecIdentityMode(), String(ecAuthEpoch())].join('|');
    }

    // =================================================================
    // State
    // =================================================================
    var ecState = {
        mounted: false,
        status: 'idle',          // idle | loading | ready | failed | no-permission
        epoch: 0,
        identity: '',
        types: [],
        cards: [],
        scopes: [],              // scopes whose read succeeded
        deniedScopes: [],        // scopes the caller is not allowed to read
        failedScopes: [],        // scopes whose read failed for another reason
        loadError: null,
        kindFilter: 'all',
        statusFilter: 'all',
        search: '',
        erpDefault: null,        // {connection_id, revision} for the current tenant
        erpDefaultError: false,
    };

    var ecDrawer = null;         // open form: {mode,kind,scope,id,version,draft,original,...}
    var ecModal = null;          // open modal: {node,dirty,onClose}
    var ecFocusReturn = null;    // element focus returns to when the drawer closes
    var ecRouteTarget = null;    // navigation target a discard confirmation will re-run
    var ecPeriodic = null;

    // =================================================================
    // Derived helpers
    // =================================================================
    // The server reports a reason *code*; the page shows the sentence. An
    // unknown code still renders something rather than an empty justification.
    function ecReasonText(reason) {
        if (!reason) return '';
        return ecT(REASON_KEY[reason] || 'ec_reason_not_configured');
    }

    function ecTypeByKind(kind) {
        for (var i = 0; i < ecState.types.length; i += 1) {
            if (ecState.types[i].kind === kind) return ecState.types[i];
        }
        return null;
    }

    function ecCapabilityFor(kind) {
        var type = ecTypeByKind(kind);
        return (type && type.capabilities) || null;
    }

    function ecScopeEntry(type, scope) {
        var list = (type && type.scopes) || [];
        for (var i = 0; i < list.length; i += 1) {
            if (list[i].scope === scope) return list[i];
        }
        return null;
    }

    function ecCanManageTenant() {
        return ecState.types.some(function (type) {
            return (type.scopes || []).some(function (s) {
                return s.scope === 'tenant' && s.available === true;
            });
        });
    }

    function ecPreferredScope(type) {
        var list = (type && type.scopes) || [];
        for (var i = 0; i < list.length; i += 1) {
            if (list[i].available) return list[i].scope;
        }
        return (list[0] || {}).scope || 'tenant';
    }

    function ecIsDirty() {
        return !!((ecDrawer && ecDrawer.open && ecDrawer.dirty)
            || (ecModal && ecModal.dirty));
    }

    function ecStillCurrent(epoch, identity) {
        return epoch === ecState.epoch && identity === ecIdentityToken();
    }

    // =================================================================
    // Loading
    // =================================================================
    function ecScopesToRead() {
        var tenant = ecTenantId();
        var out = [];
        var platformReadable = ecState.types.some(function (type) {
            var entry = ecScopeEntry(type, 'platform');
            return entry && entry.available === true;
        });
        if (platformReadable) out.push('platform');
        if (tenant) {
            // The tenant range is where overrides, ERP, OA and the inherited
            // platform templates live. A member without the permission will
            // get 403 here; that is exactly the "no-permission" evidence the
            // page must render instead of guessing from the type catalogue.
            out.push('tenant');
            var personalReadable = ecState.types.some(function (type) {
                var entry = ecScopeEntry(type, 'personal');
                return !!entry;
            });
            if (personalReadable) out.push('personal');
        }
        return out;
    }

    function loadExternalConnectionsView() {
        ecState.epoch += 1;
        var epoch = ecState.epoch;
        var identity = ecIdentityToken();
        ecState.identity = identity;
        ecState.status = 'loading';
        ecState.types = [];
        ecState.cards = [];
        ecState.scopes = [];
        ecState.deniedScopes = [];
        ecState.failedScopes = [];
        ecState.loadError = null;
        ecState.erpDefault = null;
        ecState.erpDefaultError = false;
        ecRender();

        return ecRequest('/api/external-connections/types').then(function (res) {
            if (!ecStillCurrent(epoch, identity)) return;
            if (!ecOk(res)) {
                ecState.status = 'failed';
                ecState.loadError = ecErrorOf(res);
                ecRender();
                return;
            }
            var payload = ecPayloadOf(res);
            ecState.types = payload.types || payload.items || [];
            var scopes = ecScopesToRead();
            if (!scopes.length) {
                ecState.status = 'no-permission';
                ecRender();
                return;
            }
            var reads = scopes.map(function (scope) {
                var url = '/api/external-connections/catalog?scope=' + encodeURIComponent(scope);
                return ecRequest(url).then(function (cat) {
                    return { scope: scope, res: cat };
                });
            });
            return Promise.all(reads).then(function (results) {
                if (!ecStillCurrent(epoch, identity)) return;
                results.forEach(function (item) {
                    if (ecOk(item.res)) {
                        var payload = ecPayloadOf(item.res);
                        var items = payload.items || [];
                        items.forEach(function (card) { ecState.cards.push(card); });
                        ecState.scopes.push(item.scope);
                    } else {
                        var error = ecErrorOf(item.res);
                        if (error.status === 403 || error.status === 401) {
                            ecState.deniedScopes.push(item.scope);
                        } else {
                            ecState.failedScopes.push({ scope: item.scope, error: error });
                        }
                    }
                });
                if (ecState.cards.length) ecState.status = 'ready';
                else if (ecState.failedScopes.length && !ecState.deniedScopes.length) {
                    ecState.status = 'failed';
                    ecState.loadError = ecState.failedScopes[0].error;
                } else if (ecState.deniedScopes.length && !ecState.scopes.length) {
                    ecState.status = 'no-permission';
                } else {
                    ecState.status = 'ready';
                }
                if (ecState.scopes.indexOf('tenant') >= 0) ecLoadErpDefault(epoch, identity);
                ecRender();
            });
        });
    }

    function ecLoadErpDefault(epoch, identity) {
        return ecRequest(ecPathFor('tenant', 'erp-default')).then(function (res) {
            if (!ecStillCurrent(epoch, identity)) return;
            if (ecOk(res)) {
                ecState.erpDefault = ecPayloadOf(res);
                ecState.erpDefaultError = false;
            } else {
                ecState.erpDefault = null;
                ecState.erpDefaultError = true;
            }
            ecRender();
        });
    }

    // =================================================================
    // Filtering
    // =================================================================
    function ecVisibleCards() {
        var query = String(ecState.search || '').trim().toLowerCase();
        return ecState.cards.filter(function (card) {
            if (ecState.kindFilter !== 'all' && card.kind !== ecState.kindFilter) return false;
            if (ecState.statusFilter === 'enabled' && !card.enabled) return false;
            if (ecState.statusFilter === 'disabled' && card.enabled) return false;
            if (query && String(card.name || '').toLowerCase().indexOf(query) < 0) return false;
            return true;
        });
    }

    function ecIsDefault(card) {
        return !!(ecState.erpDefault && ecState.erpDefault.connection_id
            && ecState.erpDefault.connection_id === card.id);
    }

    function ecCardFlags(card) {
        var actions = card.actions || [];
        var canManage = actions.indexOf('manage') >= 0;
        var inherited = card.source === 'inherited';
        var overridden = card.source === 'overridden';
        return {
            actions: actions,
            canManage: canManage,
            readOnly: !canManage,
            inherited: inherited,
            overridden: overridden,
            // A tenant administrator can override an inherited platform MCP even
            // though the inherited card itself is read-only to them.
            canOverride: card.kind === 'mcp' && card.scope === 'platform' && inherited
                && ecCanManageTenant(),
            isDefault: ecIsDefault(card),
            canDelete: canManage && !inherited,
        };
    }

    // =================================================================
    // Rendering: page
    // =================================================================
    function ecRoot() {
        return document.getElementById('ec-root');
    }

    function ecRender() {
        var root = ecRoot();
        if (!root) return;
        root.innerHTML = ecPageHtml();
        var container = document.getElementById('view-external_connections');
        if (container) {
            var search = container.querySelector('[data-ec-action="search"]');
            if (search && ecState.search) search.value = ecState.search;
        }
        if (ecIsDirty() && (!ecDrawer || !ecDrawer.open)) {
            // A modal (tenant access) owns the draft; nothing else to sync here.
        }
    }

    function ecPageHtml() {
        if (ecState.status === 'loading' || ecState.status === 'idle') {
            return ecHeaderHtml() + ecPanelHtml('ec-loading', 'ec_loading');
        }
        if (ecState.status === 'failed') return ecHeaderHtml() + ecLoadFailedHtml();
        if (ecState.status === 'no-permission') return ecHeaderHtml() + ecNoPermissionHtml();
        var visible = ecVisibleCards();
        return ecHeaderHtml()
            + ecFilterBarHtml()
            + ecBannersHtml()
            + ecSummaryHtml(visible)
            + ecCardsHtml(visible);
    }

    function ecHeaderHtml() {
        var creatable = ecState.types.some(function (type) {
            return (type.scopes || []).some(function (s) { return s.available === true; });
        });
        return ''
            + '<div class="ec-header">'
            + '  <div class="ec-header-text">'
            + '    <h1 class="ec-title">' + ecEsc(ecT('ec_title')) + '</h1>'
            + '    <p class="ec-desc">' + ecEsc(ecT('ec_desc')) + '</p>'
            + '  </div>'
            + '  <div class="ec-header-actions">'
            + '    <button type="button" class="ec-btn ec-btn-ghost" data-ec-action="refresh">'
            + '      <i class="fas fa-rotate" aria-hidden="true"></i>'
            + '      <span>' + ecEsc(ecT('ec_refresh')) + '</span>'
            + '    </button>'
            + '    <button type="button" class="ec-btn ec-btn-primary" data-ec-action="add"'
            + (creatable ? '' : ' disabled') + '>'
            + '      <i class="fas fa-plus" aria-hidden="true"></i>'
            + '      <span>' + ecEsc(ecT('ec_add')) + '</span>'
            + '    </button>'
            + '  </div>'
            + '</div>';
    }

    function ecPanelHtml(icon, messageKey, extraHtml) {
        return ''
            + '<div class="ec-panel ec-panel-loading" role="status" aria-live="polite">'
            + '  <i class="fas ' + icon + ' ec-panel-icon" aria-hidden="true"></i>'
            + '  <p class="ec-panel-text">' + ecEsc(ecT(messageKey)) + '</p>'
            + (extraHtml || '')
            + '</div>';
    }

    function ecLoadFailedHtml() {
        return ''
            + '<div class="ec-panel ec-panel-error" role="alert">'
            + '  <i class="fas fa-triangle-exclamation ec-panel-icon" aria-hidden="true"></i>'
            + '  <h2 class="ec-panel-title">' + ecEsc(ecT('ec_load_failed_title')) + '</h2>'
            + '  <p class="ec-panel-text">' + ecEsc(ecT('ec_load_failed_desc')) + '</p>'
            + '  <button type="button" class="ec-btn ec-btn-primary" data-ec-action="retry">'
            + ecEsc(ecT('ec_retry')) + '</button>'
            + '</div>';
    }

    function ecNoPermissionHtml() {
        return ''
            + '<div class="ec-panel" role="status">'
            + '  <i class="fas fa-lock ec-panel-icon" aria-hidden="true"></i>'
            + '  <h2 class="ec-panel-title">' + ecEsc(ecT('ec_no_permission_title')) + '</h2>'
            + '  <p class="ec-panel-text">' + ecEsc(ecT('ec_no_permission_desc')) + '</p>'
            + '</div>';
    }

    function ecBannersHtml() {
        var html = '';
        if (ecState.deniedScopes.length) {
            html += '<p class="ec-banner ec-banner-warn" role="status">'
                + ecEsc(ecT('ec_partial_denied')) + '</p>';
        }
        if (ecState.failedScopes.length) {
            html += '<p class="ec-banner ec-banner-error" role="alert">'
                + ecEsc(ecT('ec_load_failed_desc')) + '</p>';
        }
        return html;
    }

    function ecFilterBarHtml() {
        var chips = ['all'].concat(KIND_ORDER.filter(function (kind) {
            return ecState.cards.some(function (card) { return card.kind === kind; });
        }));
        var chipsHtml = chips.map(function (kind) {
            var active = ecState.kindFilter === kind;
            var label = kind === 'all' ? ecT('ec_tab_all') : ecT(TYPE_LABEL_KEY[kind]);
            return '<button type="button" role="tab" class="ec-chip' + (active ? ' ec-chip-active' : '')
                + '" data-ec-action="kind-filter" data-kind="' + ecEsc(kind) + '"'
                + ' aria-selected="' + (active ? 'true' : 'false') + '">'
                + ecEsc(label) + '</button>';
        }).join('');
        var statusOptions = [
            { value: 'all', label: ecT('ec_status_all') },
            { value: 'enabled', label: ecT('ec_status_enabled') },
            { value: 'disabled', label: ecT('ec_status_disabled') },
        ].map(function (option) {
            return '<option value="' + ecEsc(option.value) + '"'
                + (ecState.statusFilter === option.value ? ' selected' : '') + '>'
                + ecEsc(option.label) + '</option>';
        }).join('');
        return ''
            + '<div class="ec-toolbar">'
            + '  <div class="ec-chips" role="tablist" aria-label="' + ecEsc(ecT('ec_field_name')) + '">'
            + chipsHtml
            + '  </div>'
            + '  <div class="ec-toolbar-fields">'
            + '    <label class="ec-sr-only" for="ec-search">' + ecEsc(ecT('ec_search_placeholder')) + '</label>'
            + '    <input id="ec-search" type="search" class="ec-input ec-search" data-ec-action="search"'
            + '      placeholder="' + ecEsc(ecT('ec_search_placeholder')) + '"'
            + '      value="' + ecEsc(ecState.search) + '">'
            + '    <label class="ec-sr-only" for="ec-status-filter">' + ecEsc(ecT('ec_status_all')) + '</label>'
            + '    <select id="ec-status-filter" class="ec-input ec-select" data-ec-action="status-filter">'
            + statusOptions
            + '    </select>'
            + '  </div>'
            + '</div>';
    }

    function ecSummaryHtml(visible) {
        var enabled = visible.filter(function (card) { return card.enabled; }).length;
        var disabled = visible.length - enabled;
        return ''
            + '<p class="ec-summary" role="status">'
            + '<span>' + ecEsc(ecT('ec_summary_total', { n: visible.length })) + '</span>'
            + '<span>' + ecEsc(ecT('ec_summary_enabled', { n: enabled })) + '</span>'
            + '<span>' + ecEsc(ecT('ec_summary_disabled', { n: disabled })) + '</span>'
            + '</p>';
    }

    function ecCardsHtml(visible) {
        if (!visible.length) return ecEmptyHtml();
        return '<div class="ec-grid">' + visible.map(ecCardHtml).join('') + '</div>';
    }

    function ecEmptyHtml() {
        if (ecState.cards.length) {
            return ''
                + '<div class="ec-panel">'
                + '  <i class="fas fa-magnifying-glass ec-panel-icon" aria-hidden="true"></i>'
                + '  <h2 class="ec-panel-title">' + ecEsc(ecT('ec_empty_filtered_title')) + '</h2>'
                + '  <p class="ec-panel-text">' + ecEsc(ecT('ec_empty_filtered_desc')) + '</p>'
                + '  <button type="button" class="ec-btn ec-btn-ghost" data-ec-action="clear-filters">'
                + ecEsc(ecT('ec_clear_filters')) + '</button>'
                + '</div>';
        }
        return ''
            + '<div class="ec-panel">'
            + '  <i class="fas fa-plug ec-panel-icon" aria-hidden="true"></i>'
            + '  <h2 class="ec-panel-title">' + ecEsc(ecT('ec_empty_no_conn_title')) + '</h2>'
            + '  <p class="ec-panel-text">' + ecEsc(ecT('ec_empty_no_conn_desc')) + '</p>'
            + '  <button type="button" class="ec-btn ec-btn-primary" data-ec-action="add">'
            + ecEsc(ecT('ec_add')) + '</button>'
            + '</div>';
    }

    // The card's test control is the honesty constraint in one place: when the
    // server reports `test_available:false` the button exists but is disabled,
    // and its accessible description is the server's own reason (never a
    // simulated success, never a button that always fails).
    function ecTestStateHtml(card) {
        var cap = ecCapabilityFor(card.kind) || {};
        var reason = cap.unavailable_reason || '';
        var missing = cap.missing_secret_slots || [];
        var status = TEST_STATUS_KEY[card.test_status] ? card.test_status : 'untested';
        var bits = [];
        if (status === 'untested' || !card.tested_at) bits.push(ecEsc(ecT('ec_never_tested')));
        else bits.push(ecEsc(ecT('ec_tested_at', { time: ecFormatTime(card.tested_at) })));
        if (missing.length) {
            bits.push(ecEsc(ecT('ec_missing_secrets', { slots: ecSlotList(missing) })));
        }
        var describeId = 'ec-test-reason-' + ecEsc(card.id);
        var html = '<div class="ec-test-state">'
            + '<span class="ec-test-label">' + ecEsc(ecT(TEST_STATUS_KEY[status])) + '</span>'
            + '<span class="ec-test-detail">' + bits.join(' · ') + '</span>';
        if (cap.test_available === true) {
            html += '<button type="button" class="ec-btn ec-btn-ghost ec-btn-small"'
                + ' data-ec-action="test" data-id="' + ecEsc(card.id) + '"'
                + ' data-scope="' + ecEsc(card.scope) + '">' + ecEsc(ecT('ec_test')) + '</button>';
        } else {
            html += '<button type="button" class="ec-btn ec-btn-ghost ec-btn-small" disabled'
                + ' aria-describedby="' + describeId + '">' + ecEsc(ecT('ec_test')) + '</button>'
                + '<span id="' + describeId + '" class="ec-test-reason">'
                + ecEsc(ecT('ec_test_disabled_hint', { reason: ecReasonText(reason) }))
                + '</span>';
        }
        return html + '</div>';
    }

    function ecFormatTime(value) {
        if (!value) return ecT('ec_never_tested');
        try {
            var date = new Date(typeof value === 'number' ? value * 1000 : value);
            if (isNaN(date.getTime())) return String(value);
            return date.toLocaleString();
        } catch (_) { return String(value); }
    }

    function ecSlotList(slots) {
        return (slots || []).map(function (slot) {
            return ecT(SECRET_SLOT_KEY[slot] || slot);
        }).join('、');
    }

    function ecCardHtml(card) {
        var flags = ecCardFlags(card);
        var label = ecT(TYPE_LABEL_KEY[card.kind] || card.kind);
        var scope = ecT(SCOPE_LABEL_KEY[card.scope] || card.scope);
        var sourceKey = SOURCE_LABEL_KEY[card.source];
        var source = sourceKey ? ecT(sourceKey) : String(card.source || '');
        var effective = (card.effective_id && card.effective_id !== card.id)
            ? '<div class="ec-meta-row"><dt>' + ecEsc(ecT('ec_details_effective')) + '</dt><dd>'
              + ecEsc(String(card.effective_id)) + '</dd></div>'
            : '';
        var actions = [];
        if (flags.canManage) {
            actions.push('<button type="button" class="ec-btn ec-btn-primary ec-btn-small"'
                + ' data-ec-action="open" data-mode="edit">' + ecEsc(ecT('ec_action_configure')) + '</button>');
            actions.push('<button type="button" class="ec-btn ec-btn-ghost ec-btn-small"'
                + ' data-ec-action="toggle">'
                + ecEsc(card.enabled ? ecT('ec_toggle_disable') : ecT('ec_toggle_enable')) + '</button>');
        } else {
            actions.push('<button type="button" class="ec-btn ec-btn-ghost ec-btn-small"'
                + ' data-ec-action="open" data-mode="view">' + ecEsc(ecT('ec_action_view')) + '</button>');
        }
        if (flags.canOverride) {
            actions.push('<button type="button" class="ec-btn ec-btn-ghost ec-btn-small"'
                + ' data-ec-action="override">' + ecEsc(ecT('ec_action_override')) + '</button>');
        }
        if (card.kind === 'erp' && card.scope === 'tenant' && flags.canManage) {
            if (flags.isDefault) {
                actions.push('<button type="button" class="ec-btn ec-btn-ghost ec-btn-small"'
                    + ' data-ec-action="clear-default">' + ecEsc(ecT('ec_action_clear_default')) + '</button>');
            } else if (card.enabled) {
                actions.push('<button type="button" class="ec-btn ec-btn-ghost ec-btn-small"'
                    + ' data-ec-action="set-default">' + ecEsc(ecT('ec_action_set_default')) + '</button>');
            }
        }
        if (card.scope === 'tenant' && card.base_connection_id && flags.canManage) {
            actions.push('<button type="button" class="ec-btn ec-btn-ghost ec-btn-small"'
                + ' data-ec-action="restore">' + ecEsc(ecT('ec_action_restore')) + '</button>');
        }
        if (card.kind === 'mcp' && card.scope === 'platform' && flags.canManage) {
            actions.push('<button type="button" class="ec-btn ec-btn-ghost ec-btn-small"'
                + ' data-ec-action="tenant-access">' + ecEsc(ecT('ec_action_tenant_access')) + '</button>');
        }
        if (flags.canDelete) {
            actions.push('<button type="button" class="ec-btn ec-btn-danger ec-btn-small"'
                + ' data-ec-action="delete">' + ecEsc(ecT('ec_action_delete')) + '</button>');
        }
        return ''
            + '<article class="ec-card" data-ec-card data-id="' + ecEsc(card.id) + '"'
            + ' data-scope="' + ecEsc(card.scope) + '" data-kind="' + ecEsc(card.kind) + '"'
            + ' data-version="' + ecEsc(card.version) + '">'
            + '  <div class="ec-card-head">'
            + '    <span class="ec-card-icon ec-icon-' + ecEsc(card.kind) + '">'
            + '      <i class="fas ' + ecEsc(TYPE_ICON[card.kind] || 'fa-plug') + '" aria-hidden="true"></i>'
            + '    </span>'
            + '    <div class="ec-card-title">'
            + '      <h3 class="ec-card-name">' + ecEsc(card.name) + '</h3>'
            + '      <p class="ec-card-sub">' + ecEsc(label) + ' · ' + ecEsc(scope) + '</p>'
            + '    </div>'
            + '    <div class="ec-card-badges">'
            + (flags.isDefault ? '<span class="ec-badge ec-badge-default">'
                + ecEsc(ecT('ec_default_badge')) + '</span>' : '')
            + '<span class="ec-badge ' + (card.enabled ? 'ec-badge-on' : 'ec-badge-off') + '">'
            + ecEsc(card.enabled ? ecT('ec_enabled') : ecT('ec_disabled')) + '</span>'
            + '    </div>'
            + '  </div>'
            + '  <dl class="ec-card-meta">'
            + '    <div class="ec-meta-row"><dt>' + ecEsc(ecT('ec_source_label')) + '</dt><dd>'
            + ecEsc(source) + '</dd></div>'
            + effective
            + '  </dl>'
            + ecTestStateHtml(card)
            + '  <div class="ec-card-actions">' + actions.join('') + '</div>'
            + '</article>';
    }

    // =================================================================
    // Focus helpers
    // =================================================================
    function ecFocusable(node) {
        if (!node || !node.querySelectorAll) return [];
        var nodes = node.querySelectorAll(
            'a[href], button:not([disabled]), textarea:not([disabled]),'
            + ' input:not([disabled]):not([type="hidden"]), select:not([disabled]),'
            + ' [tabindex]:not([tabindex="-1"])');
        return Array.prototype.slice.call(nodes).filter(function (el) {
            return !el.hidden && el.offsetParent !== null;
        });
    }

    function ecFocusFirst(node) {
        var list = ecFocusable(node);
        if (list.length) { try { list[0].focus(); } catch (_) {} }
    }

    function ecRestoreFocus() {
        if (ecFocusReturn && typeof ecFocusReturn.focus === 'function') {
            try { ecFocusReturn.focus(); } catch (_) { /* detached */ }
        }
        ecFocusReturn = null;
    }

    function ecTrapFocus(event, node) {
        if (event.key !== 'Tab' || !node) return;
        var list = ecFocusable(node);
        if (!list.length) return;
        var first = list[0];
        var last = list[list.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }

    // =================================================================
    // Draggable-free drawer (form) — markup appended once, content per open
    // =================================================================
    function ecEnsureDrawerNode() {
        var node = document.getElementById('ec-drawer');
        if (node) return node;
        node = document.createElement('div');
        node.id = 'ec-drawer';
        node.className = 'ec-drawer';
        node.setAttribute('role', 'dialog');
        node.setAttribute('aria-modal', 'true');
        node.hidden = true;
        node.innerHTML = '<div class="ec-drawer-backdrop" data-ec-action="drawer-backdrop"></div>'
            + '<div class="ec-drawer-panel" tabindex="-1">'
            + '  <div class="ec-drawer-head" id="ec-drawer-head"></div>'
            + '  <div class="ec-drawer-body" id="ec-drawer-body"></div>'
            + '  <div class="ec-drawer-foot" id="ec-drawer-foot"></div>'
            + '</div>';
        document.body.appendChild(node);
        return node;
    }

    function ecEnsureModalNode() {
        var node = document.getElementById('ec-modal');
        if (node) return node;
        node = document.createElement('div');
        node.id = 'ec-modal';
        node.className = 'ec-modal';
        node.hidden = true;
        node.setAttribute('role', 'dialog');
        node.setAttribute('aria-modal', 'true');
        node.innerHTML = '<div class="ec-modal-backdrop" data-ec-action="modal-backdrop"></div>'
            + '<div class="ec-modal-panel" tabindex="-1" id="ec-modal-panel"></div>';
        document.body.appendChild(node);
        return node;
    }

    // =================================================================
    // Field specs and form markup
    // =================================================================
    function ecInputHtml(opts) {
        var name = opts.name;
        var required = opts.required ? ' <span class="ec-required" aria-hidden="true">*</span>' : '';
        var hint = opts.hint ? '<p class="ec-hint">' + ecEsc(opts.hint) + '</p>' : '';
        var type = opts.type || 'text';
        var extra = '';
        if (opts.min !== undefined) extra += ' min="' + ecEsc(opts.min) + '"';
        if (opts.max !== undefined) extra += ' max="' + ecEsc(opts.max) + '"';
        if (opts.readonly) extra += ' readonly aria-readonly="true"';
        return ''
            + '<div class="ec-field' + (opts.full ? ' ec-field-full' : '') + '" data-ec-field-wrap="' + ecEsc(name) + '">'
            + '  <label class="ec-label" for="ec-f-' + ecEsc(name) + '">' + ecEsc(opts.label) + required + '</label>'
            + '  <input id="ec-f-' + ecEsc(name) + '" class="ec-input" type="' + ecEsc(type) + '"'
            + (opts.autocomplete ? ' autocomplete="' + ecEsc(opts.autocomplete) + '"' : '')
            + ' data-ec-field="' + ecEsc(name) + '"' + extra
            + (opts.required ? ' aria-required="true"' : '')
            + ' value="' + ecEsc(opts.value === undefined || opts.value === null ? '' : opts.value) + '">'
            + hint
            + '  <p class="ec-field-error" data-ec-error-for="' + ecEsc(name) + '" hidden></p>'
            + '</div>';
    }

    function ecTextareaHtml(opts) {
        var value = opts.value;
        if (Array.isArray(value)) value = value.join('\n');
        return ''
            + '<div class="ec-field' + (opts.full ? ' ec-field-full' : '') + '" data-ec-field-wrap="' + ecEsc(opts.name) + '">'
            + '  <label class="ec-label" for="ec-f-' + ecEsc(opts.name) + '">' + ecEsc(opts.label) + '</label>'
            + '  <textarea id="ec-f-' + ecEsc(opts.name) + '" class="ec-input ec-textarea" rows="' + ecEsc(opts.rows || 3) + '"'
            + ' data-ec-field="' + ecEsc(opts.name) + '">' + ecEsc(value === undefined || value === null ? '' : value) + '</textarea>'
            + (opts.hint ? '<p class="ec-hint">' + ecEsc(opts.hint) + '</p>' : '')
            + '  <p class="ec-field-error" data-ec-error-for="' + ecEsc(opts.name) + '" hidden></p>'
            + '</div>';
    }

    function ecSelectHtml(opts) {
        var options = (opts.options || []).map(function (option) {
            return '<option value="' + ecEsc(option.value) + '"'
                + (String(opts.value) === String(option.value) ? ' selected' : '') + '>'
                + ecEsc(option.label) + '</option>';
        }).join('');
        return ''
            + '<div class="ec-field' + (opts.full ? ' ec-field-full' : '') + '" data-ec-field-wrap="' + ecEsc(opts.name) + '">'
            + '  <label class="ec-label" for="ec-f-' + ecEsc(opts.name) + '">' + ecEsc(opts.label) + '</label>'
            + '  <select id="ec-f-' + ecEsc(opts.name) + '" class="ec-input ec-select"'
            + ' data-ec-field="' + ecEsc(opts.name) + '"'
            + (opts.toggle ? ' data-ec-toggle="' + ecEsc(opts.toggle) + '"' : '') + '>'
            + options + '  </select>'
            + (opts.hint ? '<p class="ec-hint">' + ecEsc(opts.hint) + '</p>' : '')
            + '  <p class="ec-field-error" data-ec-error-for="' + ecEsc(opts.name) + '" hidden></p>'
            + '</div>';
    }

    function ecCheckboxHtml(opts) {
        return ''
            + '<label class="ec-check">'
            + '  <input type="checkbox" data-ec-field="' + ecEsc(opts.name) + '"'
            + (opts.checked ? ' checked' : '')
            + (opts.toggle ? ' data-ec-toggle="' + ecEsc(opts.toggle) + '"' : '') + '>'
            + '  <span>' + ecEsc(opts.label) + '</span>'
            + '</label>';
    }

    function ecSecretFieldHtml(opts) {
        var configured = !!opts.configured;
        var hintKey = configured ? 'ec_secret_keep_hint' : 'ec_secret_replace_hint';
        var clear = configured
            ? '<label class="ec-check ec-check-clear">'
              + '<input type="checkbox" data-ec-clear="' + ecEsc(opts.slot) + '">'
              + '<span>' + ecEsc(ecT('ec_secret_clear')) + '</span></label>'
            : '';
        return ''
            + '<div class="ec-field ec-field-full" data-ec-field-wrap="secret:' + ecEsc(opts.slot) + '">'
            + '  <label class="ec-label" for="ec-f-secret-' + ecEsc(opts.slot) + '">'
            + ecEsc(opts.label)
            + (configured ? ' <span class="ec-badge ec-badge-on">' + ecEsc(ecT('ec_secret_configured')) + '</span>' : '')
            + '  </label>'
            + '  <input id="ec-f-secret-' + ecEsc(opts.slot) + '" class="ec-input" type="password"'
            + ' autocomplete="new-password" data-ec-secret="' + ecEsc(opts.slot) + '" value="">'
            + '  <p class="ec-hint">' + ecEsc(ecT(hintKey) + ' ' + ecT('ec_secret_never_persisted')) + '</p>'
            + clear
            + '  <p class="ec-field-error" data-ec-error-for="secret:' + ecEsc(opts.slot) + '" hidden></p>'
            + '</div>';
    }

    function ecSectionHtml(titleKey, body, extraClass) {
        return '<fieldset class="ec-section' + (extraClass ? ' ' + extraClass : '') + '">'
            + '<legend class="ec-section-title">' + ecEsc(ecT(titleKey)) + '</legend>'
            + '<div class="ec-form-grid">' + body + '</div>'
            + '</fieldset>';
    }

    // -- per-kind form bodies -----------------------------------------
    function ecMcpFormHtml(draft, secrets, options) {
        var isStdio = draft.transport === 'stdio';
        var isHeader = draft.auth === 'header';
        var isOauth = draft.auth === 'oauth';
        var remote = ''
            + ecInputHtml({ name: 'url', label: ecT('ec_field_url'), value: draft.url || '',
                required: true, full: true, hint: 'https://example.com/mcp' })
            + ecSelectHtml({ name: 'auth', label: ecT('ec_field_auth'), value: draft.auth || 'none',
                toggle: 'mcp-auth', options: [
                    { value: 'none', label: ecT('ec_auth_none') },
                    { value: 'header', label: ecT('ec_auth_header') },
                    { value: 'oauth', label: ecT('ec_auth_oauth') },
                ] });
        var headerGroup = '<div class="ec-subgroup" data-ec-group="mcp-auth-header"'
            + (isHeader ? '' : ' hidden') + '>'
            + ecInputHtml({ name: 'header_name', label: ecT('ec_field_header_name'),
                value: draft.header_name || '', required: isHeader })
            + '</div>';
        var oauthGroup = '<div class="ec-subgroup" data-ec-group="mcp-auth-oauth"'
            + (isOauth ? '' : ' hidden') + '>'
            + ecInputHtml({ name: 'oauth_provider', label: ecT('ec_field_oauth_provider'),
                value: draft.oauth_provider || '' })
            + '</div>';
        var stdio = ''
            + ecInputHtml({ name: 'command', label: ecT('ec_field_command'), value: draft.command || '',
                required: true, full: true })
            + ecTextareaHtml({ name: 'args', label: ecT('ec_field_args'), value: draft.args || '',
                full: true, hint: ecT('ec_field_args') + '（每行一个）' })
            + ecTextareaHtml({ name: 'env_keys', label: ecT('ec_field_env_keys'), value: draft.env_keys || '',
                full: true, hint: '^[A-Za-z_][A-Za-z0-9_]*$' });
        var remoteGroup = '<div class="ec-subgroup ec-subgroup-full" data-ec-group="mcp-remote"'
            + (isStdio ? ' hidden' : '') + '>' + remote + headerGroup + oauthGroup + '</div>';
        var stdioGroup = '<div class="ec-subgroup ec-subgroup-full" data-ec-group="mcp-stdio"'
            + (isStdio ? '' : ' hidden') + '>' + stdio + '</div>';

        // Every possible secret field is rendered, inside the same group that
        // shows the configuration using it, so switching transport/auth reveals
        // it without rebuilding the form (a rebuild would drop what the user
        // already typed in the other fields).
        var secretFields = ''
            + '<div class="ec-subgroup ec-subgroup-full" data-ec-group="mcp-auth-header"'
            + (isHeader ? '' : ' hidden') + '>'
            + ecSecretFieldHtml({ slot: 'header', label: ecT('ec_field_secret_header'),
                configured: secrets.header }) + '</div>'
            + '<div class="ec-subgroup ec-subgroup-full" data-ec-group="mcp-auth-oauth"'
            + (isOauth ? '' : ' hidden') + '>'
            + ecSecretFieldHtml({ slot: 'oauth', label: ecT('ec_field_secret_oauth'),
                configured: secrets.oauth }) + '</div>'
            + '<div class="ec-subgroup ec-subgroup-full" data-ec-group="mcp-stdio"'
            + (isStdio ? '' : ' hidden') + '>'
            + ecSecretFieldHtml({ slot: 'env', label: ecT('ec_field_secret_env'),
                configured: secrets.env }) + '</div>';
        return ecSectionHtml('ec_section_connection',
                ecSelectHtml({ name: 'transport', label: ecT('ec_field_transport'),
                    value: draft.transport || 'streamable_http', toggle: 'mcp-transport', options: [
                        { value: 'streamable_http', label: ecT('ec_transport_streamable_http') },
                        { value: 'sse', label: ecT('ec_transport_sse') },
                        { value: 'stdio', label: ecT('ec_transport_stdio') },
                    ] })
                + remoteGroup + stdioGroup)
            + ecSectionHtml('ec_section_auth', secretFields);
    }

    function ecErpFormHtml(draft, secrets) {
        var isRfc = (draft.provider || 'rfc') === 'rfc';
        var group = ecSelectHtml({ name: 'provider', label: ecT('ec_field_provider'),
            value: draft.provider || 'rfc', toggle: 'erp-provider', options: [
                { value: 'rfc', label: ecT('ec_provider_rfc') },
                { value: 'adt_sql', label: ecT('ec_provider_adt_sql') },
            ] });
        var rfc = '<div class="ec-subgroup ec-subgroup-full" data-ec-group="erp-rfc"'
            + (isRfc ? '' : ' hidden') + '>'
            + ecInputHtml({ name: 'ashost', label: ecT('ec_field_ashost'), value: draft.ashost || '', required: isRfc })
            + ecInputHtml({ name: 'sysnr', label: ecT('ec_field_sysnr'), value: draft.sysnr || '', required: isRfc })
            + ecInputHtml({ name: 'client', label: ecT('ec_field_client'), value: draft.client || '', required: true })
            + ecInputHtml({ name: 'user', label: ecT('ec_field_user'), value: draft.user || '', required: true })
            + ecInputHtml({ name: 'lang', label: ecT('ec_field_lang'), value: draft.lang || 'EN' })
            + '</div>';
        var adt = '<div class="ec-subgroup ec-subgroup-full" data-ec-group="erp-adt"'
            + (isRfc ? ' hidden' : '') + '>'
            + ecInputHtml({ name: 'base_url', label: ecT('ec_field_base_url'), value: draft.base_url || '',
                required: !isRfc, full: true })
            + ecInputHtml({ name: 'client', label: ecT('ec_field_client'), value: draft.client || '', required: true })
            + ecInputHtml({ name: 'user', label: ecT('ec_field_user'), value: draft.user || '', required: true })
            + ecCheckboxHtml({ name: 'verify_ssl', label: ecT('ec_field_verify_ssl'),
                checked: draft.verify_ssl !== false })
            + ecInputHtml({ name: 'timeout', label: ecT('ec_field_timeout'),
                value: draft.timeout === undefined ? 30 : draft.timeout, type: 'number', min: 1, max: 300 })
            + '</div>';
        var secret = ecSectionHtml('ec_section_auth', ecSecretFieldHtml({
            slot: 'password', label: ecT('ec_field_password'), configured: secrets.password }));
        return ecSectionHtml('ec_section_connection', group + rfc + adt) + secret;
    }

    function ecOaFormHtml(draft, secrets) {
        var body = ''
            + ecInputHtml({ name: 'base_url', label: ecT('ec_field_base_url'), value: draft.base_url || '',
                required: true, full: true })
            + ecInputHtml({ name: 'username', label: ecT('ec_field_oa_username'), value: draft.username || '', required: true })
            + ecInputHtml({ name: 'app_key', label: ecT('ec_field_app_key'), value: draft.app_key || '' })
            + ecInputHtml({ name: 'corp_id', label: ecT('ec_field_corp_id'), value: draft.corp_id || '' })
            + ecInputHtml({ name: 'tenant_key', label: ecT('ec_field_tenant_key'), value: draft.tenant_key || '' })
            + ecInputHtml({ name: 'custom_page_config_id', label: ecT('ec_field_custom_page_config_id'),
                value: draft.custom_page_config_id || '' });
        var secret = ecSectionHtml('ec_section_auth',
            ecSecretFieldHtml({ slot: 'password', label: ecT('ec_field_password'), configured: secrets.password })
            + ecSecretFieldHtml({ slot: 'app_secret', label: ecT('ec_field_app_secret'), configured: secrets.app_secret }));
        return ecSectionHtml('ec_section_connection', body) + secret;
    }

    function ecMailSideHtml(side, draft, secrets) {
        var prefix = side === 'imap' ? 'imap_' : 'smtp_';
        var enabled = draft[prefix + 'enabled'] !== false;
        var titleKey = side === 'imap' ? 'ec_section_imap' : 'ec_section_smtp';
        var fields = side === 'imap'
            ? ecInputHtml({ name: 'imap_host', label: ecT('ec_field_imap_host'), value: draft.imap_host || '', required: enabled })
              + ecInputHtml({ name: 'imap_port', label: ecT('ec_field_imap_port'),
                  value: draft.imap_port === undefined ? 993 : draft.imap_port, type: 'number', min: 1, max: 65535 })
              + ecInputHtml({ name: 'imap_user', label: ecT('ec_field_imap_user'), value: draft.imap_user || '',
                  required: enabled, full: true, type: 'email' })
              + ecCheckboxHtml({ name: 'imap_tls', label: ecT('ec_field_imap_tls'), checked: draft.imap_tls !== false })
              + ecCheckboxHtml({ name: 'imap_reject_unauthorized', label: ecT('ec_field_imap_reject'),
                  checked: draft.imap_reject_unauthorized !== false })
              + ecInputHtml({ name: 'imap_mailbox', label: ecT('ec_field_mailbox'),
                  value: draft.imap_mailbox || 'INBOX' })
            : ecInputHtml({ name: 'smtp_host', label: ecT('ec_field_smtp_host'), value: draft.smtp_host || '', required: enabled })
              + ecInputHtml({ name: 'smtp_port', label: ecT('ec_field_smtp_port'),
                  value: draft.smtp_port === undefined ? 465 : draft.smtp_port, type: 'number', min: 1, max: 65535 })
              + ecInputHtml({ name: 'smtp_user', label: ecT('ec_field_smtp_user'), value: draft.smtp_user || '',
                  required: enabled, full: true, type: 'email' })
              + ecInputHtml({ name: 'smtp_from_addr', label: ecT('ec_field_smtp_from'), value: draft.smtp_from_addr || '',
                  required: enabled, type: 'email' })
              + ecSelectHtml({ name: 'smtp_tls_mode', label: ecT('ec_field_smtp_tls_mode'),
                  value: draft.smtp_tls_mode || 'implicit_tls', options: [
                      { value: 'implicit_tls', label: ecT('ec_tls_mode_implicit_tls') },
                      { value: 'starttls', label: ecT('ec_tls_mode_starttls') },
                      { value: 'none', label: ecT('ec_tls_mode_none') },
                  ] })
              + ecCheckboxHtml({ name: 'smtp_reject_unauthorized', label: ecT('ec_field_smtp_reject'),
                  checked: draft.smtp_reject_unauthorized !== false });
        var secret = ecSecretFieldHtml({
            slot: side === 'imap' ? 'imap_password' : 'smtp_password',
            label: side === 'imap' ? ecT('ec_field_imap_password') : ecT('ec_field_smtp_password'),
            configured: secrets[side === 'imap' ? 'imap_password' : 'smtp_password'],
        });
        return '<fieldset class="ec-section ec-mail-side' + (enabled ? '' : ' ec-mail-side-off') + '">'
            + '<legend class="ec-section-title">' + ecEsc(ecT(titleKey)) + '</legend>'
            + ecCheckboxHtml({ name: prefix + 'enabled', label: ecT('ec_protocol_enabled'), checked: enabled,
                toggle: 'mail-' + side })
            + '<div class="ec-form-grid ec-subgroup-full" data-ec-group="mail-' + side + '"'
            + (enabled ? '' : ' hidden') + '>' + fields + secret + '</div>'
            + '</fieldset>';
    }

    function ecEmailFormHtml(draft, secrets) {
        return ''
            + ecMailSideHtml('imap', draft, secrets)
            + ecMailSideHtml('smtp', draft, secrets)
            + ecSectionHtml('ec_section_advanced', ecTextareaHtml({
                name: 'attachment_dirs', label: ecT('ec_field_attachment_dirs'),
                value: draft.attachment_dirs || '', full: true, rows: 3, hint: ecT('ec_validate_attachment_dirs'),
            }));
    }

    function ecFormForKind(kind, draft, secrets) {
        if (kind === 'mcp') return ecMcpFormHtml(draft, secrets);
        if (kind === 'erp') return ecErpFormHtml(draft, secrets);
        if (kind === 'oa') return ecOaFormHtml(draft, secrets);
        if (kind === 'email') return ecEmailFormHtml(draft, secrets);
        return '';
    }

    // =================================================================
    // Drawer open / close
    // =================================================================
    function ecOpenDrawerFromCard(card, mode) {
        ecFetchDetail(card.scope, card.id).then(function (res) {
            if (!ecOk(res)) {
                ecToast(ecT('ec_error_server', { message: ecErrorOf(res).message || ecErrorOf(res).code }), 'error');
                return;
            }
            var detail = ecPayloadOf(res);
            ecOpenDrawer({
                mode: mode || (detail.actions && detail.actions.indexOf('manage') >= 0 ? 'edit' : 'view'),
                kind: detail.kind,
                scope: detail.scope,
                id: detail.id,
                version: detail.version,
                original: detail,
                focus: card && card.node,
            });
        });
    }

    function ecFetchDetail(scope, id) {
        return ecRequest(ecPathFor(scope, id));
    }

    function ecOpenDrawer(options) {
        var original = options.original || {};
        var draft = ecDraftFromOriginal(options.kind, original);
        var secrets = {};
        // An override carries the tenant's *own* credentials; the platform
        // template's secret presence must never be presented as if the tenant
        // already owned it.
        var presence = (options.mode === 'override') ? {} : (original.secrets || {});
        Object.keys(presence).forEach(function (slot) {
            secrets[slot] = !!(presence[slot] && presence[slot].configured);
        });
        ecDrawer = {
            open: true,
            mode: options.mode || 'create',
            kind: options.kind,
            scope: options.scope || ecPreferredScope(ecTypeByKind(options.kind)),
            id: options.id || null,
            version: options.version === undefined ? null : options.version,
            baseConnectionId: options.baseConnectionId || null,
            original: original,
            draft: draft,
            secrets: secrets,
            secretInputs: {},
            clears: {},
            dirty: options.mode === 'create' ? false : false,
            errors: {},
            serverFields: {},
            notice: null,
            saving: false,
            epoch: 0,
            identity: ecIdentityToken(),
        };
        if (options.focus) ecFocusReturn = options.focus;
        ecRenderDrawer();
        var node = ecEnsureDrawerNode();
        node.hidden = false;
        var panel = node.querySelector('.ec-drawer-panel');
        ecFocusFirst(panel);
    }

    function ecDraftFromOriginal(kind, detail) {
        var config = (detail && detail.config) || {};
        var draft = { name: (detail && detail.name) || '', enabled: detail ? detail.enabled !== false : true };
        if (kind === 'mcp') {
            draft.transport = config.transport || 'streamable_http';
            draft.url = config.url || '';
            draft.auth = config.auth || 'none';
            draft.header_name = config.header_name || '';
            draft.oauth_provider = config.oauth_provider || '';
            draft.command = config.command || '';
            draft.args = (config.args || []).join('\n');
            draft.env_keys = (config.env_keys || []).join('\n');
        } else if (kind === 'erp') {
            draft.provider = config.provider || 'rfc';
            draft.ashost = config.ashost || '';
            draft.sysnr = config.sysnr || '';
            draft.client = config.client || '';
            draft.user = config.user || '';
            draft.lang = config.lang || 'EN';
            draft.base_url = config.base_url || '';
            draft.verify_ssl = config.verify_ssl !== false;
            draft.timeout = config.timeout === undefined ? 30 : config.timeout;
        } else if (kind === 'oa') {
            ['base_url', 'username', 'tenant_key', 'custom_page_config_id', 'app_key', 'corp_id'].forEach(function (key) {
                draft[key] = config[key] || '';
            });
        } else if (kind === 'email') {
            var imap = config.imap || {};
            var smtp = config.smtp || {};
            draft.imap_enabled = imap.enabled !== false;
            draft.imap_host = imap.host || '';
            draft.imap_port = imap.port === undefined ? 993 : imap.port;
            draft.imap_user = imap.user || '';
            draft.imap_tls = imap.tls !== false;
            draft.imap_reject_unauthorized = imap.reject_unauthorized !== false;
            draft.imap_mailbox = imap.mailbox || 'INBOX';
            draft.smtp_enabled = smtp.enabled !== false;
            draft.smtp_host = smtp.host || '';
            draft.smtp_port = smtp.port === undefined ? 465 : smtp.port;
            draft.smtp_user = smtp.user || '';
            draft.smtp_from_addr = smtp.from_addr || '';
            draft.smtp_tls_mode = smtp.tls_mode || 'implicit_tls';
            draft.smtp_reject_unauthorized = smtp.reject_unauthorized !== false;
            draft.attachment_dirs = (config.attachment_dirs || []).join('\n');
        }
        return draft;
    }

    function ecRenderDrawer() {
        if (!ecDrawer || !ecDrawer.open) return;
        var node = ecEnsureDrawerNode();
        var head = document.getElementById('ec-drawer-head');
        var body = document.getElementById('ec-drawer-body');
        var foot = document.getElementById('ec-drawer-foot');
        var titleKey = ecDrawer.mode === 'create' ? 'ec_editor_new'
            : (ecDrawer.mode === 'override' ? 'ec_editor_override'
                : (ecDrawer.mode === 'view' ? 'ec_editor_view' : 'ec_editor_edit'));
        var typeLabel = ecT(TYPE_LABEL_KEY[ecDrawer.kind] || ecDrawer.kind);
        var scopeHint = ecDrawer.scope === 'personal' ? 'ec_editor_scope_hint_personal'
            : (ecDrawer.scope === 'platform' ? 'ec_editor_scope_hint_platform' : 'ec_editor_scope_hint_tenant');
        head.innerHTML = '<div>'
            + '<h2 class="ec-drawer-title" id="ec-drawer-title">'
            + ecEsc(ecT(titleKey, { type: typeLabel })) + '</h2>'
            + '<p class="ec-drawer-sub">' + ecEsc(ecT(scopeHint)) + '</p>'
            + (ecDrawer.mode === 'override' ? '<p class="ec-hint">' + ecEsc(ecT('ec_editor_override_hint')) + '</p>' : '')
            + '</div>'
            + '<button type="button" class="ec-icon-btn" data-ec-action="drawer-close" aria-label="'
            + ecEsc(ecT('ec_close')) + '"><i class="fas fa-xmark" aria-hidden="true"></i></button>';

        var readonly = ecDrawer.mode === 'view';
        var banner = ecDrawer.notice
            ? '<p class="ec-banner ec-banner-' + ecEsc(ecDrawer.notice.tone || 'warn') + '" role="alert">'
              + ecEsc(ecDrawer.notice.text) + '</p>'
            : '';
        if (ecDrawer.conflict || ecDrawer.unknown) {
            // The user's input is untouched; this re-reads the server baseline.
            banner += '<p class="ec-banner-actions">'
                + '<button type="button" class="ec-btn ec-btn-ghost ec-btn-small" data-ec-action="drawer-reload">'
                + ecEsc(ecT('ec_error_reload')) + '</button></p>';
        }
        body.innerHTML = banner
            + (readonly ? '<p class="ec-banner ec-banner-info">' + ecEsc(ecT('ec_readonly_banner')) + '</p>' : '')
            + ecSectionHtml('ec_section_basic', ecInputHtml({
                name: 'name', label: ecT('ec_field_name'), value: ecDrawer.draft.name || '',
                required: true, full: true }))
            + ecFormForKind(ecDrawer.kind, ecDrawer.draft, ecDrawer.secrets)
            + (ecDrawer.mode !== 'create' && ecDrawer.kind !== 'email'
                ? ecSectionHtml('ec_section_advanced', ecCheckboxHtml({
                    name: 'enabled', label: ecT('ec_enable_connection'),
                    checked: ecDrawer.draft.enabled !== false }))
                : '');

        var statusKey = ecDrawer.dirty ? 'ec_save_state_dirty' : 'ec_save_state_new';
        var footer = '<span class="ec-save-state' + (ecDrawer.dirty ? ' ec-save-state-dirty' : '') + '"'
            + (ecDrawer.mode === 'view' ? '' : ' role="status"') + '>'
            + ecEsc(ecT(ecDrawer.mode === 'view' ? 'ec_save_state_loaded' : statusKey)) + '</span>';
        footer += '<span class="ec-foot-actions">'
            + '<button type="button" class="ec-btn ec-btn-ghost" data-ec-action="drawer-close">'
            + ecEsc(ecT('ec_cancel')) + '</button>'
            + (readonly ? '' : '<button type="button" class="ec-btn ec-btn-primary" data-ec-action="drawer-save"'
                + (ecDrawer.saving ? ' disabled' : '') + '>' + ecEsc(ecT('ec_save')) + '</button>')
            + '</span>';
        foot.innerHTML = footer;

        if (readonly) {
            Array.prototype.slice.call(body.querySelectorAll('input, select, textarea')).forEach(function (el) {
                el.disabled = true;
            });
        }
        ecApplyFieldErrors();
        if (ecDrawer.mode === 'view') {
            node.querySelectorAll('[data-ec-action="drawer-close"]').forEach(function (el) {
                el.setAttribute('aria-label', ecT('ec_close'));
            });
        }
    }

    function ecApplyFieldErrors() {
        var body = document.getElementById('ec-drawer-body');
        if (!body) return;
        Array.prototype.slice.call(body.querySelectorAll('[data-ec-error-for]')).forEach(function (el) {
            el.hidden = true;
            el.textContent = '';
        });
        Array.prototype.slice.call(body.querySelectorAll('.ec-input')).forEach(function (el) {
            el.removeAttribute('aria-invalid');
        });
        Object.keys(ecDrawer.errors || {}).forEach(function (name) {
            var error = body.querySelector('[data-ec-error-for="' + name.replace(/"/g, '\\"') + '"]');
            if (!error) return;
            error.hidden = false;
            error.textContent = ecDrawer.errors[name];
            var input = null;
            if (name.indexOf('secret:') === 0) {
                input = error.parentNode
                    ? error.parentNode.querySelector('[data-ec-secret="' + name.slice(7) + '"]') : null;
            } else {
                input = body.querySelector('[data-ec-field="' + name + '"]');
            }
            if (input) input.setAttribute('aria-invalid', 'true');
        });
    }

    function ecRequestCloseDrawer() {
        if (!ecDrawer || !ecDrawer.open) return;
        if (!ecDrawer.dirty) { ecCloseDrawer(true); return; }
        ecConfirmDiscard(function () { ecCloseDrawer(true); });
    }

    function ecCloseDrawer(force) {
        if (!ecDrawer) return;
        if (!force && ecDrawer.dirty) { ecRequestCloseDrawer(); return; }
        var node = document.getElementById('ec-drawer');
        if (node) {
            node.hidden = true;
            var body = document.getElementById('ec-drawer-body');
            // The typed credentials exist only in these inputs; dropping the
            // markup is what removes them.
            if (body) body.innerHTML = '';
            var head = document.getElementById('ec-drawer-head');
            if (head) head.innerHTML = '';
            var foot = document.getElementById('ec-drawer-foot');
            if (foot) foot.innerHTML = '';
        }
        ecDrawer = null;
        ecRestoreFocus();
    }

    // =================================================================
    // Modal (confirm / picker / tenant access)
    // =================================================================
    function ecOpenModal(html, options) {
        var opts = options || {};
        var node = ecEnsureModalNode();
        var panel = document.getElementById('ec-modal-panel');
        panel.innerHTML = html;
        node.hidden = false;
        // Focus returns to whatever opened the modal.
        var active = opts.focus || document.activeElement;
        if (active && active !== document.body && active !== document.documentElement) {
            ecFocusReturn = active;
        }
        ecModal = { node: node, dirty: !!opts.dirty, onClose: opts.onClose || null };
        ecFocusFirst(panel);
        return panel;
    }

    function ecCloseModal(force) {
        if (!ecModal) return;
        if (!force && ecModal.dirty && !window.confirm(ecT('ec_unsaved_desc'))) return;
        var node = ecModal.node;
        if (node) {
            node.hidden = true;
            var panel = document.getElementById('ec-modal-panel');
            if (panel) panel.innerHTML = '';
        }
        var onClose = ecModal.onClose;
        ecModal = null;
        if (typeof onClose === 'function') onClose();
    }

    function ecConfirmHtml(titleKey, descKey, vars) {
        return '<h2 class="ec-modal-title" id="ec-modal-title">' + ecEsc(ecT(titleKey)) + '</h2>'
            + '<p class="ec-modal-text">' + ecEsc(ecT(descKey, vars)) + '</p>';
    }

    function ecConfirm(html, okLabelKey, onConfirm, options) {
        var opts = options || {};
        var body = ecOpenModal(html
            + '<div class="ec-modal-actions">'
            + '  <button type="button" class="ec-btn ec-btn-ghost" data-ec-action="modal-close">'
            + ecEsc(ecT('ec_cancel')) + '</button>'
            + '  <button type="button" class="ec-btn ' + (opts.danger ? 'ec-btn-danger' : 'ec-btn-primary')
            + '" data-ec-action="modal-confirm">' + ecEsc(ecT(okLabelKey)) + '</button>'
            + '</div>', { focus: opts.focus });
        var ok = body.querySelector('[data-ec-action="modal-confirm"]');
        if (ok) {
            ok.addEventListener('click', function () {
                var keep = typeof opts.beforeConfirm === 'function' ? opts.beforeConfirm(body) : true;
                if (keep === false) return;
                ecCloseModal(true);
                if (onConfirm) onConfirm();
            });
        }
        return body;
    }

    function ecConfirmDiscard(onDiscard, onKeep) {
        var dialog = ecConfirm(ecConfirmHtml('ec_unsaved_title', 'ec_unsaved_desc'), 'ec_discard', onDiscard, {
            danger: true,
            focus: ecDrawer && ecDrawer.open ? document.querySelector('.ec-drawer-panel') : null,
        });
        var cancel = dialog && dialog.querySelector('[data-ec-action="modal-close"]');
        if (cancel) {
            cancel.textContent = ecT('ec_keep_editing');
            cancel.addEventListener('click', function () { if (onKeep) onKeep(); });
        }
    }

    // =================================================================
    // Type picker (「新增连接」)
    // =================================================================
    function ecOpenTypePicker() {
        var rows = ecState.types.map(function (type) {
            var scope = ecPreferredScope(type);
            var entry = ecScopeEntry(type, scope) || {};
            var available = entry.available === true;
            var reason = entry.reason || '';
            var desc = ecT(TYPE_DESC_KEY[type.kind] || '');
            var meta = [];
            if (scope) meta.push(ecT(SCOPE_LABEL_KEY[scope] || scope));
            if (!available) meta.push(ecT('ec_type_unavailable'));
            var action;
            if (available) {
                action = '<button type="button" class="ec-btn ec-btn-primary ec-btn-small"'
                    + ' data-ec-action="pick-type" data-kind="' + ecEsc(type.kind) + '"'
                    + ' data-scope="' + ecEsc(scope) + '">' + ecEsc(ecT('ec_add')) + '</button>';
            } else if (reason === 'singleton_exists') {
                // The singleton already exists, so the useful action is editing
                // it, not a dead "create" button.
                action = '<button type="button" class="ec-btn ec-btn-ghost ec-btn-small"'
                    + ' data-ec-action="pick-existing" data-kind="' + ecEsc(type.kind) + '"'
                    + ' data-scope="' + ecEsc(scope) + '">' + ecEsc(ecT('ec_type_go_edit_existing')) + '</button>';
            } else {
                action = '<span class="ec-type-reason">' + ecEsc(ecReasonText(reason)) + '</span>';
            }
            return '<li class="ec-type-row" data-kind="' + ecEsc(type.kind) + '">'
                + '  <span class="ec-card-icon ec-icon-' + ecEsc(type.kind) + '">'
                + '    <i class="fas ' + ecEsc(TYPE_ICON[type.kind] || 'fa-plug') + '" aria-hidden="true"></i></span>'
                + '  <div class="ec-type-text"><h3>' + ecEsc(ecT(TYPE_LABEL_KEY[type.kind] || type.kind)) + '</h3>'
                + '    <p>' + ecEsc(desc) + '</p>'
                + '    <p class="ec-type-meta">' + ecEsc(meta.join(' · ')) + '</p></div>'
                + '  <div class="ec-type-action">' + action + '</div>'
                + '</li>';
        }).join('');
        var trigger = document.activeElement;
        ecOpenModal('<h2 class="ec-modal-title" id="ec-modal-title">' + ecEsc(ecT('ec_choose_type_title')) + '</h2>'
            + '<p class="ec-modal-text">' + ecEsc(ecT('ec_choose_type_sub')) + '</p>'
            + '<ul class="ec-type-list">' + rows + '</ul>'
            + '<div class="ec-modal-actions">'
            + '  <button type="button" class="ec-btn ec-btn-ghost" data-ec-action="modal-close">'
            + ecEsc(ecT('ec_cancel')) + '</button></div>', { focus: trigger });
    }

    function ecOpenExistingFor(kind, scope) {
        var card = ecState.cards.filter(function (item) {
            return item.kind === kind && item.scope === scope;
        })[0];
        ecCloseModal(true);
        if (card) ecOpenDrawerFromCard(card, 'edit');
        else ecToast(ecT('ec_toast_save_failed'), 'error');
    }

    // =================================================================
    // Draft collection / validation / payload assembly
    // =================================================================
    function ecSplitLines(value) {
        return String(value || '').split('\n').map(function (line) { return line.trim(); })
            .filter(function (line) { return !!line; });
    }

    function ecIsHttpUrl(value) {
        if (!/^https?:\/\//i.test(String(value || ''))) return false;
        try {
            var url = new URL(String(value));
            if (!url.hostname) return false;
            if (url.username || url.password) return false;
            return true;
        } catch (_) { return false; }
    }

    function ecIsEmail(value) {
        return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(String(value || ''));
    }

    function ecIsPort(value) {
        var n = Number(value);
        return Number.isInteger(n) && n >= 1 && n <= 65535;
    }

    // Which credential slots the current draft actually uses. A hidden group's
    // stale input is neither validated nor submitted, so switching transport or
    // disabling a mail protocol cannot leak an unrelated credential.
    function ecRelevantSlots(kind, draft) {
        var slots = [];
        if (kind === 'mcp') {
            if (draft.auth === 'header') slots.push('header');
            if (draft.auth === 'oauth') slots.push('oauth');
            if (draft.transport === 'stdio') slots.push('env');
        } else if (kind === 'erp') {
            slots.push('password');
        } else if (kind === 'oa') {
            slots.push('password', 'app_secret');
        } else if (kind === 'email') {
            if (draft.imap_enabled !== false) slots.push('imap_password');
            if (draft.smtp_enabled !== false) slots.push('smtp_password');
        }
        return slots;
    }

    // Mirrors integrations/external/registry.py `_validate_*` so the form and
    // the server agree on what a saveable connection is.
    function ecValidateDraft(drawer) {
        var draft = drawer.draft;
        var errors = {};
        var kind = drawer.kind;
        var name = String(draft.name || '').trim();
        if (!name) errors.name = ecT('ec_validate_required');
        else if (name.length > 64) errors.name = ecT('ec_validate_required');
        if (kind === 'mcp') {
            var transport = draft.transport;
            if (['stdio', 'sse', 'streamable_http'].indexOf(transport) < 0) {
                errors.transport = ecT('ec_validate_invalid');
            } else if (transport === 'stdio') {
                if (!String(draft.command || '').trim()) errors.command = ecT('ec_validate_required');
                ecSplitLines(draft.args).forEach(function (arg) {
                    if (arg.length > 512) errors.args = ecT('ec_validate_invalid');
                });
                ecSplitLines(draft.env_keys).forEach(function (key) {
                    if (!/^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(key)) errors.env_keys = ecT('ec_validate_env_key');
                });
            } else {
                if (!ecIsHttpUrl(draft.url)) errors.url = ecT('ec_validate_url');
                var auth = draft.auth || 'none';
                if (auth === 'header' && !String(draft.header_name || '').trim()) {
                    errors.header_name = ecT('ec_validate_required');
                }
            }
            var slots = ecRelevantSlots(kind, draft);
            slots.forEach(function (slot) {
                var value = drawer.secretInputs ? drawer.secretInputs[slot] : '';
                if (value && String(value).indexOf('••••') >= 0) {
                    errors['secret:' + slot] = ecT('ec_validate_secret_masked');
                }
            });
        } else if (kind === 'erp') {
            var provider = draft.provider || 'rfc';
            if (provider === 'rfc') {
                if (!String(draft.ashost || '').trim()) errors.ashost = ecT('ec_validate_required');
                if (!/^\d{2}$/.test(String(draft.sysnr || '').trim())) errors.sysnr = ecT('ec_validate_sysnr');
                if (!String(draft.client || '').trim()) errors.client = ecT('ec_validate_client_erp');
                if (!String(draft.user || '').trim()) errors.user = ecT('ec_validate_required');
            } else {
                if (!ecIsHttpUrl(draft.base_url)) errors.base_url = ecT('ec_validate_url');
                if (!String(draft.client || '').trim()) errors.client = ecT('ec_validate_client_erp');
                if (!String(draft.user || '').trim()) errors.user = ecT('ec_validate_required');
                var timeout = Number(draft.timeout);
                if (!Number.isInteger(timeout) || timeout < 1 || timeout > 300) {
                    errors.timeout = ecT('ec_validate_timeout');
                }
            }
        } else if (kind === 'oa') {
            if (!ecIsHttpUrl(draft.base_url)) errors.base_url = ecT('ec_validate_url');
            if (!String(draft.username || '').trim()) errors.username = ecT('ec_validate_required');
        } else if (kind === 'email') {
            var imapOn = draft.imap_enabled !== false;
            var smtpOn = draft.smtp_enabled !== false;
            if (!imapOn && !smtpOn) errors.imap_enabled = ecT('ec_validate_at_least_one_protocol');
            if (imapOn) {
                if (!String(draft.imap_host || '').trim()) errors.imap_host = ecT('ec_validate_required');
                if (!ecIsPort(draft.imap_port)) errors.imap_port = ecT('ec_validate_port');
                if (!ecIsEmail(draft.imap_user)) errors.imap_user = ecT('ec_validate_email');
            }
            if (smtpOn) {
                if (!String(draft.smtp_host || '').trim()) errors.smtp_host = ecT('ec_validate_required');
                if (!ecIsPort(draft.smtp_port)) errors.smtp_port = ecT('ec_validate_port');
                if (!ecIsEmail(draft.smtp_user)) errors.smtp_user = ecT('ec_validate_email');
                if (!ecIsEmail(draft.smtp_from_addr)) errors.smtp_from_addr = ecT('ec_validate_email');
            }
            ecSplitLines(draft.attachment_dirs).forEach(function (dir) {
                var normalized = dir.replace(/\\/g, '/');
                if (normalized.charAt(0) === '/' || normalized.split('/').indexOf('..') >= 0) {
                    errors.attachment_dirs = ecT('ec_validate_attachment_dirs');
                }
            });
        }
        ecRelevantSlots(kind, draft).forEach(function (slot) {
            var value = drawer.secretInputs ? drawer.secretInputs[slot] : '';
            if (value && String(value).indexOf('••••') >= 0) {
                errors['secret:' + slot] = ecT('ec_validate_secret_masked');
            }
        });
        return errors;
    }

    function ecBuildConfig(kind, draft) {
        if (kind === 'mcp') {
            if (draft.transport === 'stdio') {
                return { transport: 'stdio', command: String(draft.command || '').trim(),
                    args: ecSplitLines(draft.args), env_keys: ecSplitLines(draft.env_keys) };
            }
            var config = { transport: draft.transport, url: String(draft.url || '').trim(),
                auth: draft.auth || 'none' };
            if (config.auth === 'header') config.header_name = String(draft.header_name || '').trim();
            if (config.auth === 'oauth') config.oauth_provider = String(draft.oauth_provider || '').trim() || 'generic';
            return config;
        }
        if (kind === 'erp') {
            if ((draft.provider || 'rfc') === 'rfc') {
                return { provider: 'rfc', ashost: String(draft.ashost || '').trim(),
                    sysnr: String(draft.sysnr || '').trim(), client: String(draft.client || '').trim(),
                    user: String(draft.user || '').trim(), lang: String(draft.lang || 'EN').trim() || 'EN' };
            }
            return { provider: 'adt_sql', base_url: String(draft.base_url || '').trim(),
                client: String(draft.client || '').trim(), user: String(draft.user || '').trim(),
                verify_ssl: draft.verify_ssl !== false, timeout: Number(draft.timeout) };
        }
        if (kind === 'oa') {
            var oa = { base_url: String(draft.base_url || '').trim(),
                username: String(draft.username || '').trim() };
            ['tenant_key', 'custom_page_config_id', 'app_key', 'corp_id'].forEach(function (key) {
                var value = String(draft[key] || '').trim();
                if (value) oa[key] = value;
            });
            return oa;
        }
        if (kind === 'email') {
            var imap = { enabled: draft.imap_enabled !== false };
            if (imap.enabled) {
                imap.host = String(draft.imap_host || '').trim();
                imap.port = Number(draft.imap_port);
                imap.user = String(draft.imap_user || '').trim();
                imap.reject_unauthorized = draft.imap_reject_unauthorized !== false;
                imap.tls = draft.imap_tls !== false;
                imap.mailbox = String(draft.imap_mailbox || 'INBOX').trim() || 'INBOX';
            }
            var smtp = { enabled: draft.smtp_enabled !== false };
            if (smtp.enabled) {
                smtp.host = String(draft.smtp_host || '').trim();
                smtp.port = Number(draft.smtp_port);
                smtp.user = String(draft.smtp_user || '').trim();
                smtp.reject_unauthorized = draft.smtp_reject_unauthorized !== false;
                smtp.from_addr = String(draft.smtp_from_addr || '').trim();
                smtp.tls_mode = String(draft.smtp_tls_mode || 'implicit_tls');
            }
            return { imap: imap, smtp: smtp, attachment_dirs: ecSplitLines(draft.attachment_dirs) };
        }
        return {};
    }

    // keep / replace / clear, exactly the server's vocabulary. A blank input
    // means "keep" (never a replacement); a mask is refused before it leaves
    // the page; a ticked clear sends null.
    function ecBuildSecrets(drawer) {
        var secrets = {};
        ecRelevantSlots(drawer.kind, drawer.draft).forEach(function (slot) {
            if (drawer.clears[slot]) { secrets[slot] = null; return; }
            var value = drawer.secretInputs[slot];
            if (value === undefined || value === null || String(value) === '') return;
            if (String(value).indexOf('••••') >= 0) return;
            secrets[slot] = String(value);
        });
        return secrets;
    }

    // =================================================================
    // Save / test / operations
    // =================================================================
    function ecCollectDraftFromDom() {
        if (!ecDrawer || !ecDrawer.open) return;
        var body = document.getElementById('ec-drawer-body');
        if (!body) return;
        Array.prototype.slice.call(body.querySelectorAll('[data-ec-field]')).forEach(function (el) {
            var name = el.getAttribute('data-ec-field');
            ecDrawer.draft[name] = el.type === 'checkbox' ? !!el.checked : el.value;
        });
        Array.prototype.slice.call(body.querySelectorAll('[data-ec-secret]')).forEach(function (el) {
            ecDrawer.secretInputs[el.getAttribute('data-ec-secret')] = el.value;
        });
        Array.prototype.slice.call(body.querySelectorAll('[data-ec-clear]')).forEach(function (el) {
            ecDrawer.clears[el.getAttribute('data-ec-clear')] = !!el.checked;
        });
    }

    function ecSaveDrawer() {
        if (!ecDrawer || !ecDrawer.open || ecDrawer.saving) return Promise.resolve(false);
        ecCollectDraftFromDom();
        var errors = ecValidateDraft(ecDrawer);
        ecDrawer.errors = errors;
        ecDrawer.serverFields = {};
        if (Object.keys(errors).length) {
            ecDrawer.notice = { tone: 'error', text: ecT('ec_error_banner', { n: Object.keys(errors).length }) };
            ecRenderDrawer();
            return Promise.resolve(false);
        }
        ecDrawer.saving = true;
        ecDrawer.notice = null;
        ecDrawer.epoch += 1;
        var epoch = ecDrawer.epoch;
        var identity = ecIdentityToken();
        ecDrawer.identity = identity;
        ecRenderDrawer();

        var kind = ecDrawer.kind;
        var config = ecBuildConfig(kind, ecDrawer.draft);
        var secrets = ecBuildSecrets(ecDrawer);
        var body = { config: config };
        if (Object.keys(secrets).length) body.secrets = secrets;
        var path;
        var method = 'POST';
        var headers = {};
        if (ecDrawer.mode === 'create' || ecDrawer.mode === 'override') {
            body.kind = kind;
            body.name = String(ecDrawer.draft.name || '').trim();
            if (ecDrawer.mode === 'override') body.base_connection_id = ecDrawer.baseConnectionId;
            path = ecPathFor(ecDrawer.scope);
            headers['Idempotency-Key'] = ecNewIdempotencyKey();
        } else {
            body.expected_version = ecDrawer.version;
            body.name = String(ecDrawer.draft.name || '').trim();
            if (ecDrawer.kind !== 'email') body.enabled = ecDrawer.draft.enabled !== false;
            path = ecPathFor(ecDrawer.scope, ecDrawer.id) + '/update';
        }

        return ecRequest(path, { method: method, headers: headers, body: body }).then(function (res) {
            if (!ecDrawerCurrent(epoch, identity)) return false;
            ecDrawer.saving = false;
            if (res.status === 409) {
                var error = ecErrorOf(res);
                if (error.code === 'referenced' || (error.references && error.references.length)) {
                    ecDrawer.notice = null;
                    ecRenderDrawer();
                    ecShowReferenceRefusal(error.references);
                    return false;
                }
                // Keep the user's input; offer a re-read instead of overwriting.
                ecDrawer.notice = { tone: 'error', text: ecT('ec_error_conflict_desc') };
                ecDrawer.conflict = true;
                ecRenderDrawer();
                return false;
            }
            if (!ecOk(res)) {
                var failure = ecErrorOf(res);
                if (failure.transport) {
                    ecDrawer.notice = { tone: 'error', text: ecT('ec_error_unknown_desc') };
                    ecDrawer.unknown = true;
                    ecRenderDrawer();
                    return false;
                }
                ecDrawer.serverFields = failure.fields || {};
                ecDrawer.errors = ecServerFieldErrors(failure.fields);
                ecDrawer.notice = { tone: 'error', text: ecT('ec_error_server', {
                    message: failure.message || failure.code || String(failure.status) }) };
                ecRenderDrawer();
                return false;
            }
            var created = (ecDrawer.mode === 'create' || ecDrawer.mode === 'override');
            ecCloseDrawer(true);
            ecToast(ecT(created ? 'ec_toast_created' : 'ec_toast_saved'));
            loadExternalConnectionsView();
            return true;
        });
    }

    function ecServerFieldErrors(fields) {
        var errors = {};
        Object.keys(fields || {}).forEach(function (name) {
            errors[name] = ecFieldErrorText(name, String(fields[name]));
        });
        return errors;
    }

    function ecFieldErrorText(name, token) {
        if (FIELD_ERROR_OVERRIDE[name] && (token === 'invalid' || token === 'range'
                || token === 'type' || token === 'not_allowed')) {
            return ecT(FIELD_ERROR_OVERRIDE[name]);
        }
        var key = FIELD_ERROR_KEY[token];
        if (key) return ecT(key);
        return ecT('ec_validate_invalid');
    }

    function ecDrawerCurrent(epoch, identity) {
        return !!(ecDrawer && ecDrawer.open && ecDrawer.epoch === epoch
            && identity === ecIdentityToken());
    }

    function ecReloadDrawerDetail() {
        if (!ecDrawer || !ecDrawer.id) return Promise.resolve(false);
        var epoch = ecDrawer.epoch;
        var identity = ecIdentityToken();
        return ecFetchDetail(ecDrawer.scope, ecDrawer.id).then(function (res) {
            if (!ecDrawerCurrent(epoch, identity)) return false;
            if (!ecOk(res)) {
                ecDrawer.notice = { tone: 'error', text: ecT('ec_error_unknown_desc') };
                ecRenderDrawer();
                return false;
            }
            var detail = ecPayloadOf(res);
            // The user's typed input stays in place; only the CAS token and the
            // server-side baseline are refreshed so a merge is possible.
            ecDrawer.version = detail.version;
            ecDrawer.original = detail;
            ecDrawer.conflict = false;
            ecDrawer.notice = { tone: 'info', text: ecT('ec_error_conflict_desc') };
            ecRenderDrawer();
            return true;
        });
    }

    function ecShowReferenceRefusal(references) {
        var items = (references || []).map(function (ref) {
            var key = ref.kind === 'erp_default' ? 'ec_reference_kind_erp_default'
                : (ref.kind === 'tenant_access' ? 'ec_reference_kind_tenant_access'
                    : 'ec_reference_kind_override');
            var suffix = ref.count ? ' × ' + ref.count : '';
            return '<li>' + ecEsc(ecT(key) + suffix) + '</li>';
        }).join('');
        ecConfirm(ecConfirmHtml('ec_referenced_title', 'ec_referenced_desc')
            + '<ul class="ec-ref-list">' + items + '</ul>', 'ec_close', null, { focus: ecFocusReturn });
    }

    function ecRunTest(card) {
        var cap = ecCapabilityFor(card.kind) || {};
        if (cap.test_available !== true) return;   // the control is disabled; never pretend
        ecRequest(ecPathFor(card.scope, card.id) + '/test', { method: 'POST', body: {} })
            .then(function (res) {
                if (!ecOk(res)) {
                    ecToast(ecT('ec_error_server', { message: ecErrorOf(res).message }), 'error');
                    return;
                }
                var payload = ecPayloadOf(res);
                var status = payload.test_status || 'untested';
                ecToast(ecT(TEST_STATUS_KEY[status] || 'ec_status_untested'));
                loadExternalConnectionsView();
            });
    }

    function ecToggleConnection(card) {
        var flags = ecCardFlags(card);
        if (!card.enabled) {
            // Turning a connection on has nothing destructive to confirm.
            ecRequest(ecPathFor(card.scope, card.id) + '/update', {
                method: 'POST',
                body: { expected_version: card.version, enabled: true },
            }).then(function (res) { ecHandleWriteResult(res, 'ec_toast_enabled'); });
            return;
        }
        var html = '<h2 class="ec-modal-title">' + ecEsc(ecT('ec_confirm_disable_title')) + '</h2>'
            + '<p class="ec-modal-text">' + ecEsc(ecT('ec_confirm_disable_desc')) + '</p>';
        if (card.kind === 'erp' && flags.isDefault) html += ecDefaultHandlingHtml(card, 'disable');
        var chosenHandling = null;
        ecConfirm(html, 'ec_toggle_disable', function () {
            var body = { expected_version: card.version, enabled: false };
            if (chosenHandling) body.default_handling = chosenHandling;
            ecRequest(ecPathFor(card.scope, card.id) + '/update', { method: 'POST', body: body })
                .then(function (res) { ecHandleWriteResult(res, 'ec_toast_disabled'); });
        }, {
            danger: true,
            beforeConfirm: function (panel) {
                chosenHandling = ecPickDefaultHandlingFromModal(panel);
                return true;
            },
        });
    }

    function ecDefaultHandlingHtml(card, mode) {
        var candidates = ecState.cards.filter(function (item) {
            return item.kind === 'erp' && item.scope === 'tenant' && item.enabled && item.id !== card.id;
        });
        var options = candidates.map(function (item) {
            return '<option value="' + ecEsc(item.id) + '">' + ecEsc(item.name) + '</option>';
        }).join('');
        return '<div class="ec-field ec-field-full" data-ec-default-handling>'
            + '<p class="ec-hint">' + ecEsc(ecT('ec_confirm_default_replace_desc')) + '</p>'
            + (candidates.length
                ? '<label class="ec-label" for="ec-default-replacement">'
                  + ecEsc(ecT('ec_default_replacement_label')) + '</label>'
                  + '<select id="ec-default-replacement" class="ec-input ec-select">'
                  + '<option value="">' + ecEsc(ecT('ec_default_clear_confirm')) + '</option>'
                  + options + '</select>'
                : '<p class="ec-hint">' + ecEsc(ecT('ec_confirm_delete_default_desc')) + '</p>')
            + '</div>';
    }

    // The confirm callback runs after the modal is emptied, so the chosen
    // handling has to be read while the markup is still there (`beforeConfirm`).
    function ecPickDefaultHandlingFromModal(scope) {
        var group = (scope || document).querySelector('[data-ec-default-handling]');
        if (!group) return null;
        var select = group.querySelector('select');
        if (!select || !select.value) return { action: 'clear' };
        return { action: 'replace', connection_id: select.value };
    }

    function ecHandleWriteResult(res, successKey) {
        if (res.status === 409) {
            var error = ecErrorOf(res);
            if (error.references && error.references.length) { ecShowReferenceRefusal(error.references); return; }
            ecToast(ecT('ec_error_conflict_desc'), 'error');
            return;
        }
        if (!ecOk(res)) {
            var failure = ecErrorOf(res);
            ecToast(ecT('ec_error_server', { message: failure.message || failure.code }), 'error');
            return;
        }
        if (successKey) ecToast(ecT(successKey));
        loadExternalConnectionsView();
    }

    function ecConfirmDelete(card) {
        var flags = ecCardFlags(card);
        var html = '<h2 class="ec-modal-title">' + ecEsc(ecT('ec_confirm_delete_title')) + '</h2>'
            + '<p class="ec-modal-text">' + ecEsc(ecT('ec_confirm_delete_desc', { name: card.name })) + '</p>';
        if (flags.isDefault) html += ecDefaultHandlingHtml(card, 'delete');
        var chosenHandling = null;
        ecConfirm(html, 'ec_action_delete', function () {
            var body = { expected_version: card.version };
            if (chosenHandling) body.default_handling = chosenHandling;
            ecRequest(ecPathFor(card.scope, card.id) + '/delete', { method: 'POST', body: body })
                .then(function (res) {
                    if (res.status === 409) {
                        var error = ecErrorOf(res);
                        if (error.references && error.references.length) {
                            ecShowReferenceRefusal(error.references);
                            return;
                        }
                    }
                    ecHandleWriteResult(res, 'ec_toast_deleted');
                });
        }, {
            danger: true,
            beforeConfirm: function (panel) {
                chosenHandling = ecPickDefaultHandlingFromModal(panel);
                return true;
            },
        });
    }

    function ecSetErpDefault(card, clear) {
        if (!ecState.erpDefault) return;
        var body = {
            connection_id: clear ? null : card.id,
            expected_revision: ecState.erpDefault.revision,
        };
        var titleKey = clear ? 'ec_confirm_default_clear_title' : 'ec_action_set_default';
        var descKey = clear ? 'ec_confirm_default_clear_desc' : 'ec_confirm_default_replace_desc';
        ecConfirm('<h2 class="ec-modal-title">' + ecEsc(ecT(titleKey)) + '</h2>'
            + '<p class="ec-modal-text">' + ecEsc(ecT(descKey)) + '</p>',
            clear ? 'ec_action_clear_default' : 'ec_action_set_default', function () {
                ecRequest(ecPathFor('tenant', 'erp-default'), { method: 'POST', body: body })
                    .then(function (res) {
                        ecHandleWriteResult(res, clear ? 'ec_toast_default_cleared' : 'ec_toast_default_set');
                    });
            }, { danger: false });
    }

    function ecRestoreInheritance(card) {
        ecConfirm(ecConfirmHtml('ec_confirm_restore_title', 'ec_confirm_restore_desc'), 'ec_action_restore', function () {
            ecRequest(ecPathFor('tenant', card.base_connection_id) + '/restore-inheritance',
                { method: 'POST', body: { expected_version: card.version } })
                .then(function (res) { ecHandleWriteResult(res, 'ec_toast_restored'); });
        });
    }

    function ecOpenOverrideDrawer(card) {
        // The override carries the tenant's own complete configuration and its
        // own credentials; the platform template's secret is never copied.
        ecFetchDetail('tenant', card.id).then(function (res) {
            if (!ecOk(res)) {
                ecToast(ecT('ec_error_server', { message: ecErrorOf(res).message }), 'error');
                return;
            }
            var detail = ecPayloadOf(res);
            ecOpenDrawer({
                mode: 'override',
                kind: 'mcp',
                scope: 'tenant',
                baseConnectionId: card.id,
                version: null,
                original: detail,
                focus: card.node,
            });
        });
    }

    function ecOpenTenantAccess(card) {
        var epoch = ecState.epoch;
        var identity = ecIdentityToken();
        ecRequest(ecPathFor('platform', card.id) + '/tenant-access').then(function (res) {
            if (!ecStillCurrent(epoch, identity)) return;
            if (!ecOk(res)) {
                ecToast(ecT('ec_error_server', { message: ecErrorOf(res).message }), 'error');
                return;
            }
            var data = ecPayloadOf(res);
            var lines = (data.tenants || []).filter(function (item) { return item.enabled; })
                .map(function (item) { return item.tenant_id; }).join('\n');
            var body = ecOpenModal('<h2 class="ec-modal-title">' + ecEsc(ecT('ec_tenant_access_title')) + '</h2>'
                + '<p class="ec-modal-text">' + ecEsc(ecT('ec_tenant_access_desc')) + '</p>'
                + '<div class="ec-field ec-field-full">'
                + '<label class="ec-label" for="ec-tenant-access-input">'
                + ecEsc(ecT('ec_tenant_access_title')) + '</label>'
                + '<textarea id="ec-tenant-access-input" class="ec-input ec-textarea" rows="6"'
                + ' placeholder="' + ecEsc(ecT('ec_tenant_access_placeholder')) + '">' + ecEsc(lines) + '</textarea>'
                + '</div>'
                + '<div class="ec-modal-actions">'
                + '<button type="button" class="ec-btn ec-btn-ghost" data-ec-action="modal-close">'
                + ecEsc(ecT('ec_cancel')) + '</button>'
                + '<button type="button" class="ec-btn ec-btn-primary" data-ec-action="tenant-access-save">'
                + ecEsc(ecT('ec_tenant_access_save')) + '</button></div>',
                { focus: card.node, dirty: true });
            var area = body.querySelector('#ec-tenant-access-input');
            area.addEventListener('input', function () { ecModal.dirty = area.value !== lines; });
            body.querySelector('[data-ec-action="tenant-access-save"]').addEventListener('click', function () {
                var tenantIds = ecSplitLines(area.value);
                ecRequest(ecPathFor('platform', card.id) + '/tenant-access', {
                    method: 'POST',
                    body: { tenant_ids: tenantIds, expected_revision: data.revision },
                }).then(function (saveRes) {
                    if (saveRes.status === 409) {
                        ecToast(ecT('ec_error_conflict_desc'), 'error');
                        return;
                    }
                    if (!ecOk(saveRes)) {
                        ecToast(ecT('ec_error_server', { message: ecErrorOf(saveRes).message }), 'error');
                        return;
                    }
                    ecCloseModal(true);
                    ecToast(ecT('ec_toast_tenant_access_saved'));
                    loadExternalConnectionsView();
                });
            });
        });
    }

    // =================================================================
    // Toast
    // =================================================================
    function ecToast(message, tone) {
        if (!message) return;
        try {
            if (typeof window._wsToast === 'function') {
                window._wsToast(message, tone || 'success');
                return;
            }
            if (typeof window.showNotification === 'function') {
                window.showNotification(message, tone || 'success');
                return;
            }
        } catch (_) { /* fall through to the console */ }
        if (tone === 'error' && typeof console !== 'undefined' && console.warn) console.warn(message);
    }

    // =================================================================
    // Events
    // =================================================================
    function ecCardFor(node) {
        var cardNode = node.closest ? node.closest('[data-ec-card]') : null;
        if (!cardNode) return null;
        var id = cardNode.getAttribute('data-id');
        var card = ecState.cards.filter(function (item) { return item.id === id; })[0] || null;
        if (card) card.node = cardNode;
        return card;
    }

    function ecHandleClick(event) {
        var actionNode = event.target.closest ? event.target.closest('[data-ec-action]') : null;
        if (!actionNode) return;
        var action = actionNode.getAttribute('data-ec-action');
        var card = ecCardFor(actionNode);
        switch (action) {
            case 'refresh':
                loadExternalConnectionsView();
                break;
            case 'retry':
                loadExternalConnectionsView();
                break;
            case 'add':
                ecOpenTypePicker();
                break;
            case 'clear-filters':
                ecState.kindFilter = 'all';
                ecState.statusFilter = 'all';
                ecState.search = '';
                ecRender();
                break;
            case 'kind-filter':
                ecState.kindFilter = actionNode.getAttribute('data-kind');
                ecRender();
                break;
            case 'open':
                if (card) ecOpenDrawerFromCard(card, actionNode.getAttribute('data-mode'));
                break;
            case 'toggle':
                if (card) ecToggleConnection(card);
                break;
            case 'delete':
                if (card) ecConfirmDelete(card);
                break;
            case 'set-default':
                if (card) ecSetErpDefault(card, false);
                break;
            case 'clear-default':
                if (card) ecSetErpDefault(card, true);
                break;
            case 'restore':
                if (card) ecRestoreInheritance(card);
                break;
            case 'override':
                if (card) ecOpenOverrideDrawer(card);
                break;
            case 'tenant-access':
                if (card) ecOpenTenantAccess(card);
                break;
            case 'test':
                if (card) ecRunTest(card);
                break;
            case 'pick-type':
                ecCloseModal(true);
                ecOpenDrawer({
                    mode: 'create',
                    kind: actionNode.getAttribute('data-kind'),
                    scope: actionNode.getAttribute('data-scope'),
                    original: {},
                    focus: actionNode,
                });
                break;
            case 'pick-existing':
                ecOpenExistingFor(actionNode.getAttribute('data-kind'), actionNode.getAttribute('data-scope'));
                break;
            case 'drawer-close':
                ecRequestCloseDrawer();
                break;
            case 'drawer-save':
                ecSaveDrawer();
                break;
            case 'drawer-backdrop':
                ecRequestCloseDrawer();
                break;
            case 'drawer-reload':
                ecReloadDrawerDetail();
                break;
            case 'modal-close':
                ecCloseModal(false);
                break;
            case 'modal-backdrop':
                if (event.target === actionNode) ecCloseModal(false);
                break;
            default:
                break;
        }
    }

    function ecHandleInput(event) {
        var el = event.target;
        if (!el || !el.getAttribute) return;
        if (el.getAttribute('data-ec-action') === 'search') {
            ecState.search = el.value;
            // Re-rendering rebuilds the search input; focus and caret are
            // restored right after so typing is not interrupted.
            ecRender();
            var container = document.getElementById('view-external_connections');
            var search = container && container.querySelector('[data-ec-action="search"]');
            if (search) {
                search.focus();
                try { search.setSelectionRange(search.value.length, search.value.length); } catch (_) {}
            }
            return;
        }
        if (!ecDrawer || !ecDrawer.open) return;
        var field = el.getAttribute('data-ec-field');
        if (field && el.type !== 'checkbox' && el.tagName !== 'SELECT') {
            ecDrawer.draft[field] = el.value;
            ecMarkDirty();
        }
        var secret = el.getAttribute('data-ec-secret');
        if (secret) {
            ecDrawer.secretInputs[secret] = el.value;
            ecMarkDirty();
        }
        var clear = el.getAttribute('data-ec-clear');
        if (clear) {
            ecDrawer.clears[clear] = !!el.checked;
            ecMarkDirty();
        }
    }

    function ecMarkDirty() {
        if (!ecDrawer || !ecDrawer.open) return;
        ecDrawer.dirty = true;
        var state = document.querySelector('.ec-save-state');
        if (state) {
            state.textContent = ecT('ec_save_state_dirty');
            state.classList.add('ec-save-state-dirty');
        }
    }

    function ecHandleChange(event) {
        var el = event.target;
        if (!el || !el.getAttribute) return;
        if (el.getAttribute('data-ec-action') === 'status-filter') {
            ecState.statusFilter = el.value;
            ecRender();
            return;
        }
        if (!ecDrawer || !ecDrawer.open) return;
        var field = el.getAttribute('data-ec-field');
        if (field && (el.type === 'checkbox' || el.tagName === 'SELECT')) {
            ecDrawer.draft[field] = el.type === 'checkbox' ? !!el.checked : el.value;
            ecMarkDirty();
        }
        var clear = el.getAttribute('data-ec-clear');
        if (clear) {
            ecDrawer.clears[clear] = !!el.checked;
            ecMarkDirty();
        }
        if (el.getAttribute('data-ec-toggle')) ecApplyToggles();
    }

    function ecApplyToggles() {
        var body = document.getElementById('ec-drawer-body');
        if (!body || !ecDrawer) return;
        var draft = ecDrawer.draft;
        function setGroup(name, visible) {
            Array.prototype.slice.call(body.querySelectorAll('[data-ec-group="' + name + '"]'))
                .forEach(function (node) { node.hidden = !visible; });
        }
        setGroup('mcp-remote', draft.transport !== 'stdio');
        setGroup('mcp-stdio', draft.transport === 'stdio');
        setGroup('mcp-auth-header', draft.auth === 'header');
        setGroup('mcp-auth-oauth', draft.auth === 'oauth');
        setGroup('erp-rfc', (draft.provider || 'rfc') === 'rfc');
        setGroup('erp-adt', (draft.provider || 'rfc') !== 'rfc');
        setGroup('mail-imap', draft.imap_enabled !== false);
        setGroup('mail-smtp', draft.smtp_enabled !== false);
    }

    function ecHandleKeydown(event) {
        if (event.key === 'Escape') {
            if (ecModal) { ecCloseModal(false); return; }
            if (ecDrawer && ecDrawer.open) { ecRequestCloseDrawer(); return; }
        }
        if (ecModal && ecModal.node && !ecModal.node.hidden) {
            ecTrapFocus(event, document.getElementById('ec-modal-panel'));
        } else if (ecDrawer && ecDrawer.open) {
            ecTrapFocus(event, document.querySelector('.ec-drawer-panel'));
        }
    }

    // =================================================================
    // Mount / leave / identity invalidation
    // =================================================================
    function ecMount() {
        var root = ecRoot();
        if (!root) return;
        ecEnsureDrawerNode();
        ecEnsureModalNode();
        if (!ecState.mounted) {
            ecState.mounted = true;
            root.addEventListener('click', ecHandleClick);
            root.addEventListener('input', ecHandleInput);
            root.addEventListener('change', ecHandleChange);
            document.addEventListener('keydown', ecHandleKeydown);
            var drawer = document.getElementById('ec-drawer');
            if (drawer) drawer.addEventListener('click', ecHandleClick);
            var modal = document.getElementById('ec-modal');
            if (modal) modal.addEventListener('click', ecHandleClick);
        }
        if (!ecPeriodic) {
            ecPeriodic = window.setInterval(ecCheckIdentity, 1500);
        }
    }

    function ecCheckIdentity() {
        if (!ecState.identity) return;
        if (ecIdentityToken() === ecState.identity) return;
        // Identity or tenant changed under us: every draft on this page belongs
        // to the previous identity and is destroyed without being offered back.
        ecForceClearDrafts();
        ecState.identity = ecIdentityToken();
        if (typeof currentView !== 'undefined' && currentView === 'external_connections') {
            loadExternalConnectionsView();
        }
    }

    function ecForceClearDrafts() {
        if (ecDrawer && ecDrawer.open) {
            ecDrawer.dirty = false;
            ecCloseDrawer(true);
        }
        if (ecModal) ecCloseModal(true);
    }

    function ecConfirmLeave(targetViewId) {
        if (!ecIsDirty()) return true;
        ecRouteTarget = targetViewId || 'chat';
        ecConfirmDiscard(function () {
            ecForceClearDrafts();
            if (typeof window.navigateTo === 'function') window.navigateTo(ecRouteTarget);
        });
        return false;
    }

    function ecOnLeave() {
        // Leaving for real: secrets in the open inputs go with the markup.
        if (ecDrawer && ecDrawer.open) {
            ecDrawer.dirty = false;
            ecCloseDrawer(true);
        }
        if (ecModal) ecCloseModal(true);
        if (ecPeriodic) { window.clearInterval(ecPeriodic); ecPeriodic = null; }
    }

    // =================================================================
    // Registration
    // =================================================================
    if (typeof window.registerConsoleView === 'function') {
        window.registerConsoleView({
            id: 'external_connections',
            title: 'ec_title',
            page: 'menu_external_connections',
            keepAlive: false,
            load: function () {
                ecMount();
                return loadExternalConnectionsView();
            },
            isDirty: ecIsDirty,
            confirmLeave: ecConfirmLeave,
            onLeave: ecOnLeave,
        });
    }

    // Test surface. The page's honesty constraints (the closed test state, the
    // keep/replace/clear secret payload, the 409-preserving save) are pure
    // functions, so the front-end test drives exactly the code the browser runs
    // rather than a re-implementation of it.
    window.ExternalConnectionsPage = {
        constants: {
            KIND_ORDER: KIND_ORDER,
            REASON_KEY: REASON_KEY,
            SECRET_SLOT_KEY: SECRET_SLOT_KEY,
            TYPE_LABEL_KEY: TYPE_LABEL_KEY,
        },
        state: ecState,
        mount: ecMount,
        load: loadExternalConnectionsView,
        render: ecRender,
        pageHtml: ecPageHtml,
        cardHtml: ecCardHtml,
        testStateHtml: ecTestStateHtml,
        reasonText: ecReasonText,
        reasonKeyFor: function (reason) { return REASON_KEY[reason] || ''; },
        capabilityFor: ecCapabilityFor,
        validateDraft: ecValidateDraft,
        buildConfig: ecBuildConfig,
        buildSecrets: ecBuildSecrets,
        openDrawer: ecOpenDrawer,
        openTypePicker: ecOpenTypePicker,
        drawerState: function () { return ecDrawer; },
        setDraftValue: function (name, value) {
            if (!ecDrawer) return false;
            ecDrawer.draft[name] = value;
            ecDrawer.dirty = true;
            return true;
        },
        setSecretInput: function (slot, value) {
            if (!ecDrawer) return false;
            ecDrawer.secretInputs[slot] = value;
            ecDrawer.dirty = true;
            return true;
        },
        setClear: function (slot, value) {
            if (!ecDrawer) return false;
            ecDrawer.clears[slot] = !!value;
            ecDrawer.dirty = true;
            return true;
        },
        collectDraftFromDom: ecCollectDraftFromDom,
        save: ecSaveDrawer,
        runTest: ecRunTest,
        reloadDetail: ecReloadDrawerDetail,
        closeDrawer: ecCloseDrawer,
        // Operations are driven directly so the rendered impact copy and the
        // request bodies they build are asserted on the real code path.
        confirmDelete: ecConfirmDelete,
        toggleConnection: ecToggleConnection,
        setErpDefault: ecSetErpDefault,
        restoreInheritance: ecRestoreInheritance,
        openTenantAccess: ecOpenTenantAccess,
        showReferenceRefusal: ecShowReferenceRefusal,
        openExistingFor: ecOpenExistingFor,
        isDirty: ecIsDirty,
        identityToken: ecIdentityToken,
        request: ecRequest,
        confirmLeave: ecConfirmLeave,
        forceClearDrafts: ecForceClearDrafts,
        source: 'channel/web/static/js/external-connections.js',
    };
}());
