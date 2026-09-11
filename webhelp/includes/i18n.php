<?php
declare(strict_types=1);

/**
 * 极简 i18n：语言解析 + 点号取值 + 语言切换 URL 生成。
 * 语言通过 ?lang=xx 切换，并用 cookie 记住选择。
 */

const WEBHELP_LANGS        = ['zh', 'en'];
const WEBHELP_LANG_DEFAULT = 'zh';
const WEBHELP_LANG_COOKIE  = 'webhelp_lang';

/** 所有语言包缓存（惰性加载） */
function lang_pack(?string $lang = null): array
{
    static $cache = [];

    $lang = $lang ?? current_lang();
    if (!isset($cache[$lang])) {
        $file = WEBHELP_ROOT . '/lang/' . $lang . '.php';
        $cache[$lang] = is_file($file) ? (array) require $file : [];
    }

    return $cache[$lang];
}

/** 解析并记忆当前语言（须在输出前调用一次） */
function init_i18n(): string
{
    static $resolved = null;
    if ($resolved !== null) {
        return $resolved;
    }

    $lang = null;

    if (isset($_GET['lang'])) {
        $candidate = strtolower((string) $_GET['lang']);
        if (in_array($candidate, WEBHELP_LANGS, true)) {
            $lang = $candidate;
            // 记录用户选择；路径设为站点根，便于全站生效
            if (!headers_sent()) {
                setcookie(WEBHELP_LANG_COOKIE, $lang, [
                    'expires'  => time() + 31536000,
                    'path'     => '/',
                    'samesite' => 'Lax',
                ]);
            }
        }
    }

    if ($lang === null && isset($_COOKIE[WEBHELP_LANG_COOKIE])) {
        $candidate = strtolower((string) $_COOKIE[WEBHELP_LANG_COOKIE]);
        if (in_array($candidate, WEBHELP_LANGS, true)) {
            $lang = $candidate;
        }
    }

    if ($lang === null) {
        $lang = WEBHELP_LANG_DEFAULT;
    }

    return $resolved = $lang;
}

function current_lang(): string
{
    return init_i18n();
}

/** 按点号路径取值：t('hero.title')、t('capabilities.items.memory.title') */
function t(string $key, string $fallback = ''): string
{
    $value = lang_pack();
    foreach (explode('.', $key) as $segment) {
        if (!is_array($value) || !array_key_exists($segment, $value)) {
            // 回退到默认语言包
            $value = lang_pack(WEBHELP_LANG_DEFAULT);
            foreach (explode('.', $key) as $seg) {
                if (!is_array($value) || !array_key_exists($seg, $value)) {
                    return $fallback !== '' ? $fallback : $key;
                }
                $value = $value[$seg];
            }
            break;
        }
        $value = $value[$segment];
    }

    if (is_array($value)) {
        return $fallback !== '' ? $fallback : $key;
    }

    $text = (string) $value;

    return $text !== '' ? $text : ($fallback !== '' ? $fallback : $key);
}

/** 某键在指定语言下是否存在（用于回退判断） */
function t_exists(string $key): bool
{
    return t($key, "\0") !== "\0";
}

/** 取数组型文案（如要点列表），始终返回数组 */
function t_list(string $key): array
{
    $value = lang_pack();
    foreach (explode('.', $key) as $segment) {
        if (!is_array($value) || !array_key_exists($segment, $value)) {
            $value = lang_pack(WEBHELP_LANG_DEFAULT);
            foreach (explode('.', $key) as $seg) {
                if (!is_array($value) || !array_key_exists($seg, $value)) {
                    return [];
                }
                $value = $value[$seg];
            }
            break;
        }
        $value = $value[$segment];
    }

    return is_array($value) ? array_values($value) : [];
}

/** 取关联数组型文案（保留键名，如按 id 索引的映射） */
function t_map(string $key): array
{
    $value = lang_pack();
    foreach (explode('.', $key) as $segment) {
        if (!is_array($value) || !array_key_exists($segment, $value)) {
            $value = lang_pack(WEBHELP_LANG_DEFAULT);
            foreach (explode('.', $key) as $seg) {
                if (!is_array($value) || !array_key_exists($seg, $value)) {
                    return [];
                }
                $value = $value[$seg];
            }
            break;
        }
        $value = $value[$segment];
    }

    return is_array($value) ? $value : [];
}

/** 生成当前页面的语言切换地址 */
function lang_url(string $lang): string
{
    $params        = $_GET;
    $params['lang'] = $lang;

    return strtok($_SERVER['REQUEST_URI'] ?? '/', '?') . '?' . http_build_query($params);
}
