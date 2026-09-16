// Per-domain i18n namespace: personal-console
//
// What survives here after change ``unify-console-by-data-scope`` (task 8.8)
// retired the independent member personal console (``personal-console.js``):
//
//   * ``nav_group_personal`` / ``menu_personal_*`` — the labels the *retired*
//     view ids keep in ``console.js``'s ``VIEW_META``. The ids are no longer
//     hosted, registered or signed; they stay resolvable as **addresses** so a
//     ``#view-personal-*`` bookmark forwards to the shared page that carries the
//     same objects (task 8.1). An unresolvable hash would fall through to the
//     area default instead of redirecting.
//   * ``personal_action_clear`` — used by the shared resource-detail component
//     (``console.js``) for "clear my saved parameters", which is a *formal*
//     console verb, not a personal page.
//
// Every other personal key was deleted together with the module it belonged to
// (109 keys x 3 languages, mirrored in ``tests/fixtures/console_i18n_snapshot.json``).
// Every key here exists in zh / zh-Hant / en per the parity contract.
(function () {
    'use strict';
    var registry = window.__cowI18N__ = window.__cowI18N__ || {};
    registry["personal-console"] = {
        "zh": {
            "nav_group_personal": "我的",
            "menu_personal_agents": "我的智能体",
            "menu_personal_channels": "我的渠道",
            "menu_personal_memory": "我的记忆",
            "menu_personal_tools": "我的工具",
            "menu_personal_skills": "我的技能",
            "personal_action_clear": "清除配置"
        },
        "zh-Hant": {
            "nav_group_personal": "我的",
            "menu_personal_agents": "我的智慧體",
            "menu_personal_channels": "我的渠道",
            "menu_personal_memory": "我的記憶",
            "menu_personal_tools": "我的工具",
            "menu_personal_skills": "我的技能",
            "personal_action_clear": "清除設定"
        },
        "en": {
            "nav_group_personal": "Mine",
            "menu_personal_agents": "My Agents",
            "menu_personal_channels": "My Channels",
            "menu_personal_memory": "My Memory",
            "menu_personal_tools": "My Tools",
            "menu_personal_skills": "My Skills",
            "personal_action_clear": "Clear configuration"
        }
    };
})();
