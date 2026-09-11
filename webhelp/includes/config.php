<?php
declare(strict_types=1);

/**
 * 站点级配置：品牌、外链、导航、统计与部署命令。
 * 可翻译文案统一放在 lang/*.php，此处只保留结构与非翻译数据。
 */

return [
    // 品牌：容大AI（本仓库 branding/branding.json），并注明基于 CowAgent 构建
    'brand' => [
        'name'        => '容大AI',
        'name_en'     => 'RongAI',
        'logo_light'  => 'assets/img/rongda-ai-logo.svg',
        'logo_dark'   => 'assets/img/rongda-ai-logo-dark.svg',
        'favicon'     => 'assets/img/favicon.ico',
        'upstream_name' => 'CowAgent',
        'upstream_logo' => 'assets/img/cow-logo.png',
    ],

    'version' => '2.x',

    // 站点对外访问地址。部署到内网或正式环境时改成实际地址。
    // 部署命令中的 {site_url} 占位符会在渲染时替换为该值。
    'site_url' => 'http://YOUR-SITE-DOMAIN',

    // 演示视频（已本地化；上游英文版资源缺失，复用中文版）
    'demo_video' => [
        'zh' => 'assets/video/cow-demo-zh-v1.mp4',
        'en' => 'assets/video/cow-demo-zh-v1.mp4',
    ],

    // 导航：文件名 => 语言包键
    'nav' => [
        'index.php'      => 'nav.home',
        'features.php'   => 'nav.features',
        'enterprise.php' => 'nav.enterprise',
        'architecture.php' => 'nav.architecture',
        'about.php'      => 'nav.about',
    ],

    // 页面标题键
    'page_titles' => [
        'index.php'        => 'meta.title_home',
        'features.php'     => 'meta.title_features',
        'enterprise.php'   => 'meta.title_enterprise',
        'quickstart.php'   => 'meta.title_quickstart',
        'architecture.php' => 'meta.title_architecture',
        'about.php'        => 'meta.title_about',
    ],

    // 部署命令（脚本已本地化到 assets/deploy/，{site_url} 在渲染时替换）
    'deployments' => [
        'unix'   => "bash <(curl -fsSL {site_url}/assets/deploy/run.sh)",
        'win'    => 'irm {site_url}/assets/deploy/run.ps1 | iex',
        'docker' => "curl -O {site_url}/assets/deploy/docker-compose.yml\ndocker compose up -d",
    ],

    // 模型厂商与接入通道（上游一致的标签集合）
    'model_vendors' => ['OpenAI', 'Claude', 'Gemini', 'DeepSeek', 'MiniMax', 'GLM', 'Qwen', 'Kimi', 'Doubao', 'LinkAI'],

    'channels' => [
        'wechat', 'feishu', 'dingtalk', 'wecom_bot', 'qq',
        'wecom_app', 'official', 'telegram', 'slack', 'discord', 'web', 'terminal',
    ],

    // 配置文件示例（快速开始 / 企业部署）
    'config_sample' => "{\n"
        . "  \"model\": \"deepseek-v4-flash\",\n"
        . "  \"deepseek_api_key\": \"sk-...\",\n"
        . "  \"web_host\": \"127.0.0.1\",\n"
        . "  \"web_port\": 9899,\n"
        . "  \"identity_mode\": \"database\"\n"
        . "}",

    // 企业身份初始化命令（本仓库能力）
    'bootstrap_sample' => ".venv/bin/python -m cli.cli management bootstrap \\\n"
        . "  --tenant-code default \\\n"
        . "  --tenant-name \"默认租户\" \\\n"
        . "  --admin-username admin",
];
