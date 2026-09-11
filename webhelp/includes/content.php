<?php
declare(strict_types=1);

/**
 * 结构化内容：仅描述「顺序、图标、外链、命令」等结构，
 * 所有可翻译文案在 lang/*.php 中按 id 关联。
 */

return [
    // 上游九项核心能力（文案：capabilities.items.<id>）
    // doc = 本地文档 slug，指向 docs/<slug>.html（见 includes/docs.php）
    'capabilities' => [
        ['id' => 'planning',   'icon' => 'planning',   'doc' => 'architecture'],
        ['id' => 'memory',     'icon' => 'memory',     'doc' => 'memory'],
        ['id' => 'knowledge',  'icon' => 'knowledge',  'doc' => 'knowledge'],
        ['id' => 'skills',     'icon' => 'skills',     'doc' => 'skills'],
        ['id' => 'tools',      'icon' => 'tools',      'doc' => 'tools'],
        ['id' => 'evolution',  'icon' => 'evolution',  'doc' => 'evolution'],
        ['id' => 'models',     'icon' => 'models',     'doc' => 'models'],
        ['id' => 'channels',   'icon' => 'channels',   'doc' => 'channels'],
        ['id' => 'multiagent', 'icon' => 'multiagent', 'doc' => 'multiagent'],
    ],

    // 企业级管控九项（文案：enterprise.items.<id>）
    'enterprise' => [
        ['id' => 'tenant',     'icon' => 'tenant'],
        ['id' => 'rbac',       'icon' => 'rbac'],
        ['id' => 'org',        'icon' => 'org'],
        ['id' => 'audit',      'icon' => 'audit'],
        ['id' => 'approval',   'icon' => 'approval'],
        ['id' => 'credential', 'icon' => 'credential'],
        ['id' => 'quota',      'icon' => 'quota'],
        ['id' => 'isolation',  'icon' => 'isolation'],
        ['id' => 'access',     'icon' => 'access'],
    ],

    // 管控流转四步（文案：enterprise.flow.<id>）
    'governance_flow' => [
        ['id' => 'identify', 'icon' => 'access'],
        ['id' => 'authorize', 'icon' => 'rbac'],
        ['id' => 'guard',    'icon' => 'approval'],
        ['id' => 'trace',    'icon' => 'audit'],
    ],

    // 快速开始五步（文案：quickstart.steps.<id>）
    'quickstart_steps' => [
        ['id' => 'install',   'icon' => 'download'],
        ['id' => 'configure', 'icon' => 'key'],
        ['id' => 'start',     'icon' => 'rocket'],
        ['id' => 'access',    'icon' => 'globe'],
        ['id' => 'channel',   'icon' => 'channels'],
    ],

    // 部署方式（命令见 config.php deployments）
    'deployments' => [
        ['id' => 'unix'],
        ['id' => 'win'],
        ['id' => 'docker'],
    ],

    // 内置工具（文案：tools.items.<id>）
    'tools' => [
        ['id' => 'file',      'icon' => 'book'],
        ['id' => 'bash',      'icon' => 'terminal'],
        ['id' => 'browser',   'icon' => 'globe'],
        ['id' => 'scheduler', 'icon' => 'clock'],
        ['id' => 'websearch', 'icon' => 'globe'],
        ['id' => 'memory',    'icon' => 'memory'],
        ['id' => 'sendfile',  'icon' => 'chat'],
        ['id' => 'env',       'icon' => 'key'],
        ['id' => 'mcp',       'icon' => 'layers'],
    ],

    // 终端 CLI（描述：cli.commands.<id>）
    'cli_commands' => [
        ['id' => 'start',   'cmd' => 'cow start'],
        ['id' => 'stop',    'cmd' => 'cow stop'],
        ['id' => 'restart', 'cmd' => 'cow restart'],
        ['id' => 'status',  'cmd' => 'cow status'],
        ['id' => 'logs',    'cmd' => 'cow logs'],
        ['id' => 'update',  'cmd' => 'cow update'],
        ['id' => 'skill',   'cmd' => 'cow skill install <name>'],
        ['id' => 'browser', 'cmd' => 'cow install-browser'],
    ],

    // 对话内斜杠命令（描述：cli.slash.<id>）
    'slash_commands' => [
        ['id' => 'skill_list',    'cmd' => '/skill list'],
        ['id' => 'skill_install', 'cmd' => '/skill install <name>'],
        ['id' => 'knowledge',     'cmd' => '/knowledge'],
        ['id' => 'model',         'cmd' => '/model'],
        ['id' => 'memory',        'cmd' => '/memory'],
    ],

    // 架构分层（文案：architecture.layers.<id>）
    'arch_layers' => [
        ['id' => 'access',     'icon' => 'channels'],
        ['id' => 'identity',   'icon' => 'access'],
        ['id' => 'agent',      'icon' => 'planning'],
        ['id' => 'capability', 'icon' => 'tools'],
        ['id' => 'storage',    'icon' => 'tenant'],
    ],

    // 请求流转（文案：architecture.flow.<id>）
    'arch_flow' => [
        ['id' => 'receive',  'icon' => 'channels'],
        ['id' => 'identify', 'icon' => 'access'],
        ['id' => 'authorize','icon' => 'rbac'],
        ['id' => 'plan',     'icon' => 'planning'],
        ['id' => 'act',      'icon' => 'tools'],
        ['id' => 'audit',    'icon' => 'audit'],
        ['id' => 'reply',    'icon' => 'sendfile'],
    ],

    // 企业控制台四个管理菜单（文案：enterprise.console.<id>）
    'console_menus' => [
        ['id' => 'user',   'icon' => 'org'],
        ['id' => 'tenant', 'icon' => 'tenant'],
        ['id' => 'role',   'icon' => 'rbac'],
        ['id' => 'org',    'icon' => 'org'],
    ],

    // 企业级权限管控模型（结构；文案见 lang/*.php 的 perm.*）
    // 核心设计原则四张卡（文案：perm.principles.<id>）
    'perm_principles' => [
        ['id' => 'ownership', 'icon' => 'shield'],
        ['id' => 'layering',  'icon' => 'layers'],
        ['id' => 'private',   'icon' => 'lock'],
        ['id' => 'traceable', 'icon' => 'audit'],
    ],
    // 三级权限架构（文案：perm.tiers.<id>）
    'perm_tiers'      => [
        ['id' => 'platform', 'icon' => 'tenant'],
        ['id' => 'tenant',   'icon' => 'rbac'],
        ['id' => 'user',     'icon' => 'access'],
    ],
    // 三类角色及其职责组（文案：perm.roles.<id>）
    'perm_roles'      => [
        ['id' => 'platform', 'icon' => 'tenant'],
        ['id' => 'tenant',   'icon' => 'rbac'],
        ['id' => 'user',     'icon' => 'access'],
    ],

    // FAQ（问答：faq.items.<id>）
    'faq' => ['q1', 'q2', 'q3', 'q4', 'q5', 'q6'],

    // 关于页理念（文案：about.mission.<id>）
    'mission' => [
        ['id' => 'open',  'icon' => 'github'],
        ['id' => 'grow',  'icon' => 'evolution'],
        ['id' => 'guard', 'icon' => 'access'],
        ['id' => 'local', 'icon' => 'credential'],
    ],

    // 关于页资源：原始外链已全部移除，站点不引用任何外部网站。
    'about_resources' => [],

    // 注意：本地能力文档清单不在这里维护，而是由构建脚本生成到
    // docs/manifest.php（tools/build-docs.php 产出），避免与上游重复维护。
];
