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
        // A per-call tenant override lets multi-tenant member writes target each
        // tenant explicitly; the server still re-validates the actor's
        // tenant_admin qualification for that tenant on every request.
        const tenant = (options && options.tenantId) || sessionStorage.getItem('cow_tenant_id');
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
            '<div class="bg-white dark:bg-[#1A1A1A] rounded-2xl border border-slate-200 dark:border-white/10 shadow-2xl w-full max-w-4xl max-h-[calc(100dvh-4rem)] m-2 overflow-hidden flex flex-col">' +
            // Header: icon + title + subtitle + close (bordered bottom)
            '<div class="flex items-center justify-between px-6 py-4 border-b border-slate-200 dark:border-white/10 flex-shrink-0">' +
            '<div class="flex items-center gap-3 min-w-0">' +
            '<div class="w-9 h-9 rounded-lg bg-primary-500/10 dark:bg-primary-400/15 text-primary-600 dark:text-primary-400 flex items-center justify-center flex-shrink-0">' +
            '<i id="admin-modal-icon" class="fas fa-plus text-sm"></i>' +
            '</div>' +
            '<div class="min-w-0">' +
            '<h3 id="admin-modal-title" class="text-base font-semibold text-slate-800 dark:text-slate-100 truncate"></h3>' +
            '<p id="admin-modal-subtitle" class="text-xs text-slate-500 dark:text-slate-400 mt-0.5 truncate"></p>' +
            '</div>' +
            '</div>' +
            '<button class="admin-modal-close p-2 -mr-1 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100 dark:hover:text-slate-300 dark:hover:bg-white/10 transition-colors cursor-pointer"><i class="fas fa-times text-sm"></i></button>' +
            '</div>' +
            // Body (scrollable) + inline error
            '<div id="admin-modal-body" class="flex-1 overflow-y-auto px-6 py-6 space-y-6"></div>' +
            '<p id="admin-modal-error" class="hidden px-6 -mt-2 text-xs text-red-500"></p>' +
            // Footer: bottom bar
            '<div class="flex items-center justify-between px-6 py-4 border-t border-slate-200 dark:border-white/10 bg-slate-50/60 dark:bg-white/[0.02] flex-shrink-0">' +
            '<div></div>' +
            '<div class="flex items-center gap-3">' +
            '<button id="admin-modal-cancel" class="admin-modal-close px-4 py-2 rounded-lg text-sm font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/10 active:scale-[0.98] cursor-pointer transition-all"></button>' +
            '<button id="admin-modal-submit" class="px-5 py-2 rounded-lg bg-primary-500 hover:bg-primary-600 active:scale-[0.98] text-white text-sm font-medium shadow-sm shadow-primary-500/20 disabled:opacity-50 cursor-pointer transition-all inline-flex items-center gap-1.5"><i class="fas fa-check text-xs"></i><span class="admin-modal-submit-label"></span></button>' +
            '</div>' +
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
            control = '<div class="agent-input-wrap relative"><span class="agent-icon-abs">' + (f.icon ? '<i class="fas fa-' + escapeHtml(f.icon) + '"></i>' : '') + '</span>' +
                '<select id="' + id + '" class="agent-input"' + (f.icon ? ' data-with-icon="1"' : '') + '>' + (f.options || []).map(function (o) {
                    return '<option value="' + escapeHtml(o.value) + '"' + (String(o.value) === String(val) ? ' selected' : '') + '>' + escapeHtml(o.label) + '</option>';
                }).join('') + '</select></div>';
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
            control = '<div class="agent-input-wrap relative"><span class="agent-icon-abs">' + (f.icon ? '<i class="fas fa-' + escapeHtml(f.icon) + '"></i>' : '') + '</span>' +
                '<input type="' + type + '" id="' + id + '" class="agent-input"' + (f.icon ? ' data-with-icon="1"' : '') + ' value="' + escapeHtml(val) + '" placeholder="' + escapeHtml(f.placeholder || '') + '"></div>';
        }
        const isFullWidth = f.type === 'resourcegroup' || f.type === 'modeldefaults' || f.type === 'modelgrant' || f.type === 'textarea' || f.type === 'select' && f.full;
        const wrapClass = isFullWidth ? 'agent-field w-full md:col-span-2' : (f.inline ? 'agent-field agent-field-inline flex-1 min-w-[220px]' : 'agent-field w-full');
        return '<div class="' + wrapClass + '">' + label + control +
            (f.hint ? '<div class="agent-field-hint">' + escapeHtml(f.hint) + '</div>' : '') +
            '</div>';
    }

    // Group fields into optional "section" blocks, each with an icon + heading +
    // divider line. Within a section, fields lay out in a two-column grid.
    // A new section begins only at a field that carries an explicit
    // `sectionTitle`; fields without one continue in the current section, so
    // callers may mark just the first field of a group. Fields that precede any
    // titled section are rendered as a single implicit (untitled) section.
    function renderModalBody(fields) {
        const sections = [];
        let current = null;
        fields.forEach(function (f) {
            if (f.sectionTitle) {
                current = { key: f.sectionTitle, title: f.sectionTitle, icon: f.sectionIcon, fields: [] };
                sections.push(current);
            } else if (!current) {
                current = { key: '', title: '', icon: '', fields: [] };
                sections.push(current);
            }
            current.fields.push(f);
        });
        return sections.map(function (s) {
            const head = s.title
                ? '<div class="flex items-center gap-2 mb-3">' +
                  '<i class="fas fa-' + escapeHtml(s.icon || 'circle') + ' text-xs text-slate-400 dark:text-slate-500 w-4 text-center"></i>' +
                  '<h4 class="text-xs font-semibold text-slate-500 dark:text-slate-400">' + escapeHtml(s.title) + '</h4>' +
                  '<div class="flex-1 h-px bg-slate-100 dark:bg-white/5"></div>' +
                  '</div>'
                : '';
            const grid = '<div class="grid grid-cols-1 md:grid-cols-2 gap-4">' +
                s.fields.map(fieldHtml).join('') +
                '</div>';
            return '<section>' + head + grid + '</section>';
        }).join('');
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
        document.getElementById('admin-modal-icon').className = 'fas ' + (cfg.icon || 'fa-plus') + ' text-sm';
        const submitBtn = document.getElementById('admin-modal-submit');
        const submitLabel = submitBtn.querySelector('.admin-modal-submit-label');
        if (submitLabel) submitLabel.textContent = cfg.submitLabel || t('save');
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
        body.innerHTML = renderModalBody(cfg.fields || []);
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

    // Tenants the actor administers (active tenant_admin). This is the candidate
    // set for the member tenant multi-select; a platform admin is NOT broadened
    // to all tenants (see `administered_tenants` in auth/service.py). Pass a
    // target `user_id` to also learn that user's membership status within these
    // administered tenants (never other tenants).
    let _adminTenants = null;
    async function administeredTenants(targetUserId) {
        if (!targetUserId && _adminTenants) return _adminTenants;
        const path = '/api/identity/administered-tenants' +
            (targetUserId ? '?' + qs({ user_id: targetUserId }) : '');
        const data = await apiFetch(path);
        const items = data.items || [];
        if (!targetUserId) _adminTenants = items;
        return items;
    }

    function tenantOptions(tenants) {
        return (tenants || []).map(function (t) {
            return { value: t.id, label: t.name + ' (' + t.code + ')' };
        });
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
                    + '<button class="admin-row-btn" onclick="adminRowAction(\'member\',\'tenants\',\'' + escapeHtml(m.id) + '\')"><i class="fas fa-building mr-1"></i>' + escapeHtml(t('member_tenants')) + '</button>'
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
        const tenants = await administeredTenants();
        const currentTenant = sessionStorage.getItem('cow_tenant_id');
        openAdminModal({
            title: t('member_create'),
            icon: 'fa-plus',
            fields: [
                // Section 0: 所属租户
                { name: 'tenants', label: t('admin_field_tenants'), type: 'multi', options: tenantOptions(tenants), value: [currentTenant], required: true, hint: t('admin_field_tenants_hint'), sectionTitle: t('member_section_tenants'), sectionIcon: 'building' },
                // Section 1: 账号信息
                { name: 'username', label: t('admin_field_username'), type: 'text', required: true, hint: t('admin_field_username_hint'), inline: true, icon: 'user', sectionTitle: t('member_section_account'), sectionIcon: 'address-card' },
                { name: 'display_name', label: t('admin_field_display_name'), type: 'text', required: true, inline: true, icon: 'id-card' },
                { name: 'temporary_password', label: t('admin_field_temp_password'), type: 'password', required: true, hint: t('admin_field_password_hint'), icon: 'lock' },
                { name: 'department_id', label: t('admin_field_department'), type: 'select', options: memberDeptOptions(depts), hint: t('admin_field_department_hint'), icon: 'building' },
                { name: 'position_text', label: t('admin_field_position'), type: 'text', icon: 'briefcase' },
                // Section 2: 角色与状态
                { name: 'roles', label: t('admin_field_roles'), type: 'multi', options: roleOptions(roles), value: ['member'], hint: t('admin_field_roles_hint'), sectionTitle: t('member_section_role'), sectionIcon: 'user-shield' },
            ],
            submitLabel: t('admin_create'),
            statusEl: document.getElementById('member-status'),
            submit: async function (body) {
                const tenantIds = body.tenants || [];
                for (let i = 0; i < tenantIds.length; i++) {
                    const tid = tenantIds[i];
                    const op = (i === 0) ? 'create-new' : 'bind-existing';
                    await apiFetch('/api/tenant/members', {
                        method: 'POST',
                        tenantId: tid,
                        body: {
                            operation: op,
                            username: body.username,
                            display_name: body.display_name,
                            temporary_password: op === 'create-new' ? (body.temporary_password || '') : '',
                            roles: body.roles,
                            department_id: body.department_id || null,
                            position_text: body.position_text,
                        },
                    });
                }
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
                // Section 1: 账号信息
                { name: 'username', label: t('admin_field_username'), type: 'locked', value: m.username, sectionTitle: t('member_section_account'), sectionIcon: 'address-card' },
                { name: 'display_name', label: t('admin_field_display_name'), type: 'text', value: m.display_name, required: true, icon: 'id-card' },
                { name: 'department_id', label: t('admin_field_department'), type: 'select', options: memberDeptOptions(depts), value: m.department_id || '', icon: 'building' },
                { name: 'position_text', label: t('admin_field_position'), type: 'text', value: m.position_text || '', icon: 'briefcase' },
                // Section 2: 角色与状态
                { name: 'active', label: t('admin_field_active'), type: 'checkbox', value: !!m.active, sectionTitle: t('member_section_role'), sectionIcon: 'user-shield' },
                { name: 'roles', label: t('admin_field_roles'), type: 'multi', options: roleOptions(roles), value: m.role_codes || [], hint: t('admin_field_roles_edit_hint') },
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

    // Adjust which (administered) tenants a member belongs to — the tenant side
    // of member editing, decoupled from the per-tenant profile edit. Adding a
    // tenant binds the existing account there (default member role); removing
    // one deactivates that membership (continuity is enforced server-side).
    async function openMemberTenants(id) {
        const m = _memberById[id];
        if (!m) return;
        const tenants = await administeredTenants(m.user_id);
        const current = tenants.filter(function (t) { return t.member; }).map(function (t) { return t.id; });
        openAdminModal({
            title: t('member_tenants_title'),
            subtitle: m.username || '',
            icon: 'fa-building',
            fields: [
                { name: 'tenants', label: t('admin_field_tenants'), type: 'multi', options: tenantOptions(tenants), value: current, required: true, hint: t('admin_field_tenants_edit_hint') },
            ],
            submitLabel: t('admin_save'),
            statusEl: document.getElementById('member-status'),
            submit: async function (body) {
                const selected = new Set(body.tenants || []);
                for (let i = 0; i < tenants.length; i++) {
                    const t = tenants[i];
                    if (!t.member && selected.has(t.id)) {
                        await apiFetch('/api/tenant/members', {
                            method: 'POST',
                            tenantId: t.id,
                            body: {
                                operation: 'bind-existing',
                                username: m.username,
                                display_name: m.display_name,
                                temporary_password: '',
                                roles: ['member'],
                                department_id: null,
                                position_text: '',
                            },
                        });
                    }
                }
                for (let i = 0; i < tenants.length; i++) {
                    const t = tenants[i];
                    if (t.member && !selected.has(t.id)) {
                        await apiFetch('/api/tenant/members/' + encodeURIComponent(t.member_id), {
                            method: 'POST',
                            tenantId: t.id,
                            body: {
                                display_name: m.display_name,
                                active: false,
                                department_id: null,
                                position_text: '',
                                expected_version: t.member_version,
                            },
                        });
                    }
                }
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

    // ---- Role page editor (create / edit / copy; replaces admin-modal for roles) --
    let _roleEditor = {
        open: false, dirty: false, mode: 'create', role: null,
        submit: null, onConflictReload: null, statusEl: null,
        successMsg: '', activeTab: 'basic', perms: null,
    };

    function markRoleEditorDirty() {
        _roleEditor.dirty = true;
        const pill = document.getElementById('role-dirty-pill');
        if (pill) pill.classList.add('show');
    }

    function _roleEditorTabLabel(tab) {
        if (tab === 'basic') return t('role_tab_basic') || '基本信息';
        if (tab === 'model') return t('admin_resource_kind_model') || '模型';
        return _resourceKindLabel(tab);
    }

    function ensureRoleEditor() {
        let el = document.getElementById('role-editor');
        if (el) return el;
        el = document.createElement('div');
        el.id = 'role-editor';
        el.className = 'role-editor hidden';
        const tabs = ['basic', 'menu', 'skill', 'tool', 'agent', 'model'];
        const tabHtml = tabs.map(function (tab) {
            const badgeId = tab === 'basic' ? 'role-badge-perms' : ('role-badge-' + tab);
            return '<button type="button" class="role-editor-tab" data-tab="' + tab + '">' +
                '<span class="role-editor-tab-label" data-tab-label="' + tab + '"></span>' +
                '<span class="role-editor-badge" id="' + badgeId + '">0</span></button>';
        }).join('');
        const kindPanels = ['menu', 'skill', 'tool', 'agent'].map(function (kind) {
            return '<div class="role-editor-panel" id="role-panel-' + kind + '">' +
                '<p class="text-sm text-slate-500 dark:text-slate-400 mb-4 role-panel-hint" data-kind-hint="' + kind + '"></p>' +
                '<div class="role-res-picker" id="role-res-manage-' + kind + '">' +
                '<div class="flex flex-wrap gap-2 items-center mb-3">' +
                '<input type="text" class="agent-input flex-1 min-w-[180px] role-res-search" data-kind="' + kind + '" placeholder="">' +
                '<button type="button" class="admin-row-btn role-res-selectall" data-kind="' + kind + '"></button>' +
                '<button type="button" class="admin-row-btn role-res-clear" data-kind="' + kind + '"></button>' +
                '</div>' +
                '<div class="role-res-list" id="role-res-list-' + kind + '"></div>' +
                '<div class="flex items-center justify-between pt-2 text-xs text-slate-400">' +
                '<span class="role-res-total" data-kind="' + kind + '"></span>' +
                '<div class="role-res-pagination" data-kind="' + kind + '"></div>' +
                '</div></div></div>';
        }).join('');
        el.innerHTML =
            '<div class="role-editor-head">' +
            '<button type="button" class="role-editor-back" id="role-editor-back"></button>' +
            '<div class="role-editor-title-row">' +
            '<div class="min-w-0">' +
            '<h2 id="role-editor-title" class="text-xl font-bold text-slate-800 dark:text-slate-100 m-0"></h2>' +
            '<p id="role-editor-sub" class="text-xs text-slate-400 mt-1 truncate"></p>' +
            '</div>' +
            '<span class="role-dirty-pill" id="role-dirty-pill"></span>' +
            '</div>' +
            '<nav class="role-editor-tabs" role="tablist">' + tabHtml + '</nav>' +
            '</div>' +
            '<div class="role-editor-body">' +
            '<div class="role-editor-panel active" id="role-panel-basic">' +
            '<div class="role-editor-block-title" id="role-block-basic-title"></div>' +
            '<p class="text-sm text-slate-500 dark:text-slate-400 mb-4" id="role-basic-hint"></p>' +
            '<div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-2">' +
            '<div class="agent-field"><label class="agent-field-label" id="role-label-code"></label>' +
            '<div id="role-code-wrap"></div></div>' +
            '<div class="agent-field"><label class="agent-field-label" id="role-label-name"></label>' +
            '<input type="text" id="adm-fld-name" class="agent-input" value=""></div>' +
            '</div>' +
            '<div class="role-editor-block-title" id="role-block-perms-title"></div>' +
            '<p class="text-sm text-slate-500 dark:text-slate-400 mb-3" id="role-perms-hint"></p>' +
            '<div class="flex flex-wrap gap-2 items-center mb-3">' +
            '<input type="text" id="role-perm-search" class="agent-input flex-1 min-w-[180px]" placeholder="">' +
            '<button type="button" class="admin-row-btn" id="role-perm-clear-all"></button>' +
            '</div>' +
            '<div id="adm-fld-permissions"></div>' +
            '</div>' +
            kindPanels +
            '<div class="role-editor-panel" id="role-panel-model">' +
            '<div class="role-editor-block-title" id="role-block-model-assign-title"></div>' +
            '<p class="text-sm text-slate-500 dark:text-slate-400 mb-4" id="role-model-assign-hint"></p>' +
            '<div class="role-res-picker mb-4" id="role-res-manage-model">' +
            '<div class="flex flex-wrap gap-2 items-center mb-3">' +
            '<input type="text" class="agent-input flex-1 min-w-[180px] role-res-search" data-kind="model" placeholder="">' +
            '<button type="button" class="admin-row-btn role-res-selectall" data-kind="model"></button>' +
            '<button type="button" class="admin-row-btn role-res-clear" data-kind="model"></button>' +
            '</div>' +
            '<div class="role-res-list" id="role-res-list-model"></div>' +
            '<div class="flex items-center justify-between pt-2 text-xs text-slate-400">' +
            '<span class="role-res-total" data-kind="model"></span>' +
            '<div class="role-res-pagination" data-kind="model"></div>' +
            '</div></div>' +
            '<div class="role-editor-block-title" id="role-block-model-defaults-title"></div>' +
            '<p class="text-sm text-slate-500 dark:text-slate-400 mb-3" id="role-model-defaults-hint"></p>' +
            '<div id="adm-fld-modeldefaults" class="role-res-picker space-y-2"></div>' +
            '</div>' +
            '</div>' +
            '<p id="role-editor-error" class="hidden px-7 text-xs text-red-500"></p>' +
            '<div class="role-editor-foot">' +
            '<div class="text-xs text-slate-400" id="role-editor-foot-hint"></div>' +
            '<div class="flex items-center gap-2">' +
            '<button type="button" class="px-4 py-2 rounded-lg text-sm font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/10" id="role-editor-cancel"></button>' +
            '<button type="button" class="px-5 py-2 rounded-lg bg-primary-500 hover:bg-primary-600 text-white text-sm font-medium" id="role-editor-submit"></button>' +
            '</div></div>';

        const view = document.getElementById('view-roles');
        if (view) {
            if (!view.style.position) view.style.position = 'relative';
            el.style.position = 'absolute';
            el.style.inset = '0';
            el.style.zIndex = '20';
            view.appendChild(el);
        } else {
            document.body.appendChild(el);
        }

        el.querySelectorAll('.role-editor-tab').forEach(function (btn) {
            btn.addEventListener('click', function () {
                switchRoleEditorTab(btn.getAttribute('data-tab'));
            });
        });
        document.getElementById('role-editor-back').addEventListener('click', function () { closeRoleEditor(); });
        document.getElementById('role-editor-cancel').addEventListener('click', function () { closeRoleEditor(); });
        document.getElementById('role-editor-submit').addEventListener('click', submitRoleEditor);
        document.getElementById('adm-fld-name').addEventListener('input', markRoleEditorDirty);
        document.getElementById('role-perm-search').addEventListener('input', function () {
            renderRolePermissions(_roleEditor.perms || [], _collectRolePermissionIds());
        });
        document.getElementById('role-perm-clear-all').addEventListener('click', function () {
            renderRolePermissions(_roleEditor.perms || [], []);
            markRoleEditorDirty();
            updateRoleEditorBadges();
        });
        el.querySelectorAll('.role-res-search').forEach(function (inp) {
            inp.addEventListener('input', function () {
                const kind = inp.getAttribute('data-kind');
                const st = _resourceState[kind];
                if (!st) return;
                st.q = inp.value || '';
                st.page = 1;
                _renderRoleKindList(kind);
            });
        });
        el.querySelectorAll('.role-res-selectall').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const kind = btn.getAttribute('data-kind');
                const list = document.getElementById('role-res-list-' + kind);
                if (!list) return;
                const st = _resourceState[kind];
                list.querySelectorAll('input[type=checkbox]').forEach(function (cb) {
                    st.selected.add(cb.value);
                    cb.checked = true;
                    if (cb.parentElement) cb.parentElement.classList.add('checked');
                });
                markRoleEditorDirty();
                updateRoleEditorBadges();
                if (kind === 'model') _refreshRoleModelDefaults();
            });
        });
        el.querySelectorAll('.role-res-clear').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const kind = btn.getAttribute('data-kind');
                const st = _resourceState[kind];
                if (st) st.selected.clear();
                _renderRoleKindList(kind);
                markRoleEditorDirty();
                updateRoleEditorBadges();
                if (kind === 'model') _refreshRoleModelDefaults();
            });
        });
        return el;
    }

    function _localizeRoleEditorChrome() {
        const tabs = ['basic', 'menu', 'skill', 'tool', 'agent', 'model'];
        tabs.forEach(function (tab) {
            const label = document.querySelector('.role-editor-tab-label[data-tab-label="' + tab + '"]');
            if (label) label.textContent = _roleEditorTabLabel(tab);
        });
        const setText = function (id, value) {
            const el = document.getElementById(id);
            if (el) el.textContent = value;
        };
        setText('role-editor-back', '← ' + (t('role_editor_back') || t('roles_title') || '返回角色列表'));
        setText('role-dirty-pill', t('role_dirty_pill') || '有未保存更改');
        setText('role-block-basic-title', t('role_section_basic') || '基本信息');
        setText('role-basic-hint', t('role_basic_hint') || '填写角色标识；下方配置功能权限。');
        setText('role-label-code', t('admin_field_code'));
        setText('role-label-name', t('admin_field_name') + ' *');
        setText('role-block-perms-title', t('admin_field_permissions'));
        setText('role-perms-hint', t('admin_field_permissions_hint'));
        setText('role-perm-clear-all', t('admin_resource_clear'));
        const permSearch = document.getElementById('role-perm-search');
        if (permSearch) permSearch.placeholder = t('role_perm_search_placeholder') || '搜索权限…';
        setText('role-block-model-assign-title', t('role_section_model_assign') || '可分配模型');
        setText('role-model-assign-hint', t('role_model_assign_hint') || '勾选该角色可使用的模型；默认模型只能从已选项中选择。');
        setText('role-block-model-defaults-title', t('admin_field_model_defaults') || '默认模型');
        setText('role-model-defaults-hint', t('admin_field_model_defaults_hint') || '');
        setText('role-editor-foot-hint', t('role_editor_foot_hint') || '切换 Tab 不丢草稿 · 离开前若有改动会确认');
        setText('role-editor-cancel', t('cancel'));
        ['menu', 'skill', 'tool', 'agent', 'model'].forEach(function (kind) {
            const search = document.querySelector('.role-res-search[data-kind="' + kind + '"]');
            if (search) search.placeholder = t('admin_resource_search_placeholder');
            const allBtn = document.querySelector('.role-res-selectall[data-kind="' + kind + '"]');
            if (allBtn) allBtn.textContent = t('admin_resource_selectall');
            const clearBtn = document.querySelector('.role-res-clear[data-kind="' + kind + '"]');
            if (clearBtn) clearBtn.textContent = t('admin_resource_clear');
            const hint = document.querySelector('.role-panel-hint[data-kind-hint="' + kind + '"]');
            if (hint) hint.textContent = (t('admin_field_resource_grants_hint') || '');
        });
    }

    function switchRoleEditorTab(tab) {
        _roleEditor.activeTab = tab || 'basic';
        document.querySelectorAll('.role-editor-tab').forEach(function (btn) {
            btn.classList.toggle('active', btn.getAttribute('data-tab') === _roleEditor.activeTab);
        });
        document.querySelectorAll('.role-editor-panel').forEach(function (panel) {
            panel.classList.toggle('active', panel.id === 'role-panel-' + _roleEditor.activeTab);
        });
        if (_roleEditor.activeTab !== 'basic') {
            _renderRoleKindList(_roleEditor.activeTab === 'model' ? 'model' : _roleEditor.activeTab);
        }
        if (_roleEditor.activeTab === 'model') _refreshRoleModelDefaults();
    }

    function updateRoleEditorBadges() {
        const permEl = document.getElementById('role-badge-perms');
        if (permEl) permEl.textContent = String(_collectRolePermissionIds().length);
        ['menu', 'skill', 'tool', 'agent', 'model'].forEach(function (kind) {
            const el = document.getElementById('role-badge-' + kind);
            if (el) el.textContent = String(_resCount(kind));
        });
    }

    function _collectRolePermissionIds() {
        const root = document.getElementById('adm-fld-permissions');
        if (!root) return [];
        return Array.prototype.slice.call(root.querySelectorAll('input:checked')).map(function (i) { return i.value; });
    }

    function renderRolePermissions(catalog, selected) {
        const root = document.getElementById('adm-fld-permissions');
        if (!root) return;
        const selectedSet = {};
        (selected || []).forEach(function (id) { selectedSet[id] = true; });
        const q = ((document.getElementById('role-perm-search') || {}).value || '').trim().toLowerCase();
        const groups = permGroups(catalog);
        const keys = Object.keys(groups);
        // Stable-ish order: nonempty groups first alphabetically, then blank.
        keys.sort(function (a, b) {
            if (!a) return 1;
            if (!b) return -1;
            return a.localeCompare(b);
        });
        let html = '';
        keys.forEach(function (g) {
            const items = (groups[g] || []).filter(function (p) {
                if (!q) return true;
                const hay = ((p.group || '') + ' ' + (p.label || '') + ' ' + (p.id || '')).toLowerCase();
                return hay.indexOf(q) !== -1;
            });
            if (!items.length) return;
            const selectedCount = items.filter(function (p) { return selectedSet[p.id]; }).length;
            const allOn = selectedCount === items.length;
            const title = g || (t('admin_field_permissions') || '权限');
            html += '<div class="role-perm-group" data-group="' + escapeHtml(g) + '">' +
                '<div class="role-perm-group-head">' +
                '<div class="text-sm font-semibold text-slate-700 dark:text-slate-200">' + escapeHtml(title) +
                ' <span class="text-xs font-normal text-slate-400">' + selectedCount + ' / ' + items.length + '</span></div>' +
                '<button type="button" class="admin-row-btn role-perm-group-toggle" data-group="' + escapeHtml(g) + '" data-all="' + (allOn ? '1' : '0') + '">' +
                (allOn ? (t('role_perm_clear_group') || '清空本组') : (t('role_perm_select_group') || '全选本组')) +
                '</button></div><div class="role-perm-group-body">';
            items.forEach(function (p) {
                const checked = !!selectedSet[p.id];
                html += '<label class="role-perm-item' + (checked ? ' checked' : '') + '">' +
                    '<input type="checkbox" value="' + escapeHtml(p.id) + '"' + (checked ? ' checked' : '') + '>' +
                    escapeHtml(p.label || p.id) + '</label>';
            });
            html += '</div></div>';
        });
        if (!html) {
            html = '<div class="text-sm text-slate-400 py-6 text-center">' + escapeHtml(t('admin_resources_none') || '无匹配') + '</div>';
        }
        root.innerHTML = html;
        root.querySelectorAll('input[type=checkbox]').forEach(function (cb) {
            cb.addEventListener('change', function () {
                if (cb.parentElement) cb.parentElement.classList.toggle('checked', !!cb.checked);
                markRoleEditorDirty();
                updateRoleEditorBadges();
            });
        });
        root.querySelectorAll('.role-perm-group-toggle').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const group = btn.getAttribute('data-group') || '';
                const turnOn = btn.getAttribute('data-all') !== '1';
                const current = _collectRolePermissionIds();
                const set = {};
                current.forEach(function (id) { set[id] = true; });
                (groups[group] || []).forEach(function (p) {
                    if (turnOn) set[p.id] = true;
                    else delete set[p.id];
                });
                renderRolePermissions(catalog, Object.keys(set));
                markRoleEditorDirty();
                updateRoleEditorBadges();
            });
        });
    }

    async function _renderRoleKindList(kind) {
        const manage = document.getElementById('role-res-manage-' + kind);
        const list = document.getElementById('role-res-list-' + kind);
        if (!manage || !list) return;
        const st = _resourceState[kind];
        if (!st) return;
        list.innerHTML = '<div class="text-xs text-slate-400 py-2">' + escapeHtml(t('admin_loading') || '…') + '</div>';
        try {
            const data = await _loadResourceCatalog(kind, st.q, st.page, st.pageSize);
            const items = data.items || [];
            const total = data.total || 0;
            const actions = data.resource_actions || _resourceActions[kind] || [];
            st.actions = actions;
            st.loaded = true;
            if (!items.length) {
                list.innerHTML = '<div class="text-xs text-slate-400 py-2">' + escapeHtml(t('admin_resources_none')) + '</div>';
            } else {
                list.innerHTML = items.map(function (r) {
                    const rid = r.resource_id;
                    const checked = st.selected.has(rid);
                    const idSuffix = rid && rid.indexOf('nav:') === 0 ? rid.slice(4) : rid;
                    return '<label class="role-res-item' + (checked ? ' checked' : '') + '">' +
                        '<input type="checkbox" value="' + escapeHtml(rid) + '"' + (checked ? ' checked' : '') + '>' +
                        '<span class="flex flex-col leading-tight min-w-0"><span class="truncate">' + escapeHtml(r.name) + '</span>' +
                        (idSuffix && idSuffix !== r.name ? '<span class="text-[10px] text-slate-400 truncate">' + escapeHtml(idSuffix) + '</span>' : '') +
                        '</span></label>';
                }).join('');
            }
            list.querySelectorAll('input[type=checkbox]').forEach(function (cb) {
                cb.addEventListener('change', function () {
                    if (cb.checked) st.selected.add(cb.value);
                    else st.selected.delete(cb.value);
                    if (cb.parentElement) cb.parentElement.classList.toggle('checked', !!cb.checked);
                    markRoleEditorDirty();
                    updateRoleEditorBadges();
                    if (kind === 'model') _refreshRoleModelDefaults();
                });
            });
            const totalEl = manage.querySelector('.role-res-total[data-kind="' + kind + '"]');
            if (totalEl) totalEl.textContent = (t('admin_total_label') || '共') + ' ' + total;
            const pag = manage.querySelector('.role-res-pagination[data-kind="' + kind + '"]');
            if (pag && typeof renderPagination === 'function') {
                renderPagination(pag, st.page, st.pageSize, total, function (p) {
                    st.page = p;
                    _renderRoleKindList(kind);
                });
            } else if (pag) {
                pag.innerHTML = '';
            }
        } catch (err) {
            list.innerHTML = '<div class="text-xs text-red-500 py-2">' + escapeHtml(err.message || t('load_error')) + '</div>';
        }
    }

    function _refreshRoleModelDefaults() {
        const node = document.getElementById('adm-fld-modeldefaults');
        if (!node) return;
        const modelSel = (_resourceState.model && _resourceState.model.selected) ? _resourceState.model.selected : new Set();
        const options = Array.from(modelSel);
        node.innerHTML = _modelCapabilities.map(function (cap) {
            const val = _modelDefaultSel[cap] || '';
            const opts = '<option value="">' + escapeHtml(t('admin_resources_none')) + '</option>' +
                options.map(function (rid) {
                    return '<option value="' + escapeHtml(rid) + '"' + (rid === val ? ' selected' : '') + '>' +
                        escapeHtml(rid) + '</option>';
                }).join('');
            return '<div class="flex items-center gap-2 py-1">' +
                '<span class="w-32 text-xs text-slate-500 dark:text-slate-400">' + escapeHtml(cap) + '</span>' +
                '<select class="agent-input model-default-select" data-cap="' + escapeHtml(cap) + '">' + opts + '</select>' +
                '</div>';
        }).join('');
        // Drop defaults that are no longer granted.
        Object.keys(_modelDefaultSel).forEach(function (cap) {
            if (_modelDefaultSel[cap] && !modelSel.has(_modelDefaultSel[cap])) delete _modelDefaultSel[cap];
        });
        node.querySelectorAll('.model-default-select').forEach(function (sel) {
            sel.addEventListener('change', function () {
                const cap = sel.getAttribute('data-cap');
                const v = sel.value;
                if (v) _modelDefaultSel[cap] = v;
                else delete _modelDefaultSel[cap];
                markRoleEditorDirty();
            });
        });
    }

    function closeRoleEditorErr() {
        const el = document.getElementById('role-editor-error');
        if (el) { el.classList.add('hidden'); el.textContent = ''; }
    }

    function showRoleEditorErr(msg) {
        const el = document.getElementById('role-editor-error');
        if (el) { el.textContent = msg || ''; el.classList.remove('hidden'); }
    }

    function closeRoleEditor() {
        if (_roleEditor.dirty && !confirmDiscard(true)) return;
        closeRoleEditorNoPrompt();
    }

    function closeRoleEditorNoPrompt() {
        const el = document.getElementById('role-editor');
        if (el) el.classList.add('hidden');
        _roleEditor = {
            open: false, dirty: false, mode: 'create', role: null,
            submit: null, onConflictReload: null, statusEl: null,
            successMsg: '', activeTab: 'basic', perms: null,
        };
    }

    async function openRoleEditorPage(cfg) {
        const el = ensureRoleEditor();
        _localizeRoleEditorChrome();
        _roleEditor = {
            open: true,
            dirty: false,
            mode: cfg.mode || 'create',
            role: cfg.role || null,
            submit: cfg.submit || null,
            onConflictReload: cfg.onConflictReload || null,
            statusEl: cfg.statusEl || null,
            successMsg: cfg.successMsg || t('admin_saved'),
            activeTab: 'basic',
            perms: cfg.perms || [],
        };
        document.getElementById('role-editor-title').textContent = cfg.title || '';
        document.getElementById('role-editor-sub').innerHTML = cfg.subtitleHtml || '';
        document.getElementById('role-editor-submit').textContent = cfg.submitLabel || t('admin_save');
        document.getElementById('role-dirty-pill').classList.remove('show');
        closeRoleEditorErr();

        const codeWrap = document.getElementById('role-code-wrap');
        if (cfg.codeLocked) {
            codeWrap.innerHTML = '<div class="agent-input-locked" id="adm-fld-code">' + escapeHtml(cfg.code || '') + '</div>';
        } else {
            codeWrap.innerHTML = '<input type="text" id="adm-fld-code" class="agent-input" value="' + escapeHtml(cfg.code || '') + '" placeholder="' + escapeHtml(t('admin_field_code_hint') || '') + '">';
            const codeInput = document.getElementById('adm-fld-code');
            if (codeInput) codeInput.addEventListener('input', markRoleEditorDirty);
        }
        const nameInput = document.getElementById('adm-fld-name');
        nameInput.value = cfg.name || '';

        renderRolePermissions(cfg.perms || [], cfg.selectedPermissions || []);
        updateRoleEditorBadges();
        switchRoleEditorTab('basic');
        // Prefetch model list so defaults can resolve immediately on the model tab.
        _renderRoleKindList('model');
        el.classList.remove('hidden');
        try { nameInput.focus(); } catch (e) {}
    }

    async function submitRoleEditor() {
        if (!_roleEditor.submit) return;
        closeRoleEditorErr();
        const nameEl = document.getElementById('adm-fld-name');
        const codeEl = document.getElementById('adm-fld-code');
        const name = (nameEl && nameEl.value || '').trim();
        if (!name) {
            showRoleEditorErr(t('admin_field_name') + ' *');
            switchRoleEditorTab('basic');
            return;
        }
        const body = {
            name: name,
            permissions: _collectRolePermissionIds(),
            resource_grants: _collectResourceGrants(),
            model_defaults: _collectModelDefaults(),
        };
        if (_roleEditor.mode !== 'edit') {
            const code = (codeEl && codeEl.value || '').trim();
            if (!code) {
                showRoleEditorErr(t('admin_field_code') + ' *');
                switchRoleEditorTab('basic');
                return;
            }
            body.code = code;
        }
        const btn = document.getElementById('role-editor-submit');
        if (btn) btn.disabled = true;
        try {
            await _roleEditor.submit(body);
            const statusEl = _roleEditor.statusEl;
            const msg = _roleEditor.successMsg;
            closeRoleEditorNoPrompt();
            if (statusEl) status(statusEl, msg, true);
            await loadRolesView();
        } catch (err) {
            if (btn) btn.disabled = false;
            if (err && (err.status === 409 || err.code === 'conflict')) {
                showRoleEditorErr(err.message || t('admin_conflict'));
                if (_roleEditor.onConflictReload) _roleEditor.onConflictReload();
                return;
            }
            showRoleEditorErr((err && err.message) || t('admin_save_failed'));
        }
    }

    async function openRoleCreate() {
        const perms = await ensurePermCatalog();
        if (!perms) return;
        _resetResourceState([], {});
        await openRoleEditorPage({
            mode: 'create',
            title: t('role_create'),
            subtitleHtml: escapeHtml(t('role_create_sub') || '创建后编码不可修改'),
            codeLocked: false,
            code: '',
            name: '',
            perms: perms,
            selectedPermissions: [],
            submitLabel: t('admin_create'),
            statusEl: document.getElementById('role-status'),
            submit: async function (body) {
                await apiFetch(_roleApiBase(), { method: 'POST', body: body });
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
        await openRoleEditorPage({
            mode: 'edit',
            role: r,
            title: t('role_edit_title'),
            subtitleHtml: (t('admin_field_code') || '编码') + ' <code>' + escapeHtml(r.code || '') + '</code>',
            codeLocked: true,
            code: r.code || '',
            name: r.name || '',
            perms: perms,
            selectedPermissions: r.permissions || [],
            submitLabel: t('admin_save'),
            statusEl: document.getElementById('role-status'),
            submit: async function (body) {
                body.expected_version = r.version;
                await apiFetch(_roleApiBase() + '/' + encodeURIComponent(id), { method: 'POST', body: body });
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
        await openRoleEditorPage({
            mode: 'copy',
            role: r,
            title: t('role_copy_title'),
            subtitleHtml: escapeHtml((t('role_copy_from') || '从 {name} 复制').replace('{name}', r.name || r.code || '')),
            codeLocked: false,
            code: '',
            name: (r.name || '') + (t('role_copy_suffix') || '（副本）'),
            perms: perms,
            selectedPermissions: copyablePermissions,
            submitLabel: t('admin_create'),
            statusEl: document.getElementById('role-status'),
            submit: async function (body) {
                await apiFetch(_roleApiBase(), { method: 'POST', body: body });
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
        } else if (action === 'tenants') {
            if (kind === 'member') openMemberTenants(id);
        }
    }

    // Let console.js navigation ask whether it's safe to leave an admin view.
    // Returns true when there is no unsaved form (or the user confirms discard).
    function identityAdminDirtyGuard() {
        if (_roleEditor && _roleEditor.open && _roleEditor.dirty) {
            const ok = confirmDiscard(true);
            if (ok) closeRoleEditorNoPrompt();
            return ok;
        }
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
