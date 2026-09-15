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

    // 产品使用手册（manual.php）
    // 面向使用者的「应用操作手册」：讲怎么用，不讲怎么装（安装见 quickstart.php）。
    // 章节顺序即页面与侧栏顺序：先工作台的日常使用，再管理控制台的配置与治理。
    // 结构只声明 id / 分组 / 图标 / 块清单 / 引用的文档 slug 与站内页面；
    // 文案全部在 lang/*.php 的 manual.* 下按同一 id 组织（语言包中按 id 索引，与顺序无关）。
    // 每章由若干「块」组成，块内可有 title / goal / steps / fields / note。
    'manual_parts' => [
        ['id' => 'workbench'],
        ['id' => 'console'],
        ['id' => 'personal'],
    ],

    'manual_sections' => [
        // ---- 工作台：日常使用 ----
        [
            'id' => 'start', 'part' => 'workbench', 'icon' => 'rocket',
            'blocks' => ['login', 'navigate', 'preference'],
            'docs' => [], 'links' => [],
        ],
        [
            'id' => 'chat', 'part' => 'workbench', 'icon' => 'chat',
            'blocks' => ['new', 'compose', 'attach', 'session', 'command'],
            'docs' => [], 'links' => [],
        ],
        [
            'id' => 'agents', 'part' => 'workbench', 'icon' => 'users',
            'blocks' => ['browse'],
            'docs' => [], 'links' => [],
        ],
        [
            'id' => 'knowledge', 'part' => 'workbench', 'icon' => 'knowledge',
            'blocks' => ['overview', 'category', 'document', 'import', 'bind'],
            'docs' => ['knowledge'], 'links' => [],
        ],
        [
            'id' => 'todo', 'part' => 'workbench', 'icon' => 'list',
            'blocks' => ['todo', 'task-view', 'task-edit'],
            'docs' => ['tools-scheduler'], 'links' => [],
        ],

        // ---- 管理控制台：配置与治理 ----
        [
            'id' => 'agents-admin', 'part' => 'console', 'icon' => 'users',
            'blocks' => ['create', 'configure', 'capability', 'corefiles'],
            'docs' => ['multiagent'], 'links' => [],
        ],
        [
            'id' => 'memory', 'part' => 'console', 'icon' => 'memory',
            'blocks' => ['view', 'dream', 'index'],
            'docs' => ['memory'], 'links' => [],
        ],
        [
            'id' => 'models', 'part' => 'console', 'icon' => 'models',
            'blocks' => ['basic', 'vendor', 'capability'],
            'docs' => ['models'], 'links' => [],
        ],
        [
            'id' => 'channels', 'part' => 'console', 'icon' => 'channels',
            'blocks' => ['scope', 'create', 'credentials', 'scan', 'bind'],
            'docs' => ['channels'], 'links' => [],
        ],
        [
            'id' => 'roles', 'part' => 'console', 'icon' => 'rbac',
            'blocks' => ['list', 'editor', 'permissions', 'resources', 'assign', 'rules'],
            'docs' => [], 'links' => [],
        ],
        [
            'id' => 'members', 'part' => 'console', 'icon' => 'org',
            'blocks' => ['create', 'edit', 'password', 'org'],
            'docs' => [], 'links' => [],
        ],
        [
            'id' => 'tenant', 'part' => 'console', 'icon' => 'tenant',
            'blocks' => ['tenant', 'audit'],
            'docs' => [], 'links' => [],
        ],

        // ---- 个人与参考 ----
        [
            'id' => 'account', 'part' => 'personal', 'icon' => 'key',
            'blocks' => ['profile', 'password', 'prefs', 'tenant-switch'],
            'docs' => [], 'links' => [],
        ],
        [
            'id' => 'commands', 'part' => 'personal', 'icon' => 'terminal',
            'blocks' => ['commands'],
            'docs' => ['cli-skill'], 'links' => [],
        ],
        [
            'id' => 'troubleshoot', 'part' => 'personal', 'icon' => 'x-circle',
            'blocks' => ['issues'],
            'docs' => [], 'links' => [],
        ],
        [
            'id' => 'further', 'part' => 'personal', 'icon' => 'book',
            'blocks' => ['docs'],
            'docs' => [], 'links' => [
                'architecture', 'quickstart', 'enterprise', 'features',
            ],
        ],
    ],

    // 手册引用的站内页面：id => 文件（文案键 manual.page_links.<id>）
    'manual_page_links' => [
        'quickstart'   => 'quickstart.php',
        'features'     => 'features.php',
        'enterprise'   => 'enterprise.php',
        'architecture' => 'architecture.php',
    ],

    // 注意：本地能力文档清单不在这里维护，而是由构建脚本生成到
    // docs/manifest.php（tools/build-docs.php 产出），避免与上游重复维护。
];
