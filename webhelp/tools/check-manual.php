<?php
declare(strict_types=1);

/**
 * 校验手册结构与双语文案的对应关系：
 * 1. content.php 声明的每个章节 / 块，在 zh 与 en 里都有对应文案；
 * 2. zh 与 en 的 manual.* 键结构完全一致（段落、块、字段均为「名称 + 说明」二元组）；
 * 3. 每个块至少要有 steps / fields / items / note 之一，避免出现空块。
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

// ---- 章节 / 块结构对照 ----
foreach ((array) content('manual_sections', []) as $item) {
    $id = (string) $item['id'];
    foreach (['zh' => $zh, 'en' => $en] as $lang => $pack) {
        $sec = $pack['manual']['sections'][$id] ?? null;
        if (!is_array($sec)) {
            $note("{$lang} 缺少章节 {$id}");
            continue;
        }
        foreach (['nav', 'title', 'goal'] as $field) {
            if (trim((string) ($sec[$field] ?? '')) === '') {
                $note("{$lang} 章节 {$id} 缺少 {$field}");
            }
        }

        foreach ((array) ($item['blocks'] ?? []) as $blockId) {
            $block = $sec['blocks'][$blockId] ?? null;
            if (!is_array($block)) {
                $note("{$lang} 章节 {$id} 缺少块 {$blockId}");
                continue;
            }
            $hasBody = ($block['steps'] ?? []) !== []
                || ($block['fields'] ?? []) !== []
                || ($block['items'] ?? []) !== []
                || trim((string) ($block['note'] ?? '')) !== '';
            if (!$hasBody) {
                $note("{$lang} 章节 {$id} 的块 {$blockId} 没有任何内容");
            }
            if (trim((string) ($block['title'] ?? '')) === '') {
                $note("{$lang} 章节 {$id} 的块 {$blockId} 缺少标题");
            }
        }
    }

    // 分组必须在 manual_parts 里声明
    $part = (string) ($item['part'] ?? '');
    $parts = array_column((array) content('manual_parts', []), 'id');
    if (!in_array($part, $parts, true)) {
        $note("章节 {$id} 的分组 {$part} 未在 manual_parts 中声明");
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
$required = ['title', 'title_accent', 'lead', 'toc', 'goal_label', 'docs_label', 'pages_label', 'cli_title', 'slash_title'];
foreach ($required as $field) {
    foreach (['zh' => $zh, 'en' => $en] as $lang => $pack) {
        if (trim((string) ($pack['manual'][$field] ?? '')) === '') {
            $note("{$lang} 缺少手册顶层文案 manual.{$field}");
        }
    }
}

// ---- 站内页面映射 ----
foreach ((array) content('manual_sections', []) as $item) {
    foreach ((array) ($item['links'] ?? []) as $link) {
        if (!isset(content('manual_page_links', [])[(string) $link])) {
            $note("章节 {$item['id']} 引用了未映射的页面 id {$link}");
        }
    }
    foreach ((array) ($item['docs'] ?? []) as $slug) {
        if (!doc_exists((string) $slug)) {
            $note("章节 {$item['id']} 引用了未登记的文档 slug {$slug}");
        }
    }
}

echo $fail === 0 ? "OK  手册结构与双语文案一致\n" : "{$fail} 项不一致\n";
exit($fail === 0 ? 0 : 1);
