<?php
declare(strict_types=1);

/**
 * 文档本地化构建脚本（开发期工具，**不属于站点运行时**）。
 *
 * 用途：从上游 CowAgent 官方中文文档抓取「9 篇能力文档 + 其正文引用到的全部页面」，
 *       抽取正文、净化掉全部站外引用与装饰标记，并把图片转存到本地。
 *
 * 产出：
 *   docs/<slug>.html            净化后的正文片段（由 doc.php 渲染）
 *   docs/manifest.php           文档清单（slug / 分组 / 顺序 / 中英标题 / 来源路径）
 *   assets/img/docs/*.{png,jpg} 正文配图（已缩到最大宽 1600px）
 *
 * 说明：站点运行时（任何 .php 页面）不会访问外网；本脚本是唯一的联网组件，
 *       仅在需要同步上游文档更新时手动执行。
 *
 * 用法：
 *   php tools/build-docs.php
 */

if (PHP_SAPI !== 'cli') {
    http_response_code(404);
    exit("This script is CLI-only.\n");
}

$root = dirname(__DIR__);

/**
 * 抓取入口：9 篇能力文档。docs.php 的能力卡片即指向这 9 个 slug。
 * 其余页面由正文链接 BFS 发现（见下面显式映射表）。
 */
$roots = [
    '/zh/intro/architecture',
    '/zh/memory',
    '/zh/knowledge',
    '/zh/skills/index',
    '/zh/tools',
    '/zh/memory/self-evolution',
    '/zh/models/index',
    '/zh/channels/weixin',
    '/zh/multi-agent/team',
];

/**
 * 上游路径 => 本地 slug（显式维护，保证 URL 稳定）。
 * 发现阶段若出现表中没有的新路径，会派生一个兜底 slug 并给出告警，
 * 提示把它补进本表。
 */
$slugs = [
    // ---- 9 篇能力文档（与 content.php 的 capabilities[].doc 一一对应）----
    '/zh/intro/architecture'     => 'architecture',
    '/zh/memory'                 => 'memory',
    '/zh/knowledge'              => 'knowledge',
    '/zh/skills/index'           => 'skills',
    '/zh/tools'                  => 'tools',
    '/zh/memory/self-evolution'  => 'evolution',
    '/zh/models/index'           => 'models',
    '/zh/channels/weixin'        => 'channels',
    '/zh/multi-agent/team'       => 'multiagent',
    // ---- 正文引用到的其余页面 ----
    '/zh/memory/deep-dream'      => 'memory-deep-dream',
    '/zh/skills/install'         => 'skills-install',
    '/zh/skills/create'          => 'skills-create',
    '/zh/cli/skill'              => 'cli-skill',
    '/zh/models/deepseek'        => 'models-deepseek',
    '/zh/models/claude'          => 'models-claude',
    '/zh/models/openai'          => 'models-openai',
    '/zh/models/gemini'          => 'models-gemini',
    '/zh/models/minimax'         => 'models-minimax',
    '/zh/models/glm'             => 'models-glm',
    '/zh/models/qwen'            => 'models-qwen',
    '/zh/models/kimi'            => 'models-kimi',
    '/zh/models/doubao'          => 'models-doubao',
    '/zh/models/qianfan'         => 'models-qianfan',
    '/zh/models/mimo'            => 'models-mimo',
    '/zh/models/linkai'          => 'models-linkai',
    '/zh/models/custom'          => 'models-custom',
    '/zh/channels/web'           => 'channels-web',
    '/zh/tools/delegate'         => 'tools-delegate',
    '/zh/tools/scheduler'        => 'tools-scheduler',
    '/zh/tools/subagent'         => 'tools-subagent',
    '/zh/multi-agent/subagent'   => 'multi-agent-subagent',
];

/**
 * 上游死链别名：正文里有链接指向这些路径，但上游已 404。
 * 映射到仍然存活的等价页面，避免这些链接被降级成纯文本。
 */
$aliases = [
    '/zh/blog/self-evolution' => '/zh/memory/self-evolution',
];

/** 白名单标签与保留属性（其余一律剥离或解包） */
$ALLOWED = ['h2','h3','h4','p','ul','ol','li','pre','code','table','thead','tbody',
            'tr','th','td','blockquote','strong','em','b','i','hr','br','img','a'];
$KEEP_ATTR = ['a' => ['href'], 'img' => ['src','alt'], 'th' => ['colspan','rowspan'],
              'td' => ['colspan','rowspan'], 'h2' => ['id'], 'h3' => ['id'], 'h4' => ['id']];

/** 图片最大宽度（约 2x 显示宽度，兼顾清晰度与体积） */
const IMG_MAX_WIDTH = 1600;
/** 正文容器 XPath（Mintlify 站点） */
const CONTENT_XPATH = '//*[@id="content"]';
/** BFS 边界，防止上游结构变化导致无限扩张 */
const MAX_PAGES = 120;
const MAX_DEPTH = 4;

$IMG_DIR = $root . '/assets/img/docs';
@mkdir($root . '/docs', 0777, true);
@mkdir($IMG_DIR, 0777, true);

/*
 * 刻意不在开头清空 assets/img/docs：
 * 图片文件名由「slug + 源地址 md5」决定，是稳定的；保留已有文件可让
 * 重新构建时直接复用，避免一次网络抖动就把正文里的图片抹掉。
 * 未被引用的旧图会在构建成功后统一清理（见文件末尾）。
 */

$stats    = ['icon' => 0, 'anchor' => 0, 'local' => 0, 'unwrapped' => 0];
$imgStats = ['ok' => 0, 'fail' => 0];
$warnings = [];

// ---------------------------------------------------------------------------
// 网络与图片
// ---------------------------------------------------------------------------

/**
 * 用 curl 抓取（PHP 的 https 流对 cdn.jsdelivr.net 会 TLS 握手失败）。
 * $status 回传 HTTP 状态码（网络层失败为 0）；对 5xx / 超时自动重试，
 * 对 404 / 410 立即返回（页面本来就不存在，重试无意义）。
 */
function fetch(string $url, int $timeout = 60, ?int &$status = null, int $attempts = 3): ?string
{
    $status = 0;
    for ($i = 1; $i <= $attempts; $i++) {
        $tmp = tempnam(sys_get_temp_dir(), 'fetch');
        $cmd = 'curl -sL --max-time ' . $timeout . ' -A ' . escapeshellarg('Mozilla/5.0')
            . ' -o ' . escapeshellarg($tmp) . ' -w %{http_code} ' . escapeshellarg($url) . ' 2>/dev/null';
        $out = [];
        exec($cmd, $out, $rc);
        $code   = trim(implode('', $out));
        $status = ctype_digit($code) ? (int) $code : 0;

        if ($rc === 0 && $status === 200 && is_file($tmp) && filesize($tmp) > 0) {
            $data = (string) file_get_contents($tmp);
            @unlink($tmp);

            return $data;
        }
        @unlink($tmp);

        if ($status === 404 || $status === 410) {
            return null;
        }
        if ($i < $attempts) {
            sleep(2);
        }
    }

    return null;
}

/** 同一资源在 jsdelivr 各镜像上的等价地址（cdn 主域偶发 301 / 超时） */
function image_mirrors(string $url): array
{
    $urls = [$url];
    foreach (['gcore.jsdelivr.net', 'testingcf.jsdelivr.net'] as $host) {
        $mirror = preg_replace('#^https://cdn\.jsdelivr\.net/#', "https://{$host}/", $url);
        if (is_string($mirror) && $mirror !== $url) {
            $urls[] = $mirror;
        }
    }

    return $urls;
}

/** 单次下载尝试，成功返回 true（同时校验落盘结果，避免把写失败当成功） */
function download(string $url, string $dest, int $timeout): bool
{
    $tmp = tempnam(sys_get_temp_dir(), 'img');
    $cmd = 'curl -sL --max-time ' . $timeout . ' -A ' . escapeshellarg('Mozilla/5.0')
        . ' -o ' . escapeshellarg($tmp) . ' -w %{http_code} ' . escapeshellarg($url) . ' 2>/dev/null';
    exec($cmd, $out, $rc);

    $ok = $rc === 0 && trim(implode('', $out)) === '200' && is_file($tmp) && filesize($tmp) >= 100;
    if ($ok && !@copy($tmp, $dest)) {
        $ok = false;   // 目标不可写等情况：必须如实报告失败
    }
    if ($ok && (!is_file($dest) || filesize($dest) < 100)) {
        $ok = false;
    }
    if (!$ok) {
        @unlink($dest);
    }
    @unlink($tmp);

    return $ok;
}

/**
 * 下载图片（含镜像回退）并用 sips 缩放。
 * sips 为 macOS 内置；缺失时只下载不缩放，不影响构建结果。
 */
function grab_image(string $url, string $dest): bool
{
    $ok = false;
    foreach (image_mirrors($url) as $tryUrl) {
        if (download($tryUrl, $dest, 30)) {
            $ok = true;
            break;
        }
    }
    if (!$ok) {
        return false;
    }

    if (trim((string) shell_exec('command -v sips 2>/dev/null')) === '') {
        return true;
    }
    $width = 0;
    exec('sips -g pixelWidth ' . escapeshellarg($dest) . ' 2>/dev/null', $wo);
    foreach ($wo as $line) {
        if (preg_match('/pixelWidth:\s*(\d+)/', $line, $m)) {
            $width = (int) $m[1];
        }
    }
    if ($width > IMG_MAX_WIDTH) {
        exec('sips --resampleWidth ' . IMG_MAX_WIDTH . ' ' . escapeshellarg($dest) . ' >/dev/null 2>&1');
    }

    return true;
}

// ---------------------------------------------------------------------------
// 发现阶段：从 9 个根出发 BFS，得到全部需要本地化的路径
// ---------------------------------------------------------------------------

/** 载入 HTML 并返回 XPath + 正文节点 */
function load_content(string $html, string $xpath = CONTENT_XPATH): ?DOMElement
{
    $dom = new DOMDocument();
    libxml_use_internal_errors(true);
    $dom->loadHTML('<?xml encoding="UTF-8">' . $html);
    libxml_clear_errors();
    $node = (new DOMXPath($dom))->query($xpath)->item(0);

    return $node instanceof DOMElement ? $node : null;
}

/** 取正文里的站内文档链接（仅 /zh/ 路径） */
function links_in(DOMElement $content): array
{
    $xp  = new DOMXPath($content->ownerDocument);
    $out = [];
    foreach ($xp->query('.//a[@href]', $content) as $a) {
        $path = parse_url($a->getAttribute('href'), PHP_URL_PATH) ?? '';
        if ($path === '' || !str_starts_with($path, '/zh/') || str_ends_with($path, '.md')) {
            continue;
        }
        $path = rtrim($path, '/');
        if ($path !== '') {
            $out[$path] = true;
        }
    }

    return array_keys($out);
}

echo "=== 发现阶段 ===\n";
$discovered = [];
$dead       = [];   // 上游已 404 的路径（死链），不纳入文档集
$queue      = array_map(static fn(string $p) => [$p, 0], $roots);

while ($queue !== [] && count($discovered) < MAX_PAGES) {
    [$path, $depth] = array_shift($queue);
    if (isset($discovered[$path]) || isset($dead[$path]) || $depth > MAX_DEPTH) {
        continue;
    }
    $html = fetch('https://docs.cowagent.ai' . $path, 40, $status);
    if ($html === null) {
        if ($status === 404 || $status === 410) {
            $dead[$path] = true;   // 上游本就没有这一页，跳过即可
            continue;
        }
        $warnings[] = "发现阶段无法访问 {$path}（HTTP {$status}，将跳过）";
        $discovered[$path] = $depth;
        continue;
    }
    $content = load_content($html);
    $discovered[$path] = $depth;
    if ($content === null) {
        continue;
    }
    foreach (links_in($content) as $found) {
        if (!isset($discovered[$found]) && !isset($dead[$found]) && !isset($aliases[$found])) {
            $queue[] = [$found, $depth + 1];
        }
    }
    usleep(150000);
}

if ($dead !== []) {
    foreach (array_keys($dead) as $path) {
        $warnings[] = "上游死链 {$path}（正文中指向它的链接将降级为纯文本）";
    }
}
foreach ($aliases as $deadPath => $livePath) {
    if (isset($slugs[$livePath])) {
        $warnings[] = "上游死链 {$deadPath} → 链接已重定向到 {$livePath}";
    }
}

/** 兜底 slug：显式表中没有的路径也能生成稳定标识 */
function fallback_slug(string $path): string
{
    $s = preg_replace('#^/zh/#', '', $path) ?? $path;
    $s = preg_replace('#/index$#', '', $s) ?? $s;

    return str_replace('/', '-', $s);
}

foreach (array_keys($discovered) as $path) {
    if (!isset($slugs[$path])) {
        $slugs[$path] = fallback_slug($path);
        $warnings[]   = "新路径 $path 未在 \$slugs 表中登记，已派生 slug「{$slugs[$path]}」，建议补充到表中";
    }
}

/** 上游路径 => 本地 slug，供净化阶段改写链接 */
$upstreamMap = $slugs;

// 死链别名同样参与链接改写，让指向失效路径的链接仍能落到本地页面
foreach ($aliases as $deadPath => $livePath) {
    if (isset($slugs[$livePath])) {
        $upstreamMap[$deadPath] = $slugs[$livePath];
    }
}

ksort($discovered);
printf("  可达 %d 页（%d 页在显式映射表中）\n", count($discovered), count(array_intersect_key($slugs, $discovered)));

// ---------------------------------------------------------------------------
// 分组与顺序：决定侧栏分组与上/下一篇的顺序
// ---------------------------------------------------------------------------
$order = array_values(array_unique(array_merge($roots, array_keys($discovered))));
$order = array_values(array_filter($order, static fn(string $p) => isset($discovered[$p])));

// ---------------------------------------------------------------------------
// 净化
// ---------------------------------------------------------------------------

/** 递归净化节点，返回安全 HTML */
function clean(DOMNode $node, array $ctx): string
{
    [$ALLOWED, $KEEP_ATTR, $upstreamMap, &$stats, &$imgStats, $slug, $IMG_DIR] = $ctx;

    $out = '';
    foreach ($node->childNodes as $child) {
        if ($child->nodeType === XML_TEXT_NODE) {
            $out .= htmlspecialchars($child->nodeValue ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
            continue;
        }
        if ($child->nodeType !== XML_ELEMENT_NODE) {
            continue;
        }

        $tag = strtolower($child->nodeName);

        // 装饰 / 脚本类元素直接丢弃
        if (in_array($tag, ['svg','button','script','style','iframe','video','audio','noscript','input'], true)) {
            continue;
        }

        // ---------- 链接 ----------
        if ($tag === 'a') {
            $href   = $child->getAttribute('href');
            $hasImg = $child->getElementsByTagName('img')->length > 0;
            // 标题旁的锚点图标用零宽空格占位，trim() 去不掉，需显式清理
            $visible = preg_replace('/[\x{200b}-\x{200f}\x{2028}\x{2029}\x{feff}\s]+/u', '', $child->textContent ?? '');

            if ($visible === '' && !$hasImg) {
                $stats['icon']++;
                continue;
            }
            if ($visible === '' && $hasImg) {
                $stats['unwrapped']++;
                $out .= clean($child, $ctx);
                continue;
            }
            if ($href !== '' && $href[0] === '#') {
                $stats['anchor']++;
                $out .= '<a href="' . htmlspecialchars($href, ENT_QUOTES, 'UTF-8') . '">' . clean($child, $ctx) . '</a>';
                continue;
            }
            $path = parse_url($href, PHP_URL_PATH) ?? '';
            $path = rtrim($path, '/');
            if ($path !== '' && isset($upstreamMap[$path])) {
                $stats['local']++;
                $out .= '<a href="doc.php?p=' . urlencode($upstreamMap[$path]) . '">' . clean($child, $ctx) . '</a>';
                continue;
            }
            // 其余（含全部站外链接）解包为纯文本，确保产物零外链
            $stats['unwrapped']++;
            $out .= clean($child, $ctx);
            continue;
        }

        // ---------- 图片 ----------
        if ($tag === 'img') {
            $src = $child->getAttribute('src');
            if ($src === '') {
                continue;
            }
            $ext  = strtolower(pathinfo(parse_url($src, PHP_URL_PATH) ?? '', PATHINFO_EXTENSION));
            $ext  = in_array($ext, ['jpg','jpeg','png','gif','webp','svg'], true) ? $ext : 'png';
            $name = $slug . '-' . substr(md5($src), 0, 8) . '.' . $ext;
            $dest = $IMG_DIR . '/' . $name;

            if (!is_file($dest)) {
                if (!grab_image($src, $dest)) {
                    echo '    ✗ 图片抓取失败: ' . basename(parse_url($src, PHP_URL_PATH) ?? $src) . "\n";
                    $imgStats['fail']++;
                    continue;
                }
                $imgStats['ok']++;
                echo '    ↓ ' . $name . '  ' . round(filesize($dest) / 1024) . "KB\n";
            }

            $alt = $child->getAttribute('alt');
            $out .= '<img src="assets/img/docs/' . $name . '"'
                . ($alt !== '' ? ' alt="' . htmlspecialchars($alt, ENT_QUOTES, 'UTF-8') . '"' : '')
                . ' loading="lazy" />';
            continue;
        }

        // ---------- <span data-as="p"> 视为段落 ----------
        if ($tag === 'span' && $child->getAttribute('data-as') === 'p') {
            $inner = clean($child, $ctx);
            if (trim(preg_replace('/[\s\x{200b}\x{feff}]+/u', '', strip_tags($inner))) !== '') {
                $out .= '<p>' . $inner . '</p>';
            }
            continue;
        }

        // ---------- 白名单标签 ----------
        if (in_array($tag, $ALLOWED, true)) {
            $attrs = '';
            foreach ($KEEP_ATTR[$tag] ?? [] as $key) {
                if ($child->hasAttribute($key) && $child->getAttribute($key) !== '') {
                    $attrs .= ' ' . $key . '="' . htmlspecialchars($child->getAttribute($key), ENT_QUOTES, 'UTF-8') . '"';
                }
            }
            $inner = clean($child, $ctx);
            if (in_array($tag, ['p','li','td','th'], true)
                && trim(preg_replace('/[\s\x{200b}\x{feff}]+/u', '', strip_tags($inner))) === '') {
                continue;
            }
            $out .= '<' . $tag . $attrs . '>' . $inner . '</' . $tag . '>';
            continue;
        }

        // ---------- div / figure 等一律解包 ----------
        $out .= clean($child, $ctx);
    }

    return $out;
}

/** 取首个段落作为页面导语（正文首个小标题之前） */
function lead_of(DOMElement $content): string
{
    foreach ((new DOMXPath($content->ownerDocument))->query('.//p | .//*[@data-as="p"]', $content) as $p) {
        $text = trim((string) preg_replace('/\s+/u', ' ', $p->textContent));
        if ($text !== '' && mb_strlen($text) >= 8) {
            return mb_strlen($text) > 160 ? mb_substr($text, 0, 157) . '…' : $text;
        }
    }

    return '';
}

/** 取页面标题：优先 data-page-title，其次 <title> */
function title_of(string $html): string
{
    if (preg_match('/data-page-title="([^"]+)"/u', $html, $m) === 1) {
        return html_entity_decode($m[1], ENT_QUOTES | ENT_HTML5, 'UTF-8');
    }
    if (preg_match('#<title>([^<]*)#u', $html, $m) === 1) {
        return trim(str_replace('- CowAgent', '', html_entity_decode($m[1], ENT_QUOTES | ENT_HTML5, 'UTF-8')));
    }

    return '';
}

// ---------------------------------------------------------------------------
// 抓取 + 落盘
// ---------------------------------------------------------------------------
echo "\n=== 抓取与净化 ===\n";

$corpus   = [];   // slug => 正文（全部成功后才落盘）
$manifest = [];   // slug => meta
$failures = [];

foreach ($order as $path) {
    $slug = $slugs[$path];
    echo "=== $slug ($path) ===\n";

    $html = fetch('https://docs.cowagent.ai' . $path);
    if ($html === null) {
        echo "  ❌ 页面抓取失败\n";
        $failures[] = "$slug: 页面抓取失败";
        continue;
    }
    $content = load_content($html);
    if ($content === null) {
        echo "  ❌ 未找到正文容器（上游结构可能已变化）\n";
        $failures[] = "$slug: 未找到 #content";
        continue;
    }

    $before = $imgStats['fail'];
    $ctx    = [$ALLOWED, $KEEP_ATTR, $upstreamMap, &$stats, &$imgStats, $slug, $IMG_DIR];
    $body   = trim((string) preg_replace("/\n{3,}/", "\n\n", clean($content, $ctx)));

    if ($imgStats['fail'] > $before) {
        // 有图片既抓不到、本地也没有留存 —— 此时落盘会静默丢内容，必须失败退出
        $failures[] = "$slug: " . ($imgStats['fail'] - $before) . ' 张图片无法获取且本地无留存';
    }

    // 英文标题（只取标题，正文仍为中文，与站点「仅镜像中文文档」的定位一致）
    $enTitle = '';
    $enHtml  = fetch('https://docs.cowagent.ai/en' . substr($path, 3), 40);
    if ($enHtml !== null) {
        $enTitle = title_of($enHtml);
    }

    // 标题锚点完整性
    preg_match_all('/href="#([^"]+)"/u', $body, $linkMatches);
    preg_match_all('/<(?:h2|h3|h4) id="([^"]+)"/u', $body, $idMatches);
    $ids    = array_flip($idMatches[1]);
    $missed = array_values(array_filter(array_unique($linkMatches[1]), static fn($t) => !isset($ids[$t])));

    // 站外引用残留（应为 0）
    preg_match_all('/(?:href|src)="(?:https?:)?\/\/[^"]*"/u', $body, $external);
    if ($external[0] !== []) {
        $failures[] = "$slug: 净化后仍残留 " . count($external[0]) . ' 处站外引用';
    }

    $corpus[$slug]   = $body;
    $manifest[$slug] = [
        'path'     => $path,
        'section'  => explode('/', trim(substr($path, 4), '/'))[0] ?: 'intro',
        'title'    => ['zh' => title_of($html), 'en' => $enTitle !== '' ? $enTitle : title_of($html)],
        'lead'     => lead_of($content),
        'anchor_ok' => $missed === [],
        'order'    => array_search($path, $order, true),
    ];

    printf(
        "  ✓ %d 字 | 表格 %d | 代码块 %d | 图片 %d | 锚点 %s | 残留外链 %d\n",
        mb_strlen(trim(preg_replace('/\s+/u', ' ', strip_tags($body)))),
        preg_match_all('/<table/u', $body),
        preg_match_all('/<pre/u', $body),
        preg_match_all('/<img/u', $body),
        $missed === [] ? '正常' : ('缺 ' . count($missed)),
        count($external[0])
    );
    usleep(150000);
}

// 落盘前最终校验：正文引用的每张图片都必须真实存在于磁盘上
foreach ($corpus as $slug => $body) {
    preg_match_all('#assets/img/docs/([^"]+)#', $body, $used);
    foreach (array_unique($used[1]) as $name) {
        if (!is_file($IMG_DIR . '/' . $name)) {
            $failures[] = "$slug: 正文引用的图片 $name 不存在于磁盘";
        }
    }
}

if ($failures !== []) {
    fwrite(STDERR, "\n❌ 构建中止，未写入任何文件（避免用不完整内容覆盖现有文档）：\n");
    foreach ($failures as $failure) {
        fwrite(STDERR, "   - $failure\n");
    }
    fwrite(STDERR, "\n可稍后重试；已存在的本地图片不会被删除，仅缺失的会重新下载。\n");
    exit(1);
}

// 全部成功后统一落盘
foreach ($corpus as $slug => $body) {
    file_put_contents($root . '/docs/' . $slug . '.html', $body . "\n");
}

// 生成清单（供 bootstrap.php 的 doc_registry() 读取）
$manifestOut = "<?php\n"
    . "declare(strict_types=1);\n\n"
    . "/**\n"
    . " * 本地能力文档清单 —— **由 tools/build-docs.php 自动生成，请勿手改**。\n"
    . " *\n"
    . " * 每个条目：\n"
    . " *   path    上游文档路径（仅作来源标注，页面不生成任何外链）\n"
    . " *   section 分组键（侧栏分组用，文案见 lang/*.php 的 doc.sections.<key>）\n"
    . " *   title   中/英标题（取自上游页面 data-page-title）\n"
    . " *   lead    正文首段，用作文档页导语\n"
    . " *   order   上/下一篇顺序\n"
    . " */\n\n"
    . 'return ' . var_export(['docs' => $manifest], true) . ";\n";
file_put_contents($root . '/docs/manifest.php', $manifestOut);

// 清理不再被引用的旧图
$referenced = [];
foreach ($corpus as $body) {
    preg_match_all('#assets/img/docs/([^"]+)#', $body, $used);
    foreach ($used[1] as $name) {
        $referenced[$name] = true;
    }
}
$removed = 0;
foreach (glob($IMG_DIR . '/*') ?: [] as $file) {
    if (!isset($referenced[basename($file)])) {
        @unlink($file);
        $removed++;
    }
}

$totalImg = 0;
foreach (glob($IMG_DIR . '/*') ?: [] as $file) {
    $totalImg += (int) filesize($file);
}

echo "\n=== 链接处理 ===\n";
echo "锚点图标丢弃 {$stats['icon']} / 页内锚点保留 {$stats['anchor']} / 本地化 {$stats['local']} / 解包移除 {$stats['unwrapped']}\n";
echo "\n=== 图片 ===\n";
echo "新下载 {$imgStats['ok']} 张, 失败 {$imgStats['fail']} 张, 清理 {$removed} 张, 本地合计 " . round($totalImg / 1024) . " KB\n";
echo "\n=== 文档集 ===\n";
echo '已写入 ' . count($corpus) . " 个片段 + docs/manifest.php\n";

if ($warnings !== []) {
    echo "\n=== 告警 ===\n";
    foreach ($warnings as $w) {
        echo "  ⚠ $w\n";
    }
}
echo "\n✅ 构建完成\n";
