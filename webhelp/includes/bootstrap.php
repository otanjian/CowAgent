<?php
declare(strict_types=1);

/**
 * 全站统一入口。每个页面第一行引入本文件即可获得：
 * 配置、语言、内容、图标与一组视图辅助函数。
 */

define('WEBHELP_ROOT', dirname(__DIR__));

require_once __DIR__ . '/config.php';
require_once __DIR__ . '/i18n.php';
require_once __DIR__ . '/content.php';
require_once __DIR__ . '/icons.php';

/** 站点配置访问：cfg('brand.name') */
function cfg(string $key, mixed $default = null): mixed
{
    static $config = null;
    if ($config === null) {
        $config = require __DIR__ . '/config.php';
    }

    $value = $config;
    foreach (explode('.', $key) as $segment) {
        if (!is_array($value) || !array_key_exists($segment, $value)) {
            return $default;
        }
        $value = $value[$segment];
    }

    return $value;
}

/** 结构化内容访问：content('capabilities') */
function content(string $key, mixed $default = []): mixed
{
    static $content = null;
    if ($content === null) {
        $content = require __DIR__ . '/content.php';
    }

    return array_key_exists($key, $content) ? $content[$key] : $default;
}

// 解析语言（可能写入 cookie，必须早于任何输出）
init_i18n();

if (!headers_sent()) {
    header('X-Content-Type-Options: nosniff');
    header('Referrer-Policy: strict-origin-when-cross-origin');
}

/** HTML 转义 */
function e(mixed $value): string
{
    return htmlspecialchars((string) $value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

/** 站点根路径（支持部署在子目录） */
function base_url(): string
{
    static $base = null;
    if ($base === null) {
        $dir  = str_replace('\\', '/', dirname($_SERVER['SCRIPT_NAME'] ?? '/index.php'));
        $base = ($dir === '/' || $dir === '.' || $dir === '\\') ? '' : rtrim($dir, '/');
    }

    return $base;
}

/** 生成站内链接，自动保留当前语言 */
function url(string $page = 'index.php', array $params = []): string
{
    if (current_lang() !== WEBHELP_LANG_DEFAULT && !isset($params['lang'])) {
        $params['lang'] = current_lang();
    }

    $query = $params ? '?' . http_build_query($params) : '';

    return base_url() . '/' . ltrim($page, '/') . $query;
}

/** 静态资源地址（带版本参数用于缓存失效） */
function asset(string $path): string
{
    return base_url() . '/' . ltrim($path, '/') . '?v=' . rawurlencode((string) cfg('version', '1'));
}

/** 站点对外绝对地址（用于部署命令等需要绝对 URL 的场景） */
function site_url(): string
{
    return rtrim((string) cfg('site_url', ''), '/');
}

/** 把命令里的 {site_url} 占位符替换为实际站点地址 */
function resolve_site_url(string $command): string
{
    return str_replace('{site_url}', site_url(), $command);
}

/** 当前页面文件名 */
function current_page(): string
{
    return basename((string) parse_url($_SERVER['REQUEST_URI'] ?? 'index.php', PHP_URL_PATH)) ?: 'index.php';
}

function is_current(string $page): bool
{
    return current_page() === $page;
}

/** 渲染一个 SVG 图标 */
function icon(string $name, string $class = 'icon'): string
{
    return render_icon($name, $class);
}

/** 首页 Hero 演示图地址（本地素材） */
function demo_image(): string
{
    $path = (string) cfg('demo_image', '');

    return $path === '' ? '' : asset($path);
}

/**
 * 上游风格的「单行命令」代码块（含注释行与提示符）。
 */
function code_snippet(string $comment, string $command, string $prompt = '$'): string
{
    $lines = explode("\n", $command);
    $body  = '';
    foreach ($lines as $index => $line) {
        $separator = $index > 0 ? "\n" : '';
        $body .= $separator . '<span class="code-prompt">' . e($prompt) . '</span> ' . e($line);
    }

    $html  = '<span class="code-comment">' . e('# ' . $comment) . '</span>' . "\n" . $body;

    return '<pre class="code-content"><code>' . $html . '</code></pre>';
}

/**
 * 上游风格的带标签页安装代码块（Linux/macOS、Windows、Docker、源码）。
 */
function deploy_block(): string
{
    $deployments = (array) content('deployments', []);
    $commands    = (array) cfg('deployments', []);
    $prompts     = ['unix' => '$', 'win' => '>', 'docker' => '$', 'source' => '$'];

    $tabs   = '';
    $panels = '';

    foreach ($deployments as $index => $item) {
        $id      = (string) $item['id'];
        $label   = t('quickstart.tab_' . $id);
        $comment = t('quickstart.comment_' . $id);
        $command = resolve_site_url((string) ($commands[$id] ?? ''));
        $prompt  = $prompts[$id] ?? '$';
        $active  = $index === 0;

        $tabs .= '<button class="code-tab' . ($active ? ' is-active' : '') . '" type="button"'
            . ' data-code-tab="' . e($id) . '">' . e($label) . '</button>';

        $lines = explode("\n", $command);
        $body  = '';
        foreach ($lines as $lineIndex => $line) {
            $body .= ($lineIndex > 0 ? "\n" : '')
                . '<span class="code-prompt">' . e($prompt) . '</span> ' . e($line);
        }

        $panels .= '<pre class="code-content' . ($active ? ' is-active' : '') . '" data-code-panel="' . e($id) . '"><code>'
            . '<span class="code-comment">' . e('# ' . $comment) . '</span>' . "\n" . $body
            . '</code></pre>';
    }

    return '<div class="code-block">'
        . '<div class="code-header">'
        . '<span class="code-dot red"></span><span class="code-dot yellow"></span><span class="code-dot green"></span>'
        . '<div class="code-tabs">' . $tabs . '</div>'
        . '<button class="copy-btn" type="button" data-copy-label="' . e(t('common.copy')) . '"'
        . ' data-copy-done="' . e(t('common.copied')) . '">' . e(t('common.copy')) . '</button>'
        . '</div>'
        . $panels
        . '</div>';
}

/** 带复制按钮的通用代码块 */
function code_block(string $label, string $code): string
{
    return '<div class="code-block code-block--wide">'
        . '<div class="code-header">'
        . '<span class="code-dot red"></span><span class="code-dot yellow"></span><span class="code-dot green"></span>'
        . '<span class="code-label">' . e($label) . '</span>'
        . '<button class="copy-btn" type="button" data-copy-label="' . e(t('common.copy')) . '"'
        . ' data-copy-done="' . e(t('common.copied')) . '">' . e(t('common.copy')) . '</button>'
        . '</div>'
        . '<pre class="code-content is-active"><code>' . e($code) . '</code></pre>'
        . '</div>';
}

/** 核心能力卡片：点击进入对应的本地文档（不产生任何外链） */
function capability_card(array $item, string $extraClass = ''): string
{
    $id  = (string) $item['id'];
    $doc = (string) ($item['doc'] ?? '');
    $cls = 'feature-card feature-card--link reveal' . ($extraClass !== '' ? ' ' . $extraClass : '');

    $inner = '<span class="feature-icon">' . icon((string) $item['icon']) . '</span>'
        . '<h3 class="feature-title">' . e(t('capabilities.items.' . $id . '.title')) . '</h3>'
        . '<p class="feature-desc">' . e(t('capabilities.items.' . $id . '.desc')) . '</p>';

    if ($doc === '') {
        return '<article class="feature-card reveal' . ($extraClass !== '' ? ' ' . $extraClass : '') . '">' . $inner . '</article>';
    }

    return '<a class="' . e($cls) . '" href="' . e(doc_url($doc)) . '">'
        . $inner
        . '<span class="feature-more">' . e(t('common.read_doc')) . icon('arrow') . '</span>'
        . '</a>';
}

/** 企业级管控卡片（含要点列表，不可点击） */
function enterprise_card(array $item): string
{
    $id     = (string) $item['id'];
    $points = t_list('enterprise.items.' . $id . '.points');

    $list = '';
    if ($points !== []) {
        $list .= '<ul class="feature-points">';
        foreach ($points as $point) {
            $list .= '<li>' . icon('check') . '<span>' . e($point) . '</span></li>';
        }
        $list .= '</ul>';
    }

    return '<article class="feature-card enterprise-card reveal">'
        . '<span class="feature-icon">' . icon((string) $item['icon']) . '</span>'
        . '<h3 class="feature-title">' . e(t('enterprise.items.' . $id . '.title')) . '</h3>'
        . '<p class="feature-desc">' . e(t('enterprise.items.' . $id . '.desc')) . '</p>'
        . $list
        . '</article>';
}

/** 模型 / 通道标签 */
function tag(string $label, string $modifier = ''): string
{
    return '<span class="tag' . ($modifier !== '' ? ' tag--' . $modifier : '') . '">' . e($label) . '</span>';
}

// ---------------------------------------------------------------------------
// 企业级权限管控区块（enterprise.php）
// 概要（眉标 + 标题 + 导语 + 亮点）提升为页头，见 enterprise.php 的 page_hero() 调用
// ---------------------------------------------------------------------------

/** 概览亮点：4 张数字卡，用于页头导语下方 */
function perm_stats(): string
{
    $out = '';
    foreach (t_list('perm.stats') as $stat) {
        $out .= '<div class="perm-stat reveal">'
            . '<strong>' . e((string) ($stat['value'] ?? '')) . '</strong>'
            . '<span>' . e((string) ($stat['label'] ?? '')) . '</span>'
            . '</div>';
    }

    return '<div class="perm-stats">' . $out . '</div>';
}

/** 三级权限架构：三张并列卡，桌面端由渐变连接线串联 */
function perm_tiers(): string
{
    $out = '';
    foreach ((array) content('perm_tiers', []) as $item) {
        $id     = (string) $item['id'];
        $points = '';
        foreach (t_list('perm.tiers.' . $id . '.actions') as $action) {
            $points .= '<li>' . e($action) . '</li>';
        }

        $out .= '<article class="perm-tier perm-tier--' . $id . ' reveal">'
            // 压在卡片上边框的层级徽章
            . '<span class="perm-tier-badge">' . e(t('perm.tiers.' . $id . '.name')) . '</span>'
            . '<span class="perm-tier-icon">' . icon((string) $item['icon']) . '</span>'
            . '<h3 class="perm-tier-actor">' . e(t('perm.tiers.' . $id . '.actor')) . '</h4>'
            . '<p class="perm-tier-actordesc">' . e(t('perm.tiers.' . $id . '.actor_desc')) . '</p>'
            . '<div class="perm-tier-field">'
            . '<span class="perm-tier-label">' . e(t('perm.labels.scope')) . '</span>'
            . '<p class="perm-tier-scope">' . e(t('perm.tiers.' . $id . '.scope')) . '</p>'
            . '</div>'
            . '<div class="perm-tier-field">'
            . '<span class="perm-tier-label">' . e(t('perm.labels.actions')) . '</span>'
            . '<ul class="perm-tier-actions">' . $points . '</ul>'
            . '</div>'
            . '</article>';
    }

    return '<div class="perm-tiers">' . $out . '</div>';
}

/**
 * 资源授权流转面板：深色底 + 逐级授权箭头，底部为个人资源分享回流。
 * 平台管理员 →〔授权平台级资源〕→ 租户管理员 ↓〔授权本租户资源〕 用户
 */
function perm_flow_panel(): string
{
    $g        = t_map('perm.flow_graph');
    $desc     = (array) ($g['actor_desc'] ?? []);
    $icons    = ['platform' => 'tenant', 'tenant' => 'rbac', 'user' => 'access'];

    $node = static function (string $tier) use ($desc, $icons): string {
        return '<div class="perm-node perm-node--' . $tier . '">'
            . '<span class="perm-node-icon">' . icon($icons[$tier] ?? 'users') . '</span>'
            . '<span class="perm-node-name">' . e(t('perm.tiers.' . $tier . '.actor')) . '</span>'
            . '<span class="perm-node-desc">' . e((string) ($desc[$tier] ?? '')) . '</span>'
            . '</div>';
    };

    return '<div class="perm-flow reveal">'
        . '<div class="perm-flow-grid">'
        . $node('platform')
        . '<div class="perm-arrow perm-arrow--right">'
        . '<span class="perm-arrow-label">' . e((string) ($g['grant_platform'] ?? '')) . '</span>'
        . '<span class="perm-arrow-line" aria-hidden="true"></span>'
        . '</div>'
        . $node('tenant')
        . '<div class="perm-arrow perm-arrow--down">'
        . '<span class="perm-arrow-label">' . e((string) ($g['grant_tenant'] ?? '')) . '</span>'
        . '<span class="perm-arrow-line" aria-hidden="true"></span>'
        . '</div>'
        . $node('user')
        . '</div>'
        // 反向回流：个人资源分享回租户，再由租户管理员二次授权
        . '<div class="perm-return">'
        . '<span class="perm-return-text">' . e((string) ($g['share_out'] ?? '')) . '</span>'
        . '<span class="perm-return-arrow" aria-hidden="true"></span>'
        . '<span class="perm-return-text">' . e((string) ($g['share_in'] ?? '')) . '</span>'
        . '</div>'
        . '</div>';
}

/** 平台角色与职责：每张卡为「头部 + 三列短要点」 */
function perm_roles(): string
{
    $out = '';
    foreach ((array) content('perm_roles', []) as $item) {
        $id     = (string) $item['id'];
        $groups = '';

        foreach (t_list('perm.roles.' . $id . '.groups') as $group) {
            $entries = '';
            foreach ((array) ($group['items'] ?? []) as $entry) {
                $entries .= '<li>' . e($entry) . '</li>';
            }
            $groups .= '<div class="perm-role-group">'
                . '<h4 class="perm-role-grouptitle">' . e((string) ($group['title'] ?? '')) . '</h5>'
                . '<ul class="perm-role-list">' . $entries . '</ul>'
                . '</div>';
        }

        $out .= '<article class="perm-role perm-role--' . $id . ' reveal">'
            . '<header class="perm-role-head">'
            . '<span class="perm-role-icon">' . icon((string) $item['icon']) . '</span>'
            . '<div class="perm-role-id">'
            . '<h3 class="perm-role-name">' . e(t('perm.roles.' . $id . '.name')) . '</h4>'
            . '<p class="perm-role-desc">' . e(t('perm.roles.' . $id . '.desc')) . '</p>'
            . '</div>'
            . '<span class="perm-role-tag">' . e(t('perm.roles.' . $id . '.tag')) . '</span>'
            . '</header>'
            . '<div class="perm-role-groups">' . $groups . '</div>'
            . '</article>';
    }

    return '<div class="perm-roles">' . $out . '</div>';
}

// ---------------------------------------------------------------------------
// 本地能力文档（正文与清单由 tools/build-docs.php 生成，见 docs/ 目录）
// ---------------------------------------------------------------------------

/** 文档清单，按上游顺序排列；清单缺失时返回空数组（例如尚未执行构建） */
function doc_registry(): array
{
    static $registry = null;
    if ($registry === null) {
        $registry = [];
        $file     = WEBHELP_ROOT . '/docs/manifest.php';
        if (is_file($file)) {
            $manifest = require $file;
            foreach ((array) ($manifest['docs'] ?? []) as $slug => $meta) {
                $registry[(string) $slug] = (array) $meta;
            }
            uasort($registry, static fn(array $a, array $b) => ($a['order'] ?? 0) <=> ($b['order'] ?? 0));
        }
    }

    return $registry;
}

/** 文档按分组归并，保持清单顺序：section => [slug => meta] */
function doc_grouped(): array
{
    $grouped = [];
    foreach (doc_registry() as $slug => $meta) {
        $grouped[(string) ($meta['section'] ?? 'intro')][$slug] = $meta;
    }

    return $grouped;
}

/** 文档是否存在（同时防止路径穿越：只认清单里登记过的 slug） */
function doc_exists(string $slug): bool
{
    return $slug !== '' && isset(doc_registry()[$slug]);
}

/** 文档标题：按当前语言取清单里的标题 */
function doc_title(string $slug): string
{
    if (!doc_exists($slug)) {
        return '';
    }
    $titles = (array) (doc_registry()[$slug]['title'] ?? []);
    $lang   = current_lang();

    return (string) ($titles[$lang] ?? $titles['zh'] ?? $slug);
}

/** 文档导语：正文首段（构建时抽取，与站点语言无关——正文本身仅中文） */
function doc_lead(string $slug): string
{
    return doc_exists($slug) ? (string) (doc_registry()[$slug]['lead'] ?? '') : '';
}

/** 能力卡片对应的图标；未登记文档的能力卡片用默认图标 */
function doc_icon(string $slug): string
{
    foreach ((array) content('capabilities', []) as $item) {
        if (($item['doc'] ?? '') === $slug) {
            return (string) $item['icon'];
        }
    }

    return 'book';
}

/** 文档页地址 */
function doc_url(string $slug): string
{
    return url('doc.php', ['p' => $slug]);
}

/** 上一篇 / 下一篇文档 slug（按清单顺序） */
function doc_neighbor(string $slug, int $offset): string
{
    $ids = array_keys(doc_registry());
    $i   = array_search($slug, $ids, true);
    if ($i === false) {
        return '';
    }
    $target = $ids[$i + $offset] ?? '';

    return is_string($target) ? $target : '';
}

/**
 * 文档正文 HTML。
 * 正文在构建阶段已净化：不含脚本、样式与任何站外引用；
 * 仅把相对的 assets/ 前缀补成带 base_url 的绝对站内路径。
 */
function doc_body(string $slug): string
{
    if (!doc_exists($slug)) {
        return '';
    }
    $file = WEBHELP_ROOT . '/docs/' . $slug . '.html';
    if (!is_file($file)) {
        return '';
    }

    $html = (string) file_get_contents($file);
    $base = base_url();
    if ($base !== '') {
        $html = str_replace('src="assets/', 'src="' . $base . '/assets/', $html);
    }

    // 宽表格在窄屏下横向滚动（片段由构建脚本生成，<table> 不带属性）
    $opens  = substr_count($html, '<table>');
    $closes = substr_count($html, '</table>');
    if ($opens > 0 && $opens === $closes) {
        $html = str_replace('<table>', '<div class="doc-table-wrap"><table>', $html);
        $html = str_replace('</table>', '</table></div>', $html);
    }

    return $html;
}

/** 从正文中抽取目录项（h2 / h3） */
function doc_toc(string $html): array
{
    if (trim($html) === '') {
        return [];
    }

    $dom = new DOMDocument();
    libxml_use_internal_errors(true);
    $dom->loadHTML('<?xml encoding="UTF-8"><div id="toc-root">' . $html . '</div>');
    libxml_clear_errors();

    $items = [];
    foreach ((new DOMXPath($dom))->query('//*[@id="toc-root"]//h2 | //*[@id="toc-root"]//h3') as $heading) {
        $id   = $heading->getAttribute('id');
        $text = trim((string) preg_replace('/\s+/u', ' ', $heading->textContent));
        if ($id === '' || $text === '') {
            continue;
        }
        $items[] = ['level' => (int) substr($heading->nodeName, 1), 'id' => $id, 'text' => $text];
    }

    return $items;
}

/** 内页页头 */
/**
 * 页头标题 HTML。
 *
 * 支持两种排版能力：
 *   - 文案里的换行符渲染为 <br>，用于标语式的两行标题；
 *   - $accentKey 指定一个语言键，取到的子串在标题中首次出现处包一层
 *     .hero-accent（品牌绿渐变），用于突出标语里的关键词。
 *     找不到子串时整段按普通文本渲染，不会报错。
 */
function hero_title_html(string $titleKey, string $accentKey = ''): string
{
    $title  = t($titleKey);
    $accent = $accentKey === '' ? '' : t($accentKey);

    $html = '';
    foreach (explode("\n", $title) as $index => $line) {
        if ($index > 0) {
            $html .= '<br>';
        }

        $pos = $accent === '' ? false : mb_strpos($line, $accent);
        if ($pos === false) {
            $html .= e($line);
            continue;
        }

        $html .= e(mb_substr($line, 0, $pos))
            . '<span class="hero-accent">' . e($accent) . '</span>'
            . e(mb_substr($line, $pos + mb_strlen($accent)));
    }

    return $html;
}

/**
 * 内页页头。
 *
 * 基础用法（5 个内页均如此）：page_hero('meta.title_x', 'x.subtitle')
 *
 * $opts 可选：
 *   breadcrumb   面包屑末项文案键，默认与标题一致；传空字符串则不渲染面包屑
 *   eyebrow      眉标文案键（如「核心功能一」），留空不渲染
 *   eyebrow_icon 眉标图标，默认 sparkles
 *   title_accent 标题中要高亮的关键词（语言键），见 hero_title_html()
 *   extra        追加在导语下方的 HTML，调用方自行拼装（如概览数字卡）
 */
function page_hero(string $titleKey, string $leadKey, array $opts = []): void
{
    $breadcrumbKey = (string) ($opts['breadcrumb'] ?? $titleKey);
    $eyebrowKey    = (string) ($opts['eyebrow'] ?? '');
    $eyebrowIcon   = (string) ($opts['eyebrow_icon'] ?? 'sparkles');
    $accentKey     = (string) ($opts['title_accent'] ?? '');
    $extraHtml     = (string) ($opts['extra'] ?? '');
    ?>
    <header class="page-hero<?= $extraHtml !== '' ? ' page-hero--rich' : '' ?>">
      <div class="section-container">
        <?php if ($breadcrumbKey !== ''): ?>
          <nav class="breadcrumb" aria-label="breadcrumb">
            <a href="<?= e(url('index.php')) ?>"><?= e(t('nav.home')) ?></a>
            <span aria-hidden="true">/</span>
            <span><?= e(t($breadcrumbKey)) ?></span>
          </nav>
        <?php endif; ?>
        <?php if ($eyebrowKey !== ''): ?>
          <span class="perm-eyebrow"><?= icon($eyebrowIcon) ?><?= e(t($eyebrowKey)) ?></span>
        <?php endif; ?>
        <h1><?= hero_title_html($titleKey, $accentKey) ?></h1>
        <p><?= e(t($leadKey)) ?></p>
        <?= $extraHtml ?>
      </div>
    </header>
    <?php
}

/** 区块标题 */
function section_heading(string $titleKey, string $subtitleKey = '', bool $left = false): void
{
    $class = $left ? ' section-title--left' : '';
    ?>
    <h2 class="section-title<?= $class ?>"><?= e(t($titleKey)) ?></h2>
    <?php if ($subtitleKey !== ''): ?>
      <p class="section-subtitle<?= $left ? ' section-subtitle--left' : '' ?>"><?= e(t($subtitleKey)) ?></p>
    <?php endif; ?>
    <?php
}

// ---------------------------------------------------------------------------
// 产品使用手册（manual.php）
// 形态是「截图为主的步骤手册」：主题（一句话定位 + 若干编号步骤，每步一张真实界面截图）。
// 结构见 content.php 的 manual_parts / manual_topics，文案见 lang/*.php 的 manual.*
// ---------------------------------------------------------------------------

/** 手册主题导航：按手册分组渲染（桌面端 sticky 侧栏，窄屏由 .doc-layout 退化） */
function manual_nav(): string
{
    $topics = (array) content('manual_topics', []);
    $byPart = [];
    foreach ($topics as $index => $item) {
        $byPart[(string) ($item['part'] ?? 'conversation')][] = [$index, $item];
    }

    $out = '<p class="doc-aside-title">' . e(t('manual.toc')) . '</p>';
    foreach ((array) content('manual_parts', []) as $part) {
        $id = (string) $part['id'];
        if (!isset($byPart[$id])) {
            continue;
        }

        $out .= '<p class="doc-aside-section">' . e(t('manual.parts.' . $id)) . '</p>'
            . '<ul class="doc-aside-list">';
        foreach ($byPart[$id] as [$index, $item]) {
            $tid = (string) $item['id'];
            $out .= '<li>'
                . '<a class="doc-aside-link" href="#manual-' . e($tid) . '">'
                . e((string) ($index + 1)) . '. ' . e(t('manual.topics.' . $tid . '.nav'))
                . '</a></li>';
        }
        $out .= '</ul>';
    }

    return $out;
}

/**
 * 一个编号步骤：序号 + 目标 + 操作要点 + 真实界面截图。
 * 步骤 id 与截图路径由 content.php 声明，文案按 manual.topics.<主题>.steps.<步骤>.{title,body} 取。
 * 截图是 `assets/` 下的相对路径，经 asset() 输出；缺图会渲染成破图，因此由
 * tools/check-manual.php 在提交前拦下。
 * 截图本身是链接，点击在新标签打开原图——手册页面按阅读宽度缩放，细节控件要靠原图看清。
 */
function manual_step(string $topicId, int $number, array $step): string
{
    $stepId = (string) ($step['id'] ?? '');
    $key    = 'manual.topics.' . $topicId . '.steps.' . $stepId;
    $title  = trim(t_opt($key . '.title'));
    $body   = trim(t_opt($key . '.body'));
    $shot   = trim((string) ($step['shot'] ?? ''));

    $out = '<figure class="manual-step" id="manual-' . e($topicId) . '-' . e($stepId) . '">'
        . '<figcaption class="manual-step-head">'
        . '<span class="manual-step-no" aria-hidden="true">' . e((string) $number) . '</span>'
        . '<span class="manual-step-copy">'
        . '<span class="manual-step-title">' . e($title) . '</span>'
        . '<span class="manual-step-body">' . e($body) . '</span>'
        . '</span></figcaption>';

    if ($shot !== '') {
        $out .= '<a class="manual-shot" href="' . e(asset($shot)) . '" target="_blank" rel="noopener">'
            . '<img class="manual-shot-img" src="' . e(asset($shot)) . '"'
            . ' alt="' . e($title) . '" loading="lazy" decoding="async">'
            . '</a>';
    }

    return $out . '</figure>';
}

/**
 * 章节末尾的深链：既有能力文档（docs/manifest.php 登记过的 slug）+ 既有站内页面。
 * slug 未登记或页面 id 未映射时静默跳过，因此文档增删不会产生死链。
 */
function manual_refs(array $item): string
{
    $groups = [
        'manual.docs_label'  => (array) ($item['docs'] ?? []),
        'manual.pages_label' => (array) ($item['links'] ?? []),
    ];
    $pageLinks = (array) content('manual_page_links', []);

    $out = '';
    foreach ($groups as $labelKey => $refs) {
        $links = '';

        foreach ($refs as $ref) {
            $ref = (string) $ref;
            if ($labelKey === 'manual.docs_label') {
                if (!doc_exists($ref)) {
                    continue;
                }
                $href  = doc_url($ref);
                $label = doc_title($ref);
            } else {
                if (!isset($pageLinks[$ref])) {
                    continue;
                }
                $href  = url((string) $pageLinks[$ref]);
                $label = t('manual.page_links.' . $ref, $ref);
            }

            $links .= '<a class="manual-ref" href="' . e($href) . '">' . e($label) . icon('arrow') . '</a>';
        }

        if ($links === '') {
            continue;
        }

        $out .= '<div class="manual-refs">'
            . '<span class="manual-refs-label">' . e(t($labelKey)) . '</span>'
            . '<div class="manual-refs-list">' . $links . '</div>'
            . '</div>';
    }

    return $out;
}
