<?php
declare(strict_types=1);

/**
 * 页面头部：<head> 元信息 + 固定导航栏。
 * 引入前可设置 $pageTitle / $pageDescription / $pageKeywords 覆盖默认值。
 */

$brand        = (array) cfg('brand', []);
$page         = current_page();
$titleKey     = (string) cfg('page_titles.' . $page, 'meta.title_home');
$pageTitle    = $pageTitle ?? (t($titleKey) . ' · ' . (string) ($brand['name'] ?? ''));
$pageDesc     = $pageDescription ?? t('meta.description');
$pageKeywords = $pageKeywords ?? t('meta.keywords');
$lang         = current_lang();
$otherLang    = $lang === 'zh' ? 'en' : 'zh';
$htmlLang     = $lang === 'zh' ? 'zh-CN' : 'en';
$nav          = (array) cfg('nav', []);

// canonical 保留除 lang 外的查询参数（文档页靠 ?p= 区分，否则会全部指向同一地址）
$canonicalQuery = $_GET;
unset($canonicalQuery['lang']);
$canonical = base_url() . '/' . $page . ($canonicalQuery ? '?' . http_build_query($canonicalQuery) : '');
?>
<!DOCTYPE html>
<html lang="<?= e($htmlLang) ?>">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title><?= e($pageTitle) ?></title>
  <meta name="description" content="<?= e($pageDesc) ?>" />
  <meta name="keywords" content="<?= e($pageKeywords) ?>" />
  <meta name="theme-color" content="#0a0a0a" />

  <link rel="canonical" href="<?= e($canonical) ?>" />
  <link rel="alternate" hreflang="zh-CN" href="<?= e($canonical . ($canonical === '' ? '?' : '&') . 'lang=zh') ?>" />
  <link rel="alternate" hreflang="en" href="<?= e($canonical . ($canonical === '' ? '?' : '&') . 'lang=en') ?>" />

  <meta property="og:type" content="website" />
  <meta property="og:site_name" content="<?= e((string) ($brand['name'] ?? '')) ?>" />
  <meta property="og:title" content="<?= e($pageTitle) ?>" />
  <meta property="og:description" content="<?= e($pageDesc) ?>" />
  <meta property="og:locale" content="<?= $lang === 'zh' ? 'zh_CN' : 'en_US' ?>" />
  <meta property="og:image" content="<?= e((string) ($brand['upstream_logo'] ?? '')) ?>" />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content="<?= e($pageTitle) ?>" />
  <meta name="twitter:description" content="<?= e($pageDesc) ?>" />

  <link rel="icon" type="image/x-icon" href="<?= e((string) ($brand['favicon'] ?? '')) ?>" />
  <link rel="stylesheet" href="<?= e(asset('assets/css/style.css')) ?>" />

  <script>
    // 尽早应用主题，避免首屏闪烁
    (function () {
      try {
        var saved = localStorage.getItem('webhelp-theme');
        if (saved === 'light' || saved === 'dark') {
          document.documentElement.setAttribute('data-theme', saved);
        }
      } catch (err) { /* 忽略隐私模式下的异常 */ }
    })();
  </script>
</head>
<body>
  <a class="skip-link" href="#main"><?= e(t('common.skip_to_content')) ?></a>

  <nav class="navbar" id="navbar">
    <div class="nav-container">
      <a href="<?= e(url('index.php')) ?>" class="nav-logo" aria-label="<?= e((string) ($brand['name'] ?? '')) ?>">
        <img src="<?= e(asset((string) ($brand['logo_dark'] ?? ''))) ?>" alt="<?= e((string) ($brand['name'] ?? '')) ?>" class="logo-img logo-img--dark" />
        <img src="<?= e(asset((string) ($brand['logo_light'] ?? ''))) ?>" alt="" aria-hidden="true" class="logo-img logo-img--light" />
      </a>

      <button class="nav-toggle" type="button" id="navToggle"
              aria-expanded="false" aria-controls="navLinks" aria-label="<?= e(t('common.menu')) ?>">
        <?= icon('menu') ?>
      </button>

      <div class="nav-links" id="navLinks">
        <?php foreach ($nav as $navPage => $navKey): ?>
          <a href="<?= e(url((string) $navPage)) ?>" class="nav-link<?= is_current((string) $navPage) ? ' is-active' : '' ?>"><?= e(t($navKey)) ?></a>
        <?php endforeach; ?>

        <button class="theme-switch" type="button" id="themeToggle" aria-label="<?= e(t('common.theme_toggle')) ?>">
          <span class="icon-sun"><?= icon('sun') ?></span>
          <span class="icon-moon"><?= icon('moon') ?></span>
        </button>

        <a href="<?= e(lang_url($otherLang)) ?>" class="lang-switch" hreflang="<?= e($otherLang) ?>"
           aria-label="<?= e(t('common.lang_switch_aria')) ?>"><?= e(t('common.lang_switch')) ?></a>
      </div>
    </div>
  </nav>
