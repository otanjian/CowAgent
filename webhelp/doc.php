<?php
declare(strict_types=1);

/**
 * 本地能力文档阅读页。
 *
 * 正文来自上游 CowAgent 官方中文文档的本地化归档（docs/<slug>.html），
 * 在构建阶段已剥离全部站外引用，页面本身不产生任何外部请求。
 */

require_once __DIR__ . '/includes/bootstrap.php';

$slug = trim((string) ($_GET['p'] ?? ''));
$ok   = doc_exists($slug);

if (!$ok) {
    http_response_code(404);
    $pageTitle       = t('doc.not_found') . ' · ' . (string) cfg('brand.name');
    $pageDescription = t('doc.not_found_desc');
} else {
    $docTitle        = doc_title($slug);
    $pageTitle       = $docTitle . ' · ' . t('doc.breadcrumb') . ' · ' . (string) cfg('brand.name');
    $pageDescription = doc_lead($slug);
}

require __DIR__ . '/includes/header.php';

if (!$ok) {
    ?>
    <main id="main">
      <header class="page-hero">
        <div class="section-container">
          <nav class="breadcrumb" aria-label="breadcrumb">
            <a href="<?= e(url('index.php')) ?>"><?= e(t('nav.home')) ?></a>
            <span aria-hidden="true">/</span>
            <span><?= e(t('doc.breadcrumb')) ?></span>
          </nav>
          <h1><?= e(t('doc.not_found')) ?></h1>
          <p><?= e(t('doc.not_found_desc')) ?></p>
        </div>
      </header>

      <section class="section">
        <div class="section-container">
          <div class="cta-actions" style="justify-content:center">
            <a class="btn btn-primary" href="<?= e(url('features.php')) ?>"><?= e(t('cta.back_features')) ?></a>
          </div>
        </div>
      </section>
    </main>
    <?php
    require __DIR__ . '/includes/footer.php';
    exit;
}

$body  = doc_body($slug);
$toc   = doc_toc($body);
$prev  = doc_neighbor($slug, -1);
$next  = doc_neighbor($slug, 1);
?>

<main id="main">

  <header class="page-hero doc-hero">
    <div class="section-container">
      <nav class="breadcrumb" aria-label="breadcrumb">
        <a href="<?= e(url('index.php')) ?>"><?= e(t('nav.home')) ?></a>
        <span aria-hidden="true">/</span>
        <a href="<?= e(url('features.php')) ?>"><?= e(t('doc.breadcrumb')) ?></a>
        <span aria-hidden="true">/</span>
        <span><?= e($docTitle) ?></span>
      </nav>
      <h1><?= e($docTitle) ?></h1>
      <p><?= e(doc_lead($slug)) ?></p>
    </div>
  </header>

  <section class="section">
    <div class="section-container">
      <div class="doc-layout">

        <article class="doc-article">
          <p class="doc-notice"><?= e(t('doc.notice')) ?></p>

          <?php if ($toc !== []): ?>
            <details class="doc-toc" open>
              <summary><?= e(t('doc.toc')) ?></summary>
              <ol>
                <?php foreach ($toc as $entry): ?>
                  <li class="doc-toc-item doc-toc-item--h<?= (int) $entry['level'] ?>">
                    <a href="#<?= e($entry['id']) ?>"><?= e($entry['text']) ?></a>
                  </li>
                <?php endforeach; ?>
              </ol>
            </details>
          <?php endif; ?>

          <div class="doc-body">
            <?= $body ?>
          </div>

          <nav class="doc-pager" aria-label="<?= e(t('doc.all_docs')) ?>">
            <?php if ($prev !== ''): ?>
              <a class="doc-pager-link doc-pager-link--prev" href="<?= e(doc_url($prev)) ?>">
                <span class="doc-pager-label"><?= e(t('doc.prev')) ?></span>
                <span class="doc-pager-title"><?= e(doc_title($prev)) ?></span>
              </a>
            <?php else: ?>
              <span></span>
            <?php endif; ?>
            <?php if ($next !== ''): ?>
              <a class="doc-pager-link doc-pager-link--next" href="<?= e(doc_url($next)) ?>">
                <span class="doc-pager-label"><?= e(t('doc.next')) ?></span>
                <span class="doc-pager-title"><?= e(doc_title($next)) ?></span>
              </a>
            <?php endif; ?>
          </nav>

          <p class="doc-source"><?= e(t('doc.source')) ?></p>
        </article>

        <aside class="doc-aside">
          <p class="doc-aside-title"><?= e(t('doc.all_docs')) ?></p>
          <?php foreach (doc_grouped() as $section => $items): ?>
            <p class="doc-aside-section"><?= e(t('doc.sections.' . $section, $section)) ?></p>
            <ul class="doc-aside-list">
              <?php foreach ($items as $itemSlug => $item): ?>
                <li>
                  <a class="doc-aside-link<?= $itemSlug === $slug ? ' is-active' : '' ?>"
                     href="<?= e(doc_url((string) $itemSlug)) ?>"><?= e(doc_title((string) $itemSlug)) ?></a>
                </li>
              <?php endforeach; ?>
            </ul>
          <?php endforeach; ?>
          <a class="btn btn-cloud doc-aside-back" href="<?= e(url('features.php')) ?>"><?= e(t('cta.back_features')) ?></a>
        </aside>

      </div>
    </div>
  </section>

</main>

<?php require __DIR__ . '/includes/footer.php'; ?>
