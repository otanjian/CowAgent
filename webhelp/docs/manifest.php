<?php
declare(strict_types=1);

/**
 * 本地能力文档清单 —— **由 tools/build-docs.php 自动生成，请勿手改**。
 *
 * 每个条目：
 *   path    上游文档路径（仅作来源标注，页面不生成任何外链）
 *   section 分组键（侧栏分组用，文案见 lang/*.php 的 doc.sections.<key>）
 *   title   中/英标题（取自上游页面 data-page-title）
 *   lead    正文首段，用作文档页导语
 *   order   上/下一篇顺序
 */

return array (
  'docs' => 
  array (
    'architecture' => 
    array (
      'path' => '/zh/intro/architecture',
      'section' => 'intro',
      'title' => 
      array (
        'zh' => '项目架构',
        'en' => 'Architecture',
      ),
      'lead' => 'CowAgent 是一个开箱即用的超级 AI 助理，也是一个完整的 Agent harness 框架，具备复杂任务规划、长期记忆、技能扩展、自主进化和多智能体协作等能力。',
      'anchor_ok' => true,
      'order' => 0,
    ),
    'memory' => 
    array (
      'path' => '/zh/memory',
      'section' => 'memory',
      'title' => 
      array (
        'zh' => '长期记忆',
        'en' => 'Long-term Memory',
      ),
      'lead' => '长期记忆保存在工作空间文件中，跨会话持久存在。Agent 在对话中通过检索工具按需加载历史记忆，也会在上下文裁剪时自动将对话摘要写入长期记忆。',
      'anchor_ok' => true,
      'order' => 1,
    ),
    'knowledge' => 
    array (
      'path' => '/zh/knowledge',
      'section' => 'knowledge',
      'title' => 
      array (
        'zh' => '个人知识库',
        'en' => 'Personal Knowledge Base',
      ),
      'lead' => '个人知识库是 Agent 的长期结构化知识存储，保存在工作空间的 knowledge/ 目录下。与按时间线组织的记忆不同，知识库以主题为维度，将用户分享的文章、对话中的洞察、学习材料等整理为互相关联的 Markdown 页面，形成可持续增长的知识网络。',
      'anchor_ok' => true,
      'order' => 2,
    ),
    'skills' => 
    array (
      'path' => '/zh/skills/index',
      'section' => 'skills',
      'title' => 
      array (
        'zh' => '技能概览',
        'en' => 'Skills Overview',
      ),
      'lead' => '技能（Skill）为 Agent 提供无限的扩展性。每个 Skill 由说明文件（SKILL.md）、运行脚本（可选）、资源（可选）组成，描述如何完成特定类型的任务。',
      'anchor_ok' => true,
      'order' => 3,
    ),
    'tools' => 
    array (
      'path' => '/zh/tools',
      'section' => 'tools',
      'title' => 
      array (
        'zh' => '工具概览',
        'en' => 'Tools Overview',
      ),
      'lead' => '工具是 Agent 访问操作系统资源的核心能力。Agent 会根据任务需求智能选择和调用工具，完成文件操作、命令执行、联网搜索、定时任务等各类操作。工具实现在项目的 agent/tools/ 目录下。',
      'anchor_ok' => true,
      'order' => 4,
    ),
    'evolution' => 
    array (
      'path' => '/zh/memory/self-evolution',
      'section' => 'memory',
      'title' => 
      array (
        'zh' => '自主进化',
        'en' => 'Self-Evolution',
      ),
      'lead' => '自主进化（Self-Evolution）让 Agent 不止于”完成单次任务”，而是能在与你的相处中持续成长。在每段对话告一段落后，它会自动”回头复盘”一次：把使用中暴露的问题修进技能、把没做完的事情接着推进，并把值得记住的沉淀进记忆与知识库。久而久之，Agent 会越来越懂你的偏好、越来越少重复犯错、越来越主动…',
      'anchor_ok' => true,
      'order' => 5,
    ),
    'models' => 
    array (
      'path' => '/zh/models/index',
      'section' => 'models',
      'title' => 
      array (
        'zh' => '模型概览',
        'en' => 'Models Overview',
      ),
      'lead' => 'CowAgent 支持国内外主流厂商的大语言模型，模型接口实现在项目的 models/ 目录下。除文本对话外，部分厂商还提供视觉理解、图像生成、语音识别、语音合成、向量等能力，可在 Agent 流程中按需调用。',
      'anchor_ok' => true,
      'order' => 6,
    ),
    'channels' => 
    array (
      'path' => '/zh/channels/weixin',
      'section' => 'channels',
      'title' => 
      array (
        'zh' => '微信',
        'en' => 'WeChat',
      ),
      'lead' => '接入个人微信，扫码登录即可使用，支持文本、图片、语音、文件、视频等消息的私聊收发。通过微信官方API进行接入，无安全风险，接入后会在会话中新增一个机器人助手，不影响当前账号的使用。',
      'anchor_ok' => true,
      'order' => 7,
    ),
    'multiagent' => 
    array (
      'path' => '/zh/multi-agent/team',
      'section' => 'multi-agent',
      'title' => 
      array (
        'zh' => 'Agent 团队',
        'en' => 'Agent Teams',
      ),
      'lead' => 'CowAgent 不再只是一个 Agent，你可以创建多个 Agent 组成一支团队：每个成员有自己的职责、模型、技能和知识，既能各自独立工作，也能被拉进同一个会话里一起协作。',
      'anchor_ok' => true,
      'order' => 8,
    ),
    'channels-web' => 
    array (
      'path' => '/zh/channels/web',
      'section' => 'channels',
      'title' => 
      array (
        'zh' => 'Web 控制台',
        'en' => 'Web Console',
      ),
      'lead' => 'Web 控制台是 CowAgent 的默认通道，启动后会自动运行，通过浏览器即可与 Agent 对话，并支持在线管理模型、技能、记忆、通道等配置。',
      'anchor_ok' => true,
      'order' => 9,
    ),
    'cli-skill' => 
    array (
      'path' => '/zh/cli/skill',
      'section' => 'cli',
      'title' => 
      array (
        'zh' => '技能管理',
        'en' => 'Skill Management',
      ),
      'lead' => '技能管理命令用于安装、查询和管理 CowAgent 的技能。在对话中使用 /skill <子命令>，在终端中使用 cow skill <子命令>。',
      'anchor_ok' => true,
      'order' => 10,
    ),
    'memory-deep-dream' => 
    array (
      'path' => '/zh/memory/deep-dream',
      'section' => 'memory',
      'title' => 
      array (
        'zh' => '梦境蒸馏',
        'en' => 'Deep Dream',
      ),
      'lead' => '梦境蒸馏（Deep Dream）是 CowAgent 记忆系统的核心整理机制，负责将分散的天级记忆蒸馏为精炼的长期记忆，并生成梦境日记。',
      'anchor_ok' => true,
      'order' => 11,
    ),
    'models-claude' => 
    array (
      'path' => '/zh/models/claude',
      'section' => 'models',
      'title' => 
      array (
        'zh' => 'Claude',
        'en' => 'Claude',
      ),
      'lead' => 'Claude 由 Anthropic 提供，支持文本对话与图像理解，主流 Sonnet / Opus 模型均原生支持视觉，无需额外指定 Vision 模型。',
      'anchor_ok' => true,
      'order' => 12,
    ),
    'models-custom' => 
    array (
      'path' => '/zh/models/custom',
      'section' => 'models',
      'title' => 
      array (
        'zh' => '自定义',
        'en' => 'Custom',
      ),
      'lead' => '适用于通过 OpenAI 兼容协议接入的模型服务，例如：',
      'anchor_ok' => true,
      'order' => 13,
    ),
    'models-deepseek' => 
    array (
      'path' => '/zh/models/deepseek',
      'section' => 'models',
      'title' => 
      array (
        'zh' => 'DeepSeek',
        'en' => 'DeepSeek',
      ),
      'lead' => 'DeepSeek 是当前 Agent 模式默认推荐的厂商之一，主打高性价比的文本对话和任务规划能力。',
      'anchor_ok' => true,
      'order' => 14,
    ),
    'models-doubao' => 
    array (
      'path' => '/zh/models/doubao',
      'section' => 'models',
      'title' => 
      array (
        'zh' => '豆包 Doubao',
        'en' => 'Doubao',
      ),
      'lead' => '豆包（火山方舟）支持文本对话、图像理解、图像生成（Seedream）和向量能力，一份 ark_api_key 即可启用全部能力。',
      'anchor_ok' => true,
      'order' => 15,
    ),
    'models-gemini' => 
    array (
      'path' => '/zh/models/gemini',
      'section' => 'models',
      'title' => 
      array (
        'zh' => 'Gemini',
        'en' => 'Gemini',
      ),
      'lead' => 'Google Gemini 支持文本对话、图像理解和图像生成（Nano Banana 系列），一个 gemini_api_key 即可启用全部能力。',
      'anchor_ok' => true,
      'order' => 16,
    ),
    'models-glm' => 
    array (
      'path' => '/zh/models/glm',
      'section' => 'models',
      'title' => 
      array (
        'zh' => '智谱 GLM',
        'en' => 'GLM',
      ),
      'lead' => '智谱 AI 支持文本对话、图像理解、语音识别（ASR）和向量（Embedding），一份 zhipu_ai_api_key 即可启用全部能力。',
      'anchor_ok' => true,
      'order' => 17,
    ),
    'models-kimi' => 
    array (
      'path' => '/zh/models/kimi',
      'section' => 'models',
      'title' => 
      array (
        'zh' => 'Kimi',
        'en' => 'Kimi',
      ),
      'lead' => 'Kimi 由 Moonshot 提供，支持文本对话与图像理解，kimi-k3 与 kimi-k2.x 系列原生支持视觉。',
      'anchor_ok' => true,
      'order' => 18,
    ),
    'models-linkai' => 
    array (
      'path' => '/zh/models/linkai',
      'section' => 'models',
      'title' => 
      array (
        'zh' => 'LinkAI',
        'en' => 'LinkAI',
      ),
      'lead' => '通过一份 linkai_api_key 即可访问 OpenAI、Claude、Gemini、DeepSeek、MiniMax、Qwen、Kimi、豆包 等主流厂商的全部能力。',
      'anchor_ok' => true,
      'order' => 19,
    ),
    'models-mimo' => 
    array (
      'path' => '/zh/models/mimo',
      'section' => 'models',
      'title' => 
      array (
        'zh' => '小米 MiMo',
        'en' => 'MiMo',
      ),
      'lead' => '小米 MiMo 是原生全模态大模型，单 mimo_api_key 即可同时启用文本对话、图像理解与语音合成。',
      'anchor_ok' => true,
      'order' => 20,
    ),
    'models-minimax' => 
    array (
      'path' => '/zh/models/minimax',
      'section' => 'models',
      'title' => 
      array (
        'zh' => 'MiniMax',
        'en' => 'MiniMax',
      ),
      'lead' => 'MiniMax 支持文本对话、图像理解、图像生成与语音合成，一份 minimax_api_key 即可启用全部能力。',
      'anchor_ok' => true,
      'order' => 21,
    ),
    'models-openai' => 
    array (
      'path' => '/zh/models/openai',
      'section' => 'models',
      'title' => 
      array (
        'zh' => 'OpenAI',
        'en' => 'OpenAI',
      ),
      'lead' => 'OpenAI 是覆盖最完整的厂商，可同时承担文本对话、视觉理解、图像生成、语音识别（ASR）、语音合成（TTS）和向量（Embedding）能力。一份 open_ai_api_key 即可让 Agent 用到全部能力。',
      'anchor_ok' => true,
      'order' => 22,
    ),
    'models-qianfan' => 
    array (
      'path' => '/zh/models/qianfan',
      'section' => 'models',
      'title' => 
      array (
        'zh' => '百度千帆',
        'en' => 'ERNIE',
      ),
      'lead' => '百度千帆提供 ERNIE 系列模型，支持文本对话与图像理解。',
      'anchor_ok' => true,
      'order' => 23,
    ),
    'models-qwen' => 
    array (
      'path' => '/zh/models/qwen',
      'section' => 'models',
      'title' => 
      array (
        'zh' => '通义千问 Qwen',
        'en' => 'Qwen',
      ),
      'lead' => '通义千问（DashScope / 百炼）是国内覆盖最完整的厂商之一，文本、图像理解、图像生成、语音识别、语音合成与向量能力均可用一份 dashscope_api_key 启用。',
      'anchor_ok' => true,
      'order' => 24,
    ),
    'multi-agent-subagent' => 
    array (
      'path' => '/zh/multi-agent/subagent',
      'section' => 'multi-agent',
      'title' => 
      array (
        'zh' => '子 Agent',
        'en' => 'Sub-Agent',
      ),
      'lead' => '子 Agent 是主 Agent 在对话过程中临时创建的执行单元。主 Agent 把一个独立的任务交给它，它在自己的上下文中完成任务，然后返回结果。任务过程中打开的网页、读取的文件和执行的命令都不会进入主对话。',
      'anchor_ok' => true,
      'order' => 25,
    ),
    'skills-create' => 
    array (
      'path' => '/zh/skills/create',
      'section' => 'skills',
      'title' => 
      array (
        'zh' => '创造技能',
        'en' => 'Create Skills',
      ),
      'lead' => 'CowAgent 内置了 Skill Creator，可以通过自然语言对话快速创建、安装或更新技能。',
      'anchor_ok' => true,
      'order' => 26,
    ),
    'skills-install' => 
    array (
      'path' => '/zh/skills/install',
      'section' => 'skills',
      'title' => 
      array (
        'zh' => '安装技能',
        'en' => 'Install Skills',
      ),
      'lead' => 'CowAgent 支持通过统一的 install 命令安装来自 Cow 技能广场、GitHub、ClawHub、LinkAI 以及任意 URL 上的技能。在对话中使用 /skill install，在终端中使用 cow skill install。',
      'anchor_ok' => true,
      'order' => 27,
    ),
    'tools-delegate' => 
    array (
      'path' => '/zh/tools/delegate',
      'section' => 'tools',
      'title' => 
      array (
        'zh' => 'agent_delegate - 委派',
        'en' => 'agent_delegate - Delegation',
      ),
      'lead' => 'agent_delegate 让一个 Agent 把任务交给会话里的另一个 Agent 处理。被委派的是一个常驻的正式成员，它有自己的工作空间、记忆、技能和会话，在自己的环境里作答，结果回到发起方，不会直接发给用户。',
      'anchor_ok' => true,
      'order' => 28,
    ),
    'tools-scheduler' => 
    array (
      'path' => '/zh/tools/scheduler',
      'section' => 'tools',
      'title' => 
      array (
        'zh' => 'scheduler - 定时任务',
        'en' => 'scheduler - Scheduler',
      ),
      'lead' => '创建和管理动态定时任务，支持灵活的调度方式和执行模式。',
      'anchor_ok' => true,
      'order' => 29,
    ),
    'tools-subagent' => 
    array (
      'path' => '/zh/tools/subagent',
      'section' => 'tools',
      'title' => 
      array (
        'zh' => 'subagent - 子 Agent',
        'en' => 'subagent - Sub-Agent',
      ),
      'lead' => 'subagent 让主 Agent 在对话过程中临时创建执行单元，把一个独立任务交给它，在自己的上下文中完成后返回结果。功能效果和使用场景见子 Agent，本页介绍工具的类型、配置与实现细节。',
      'anchor_ok' => true,
      'order' => 30,
    ),
  ),
);
