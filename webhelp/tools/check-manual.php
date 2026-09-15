<?php
declare(strict_types=1);

/**
 * 校验手册结构与双语文案的对应关系（视频版）：
 * 1. content.php 声明的每个主题 / 视频，在 zh 与 en 里都有对应文案；
 * 2. zh 与 en 的 manual.* 键结构完全一致；
 * 3. 每个视频都有标题与至少一条内容要点，避免出现空条目；
 * 4. 声明了视频源（src）的视频必须具备标题与时长，避免出现无标题播放器。
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

// ---- 主题 / 视频结构对照 ----
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

        foreach ((array) ($item['videos'] ?? []) as $video) {
            $videoId = (string) ($video['id'] ?? '');
            $entry   = $topic['videos'][$videoId] ?? null;
            if (!is_array($entry)) {
                $note("{$lang} 主题 {$id} 缺少视频 {$videoId}");
                continue;
            }

            $title = trim((string) ($entry['title'] ?? ''));
            if ($title === '') {
                $note("{$lang} 主题 {$id} 的视频 {$videoId} 缺少标题");
            }

            $covers = array_filter(
                (array) ($entry['covers'] ?? []),
                static fn ($v): bool => trim((string) $v) !== ''
            );
            if ($covers === []) {
                $note("{$lang} 主题 {$id} 的视频 {$videoId} 没有任何内容要点");
            }

            if (trim((string) ($video['src'] ?? '')) !== '') {
                if ($title === '' || trim((string) ($video['duration'] ?? '')) === '') {
                    $note("主题 {$id} 的视频 {$videoId} 声明了视频源，但缺少标题或时长");
                }
            }
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
$required = [
    'title', 'title_accent', 'lead', 'toc', 'covers_label',
    'docs_label', 'pages_label', 'cli_title', 'slash_title',
    'video_pending', 'video_fallback',
];
foreach ($required as $field) {
    foreach (['zh' => $zh, 'en' => $en] as $lang => $pack) {
        if (trim((string) ($pack['manual'][$field] ?? '')) === '') {
            $note("{$lang} 缺少手册顶层文案 manual.{$field}");
        }
    }
}

echo $fail === 0 ? "OK  手册结构与双语文案一致\n" : "{$fail} 项不一致\n";
exit($fail === 0 ? 0 : 1);
