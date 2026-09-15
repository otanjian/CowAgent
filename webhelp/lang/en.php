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
        'title_accent' => 'Product Manual',
        'lead'         => 'This is an application operation manual: from signing in and chatting, to choosing and creating agents, setting up knowledge bases, connecting messaging channels and configuring permissions — each step tells you where to click and what to fill in. When you need the underlying design or limits, jump to the matching capability document.',
        'toc'          => 'Contents',
        'goal_label'   => 'Goal',
        'docs_label'   => 'Related documents',
        'pages_label'  => 'Related pages',
        'cli_title'    => 'Terminal commands',
        'slash_title'  => 'In-chat commands',

        // Manual groups (order drives page and sidebar grouping)
        'parts' => [
            'workbench' => 'Workbench (daily use)',
            'console'   => 'Admin console (configuration and governance)',
            'personal'  => 'Account and reference',
        ],

        // Titles of in-site pages referenced by chapters
        'page_links' => [
            'quickstart'   => 'Install and deployment commands',
            'features'     => 'Capabilities in depth',
            'enterprise'   => 'Enterprise governance overview',
            'architecture' => 'System architecture',
        ],

        'sections' => [
            // ===== Getting started (workbench) =====
            'start' => [
                'nav'   => 'Getting started',
                'title' => 'Getting started: sign in and find your way around',
                'goal'  => 'Sign in with your account and tell the workbench and the admin console apart. This manual covers workbench use first, then admin console configuration and governance.',
                'blocks' => [
                    'login' => [
                        'title' => 'Sign in',
                        'steps' => [
                            'Open the console and sign in with the account and password your administrator gave you.',
                            'A first sign-in uses a time-limited temporary password (valid for 24 hours by default). You land in a restricted session that can only view a minimal slice of your own information, change your password, and sign out.',
                            'Set a new password when prompted. After that the console is fully available; changing your password also invalidates your other signed-in sessions, so you sign in again.',
                            'If you forget your password, ask an administrator to reset it; you will receive a new one-time temporary password.',
                        ],
                        'note' => 'Too many failed sign-in attempts return 429 and ask you to retry later. A nonexistent account and a wrong password return the same message, so the response never reveals whether an account exists.',
                    ],
                    'navigate' => [
                        'title' => 'Two navigation areas',
                        'steps' => [
                            'The workbench is for daily use and has six pages: Chat, Agents, My Todos, Scheduled Tasks, Knowledge Base, and Scenes.',
                            'The admin console is for configuration and governance. It is grouped into four sections — Agent development (Agents, Tools & Skills, Memory), Models & channels (Model Service, Messaging Channels), Organization & permissions (Members, Organization, Roles), and Platform operations (Tenants, System Settings, Branding, Runtime Logs, Audit) — with an overview page above them.',
                            'Switch between the two areas from the entry at the top of the sidebar. If no admin console page is available to you, the entry does not appear at all.',
                            'The account menu sits at the bottom of the sidebar: your identity, tenant switching, password, personal preferences, and sign-out.',
                        ],
                        'note' => 'What you can see is decided by server-side authorization: only pages you may read and that are enabled are rendered, and empty groups are hidden. Hiding a menu is not access control — every request is re-checked on the server.',
                    ],
                    'preference' => [
                        'title' => 'Theme and interface language',
                        'steps' => [
                            'Open the account menu at the bottom of the sidebar and choose "Preferences".',
                            'Pick a theme (light or dark) and an interface language (Simplified Chinese, Traditional Chinese, or English). The change applies immediately.',
                        ],
                        'note' => 'Preferences are stored in your browser only. They are not written to the instance configuration and do not affect your colleagues.',
                    ],
                ],
            ],

            // ===== How to chat (workbench) =====
            'chat' => [
                'nav'   => 'How to chat',
                'title' => 'How to chat: pick an agent, send a task, attach files',
                'goal'  => 'Start a conversation, pick the right agent, and hand work over with attachments and commands.',
                'blocks' => [
                    'new' => [
                        'title' => 'Start a conversation',
                        'steps' => [
                            'Click "New Chat" in the sidebar, or open the History page and click "New Chat".',
                            'Click the caret next to "New Chat" to choose an agent: the menu lists the agents available to you, and clicking one starts a new session with it.',
                            'For work that needs several agents, choose "Group chat" from the same menu, tick the participating agents, and click Start.',
                            'You can also start from the workbench: on the Agents page, click "Start chat" on an agent card.',
                            'Describe your goal in the input box ("Describe your task, or just ask…") and press Enter or click send.',
                        ],
                        'note' => 'In a group chat the first agent you tick is the session default: it receives messages and can delegate to the other members. When an agent cannot run, "Start chat" is blocked and the reason is shown instead (for example, chat is not enabled in this version, or you have no chat permission).',
                    ],
                    'compose' => [
                        'title' => 'Every control in the composer',
                        'fields' => [
                            ['name' => 'Send / Cancel', 'desc' => 'Sends the message; while a run is active the button turns into Cancel and stops that run.'],
                            ['name' => 'Session model', 'desc' => 'Chooses the model for this session: follow the global setting, follow the agent default, or name a specific model.'],
                            ['name' => 'Optimize prompt', 'desc' => 'Rewrites a casual description into a clearer instruction before sending.'],
                            ['name' => 'Voice input', 'desc' => 'Click the microphone to record, click again to stop; the audio is transcribed into text you can send as a question.'],
                            ['name' => 'Clear Context', 'desc' => 'Clears the conversation context of the current session and continues from a clean slate.'],
                            ['name' => 'Steer active task', 'desc' => 'Available while a run is active: add instructions or correct the direction without cancelling and starting over.'],
                        ],
                        'note' => 'To keep the context within limits, send /compact in the conversation to compress earlier turns, or /clear to empty it.',
                    ],
                    'attach' => [
                        'title' => 'Upload files and folders',
                        'steps' => [
                            'Click the attachment button in the composer and choose "Upload File" or "Upload Folder". You can also drag files straight into the conversation or paste an image.',
                            'Selected files appear above the input box and are submitted together with your next message.',
                            'Supported: common images, documents (PDF, Word, Excel, PowerPoint), text and code files, and zip, rar, and 7z archives.',
                            'The workspace panel on the right has a Preview and a Files tab where you can edit, save, download, copy the path of, or open a file in a new tab.',
                        ],
                        'note' => 'An uploaded folder is stored in the workspace as a whole, so the agent can process all of its files in one go.',
                    ],
                    'session' => [
                        'title' => 'Find and manage past conversations',
                        'steps' => [
                            'Open the History page to see past sessions across all agents, and search by title with "Search conversation titles".',
                            'In the sidebar you can pin a frequent session, rename it, or archive it.',
                            'Archived sessions are collected under the archived group; click Restore to bring one back.',
                            'Deleting a session cannot be undone: once confirmed, every message in it is removed.',
                        ],
                    ],
                    'command' => [
                        'title' => 'Slash commands and @ references',
                        'steps' => [
                            'Type / in the composer to open the command menu — for example /status for runtime state, /context for the conversation context, /skill list for installed skills, and /knowledge for knowledge base statistics.',
                            'Type @ to reference an agent or a file and pull it into your question.',
                        ],
                        'note' => 'The full command list is in the "Command reference" chapter of this manual.',
                    ],
                ],
            ],

            // ===== Agents (workbench) =====
            'agents' => [
                'nav'   => 'Choose an agent',
                'title' => 'Choose an agent in the workbench',
                'goal'  => 'Browse the agents available to you on the workbench Agents page, see what each one does, and start chatting from its card.',
                'blocks' => [
                    'browse' => [
                        'title' => 'Browse agents and start chatting',
                        'steps' => [
                            'Open the Agents page in the workbench; cards list the agents you can use along with their availability.',
                            'Each card shows the agent\'s name, responsibilities, and availability; click Refresh when you need the latest state.',
                            'Click "Start chat" on a card to open a new session with that agent.',
                            'To see an agent\'s full configuration first, open it under Agents in the admin console.',
                        ],
                        'note' => 'The list only shows agents you are authorized to reach. When it is empty the page distinguishes "no agents available" from "no accessible agents — ask an administrator to check your authorization": the second is a permission problem, not missing data.',
                    ],
                ],
            ],

            // ===== Agents (admin console) =====
            'agents-admin' => [
                'nav'   => 'Create and configure agents',
                'title' => 'Create and configure agents',
                'goal'  => 'Create an agent under Agents in the admin console, then configure its profile, skills, and core files tab by tab.',
                'blocks' => [
                    'create' => [
                        'title' => 'Create an agent',
                        'steps' => [
                            'Open the admin console, go to Agents, and click "New Agent".',
                            'Fill in the name and responsibilities, and optionally set an avatar, an ID, and the knowledge mode.',
                            'To start from an existing agent, pick one under "Copy from an existing agent": its configuration, skills, and knowledge are used as the starting point.',
                            'Click "New Agent" to finish creating it; you can then continue configuring skills and core files in the detail panel.',
                        ],
                        'fields' => [
                            ['name' => 'Name', 'desc' => 'The agent\'s display name. Required.'],
                            ['name' => 'Avatar', 'desc' => 'Optional; upload an image.'],
                            ['name' => 'ID', 'desc' => 'A unique identifier: lowercase letters, digits, and hyphens only, fixed once created. Left blank, it is derived from the name.'],
                            ['name' => 'Responsibilities', 'desc' => 'What this agent handles and in what situations it is used. In group chats it drives task assignment.'],
                            ['name' => 'Copy from an existing agent', 'desc' => 'Copies that agent\'s configuration, skills, and knowledge as a starting point; choose Blank to create a fresh agent.'],
                            ['name' => 'Knowledge base', 'desc' => 'Shared means the agent reads and writes the same knowledge base as the team; Own gives it a dedicated knowledge base that does not affect the team.'],
                        ],
                    ],
                    'configure' => [
                        'title' => 'Configure an agent: Profile',
                        'steps' => [
                            'Open an agent from the Agents list; it opens on the Profile tab.',
                            'Add the position, category, tags, greeting, and persona summary, and link a scene if needed.',
                            'Choose the agent\'s default model.',
                            'Click Save when done, or go straight to "Start chat"; "Set as default" makes it the tenant default agent.',
                        ],
                        'fields' => [
                            ['name' => 'Position', 'desc' => 'For example "Procurement Specialist" — the role this agent plays in the organization.'],
                            ['name' => 'Category', 'desc' => 'Files the agent under a business category; shows "Uncategorized" when unset.'],
                            ['name' => 'Tags', 'desc' => 'Comma-separated, for example "supplier, tender", to make the agent easier to find.'],
                            ['name' => 'Greeting', 'desc' => 'The opening line used when a session with this agent starts.'],
                            ['name' => 'Persona summary', 'desc' => 'A description of who this agent is, injected into its system prompt.'],
                            ['name' => 'Default model', 'desc' => 'The model this agent uses by default. The default agent is locked to following the global configuration, which you change in Model Service.'],
                        ],
                        'note' => 'The default agent is marked as such, cannot be deleted, and its model and knowledge mode cannot be changed from the interface.',
                    ],
                    'capability' => [
                        'title' => 'Configure an agent: Skills',
                        'steps' => [
                            'Open the Skills tab to decide which skills and tools this agent may use.',
                            'For skills, either use every installed skill, or enable only the ones you tick.',
                            'Manage tools as an allow list or a deny list: ticking Allow enables only the selected tools, while Deny removes tools from the available set. The two are mutually exclusive.',
                            'To lock in a standard process, enter an SOP ID under SOPs and click Add.',
                        ],
                        'note' => 'With neither an allow list nor a deny list, the agent may use every installed tool. Prefer an allow list when narrowing the scope.',
                    ],
                    'corefiles' => [
                        'title' => 'Configure an agent: Core files and Tasks',
                        'steps' => [
                            'The Core files tab edits the agent\'s long-term setup: the agent definition, user information, workspace rules, and long-term memory.',
                            'Switch between edit and preview, then click Save.',
                            'The Tasks tab lists the scheduled tasks that belong to this agent.',
                        ],
                        'note' => 'Core files are the agent\'s long-term setup; edits affect every future conversation with it, so keep them concise and accurate.',
                    ],
                ],
                'docs' => ['multiagent'],
            ],

            // ===== Knowledge (workbench) =====
            'knowledge' => [
                'nav'   => 'Set up a knowledge base',
                'title' => 'Set up a knowledge base: categories, documents, and agent binding',
                'goal'  => 'Create categories and documents, then let a specific agent answer from them.',
                'blocks' => [
                    'overview' => [
                        'title' => 'First, the structure: knowledge is per agent',
                        'steps' => [
                            'Open the Knowledge Base page in the workbench; the header shows the current page and size statistics.',
                            'Use the selector at the top to switch agents: shared and dedicated knowledge bases do not affect each other.',
                            'A tenant-level agent\'s knowledge base is maintained by tenant administrators; a private agent\'s is maintained by its owner.',
                        ],
                        'note' => 'Without read permission the page says so plainly instead of showing empty data. That is different from a genuinely empty knowledge base.',
                    ],
                    'category' => [
                        'title' => 'New category',
                        'steps' => [
                            'Click "New" in the top right corner and choose "New category".',
                            'Enter a category path; nesting is supported, for example research/ai.',
                        ],
                        'note' => 'A category becomes a directory under knowledge/. You need a category before you can create or import documents.',
                    ],
                    'document' => [
                        'title' => 'New document',
                        'steps' => [
                            'Click "New" and choose "New document".',
                            'Pick a target category, enter a file name and the content, then save.',
                        ],
                        'fields' => [
                            ['name' => 'Target category', 'desc' => 'The knowledge directory the document goes into; create one first if none exists.'],
                            ['name' => 'File name', 'desc' => 'The .md extension may be omitted; new documents are Markdown only.'],
                            ['name' => 'Content', 'desc' => 'The document body. It cannot be empty, and a single document is capped at 10MB.'],
                        ],
                        'note' => 'Saving synchronizes the index automatically, after which agents can retrieve the document.',
                    ],
                    'import' => [
                        'title' => 'Import existing documents',
                        'steps' => [
                            'Click "New" and choose "Import documents", then select local files (Markdown and TXT, multiple selection supported).',
                            'Pick a target category and confirm the import.',
                        ],
                        'note' => 'TXT files are converted to Markdown. The index is synchronized automatically afterwards. When importing a large batch, sort the files into categories by topic first.',
                    ],
                    'bind' => [
                        'title' => 'Let an agent use the knowledge base',
                        'steps' => [
                            'Choose the knowledge mode when creating or configuring an agent: Shared reads and writes the team knowledge base, Own gives the agent a dedicated one.',
                            'You can review and switch that mode on the Profile tab in Agents (except for the default agent).',
                            'Toggle knowledge in a conversation: /knowledge on enables it, /knowledge off disables it, /knowledge list shows the file tree, and /knowledge shows statistics.',
                        ],
                        'note' => 'Knowledge can also flow the other way: send the agent a document, a link, or a topic in chat and it will file it into the knowledge base for you.',
                    ],
                ],
                'docs' => ['knowledge'],
            ],

            // ===== Memory (admin console) =====
            'memory' => [
                'nav'   => 'Memory',
                'title' => 'Review and consolidate agent memory',
                'goal'  => 'Review what the agent has remembered, and trigger consolidation or an index rebuild when needed.',
                'blocks' => [
                    'view' => [
                        'title' => 'Review memory contents',
                        'steps' => [
                            'Open the admin console, go to Memory, and pick an agent with the selector at the top.',
                            'The "Memory Files" tab lists memory files by name, type, size, and last update.',
                            'Click any row to open and read it.',
                        ],
                        'note' => 'Type badges include Global, Daily, Dream (distillation output), and Evolution (self-evolution), which tell you where a memory came from.',
                    ],
                    'dream' => [
                        'title' => 'Trigger memory consolidation',
                        'steps' => [
                            'Open the "Self-Evolution" tab to review evolution records.',
                            'To consolidate manually, send /memory dream in a conversation; it accepts a day count (3 by default, 30 at most).',
                        ],
                        'note' => 'Distillation needs a working model to be configured. Without one the command reports that it cannot run rather than silently skipping.',
                    ],
                    'index' => [
                        'title' => 'Rebuild the retrieval index',
                        'steps' => [
                            'Change the embedding model in Model Service, where the interface warns that existing indexes become invalid.',
                            'Follow the prompt and send /memory rebuild-index in a conversation to rebuild them.',
                            'Use /memory status to see the current embedding model, its dimensions, and the indexed chunk count.',
                        ],
                        'note' => 'After switching embedding models you must rebuild the index, otherwise memory and knowledge retrieval return inaccurate results.',
                    ],
                ],
                'docs' => ['memory'],
            ],

            // ===== Todos and tasks (workbench) =====
            'todo' => [
                'nav'   => 'Todos and tasks',
                'title' => 'My todos and scheduled tasks',
                'goal'  => 'Track the items you need to follow up, and manage the tasks that run on a schedule.',
                'blocks' => [
                    'todo' => [
                        'title' => 'Create and manage todos',
                        'steps' => [
                            'Open "My Todos" in the workbench and click "New Todo".',
                            'Fill in a title and description, choose a category, priority, and due time, then save.',
                            'Use the filters at the top (all / open / pending / in progress / done / cancelled) and the overdue-only switch to focus, or search by keyword.',
                        ],
                        'fields' => [
                            ['name' => 'Title', 'desc' => 'The name of the todo item.'],
                            ['name' => 'Description', 'desc' => 'Additional context and requirements.'],
                            ['name' => 'Category', 'desc' => 'General item, additional material, plan confirmation, or result acceptance.'],
                            ['name' => 'Priority', 'desc' => 'Low, normal, or high.'],
                            ['name' => 'Due time', 'desc' => 'Optional; clear it again if you set it by mistake.'],
                        ],
                        'note' => 'The agent also creates todos during conversations and synchronizes them here; the source field on an item shows whether you or the agent created it.',
                    ],
                    'task-view' => [
                        'title' => 'Review and run scheduled tasks',
                        'steps' => [
                            'Open "Scheduled Tasks" in the workbench. Tasks are listed with their status, name, schedule, and next run time.',
                            'Click Run now to execute a task immediately; because it sends content to the configured channels and recipients, you confirm first.',
                            'Use the switch on a card to enable or disable a task, and click a card to open the edit dialog.',
                        ],
                        'note' => 'Scheduled tasks are created by the agent inside a conversation through the scheduler tool, and belong to that agent. This page is for reviewing, editing, running, enabling, and deleting them — it deliberately provides no "add task" entry.',
                    ],
                    'task-edit' => [
                        'title' => 'Edit a scheduled task',
                        'steps' => [
                            'Open a task card, change its name, enabled state, schedule, and action, then save.',
                            'The schedule can be a cron expression, a fixed interval (60 seconds minimum), or a one-off.',
                            'The action can be "send a message" or an "AI task"; choose the channel it should use.',
                            'For a task you no longer need, click Delete in the dialog; deletion cannot be undone.',
                        ],
                        'note' => 'The channel type of an existing task cannot be changed. If the task capability is unavailable, the page tells you to ask an administrator to enable it or finish the adaptation.',
                    ],
                ],
                'docs' => ['tools-scheduler'],
            ],

            // ===== Model service (admin console) =====
            'models' => [
                'nav'   => 'Model service',
                'title' => 'Configure models and provider credentials',
                'goal'  => 'Connect usable providers and credentials, and decide which model each capability uses.',
                'blocks' => [
                    'basic' => [
                        'title' => 'Basics',
                        'steps' => [
                            'Open the admin console, go to Model Service; it opens on the Basics tab.',
                            'Under Model configuration choose a provider and model and save. With Custom, the endpoint must follow the OpenAI API protocol.',
                            'Under Agent configuration tune the runtime parameters: maximum context tokens, maximum memory turns, maximum execution steps, deep thinking and its effort, sub-agents, and self-evolution.',
                            'Under System set the interface language and task notifications, including the notification sound.',
                        ],
                        'note' => 'The default execution permission shown under Security settings comes from the role resource grants your administrator assigned. It is read-only here and cannot be changed from within a conversation.',
                    ],
                    'vendor' => [
                        'title' => 'Provider credentials',
                        'steps' => [
                            'Switch to the Models tab and click "Add Provider" under vendor credentials.',
                            'Choose a provider and enter the API key. Put a self-hosted gateway or proxy address in API Base; leave it blank to use the official default.',
                            'After saving, that provider\'s models become available in the capability cards and in role grants.',
                            'For your own OpenAI-compatible service, use "Add custom provider" with a name, API Base, and API key.',
                        ],
                        'note' => 'Credentials are stored encrypted and are never echoed back to the page. Clearing them disables the related capabilities immediately, so the interface asks for confirmation first. There is no "test connection" button: verify a new configuration with one real conversation.',
                    ],
                    'capability' => [
                        'title' => 'Choose the model for each capability',
                        'steps' => [
                            'Assign models on the capability cards: main model, main-model fallback, vision, image generation, speech recognition, speech synthesis, embedding, and web search.',
                            'The main model is what chat uses by default; with the fallback enabled, an unavailable main model switches over automatically.',
                            'The embedding model drives memory and knowledge retrieval, so switching it requires an index rebuild.',
                        ],
                        'note' => 'Which models a given role may actually use is decided by the grants on the Models tab of Roles. Here you only set instance-level defaults.',
                    ],
                ],
                'docs' => ['models'],
            ],

            // ===== Messaging channels (admin console) =====
            'channels' => [
                'nav'   => 'Messaging channels',
                'title' => 'Connect messaging channels',
                'goal'  => 'Connect the assistant to WeChat, Feishu, DingTalk and other channels, bound to the right agent.',
                'blocks' => [
                    'scope' => [
                        'title' => 'Know which scope you are configuring in',
                        'steps' => [
                            'Open the admin console, go to Messaging Channels. Depending on your permissions the page shows one of two scopes.',
                            'Platform scope: manage the channels connected at instance level.',
                            'Tenant scope: configure the channels belonging to this tenant; credentials are stored encrypted and never echoed back.',
                        ],
                        'note' => 'When you lack access or the feature is not open, the page says so explicitly rather than showing an empty list.',
                    ],
                    'create' => [
                        'title' => 'Create a channel instance',
                        'steps' => [
                            'Click Connect in the top right corner and choose a channel type.',
                            'Enter a display name and choose the agent to bind.',
                            'Fill in the credential fields that channel type requires, then save.',
                        ],
                        'note' => 'You can bind several agents: the first is the default, receiving messages and delegating to the others. "Do not bind" is also allowed. Leaving a secret field blank keeps the existing value; saving takes effect immediately with no service restart.',
                    ],
                    'credentials' => [
                        'title' => 'Credentials each channel needs',
                        'fields' => [
                            ['name' => 'Feishu', 'desc' => 'App ID and App Secret; optionally a verification token and bot name.'],
                            ['name' => 'DingTalk', 'desc' => 'Client ID, Client Secret, and the robot code.'],
                            ['name' => 'WeCom smart robot', 'desc' => 'Bot ID, Secret, Token, and EncodingAESKey.'],
                            ['name' => 'WeChat', 'desc' => 'Token and callback base URL.'],
                            ['name' => 'QQ bot', 'desc' => 'App ID and App Secret.'],
                            ['name' => 'Telegram', 'desc' => 'Bot Token.'],
                            ['name' => 'Slack', 'desc' => 'Bot Token (xoxb-) and App Token (xapp-).'],
                            ['name' => 'Discord', 'desc' => 'Bot Token.'],
                        ],
                        'note' => 'Secret fields render as password inputs and are never shown in clear text again after saving.',
                    ],
                    'scan' => [
                        'title' => 'Scan a QR code instead of typing credentials',
                        'steps' => [
                            'WeChat: choose the WeChat scan sign-in option, scan the QR code in the card, and confirm on your phone — the channel starts automatically.',
                            'WeCom smart robot: switch to the scan tab and scan with WeCom to create the smart robot in one step; manual entry remains available.',
                            'Feishu: switch to the scan tab and scan with the Feishu app to create the application with permissions and event subscriptions preconfigured; manual entry remains available.',
                        ],
                        'note' => 'QR codes expire (about 2 minutes for WeChat; 10 minutes and a single scan for Feishu). Retry when prompted. After a successful scan the credentials are saved automatically and appear in the list.',
                    ],
                    'bind' => [
                        'title' => 'Enable, disable, and disconnect',
                        'steps' => [
                            'Use the enable/disable control on the card to decide whether the channel runs.',
                            'Confirm to apply; sensitive operations ask for your current account password again.',
                            'Disconnect stops the channel while keeping its configuration, once confirmed.',
                        ],
                        'note' => 'Creating a channel instance is not the same as receiving messages: make sure the instance is enabled and bound to a usable agent.',
                    ],
                ],
                'docs' => ['channels'],
            ],

            // ===== Roles and permissions (admin console) =====
            'roles' => [
                'nav'   => 'Roles and permissions',
                'title' => 'Permissions: combine them into roles and assign them',
                'goal'  => 'Create or adjust a role, combine functional permissions with accessible resources, and assign it to members.',
                'blocks' => [
                    'list' => [
                        'title' => 'The role list',
                        'steps' => [
                            'Open the admin console, go to Roles. The list shows each role\'s name, code, whether it is built in, and its permission labels.',
                            'Click "New role" to create a custom role.',
                            'To start from an existing role, click Copy; the new role is created with a "(copy)" suffix.',
                            'Click Members to see who currently holds the role.',
                        ],
                        'note' => 'Built-in roles (Tenant Administrator, Member) are marked as such and offer no delete action, but their permissions and resource grants can still be edited.',
                    ],
                    'editor' => [
                        'title' => 'The six tabs of the role editor',
                        'steps' => [
                            'Click Edit, or create a new role, to open the editor: Basics, Menus, Skills, Tools, Agents, and Models, each with a count of what is selected.',
                            'On Basics, set the code and name, then tick functional permissions from the catalog (searchable, and you can clear the selection).',
                            'The other tabs are resource grants: tick the menus, skills, tools, agents, and models this role may access.',
                            'Click Save when done.',
                        ],
                        'note' => 'The code cannot be changed after creation. Switching tabs does not lose your draft, and leaving with unsaved changes asks for confirmation first.',
                    ],
                    'permissions' => [
                        'title' => 'Assignable functional permissions',
                        'fields' => [
                            ['name' => 'Tenant', 'desc' => 'View tenant information, view members, view the organization.'],
                            ['name' => 'Assets and sessions', 'desc' => 'View agents, view conversation history.'],
                            ['name' => 'Knowledge / memory / todos', 'desc' => 'View knowledge, view memory, view todos, manage todos.'],
                            ['name' => 'Skills', 'desc' => 'View skills, use skills, edit skills, enable or disable skills.'],
                            ['name' => 'Tools', 'desc' => 'View tools, execute tools, configure tools.'],
                            ['name' => 'Models', 'desc' => 'View models, use models.'],
                            ['name' => 'Agents', 'desc' => 'Use agents, edit agents, enable or disable agents.'],
                            ['name' => 'Chat', 'desc' => 'Use chat.'],
                        ],
                        'note' => 'The permission catalog is fixed on the server; the interface can only tick entries from it and cannot invent new permissions. Anything not ticked is denied.',
                    ],
                    'resources' => [
                        'title' => 'Resource grants and model defaults',
                        'steps' => [
                            'On the Menus, Skills, Tools, and Agents tabs, search for a resource and tick what this role may use ("Select this page" and paging are available).',
                            'On the Models tab first tick the assignable models, then pick role default models for each connected capability under Model defaults.',
                        ],
                        'note' => 'Resource grants are tenant-scoped: only resources inside your tenant can be selected, and model defaults must come from the assigned models.',
                    ],
                    'assign' => [
                        'title' => 'Assign a role to a member',
                        'steps' => [
                            'Open Members and find the Roles field in the create or edit member dialog.',
                            'Tick the roles this member should hold (Member is ticked by default) and save; the change applies immediately.',
                            'To confirm someone\'s effective permissions, ask them to open the account menu and check "Actual roles" under Profile.',
                        ],
                        'note' => 'Role changes are re-resolved on the server, so a signed-in session picks up the new permissions without signing in again.',
                    ],
                    'rules' => [
                        'title' => 'A few hard rules',
                        'steps' => [
                            'An instance always keeps at least one active administrator; the last one cannot be disabled or demoted.',
                            'Editing a built-in role\'s permissions never changes administrator standing — administrator status and the permission set are independent.',
                            'A platform administrator can see the tenant list, which does not grant membership or read access to that tenant\'s business data.',
                        ],
                        'note' => 'Every grant is checked independently at the server API layer and re-resolved on each request. Hiding a menu is only a visual affordance, not access control.',
                    ],
                ],
            ],

            // ===== Members and organization (admin console) =====
            'members' => [
                'nav'   => 'Members and organization',
                'title' => 'Manage member accounts and the organization',
                'goal'  => 'Create and maintain member accounts, handle temporary passwords, and build the department structure.',
                'blocks' => [
                    'create' => [
                        'title' => 'Create a member',
                        'steps' => [
                            'Open the admin console, go to Members, and click "New member".',
                            'Under Tenants, choose the target tenants (multiple selection allowed).',
                            'Under Account Information, fill in the username, display name, and temporary password, and optionally a department and position.',
                            'Under Roles & Status, tick the roles and click Create.',
                            'Hand the temporary password to the member; they must change it at first sign-in.',
                        ],
                        'fields' => [
                            ['name' => 'Target tenants', 'desc' => 'Which tenants to join. The first tenant gets a new account; the others join by binding that account.'],
                            ['name' => 'Username', 'desc' => '3-64 characters from letters, digits, and . _ -'],
                            ['name' => 'Display name', 'desc' => 'The name shown in the console and the organization. Required.'],
                            ['name' => 'Temporary password', 'desc' => 'At least 8 characters; avoid common passwords. The member must change it at first sign-in.'],
                            ['name' => 'Department', 'desc' => 'Optional; defaults to no department.'],
                            ['name' => 'Position', 'desc' => 'Optional; the job title used inside the organization.'],
                            ['name' => 'Roles', 'desc' => 'Decides the member\'s functional permissions and accessible resources. Member is the default.'],
                        ],
                        'note' => 'A global account and a tenant membership are separate things: the same account can hold an independent name, department, and set of roles in each tenant.',
                    ],
                    'edit' => [
                        'title' => 'Edit a member and toggle access',
                        'steps' => [
                            'Click a member in the list to open the edit dialog, where you can change the display name, department, position, and roles.',
                            'Use the enable switch to control whether the member is usable in the current tenant, then save.',
                        ],
                        'note' => 'The account itself cannot be changed. Disabling a member affects only the current tenant; their identity and history in other tenants are untouched.',
                    ],
                    'password' => [
                        'title' => 'Reset a password',
                        'steps' => [
                            'Use the reset action on a member row; after confirmation the system generates a one-time temporary password.',
                            'Give it to the member, who must set a new password at the next sign-in.',
                        ],
                        'note' => 'A temporary password expires (24 hours by default). Once expired the member cannot sign in and the password must be reset again.',
                    ],
                    'org' => [
                        'title' => 'Organization',
                        'steps' => [
                            'Open Organization and click "New department", then fill in the code, name, parent department, and sort order.',
                            'To move a department, edit its parent department; the sort order is also changed in the edit dialog.',
                            'Use the members action on a department row to see who is in it.',
                        ],
                        'note' => 'A department code cannot be changed after creation. A department still referenced by members or resources must be unreferenced before it can be deleted, and a cycle in the hierarchy is rejected outright.',
                    ],
                ],
            ],

            // ===== Tenants and audit (admin console) =====
            'tenant' => [
                'nav'   => 'Tenants and audit',
                'title' => 'Tenant management and identity audit',
                'goal'  => 'Create and maintain tenants from the platform side, and review audit records as required.',
                'blocks' => [
                    'tenant' => [
                        'title' => 'Tenant management',
                        'steps' => [
                            'Platform administrators open Tenants and filter by status (not archived, active, disabled, archived); archived tenants are excluded from the list by default.',
                            'Click "New tenant" to fill in the basics: on creation only the Basics tab can be submitted.',
                            'Once inside an existing tenant, each of the five tabs is submitted separately.',
                            'Disabling, archiving, and restoring all re-check that the tenant still has an active administrator.',
                        ],
                        'fields' => [
                            ['name' => 'Basics', 'desc' => 'The tenant name, code, and other fundamentals. The code is globally unique and cannot be changed.'],
                            ['name' => 'Model grants', 'desc' => 'The range of models this tenant may use.'],
                            ['name' => 'Tool grants', 'desc' => 'The range of tools this tenant may use.'],
                            ['name' => 'Agents', 'desc' => 'Agent-related configuration inside the tenant.'],
                            ['name' => 'Tenant management', 'desc' => 'Designate or adjust this tenant\'s administrators.'],
                        ],
                        'note' => 'Creating a tenant generates its identifier, built-in roles, and organization root in a single transaction, and the response never echoes the initial administrator\'s temporary password. An archived tenant only offers Restore, and only once it has an active tenant administrator.',
                    ],
                    'audit' => [
                        'title' => 'Identity audit',
                        'steps' => [
                            'Open Audit and search by actor or action, and filter by result (all, success, denied).',
                            'Each record shows the action, result, actor, a localized timestamp, and a redacted summary of the change.',
                        ],
                        'note' => 'Platform administrators see every record; tenant administrators see only their own tenant\'s. Regular members have no audit entry at all.',
                    ],
                ],
            ],

            // ===== Your account (account and reference) =====
            'account' => [
                'nav'   => 'Your account',
                'title' => 'Maintain your own account and preferences',
                'goal'  => 'Manage your profile, password, interface preferences, and current tenant.',
                'blocks' => [
                    'profile' => [
                        'title' => 'Profile',
                        'steps' => [
                            'Open the account menu at the bottom of the sidebar and choose "Profile".',
                            'Review your name, sign-in account, platform identity, current tenant, member name, actual roles, department, and position.',
                            'Click Edit to change your name and save; double-click the avatar to replace it.',
                        ],
                        'note' => '"Actual roles" is where your permissions come from, matching the roles configured on the Roles page. Check there first when permissions look wrong.',
                    ],
                    'password' => [
                        'title' => 'Change your password',
                        'steps' => [
                            'In the account menu choose "Account security", enter your current and new password, and submit.',
                        ],
                        'note' => 'Your other signed-in sessions are invalidated once the change succeeds, so you sign in again with the new password.',
                    ],
                    'prefs' => [
                        'title' => 'Preferences',
                        'steps' => [
                            'In the account menu choose "Preferences" and pick a theme (light or dark) and interface language (Simplified Chinese, Traditional Chinese, English).',
                        ],
                        'note' => 'Preferences apply to this browser only. They are not written to the instance configuration and do not affect others.',
                    ],
                    'tenant-switch' => [
                        'title' => 'Switch tenant',
                        'steps' => [
                            'Use the tenant selector in the top bar to switch tenants; the current one is marked.',
                            'The page reloads for the new tenant, and the agents, knowledge bases, members, and permission scope you see change accordingly.',
                        ],
                        'note' => 'If you belong to no tenant the selector says so; if the target tenant is no longer valid it asks you to choose again.',
                    ],
                ],
            ],

            // ===== Command reference (account and reference) =====
            'commands' => [
                'nav'   => 'Command reference',
                'title' => 'Command reference',
                'goal'  => 'Keep the most useful terminal and in-chat commands at hand.',
                'blocks' => [
                    'commands' => [
                        'title' => 'Terminal and in-chat commands',
                        'note' => 'Start, stop, and restart run in a terminal only; every other command is sent from the composer by starting with /. Slash commands support custom aliases through command_aliases in config.json.',
                    ],
                ],
            ],

            // ===== Troubleshooting (account and reference) =====
            'troubleshoot' => [
                'nav'   => 'Troubleshooting',
                'title' => 'Common problems and what to do',
                'goal'  => 'Match a symptom to its cause, and understand why the system answers the way it does.',
                'blocks' => [
                    'issues' => [
                        'title' => 'Common problems',
                        'items' => [
                            ['ask' => 'After signing in I only see the change-password page', 'answer' => 'You are in the forced-password-change restricted session (temporary password sign-in, or an administrator just reset your password). A restricted session may only view a minimal slice of its own information, change the password, and sign out. Finish the change to continue — administrator status is no exception.'],
                            ['ask' => '"Start chat" on an agent card does nothing or reports no permission', 'answer' => 'Either the agent cannot run right now or your role lacks chat permission. The page names the reason (for example "chat is not enabled in this version" or "no chat permission — ask an administrator"), so follow the message.'],
                            ['ask' => 'Creating a member reports the temporary password is invalid', 'answer' => 'A temporary password needs at least 8 characters and must not match the common-password blocklist. The form returns a structured validation error naming the reason; change it and retry.'],
                            ['ask' => 'I uploaded a file but the agent cannot see it', 'answer' => 'Attachments only count once they are submitted with a message: confirm the file appears above the input box, then send that message. Dragging a file into the conversation works too.'],
                            ['ask' => 'A document I just added is missing from the knowledge base', 'answer' => 'First confirm the correct agent is selected at the top — knowledge is per agent, and shared and dedicated knowledge bases do not affect each other — then confirm the document\'s category is not filtered out. Saving or importing synchronizes the index automatically.'],
                            ['ask' => 'The channel is configured but no messages arrive', 'answer' => 'Configuring an instance and receiving messages are two different things: check that the instance is enabled and bound to a usable agent. Personal channel runtimes are off by default in enterprise deployments.'],
                            ['ask' => 'I forgot my password', 'answer' => 'Ask an administrator to reset it under Members. You receive a one-time temporary password and must set a new one immediately after signing in.'],
                            ['ask' => 'Sign-in says to retry later (429)', 'answer' => 'Too many failed attempts triggered rate limiting; retry shortly. The system returns the same message for a nonexistent account and a wrong password so it never reveals whether an account exists.'],
                            ['ask' => 'An API call returns 401', 'answer' => 'The request has no valid session, or the credential expired. Sign in again and retry. If two credentials from different sources are sent together the server rejects them with 400 mixed_credentials — keep exactly one.'],
                            ['ask' => 'An API call returns 403', 'answer' => 'Your identity lacks the required qualification or permission. Note that when a target resource is not visible to you the server returns 404 rather than 403, so a status code cannot be used to probe whether a resource exists.'],
                            ['ask' => 'An API call returns 503', 'answer' => 'The identity store is unavailable. The system never falls back to a default identity or a shared password, so the store must be restored first. Explicitly disabled consumers are also rejected with 503 and trigger no downstream side effects.'],
                            ['ask' => 'A skill or command execution is denied', 'answer' => 'In multi-tenant mode code execution is confined to an isolation boundary: path traversal, symlink escapes, and cross-tenant directory references are rejected outright, and arbitrary code execution is denied by default until the execution-isolation slice passes acceptance. Raising the permission mode still never crosses the tenant boundary.'],
                            ['ask' => 'Usage is rejected as over the limit', 'answer' => 'A hard quota limit was reached. Quotas are metered per tenant and per identity; lowering one re-checks queued tasks before they run and rejects them, so either raise the quota or reduce usage.'],
                            ['ask' => 'Backup or restore fails', 'answer' => 'Restore requires the service to be stopped first (cow stop), otherwise it is refused. Backup archives contain API keys and personal data — treat them as sensitive files.'],
                            ['ask' => 'Port 9899 is taken and the service will not start', 'answer' => 'Find the process holding it: lsof -nP -i :9899 | grep LISTEN. Either stop that process or use another port via web_port in config.json or the COW_WEB_PORT environment variable.'],
                            ['ask' => 'The process refuses to start and the log mentions the identity mode', 'answer' => 'Check identity_mode in config.json. Only database is supported: explicitly configuring the old shared-password mode is refused at startup and never falls back to a default identity.'],
                        ],
                    ],
                ],
            ],

            // ===== Further reading (account and reference) =====
            'further' => [
                'nav'   => 'Further reading',
                'title' => 'Read the full documentation by topic',
                'goal'  => 'When you need the underlying design, parameters, or limits, start from the group below.',
                'blocks' => [
                    'docs' => [
                        'title' => 'All capability documents',
                        'note' => 'Every document is a localized offline copy; the site references no external websites.',
                    ],
                ],
            ],
        ],
    ],

    'footer' => [
        'copyright' => 'Great capacity through inclusiveness',
    ],
];
