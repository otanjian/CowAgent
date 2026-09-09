/* identity-admin.js - Database-mode admin views (Tenant / Users / Roles / Org /
 * Platform accounts / Audit).
 *
 * Loaded alongside console.js when identity_mode=database. Provides the admin
 * views, a shared request helper, per-view loading and rendering, plus full
 * create/edit/delete forms (previously read-only). All authorization happens
 * server-side; this only improves UX and wires the frontend to the existing
 * CRUD endpoints in admin_handlers.py.
 *
 * Create/Edit use a single reusable modal built from a field spec. Every write
 * includes an `expected_version` so a concurrent change surfaces as a 409
 * conflict, which we surface and reload the list (the refresh-the-list-on-409
 * behaviour is the acceptance focus of this change).
 */
(function () {
    'use strict';

    // ---- shared request helper -------------------------------------------
    function t(key) {
        const lang = window.__cowLang__ || 'zh';
        const i18n = window.I18N || {};
        return (i18n[lang] && i18n[lang][key]) || (i18n.en && i18n.en[key]) || key;
    }

    let _generation = 0;

    // Bump on tenant switch or page cleanup so in-flight responses from the
    // previous tenant are discarded (task 3.7 late-response guard).
    function bumpTenantGeneration() {
        _generation += 1;
    }

    async function apiFetch(path, options) {
        const gen = _generation;
        const headers = Object.assign({}, (options && options.headers) || {});
        const tenant = sessionStorage.getItem('cow_tenant_id');
        if (tenant) headers['X-Tenant-ID'] = tenant;
        if (options && options.body) headers['Content-Type'] = 'application/json';
        const resp = await fetch(path, {
            credentials: 'same-origin',
            method: (options && options.method) || 'GET',
            headers: headers,
            body: (options && options.body) ? JSON.stringify(options.body) : undefined,
        });
        if (gen !== _generation) throw new Error('stale-response');
        if (resp.status === 401) {
            if (typeof maybeShowLoginOverlay === 'function') maybeShowLoginOverlay();
            throw new Error('unauthorized');
        }
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.status !== 'success') {
            const err = new Error(data.message || 'load-failed');
            err.status = resp.status;
            err.code = data.code;
            err.data = data;
            throw err;
        }
        return data;
    }

    function status(el, text, ok) {
        if (!el) return;
        el.textContent = text || '';
        el.classList.remove('opacity-0');
        el.style.color = ok ? '' : '#ef4444';
        if (text) setTimeout(() => el.classList.add('opacity-0'), 5000);
    }

    function confirmDiscard(dirty) {
        if (dirty && !window.confirm(t('unsaved_changes_warning'))) return false;
        return true;
    }

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function fmtActive(active) {
        return active ? t('active') : t('inactive');
    }

    function qs(params) {
        // Build a query string from {key: value}, dropping null/undefined/''.
        const parts = [];
        Object.keys(params || {}).forEach(function (k) {
            const v = params[k];
            if (v == null || v === '') return;
            parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(v));
        });
        return parts.join('&');
    }

    // Session-stored items so inline row buttons can look a row up by id.
    let _tenantById = {};
    let _memberById = {};
    let _roleById = {};
    let _deptById = {};
    let _roles = [];
    let _depts = [];
    let _permCatalog = null;
    // Pagination state (kept in-view so a filter change resets to page 1).
    let _memberPage = 1;
    let _memberPageSize = 20;
    let _memberFilters = { q: '', status: '', role: '', department_id: '' };
    let _platformUserPage = 1;
    let _platformUserPageSize = 20;
    let _platformUserFilters = { q: '', status: '' };
    let _auditFilters = { actor: '', action: '', result: '', since: '', until: '' };
    let _auditPage = 1;
    let _auditPageSize = 25;

    // Platform-admin target-tenant role editing scope (task 3.2). When a
    // platform admin edits another tenant's roles, this holds that tenant id;
    // when null, the role UI targets the *current* tenant via /api/tenant/roles.
    // role base helpers route role CRUD + the assign catalog accordingly.
    let _rolePlatformTarget = null;

    function _roleApiBase() {
        return _rolePlatformTarget
            ? '/api/platform/tenants/' + encodeURIComponent(_rolePlatformTarget) + '/roles'
            : '/api/tenant/roles';
    }

    function _roleCatalogBase() {
        return _rolePlatformTarget
            ? '/api/platform/tenants/' + encodeURIComponent(_rolePlatformTarget) + '/authorization/catalog'
            : '/api/tenant/authorization/catalog';
    }

    // Resource-authorization selection state (task 3.1). Selections are kept
    // here (not just in checked DOM boxes) so search/pagination across pages does
    // not drop an already-checked resource. Each kind keeps its own q/page and
    // a Set of selected resource_id. `_resourceActions` maps kind -> the grant
    // actions emitted when a resource is selected (defaults to the kind's full
    // set, mirroring the backend RESOURCE_ACTIONS).
    let _resourceState = {};
    let _resourceActions = {
        menu: ['view'],
        skill: ['read', 'use', 'edit', 'enable'],
        tool: ['read', 'execute', 'configure'],
        model: ['read', 'use'],
        agent: ['read', 'use', 'edit', 'enable'],
    };
    let _resourceKinds = ['menu', 'skill', 'tool', 'model', 'agent'];
    function _resourceKindLabel(k) {
        return t('admin_resource_kind_' + k) || k;
    }
    let _modelCapabilities = ['chat', 'chat_fallback', 'vision', 'asr', 'tts', 'embedding', 'image', 'search'];
    let _modelDefaultSel = {}; // capability -> model resource_id

    // Tenant-model grant picker (edit-tenant modal): the platform admin picks
    // models to allocate to a tenant. Kept apart from _resourceState (which is
    // the role-assign picker) because the catalog source (platform all-mode)
    // and the produced grants (tenant_resource_grants) differ.
    let _tenantGrantSel = new Set();   // selected model resource_ids
    let _tenantGrantCatalog = [];      // cached platform all-mode model items
    let _tenantGrantLoaded = false;    // whether we've fetched the catalog yet
    let _tenantGrantApiBase = '';      // platform tenant catalog base
    let _tenantGrantResourcesBase = ''; // platform tenant resources base
    let _tenantGrantVersion = 0;       // expected_version for the resources PUT

    function _resetResourceState(initialGrants, modelDefaults) {
        _resourceState = {};
        _resourceKinds.forEach(function (k) {
            _resourceState[k] = { q: '', page: 1, pageSize: 12, selected: new Set(), loaded: false };
        });
        _modelDefaultSel = {};
        (initialGrants || []).forEach(function (g) {
            if (!g || !_resourceKinds.includes(g.resource_kind)) return;
            if (!_resourceState[g.resource_kind]) return;
            _resourceState[g.resource_kind].selected.add(g.resource_id);
        });
        Object.keys(modelDefaults || {}).forEach(function (k) {
            const v = modelDefaults[k];
            if (v) _modelDefaultSel[k] = v;
        });
    }

    // ---- reusable create/edit modal --------------------------------------
    let _adminModal = { open: false, dirty: false, fields: [], submit: null, onConflictReload: null, statusEl: null };

    function ensureAdminModal() {
        let el = document.getElementById('admin-modal');
        if (el) return el;
        el = document.createElement('div');
        el.id = 'admin-modal';
        el.className = 'fixed inset-0 bg-black/50 z-[200] hidden flex items-center justify-center';
        el.innerHTML =
            '<div class="bg-white dark:bg-[#1A1A1A] rounded-xl border border-slate-200 dark:border-white/10 shadow-xl w-full h-full m-2 overflow-hidden flex flex-col">' +
            '<div class="px-6 pt-6 pb-3 flex-shrink-0">' +
            '<div class="flex items-center gap-3 mb-4">' +
            '<div class="w-10 h-10 rounded-xl bg-primary-50 dark:bg-primary-900/20 flex items-center justify-center flex-shrink-0">' +
            '<i id="admin-modal-icon" class="fas fa-plus text-primary-500"></i>' +
            '</div>' +
            '<div class="min-w-0 flex-1">' +
            '<h3 id="admin-modal-title" class="font-semibold text-slate-800 dark:text-slate-100 text-base"></h3>' +
            '<p id="admin-modal-subtitle" class="text-xs text-slate-500 dark:text-slate-400 mt-0.5"></p>' +
            '</div>' +
            '<button class="admin-modal-close p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-white/5 text-slate-400 cursor-pointer"><i class="fas fa-xmark"></i></button>' +
            '</div>' +
            '<div id="admin-modal-body" class="flex-1 overflow-y-auto px-6 py-3 flex flex-wrap gap-4 content-start"></div>' +
            '<p id="admin-modal-error" class="hidden mt-2 text-xs text-red-500"></p>' +
            '</div>' +
            '<div class="flex justify-end gap-3 px-6 py-4 border-t border-slate-100 dark:border-white/5 flex-shrink-0">' +
            '<button id="admin-modal-cancel" class="admin-modal-close px-4 py-2 rounded-lg border border-slate-200 dark:border-white/10 text-slate-600 dark:text-slate-300 text-sm hover:bg-slate-50 dark:hover:bg-white/5 cursor-pointer"></button>' +
            '<button id="admin-modal-submit" class="px-4 py-2 rounded-lg bg-primary-500 hover:bg-primary-600 text-white text-sm font-medium disabled:opacity-50"></button>' +
            '</div>' +
            '</div>';
        document.body.appendChild(el);
        el.querySelectorAll('.admin-modal-close').forEach(function (btn) {
            btn.addEventListener('click', function () { closeAdminModal(); });
        });
        el.addEventListener('click', function (e) { if (e.target === el) closeAdminModal(); });
        document.getElementById('admin-modal-submit').addEventListener('click', submitAdminModal);
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && _adminModal.open) closeAdminModal();
        });
        return el;
    }

    function fieldHtml(f) {
        const id = 'adm-fld-' + f.name;
        const val = (f.value == null ? '' : f.value);
        const req = f.required ? '<span class="text-red-500 ml-0.5">*</span>' : '';
        let control = '';
        let label = '';
        if (f.type === 'checkbox') {
            control = '<div class="flex items-center gap-2 py-1"><input type="checkbox" id="' + id + '"' + (val ? ' checked' : '') + ' class="agent-checkbox">' +
                '<span class="text-sm text-slate-600 dark:text-slate-300">' + escapeHtml(f.label) + '</span></div>';
        } else if (f.type === 'textarea') {
            label = '<label class="agent-field-label" for="' + id + '">' + escapeHtml(f.label) + req + '</label>';
            control = '<textarea id="' + id + '" class="agent-input agent-textarea" placeholder="' + escapeHtml(f.placeholder || '') + '">' + escapeHtml(val) + '</textarea>';
        } else if (f.type === 'select') {
            label = '<label class="agent-field-label" for="' + id + '">' + escapeHtml(f.label) + req + '</label>';
            control = '<select id="' + id + '" class="agent-input">' + (f.options || []).map(function (o) {
                return '<option value="' + escapeHtml(o.value) + '"' + (String(o.value) === String(val) ? ' selected' : '') + '>' + escapeHtml(o.label) + '</option>';
            }).join('') + '</select>';
        } else if (f.type === 'multi') {
            label = '<label class="agent-field-label">' + escapeHtml(f.label) + req + '</label>';
            control = '<div id="' + id + '" class="flex flex-wrap gap-2">' + (f.options || []).map(function (o) {
                const checked = (f.value || []).indexOf(o.value) !== -1;
                return '<label class="inline-flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-300 px-2 py-1.5 rounded-lg border border-slate-200 dark:border-white/10 cursor-pointer bg-slate-50 dark:bg-white/5">' +
                    '<input type="checkbox" value="' + escapeHtml(o.value) + '"' + (checked ? ' checked' : '') + '>' +
                    escapeHtml(o.label) + '</label>';
            }).join('') + '</div>';
        } else if (f.type === 'locked') {
            label = '<label class="agent-field-label">' + escapeHtml(f.label) + '</label>';
            control = '<div class="agent-input-locked">' + escapeHtml(val) + '</div>';
        } else if (f.type === 'resourcegroup') {
            control = resourceGroupHtml(f);
        } else if (f.type === 'modeldefaults') {
            control = modelDefaultsHtml(f);
        } else if (f.type === 'modelgrant') {
            control = tenantModelGrantHtml(f);
        } else {
            const type = f.type || 'text';
            label = '<label class="agent-field-label" for="' + id + '">' + escapeHtml(f.label) + req + '</label>';
            control = '<input type="' + type + '" id="' + id + '" class="agent-input" value="' + escapeHtml(val) + '" placeholder="' + escapeHtml(f.placeholder || '') + '">';
        }
        const wrapClass = f.inline ? 'agent-field agent-field-inline flex-1 min-w-[220px]' : 'agent-field w-full';
        return '<div class="' + wrapClass + '">' + label + control +
            (f.hint ? '<div class="agent-field-hint">' + escapeHtml(f.hint) + '</div>' : '') +
            '</div>';
    }

    // ---- resource-authorization pickers (task 3.1) -------------------------
    function _resCount(kind) {
        const st = _resourceState[kind];
        return st ? st.selected.size : 0;
    }

    function resourceGroupHtml(f) {
        // Renders a compact block listing the five resource kinds, each with a
        // live selection count and an expandable management area (search + paged
        // checkbox list). The whole block is a single field; collectField reads
        // the selections from _resourceState.
        const rows = _resourceKinds.map(function (k) {
            const n = _resCount(k);
            const summary = n ? t('admin_resources_selected').replace('{n}', n) : t('admin_resources_none');
            return '<div class="resource-kind-row" data-kind="' + escapeHtml(k) + '">' +
                '<div class="flex items-center justify-between py-1.5 cursor-pointer resource-kind-toggle">' +
                '<span class="text-sm font-medium text-slate-700 dark:text-slate-200">' + escapeHtml(_resourceKindLabel(k)) + '</span>' +
                '<span class="text-xs text-slate-400 resource-kind-summary">' + escapeHtml(summary) + '</span>' +
                '</div>' +
                '<div class="resource-kind-manage hidden pl-3 border-l border-slate-200 dark:border-white/10"></div>' +
                '</div>';
        }).join('');
        return '<div id="adm-fld-' + escapeHtml(f.name) + '" class="space-y-1">' + rows + '</div>';
    }

    async function _loadResourceCatalog(kind, q, page, pageSize) {
        const query = qs({ purpose: 'assign', kind: kind, q: q || '', page: page, page_size: pageSize });
        const data = await apiFetch(_roleCatalogBase() + '?' + query);
        return data;
    }

    function _resourceKindRowFind(kind) {
        const container = document.getElementById('adm-fld-resource_grants');
        if (!container) return null;
        // kind is one of a fixed small set (menu/skill/tool/model/agent), safe to
        // embed directly.
        return container.querySelector('.resource-kind-row[data-kind="' + kind + '"]');
    }

    async function _openResourceManage(kind) {
        const row = _resourceKindRowFind(kind);
        if (!row) return;
        const manage = row.querySelector('.resource-kind-manage');
        manage.classList.remove('hidden');
        const st = _resourceState[kind];
        if (!st) return;
        st.kind = kind;
        await _renderResourceList(kind);
    }

    function _closeResourceManage(kind) {
        const row = _resourceKindRowFind(kind);
        if (!row) return;
        const manage = row.querySelector('.resource-kind-manage');
        manage.classList.add('hidden');
    }

    async function _renderResourceList(kind) {
        const row = _resourceKindRowFind(kind);
        if (!row) return;
        const manage = row.querySelector('.resource-kind-manage');
        const st = _resourceState[kind];
        if (!st) return;
        manage.innerHTML = '<div class="py-1 text-xs text-slate-400">' + escapeHtml(t('admin_loading')) + '</div>';
        try {
            const data = await _loadResourceCatalog(kind, st.q, st.page, st.pageSize);
            const items = data.items || [];
            const total = data.total || 0;
            const actions = data.resource_actions || _resourceActions[kind] || [];
            st.actions = actions;
            const fn = function (selected) {
                return function (r) {
                    const checked = selected.has(r.resource_id);
                    const rid = r.resource_id;
                    const idSuffix = rid && rid.indexOf('nav:') === 0 ? rid.slice(4) : rid;
                    return '<label class="inline-flex items-start gap-2 text-xs text-slate-600 dark:text-slate-300 py-1 px-1.5 rounded hover:bg-slate-50 dark:hover:bg-white/5 cursor-pointer">' +
                        '<input type="checkbox" value="' + escapeHtml(rid) + '"' + (checked ? ' checked' : '') + '>' +
                        '<span class="flex flex-col leading-tight"><span class="truncate">' + escapeHtml(r.name) + '</span>' +
                        (idSuffix && idSuffix !== r.name ? '<span class="text-[10px] text-slate-400 dark:text-slate-500 truncate">' + escapeHtml(idSuffix) + '</span>' : '') +
                        '</span></label>';
                };
            };
            const listHtml = items.length
                ? items.map(fn(st.selected)).join('')
                : '<div class="text-xs text-slate-400 py-1">' + escapeHtml(t('admin_resources_none')) + '</div>';
            manage.innerHTML =
                '<div class="pt-1 pb-2">' +
                '<div class="flex gap-2 items-center pb-2">' +
                '<input type="text" class="agent-input resource-kind-search" placeholder="' + escapeHtml(t('admin_resource_search_placeholder')) + '" value="' + escapeHtml(st.q || '') + '">' +
                '<button type="button" class="admin-row-btn resource-kind-clear">' + escapeHtml(t('admin_resource_clear')) + '</button>' +
                '</div>' +
                '<div class="grid grid-cols-2 gap-x-2 resource-kind-list">' + listHtml + '</div>' +
                '<div class="flex items-center justify-between pt-2">' +
                '<button type="button" class="admin-row-btn resource-kind-selectall">' + escapeHtml(t('admin_resource_selectall')) + '</button>' +
                '<span class="text-xs text-slate-400">' + escapeHtml(t('admin_total_label')) +
                ' <span class="font-medium">' + total + '</span></span>' +
                '</div>' +
                '<div class="resource-kind-pagination pt-1"></div>' +
                '</div>';
            // Wire search (reset to page 1).
            const search = manage.querySelector('.resource-kind-search');
            search.addEventListener('input', function () {
                st.q = search.value;
                st.page = 1;
                markModalDirty();
                clearTimeout(this._deb);
                this._deb = setTimeout(function () { _renderResourceList(kind); }, 250);
            });
            search.addEventListener('keyup', function (e) { if (e.key === 'Enter') { st.page = 1; _renderResourceList(kind); } });
            // Wire select-all (this page).
            manage.querySelector('.resource-kind-selectall').addEventListener('click', function () {
                items.forEach(function (r) { st.selected.add(r.resource_id); });
                _afterResourceSelectChanged(kind);
            });
            // Wire clear (deselect all for this kind).
            manage.querySelector('.resource-kind-clear').addEventListener('click', function () {
                st.selected.clear();
                _afterResourceSelectChanged(kind);
            });
            // Wire each checkbox.
            manage.querySelectorAll('.resource-kind-list input[type=checkbox]').forEach(function (cb) {
                cb.addEventListener('change', function () {
                    if (cb.checked) st.selected.add(cb.value);
                    else st.selected.delete(cb.value);
                    _afterResourceSelectChanged(kind);
                });
            });
            // Pagination.
            renderPagination(manage.querySelector('.resource-kind-pagination'), st.page, st.pageSize, total, function (p) {
                st.page = p;
                _renderResourceList(kind);
            });
        } catch (e) {
            manage.innerHTML = '<div class="py-1 text-xs text-red-500">' + escapeHtml(e.message || t('load_error')) + '</div>';
        }
    }

    function _updateResourceSummary(kind) {
        const row = _resourceKindRowFind(kind);
        if (!row) return;
        const n = _resCount(kind);
        const summaryEl = row.querySelector('.resource-kind-summary');
        if (summaryEl) {
            summaryEl.textContent = n ? t('admin_resources_selected').replace('{n}', n) : t('admin_resources_none');
        }
    }

    function _afterResourceSelectChanged(kind) {
        _updateResourceSummary(kind);
        markModalDirty();
        if (kind === 'model') {
            // Rebuild the model-default capability options from the new set.
            _refreshModelDefaultOptions();
        }
        _renderResourceList(kind);
    }

    function _collectResourceGrants() {
        const grants = [];
        _resourceKinds.forEach(function (k) {
            const st = _resourceState[k];
            if (!st || !st.selected.size) return;
            const actions = st.actions || _resourceActions[k] || [];
            st.selected.forEach(function (rid) {
                actions.forEach(function (a) {
                    grants.push({ resource_kind: k, resource_id: rid, action: a });
                });
            });
        });
        return grants;
    }

    function modelDefaultsHtml(f) {
        const id = 'adm-fld-modeldefaults';
        // Build model options from the currently selected model resources.
        const modelSel = (_resourceState && _resourceState.model && _resourceState.model.selected) ? _resourceState.model.selected : new Set();
        const options = Array.from(modelSel).map(function (rid) { return { value: rid, label: rid }; });
        const rows = _modelCapabilities.map(function (cap) {
            const val = _modelDefaultSel[cap] || '';
            const opts = '<option value="">' + escapeHtml(t('admin_resources_none')) + '</option>' +
                options.map(function (o) {
                    return '<option value="' + escapeHtml(o.value) + '"' + (String(o.value) === val ? ' selected' : '') + '>' +
                        escapeHtml(o.label) + '</option>';
                }).join('');
            return '<div class="flex items-center gap-2 py-1">' +
                '<span class="w-32 text-xs text-slate-500 dark:text-slate-400">' + escapeHtml(cap) + '</span>' +
                '<select class="agent-input model-default-select" data-cap="' + escapeHtml(cap) + '">' + opts + '</select>' +
                '</div>';
        }).join('');
        return '<div id="' + id + '" class="space-y-1">' +
            '<div class="text-xs text-slate-400 mb-1">' + escapeHtml(t('admin_resource_model_capabilities')) + '</div>' +
            rows + '</div>';
    }

    function _collectModelDefaults() {
        const out = {};
        _modelCapabilities.forEach(function (cap) {
            const v = _modelDefaultSel[cap];
            if (v) out[cap] = v;
        });
        return out;
    }

    // ---- tenant-level model grant picker (edit-tenant modal) --------------
    function tenantModelGrantHtml(f) {
        // Renders a search + paged checkbox list of the platform all-mode model
        // catalog. Selections are tracked in _tenantGrantSel and emitted as
        // tenant_resource_grants entries ({resource_kind:model, read+use}).
        const id = 'adm-fld-' + f.name;
        _tenantGrantApiBase = f.apiBase || '';
        _tenantGrantResourcesBase = f.resourcesBase || '';
        _tenantGrantVersion = f.version || 0;
        // Seed selection from the field value (existing granted model ids).
        _tenantGrantSel = new Set();
        (f.value || []).forEach(function (g) {
            if (g && g.resource_kind === 'model' && g.resource_id) _tenantGrantSel.add(g.resource_id);
        });
        _tenantGrantLoaded = false;
        const label = f.label ? '<div class="text-sm font-medium text-slate-700 dark:text-slate-200 mb-1">' + escapeHtml(f.label) + '</div>' : '';
        return '<div id="' + id + '" class="tenant-model-grant">' +
            label +
            '<div class="flex gap-2 items-center pb-2">' +
            '<input type="text" class="agent-input resource-kind-search" placeholder="' + escapeHtml(t('admin_resource_search_placeholder')) + '">' +
            '<button type="button" class="admin-row-btn resource-kind-clear">' + escapeHtml(t('admin_resource_clear')) + '</button>' +
            '</div>' +
            '<div class="grid grid-cols-2 gap-x-2 resource-kind-list">' +
            '<div class="py-1 text-xs text-slate-400 col-span-2">' + escapeHtml(t('admin_loading')) + '</div>' +
            '</div>' +
            '<div class="flex items-center justify-between pt-2">' +
            '<button type="button" class="admin-row-btn resource-kind-selectall">' + escapeHtml(t('admin_resource_selectall')) + '</button>' +
            '<span class="text-xs text-slate-400"><span class="resource-kind-selcount font-medium"></span></span>' +
            '</div>' +
            '<div class="resource-kind-pagination pt-1"></div>' +
            '</div>';
    }

    function _collectTenantModelGrants() {
        const grants = [];
        _tenantGrantSel.forEach(function (rid) {
            grants.push({ resource_kind: 'model', resource_id: rid, action: 'read' });
            grants.push({ resource_kind: 'model', resource_id: rid, action: 'use' });
        });
        return grants;
    }

    async function _initTenantModelGrant(node) {
        if (!_tenantGrantApiBase) return;
        const list = node.querySelector('.resource-kind-list');
        const search = node.querySelector('.resource-kind-search');
        const selCount = node.querySelector('.resource-kind-selcount');
        if (!list || !search) return;
        let q = '';
        let page = 1;
        const pageSize = 12;
        function updateCount() {
            if (selCount) selCount.textContent = t('admin_resources_selected').replace('{n}', _tenantGrantSel.size);
        }
        async function render() {
            const query = qs({ kind: 'model', q: q, page: page, page_size: pageSize });
            try {
                const data = await apiFetch(_tenantGrantApiBase + '?' + query);
                _tenantGrantCatalog = data.items || [];
                const total = data.total || 0;
                if (!_tenantGrantCatalog.length) {
                    list.innerHTML = '<div class="py-1 text-xs text-slate-400 col-span-2">' + escapeHtml(t('admin_resources_none')) + '</div>';
                } else {
                    list.innerHTML = _tenantGrantCatalog.map(function (r) {
                        const checked = _tenantGrantSel.has(r.resource_id);
                        const idSuffix = r.resource_id.indexOf('provider:') === 0 ? r.resource_id : '';
                        return '<label class="inline-flex items-start gap-2 text-xs text-slate-600 dark:text-slate-300 py-1 px-1.5 rounded hover:bg-slate-50 dark:hover:bg-white/5 cursor-pointer">' +
                            '<input type="checkbox" value="' + escapeHtml(r.resource_id) + '"' + (checked ? ' checked' : '') + '>' +
                            '<span class="flex flex-col leading-tight"><span class="truncate">' + escapeHtml(r.name) + '</span>' +
                            (idSuffix && idSuffix !== r.name ? '<span class="text-[10px] text-slate-400 dark:text-slate-500 truncate">' + escapeHtml(idSuffix) + '</span>' : '') +
                            '</span></label>';
                    }).join('');
                }
                list.querySelectorAll('input[type=checkbox]').forEach(function (cb) {
                    cb.addEventListener('change', function () {
                        if (cb.checked) _tenantGrantSel.add(cb.value);
                        else _tenantGrantSel.delete(cb.value);
                        markModalDirty();
                        updateCount();
                    });
                });
                const pag = node.querySelector('.resource-kind-pagination');
                renderPagination(pag, page, pageSize, total, function (p) { page = p; render(); });
            } catch (e) {
                list.innerHTML = '<div class="py-1 text-xs text-red-500 col-span-2">' + escapeHtml(e.message || t('load_error')) + '</div>';
            }
        }
        function setSearch(v) {
            q = v; page = 1;
        }
        search.addEventListener('input', function () {
            setSearch(search.value);
            clearTimeout(this._deb);
            this._deb = setTimeout(render, 250);
        });
        search.addEventListener('keyup', function (e) { if (e.key === 'Enter') { setSearch(search.value); render(); } });
        node.querySelector('.resource-kind-clear').addEventListener('click', function () {
            _tenantGrantSel.clear();
            markModalDirty();
            updateCount();
            render();
        });
        node.querySelector('.resource-kind-selectall').addEventListener('click', function () {
            (_tenantGrantCatalog || []).forEach(function (r) { _tenantGrantSel.add(r.resource_id); });
            markModalDirty();
            updateCount();
            render();
        });
        updateCount();
        await render();
    }


    function _initResourceGroup(node) {
        // Bind the kind toggle (expand/collapse the management area).
        node.querySelectorAll('.resource-kind-toggle').forEach(function (toggle) {
            toggle.addEventListener('click', function () {
                const row = toggle.closest('.resource-kind-row');
                const kind = row && row.getAttribute('data-kind');
                const manage = row && row.querySelector('.resource-kind-manage');
                if (!kind) return;
                if (manage.classList.contains('hidden')) {
                    _openResourceManage(kind);
                } else {
                    _closeResourceManage(kind);
                }
            });
        });
    }

    function _refreshModelDefaultOptions() {
        const node = document.getElementById('adm-fld-modeldefaults');
        if (!node) return;
        const modelSel = (_resourceState.model && _resourceState.model.selected) ? _resourceState.model.selected : new Set();
        const options = Array.from(modelSel).map(function (rid) { return { value: rid, label: rid }; });
        node.querySelectorAll('.model-default-select').forEach(function (sel) {
            const cur = sel.value;
            sel.innerHTML = '<option value="">' + escapeHtml(t('admin_resources_none')) + '</option>' +
                options.map(function (o) {
                    return '<option value="' + escapeHtml(o.value) + '"' + (String(o.value) === cur ? ' selected' : '') + '>' +
                        escapeHtml(o.label) + '</option>';
                }).join('');
        });
    }

    function _initModelDefaults(node) {
        node.querySelectorAll('.model-default-select').forEach(function (sel) {
            sel.addEventListener('change', function () {
                const cap = sel.getAttribute('data-cap');
                const v = sel.value;
                if (v) _modelDefaultSel[cap] = v;
                else delete _modelDefaultSel[cap];
                markModalDirty();
            });
        });
    }

    function collectField(f) {
        if (f.type === 'resourcegroup') {
            return _collectResourceGrants();
        }
        if (f.type === 'modeldefaults') {
            return _collectModelDefaults();
        }
        if (f.type === 'modelgrant') {
            return _collectTenantModelGrants();
        }
        const el = document.getElementById('adm-fld-' + f.name);
        if (!el) return undefined;
        if (f.type === 'checkbox') return !!el.checked;
        if (f.type === 'multi') {
            return Array.prototype.slice.call(el.querySelectorAll('input:checked')).map(function (i) { return i.value; });
        }
        return el.value;
    }

    function openAdminModal(cfg) {
        const el = ensureAdminModal();
        document.getElementById('admin-modal-title').textContent = cfg.title || '';
        document.getElementById('admin-modal-subtitle').textContent = cfg.subtitle || '';
        document.getElementById('admin-modal-icon').className = 'fas ' + (cfg.icon || 'fa-plus') + ' text-primary-500';
        const submitBtn = document.getElementById('admin-modal-submit');
        submitBtn.textContent = cfg.submitLabel || t('save');
        submitBtn.disabled = false;
        document.getElementById('admin-modal-cancel').textContent = t('cancel');
        _adminModal = {
            open: true, dirty: false,
            fields: cfg.fields || [],
            submit: cfg.submit || null,
            onConflictReload: cfg.onConflictReload || null,
            statusEl: cfg.statusEl || null,
            successMsg: cfg.successMsg || t('admin_saved'),
        };
        const body = document.getElementById('admin-modal-body');
        body.innerHTML = (cfg.fields || []).map(fieldHtml).join('');
        closeAdminErr();
        (cfg.fields || []).forEach(function (f) {
            // The modal-control container id is stamped by the field renderer.
            // resourcegroup uses `adm-fld-resource_grants` (matches f.name), while
            // modeldefaults renders `adm-fld-modeldefaults`; resolve the id the
            // same way the renderer did so _initModelDefaults can bind.
            const nodeId = f.type === 'modeldefaults' ? 'adm-fld-modeldefaults' : ('adm-fld-' + f.name);
            const node = document.getElementById(nodeId);
            if (!node) return;
            node.addEventListener('input', markModalDirty);
            if (f.type === 'multi') {
                node.querySelectorAll('input[type=checkbox]').forEach(function (cb) { cb.addEventListener('change', markModalDirty); });
            } else {
                node.addEventListener('change', markModalDirty);
            }
            if (f.type === 'resourcegroup') {
                _initResourceGroup(node);
            } else if (f.type === 'modeldefaults') {
                _initModelDefaults(node);
            } else if (f.type === 'modelgrant') {
                _initTenantModelGrant(node);
            }
        });
        el.classList.remove('hidden');
        const first = body.querySelector('input, textarea, select');
        if (first) { try { first.focus(); } catch (e) {} }
    }

    function markModalDirty() { _adminModal.dirty = true; }

    function closeAdminErr() {
        const el = document.getElementById('admin-modal-error');
        if (el) { el.classList.add('hidden'); el.textContent = ''; }
    }

    function showAdminErr(msg) {
        const el = document.getElementById('admin-modal-error');
        if (el) { el.textContent = msg || ''; el.classList.remove('hidden'); }
    }

    function closeAdminModal() {
        if (_adminModal.dirty && !confirmDiscard(true)) return;
        closeAdminModalNoPrompt();
    }

    function closeAdminModalNoPrompt() {
        const el = document.getElementById('admin-modal');
        if (el) el.classList.add('hidden');
        _adminModal = { open: false, dirty: false, fields: [], submit: null, onConflictReload: null, statusEl: null, successMsg: '' };
    }

    async function submitAdminModal() {
        const cfg = _adminModal;
        if (!cfg.submit) return;
        const body = {};
        cfg.fields.forEach(function (f) {
            if (f.type === 'locked') return;
            body[f.name] = collectField(f);
        });
        for (let i = 0; i < cfg.fields.length; i++) {
            const f = cfg.fields[i];
            if (!f.required) continue;
            const v = body[f.name];
            if (v == null || v === '' || (Array.isArray(v) && v.length === 0)) {
                showAdminErr(t('admin_required_field'));
                return;
            }
        }
        const btn = document.getElementById('admin-modal-submit');
        btn.disabled = true;
        closeAdminErr();
        try {
            await cfg.submit(body);
            closeAdminModalNoPrompt();
            if (cfg.statusEl) status(cfg.statusEl, cfg.successMsg, true);
        } catch (err) {
            if (err.status === 409 || err.code === 'conflict') {
                showAdminErr(err.message || t('admin_conflict'));
                if (cfg.onConflictReload) cfg.onConflictReload();
                closeAdminModalNoPrompt();
                if (cfg.statusEl) status(cfg.statusEl, err.message || t('admin_conflict'), false);
            } else if (err.status === 401) {
                showAdminErr(err.message || t('account_credentials_error'));
            } else {
                showAdminErr((err.data && err.data.message) || err.message || t('admin_save_failed'));
            }
        } finally {
            btn.disabled = false;
        }
    }

    // ---- option builders -------------------------------------------------
    function roleOptions(roles) {
        return (roles || []).map(function (r) {
            return { value: r.code, label: r.name + ' (' + r.code + ')' };
        });
    }

    function deptOptions(depts, excludeId) {
        const opts = [];
        let rootId = '';
        (depts || []).forEach(function (d) {
            if (d.code === '__root__') { rootId = d.id; return; }
            if (excludeId && d.id === excludeId) return;
        });
        opts.push({ value: rootId, label: t('admin_field_parent_none') });
        (depts || []).forEach(function (d) {
            if (d.code === '__root__') return;
            if (excludeId && d.id === excludeId) return;
            opts.push({ value: d.id, label: d.name });
        });
        return opts;
    }

    function memberDeptOptions(depts) {
        const opts = [{ value: '', label: t('admin_field_department_none') }];
        (depts || []).forEach(function (d) {
            if (d.code === '__root__') return;
            opts.push({ value: d.id, label: d.name });
        });
        return opts;
    }

    function permOptions(perms) {
        return (perms || []).map(function (p) {
            if (typeof p === 'string') return { value: p, label: p };
            return { value: p.id, label: p.label || p.id };
        });
    }

    // Group the permission catalog by its `group` field (task 5.5).
    function permGroups(catalog) {
        const groups = {};
        (catalog || []).forEach(function (p) {
            const g = p.group || '';
            (groups[g] = groups[g] || []).push(p);
        });
        return groups;
    }

    async function fetchRoles() {
        try {
            const d = await apiFetch('/api/tenant/roles');
            _roles = d.items || [];
            _roleById = {};
            _roles.forEach(function (r) { _roleById[r.id] = r; });
            return _roles;
        } catch (e) { _roles = []; _roleById = {}; return []; }
    }

    async function fetchDepts() {
        try {
            const d = await apiFetch('/api/tenant/departments');
            _depts = d.items || [];
            _deptById = {};
            _depts.forEach(function (x) { _deptById[x.id] = x; });
            return _depts;
        } catch (e) { _depts = []; _deptById = {}; return []; }
    }

    async function ensurePermCatalog() {
        if (_permCatalog) return _permCatalog;
        try {
            const d = await apiFetch('/api/tenant/permissions');
            if (!Array.isArray(d.permissions) || !d.permissions.length) {
                throw new Error('invalid-permission-catalog');
            }
            // Build into a local first; only cache a fully-valid catalog so a
            // transient/invalid response never becomes a retryable empty state
            // that would let a modal open without real permission choices.
            const parsed = d.permissions.map(function (p) {
                if (typeof p === 'string') return { id: p, label: p, group: '' };
                return {
                    id: p.id || '',
                    label: p.label || p.id,
                    group: p.group || '',
                    description: p.description || '',
                    scope: p.scope || '',
                    assignable: p.assignable !== false,
                };
            });
            if (parsed.some(function (p) { return !p.id || !p.id.trim(); })) {
                throw new Error('invalid-permission-catalog');
            }
            _permCatalog = parsed;
        } catch (e) {
            if (e.message !== 'stale-response') {
                status(document.getElementById('role-status'), t('admin_permissions_load_failed'), false);
            }
            return null;
        }
        return _permCatalog;
    }

    // ---- pagination control ----------------------------------------------
    function renderPagination(container, page, pageSize, total, onPage) {
        if (!container) return;
        if (!total || total <= pageSize) {
            container.innerHTML = '';
            return;
        }
        const pages = Math.ceil(total / pageSize);
        const prev = '<button class="admin-row-btn" data-pg="' + (page - 1) + '"' + (page <= 1 ? ' disabled style="opacity:.4"' : '') + '><i class="fas fa-chevron-left mr-1"></i>' + escapeHtml(t('admin_prev')) + '</button>';
        const next = '<button class="admin-row-btn" data-pg="' + (page + 1) + '"' + (page >= pages ? ' disabled style="opacity:.4"' : '') + '>' + escapeHtml(t('admin_next')) + '<i class="fas fa-chevron-right ml-1"></i></button>';
        container.innerHTML = '<div class="text-xs text-slate-400">' +
            escapeHtml(t('admin_total_label')) + ' <span class="font-semibold text-slate-600 dark:text-slate-300">' + total + '</span>' +
            (pages > 1 ? ' · ' + escapeHtml(t('admin_page_label')) + ' ' + page + '/' + pages : '') +
            '</div><div class="flex gap-2">' + prev + next + '</div>';
        container.querySelectorAll('[data-pg]').forEach(function (btn) {
            btn.addEventListener('click', function () { onPage(parseInt(btn.dataset.pg, 10)); });
        });
    }

    // ---- helper to know if the account is a platform admin ----------------
    // The self profile (/auth/me) is authoritative. We cache it once so the
    // admin views can decide whether to render platform-only controls; the
    // backend still independently enforces every authorization.
    let _selfProfile = null;
    async function isPlatformAdmin() {
        if (_selfProfile) return !!_selfProfile.user?.is_platform_admin;
        try {
            const resp = await fetch('/auth/me', { credentials: 'same-origin', cache: 'no-store' });
            const data = await resp.json().catch(() => ({}));
            if (data && data.status === 'success' && data.user) {
                _selfProfile = data;
                return !!data.user.is_platform_admin;
            }
            return false;
        } catch (e) { return false; }
    }

    // ---- Tenant view ------------------------------------------------------
    async function loadTenantView() {
        const list = document.getElementById('tenant-list');
        const btn = document.getElementById('tenant-create-btn');
        const empty = document.getElementById('tenant-empty');
        if (!list) return;
        // Ordinary members with only tenant.info.read must NOT request the
        // platform tenant list (server rejects 403); route them to the read-only
        // current-tenant profile instead.
        if (!(await isPlatformAdmin())) {
            await loadTenantReadOnly();
            return;
        }
        list.innerHTML = '<div class="text-sm text-slate-400 dark:text-slate-500">' + t('tenant_loading') + '</div>';
        try {
            const searchEl = document.getElementById('tenant-search');
            const q = searchEl ? searchEl.value : '';
            const data = await apiFetch('/api/platform/tenants' + (q ? '?' + qs({ q: q }) : ''));
            if (!data.items || !data.items.length) {
                list.innerHTML = '';
                empty.classList.remove('hidden');
                btn.classList.add('hidden');
                return;
            }
            empty.classList.add('hidden');
            btn.classList.remove('hidden');
            _tenantById = {};
            const rows = data.items.map(function (tn) {
                _tenantById[tn.id] = tn;
                return '<div class="flex items-center justify-between px-4 py-3 rounded-lg border border-slate-200 dark:border-white/10">'
                    + '<div class="flex items-center gap-3"><div class="w-9 h-9 rounded-lg bg-primary-50 dark:bg-primary-900/30 flex items-center justify-center">'
                    + '<i class="fas fa-building text-primary-500"></i></div>'
                    + '<div><div class="text-sm font-medium text-slate-800 dark:text-slate-100">' + escapeHtml(tn.name) + '</div>'
                    + '<div class="text-xs text-slate-400">' + escapeHtml(tn.code) + ' · ' + fmtActive(tn.active) + '</div></div></div>'
                    + '<div class="flex items-center gap-2"><div class="text-xs text-slate-400">' + t('tenant_version_label') + ' ' + tn.version + '</div>'
                    + '<button class="admin-row-btn" onclick="adminRowAction(\'tenant\',\'edit\',\'' + escapeHtml(tn.id) + '\')"><i class="fas fa-pen mr-1"></i>' + escapeHtml(t('admin_edit')) + '</button>'
                    + '<button class="admin-row-btn" onclick="adminRowAction(\'tenant\',\'roles\',\'' + escapeHtml(tn.id) + '\')"><i class="fas fa-users-cog mr-1"></i>' + escapeHtml(t('admin_tenant_roles')) + '</button>'
                    + '<button class="admin-row-btn" onclick="adminRowAction(\'tenant\',\'admin\',\'' + escapeHtml(tn.id) + '\')"><i class="fas fa-user-shield mr-1"></i>' + escapeHtml(t('admin_tenant_admin')) + '</button>' + '</div></div>';
                    + '</div></div>';
            }).join('');
            list.innerHTML = rows;
        } catch (err) {
            if (err.message === 'stale-response') return;
            list.innerHTML = '<div class="text-sm text-red-500">' + t('load_error') + ': ' + escapeHtml(err.message) + '</div>';
            status(document.getElementById('tenant-status'), err.message, false);
        }
    }

    // Read-only current-tenant profile for members with tenant.info.read.
    async function loadTenantReadOnly() {
        const list = document.getElementById('tenant-list');
        const btn = document.getElementById('tenant-create-btn');
        const empty = document.getElementById('tenant-empty');
        const searchEl = document.getElementById('tenant-search');
        if (searchEl) searchEl.classList.add('hidden');
        if (btn) btn.classList.add('hidden');
        if (!list) return;
        list.innerHTML = '<div class="text-sm text-slate-400 dark:text-slate-500">' + t('tenant_loading') + '</div>';
        try {
            const data = await apiFetch('/api/tenant');
            const tn = data.tenant || {};
            if (empty) empty.classList.add('hidden');
            list.innerHTML = '<div class="px-4 py-4 rounded-lg border border-slate-200 dark:border-white/10">'
                + '<div class="flex items-center gap-3"><div class="w-10 h-10 rounded-lg bg-primary-50 dark:bg-primary-900/30 flex items-center justify-center">'
                + '<i class="fas fa-building text-primary-500"></i></div>'
                + '<div><div class="text-sm font-medium text-slate-800 dark:text-slate-100">' + escapeHtml(tn.name) + '</div>'
                + '<div class="text-xs text-slate-400">' + escapeHtml(tn.code) + ' · ' + fmtActive(tn.active) + '</div></div></div>'
                + '</div>';
        } catch (err) {
            if (err.message === 'stale-response') return;
            list.innerHTML = '<div class="text-sm text-red-500">' + t('load_error') + ': ' + escapeHtml(err.message) + '</div>';
            status(document.getElementById('tenant-status'), err.message, false);
        }
    }

    async function openTenantCreate() {
        // shared_root is server-controlled (task 5.4): the form only carries the
        // fields the handler needs; the server generates the directory.
        openAdminModal({
            title: t('tenant_create'),
            icon: 'fa-plus',
            fields: [
                { name: 'code', label: t('admin_field_code'), type: 'text', required: true, hint: t('admin_field_code_hint'), inline: true },
                { name: 'name', label: t('admin_field_name'), type: 'text', required: true, inline: true },
                { name: 'admin_username', label: t('admin_field_admin_username'), type: 'text', required: true, inline: true },
                { name: 'admin_display', label: t('admin_field_admin_display'), type: 'text', inline: true },
                { name: 'admin_password', label: t('admin_field_admin_password'), type: 'password', required: true, hint: t('admin_field_password_hint') },
                { name: 'recent_password', label: t('admin_field_recent_password'), type: 'password', required: true, hint: t('admin_field_recent_password_hint') },
            ],
            submitLabel: t('admin_create'),
            statusEl: document.getElementById('tenant-status'),
            submit: async function (body) {
                await apiFetch('/api/platform/tenants', { method: 'POST', body: body });
                await loadTenantView();
            },
            onConflictReload: function () { loadTenantView(); },
        });
    }

    async function openTenantEdit(id) {
        const tn = _tenantById[id];
        if (!tn) return;
        // Name + status are separate operations (task 5.4). Status form surfaces
        // the last-admin/version-context; name form is a plain rename. We also
        // load the tenant's current model grants so the platform admin can
        // re-allocate which models this tenant may assign to roles.
        let currentGrants = [];
        let grantVersion = tn.version || 0;
        try {
            const res = await apiFetch('/api/platform/tenants/' + encodeURIComponent(id) + '/resources');
            currentGrants = (res && res.grants) || [];
            grantVersion = tn.version || 0;
        } catch (e) {
            // Best-effort: if grants can't be loaded, show an empty picker so the
            // edit still works for name/status.
            currentGrants = [];
        }
        const catalogBase = '/api/platform/tenants/' + encodeURIComponent(id) + '/authorization/catalog';
        const resourcesBase = '/api/platform/tenants/' + encodeURIComponent(id) + '/resources';
        openAdminModal({
            title: t('tenant_edit_title'),
            subtitle: tn.code || '',
            icon: 'fa-pen',
            fields: [
                { name: 'name', label: t('admin_field_name'), type: 'text', value: tn.name, required: true },
                { name: 'active', label: t('admin_field_active'), type: 'checkbox', value: !!tn.active },
                { name: 'model_grants', label: t('admin_field_model_grants'), type: 'modelgrant',
                  value: currentGrants, apiBase: catalogBase, resourcesBase: resourcesBase,
                  version: grantVersion, hint: t('admin_field_model_grants_hint') },
                { name: 'recent_password', label: t('admin_field_recent_password'), type: 'password', required: true, hint: t('admin_field_recent_password_hint') },
            ],
            submitLabel: t('admin_save'),
            statusEl: document.getElementById('tenant-status'),
            submit: async function (body) {
                const modelGrants = body.model_grants || [];
                delete body.model_grants;
                body.expected_version = grantVersion;
                const res = await apiFetch('/api/platform/tenants/' + encodeURIComponent(id), { method: 'POST', body: body });
                // PUT the tenant's model grants (replaces wholesale). The rename
                // /status POST increments version by exactly one, so use that
                // (the handler response carries no version field).
                const newVersion = grantVersion + 1;
                await apiFetch(resourcesBase, {
                    method: 'PUT',
                    body: { grants: modelGrants, expected_version: newVersion },
                });
                await loadTenantView();
            },
            onConflictReload: function () { loadTenantView(); },
        });
    }

    function openTenantAdmin(id) {
        const tn = _tenantById[id];
        if (!tn) return;
        openAdminModal({
            title: t('admin_tenant_admin_edit'),
            subtitle: (tn.code || '') + ' · ' + (tn.name || ''),
            icon: 'fa-user-shield',
            fields: [
                { name: 'user_id', label: t('admin_field_admin_user_id'), type: 'text', required: true, hint: t('admin_field_admin_user_id_hint') },
                { name: 'display_name', label: t('admin_field_admin_display'), type: 'text' },
                { name: 'recent_password', label: t('admin_field_recent_password'), type: 'password', required: true, hint: t('admin_field_recent_password_hint') },
            ],
            submitLabel: t('admin_save'),
            statusEl: document.getElementById('tenant-status'),
            submit: async function (body) {
                await apiFetch('/api/platform/tenants/' + encodeURIComponent(id) + '/admins', { method: 'POST', body: body });
                await loadTenantView();
            },
            onConflictReload: function () { loadTenantView(); },
        });
    }

    // ---- Members (Users) view --------------------------------------------
    async function loadMembersView() {
        const list = document.getElementById('member-list');
        const btn = document.getElementById('member-create-btn');
        if (!list) return;
        // Refresh filter dropdowns (roles + depts) once.
        await refreshMemberFilters();
        list.innerHTML = '<div class="text-sm text-slate-400 dark:text-slate-500">' + t('tenant_loading') + '</div>';
        const f = _memberFilters;
        f.q = (document.getElementById('member-search') || {}).value || '';
        f.status = (document.getElementById('member-status-filter') || {}).value || '';
        f.role = (document.getElementById('member-role-filter') || {}).value || '';
        f.department_id = (document.getElementById('member-dept-filter') || {}).value || '';
        const query = qs({
            q: f.q, status: f.status, role: f.role,
            department_id: f.department_id,
            page: _memberPage, page_size: _memberPageSize,
        });
        try {
            const data = await apiFetch('/api/tenant/members?' + query);
            if (!data.items || !data.items.length) {
                list.innerHTML = '<div class="text-sm text-slate-400">' + t('member_empty') + '</div>';
                btn.classList.add('hidden');
                renderPagination(document.getElementById('member-pagination'), _memberPage, _memberPageSize, data.total || 0, function (p) { _memberPage = p; loadMembersView(); });
                return;
            }
            btn.classList.remove('hidden');
            _memberById = {};
            const rolesByCode = {};
            _roles.forEach(function (r) { rolesByCode[r.code] = r; });
            const platformAdmin = await isPlatformAdmin();
            const rows = data.items.map(function (m) {
                _memberById[m.id] = m;
                const roleNames = (m.role_codes || []).map(function (c) {
                    return (rolesByCode[c] && rolesByCode[c].name) || c;
                }).join(', ');
                return '<div class="flex items-center justify-between px-4 py-3 rounded-lg border border-slate-200 dark:border-white/10">'
                    + '<div class="flex items-center gap-3"><div class="w-9 h-9 rounded-lg bg-indigo-50 dark:bg-indigo-900/30 flex items-center justify-center">'
                    + '<i class="fas fa-user text-indigo-500"></i></div>'
                    + '<div><div class="text-sm font-medium text-slate-800 dark:text-slate-100">' + escapeHtml(m.display_name) + '</div>'
                    + '<div class="text-xs text-slate-400">' + escapeHtml(m.username) + ' · ' + fmtActive(m.active) + (roleNames ? ' · ' + escapeHtml(roleNames) : '') + '</div></div></div>'
                    + '<div class="flex items-center gap-2"><div class="text-xs text-slate-400">' + escapeHtml(m.position_text || '') + '</div>'
                    + '<button class="admin-row-btn" onclick="adminRowAction(\'member\',\'edit\',\'' + escapeHtml(m.id) + '\')"><i class="fas fa-pen mr-1"></i>' + escapeHtml(t('admin_edit')) + '</button>'
                    + (platformAdmin && m.user_id
                        ? '<button class="admin-row-btn" onclick="adminRowAction(\'member\',\'reset\',\'' + escapeHtml(m.id) + '\')"><i class="fas fa-key mr-1"></i>' + escapeHtml(t('admin_reset')) + '</button>'
                        : '')
                    + '</div></div>';
            }).join('');
            list.innerHTML = rows;
            renderPagination(document.getElementById('member-pagination'), _memberPage, _memberPageSize, data.total || 0, function (p) { _memberPage = p; loadMembersView(); });
        } catch (err) {
            if (err.message === 'stale-response') return;
            list.innerHTML = '<div class="text-sm text-red-500">' + t('load_error') + ': ' + escapeHtml(err.message) + '</div>';
            if (err.status === 403) status(document.getElementById('member-status'), err.message, false);
        }
    }

    async function refreshMemberFilters() {
        const roles = await fetchRoles();
        const depts = await fetchDepts();
        const roleSel = document.getElementById('member-role-filter');
        if (roleSel) {
            const cur = roleSel.value;
            const all = '<option value="" data-i18n="filter_all">' + escapeHtml(t('filter_all')) + '</option>';
            roleSel.innerHTML = all + roles.map(function (r) {
                return '<option value="' + escapeHtml(r.code) + '"' + (r.code === cur ? ' selected' : '') + '>' + escapeHtml(r.name) + '</option>';
            }).join('');
        }
        const deptSel = document.getElementById('member-dept-filter');
        if (deptSel) {
            const cur = deptSel.value;
            const all = '<option value="" data-i18n="filter_all">' + escapeHtml(t('filter_all')) + '</option>';
            deptSel.innerHTML = all + depts.filter(function (d) { return d.code !== '__root__'; }).map(function (d) {
                return '<option value="' + escapeHtml(d.id) + '"' + (d.id === cur ? ' selected' : '') + '>' + escapeHtml(d.name) + '</option>';
            }).join('');
        }
    }

    async function openMemberCreate() {
        const roles = await fetchRoles();
        const depts = await fetchDepts();
        openAdminModal({
            title: t('member_create'),
            icon: 'fa-plus',
            fields: [
                { name: 'username', label: t('admin_field_username'), type: 'text', required: true, hint: t('admin_field_username_hint'), inline: true },
                { name: 'display_name', label: t('admin_field_display_name'), type: 'text', required: true, inline: true },
                { name: 'temporary_password', label: t('admin_field_temp_password'), type: 'password', required: true, hint: t('admin_field_password_hint') },
                { name: 'roles', label: t('admin_field_roles'), type: 'multi', options: roleOptions(roles), value: ['member'], hint: t('admin_field_roles_hint') },
                { name: 'department_id', label: t('admin_field_department'), type: 'select', options: memberDeptOptions(depts), hint: t('admin_field_department_hint') },
                { name: 'position_text', label: t('admin_field_position'), type: 'text' },
            ],
            submitLabel: t('admin_create'),
            statusEl: document.getElementById('member-status'),
            submit: async function (body) {
                body.operation = 'create-new';
                body.department_id = body.department_id || null;
                await apiFetch('/api/tenant/members', { method: 'POST', body: body });
                await loadMembersView();
            },
            onConflictReload: function () { loadMembersView(); },
        });
    }

    async function openMemberEdit(id) {
        const m = _memberById[id];
        if (!m) return;
        const roles = await fetchRoles();
        const depts = await fetchDepts();
        openAdminModal({
            title: t('member_edit_title'),
            subtitle: m.username || '',
            icon: 'fa-pen',
            fields: [
                { name: 'username', label: t('admin_field_username'), type: 'locked', value: m.username },
                { name: 'display_name', label: t('admin_field_display_name'), type: 'text', value: m.display_name, required: true },
                { name: 'active', label: t('admin_field_active'), type: 'checkbox', value: !!m.active },
                { name: 'roles', label: t('admin_field_roles'), type: 'multi', options: roleOptions(roles), value: m.role_codes || [], hint: t('admin_field_roles_edit_hint') },
                { name: 'department_id', label: t('admin_field_department'), type: 'select', options: memberDeptOptions(depts), value: m.department_id || '' },
                { name: 'position_text', label: t('admin_field_position'), type: 'text', value: m.position_text || '' },
            ],
            submitLabel: t('admin_save'),
            statusEl: document.getElementById('member-status'),
            submit: async function (body) {
                body.expected_version = m.version;
                body.department_id = body.department_id || null;
                // Task 3.2: only submit `roles` when the selection actually
                // differs from the current role_codes; omitting preserves.
                const current = (m.role_codes || []).slice().sort().join(',');
                const next = (body.roles || []).slice().sort().join(',');
                if (current === next) delete body.roles;
                await apiFetch('/api/tenant/members/' + encodeURIComponent(id), { method: 'POST', body: body });
                await loadMembersView();
            },
            onConflictReload: function () { loadMembersView(); },
        });
    }

    async function resetMemberPassword(id) {
        const m = _memberById[id];
        if (!m) return;
        if (!(await isPlatformAdmin())) return;
        if (!window.confirm(t('admin_reset_confirm').replace('{name}', m.username || m.display_name))) return;
        openAdminModal({
            title: t('admin_reset'),
            subtitle: m.username || '',
            icon: 'fa-key',
            fields: [
                { name: 'recent_password', label: t('admin_field_recent_password'), type: 'password', required: true, hint: t('admin_field_recent_password_hint') },
            ],
            submitLabel: t('admin_reset_do'),
            statusEl: document.getElementById('member-status'),
            submit: async function (body) {
                body.expected_version = m.version;
                const r = await apiFetch('/api/platform/users/' + encodeURIComponent(m.user_id) + '/password', { method: 'POST', body: body });
                // Show the one-time temp password once, then clear on close.
                const td = document.getElementById('admin-modal-body');
                const d = document.createElement('div');
                d.className = 'text-sm text-emerald-600 dark:text-emerald-400 mt-3 font-semibold break-all';
                d.textContent = t('admin_reset_temp_result') + ': ' + (r.temporary_password || '');
                td.appendChild(d);
                await loadMembersView();
            },
            onConflictReload: function () { loadMembersView(); },
        });
    }

    // ---- Platform users (task 5.3) ----------------------------------------
    async function loadPlatformUsersView() {
        const list = document.getElementById('platform-user-list');
        if (!list) return;
        if (!(await isPlatformAdmin())) {
            list.innerHTML = '<div class="text-sm text-red-500">' + escapeHtml(t('admin_forbidden')) + '</div>';
            return;
        }
        list.innerHTML = '<div class="text-sm text-slate-400 dark:text-slate-500">' + t('tenant_loading') + '</div>';
        const f = _platformUserFilters;
        f.q = (document.getElementById('platform-user-search') || {}).value || '';
        f.status = (document.getElementById('platform-user-status') || {}).value || '';
        const query = qs({ q: f.q, status: f.status, page: _platformUserPage, page_size: _platformUserPageSize });
        try {
            const data = await apiFetch('/api/platform/users?' + query);
            if (!data.items || !data.items.length) {
                list.innerHTML = '<div class="text-sm text-slate-400">' + t('platform_user_empty') + '</div>';
                return;
            }
            _platformUsers = {};
            const rows = data.items.map(function (u) {
                _platformUsers[u.id] = u;
                const badges = [];
                if (u.is_platform_admin) badges.push('<span class="px-1.5 py-0.5 rounded bg-amber-50 dark:bg-amber-900/20 text-amber-600 dark:text-amber-300 text-[10px]">' + escapeHtml(t('platform_admin_badge')) + '</span>');
                if (u.must_change_password) badges.push('<span class="px-1.5 py-0.5 rounded bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-300 text-[10px]">' + escapeHtml(t('filter_restricted')) + '</span>');
                return '<div class="flex items-center justify-between px-4 py-3 rounded-lg border border-slate-200 dark:border-white/10">'
                    + '<div class="flex items-center gap-3"><div class="w-9 h-9 rounded-lg bg-slate-50 dark:bg-white/10 flex items-center justify-center">'
                    + '<i class="fas fa-user-cog text-slate-500"></i></div>'
                    + '<div><div class="text-sm font-medium text-slate-800 dark:text-slate-100">' + escapeHtml(u.display_name) + '</div>'
                    + '<div class="text-xs text-slate-400">' + escapeHtml(u.username) + ' · ' + fmtActive(u.active) + ' ' + badges.join(' ') + '</div></div></div>'
                    + '<div class="flex items-center gap-2">'
                    + '<button class="admin-row-btn" onclick="adminRowAction(\'platform_user\',\'reset\',\'' + escapeHtml(u.id) + '\')"><i class="fas fa-key mr-1"></i>' + escapeHtml(t('admin_reset')) + '</button>'
                    + '<button class="admin-row-btn" onclick="adminRowAction(\'platform_user\',\'edit\',\'' + escapeHtml(u.id) + '\')"><i class="fas fa-pen mr-1"></i>' + escapeHtml(t('admin_edit')) + '</button>'
                    + '</div></div>';
            }).join('');
            list.innerHTML = rows;
            renderPagination(document.getElementById('platform-user-pagination'), _platformUserPage, _platformUserPageSize, data.total || 0, function (p) { _platformUserPage = p; loadPlatformUsersView(); });
        } catch (err) {
            if (err.message === 'stale-response') return;
            list.innerHTML = '<div class="text-sm text-red-500">' + t('load_error') + ': ' + escapeHtml(err.message) + '</div>';
        }
    }
    let _platformUsers = {};

    function openPlatformUserEdit(id) {
        const u = _platformUsers[id];
        if (!u) return;
        openAdminModal({
            title: t('platform_user_edit_title'),
            subtitle: u.username || '',
            icon: 'fa-pen',
            fields: [
                { name: 'username', label: t('admin_field_username'), type: 'locked', value: u.username },
                { name: 'active', label: t('admin_field_active'), type: 'checkbox', value: !!u.active },
                { name: 'is_platform_admin', label: t('admin_field_platform_admin'), type: 'checkbox', value: !!u.is_platform_admin },
                { name: 'recent_password', label: t('admin_field_recent_password'), type: 'password', required: true, hint: t('admin_field_recent_password_hint') },
            ],
            submitLabel: t('admin_save'),
            statusEl: document.getElementById('platform-user-status-msg'),
            submit: async function (body) {
                body.expected_version = u.version;
                await apiFetch('/api/platform/users/' + encodeURIComponent(id), { method: 'PATCH', body: body });
                await loadPlatformUsersView();
            },
            onConflictReload: function () { loadPlatformUsersView(); },
        });
    }

    function resetPlatformUser(id) {
        const u = _platformUsers[id];
        if (!u) return;
        if (!window.confirm(t('admin_reset_confirm').replace('{name}', u.username || u.display_name))) return;
        openAdminModal({
            title: t('admin_reset'),
            subtitle: u.username || '',
            icon: 'fa-key',
            fields: [
                { name: 'recent_password', label: t('admin_field_recent_password'), type: 'password', required: true, hint: t('admin_field_recent_password_hint') },
            ],
            submitLabel: t('admin_reset_do'),
            statusEl: document.getElementById('platform-user-status-msg'),
            submit: async function (body) {
                body.expected_version = u.version;
                const r = await apiFetch('/api/platform/users/' + encodeURIComponent(id) + '/password', { method: 'POST', body: body });
                const td = document.getElementById('admin-modal-body');
                const d = document.createElement('div');
                d.className = 'text-sm text-emerald-600 dark:text-emerald-400 mt-3 font-semibold break-all';
                d.textContent = t('admin_reset_temp_result') + ': ' + (r.temporary_password || '');
                td.appendChild(d);
                await loadPlatformUsersView();
            },
            onConflictReload: function () { loadPlatformUsersView(); },
        });
    }

    // ---- Roles view -------------------------------------------------------
    async function loadRolesView() {
        const list = document.getElementById('role-list');
        const btn = document.getElementById('role-create-btn');
        if (!list) return;
        list.innerHTML = '<div class="text-sm text-slate-400 dark:text-slate-500">' + t('tenant_loading') + '</div>';
        try {
            const data = await apiFetch(_roleApiBase());
            if (!data.items || !data.items.length) {
                list.innerHTML = '<div class="text-sm text-slate-400">' + t('role_empty') + '</div>';
                btn.classList.add('hidden');
                return;
            }
            btn.classList.remove('hidden');
            _roleById = {};
            // Reuse the already-fetched permission catalog (if any) to render
            // grouped labels; do NOT force a catalog fetch just to list roles.
            const perms = _permCatalog;
            const rows = data.items.map(function (r) {
                _roleById[r.id] = r;
                const builtin = !!r.builtin;
                const permSet = r.permissions || [];
                // Group the role's permissions by the catalog group (task 5.5).
                const grouped = renderPermGrouped(permSet, perms);
                const memberCount = countMembersForRole(r.code);
                return '<div class="px-4 py-3 rounded-lg border border-slate-200 dark:border-white/10">'
                    + '<div class="flex items-center justify-between"><div class="text-sm font-medium text-slate-800 dark:text-slate-100">'
                    + escapeHtml(r.name) + ' <span class="text-xs text-slate-400">(' + escapeHtml(r.code) + ')</span>'
                    + (builtin ? ' <span class="px-1.5 py-0.5 rounded bg-slate-100 dark:bg-white/10 text-[10px] text-slate-500">' + escapeHtml(t('role_builtin')) + '</span>' : '')
                    + (memberCount != null ? ' <span class="text-xs text-slate-400">' + escapeHtml(t('role_members_label')) + ' ' + memberCount + '</span>' : '')
                    + '</div>'
                    + '<div class="flex items-center gap-2">'
                    + '<button class="admin-row-btn" onclick="adminRowAction(\'role\',\'members\',\'' + escapeHtml(r.code) + '\')"><i class="fas fa-users mr-1"></i>' + escapeHtml(t('role_view_members')) + '</button>'
                    + '<button class="admin-row-btn" onclick="adminRowAction(\'role\',\'copy\',\'' + escapeHtml(r.id) + '\')"><i class="fas fa-copy mr-1"></i>' + escapeHtml(t('role_copy')) + '</button>'
                    + (builtin ? '' : '<button class="admin-row-btn" onclick="adminRowAction(\'role\',\'edit\',\'' + escapeHtml(r.id) + '\')"><i class="fas fa-pen mr-1"></i>' + escapeHtml(t('admin_edit')) + '</button>')
                    + (builtin ? '' : '<button class="admin-row-btn danger" onclick="adminRowAction(\'role\',\'delete\',\'' + escapeHtml(r.id) + '\')"><i class="fas fa-trash mr-1"></i>' + escapeHtml(t('admin_delete')) + '</button>')
                    + '</div></div>'
                    + '<div class="mt-1 text-xs text-slate-400">' + t('role_permissions_label') + ': ' + grouped + '</div>'
                    + '</div>';
            }).join('');
            list.innerHTML = rows;
        } catch (err) {
            if (err.message === 'stale-response') return;
            list.innerHTML = '<div class="text-sm text-red-500">' + t('load_error') + ': ' + escapeHtml(err.message) + '</div>';
        }
    }

    function renderPermGrouped(permSet, catalog) {
        if (!permSet || !permSet.length) return '-';
        const idToMeta = {};
        (catalog || []).forEach(function (p) { idToMeta[p.id] = p; });
        const labels = permSet.map(function (pid) {
            const meta = idToMeta[pid];
            return meta ? (meta.group ? '[' + meta.group + '] ' : '') + meta.label : pid;
        });
        return labels.join(', ');
    }

    function countMembersForRole(code) {
        // Count members holding this role using the member list (capped).
        // A block count would be a second request; keep a light in-page count
        // only for roles we have already loaded member data for. Return null
        // when unavailable so the row omits it.
        return null;
    }

    async function openRoleCreate() {
        const perms = await ensurePermCatalog();
        if (!perms) return;
        _resetResourceState([], {});
        openAdminModal({
            title: t('role_create'),
            icon: 'fa-plus',
            fields: [
                { name: 'code', label: t('admin_field_code'), type: 'text', required: true, hint: t('admin_field_code_hint'), inline: true },
                { name: 'name', label: t('admin_field_name'), type: 'text', required: true, inline: true },
                { name: 'permissions', label: t('admin_field_permissions'), type: 'multi', options: permOptions(perms), hint: t('admin_field_permissions_hint') },
                { name: 'resource_grants', label: t('admin_field_resource_grants'), type: 'resourcegroup', hint: t('admin_field_resource_grants_hint') },
                { name: 'model_defaults', label: t('admin_field_model_defaults'), type: 'modeldefaults', hint: t('admin_field_model_defaults_hint') },
            ],
            submitLabel: t('admin_create'),
            statusEl: document.getElementById('role-status'),
            submit: async function (body) {
                body.resource_grants = _collectResourceGrants();
                body.model_defaults = _collectModelDefaults();
                await apiFetch(_roleApiBase(), { method: 'POST', body: body });
                await loadRolesView();
            },
            onConflictReload: function () { loadRolesView(); },
        });
    }

    async function openRoleEdit(id) {
        const r = _roleById[id];
        if (!r) return;
        const perms = await ensurePermCatalog();
        if (!perms) return;
        _resetResourceState(r.resource_grants || [], r.model_defaults || {});
        openAdminModal({
            title: t('role_edit_title'),
            subtitle: r.code || '',
            icon: 'fa-pen',
            fields: [
                { name: 'code', label: t('admin_field_code'), type: 'locked', value: r.code, inline: true },
                { name: 'name', label: t('admin_field_name'), type: 'text', value: r.name, required: true, inline: true },
                { name: 'permissions', label: t('admin_field_permissions'), type: 'multi', options: permOptions(perms), value: r.permissions || [] },
                { name: 'resource_grants', label: t('admin_field_resource_grants'), type: 'resourcegroup', hint: t('admin_field_resource_grants_hint') },
                { name: 'model_defaults', label: t('admin_field_model_defaults'), type: 'modeldefaults', hint: t('admin_field_model_defaults_hint') },
            ],
            submitLabel: t('admin_save'),
            statusEl: document.getElementById('role-status'),
            submit: async function (body) {
                body.expected_version = r.version;
                body.resource_grants = _collectResourceGrants();
                body.model_defaults = _collectModelDefaults();
                await apiFetch(_roleApiBase() + '/' + encodeURIComponent(id), { method: 'POST', body: body });
                await loadRolesView();
            },
            onConflictReload: function () { loadRolesView(); },
        });
    }

    async function copyRole(id) {
        const r = _roleById[id];
        if (!r) return;
        const perms = await ensurePermCatalog();
        if (!perms) return;
        // Read then create (task 5.5); never copy admin qualification or bindings.
        const assignableSet = {};
        perms.forEach(function (p) { assignableSet[p.id] = p; });
        const copyablePermissions = (r.permissions || []).filter(function (pid) {
            return assignableSet[pid] && assignableSet[pid].assignable !== false;
        });
        _resetResourceState(r.resource_grants || [], r.model_defaults || {});
        openAdminModal({
            title: t('role_copy_title'),
            subtitle: r.name || '',
            icon: 'fa-copy',
            fields: [
                { name: 'code', label: t('admin_field_code'), type: 'text', required: true, hint: t('admin_field_code_hint') },
                { name: 'name', label: t('admin_field_name'), type: 'text', required: true },
                { name: 'permissions', label: t('admin_field_permissions'), type: 'multi', options: permOptions(perms), value: copyablePermissions },
                { name: 'resource_grants', label: t('admin_field_resource_grants'), type: 'resourcegroup', hint: t('admin_field_resource_grants_hint') },
                { name: 'model_defaults', label: t('admin_field_model_defaults'), type: 'modeldefaults', hint: t('admin_field_model_defaults_hint') },
            ],
            submitLabel: t('admin_create'),
            statusEl: document.getElementById('role-status'),
            submit: async function (body) {
                body.resource_grants = _collectResourceGrants();
                body.model_defaults = _collectModelDefaults();
                await apiFetch(_roleApiBase(), { method: 'POST', body: body });
                await loadRolesView();
            },
            onConflictReload: function () { loadRolesView(); },
        });
    }

    async function deleteRole(id) {
        const r = _roleById[id];
        if (!r) return;
        if (!window.confirm(t('admin_delete_confirm_role').replace('{name}', r.name))) return;
        try {
            await apiFetch(_roleApiBase() + '/' + encodeURIComponent(id), { method: 'DELETE' });
            status(document.getElementById('role-status'), t('admin_deleted'), true);
            await loadRolesView();
        } catch (err) {
            status(document.getElementById('role-status'), err.message || t('admin_save_failed'), false);
            if (err.status === 409 || err.code === 'conflict' || err.code === 'in_use') await loadRolesView();
        }
    }

    function openTenantRoles(id) {
        // Platform-admin target-tenant role editing (task 3.2). Set the platform
        // target so role CRUD and the assign catalog route to the platform
        // endpoints for this tenant, then show the roles view. If an admin form
        // has unsaved changes, confirm discard FIRST so the target is not
        // switched away while a stale draft still references the previous target.
        const tn = _tenantById[id];
        if (!tn) return;
        if (typeof window.__identityAdminDirtyGuard__ === 'function'
            && !window.__identityAdminDirtyGuard__()) return;
        _rolePlatformTarget = id;
        try {
            if (typeof window.navigateTo === 'function') window.navigateTo('roles');
            else if (typeof navigateTo === 'function') navigateTo('roles');
        } catch (e) {}
        setTimeout(function () { loadRolesView(); }, 50);
    }

    function viewRoleMembers(code) {
        // Reuse the member list's role filter (task 5.5): set the filter and
        // navigate to the Users page.
        const sel = document.getElementById('member-role-filter');
        if (sel && code) sel.value = code;
        // jump to the member view
        try {
            if (typeof window.navigateTo === 'function') window.navigateTo('system_user');
            else if (typeof navigateTo === 'function') navigateTo('system_user');
        } catch (e) {}
        if (sel) { _memberFilters.role = code; _memberPage = 1; }
        // Wait for the view switch to render, then load.
        setTimeout(function () { loadMembersView(); }, 50);
    }

    // ---- Organization view (task 5.6) -------------------------------------
    async function loadOrgView() {
        const org = document.getElementById('org-tree');
        if (!org) return;
        org.innerHTML = '<div class="text-sm text-slate-400 dark:text-slate-500">' + t('tenant_loading') + '</div>';
        try {
            const data = await apiFetch('/api/tenant/departments');
            if (!data.items || !data.items.length) {
                org.innerHTML = '<div class="text-sm text-slate-400">' + t('org_empty') + '</div>';
                return;
            }
            _deptById = {};
            _depts = data.items || [];
            _depts.forEach(function (d) { _deptById[d.id] = d; });
            org.innerHTML = buildOrgTree();
        } catch (err) {
            if (err.message === 'stale-response') return;
            org.innerHTML = '<div class="text-sm text-red-500">' + t('load_error') + ': ' + escapeHtml(err.message) + '</div>';
        }
    }

    function buildOrgTree() {
        // Build a nested tree by parent_id, ordered by sort_order then name.
        const byParent = {};
        // Resolve the synthetic root id FIRST. The __root__ record may appear
        // anywhere in the list (bootstrap typically appends it last), so a
        // single-pass assign-to-rootId groups top-level departments (empty
        // parent_id) under a blank key before rootId is known, leaving the tree
        // blank. Two passes keep top-level nodes attached to the real root.
        let rootId = '';
        for (let i = 0; i < _depts.length; i++) {
            if (_depts[i].code === '__root__') { rootId = _depts[i].id; break; }
        }
        _depts.forEach(function (d) {
            if (d.code === '__root__') { return; }
            const pid = d.parent_id || rootId;
            (byParent[pid] = byParent[pid] || []).push(d);
        });
        Object.keys(byParent).forEach(function (k) {
            byParent[k].sort(function (a, b) {
                return (a.sort_order || 0) - (b.sort_order || 0) || a.name.localeCompare(b.name);
            });
        });
        const walk = function (parentId, depth) {
            const children = byParent[parentId] || [];
            if (!children.length) return '';
            return children.map(function (d) {
                const kids = walk(d.id, depth + 1);
                const hasKids = kids !== '';
                const indent = depth > 0 ? ' style="margin-left:' + (depth * 16) + 'px"' : '';
                const chevron = hasKids
                    ? '<i class="fas fa-chevron-right text-[10px] text-slate-400 mr-1 org-chevron transition-transform duration-150"></i>'
                    : '<i class="fas fa-folder text-amber-500 mr-1"></i>';
                return '<div class="org-node" data-id="' + escapeHtml(d.id) + '"' + indent + '>'
                    + '<div class="flex items-center justify-between px-3 py-2 rounded-lg border border-slate-200 dark:border-white/10 org-row cursor-pointer">'
                    + '<div class="flex items-center gap-2">' + chevron
                    + '<span class="text-sm text-slate-800 dark:text-slate-100">' + escapeHtml(d.name) + '</span>'
                    + '<span class="text-xs text-slate-400">(' + escapeHtml(d.code) + ')</span>'
                    + '<span class="text-xs text-slate-400">' + fmtActive(d.active) + '</span>'
                    + '</div>'
                    + '<div class="flex items-center gap-2">'
                    + '<button class="admin-row-btn" onclick="adminRowAction(\'dept\',\'members\',\'' + escapeHtml(d.id) + '\')"><i class="fas fa-users mr-1"></i>' + escapeHtml(t('role_view_members')) + '</button>'
                    + '<button class="admin-row-btn" onclick="adminRowAction(\'dept\',\'edit\',\'' + escapeHtml(d.id) + '\')"><i class="fas fa-pen mr-1"></i>' + escapeHtml(t('admin_edit')) + '</button>'
                    + '<button class="admin-row-btn danger" onclick="adminRowAction(\'dept\',\'delete\',\'' + escapeHtml(d.id) + '\')"><i class="fas fa-trash mr-1"></i>' + escapeHtml(t('admin_delete')) + '</button>'
                    + '</div></div>'
                    + (hasKids ? '<div class="org-children hidden pl-2">' + kids + '</div>' : '')
                    + '</div>';
            }).join('');
        };
        return walk(rootId, 0);
    }

    function openDeptCreate(parentId) {
        const depts = _depts || [];
        openAdminModal({
            title: t('dept_create'),
            icon: 'fa-plus',
            fields: [
                { name: 'code', label: t('admin_field_code'), type: 'text', required: true, hint: t('admin_field_code_hint') },
                { name: 'name', label: t('admin_field_name'), type: 'text', required: true },
                { name: 'parent_id', label: t('admin_field_parent'), type: 'select', options: deptOptions(depts), value: parentId || '' },
                { name: 'sort_order', label: t('admin_field_sort_order'), type: 'number', value: 0 },
            ],
            submitLabel: t('admin_create'),
            statusEl: document.getElementById('org-status'),
            submit: async function (body) {
                body.parent_id = body.parent_id || null;
                await apiFetch('/api/tenant/departments', { method: 'POST', body: body });
                await loadOrgView();
            },
            onConflictReload: function () { loadOrgView(); },
        });
    }

    function rootDeptId() {
        for (let i = 0; i < _depts.length; i++) {
            if (_depts[i].code === '__root__') return _depts[i].id;
        }
        return '';
    }

    function openDeptEdit(id) {
        const d = _deptById[id];
        if (!d) return;
        const parentValue = d.parent_id || rootDeptId();
        openAdminModal({
            title: t('dept_edit_title'),
            subtitle: d.code || '',
            icon: 'fa-pen',
            fields: [
                { name: 'code', label: t('admin_field_code'), type: 'locked', value: d.code },
                { name: 'name', label: t('admin_field_name'), type: 'text', value: d.name, required: true },
                { name: 'sort_order', label: t('admin_field_sort_order'), type: 'number', value: d.sort_order },
                { name: 'active', label: t('admin_field_active'), type: 'checkbox', value: !!d.active },
                { name: 'parent_id', label: t('admin_field_parent'), type: 'select', options: deptOptions(_depts, d.id), value: parentValue },
            ],
            submitLabel: t('admin_save'),
            statusEl: document.getElementById('org-status'),
            submit: async function (body) {
                body.expected_version = d.version;
                body.parent_id = body.parent_id || null;
                await apiFetch('/api/tenant/departments/' + encodeURIComponent(id), { method: 'PUT', body: body });
                await loadOrgView();
            },
            onConflictReload: function () { loadOrgView(); },
        });
    }

    async function deleteDept(id) {
        const d = _deptById[id];
        if (!d) return;
        if (!window.confirm(t('admin_delete_confirm_dept').replace('{name}', d.name))) return;
        try {
            await apiFetch('/api/tenant/departments/' + encodeURIComponent(id), { method: 'DELETE' });
            status(document.getElementById('org-status'), t('admin_deleted'), true);
            await loadOrgView();
        } catch (err) {
            status(document.getElementById('org-status'), err.message || t('admin_save_failed'), false);
            if (err.status === 409 || err.code === 'conflict' || err.code === 'in_use' || err.code === 'cycle') await loadOrgView();
            if (err.code === 'cycle') status(document.getElementById('org-status'), t('org_cycle_rejected'), false);
        }
    }

    function viewDeptMembers(id) {
        const sel = document.getElementById('member-dept-filter');
        if (sel && id) sel.value = id;
        try {
            if (typeof window.navigateTo === 'function') window.navigateTo('system_user');
            else if (typeof navigateTo === 'function') navigateTo('system_user');
        } catch (e) {}
        if (sel) { _memberFilters.department_id = id; _memberPage = 1; }
        setTimeout(function () { loadMembersView(); }, 50);
    }

    // ---- Audit view (task 5.7) --------------------------------------------
    async function loadAuditView() {
        const list = document.getElementById('audit-list');
        if (!list) return;
        list.innerHTML = '<div class="text-sm text-slate-400 dark:text-slate-500">' + t('tenant_loading') + '</div>';
        const f = _auditFilters;
        f.actor = (document.getElementById('audit-actor') || {}).value || '';
        f.action = (document.getElementById('audit-action') || {}).value || '';
        f.result = (document.getElementById('audit-result') || {}).value || '';
        const query = qs({
            actor: f.actor, action: f.action, result: f.result,
            since: f.since, until: f.until,
            page: _auditPage, page_size: _auditPageSize,
        });
        try {
            const data = await apiFetch('/api/identity/audit?' + query);
            const items = data.items || [];
            if (!items.length) {
                list.innerHTML = '<div class="text-sm text-slate-400">' + t('audit_empty') + '</div>';
                renderPagination(document.getElementById('audit-pagination'), _auditPage, _auditPageSize, data.total || 0, function (p) { _auditPage = p; loadAuditView(); });
                return;
            }
            const rows = items.map(function (ev) {
                let changes = '';
                try { const ch = JSON.parse(ev.redacted_changes || '{}'); changes = Object.keys(ch).map(function (k) { return k + '=' + String(ch[k]); }).join(', '); } catch (e) {}
                const actionHex = ev.action && ev.action.indexOf('.') > 0 ? '' : '';
                return '<div class="px-4 py-3 rounded-lg border border-slate-200 dark:border-white/10">'
                    + '<div class="flex items-center justify-between"><div class="text-sm font-medium text-slate-800 dark:text-slate-100">' + escapeHtml(ev.action) + '</div>'
                    + '<div class="text-xs ' + (ev.result === 'denied' ? 'text-red-500' : 'text-slate-400') + '">' + escapeHtml(ev.result) + '</div></div>'
                    + '<div class="mt-1 text-xs text-slate-400">' + escapeHtml(ev.actor_username || '-') + ' · ' + new Date((ev.time || 0) * 1000).toLocaleString() + '</div>'
                    + (changes ? '<div class="mt-1 text-xs text-slate-400 break-all">' + escapeHtml(changes) + '</div>' : '')
                    + '</div>';
            }).join('');
            list.innerHTML = rows;
            renderPagination(document.getElementById('audit-pagination'), _auditPage, _auditPageSize, data.total || 0, function (p) { _auditPage = p; loadAuditView(); });
        } catch (err) {
            if (err.message === 'stale-response') return;
            list.innerHTML = '<div class="text-sm text-red-500">' + t('load_error') + ': ' + escapeHtml(err.message) + '</div>';
            status(document.getElementById('audit-status'), err.message, false);
        }
    }

    // ---- row action dispatcher (used by inline onclick) -------------------
    function adminRowAction(kind, action, id) {
        if (action === 'edit') {
            if (kind === 'tenant') openTenantEdit(id);
            else if (kind === 'member') openMemberEdit(id);
            else if (kind === 'role') openRoleEdit(id);
            else if (kind === 'dept') openDeptEdit(id);
            else if (kind === 'platform_user') openPlatformUserEdit(id);
        } else if (action === 'delete') {
            if (kind === 'role') deleteRole(id);
            else if (kind === 'dept') deleteDept(id);
        } else if (action === 'admin') {
            if (kind === 'tenant') openTenantAdmin(id);
        } else if (action === 'roles') {
            if (kind === 'tenant') openTenantRoles(id);
        } else if (action === 'copy') {
            if (kind === 'role') copyRole(id);
        } else if (action === 'members') {
            if (kind === 'role') viewRoleMembers(id);
            else if (kind === 'dept') viewDeptMembers(id);
        } else if (action === 'reset') {
            if (kind === 'member') resetMemberPassword(id);
            else if (kind === 'platform_user') resetPlatformUser(id);
        }
    }

    // Let console.js navigation ask whether it's safe to leave an admin view.
    // Returns true when there is no unsaved form (or the user confirms discard).
    function identityAdminDirtyGuard() {
        const state = _adminModal;
        if (state && state.open && state.dirty) {
            const ok = confirmDiscard(true);
            if (ok) closeAdminModalNoPrompt();
            return ok;
        }
        return true;
    }

    // ---- wire up create buttons / filters ---------------------------------
    document.addEventListener('DOMContentLoaded', function () {
        const tenantBtn = document.getElementById('tenant-create-btn');
        if (tenantBtn) tenantBtn.addEventListener('click', function () { openTenantCreate(); });
        const memberBtn = document.getElementById('member-create-btn');
        if (memberBtn) memberBtn.addEventListener('click', function () { openMemberCreate(); });
        const roleBtn = document.getElementById('role-create-btn');
        if (roleBtn) roleBtn.addEventListener('click', function () { openRoleCreate(); });
        const deptBtn = document.getElementById('dept-create-btn');
        if (deptBtn) deptBtn.addEventListener('click', function () { openDeptCreate(); });

        // Member filters: debounce search and re-load on filter/status change.
        const msearch = document.getElementById('member-search');
        if (msearch) { let deb; msearch.addEventListener('input', function () { clearTimeout(deb); deb = setTimeout(function () { _memberPage = 1; loadMembersView(); }, 300); }); }
        ['member-status-filter', 'member-role-filter', 'member-dept-filter'].forEach(function (id) {
            const el = document.getElementById(id);
            if (el) el.addEventListener('change', function () { _memberPage = 1; loadMembersView(); });
        });

        // Platform user filters.
        const psearch = document.getElementById('platform-user-search');
        if (psearch) { let deb; psearch.addEventListener('input', function () { clearTimeout(deb); deb = setTimeout(function () { _platformUserPage = 1; loadPlatformUsersView(); }, 300); }); }
        const pstatus = document.getElementById('platform-user-status');
        if (pstatus) pstatus.addEventListener('change', function () { _platformUserPage = 1; loadPlatformUsersView(); });

        // Tenant search.
        const tsearch = document.getElementById('tenant-search');
        if (tsearch) { let deb; tsearch.addEventListener('input', function () { clearTimeout(deb); deb = setTimeout(function () { loadTenantView(); }, 300); }); }

        // Audit apply button + inputs.
        const applyBtn = document.getElementById('audit-apply');
        if (applyBtn) applyBtn.addEventListener('click', function () { _auditPage = 1; loadAuditView(); });
        ['audit-actor', 'audit-action', 'audit-result'].forEach(function (id) {
            const el = document.getElementById(id);
            if (el) el.addEventListener('change', function () { _auditPage = 1; loadAuditView(); });
        });

        // Org tree expand/collapse (delegated).
        const org = document.getElementById('org-tree');
        if (org) org.addEventListener('click', function (e) {
            const row = e.target.closest('.org-row');
            if (!row) return;
            const chevron = row.querySelector('.org-chevron');
            const node = row.parentElement;
            const children = node.querySelector(':scope > .org-children');
            if (children && chevron) {
                children.classList.toggle('hidden');
                chevron.classList.toggle('-rotate-90');
            }
        });
    });

    // expose for console.js navigation / inline onclick
    window.loadTenantView = loadTenantView;
    window.loadMembersView = loadMembersView;
    window.loadRolesView = loadRolesView;
    window.loadOrgView = loadOrgView;
    window.loadPlatformUsersView = loadPlatformUsersView;
    window.loadAuditView = loadAuditView;
    window.bumpTenantGeneration = bumpTenantGeneration;
    window.adminRowAction = adminRowAction;
    window.__identityAdminDirtyGuard__ = identityAdminDirtyGuard;
})();
