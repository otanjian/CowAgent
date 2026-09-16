<?php
declare(strict_types=1);

/**
 * 校验手册结构与双语文案的对应关系（截图版）：
 * 1. content.php 声明的每个主题 / 步骤，在 zh 与 en 里都有对应文案；
 * 2. zh 与 en 的 manual.* 键结构完全一致；
 * 3. 每个步骤都有标题与操作要点，避免出现空条目；
 * 4. 每个步骤声明的截图真实存在、非空、文件名前缀与主题一致，且内容不重复；
 * 5. 主题声明的文档 slug 与站内页面 id 都能解析，避免死链。
 *
 * 截图是手册唯一的「介质」：少一张就是破图，重复一张就是拿错屏的截图，
 * 因此这两类问题都在这里拦下，而不是等到人工翻页面才发现。
 */

$root = dirname(__DIR__);

require $root . '/includes/bootstrap.php';

$zh = require $root . '/lang/zh.php';
$en = require $root . '/lang/en.php';

$fail = 0;
$note = static function (string $msg) use (&$fail): void {
    $fail++;
    echo "FAIL  {$msg}\n";
};

/** 把结构化成字符串路径，便于逐项比对 */
$flatten = static function ($node, string $prefix = '') use (&$flatten): array {
    if (!is_array($node)) {
        return [$prefix];
    }
    $out = [];
    foreach ($node as $key => $value) {
        $key = is_int($key) ? '[]' : (string) $key;
        $out = array_merge($out, $flatten($value, $prefix === '' ? $key : $prefix . '.' . $key));
    }

    return $out;
};

// ---- 1 & 2. 双语键结构一致 ----
$a = $flatten($zh['manual'] ?? [], 'manual');
$b = $flatten($en['manual'] ?? [], 'manual');
sort($a);
sort($b);

foreach (array_diff($a, $b) as $k) {
    $note("en 缺少键 {$k}");
}
foreach (array_diff($b, $a) as $k) {
    $note("zh 缺少键 {$k}");
}

// ---- 主题 / 步骤结构对照 ----
$shotSeen  = [];   // 截图路径 => 引用它的「主题.步骤」
$shotBytes = [];   // 截图路径 => 文件大小
foreach ((array) content('manual_topics', []) as $item) {
    $id    = (string) $item['id'];
    $parts = array_column((array) content('manual_parts', []), 'id');
    if (!in_array((string) ($item['part'] ?? ''), $parts, true)) {
        $note("主题 {$id} 的分组 {$item['part']} 未在 manual_parts 中声明");
    }

    foreach (['zh' => $zh, 'en' => $en] as $lang => $pack) {
        $topic = $pack['manual']['topics'][$id] ?? null;
        if (!is_array($topic)) {
            $note("{$lang} 缺少主题 {$id}");
            continue;
        }

        foreach (['nav', 'title', 'lead'] as $field) {
            if (trim((string) ($topic[$field] ?? '')) === '') {
                $note("{$lang} 主题 {$id} 缺少 {$field}");
            }
        }

        foreach ((array) ($item['steps'] ?? []) as $step) {
            $stepId = (string) ($step['id'] ?? '');
            $entry  = $topic['steps'][$stepId] ?? null;
            if (!is_array($entry)) {
                $note("{$lang} 主题 {$id} 缺少步骤 {$stepId}");
                continue;
            }

            foreach (['title', 'body'] as $field) {
                if (trim((string) ($entry[$field] ?? '')) === '') {
                    $note("{$lang} 主题 {$id} 的步骤 {$stepId} 缺少 {$field}");
                }
            }
        }
    }

    $steps = (array) ($item['steps'] ?? []);
    if ($steps === []) {
        $note("主题 {$id} 没有任何步骤");
    }

    foreach ($steps as $step) {
        $stepId = (string) ($step['id'] ?? '');
        $shot   = trim((string) ($step['shot'] ?? ''));
        $where  = "{$id}.{$stepId}";

        if ($shot === '') {
            $note("主题 {$id} 的步骤 {$stepId} 没有截图");
            continue;
        }

        if (str_starts_with($shot, '/') || str_contains($shot, '..')) {
            $note("主题 {$id} 的步骤 {$stepId} 的截图路径必须是 assets/ 下的相对路径：{$shot}");
            continue;
        }

        if (isset($shotSeen[$shot])) {
            $note("截图 {$shot} 被 {$shotSeen[$shot]} 与 {$where} 重复引用");
        }
        $shotSeen[$shot] = $where;

        $file = $root . '/' . $shot;
        if (!is_file($file)) {
            $note("主题 {$id} 的步骤 {$stepId} 的截图不存在：{$shot}");
            continue;
        }

        $bytes = (int) filesize($file);
        if ($bytes <= 0) {
            $note("主题 {$id} 的步骤 {$stepId} 的截图是空文件：{$shot}");
            continue;
        }
        $shotBytes[$shot] = $bytes;

        // 截图文件名前缀＝主题 id，便于一眼看出这张图属于哪个主题
        $base = basename($shot);
        if (!str_starts_with($base, $id . '-')) {
            $note("截图 {$base} 的文件名前缀与主题 {$id} 不一致");
        }
    }

    foreach ((array) ($item['links'] ?? []) as $link) {
        if (!isset(content('manual_page_links', [])[(string) $link])) {
            $note("主题 {$id} 引用了未映射的页面 id {$link}");
        }
    }

    foreach ((array) ($item['docs'] ?? []) as $slug) {
        if (!doc_exists((string) $slug)) {
            $note("主题 {$id} 引用了未登记的文档 slug {$slug}");
        }
    }
}

// ---- 4b. 截图目录里的孤儿文件与内容重复 ----
$shotDir = $root . '/assets/img/manual';
$onDisk  = [];
foreach (glob($shotDir . '/*.{png,jpg,jpeg,webp}', GLOB_BRACE) ?: [] as $file) {
    $onDisk[] = 'assets/img/manual/' . basename($file);
}
sort($onDisk);

foreach (array_diff($onDisk, array_keys($shotSeen)) as $orphan) {
    $note("截图目录里的 {$orphan} 没有被任何步骤引用（删掉，或在 content.php 里用上）");
}

// 内容完全相同的两张图，通常是「同一次截图存了两遍」或「打开菜单后忘了重截」
$byHash = [];
foreach ($shotBytes as $shot => $bytes) {
    $hash = (string) hash_file('sha256', $root . '/' . $shot);
    $byHash[$hash][] = $shot;
}
foreach ($byHash as $group) {
    if (count($group) > 1) {
        $note('以下截图内容完全相同，多半是抓错屏：' . implode(' / ', $group));
    }
}

// ---- 分组文案 ----
foreach ((array) content('manual_parts', []) as $part) {
    $pid = (string) $part['id'];
    foreach (['zh' => $zh, 'en' => $en] as $lang => $pack) {
        if (trim((string) ($pack['manual']['parts'][$pid] ?? '')) === '') {
            $note("{$lang} 缺少分组文案 manual.parts.{$pid}");
        }
    }
}

// ---- 页头与各分节标题等必备文案 ----
$required = ['title', 'title_accent', 'lead', 'toc', 'shot_hint', 'docs_label', 'pages_label'];
foreach ($required as $field) {
    foreach (['zh' => $zh, 'en' => $en] as $lang => $pack) {
        if (trim((string) ($pack['manual'][$field] ?? '')) === '') {
            $note("{$lang} 缺少手册顶层文案 manual.{$field}");
        }
    }
}

$topics = count((array) content('manual_topics', []));
$shots  = count($shotSeen);
echo $fail === 0
    ? "OK  手册结构、双语文案与截图一致（{$topics} 个主题 / {$shots} 张截图）\n"
    : "{$fail} 项不一致\n";
exit($fail === 0 ? 0 : 1);
