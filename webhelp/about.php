<?php
declare(strict_types=1);

require_once __DIR__ . '/includes/bootstrap.php';

$pageTitle       = t('meta.title_about') . ' · ' . (string) cfg('brand.name');
$pageDescription = t('about.lead');
$brand           = (array) cfg('brand', []);

require __DIR__ . '/includes/header.php';
?>

<main id="main">

  <?php page_hero('meta.title_about', 'about.lead'); ?>

  <!-- ===== 项目理念 ===== -->
  <section class="section">
    <div class="section-container">
      <h2 class="section-title"><?= e(t('about.mission_title')) ?></h2>
      <div class="features-grid features-grid--4" style="margin-top:28px">
        <?php foreach ((array) content('mission', []) as $item): ?>
          <?php $id = (string) $item['id']; ?>
          <article class="feature-card feature-card--compact reveal">
            <span class="feature-icon"><?= icon((string) $item['icon']) ?></span>
            <p class="feature-desc"><?= e(t('about.mission.' . $id)) ?></p>
          </article>
        <?php endforeach; ?>
      </div>
    </div>
  </section>

  <!-- ===== 与上游的关系 ===== -->
  <section class="section section--soft">
    <div class="section-container">
      <div class="split">
        <div>
          <h2 class="section-title section-title--left"><?= e(t('about.upstream_title')) ?></h2>
          <p class="section-subtitle section-subtitle--left"><?= e(t('about.upstream_desc')) ?></p>
        </div>
        <div class="card-plain">
          <div class="brand-inline">
            <img src="<?= e(asset((string) ($brand['upstream_logo'] ?? ''))) ?>" alt="<?= e((string) ($brand['upstream_name'] ?? '')) ?>" />
            <div>
              <strong><?= e((string) ($brand['upstream_name'] ?? '')) ?></strong>
              <span><?= e(t('common.open_source')) ?></span>
            </div>
          </div>
          <p style="margin-top:16px"><?= e(t('about.upstream_card')) ?></p>
        </div>
      </div>
    </div>
  </section>

  <!-- ===== 免责声明 ===== -->
  <section class="section">
    <div class="section-container">
      <h2 class="section-title section-title--left"><?= e(t('about.disclaimer_title')) ?></h2>
      <div class="stack-sm" style="margin-top:24px">
        <?php foreach (t_list('about.disclaimer') as $item): ?>
          <div class="note reveal">
            <?= icon('shield') ?>
            <span><?= e($item) ?></span>
          </div>
        <?php endforeach; ?>
      </div>
    </div>
  </section>

  <!-- ===== CTA ===== -->
  <section class="section section--soft">
    <div class="section-container">
      <div class="cta-banner reveal">
        <h2><?= e(t('cta.title')) ?></h2>
        <p><?= e(t('cta.subtitle')) ?></p>
        <div class="cta-actions">
          <a class="btn btn-primary" href="<?= e(url('quickstart.php')) ?>"><?= e(t('cta.primary')) ?></a>
          <a class="btn btn-cloud" href="<?= e(url('enterprise.php')) ?>"><?= e(t('cta.enterprise')) ?></a>
        </div>
      </div>
    </div>
  </section>

</main>

<?php require __DIR__ . '/includes/footer.php'; ?>
