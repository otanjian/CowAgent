<?php
declare(strict_types=1);

/**
 * English language pack. Structure must match zh.php.
 * Capability copy mirrors cowagent.ai/zh/; governance copy is derived from openspec/specs/.
 */

return [
    'meta' => [
        'description'        => 'RongAI is an enterprise-grade AI foundation built on the open-source CowAgent project, so enterprises truly own their own AI platform. The assistant plans tasks, calls tools and skills, and keeps growing through memory and knowledge; on top of that it adds multi-tenancy, fine-grained RBAC, audit, approval, credential and quota controls, ready for teams and production.',
        'keywords'           => 'enterprise AI platform,AI foundation,LLM,Agent Harness,multi-tenant,RBAC,audit log,approval,resource quota,execution isolation,open source',
        'title_home'         => 'Enterprise AI Platform · Multi-tenancy & RBAC',
        'title_features'     => 'Core Capabilities',
        'title_enterprise'   => 'Enterprise Governance',
        'title_quickstart'   => 'Quick Start',
        'title_architecture' => 'Architecture',
        'title_about'        => 'About',
    ],

    'nav' => [
        'home'         => 'Home',
        'features'     => 'Capabilities',
        'enterprise'   => 'Enterprise',
        'architecture' => 'Architecture',
        'about'        => 'About',
    ],

    'common' => [
        'skip_to_content'  => 'Skip to main content',
        'copy'             => 'Copy',
        'copied'           => 'Copied!',
        'menu'             => 'Menu',
        'theme_toggle'     => 'Toggle theme',
        'lang_switch'      => '中文',
        'lang_switch_aria' => 'Switch to Chinese',
        'open_source'      => 'Open sourced under the MIT License',
        'read_doc'         => 'Read the docs',
    ],

    'hero' => [
        'badge'          => 'Built on CowAgent · Enterprise governance added',
        'title_line1'    => 'The Enterprise AI Foundation,',
        'title_line2'    => 'Truly Own Your AI Platform',
        'desc'           => 'RongAI Agent is an enterprise-grade AI platform built by Rongcheng. It unifies models, knowledge, permissions and applications, moving enterprises from "using AI" to "owning AI". On-premise deployment keeps data and permissions in-house. Autonomous planning, long-term memory, tools and skills, a knowledge base and multimodal messaging, plus cloud or local models, take AI from "can chat" to "can get things done".',
    ],

    'demo' => [
        'aria'     => 'RongAI product demo video',
        'fallback' => 'Your browser does not support embedded video, ',
        'link'     => 'click here to watch in a new tab',
    ],

    'quickstart' => [
        'subtitle'       => 'Install, configure and start with a single command — built for developers and 24/7 servers',
        'tab_unix'       => 'Linux / macOS',
        'tab_win'        => 'Windows',
        'tab_docker'     => 'Docker',
        'comment_unix'   => '# Install and run CowAgent',
        'comment_win'    => '# Install and run CowAgent (PowerShell)',
        'comment_docker' => '# Run CowAgent with Docker',
        'access_title'   => 'Open the Web console',
        'access_desc'    => 'Once the service is up, open http://localhost:9899 in your browser to chat, configure models and connect channels.',
        'port_note'      => 'By default the service listens on this machine only. Before exposing it, finish the identity bootstrap and set a strong administrator password, then bind to 0.0.0.0 and open the firewall port. The console signs in with database accounts — there is no shared access password.',
        'steps_title'    => 'Up and running in five steps',
        'steps' => [
            'install'   => ['title' => 'Install', 'desc' => 'Use the official install script, or install dependencies from source in a Python 3 environment.'],
            'configure' => ['title' => 'Configure models', 'desc' => 'Put your vendor API keys in config.json or the Web console; environment variables can override them.'],
            'start'     => ['title' => 'Start the service', 'desc' => 'Run cow start to launch as a daemon; use --foreground during development to stream logs.'],
            'access'    => ['title' => 'Open the console', 'desc' => 'Open http://localhost:9899 to sign in — every setting can be configured from the UI.'],
            'channel'   => ['title' => 'Connect a channel', 'desc' => 'Follow the wizard to connect WeChat, Feishu, DingTalk or WeCom so the assistant lives in your IM.'],
        ],
        'enterprise_title' => 'Turn on enterprise governance',
        'enterprise_desc'  => 'Switch the identity mode to database and bootstrap a default tenant with the first platform admin. The first admin signs in with a time-limited temporary password and must complete a forced password change before enterprise business is enabled.',
    ],

    'capabilities' => [
        'title'    => 'Core Capabilities',
        'subtitle' => 'Not just chat — an AI assistant that actually gets things done',
        'items' => [
            'planning'   => ['title' => 'Task Planning', 'desc' => 'Understands complex tasks, breaks them down and executes step by step, looping through tools and skills until the goal is met.'],
            'memory'     => ['title' => 'Long-term Memory', 'desc' => 'A three-tier memory architecture (context → daily → core) distilled automatically, with hybrid keyword and vector retrieval.'],
            'knowledge'  => ['title' => 'Local Knowledge Base', 'desc' => 'Turns structured knowledge into a Markdown wiki and builds a visual knowledge graph through cross-references that keeps growing.'],
            'skills'     => ['title' => 'Skills System', 'desc' => 'Install skills from Skill Hub, GitHub and ClawHub in one click, or generate custom skills through natural-language conversation.'],
            'tools'      => ['title' => 'Tools System', 'desc' => '10+ built-in tools including file I/O, terminal, browser, scheduling, memory retrieval and web search, with native MCP support.'],
            'evolution'  => ['title' => 'Self-evolution', 'desc' => 'When idle it reviews the conversation, optimises skills, handles leftover tasks and consolidates memory so the agent keeps improving.'],
            'models'     => ['title' => 'Multi-model Support', 'desc' => 'Supports Claude, GPT, Gemini, DeepSeek, Qwen, GLM, Kimi, MiniMax, Doubao and more — switch with one config line.'],
            'channels'   => ['title' => 'Multi-channel Access', 'desc' => 'One agent serving Web, WeChat, Feishu, DingTalk, WeCom, QQ, official accounts and more at the same time.'],
            'multiagent' => ['title' => 'Multi-agent Teams', 'desc' => 'Create specialised agents that work as a team with group chat, task delegation and sub-agents to build your own digital workforce.'],
        ],
    ],

    'enterprise' => [
        'title'    => 'Enterprise Governance',
        'subtitle' => 'Permissions, isolation and audit on top of the open-source assistant, so it can safely enter teams and production',
        'banner_title' => 'The enterprise rework in this repository',
        'banner_desc'  => 'Upstream CowAgent provides the assistant capabilities. This build adds nine governance modules on top: multi-tenancy, fine-grained permissions, organisation & members, audit, approval, credentials, quota and execution isolation. Every control is enforced server-side — hiding UI is never treated as authorisation.',

        'items' => [
            'tenant' => [
                'title'  => 'Multi-tenant Isolation',
                'desc'   => 'Platform admins can create, edit, suspend and restore tenants. Each tenant owns its own identities, roles and resource directory, with no visibility into other tenants.',
                'points' => [
                    'Server-generated stable tenant ID and a globally unique, immutable code',
                    'Tenant creation atomically builds built-in roles and a virtual org root',
                    'Platform list visibility grants no tenant membership or business access',
                    'Suspend and restore re-validate a valid admin before taking effect',
                ],
            ],
            'rbac' => [
                'title'  => 'Roles & Permissions',
                'desc'   => 'A finite permission catalogue defined in code. Built-in roles cannot be edited or deleted; custom roles may only pick assignable business permissions and same-tenant resources.',
                'points' => [
                    'Permissions are re-resolved from the database per request; client summaries never authorise',
                    'Unknown permissions, wildcards, platform markers and identity-qualification escalation are rejected',
                    'Role changes commit together with redacted audit; version conflicts return 409',
                    'An instance always keeps at least one valid admin; the last admin cannot be disabled',
                ],
            ],
            'org' => [
                'title'  => 'Organisation & Members',
                'desc'   => 'Global accounts and tenant memberships are strictly separated; one account can hold an independent profile, status, roles and a single department in each tenant.',
                'points' => [
                    'Two atomic operations: create a new account or bind an existing one',
                    'Member fields are whitelisted — global passwords or platform markers are rejected',
                    'Department trees are per-tenant unique and acyclic; in-use departments must be unreferenced first',
                    'Disabling a member never affects other tenants, and history is preserved',
                ],
            ],
            'audit' => [
                'title'  => 'Tamper-resistant Audit Log',
                'desc'   => 'Authorization changes, credential use, approvals, quota adjustments, code execution and cross-tenant access are all recorded; append-only records cannot be silently rewritten by business code.',
                'points' => [
                    'Records time, subject, tenant, resource, action and result',
                    'Sensitive fields are masked; no plaintext credentials, password hashes or tokens',
                    'Platform admins see everything; tenant admins only see their own tenant',
                    'For strongly-consistent actions, audit failure rolls the whole change back',
                ],
            ],
            'approval' => [
                'title'  => 'Approval for Risky Actions',
                'desc'   => 'Actions with external side effects — writes, approval flows, messaging, financial or business changes — pause for a pending approval request before execution.',
                'points' => [
                    'Unapproved actions never execute and produce no side effects',
                    'Initiators cannot self-approve; approval qualification is judged independently of resource grants',
                    'Full lifecycle states: pending / approved / rejected / expired / revoked',
                    'The whole chain is traceable to initiator and approver with timestamps',
                ],
            ],
            'credential' => [
                'title'  => 'Central Credential Management',
                'desc'   => 'External credentials for SAP, finance systems and databases are stored encrypted, granted per tenant and resource, and injected only at authorised execution points.',
                'points' => [
                    'Reads return only masked identifiers (e.g. sap_prod••••) and existence status',
                    'Logs, config projections and audit bodies never contain plaintext credentials',
                    'No plaintext kept in session or request cache; revocation invalidates immediately',
                    'Rotation keeps versions and records audit; old versions stop working at once',
                ],
            ],
            'quota' => [
                'title'  => 'Resource Quota & Hard Limits',
                'desc'   => 'Hard limits on token usage, tool calls, concurrent runs and storage; exceeding a limit is rejected rather than queued or silently allowed.',
                'points' => [
                    'Metering is isolated per tenant and identity; tenants cannot borrow each other’s quota',
                    'After a quota reduction, queued tasks are re-validated and rejected before running',
                    'Platform admin management actions and business consumption are metered separately',
                    'Queries return only the current tenant’s usage and limits',
                ],
            ],
            'isolation' => [
                'title'  => 'Code Execution Isolation',
                'desc'   => 'In multi-tenant mode, skill scripts, bash and spawned subprocesses run inside an isolation boundary and cannot read another tenant’s workspace, identity data or credentials.',
                'points' => [
                    'The boundary is limited to tenant directories and whitelisted roots, enforced server-side',
                    'Path traversal, symlink escape and arbitrary directory references are rejected',
                    'While the isolation slice is unverified, arbitrary code execution is denied by default',
                    'Even full-access mode cannot break the tenant isolation boundary',
                ],
            ],
            'access' => [
                'title'  => 'Access Control & Session Security',
                'desc'   => 'A single server-side authorization order and explicit credential sources; client claims, hidden menus and cached permissions never replace real authorization.',
                'points' => [
                    'Clear 401 / 403 / 404 / 503 semantics without leaking cross-tenant existence',
                    'Forced-password-change sessions expose only password change and sign-out — admins included',
                    'Cookie-authenticated admin writes enforce origin and CSRF checks',
                    'Login failure thresholds and rate limiting are bounded, returning 429 with retry hints',
                ],
            ],
        ],

        'flow_title'    => 'Every request walks the full governance chain',
        'flow_subtitle' => 'From identity resolution to audit, all control points live server-side and are re-validated on every request.',
        'flow' => [
            'identify'  => ['title' => 'Resolve identity', 'desc' => 'One authoritative entry point yields the session, global account state and identity domain (platform / tenant / personal).'],
            'authorize' => ['title' => 'Authorize live', 'desc' => 'Roles and permissions are re-resolved per request, along with resource ownership and tenant boundaries.'],
            'guard'     => ['title' => 'Guard execution', 'desc' => 'Risky actions enter approval; quota, credential grants and isolation boundaries are re-checked at the execution point.'],
            'trace'     => ['title' => 'Record audit', 'desc' => 'Sensitive actions and denials are written to tamper-resistant audit; strong-consistency failures roll back.'],
        ],

        'console_title'    => 'Governance Console',
        'console_subtitle' => 'Manage identities, tenants, roles and organisation from the Web console.',
        'console' => [
            'user'   => ['title' => 'Users', 'desc' => 'Full lifecycle for tenant members: search, create, bind, assign roles and change status.'],
            'tenant' => ['title' => 'Tenants', 'desc' => 'Tenant list and a full-page editor with five tabs: basics, model grants, tool grants, agents and tenant management.'],
            'role'   => ['title' => 'Roles', 'desc' => 'Browse the grouped permission catalogue, configure five resource grant types and inspect member associations.'],
            'org'    => ['title' => 'Organisation', 'desc' => 'Maintain the department tree and position text, with an entry point for identity audit queries.'],
        ],

        'guarantee_title' => 'Governance guarantees',
        'guarantee_desc'  => 'These guarantees are enforced server-side and never rely on the UI or client claims.',
        'guarantee' => [
            'Server-enforced: all authorization is verified independently at the API layer; hidden menus never grant access.',
            'Immediate effect: session revocation and account/member/tenant suspension apply by the next request at the latest.',
            'Least privilege: new permissions are not granted to normal roles automatically; built-in defaults are explicit.',
            'Fail closed: an unavailable identity store returns 503 and denies access, never falling back to default identities.',
            'Invisible means 404: cross-tenant and non-existent objects return identical results without leaking tenant details.',
            'Same-DB transactions: identity changes and redacted audit commit together in identity.db.',
        ],
    ],

    // Core feature 1: enterprise permission governance (permission model block on enterprise.php)
    // Core feature 1: enterprise permission governance (permission block on enterprise.php)
    'perm' => [
        'title'   => "Layered and fine-grained \nenterprise permission system",
        // Keyword highlighted inside the title (wrapped in .hero-accent)
        'title_accent' => 'Layered and fine-grained',
        'lead'    => 'Built on the principle that whoever owns a resource decides who gets it, a three-tier architecture — platform, tenant and user — keeps permissions inside their layer, resources private by default and every sensitive action on the record.',

        // Overview highlights: numbers and qualitative claims mixed
        'stats' => [
            ['value' => '3',         'label' => 'permission tiers'],
            ['value' => '100%',      'label' => 'resource isolation'],
            ['value' => 'End-to-end', 'label' => 'audit trail'],
            ['value' => 'Zero',      'label' => 'default sharing'],
        ],

        'principles_label' => 'Core design principles',
        'principles_desc'  => 'Clear ownership boundaries from the ground up, so permissions never sprawl',
        'principles' => [
            'ownership' => [
                'title' => 'Whoever owns a resource decides who gets it',
                'desc'  => 'Ownership and granting rights are unified: platform, tenant and personal resource owners each decide their own scope, preventing out-of-band approval and privilege escalation, so data safety is protected by design.',
            ],
            'layering' => [
                'title' => 'Permissions never skip or escalate a layer',
                'desc'  => 'Layer boundaries are strictly respected: an upper tier cannot operate on personal resources directly, and a lower tier cannot reach platform configuration. Each layer does its own job, keeping the model clear and controllable.',
            ],
            'private' => [
                'title' => 'Resources are not shared by default',
                'desc'  => 'Personal and tenant resources stay private by default and become visible only after an explicit share and grant, ruling out data leakage and unauthorised access at the source.',
            ],
            'traceable' => [
                'title' => 'Sensitive operations are always on the record',
                'desc'  => 'Grant changes, resource configuration and cross-layer access are fully audited, with real-time alerts on escalation attempts and traceable security events that satisfy compliance requirements.',
            ],
        ],

        'tiers_no'    => '3.1.1',
        'tiers_title' => 'Three-tier governance architecture',
        'tiers_desc'  => 'A progressive platform → tenant → user architecture: isolated at every layer, granted step by step, balancing management efficiency and data security',
        'labels' => [
            'scope'   => 'Resource scope',
            'actions' => 'Key grant actions',
        ],
        'tiers' => [
            'platform' => [
                'name'       => 'Platform',
                'actor'      => 'Platform admin',
                'actor_desc' => 'Steward of platform-wide resources',
                'scope'      => 'Platform resources (whole platform)',
                'actions'    => [
                    'Create and maintain platform agents, tools and skills',
                    'Grant platform resources to chosen tenants',
                    'Designate tenant admins',
                ],
            ],
            'tenant' => [
                'name'       => 'Tenant',
                'actor'      => 'Tenant admin',
                'actor_desc' => 'Steward of the tenant workspace',
                'scope'      => 'All resources of this tenant',
                'actions'    => [
                    'Create and maintain tenant agents, tools and skills',
                    'Grant tenant resources to internal users',
                    'Manage tenant users, roles and permissions',
                ],
            ],
            'user' => [
                'name'       => 'User',
                'actor'      => 'User',
                'actor_desc' => 'Owner of personal resources',
                'scope'      => 'Personal resources + granted tenant resources',
                'actions'    => [
                    'Create and maintain personal agents, tools and skills',
                    'Use granted tenant resources',
                    'Share personal resources with the tenant (planned)',
                ],
            ],
        ],

        'flow_no'    => '3.1.2',
        'flow_title' => 'How grants flow',
        'flow_desc'  => 'Granted downwards step by step, shared upwards on demand — a closed, controllable loop',
        'flow_graph' => [
            'actor_desc' => [
                'platform' => 'Creates platform resources',
                'tenant'   => 'Sees and distributes resources',
                'user'     => 'Creates personal resources',
            ],
            'grant_platform' => 'Grants platform resources',
            'grant_tenant'   => 'Grants tenant resources',
            'share_out'      => 'User shares personal resources to the tenant',
            'share_in'       => 'Tenant admin re-grants them to users',
        ],

        'rules_label' => 'Key rules',
        'rules' => [
            ['title' => 'Private by default',   'desc' => 'Personal resources stay outside the tenant scope until explicitly shared'],
            ['title' => 'Grants decide access', 'desc' => 'Content you are not granted stays inaccessible even if you know its name'],
            ['title' => 'Independent settings', 'desc' => 'Platform and tenant integration settings never affect each other'],
            ['title' => 'Everything on record', 'desc' => 'Escalation and cross-tenant attempts are logged and reported to admins'],
        ],

        'roles_no'    => '3.1.3',
        'roles_title' => 'Platform roles and responsibilities',
        'roles_desc'  => 'Three roles, clearly separated, covering the whole platform lifecycle',
        'roles' => [
            'platform' => [
                'name' => 'Platform admin',
                'tag'  => 'Platform',
                'desc' => 'Owns platform-wide infrastructure and resource governance',
                'groups' => [
                    ['title' => 'Branding & models', 'items' => [
                        'Maintain the platform name, sign-in and welcome pages',
                        'Manage available models and per-tenant model scope',
                        'Branding changes are audited, previewable and reversible',
                    ]],
                    ['title' => 'Platform resources', 'items' => [
                        'Create and maintain platform agents, skills and tools',
                        'Run the shared skill library and tool library',
                        'Maintain shared account integration settings',
                    ]],
                    ['title' => 'Tenants & allocation', 'items' => [
                        'Create tenants, designate admins, enable or suspend',
                        'Set per-category resource limits per tenant',
                        'Distribute platform agents by copy, fully audited',
                    ]],
                ],
            ],
            'tenant' => [
                'name' => 'Tenant admin',
                'tag'  => 'Tenant',
                'desc' => 'Owns resources, users and permissions inside the tenant',
                'groups' => [
                    ['title' => 'Tenant resources', 'items' => [
                        'Create tenant agents; the first becomes the default',
                        'Configure agent knowledge, skills, flows and tools',
                        'Manage the tenant skill library, enable or disable',
                    ]],
                    ['title' => 'Users & roles', 'items' => [
                        'Manage tenant user accounts and identity checks',
                        'Grant menus, resources and models per role',
                        'Maintain the org tree; permissions union across roles',
                    ]],
                    ['title' => 'Public agent access', 'items' => [
                        'Create Feishu / WeCom bots quickly by QR scan',
                        'Credentials stored encrypted; changes take effect at once',
                        'External messages are handled only inside this tenant',
                    ]],
                ],
            ],
            'user' => [
                'name' => 'User',
                'tag'  => 'User',
                'desc' => 'Owner and consumer of personal resources',
                'groups' => [
                    ['title' => 'Personal space', 'items' => [
                        'Personal style, memory and preferences stay separate',
                        'Memory carries across agents and stays invisible to others',
                        'The workbench keeps your interface preferences',
                    ]],
                    ['title' => 'Personal resources', 'items' => [
                        'Agents created now belong to and are managed by the tenant',
                        'Available resources are decided by the tenant admin',
                        'Independent personal resources are planned for later',
                    ]],
                    ['title' => 'Sharing', 'items' => [
                        'Personal resources are private and not shared by default',
                        'Sharing to the tenant needs a second grant by the admin',
                        'The full sharing flow is planned for a later version',
                    ]],
                ],
            ],
        ],
    ],

    'integrations' => [
        'title'         => 'Models & Channels',
        'subtitle'      => 'Flexibly connect mainstream LLMs and deploy to many application channels',
        'label_models'  => 'Model vendors',
        'label_channels'=> 'Channels',
        'channels' => [
            'wechat'     => 'WeChat',
            'feishu'     => 'Feishu',
            'dingtalk'   => 'DingTalk',
            'wecom_bot'  => 'WeCom Bot',
            'qq'         => 'QQ',
            'wecom_app'  => 'WeCom App',
            'official'   => 'Official Account',
            'telegram'   => 'Telegram',
            'slack'      => 'Slack',
            'discord'    => 'Discord',
            'web'        => 'Web',
            'terminal'   => 'Terminal',
        ],
    ],

    'cli' => [
        'title'       => 'Command System',
        'subtitle'    => 'A terminal CLI plus in-chat slash commands for operations and daily use',
        'cli_title'   => 'Terminal CLI',
        'slash_title' => 'In-chat slash commands',
        'commands' => [
            'start'   => 'Start the service as a daemon',
            'stop'    => 'Stop the service',
            'restart' => 'Restart the service',
            'status'  => 'Show running status',
            'logs'    => 'Tail the service logs',
            'update'  => 'Update and restart',
            'skill'   => 'Install a skill',
            'browser' => 'Install the browser tool',
        ],
        'slash' => [
            'skill_list'    => 'Browse the skill marketplace',
            'skill_install' => 'Search and install a skill',
            'knowledge'     => 'Show knowledge base stats and toggle',
            'model'         => 'Switch the current model vendor',
            'memory'        => 'Inspect and search long-term memory',
        ],
    ],

    'tools' => [
        'title'    => 'Built-in Tools',
        'subtitle' => 'Tools give the agent real hands-on ability, and they are extensible',
        'items' => [
            'file'      => ['title' => 'File I/O', 'desc' => 'Read, write and precisely edit local files — the basis for coding and document work.'],
            'bash'      => ['title' => 'Terminal', 'desc' => 'Run commands in a controlled environment to chain system operations and automation.'],
            'browser'   => ['title' => 'Browser Control', 'desc' => 'Drive Chromium to visit pages, fill forms, click and screenshot, including dynamic sites.'],
            'scheduler' => ['title' => 'Scheduled Tasks', 'desc' => 'One-off, fixed-interval and cron tasks that send messages or run agent jobs.'],
            'websearch' => ['title' => 'Web Search', 'desc' => 'Query search engines for real-time information to ground answers in fresh facts.'],
            'memory'    => ['title' => 'Memory Retrieval', 'desc' => 'Search long-term memory and the knowledge base on demand so answers are grounded.'],
            'sendfile'  => ['title' => 'Send Files', 'desc' => 'Deliver generated files back through the current channel for multimodal output.'],
            'env'       => ['title' => 'Env & Secrets', 'desc' => 'Centrally manage skill secrets with built-in protection and masked display.'],
            'mcp'       => ['title' => 'MCP Integration', 'desc' => 'Native Model Context Protocol support to plug into the wider tool ecosystem.'],
        ],
    ],

    'architecture' => [
        'subtitle' => 'Cleanly layered and channel-agnostic: one agent core, with a unified identity and authorization control plane for enterprises',
        'layers_title' => 'Layered structure',
        'layers' => [
            'access' => [
                'title' => 'Access layer', 'desc' => 'Web console, desktop client and IM channels, all normalised into an internal message model.',
                'items' => ['Web console', 'WeChat / WeCom', 'Feishu / DingTalk', 'Desktop client'],
            ],
            'identity' => [
                'title' => 'Identity & authorization layer', 'desc' => 'The enterprise control plane: per-request resolution of identity domain, permissions, tenant boundary and resource ownership.',
                'items' => ['Auth & session', 'Multi-tenant & members', 'RBAC catalogue', 'Audit & approval'],
            ],
            'agent' => [
                'title' => 'Agent core', 'desc' => 'Task planning, tool calling and multi-turn reasoning, looping until the goal is achieved.',
                'items' => ['Planning & reasoning', 'Tool & skill dispatch', 'Sub-agents & delegation', 'Context & step management'],
            ],
            'capability' => [
                'title' => 'Capability layer', 'desc' => 'Pluggable modules for memory, knowledge, skills, models and voice.',
                'items' => ['Long-term memory', 'Knowledge & graph', 'Skill engine', 'Multi-model adapters', 'Speech in/out'],
            ],
            'storage' => [
                'title' => 'Storage layer', 'desc' => 'Tenant-isolated workspaces, the identity database and plugin directories — all local by default.',
                'items' => ['identity.db', 'Tenant workspaces', 'plugins directory', 'Tasks & logs'],
            ],
        ],
        'flow_title'    => 'Life of a request',
        'flow_subtitle' => 'From a channel message to the final reply, passing through identity, authorization, execution and audit.',
        'flow' => [
            'receive'   => ['title' => 'Receive', 'desc' => 'The channel normalises the user message and hands it to the agent core.'],
            'identify'  => ['title' => 'Identify', 'desc' => 'The authoritative entry point resolves session, account state and identity domain.'],
            'authorize' => ['title' => 'Authorize', 'desc' => 'Feature permissions, resource ownership and tenant boundaries are checked.'],
            'plan'      => ['title' => 'Plan', 'desc' => 'Using context, memory and knowledge, the agent decides what to do next.'],
            'act'       => ['title' => 'Act', 'desc' => 'Tools such as files, terminal, browser and skills run inside the isolation boundary.'],
            'audit'     => ['title' => 'Audit', 'desc' => 'Sensitive actions and denials are written to redacted, traceable audit records.'],
            'reply'     => ['title' => 'Reply', 'desc' => 'Results are summarised and returned as text, images or files on the original channel.'],
        ],
        'principles_title' => 'Design principles',
        'principles_subtitle' => 'These constraints span the core and the control plane, and apply to new capabilities too.',
        'principles' => [
            'Channel-agnostic: adding a channel never disturbs the core logic or data model.',
            'Pluggable capabilities: models, skills, tools and channels can be swapped or extended independently.',
            'Local-first: memory, knowledge and identity data stay on your own devices by default.',
            'One control plane: authorization rules stay consistent across real and test entry points.',
        ],
    ],

    'about' => [
        'lead'     => 'RongAI is an open-source super AI assistant and Agent Harness. The name means “great capacity through inclusiveness”: it embraces many models, many channels and many capabilities. Built on the open-source CowAgent project, it adds an enterprise layer so the same assistant works for an individual and safely inside teams and production.',
        'mission_title' => 'What we believe',
        'mission' => [
            'open'  => 'Open source: MIT licensed — auditable, forkable and self-hostable.',
            'grow'  => 'Grows with use: memory, knowledge and skills accumulate every day.',
            'guard' => 'Controllable: permissions, isolation and audit make it trustworthy at work.',
            'local' => 'Your data: local storage by default, with control handed back to you.',
        ],
        'upstream_title' => 'Relationship to upstream',
        'upstream_desc'  => 'The assistant capabilities of RongAI (task planning, memory, knowledge base, skills, tools, multi-model, multi-channel, multi-agent) come from the open-source CowAgent project. This repository layers an enterprise identity and authorization system onto that runtime mainline; it does not copy upstream runtime configuration, credentials or customer data, nor overwrite upstream implementation files wholesale.',
        'upstream_card'  => 'Assistant capabilities come from the upstream open-source community; the enterprise identity and authorization layer is added by this repository on the runtime mainline, so governance can keep evolving independently as upstream upgrades. This site links to no external website; obtain upstream material separately if needed.',
        'disclaimer_title' => 'Disclaimer',
        'disclaimer' => [
            'This project is open sourced under the MIT License for technical research and learning only. Please comply with the laws of your jurisdiction; maintainers are not liable for consequences of using this project.',
            'Agent mode consumes significantly more tokens — choose a model that balances quality and cost. Agents can operate the local system, so only deploy in trusted environments.',
            'This is a pure open-source project. It does not participate in, authorise or issue any cryptocurrency.',
        ],
    ],

    'faq' => [
        'title'    => 'FAQ',
        'subtitle' => 'Common questions about deployment, cost, data and permission controls',
        'items' => [
            'q1' => ['q' => 'What runtime do I need?', 'a' => 'Linux, macOS and Windows are supported with Python 3.10+. You can also deploy quickly via Docker or the official install script; the Web console listens on port 9899 locally by default.'],
            'q2' => ['q' => 'Does it require internet access? Can I use a local model?', 'a' => 'The core service, memory and knowledge base run locally. Model calls need your chosen vendor’s API key and network access. You control cost and data flow by choosing the vendor.'],
            'q3' => ['q' => 'Is my data uploaded anywhere?', 'a' => 'Memory, knowledge and identity data stay on your own device or server by default (tenant-isolated workspace and identity.db). Only the conversation content required for inference is sent to the model provider you select.'],
            'q4' => ['q' => 'Do enterprise controls need extra infrastructure?', 'a' => 'No extra components. Once the identity mode is set to database, multi-tenancy, roles, members, audit, approval, credentials and quota all work in the same process, with identity and audit data stored in the local identity.db.'],
            'q5' => ['q' => 'How are permissions enforced? Does hiding a menu count?', 'a' => 'It does not. All authorization is verified independently at the API layer, and permissions and identity are re-resolved from the database on every request. Front-end menus and buttons only improve interaction; hiding them never changes an API rejection.'],
            'q6' => ['q' => 'How do I extend it with my own capabilities?', 'a' => 'Two ways: install ready-made skills from the marketplace, or use the built-in skill-creator to turn a workflow into a skill through natural-language conversation. MCP integration is available too. In enterprise mode these capabilities are still subject to permissions, quota and execution isolation.'],
        ],
    ],

    'cta' => [
        'title'     => 'Put your assistant to work',
        'subtitle'  => 'Run one command to get an AI assistant that plans, remembers, grows — and stays controllable and auditable.',
        'primary'   => 'Read quick start',
        'manual'    => 'Read the user manual',
        'enterprise'=> 'Explore enterprise governance',
        'back_features' => 'Back to capabilities',
    ],

    'doc' => [
        'breadcrumb'      => 'Documentation',
        'notice'          => 'This page is a localized copy of the official CowAgent documentation: all off-site links and external assets have been removed or mirrored locally, so it reads fully offline.',
        'toc'             => 'On this page',
        'all_docs'        => 'All documents',
        'prev'            => 'Previous',
        'next'            => 'Next',
        'source'          => 'Adapted from the official CowAgent documentation (localized archive).',
        'not_found'       => 'Document not found',
        'not_found_desc'  => 'Please open it from the capabilities page.',
        // Sidebar group titles (keys match the first path segment upstream)
        'sections' => [
            'intro'       => 'Architecture',
            'memory'      => 'Memory',
            'knowledge'   => 'Knowledge',
            'skills'      => 'Skills',
            'tools'       => 'Tools',
            'cli'         => 'CLI',
            'models'      => 'Models',
            'channels'    => 'Channels',
            'multi-agent' => 'Multi-agent',
        ],
    ],

    'manual' => [
        'title'        => 'Product Manual',
        'title_accent' => 'Manual',
        'lead'         => 'This manual is built around demonstrations: every topic lists the videos to be recorded and what each video covers. Video slots are already in place — drop in a file and it plays. Jump to the matching capability docs whenever you need principles or edge cases.',
        'toc'            => 'Contents',
        'covers_label'   => 'This video covers',
        'docs_label'     => 'Related docs',
        'pages_label'    => 'Related pages',
        'cli_title'      => 'Terminal commands',
        'slash_title'    => 'In-chat commands',
        'video_pending'  => 'Video coming soon',
        'video_fallback' => 'Your browser cannot play embedded video. Open the video file directly to watch it.',

        // 手册分组（顺序即页面与侧栏的分组顺序）
        'parts' => [
            'workbench' => 'Workbench (daily use)',
            'console'   => 'Admin Console (configuration and governance)',
            'personal'  => 'Personal and reference',
        ],

        // 主题引用的站内页面标题
        'page_links' => [
            'quickstart'   => 'Install and deploy commands',
            'features'     => 'Core capabilities in depth',
            'enterprise'   => 'Enterprise governance overview',
            'architecture' => 'System architecture',
        ],

        // 主题：nav / title / lead 为文字定位，videos.<视频 id> 为一个视频条目
        'topics' => [
            // ===== 开始使用（工作台）=====
            'start' => [
                'nav'   => 'Getting started',
                'title' => 'Getting started: sign in and find your way around',
                'lead'  => 'Sign in with your account and learn what the workbench and the admin console each own.',
                'videos' => [
                    'login' => [
                        'title'  => 'Signing in and the first password change',
                        'covers' => [
                            'Signing in with the account and password your admin assigned',
                            'The temporary password on first sign-in and the forced password change',
                            'What the restricted session can and cannot do before you change it',
                            'How to get a new one-time temporary password when you forget yours',
                        ],
                    ],
                    'navigate' => [
                        'title'  => 'A tour of the two navigation areas',
                        'covers' => [
                            'The six workbench pages and what each one is for',
                            'The four groups of the admin console and the pages in each',
                            'How to switch between the two areas',
                            'What the account menu at the bottom of the sidebar is for',
                            'Menu visibility is decided by server-side authorization, not by hidden UI',
                        ],
                    ],
                ],
            ],

            // ===== 如何对话（工作台）=====
            'chat' => [
                'nav'   => 'Chatting',
                'title' => 'Chatting: pick an agent, send the task, attach files',
                'lead'  => 'Start a conversation, choose the right agent, and hand over the task with attachments and commands.',
                'videos' => [
                    'new' => [
                        'title'  => 'Starting a conversation and choosing an agent',
                        'covers' => [
                            'Starting a conversation from the sidebar or the history page',
                            'Switching the agent used by this conversation above the input box',
                            'Selecting several agents to run a multi-agent conversation',
                            'Stating the task clearly: goal, scope and expected output',
                        ],
                    ],
                    'compose' => [
                        'title'  => 'The input area, control by control',
                        'covers' => [
                            'Sending a message and stopping a running task',
                            'Switching the model used by this session',
                            'Smart input refinement and voice input',
                            'Clearing the context and steering the current task',
                            'Typing / for the command menu and @ to reference an agent or file',
                        ],
                    ],
                    'attach' => [
                        'title'  => 'Uploading files and folders',
                        'covers' => [
                            'Uploading a single file or an entire folder with the attach button',
                            'Drag-and-drop and paste upload',
                            'Confirming the attachment is submitted together with the message',
                            'Finding the output under Preview and Files in the workspace',
                        ],
                    ],
                    'session' => [
                        'title'  => 'Conversation history and everyday commands',
                        'covers' => [
                            'Searching the history page by title',
                            'Pinning, renaming and archiving',
                            'Restoring, continuing and deleting a conversation',
                            'Common slash commands: /status, /context, /clear, /compact',
                        ],
                    ],
                ],
            ],

            // ===== 选择智能体（工作台）=====
            'agents' => [
                'nav'   => 'Choosing an agent',
                'title' => 'Choosing an agent: pick one that can do the job',
                'lead'  => 'Browse the available agents on the workbench Agents page, check them, then start chatting.',
                'videos' => [
                    'browse' => [
                        'title'  => 'Browsing agents and starting a conversation',
                        'covers' => [
                            'What each agent card shows',
                            'How to tell whether an agent can be used for chat',
                            'What each reason shown when an agent is unavailable actually means',
                            'Refreshing the list and starting a conversation from a card',
                        ],
                    ],
                ],
            ],

            // ===== 设置知识库（工作台）=====
            'knowledge' => [
                'nav'   => 'Setting up knowledge',
                'title' => 'Setting up knowledge: give the agent something to look up',
                'lead'  => 'Knowledge is organized per agent: create a category, add documents, then let the agent use it.',
                'videos' => [
                    'overview' => [
                        'title'  => 'Knowledge structure and creating a category',
                        'covers' => [
                            'Knowledge is split per agent, and how shared differs from independent',
                            'Switching agents at the top, and reading page and capacity counts',
                            'Who maintains tenant-level knowledge versus private knowledge',
                            'Creating a category and using nested category paths',
                        ],
                    ],
                    'document' => [
                        'title'  => 'Creating and importing documents',
                        'covers' => [
                            'New document: target category, file name and content',
                            'Import document: md and txt are supported, TXT becomes Markdown',
                            'The index syncs automatically after saving or importing',
                            'Editing, moving and deleting documents',
                        ],
                    ],
                    'bind' => [
                        'title'  => 'Letting an agent use the knowledge base',
                        'covers' => [
                            'When to use shared mode versus independent mode',
                            'Switching the binding mode on the agent overview tab',
                            'Using /knowledge in chat to inspect, toggle and search',
                        ],
                    ],
                ],
            ],

            // ===== 待办与定时任务（工作台）=====
            'todo' => [
                'nav'   => 'Todos and scheduled tasks',
                'title' => 'Todos and scheduled tasks: let the assistant remember for you',
                'lead'  => 'Record things to follow up as todos, and hand anything recurring to scheduled tasks.',
                'videos' => [
                    'todo' => [
                        'title'  => 'My todos',
                        'covers' => [
                            'Creating a todo: title, description, category, priority and due time',
                            'Filtering by category and priority, plus overdue only',
                            'Editing, completing and deleting',
                            'Todos the assistant creates in chat land in the same list',
                        ],
                    ],
                    'task' => [
                        'title'  => 'Scheduled tasks',
                        'covers' => [
                            'The task list and the next run time',
                            'Running immediately and the enable toggle',
                            'Editing the schedule and the action',
                            'Why this page has no New button',
                        ],
                    ],
                ],
            ],

            // ===== 创建与配置智能体（管理控制台）=====
            'agents-admin' => [
                'nav'   => 'Creating and configuring agents',
                'title' => 'Creating and configuring agents: from a card to a working agent',
                'lead'  => 'Under agent management in the admin console: create an agent, then configure it tab by tab.',
                'videos' => [
                    'create' => [
                        'title'  => 'Creating an agent',
                        'covers' => [
                            'Agent management, then Create agent',
                            'How to fill in name, avatar, ID and responsibility',
                            'Using copy from an existing agent to skip repeated configuration',
                            'Choosing shared or independent knowledge',
                        ],
                    ],
                    'configure' => [
                        'title'  => 'Configuring the overview tab',
                        'covers' => [
                            'Position, category and tags',
                            'Greeting and persona summary',
                            'Related scenarios and the default model',
                            'Save, start a conversation and set as default',
                        ],
                    ],
                    'capability' => [
                        'title'  => 'Configuring capabilities and core files',
                        'covers' => [
                            'Skills: enable all, or only the checked ones',
                            'Tools: the allow list and the deny list',
                            'What SOP flows are for',
                            'Core files (agent profile, user info, workspace rules, long-term memory) and the Tasks tab',
                        ],
                    ],
                ],
            ],

            // ===== 记忆管理（管理控制台）=====
            'memory' => [
                'nav'   => 'Memory',
                'title' => 'Memory: inspect and tidy what the agent remembers',
                'lead'  => 'Memory is stored per agent: see what exists, open it, then tidy it or rebuild the index when needed.',
                'videos' => [
                    'view' => [
                        'title'  => 'Inspecting memory and triggering a tidy-up',
                        'covers' => [
                            'Switching the agent with the agent selector',
                            'The columns of the memory file list and what each memory type means',
                            'Opening a memory file to read its content',
                            'Two ways to trigger memory consolidation',
                        ],
                    ],
                    'index' => [
                        'title'  => 'Rebuilding the retrieval index',
                        'covers' => [
                            'When the index needs to be rebuilt',
                            '/memory rebuild-index and /memory status',
                            'How long it takes and what it affects',
                        ],
                    ],
                ],
            ],

            // ===== 模型服务（管理控制台）=====
            'models' => [
                'nav'   => 'Model services',
                'title' => 'Model services: connect models and assign them per capability',
                'lead'  => 'Configure the basics and vendor credentials first, then pick a model for each capability.',
                'videos' => [
                    'basic' => [
                        'title'  => 'Basic configuration',
                        'covers' => [
                            'Choosing a vendor and a model',
                            'Agent settings: context length, dialogue turns, execution steps and deep thinking',
                            'System language and task notifications',
                        ],
                    ],
                    'vendor' => [
                        'title'  => 'Vendor credentials',
                        'covers' => [
                            'The API Key and API Base to fill in when adding a vendor',
                            'When to add a custom vendor',
                            'Credentials are stored encrypted and never echoed back',
                            'Confirmation when replacing or clearing credentials',
                        ],
                    ],
                    'capability' => [
                        'title'  => 'Picking a model for each capability',
                        'covers' => [
                            'How the primary and fallback models divide the work',
                            'Image, speech, embedding and web search models',
                            'Changing the embedding model requires rebuilding the index',
                        ],
                    ],
                ],
            ],

            // ===== 消息渠道（管理控制台）=====
            'channels' => [
                'nav'   => 'Messaging channels',
                'title' => 'Messaging channels: reach the assistant where you already work',
                'lead'  => 'Create a channel instance at platform or tenant scope, fill in the credentials, then enable it.',
                'videos' => [
                    'scope' => [
                        'title'  => 'The two configuration scopes and creating an instance',
                        'covers' => [
                            'How platform scope differs from tenant scope, and who maintains each',
                            'Choosing the channel type and display name when adding a channel',
                            'Binding agents, and what it means that the first binding is the default',
                        ],
                    ],
                    'credentials' => [
                        'title'  => 'Credential fields per channel',
                        'covers' => [
                            'What to fill in for Feishu, DingTalk and WeCom',
                            'What to fill in for WeChat, QQ, Telegram, Slack and Discord',
                            'An empty secret field means the value stays unchanged',
                            'Credentials are stored encrypted and never echoed back',
                        ],
                    ],
                    'scan' => [
                        'title'  => 'Scan-to-connect, enabling and disconnecting',
                        'covers' => [
                            'WeChat sign-in by QR code and WeCom bot creation by QR code',
                            'Creating a Feishu app by QR code',
                            'QR code expiry and what to do when it times out',
                            'Enable versus disconnect, and the extra password prompt for sensitive actions',
                        ],
                    ],
                ],
            ],

            // ===== 权限与角色设置（管理控制台）=====
            'roles' => [
                'nav'   => 'Roles and permissions',
                'title' => 'Roles and permissions: who gets which features and resources',
                'lead'  => 'A role combines feature permissions and resource grants: configure it in the role editor, then assign it.',
                'videos' => [
                    'editor' => [
                        'title'  => 'The role list and the role editor',
                        'covers' => [
                            'The columns of the role list and the built-in roles',
                            'Creating a role and duplicating an existing one',
                            'What each of the six editor tabs configures',
                            'The code, name and permission points on the basics tab',
                        ],
                    ],
                    'permissions' => [
                        'title'  => 'Feature permissions and resource grants',
                        'covers' => [
                            'Feature permissions are grouped in a fixed catalogue you only check',
                            'Select all on this page, and how paging affects it',
                            'Checking the assignable models first, then setting defaults',
                            'Resources and permissions are isolated per tenant',
                        ],
                    ],
                    'assign' => [
                        'title'  => 'Assigning roles to members',
                        'covers' => [
                            'Checking roles for a member under member management',
                            'Where to see the effective roles of a member',
                            'When a permission change takes effect',
                            'How platform admins and tenant owners bypass checks',
                        ],
                    ],
                ],
            ],

            // ===== 成员与组织（管理控制台）=====
            'members' => [
                'nav'   => 'Members and org',
                'title' => 'Members and org: bring people in and place them',
                'lead'  => 'Create members, assign roles, maintain the department hierarchy and handle password resets.',
                'videos' => [
                    'create' => [
                        'title'  => 'Creating a member',
                        'covers' => [
                            'Selecting more than one target tenant',
                            'Account, display name and temporary password',
                            'Choosing department and position',
                            'Assigning roles and handing the temporary password to the member',
                        ],
                    ],
                    'password' => [
                        'title'  => 'Editing, disabling and resetting passwords',
                        'covers' => [
                            'Changing display name, department and position',
                            'Using the enable toggle to disable and restore an account',
                            'Resetting a password to issue a one-time temporary password',
                            'The member must change it right after signing in',
                        ],
                    ],
                    'org' => [
                        'title'  => 'Organization structure',
                        'covers' => [
                            'Creating a department: code, name, parent and order',
                            'Adjusting the hierarchy by editing the parent',
                            'Viewing the members of a department',
                            'The rejection you get when parents form a cycle',
                        ],
                    ],
                ],
            ],

            // ===== 租户与审计（管理控制台）=====
            'tenant' => [
                'nav'   => 'Tenants and audit',
                'title' => 'Tenants and audit: mind the boundaries and keep the trail',
                'lead'  => 'On the platform side, maintain tenants and use identity audit to see who did what and when.',
                'videos' => [
                    'tenant' => [
                        'title'  => 'Tenant management',
                        'covers' => [
                            'Filtering by status and reading the list columns',
                            'Why creating a tenant only asks for basic information',
                            'The five tabs each submit their own changes',
                            'The checks required before disabling, archiving and restoring',
                        ],
                    ],
                    'audit' => [
                        'title'  => 'Identity audit',
                        'covers' => [
                            'Searching by actor and action',
                            'Filtering by result and time range',
                            'The action, result and masked summary of one record',
                            'What platform and tenant scopes each can see',
                        ],
                    ],
                ],
            ],

            // ===== 个人账号设置（个人与参考）=====
            'account' => [
                'nav'   => 'Personal account',
                'title' => 'Personal account: profile, password and preferences',
                'lead'  => 'All in the account menu at the bottom of the sidebar: profile, password, preferences and tenant switching.',
                'videos' => [
                    'profile' => [
                        'title'  => 'Personal profile',
                        'covers' => [
                            'The account menu, then Profile',
                            'Changing the display name',
                            'Changing the avatar',
                            'Where to see your own effective roles',
                        ],
                    ],
                    'password' => [
                        'title'  => 'Changing the password and personal preferences',
                        'covers' => [
                            'Filling in the current and new password under account security',
                            'Other signed-in sessions stop working after a password change',
                            'Theme and interface language under personal preferences',
                            'Preferences are stored in the current browser only',
                        ],
                    ],
                    'tenant-switch' => [
                        'title'  => 'Switching tenants',
                        'covers' => [
                            'Using the tenant selector in the top bar',
                            'The page reloads for the newly selected tenant',
                            'What you see when you belong to no tenant',
                        ],
                    ],
                ],
            ],

            // ===== 命令速查（个人与参考）=====
            'commands' => [
                'nav'   => 'Command reference',
                'title' => 'Command reference',
                'lead'  => 'A quick look at terminal operations and in-chat commands; the two tables below are for lookup.',
                'videos' => [
                    'commands' => [
                        'title'  => 'Terminal and in-chat commands in practice',
                        'covers' => [
                            'Terminal: start, stop, restart and check status',
                            'In chat: /status, /context, /clear, /compact',
                            'Skills and knowledge: /skill, /knowledge',
                            'Memory and configuration: /memory, /config',
                            'Run control: /cancel, /steer, /logs',
                            'Slash command aliases can be customized',
                        ],
                    ],
                ],
            ],

            // ===== 故障排查（个人与参考）=====
            'troubleshoot' => [
                'nav'   => 'Troubleshooting',
                'title' => 'Troubleshooting: common symptoms and what to do',
                'lead'  => 'Watch the walkthrough for the common symptoms, then match the exact cause against the reference below.',
                'videos' => [
                    'issues' => [
                        'title'  => 'Troubleshooting walkthrough',
                        'covers' => [
                            'After signing in you only see the change password page',
                            'Start conversation is unavailable or reports no permission',
                            'You uploaded a file but the assistant cannot see it',
                            'A document you just added is missing from the knowledge base',
                            'A channel is configured but no messages arrive',
                            'A forgotten password and sign-in rate limiting',
                            'What a 401, 403 or 503 response actually means',
                        ],
                    ],
                ],
                'items' => [
                    ['ask' => 'After signing in I only see the change password page', 'answer' => 'This is a restricted session that must change its password (a temporary password was used, or an admin just reset it). It can only view minimal self information, change the password and sign out. Finish the change and everything works normally; admin status is no exception.'],
                    ['ask' => 'The Start conversation button on an agent card does nothing or reports no permission', 'answer' => 'That agent is currently not runnable, or your role cannot chat. The page states the concrete reason (for example, chatting is not available yet in this version, or you have no chat permission and should contact an admin).'],
                    ['ask' => 'Creating a member is rejected because the temporary password is not acceptable', 'answer' => 'A temporary password needs at least 8 characters and must not be in the common password blocklist. The form returns structured validation errors naming the reason; change it and retry.'],
                    ['ask' => 'I uploaded a file but the assistant cannot see it', 'answer' => 'Attachments only count when submitted with the message: after selecting the file, confirm it appears above the input box, then send that message. You can also drag the file straight into the conversation.'],
                    ['ask' => 'A document I just added is missing from the knowledge base', 'answer' => 'First confirm the agent selected at the top left is the right one, since knowledge is split per agent and shared and independent stores are separate. Then check that the category you are looking at is not filtered out. Saving or importing syncs the index automatically.'],
                    ['ask' => 'A channel is configured but no messages arrive', 'answer' => 'Configuring an instance and actually receiving messages are two different things: confirm the instance is enabled and that a usable agent is bound to it. Personal channels are off by default in enterprise deployments.'],
                    ['ask' => 'I forgot my password', 'answer' => 'Ask an admin to reset it under member management. You receive a one-time temporary password and must set a new password immediately after signing in.'],
                    ['ask' => 'Sign-in says to try again later (429)', 'answer' => 'Failed sign-in attempts passed the threshold and triggered rate limiting; retry later. The system returns the same message for an unknown account and a wrong password so that account existence is not disclosed.'],
                    ['ask' => 'The API returns 401', 'answer' => 'The request has no valid session, or the credential expired. Sign in again and retry. If two credentials from different sources are sent together the server rejects the request with 400 mixed_credentials, so keep only one.'],
                    ['ask' => 'The API returns 403', 'answer' => 'The current identity lacks the required eligibility or permission. When a resource is not visible to you the server returns 404 rather than 403, so that a status code cannot reveal whether the resource exists.'],
                    ['ask' => 'The API returns 503', 'answer' => 'The identity store is unavailable. The system does not fall back to a default identity or a shared password; restore the identity store first. Consumers that are explicitly not enabled are also rejected with 503 without triggering downstream side effects.'],
                    ['ask' => 'A skill or command execution is rejected', 'answer' => 'Under multi-tenancy, code execution is confined to an isolation boundary: path traversal, symlink escapes and cross-tenant directory references are all rejected. Until execution isolation is accepted, arbitrary code execution is denied by default, and enabling a higher privilege mode never breaks tenant isolation.'],
                    ['ask' => 'Usage is rejected as over the limit', 'answer' => 'A quota hard limit was reached. Quotas are metered per tenant and identity; after a quota is lowered, already queued tasks are re-checked before they trigger and are rejected. Adjust the quota or reduce usage.'],
                    ['ask' => 'Backup or restore fails', 'answer' => 'The service must be stopped before restoring (cow stop) or the restore is rejected. Backup archives contain API keys and personal data, so store them as sensitive files.'],
                    ['ask' => 'Port 9899 is in use and the service will not start', 'answer' => 'Find the process holding it first: lsof -nP -i :9899 | grep LISTEN. Stop that process, or use another port (web_port in config.json, or the COW_WEB_PORT environment variable).'],
                    ['ask' => 'The process refuses to start and the log mentions the identity mode', 'answer' => 'Check identity_mode in config.json. Only database is supported; explicitly configuring the old shared password mode makes startup fail and the system will not fall back to a default identity.'],
                ],
            ],

            // ===== 深入阅读（个人与参考）=====
            'further' => [
                'nav'   => 'Further reading',
                'title' => 'Further reading: the complete docs by topic',
                'lead'  => 'When you need principles, parameters or edge cases, enter the matching capability docs from the groups below.',
                'videos' => [
                    'docs' => [
                        'title'  => 'How to read the full capability docs',
                        'covers' => [
                            'Entering the matching capability doc by topic',
                            'What the docs hold: principles, parameters and boundaries',
                            'Every doc is a localized offline copy, so you never leave the site',
                        ],
                    ],
                ],
            ],
        ],
    ],

    'footer' => [
        'copyright' => 'Great capacity through inclusiveness',
    ],
];
