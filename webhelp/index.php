<?php
declare(strict_types=1);

require_once __DIR__ . '/includes/bootstrap.php';

$brand = (array) cfg('brand', []);
$demo  = demo_image();

require __DIR__ . '/includes/header.php';
?>

<main id="main">

  <!-- ===== Hero ===== -->
  <section class="hero">
    <div class="hero-container">
      <span class="hero-badge"><?= e(t('hero.badge')) ?></span>
      <h1 class="hero-title"><?= e(t('hero.title_line1')) ?><br /><?= e(t('hero.title_line2')) ?></h1>
      <p class="hero-desc"><?= e(t('hero.desc')) ?></p>

      <div class="hero-actions">
        <a class="btn btn-primary" href="<?= e(url('manual.php')) ?>">
          <?= icon('book') ?><span><?= e(t('cta.manual')) ?></span>
        </a>
        <a class="btn btn-cloud" href="<?= e(url('enterprise.php')) ?>">
          <?= icon('shield') ?><span><?= e(t('cta.enterprise')) ?></span>
        </a>
      </div>

      <div class="hero-demo">
        <img class="demo-image" src="<?= e($demo) ?>" alt="<?= e(t('demo.aria')) ?>" />
      </div>
    </div>
  </section>

  <!-- ===== 核心能力 ===== -->
  <section class="section" id="features">
    <div class="section-container">
      <?php section_heading('capabilities.title', 'capabilities.subtitle'); ?>
      <div class="features-grid">
        <?php foreach ((array) content('capabilities', []) as $item): ?>
          <?= capability_card((array) $item) ?>
        <?php endforeach; ?>
      </div>
    </div>
  </section>

  <!-- ===== 企业级管控 ===== -->
  <section class="section enterprise" id="enterprise">
    <div class="section-container">
      <div style="text-align:center">
        <span class="eyebrow-pill"><?= icon('shield') ?><?= e(t('nav.enterprise')) ?></span>
      </div>
      <?php section_heading('enterprise.title', 'enterprise.subtitle'); ?>

      <div class="enterprise-banner reveal">
        <?= icon('shield') ?>
        <div>
          <h3><?= e(t('enterprise.banner_title')) ?></h3>
          <p><?= e(t('enterprise.banner_desc')) ?></p>
        </div>
      </div>

      <div class="features-grid">
        <?php foreach ((array) content('enterprise', []) as $item): ?>
          <?= enterprise_card((array) $item) ?>
        <?php endforeach; ?>
      </div>

      <div style="text-align:center;margin-top:40px">
        <a class="btn btn-cloud" href="<?= e(url('enterprise.php')) ?>">
          <?= icon('arrow') ?><span><?= e(t('cta.enterprise')) ?></span>
        </a>
      </div>
    </div>
  </section>

  <!-- ===== 模型与通道 ===== -->
  <section class="section" id="integrations">
    <div class="section-container">
      <?php section_heading('integrations.title', 'integrations.subtitle'); ?>

      <div class="integration-group">
        <h3 class="integration-label"><?= e(t('integrations.label_models')) ?></h3>
        <div class="tags-container">
          <?php foreach ((array) cfg('model_vendors', []) as $vendor): ?>
            <?= tag((string) $vendor, 'model') ?>
          <?php endforeach; ?>
        </div>
      </div>

      <div class="integration-group">
        <h3 class="integration-label"><?= e(t('integrations.label_channels')) ?></h3>
        <div class="tags-container">
          <?php foreach ((array) cfg('channels', []) as $channel): ?>
            <?= tag(t('integrations.channels.' . $channel), 'channel') ?>
          <?php endforeach; ?>
        </div>
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
