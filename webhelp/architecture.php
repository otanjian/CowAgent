<?php
declare(strict_types=1);

require_once __DIR__ . '/includes/bootstrap.php';

$pageTitle       = t('meta.title_architecture') . ' · ' . (string) cfg('brand.name');
$pageDescription = t('architecture.subtitle');

require __DIR__ . '/includes/header.php';
?>

<main id="main">

  <?php page_hero('meta.title_architecture', 'architecture.subtitle'); ?>

  <!-- ===== 分层结构 ===== -->
  <section class="section">
    <div class="section-container">
      <h2 class="section-title section-title--left"><?= e(t('architecture.layers_title')) ?></h2>
      <div class="stack-sm" style="margin-top:28px">
        <?php foreach ((array) content('arch_layers', []) as $item): ?>
          <?php
            $id    = (string) $item['id'];
            $pills = t_list('architecture.layers.' . $id . '.items');
          ?>
          <article class="layer-row reveal">
            <span class="feature-icon"><?= icon((string) $item['icon']) ?></span>
            <div>
              <h3>
                <?= e(t('architecture.layers.' . $id . '.title')) ?>
              </h3>
              <p><?= e(t('architecture.layers.' . $id . '.desc')) ?></p>
              <?php if ($pills !== []): ?>
                <div class="pill-list" style="margin-top:14px">
                  <?php foreach ($pills as $pill): ?>
                    <span class="pill"><?= icon('check') ?><?= e($pill) ?></span>
                  <?php endforeach; ?>
                </div>
              <?php endif; ?>
            </div>
          </article>
        <?php endforeach; ?>
      </div>
    </div>
  </section>

  <!-- ===== 请求流转 ===== -->
  <section class="section section--soft">
    <div class="section-container">
      <?php section_heading('architecture.flow_title', 'architecture.flow_subtitle'); ?>
      <div class="governance-flow">
        <?php foreach ((array) content('arch_flow', []) as $index => $item): ?>
          <?php $id = (string) $item['id']; ?>
          <article class="flow-card reveal">
            <span class="flow-num"><?= e((string) ($index + 1)) ?></span>
            <h4><?= e(t('architecture.flow.' . $id . '.title')) ?></h4>
            <p><?= e(t('architecture.flow.' . $id . '.desc')) ?></p>
          </article>
        <?php endforeach; ?>
      </div>
    </div>
  </section>

  <!-- ===== 设计原则 ===== -->
  <section class="section">
    <div class="section-container">
      <h2 class="section-title section-title--left"><?= e(t('architecture.principles_title')) ?></h2>
      <p class="section-subtitle section-subtitle--left"><?= e(t('architecture.principles_subtitle')) ?></p>
      <div class="stack-sm">
        <?php foreach (t_list('architecture.principles') as $item): ?>
          <div class="note reveal">
            <?= icon('check-circle') ?>
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
          <a class="btn btn-primary" href="<?= e(url('enterprise.php')) ?>"><?= e(t('cta.enterprise')) ?></a>
          <a class="btn btn-cloud" href="<?= e(url('quickstart.php')) ?>"><?= e(t('cta.primary')) ?></a>
        </div>
      </div>
    </div>
  </section>

</main>

<?php require __DIR__ . '/includes/footer.php'; ?>
