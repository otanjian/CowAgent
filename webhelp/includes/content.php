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
    // 形态是**截图与编号步骤为主**：每个主题一句话定位 + 若干步骤（目标、操作要点、真实界面截图）。
    // 步骤文案只写界面上稳定的名称（页面名、控件名），不写像素位置——那类描述改版即失效。
    // 主题集合只收成员（member 角色）看得到、做得了的操作；管理侧治理主题（成员与组织、
    // 角色权限、租户与审计、租户级模型服务配置）不收，成员照做时会撞到「无权访问」。
    // 主题顺序即页面与侧栏顺序：先对话与协作（在对话界面内完成），再我的资源（属于我、我来维护）。
    // 结构只声明 id / 分组 / 图标 / 步骤（id + 截图路径）/ 引用的文档 slug 与站内页面；
    // 文案全部在 lang/*.php 的 manual.* 下按同一 id 组织（语言包中按 id 索引，与顺序无关）。
    //
    // 截图放在 assets/img/manual/，用相对路径声明（不是网址，因此仍满足「正文不出现网址」）。    // tools/check-manual.php 会断言每个步骤声明的截图真实存在，缺图即校验失败。
    'manual_parts' => [
        ['id' => 'conversation'],
        ['id' => 'resources'],
    ],

    'manual_topics' => [
        // ---- 对话与协作：都在对话界面内完成 ----
        [
            'id' => 'chat', 'part' => 'conversation', 'icon' => 'chat',
            'steps' => [
                ['id' => 'login', 'shot' => 'assets/img/manual/chat-1-login.jpg'],
                ['id' => 'compose', 'shot' => 'assets/img/manual/chat-2-compose.png'],
                ['id' => 'agent', 'shot' => 'assets/img/manual/chat-3-agent.png'],
                ['id' => 'model', 'shot' => 'assets/img/manual/chat-4-model.png'],
                ['id' => 'run', 'shot' => 'assets/img/manual/chat-5-answer.png'],
            ],
            'docs' => [], 'links' => [],
        ],
        [
            'id' => 'team', 'part' => 'conversation', 'icon' => 'multiagent',
            'steps' => [
                ['id' => 'entry', 'shot' => 'assets/img/manual/team-1-menu.png'],
                ['id' => 'pick', 'shot' => 'assets/img/manual/team-2-picker.png'],
                ['id' => 'route', 'shot' => 'assets/img/manual/team-3-collab.png'],
            ],
            'docs' => ['multiagent'], 'links' => [],
        ],

        // ---- 我的资源：属于我、我来维护 ----
        [
            'id' => 'knowledge', 'part' => 'resources', 'icon' => 'knowledge',
            'steps' => [
                ['id' => 'open', 'shot' => 'assets/img/manual/knowledge-1-overview.png'],
                ['id' => 'category', 'shot' => 'assets/img/manual/knowledge-2-category.png'],
                ['id' => 'document', 'shot' => 'assets/img/manual/knowledge-3-doc.png'],
                ['id' => 'result', 'shot' => 'assets/img/manual/knowledge-4-result.png'],
            ],
            'docs' => ['knowledge'], 'links' => [],
        ],
        [
            'id' => 'agent', 'part' => 'resources', 'icon' => 'planning',
            'steps' => [
                ['id' => 'open', 'shot' => 'assets/img/manual/agent-1-overview.png'],
                ['id' => 'create', 'shot' => 'assets/img/manual/agent-2-create.png'],
                ['id' => 'files', 'shot' => 'assets/img/manual/agent-3-files.png'],
            ],
            'docs' => ['multiagent'], 'links' => [],
        ],
        [
            'id' => 'channel', 'part' => 'resources', 'icon' => 'channels',
            'steps' => [
                ['id' => 'open', 'shot' => 'assets/img/manual/channel-1-overview.png'],
                ['id' => 'add', 'shot' => 'assets/img/manual/channel-2-add.png'],
            ],
            'docs' => ['channels'], 'links' => [],
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
