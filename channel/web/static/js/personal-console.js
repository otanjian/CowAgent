// Member personal console (change enable-member-personal-console, tasks 8.2-8.4).
//
// The five personal pages reuse the console shell, sidebar and list patterns,
// but they are *self* surfaces: every request is scoped by the session
// (tenant + user), and the verb set per object is rendered from the server's own
// ``actions`` projection — never re-derived from a role name on the client.
//
// Three rules this file exists to keep:
//
// 1. **A denied page never starts its consumer.** ``loadPersonalView`` reads the
//    authoritative ``console_pages`` projection first and renders the denial
//    without issuing the data request, so a page the identity cannot read cannot
//    fire the API behind it (task 8.4).
// 2. **Read / configure / execute stay apart.** The page header reports all
//    three states from ``states``. A page whose channel execution is closed still
//    lists rows and still offers configuration verbs, but never claims a live
//    connection (task 7.5/8.1).
// 3. **Late responses are dropped.** Each view keeps a generation counter bumped
//    on tenant switch and on every reload, so a reply from a previous tenant or
//    a superseded request cannot repaint the page (task 8.4).
(function () {
    'use strict';

    // ---- the finite verb vocabulary ---------------------------------------
    // The server sends ``actions`` per object; the client only *renders* that
    // set, in this order, with this copy. An unknown verb is ignored, never
    // invented.
    const PERSONAL_VERB_ORDER = [
        'create', 'configure', 'edit', 'enable', 'disable', 'revoke',
        'bind', 'unbind', 'clear', 'delete',
    ];
    const PERSONAL_VERB_LABEL = {
        create: 'personal_action_create',
        configure: 'personal_action_configure',
        edit: 'personal_action_edit',
        enable: 'personal_action_enable',
        disable: 'personal_action_disable',
        revoke: 'personal_action_revoke',
        bind: 'personal_action_bind',
        unbind: 'personal_action_unbind',
        clear: 'personal_action_clear',
        delete: 'personal_action_delete',
    };
    const PERSONAL_STATE_LABEL = {
        read: 'personal_state_read',
        config: 'personal_state_config',
        execution: 'personal_state_execution',
    };

    //: capability switch name -> i18n key. Used by the denial body so a withdrawn
    //: capability is named, not merely implied (task 9.1). The keys here must
    //: stay in step with ``auth.policy.PERSONAL_CAPABILITY_SWITCHES``.
    const PERSONAL_SWITCH_LABEL = {
        member_personal_console: 'personal_switch_member_personal_console',
        user_private_agent_management: 'personal_switch_user_private_agent_management',
        personal_memory_write: 'personal_switch_personal_memory_write',
        personal_channel_onboarding: 'personal_switch_personal_channel_onboarding',
        personal_channel_runtime: 'personal_switch_personal_channel_runtime',
    };

    //: view id -> page id, list endpoint, list key, write endpoints and (for the
    //: catalog pages) the resource kind the personal-config API takes.
    const PERSONAL_VIEWS = [
        { id: 'personal-agents', page: 'personal.agents', title: 'personal_agents_title',
          desc: 'personal_agents_desc', endpoint: '/api/agents?view=personal',
          write: '/api/agents', listKey: 'agents', kind: 'agent',
          empty: 'personal_agents_empty' },
        { id: 'personal-channels', page: 'personal.channels', title: 'personal_channels_title',
          desc: 'personal_channels_desc', endpoint: '/api/personal/channels',
          write: '/api/personal/channels', itemWrite: '/api/personal/channels/',
          listKey: 'items', kind: 'channel', empty: 'personal_channels_empty' },
        { id: 'personal-memory', page: 'personal.memory', title: 'personal_memory_title',
          desc: 'personal_memory_desc', endpoint: '/api/memory/personal',
          write: '/api/memory/personal', listKey: 'entries', kind: 'memory',
          empty: 'personal_memory_empty' },
        { id: 'personal-tools', page: 'personal.tools', title: 'personal_tools_title',
          desc: 'personal_tools_desc', endpoint: '/api/personal/resources?kind=tool',
          write: '/api/personal/resources', resourceKind: 'tool', listKey: 'resources',
          kind: 'resource', empty: 'personal_resources_empty' },
        { id: 'personal-skills', page: 'personal.skills', title: 'personal_skills_title',
          desc: 'personal_skills_desc', endpoint: '/api/personal/resources?kind=skill',
          write: '/api/personal/resources', resourceKind: 'skill', listKey: 'resources',
          kind: 'resource', empty: 'personal_resources_empty' },
    ];

    // ---- pure helpers (unit-tested in tests/test_personal_console_frontend.cjs)

    function personalView(viewId) {
        return PERSONAL_VIEWS.filter(v => v.id === viewId)[0] || null;
    }

    // The page's authoritative projection, or null when the backend did not sign
    // it (not yet loaded / legacy). A null projection is never a denial: the
    // client refuses to guess, and the server still enforces on the request.
    function personalPageFor(context, pageId) {
        const pages = context && context.console_pages && typeof context.console_pages === 'object'
            ? context.console_pages : null;
        return (pages && pages[pageId]) || null;
    }

    // A page is denied when the server withheld the menu grant, or when the
    // identity can neither read it nor see it available. Mirrors
    // ``_viewNavDenied`` for the personal ids, so navigation and the page body
    // agree on one verdict.
    //
    // A withdrawn capability (task 9.1) is checked *before* the platform-admin
    // shortcut: a switch is a deployment state, not a grant, so an operator
    // cannot walk past one the deployment turned off for their own personal
    // surface either.
    function personalPageDenied(context, pageId) {
        const info = personalPageFor(context, pageId);
        if (!info) return false;
        if (info.menu_denied === true) return true;
        if (info.reason === 'capability_disabled') return true;
        if (context && context.authorization_mode === 'all') return false;
        return !(info.available || info.read_allowed);
    }

    // The switches a denied page names, as i18n label keys. Only the ones that
    // are actually off are reported, so the denial names the capability the
    // deployment withdrew instead of the whole chain.
    function personalDeniedSwitchKeys(info) {
        const switches = (info && info.switches) || null;
        if (!switches) return [];
        return Object.keys(switches)
            .filter(name => switches[name] !== true)
            .sort()
            .map(name => PERSONAL_SWITCH_LABEL[name])
            .filter(Boolean);
    }

    // The denial verdict with its reason, or null when the page may be shown.
    // One place decides, so the shell, the body and the tests cannot disagree.
    function personalPageDenial(context, pageId) {
        const info = personalPageFor(context, pageId);
        if (!info) return null;
        if (info.menu_denied === true) {
            return { reason: 'menu_not_granted', switchKeys: [] };
        }
        if (info.reason === 'capability_disabled') {
            return { reason: 'capability_disabled',
                     switchKeys: personalDeniedSwitchKeys(info) };
        }
        if (context && context.authorization_mode === 'all') return null;
        if (info.available || info.read_allowed) return null;
        return { reason: String(info.reason || 'no_permission'), switchKeys: [] };
    }

    // The three separated states as label keys for the page header. A page whose
    // execution is closed says so explicitly instead of staying silent — the gap
    // between "no execution consumer" and "configured but not live" is the whole
    // point of task 7.5.
    function personalStateKeys(info) {
        const states = (info && info.states) || null;
        if (!states) return [];
        const out = [];
        for (const key of ['read', 'config', 'execution']) {
            if (states[key] === true) out.push(PERSONAL_STATE_LABEL[key]);
            else if (key === 'execution') out.push('personal_state_execution_closed');
        }
        return out;
    }

    // The verbs the server offered for one object, in canonical order. Empty
    // ``actions`` yields no verbs: the client never falls back to the page's own
    // capabilities.
    function personalAllowedVerbs(actions) {
        if (!actions || typeof actions !== 'object') return [];
        return PERSONAL_VERB_ORDER.filter(verb => actions[verb] === true);
    }

    function personalVerbLabel(verb) {
        return PERSONAL_VERB_LABEL[verb] || '';
    }

    function personalRowBadges(row) {
        const badges = [];
        if (row && row.is_system_assistant) badges.push('personal_badge_system');
        else if (row && row.scope === 'private') badges.push('personal_badge_private');
        if (row && row.governance_disabled) badges.push('personal_channels_governance_disabled');
        return badges;
    }

    // Case-insensitive substring match over the fields a human would search.
    function personalFilterItems(items, query) {
        const list = Array.isArray(items) ? items : [];
        const q = String(query || '').trim().toLowerCase();
        if (!q) return list.slice();
        return list.filter(item => {
            if (!item || typeof item !== 'object') return false;
            return ['name', 'display_name', 'title', 'id', 'resource_id', 'agent_id',
                'channel_type', 'entry_id']
                .some(field => String(item[field] || '').toLowerCase().indexOf(q) >= 0);
        });
    }

    // 1-based page slice. ``total`` is the *filtered* count, so the counter can
    // never exceed what the identity is allowed to see (the list itself already
    // came back grant-filtered from the server).
    function personalPaginate(items, page, pageSize) {
        const list = Array.isArray(items) ? items : [];
        const size = Math.max(1, Number(pageSize) || 20);
        const pages = Math.max(1, Math.ceil(list.length / size));
        let current = Math.floor(Number(page) || 1);
        if (current < 1) current = 1;
        if (current > pages) current = pages;
        const start = (current - 1) * size;
        return { items: list.slice(start, start + size), total: list.length,
                 page: current, pages: pages, pageSize: size };
    }

    function personalTotalText(total, t) {
        return String(t('personal_total')).replace('{n}', String(total));
    }

    // A human row title, falling back to the id so a row is never blank.
    function personalRowTitle(row) {
        if (!row || typeof row !== 'object') return '';
        return String(row.display_name || row.name || row.title || row.resource_id
            || row.agent_id || row.entry_id || row.id || '');
    }

    // A channel type's display label, preferring the active language.
    function personalTypeLabel(item, lang) {
        const label = item && item.label;
        if (!label) return String((item && item.channel_type) || '');
        return String(label[lang] || label.zh || label.en || item.channel_type || '');
    }

    // The request one object verb makes. Table-driven so the verb a button
    // carries and the request it sends cannot drift apart, and so no verb can
    // reach a public maintenance path by accident: the agents page writes to
    // ``/api/agents`` with an explicit ``update`` action, the channel page to
    // ``/api/personal/channels/<id>``, and the catalog pages to the personal
    // resource endpoint only.
    function personalActionRequest(viewId, verb, row, values) {
        const meta = personalView(viewId);
        if (!meta || !verb) return null;
        const v = values || {};
        if (meta.kind === 'agent') {
            if (verb === 'enable' || verb === 'disable') {
                return { path: meta.write, method: 'POST',
                         body: { action: 'update', id: row && row.id,
                                 enabled: verb === 'enable' } };
            }
            if (verb === 'edit') {
                return { path: meta.write, method: 'POST',
                         body: { action: 'update', id: row && row.id,
                                 name: v.name === undefined ? undefined : v.name,
                                 description: v.description } };
            }
            if (verb === 'delete') {
                return { path: meta.write, method: 'POST',
                         body: { action: 'delete', id: row && row.id } };
            }
            if (verb === 'create') {
                return { path: meta.write, method: 'POST',
                         body: { action: 'create', id: v.id, name: v.name,
                                 description: v.description } };
            }
            return null;
        }
        if (meta.kind === 'channel') {
            if (verb === 'create') {
                return { path: meta.write, method: 'POST',
                         body: { channel_type: v.channel_type,
                                 display_name: v.display_name,
                                 agent_id: v.agent_id || '',
                                 credentials: v.credentials || {},
                                 recent_password: v.recent_password } };
            }
            const path = meta.itemWrite + String((row && row.id) || '');
            // The console's verb name is not always the API's action name: the
            // instance endpoint calls an edit ``update`` and the binding verbs
            // ``start_binding`` / ``unlink``. Sending the console verb straight
            // through would be refused as an unknown action, so the mapping is
            // explicit and pinned by tests/test_personal_console_frontend.cjs.
            const actionFor = { edit: 'update', bind: 'start_binding',
                               unbind: 'unlink' };
            const action = actionFor[verb] || verb;
            if (verb === 'edit') {
                return { path: path, method: 'POST',
                         body: { action: action,
                                 expected_version: row && row.version,
                                 display_name: v.display_name === undefined
                                     ? (row && row.display_name) : v.display_name,
                                 agent_id: v.agent_id,
                                 recent_password: v.recent_password } };
            }
            return { path: path, method: 'POST',
                     body: { action: action, expected_version: row && row.version,
                             recent_password: v.recent_password } };
        }
        if (meta.kind === 'memory') {
            if (verb === 'edit') {
                return { path: meta.write, method: 'POST',
                         body: { action: 'save', id: row && row.id,
                                 content: v.content, revision: v.revision } };
            }
            if (verb === 'delete') {
                return { path: meta.write, method: 'POST',
                         body: { action: 'delete', id: row && row.id,
                                 revision: v.revision } };
            }
            return null;
        }
        if (meta.kind === 'resource') {
            if (verb === 'configure') {
                return { path: meta.write, method: 'POST',
                         body: { action: 'save', resource_kind: meta.resourceKind,
                                 resource_id: v.resource_id
                                     || (row && row.resource_id),
                                 params: v.params || {},
                                 secret: v.secret } };
            }
            if (verb === 'clear') {
                return { path: meta.write, method: 'POST',
                         body: { action: 'clear', resource_kind: meta.resourceKind,
                                 resource_id: v.resource_id
                                     || (row && row.resource_id) } };
            }
            return null;
        }
        return null;
    }

    // The form a create/edit verb opens. Returns field descriptors so the modal
    // is pure markup and the payload assembly stays testable.
    function personalFormFields(viewId, verb, row, channelTypes) {
        const meta = personalView(viewId);
        if (!meta) return [];
        if (meta.kind === 'agent') {
            if (verb === 'create') {
                return [{ name: 'id', label: 'personal_agents_name', required: true },
                        { name: 'name', label: 'personal_agents_name', required: true }];
            }
            return [{ name: 'name', label: 'personal_agents_name',
                      value: personalRowTitle(row) }];
        }
        if (meta.kind === 'channel') {
            if (verb === 'create') {
                const ready = (channelTypes || []).filter(item => item.ready);
                return [{ name: 'channel_type', type: 'select', required: true,
                          options: ready.map(item => item.channel_type),
                          optionLabels: ready.reduce((acc, item) => {
                              acc[item.channel_type] = item.label || {};
                              return acc;
                          }, {}) },
                        { name: 'display_name', label: 'personal_channels_display_name',
                          required: true },
                        { name: 'recent_password', type: 'password', required: true,
                          label: 'personal_channels_password' }];
            }
            // Editing, enabling, disabling and revoking all re-prove presence
            // with the member's own password server-side, so the form collects
            // it instead of sending a request that would come back 401.
            return [{ name: 'display_name', label: 'personal_channels_display_name',
                      value: personalRowTitle(row) },
                    { name: 'recent_password', type: 'password',
                      label: 'personal_channels_password' }];
        }
        if (meta.kind === 'memory') {
            return [{ name: 'content', type: 'textarea', required: true }];
        }
        if (meta.kind === 'resource') {
            return [{ name: 'params', type: 'textarea',
                      help: 'personal_resources_public_hint' },
                    { name: 'secret', type: 'password' }];
        }
        return [];
    }

    // The credential inputs a channel type's create form must collect. Comes
    // from the server's own field declaration, so the console cannot require a
    // key the create path does not accept.
    function personalCredentialFields(item, lang) {
        const fields = (item && item.credential_fields) || [];
        return fields.map(field => ({
            key: field.key,
            label: String((field.label && (field.label[lang] || field.label.zh
                || field.label.en)) || field.key),
            secret: field.secret === true,
            required: field.required === true,
        }));
    }

    // The second step of "register a channel": the credential keys the selected
    // type declares. Separate from ``personalFormFields`` because the keys are a
    // property of the *type* the member picks in the first step, and the server
    // owns that declaration (``credential_fields``) — the console must not keep
    // a second copy that could drift from the one the create path validates.
    // ``rawLabel`` tells the modal the label is already resolved text, not an
    // i18n key.
    function personalChannelCredentialFields(item, lang) {
        return personalCredentialFields(item, lang).map(field => ({
            name: field.key,
            label: field.label,
            rawLabel: true,
            type: field.secret ? 'password' : 'text',
            required: field.required,
        }));
    }

    // ---- request plumbing --------------------------------------------------

    const _generation = {};
    const _loaded = {};
    const _state = {};

    function personalGeneration(viewId) {
        return _generation[viewId] || 0;
    }

    // Bump every personal view's generation so in-flight replies are discarded.
    // Called on tenant switch: a reply from the previous tenant must not repaint
    // a page that now belongs to another one (task 8.4).
    function invalidatePersonalViews() {
        PERSONAL_VIEWS.forEach(view => {
            _generation[view.id] = personalGeneration(view.id) + 1;
            _loaded[view.id] = false;
            _state[view.id] = Object.assign({ q: '', page: 1 }, _state[view.id] || {});
        });
    }

    function _currentContext() {
        return (typeof _baseAuthContext === 'function') ? _baseAuthContext() : null;
    }

    async function personalFetch(path, options) {
        const headers = Object.assign({}, (options && options.headers) || {});
        const tenant = sessionStorage.getItem('cow_tenant_id');
        if (tenant) headers['X-Tenant-ID'] = tenant;
        if (options && options.body) headers['Content-Type'] = 'application/json';
        const response = await fetch(path, {
            credentials: 'same-origin',
            method: (options && options.method) || 'GET',
            headers: headers,
            body: (options && options.body) ? JSON.stringify(options.body) : undefined,
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.status !== 'success') {
            const err = new Error(data.message || 'load-failed');
            err.status = response.status;
            err.code = data.code;
            throw err;
        }
        return data;
    }

    // ---- rendering ---------------------------------------------------------

    function _esc(value) {
        return (typeof escapeHtml === 'function')
            ? escapeHtml(String(value === undefined || value === null ? '' : value))
            : String(value === undefined || value === null ? '' : value);
    }

    function _lang() {
        return (typeof currentLang === 'string' && currentLang) ? currentLang : 'zh';
    }

    // Mount (once) a ``.view`` container for a personal page. The console shell
    // only needs one hook per page, so the fork keeps its markup here instead of
    // growing chat.html with five near-identical blocks.
    function _ensureContainer(viewId) {
        let node = document.getElementById('view-' + viewId);
        if (node) return node;
        const area = document.getElementById('content-area');
        if (!area) return null;
        node = document.createElement('div');
        node.id = 'view-' + viewId;
        node.className = 'view';
        area.appendChild(node);
        return node;
    }

    function _renderShell(viewId) {
        const meta = personalView(viewId);
        const node = _ensureContainer(viewId);
        if (!node || !meta) return null;
        node.innerHTML =
            '<div class="flex-1 overflow-y-auto p-6"><div class="max-w-5xl mx-auto">' +
            '<div class="flex items-start justify-between mb-6 gap-4">' +
            '<div><h2 class="text-xl font-bold text-slate-800 dark:text-slate-100" data-i18n="' + meta.title + '">' + _esc(t(meta.title)) + '</h2>' +
            '<p class="text-sm text-slate-500 dark:text-slate-400 mt-1" data-i18n="' + meta.desc + '">' + _esc(t(meta.desc)) + '</p>' +
            '<p class="text-xs text-slate-400 dark:text-slate-500 mt-1" data-i18n="personal_scope_hint">' + _esc(t('personal_scope_hint')) + '</p></div>' +
            '<div class="flex flex-col items-end gap-1 shrink-0">' +
            '<span class="text-xs px-2 py-0.5 rounded-full bg-slate-100 dark:bg-white/10 text-slate-500 dark:text-slate-400" data-i18n="personal_scope_self">' + _esc(t('personal_scope_self')) + '</span>' +
            '<span class="text-xs text-slate-400 dark:text-slate-500" data-personal-states></span>' +
            '<input type="search" data-personal-search class="mt-1 px-2 py-1 text-xs rounded-lg border border-slate-200 dark:border-white/10 bg-transparent" placeholder="' + _esc(t('personal_search_placeholder')) + '">' +
            '</div></div>' +
            '<div data-personal-body class="text-sm text-slate-500 dark:text-slate-400">' + _esc(t('personal_loading')) + '</div>' +
            '<div data-personal-footer class="flex items-center justify-between mt-4 text-xs text-slate-500 dark:text-slate-400"></div>' +
            '<div data-personal-status class="text-xs mt-2 opacity-0 transition-opacity duration-200"></div>' +
            '</div></div>';
        return node;
    }

    function _renderStates(viewId, info) {
        const node = document.getElementById('view-' + viewId);
        if (!node) return;
        const box = node.querySelector('[data-personal-states]');
        if (!box) return;
        const keys = personalStateKeys(info);
        box.textContent = keys.map(key => t(key)).join(' · ');
        box.dataset.personalStateKeys = keys.join(',');
    }

    function _renderDenied(viewId, denial) {
        const node = _renderShell(viewId);
        if (!node) return;
        const body = node.querySelector('[data-personal-body]');
        const reason = (denial && denial.reason) || '';
        // A withdrawn deployment capability reads differently from a missing
        // grant: the first is not the member's fault and not fixable by asking an
        // administrator for a role, so it says which capability is off.
        const hintKey = reason === 'capability_disabled'
            ? 'personal_denied_capability' : 'personal_denied_hint';
        const switchKeys = (denial && denial.switchKeys) || [];
        const list = switchKeys.length
            ? '<ul class="mt-2 space-y-1 text-xs text-slate-500 dark:text-slate-400">' +
              switchKeys.map(key => '<li>' + _esc(t(key)) + '</li>').join('') +
              '</ul>'
            : '';
        body.dataset.personalDeniedReason = reason;
        body.innerHTML =
            '<div class="py-16 text-center">' +
            '<div class="font-medium text-slate-600 dark:text-slate-300">' + _esc(t('personal_denied_title')) + '</div>' +
            '<p class="mt-1">' + _esc(t(hintKey)) + '</p>' + list + '</div>';
    }

    function _verbButtons(row, index) {
        const verbs = personalAllowedVerbs(row && row.actions);
        return verbs.map(verb => {
            const label = personalVerbLabel(verb);
            return '<button type="button" data-personal-verb="' + _esc(verb) + '" data-personal-index="' + index + '"' +
                ' class="px-2 py-1 rounded-md text-xs border border-slate-200 dark:border-white/10 hover:bg-slate-100 dark:hover:bg-white/10 cursor-pointer">' +
                _esc(label ? t(label) : verb) + '</button>';
        }).join(' ');
    }

    function _renderRows(viewId, rows) {
        const meta = personalView(viewId);
        const node = document.getElementById('view-' + viewId);
        if (!node || !meta) return;
        const body = node.querySelector('[data-personal-body]');
        const footer = node.querySelector('[data-personal-footer]');
        const state = _state[viewId] || (_state[viewId] = { q: '', page: 1 });
        state.rows = rows;
        const filtered = personalFilterItems(rows, state.q);
        const page = personalPaginate(filtered, state.page, state.pageSize || 20);
        state.page = page.page;
        if (!page.total) {
            body.innerHTML = '<div class="py-16 text-center">' + _esc(t(meta.empty)) + '</div>';
            footer.textContent = '';
            return;
        }
        const start = (page.page - 1) * page.pageSize;
        body.innerHTML = '<div class="bg-white dark:bg-[#1A1A1A] rounded-xl border border-slate-200 dark:border-white/10 divide-y divide-slate-100 dark:divide-white/5">' +
            page.items.map((row, i) => {
                const badges = personalRowBadges(row).map(key =>
                    '<span class="ml-2 text-[10px] px-1.5 py-0.5 rounded bg-slate-100 dark:bg-white/10 text-slate-500 dark:text-slate-400">' + _esc(t(key)) + '</span>').join('');
                return '<div class="flex items-center justify-between px-4 py-3 gap-3">' +
                    '<div class="min-w-0"><div class="truncate text-slate-800 dark:text-slate-100">' + _esc(personalRowTitle(row)) + badges + '</div></div>' +
                    '<div class="flex items-center gap-1 shrink-0" data-personal-verbs>' + _verbButtons(row, start + i) + '</div>' +
                    '</div>';
            }).join('') + '</div>';
        footer.textContent = personalTotalText(page.total, t);
    }

    function _renderFailure(viewId, message) {
        const node = _ensureContainer(viewId);
        if (!node) return;
        const body = node.querySelector('[data-personal-body]');
        if (body) {
            body.innerHTML = '<div class="py-16 text-center">' +
                _esc(message || t('personal_load_failed')) + '</div>';
        }
    }

    // ---- modal form --------------------------------------------------------

    // A minimal modal so the create/edit verbs have a real form. Returns
    // ``null`` when cancelled, otherwise the collected values keyed by field
    // name.
    let _modalOpen = false;

    function _openForm(titleKey, fields) {
        return new Promise(resolve => {
            _modalOpen = true;
            const overlay = document.createElement('div');
            overlay.className = 'fixed inset-0 z-50 flex items-center justify-center bg-black/40';
            const fieldHtml = fields.map(field => {
                let control;
                if (field.type === 'select') {
                    const options = (field.options || []).map(option => {
                        const optionLabel = (field.optionLabels && field.optionLabels[option]) || {};
                        const text = String(optionLabel[_lang()] || optionLabel.zh
                            || optionLabel.en || option);
                        return '<option value="' + _esc(option) + '">' + _esc(text) + '</option>';
                    }).join('');
                    control = '<select data-field="' + _esc(field.name) + '" class="w-full px-2 py-1 rounded-lg border border-slate-200 dark:border-white/10 bg-transparent">' + options + '</select>';
                } else if (field.type === 'textarea') {
                    control = '<textarea data-field="' + _esc(field.name) + '" rows="5" class="w-full px-2 py-1 rounded-lg border border-slate-200 dark:border-white/10 bg-transparent font-mono text-xs"></textarea>';
                } else {
                    control = '<input type="' + (field.type === 'password' ? 'password' : 'text') +
                        '" data-field="' + _esc(field.name) + '" value="' + _esc(field.value || '') +
                        '" class="w-full px-2 py-1 rounded-lg border border-slate-200 dark:border-white/10 bg-transparent">';
                }
                const labelText = field.label
                    ? _esc(field.rawLabel ? field.label : t(field.label)) : '';
                const help = field.help
                    ? '<div class="text-[10px] text-slate-400 dark:text-slate-500 mt-1">' + _esc(t(field.help)) + '</div>'
                    : '';
                return '<div><div class="text-xs text-slate-500 dark:text-slate-400 mb-1">' +
                    labelText + '</div>' + control + help + '</div>';
            }).join('');
            overlay.innerHTML =
                '<form class="bg-white dark:bg-[#1A1A1A] rounded-xl p-5 w-[420px] max-w-[92vw] shadow-xl text-sm">' +
                '<div class="font-medium mb-3 text-slate-800 dark:text-slate-100">' + _esc(t(titleKey)) + '</div>' +
                '<div class="space-y-3">' + fieldHtml + '</div>' +
                '<div class="flex justify-end gap-2 mt-4">' +
                '<button type="button" data-cancel class="px-3 py-1.5 rounded-lg text-xs border border-slate-200 dark:border-white/10 cursor-pointer">' + _esc(t('cancel')) + '</button>' +
                '<button type="submit" class="px-3 py-1.5 rounded-lg text-xs bg-slate-800 text-white dark:bg-white dark:text-slate-900 cursor-pointer">' + _esc(t('save')) + '</button>' +
                '</div></form>';
            document.body.appendChild(overlay);
            const close = value => { _modalOpen = false; overlay.remove(); resolve(value); };
            overlay.querySelector('[data-cancel]').addEventListener('click', () => close(null));
            overlay.addEventListener('click', event => {
                if (event.target === overlay) close(null);
            });
            overlay.querySelector('form').addEventListener('submit', event => {
                event.preventDefault();
                const values = {};
                fields.forEach(field => {
                    const input = overlay.querySelector('[data-field="' + field.name + '"]');
                    values[field.name] = input ? input.value : '';
                });
                close(values);
            });
        });
    }

    // ---- loaders -----------------------------------------------------------

    async function loadPersonalView(viewId, force) {
        const meta = personalView(viewId);
        if (!meta) return;
        _ensureContainer(viewId);
        const context = _currentContext();
        const info = personalPageFor(context, meta.page);
        const denial = personalPageDenial(context, meta.page);
        if (denial) {
            // Denied: render the denial and stop. No request is issued, so the
            // consumer behind the page is never started by a denied visit.
            _renderDenied(viewId, denial);
            _renderStates(viewId, info);
            return;
        }
        if (!_loaded[viewId]) _renderShell(viewId);
        _renderStates(viewId, info);
        if (_loaded[viewId] && force !== true) return;
        const generation = personalGeneration(viewId) + 1;
        _generation[viewId] = generation;
        _loaded[viewId] = false;
        try {
            const data = await personalFetch(meta.endpoint);
            if (generation !== personalGeneration(viewId)) return; // late response
            const rows = data[meta.listKey] || data.items || data.agents || [];
            _state[viewId] = Object.assign({ q: '', page: 1, rows: rows },
                _state[viewId] || {}, { rows: rows });
            _renderRows(viewId, rows);
            _loaded[viewId] = true;
        } catch (err) {
            if (generation !== personalGeneration(viewId)) return;
            if (err && err.status === 403) {
                _renderDenied(viewId, { reason: 'forbidden', switchKeys: [] });
                return;
            }
            _renderFailure(viewId, t('personal_load_failed'));
        }
    }

    function _bindShell(viewId) {
        const node = document.getElementById('view-' + viewId);
        if (!node || node.dataset.personalBound === '1') return;
        node.dataset.personalBound = '1';
        const search = node.querySelector('[data-personal-search]');
        if (search) {
            search.addEventListener('input', () => {
                const state = _state[viewId] || (_state[viewId] = {});
                state.q = search.value;
                state.page = 1;
                _renderRows(viewId, state.rows || []);
            });
        }
        node.addEventListener('click', event => {
            const button = event.target.closest('[data-personal-verb]');
            if (!button || !node.contains(button)) return;
            const rows = (_state[viewId] && _state[viewId].rows) || [];
            const row = rows[Number(button.dataset.personalIndex)];
            _dispatch(viewId, button.dataset.personalVerb, row);
        });
    }

    async function _dispatch(viewId, verb, row) {
        const meta = personalView(viewId);
        if (!meta || !verb) return;
        let values = null;
        if (verb === 'create' || verb === 'edit') {
            let types = [];
            if (meta.kind === 'channel' && verb === 'create') {
                try {
                    const data = await personalFetch(meta.endpoint);
                    types = data.channel_types || [];
                } catch (_) { /* the form still opens with no options */ }
            }
            const fields = personalFormFields(viewId, verb, row, types);
            values = await _openForm(verb === 'create' ? meta.title : 'personal_action_edit', fields);
            if (values === null) return;
            if (meta.kind === 'channel' && verb === 'create') {
                // Second step: the credential keys the *selected* type declares.
                const selected = types.filter(
                    item => item.channel_type === values.channel_type)[0];
                const credentialFields = personalChannelCredentialFields(selected, _lang());
                if (credentialFields.length) {
                    const credentials = await _openForm('personal_channels_credentials',
                                                        credentialFields);
                    if (credentials === null) return;
                    values.credentials = {};
                    credentialFields.forEach(field => {
                        values.credentials[field.name] = credentials[field.name];
                    });
                }
            }
        } else if (meta.kind === 'channel'
                   && (verb === 'enable' || verb === 'disable' || verb === 'revoke')) {
            // These re-prove presence server-side, so the password is collected
            // here rather than sent missing and answered with a 401.
            values = await _openForm('personal_action_' + verb, [
                { name: 'recent_password', type: 'password', required: true,
                  label: 'personal_channels_password' }]);
            if (values === null) return;
        }
        const request = personalActionRequest(viewId, verb, row, values || {});
        if (!request) return;
        try {
            await personalFetch(request.path, { method: request.method, body: request.body });
            _setStatus(viewId, t('personal_saved'), true);
            await loadPersonalView(viewId, true);
        } catch (err) {
            _setStatus(viewId, err && err.code === 'conflict'
                ? t('personal_memory_revision_conflict') : t('personal_save_failed'), false);
        }
    }

    function _setStatus(viewId, text, ok) {
        const node = document.getElementById('view-' + viewId);
        if (!node) return;
        const el = node.querySelector('[data-personal-status]');
        if (!el) return;
        el.textContent = text || '';
        el.classList.remove('opacity-0');
        el.style.color = ok ? '' : '#ef4444';
        if (text) setTimeout(() => el.classList.add('opacity-0'), 4000);
    }

    function _makeLoader(viewId) {
        return function load(force) {
            const run = (async () => {
                try {
                    await loadPersonalView(viewId, force === true);
                } finally {
                    // The container is created lazily by the renderer, so the
                    // interaction bindings are attached after the first paint.
                    _bindShell(viewId);
                }
            })();
            return run;
        };
    }

    // ---- wiring ------------------------------------------------------------

    // Published so ``navigateTo`` can ask before leaving an open create/edit
    // form: "no unsaved input is discarded silently" (task 8.4).
    function personalConsoleDirtyGuard() {
        if (!_modalOpen) return true;
        return window.confirm(t('personal_unsaved_warning'));
    }

    if (typeof window !== 'undefined') {
        window.__personalConsoleDirtyGuard__ = personalConsoleDirtyGuard;
        window.PersonalConsole = {
            PERSONAL_VIEWS: PERSONAL_VIEWS,
            PERSONAL_VERB_ORDER: PERSONAL_VERB_ORDER,
            personalView: personalView,
            personalPageFor: personalPageFor,
            personalPageDenied: personalPageDenied,
            personalPageDenial: personalPageDenial,
            personalDeniedSwitchKeys: personalDeniedSwitchKeys,
            personalStateKeys: personalStateKeys,
            personalAllowedVerbs: personalAllowedVerbs,
            personalVerbLabel: personalVerbLabel,
            personalRowBadges: personalRowBadges,
            personalFilterItems: personalFilterItems,
            personalPaginate: personalPaginate,
            personalTotalText: personalTotalText,
            personalRowTitle: personalRowTitle,
            personalTypeLabel: personalTypeLabel,
            personalCredentialFields: personalCredentialFields,
            personalChannelCredentialFields: personalChannelCredentialFields,
            personalActionRequest: personalActionRequest,
            personalFormFields: personalFormFields,
            invalidatePersonalViews: invalidatePersonalViews,
            loadPersonalView: loadPersonalView,
            personalConsoleDirtyGuard: personalConsoleDirtyGuard,
        };
    }

    if (typeof window !== 'undefined' && typeof window.registerConsoleView === 'function') {
        PERSONAL_VIEWS.forEach(view => {
            window.registerConsoleView({
                id: view.id,
                label: view.title,
                load: _makeLoader(view.id),
                repaint: _makeLoader(view.id),
            });
        });
    }
})();
