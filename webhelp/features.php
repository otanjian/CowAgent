<?php
declare(strict_types=1);

require_once __DIR__ . '/includes/bootstrap.php';

$pageTitle       = t('meta.title_features') . ' · ' . (string) cfg('brand.name');
$pageDescription = t('capabilities.subtitle');

require __DIR__ . '/includes/header.php';
?>

<main id="main">

  <?php page_hero('meta.title_features', 'capabilities.subtitle'); ?>

  <!-- ===== 核心能力 ===== -->
  <section class="section">
    <div class="section-container">
      <div class="features-grid">
        <?php foreach ((array) content('capabilities', []) as $item): ?>
          <?= capability_card((array) $item) ?>
        <?php endforeach; ?>
      </div>
    </div>
  </section>

  <!-- ===== 内置工具 ===== -->
  <section class="section section--soft">
    <div class="section-container">
      <?php section_heading('tools.title', 'tools.subtitle'); ?>
      <div class="features-grid">
        <?php foreach ((array) content('tools', []) as $item): ?>
          <?php $id = (string) $item['id']; ?>
          <article class="feature-card reveal">
            <span class="feature-icon"><?= icon((string) $item['icon']) ?></span>
            <h3 class="feature-title"><?= e(t('tools.items.' . $id . '.title')) ?></h3>
            <p class="feature-desc"><?= e(t('tools.items.' . $id . '.desc')) ?></p>
          </article>
        <?php endforeach; ?>
      </div>
    </div>
  </section>

  <!-- ===== 命令系统 ===== -->
  <section class="section">
    <div class="section-container">
      <?php section_heading('cli.title', 'cli.subtitle'); ?>
      <div class="split">
        <div>
          <h3 class="section-title section-title--left" style="font-size:20px"><?= e(t('cli.cli_title')) ?></h3>
          <div class="cmd-list" style="margin-top:18px">
            <?php foreach ((array) content('cli_commands', []) as $item): ?>
              <div class="cmd-row">
                <code><?= e((string) $item['cmd']) ?></code>
                <span><?= e(t('cli.commands.' . $item['id'])) ?></span>
              </div>
            <?php endforeach; ?>
          </div>
        </div>

        <div>
          <h3 class="section-title section-title--left" style="font-size:20px"><?= e(t('cli.slash_title')) ?></h3>
          <div class="cmd-list" style="margin-top:18px">
            <?php foreach ((array) content('slash_commands', []) as $item): ?>
              <div class="cmd-row">
                <code><?= e((string) $item['cmd']) ?></code>
                <span><?= e(t('cli.slash.' . $item['id'])) ?></span>
              </div>
            <?php endforeach; ?>
          </div>
        </div>
      </div>
    </div>
  </section>

  <!-- ===== 常见问题 ===== -->
  <section class="section section--soft" id="faq">
    <div class="section-container">
      <?php section_heading('faq.title', 'faq.subtitle'); ?>
      <div class="faq">
        <?php foreach ((array) content('faq', []) as $key): ?>
          <details class="faq-item reveal">
            <summary>
              <span><?= e(t('faq.items.' . $key . '.q')) ?></span>
              <?= icon('arrow') ?>
            </summary>
            <p><?= e(t('faq.items.' . $key . '.a')) ?></p>
          </details>
        <?php endforeach; ?>
      </div>
    </div>
  </section>

  <!-- ===== CTA ===== -->
  <section class="section">
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
