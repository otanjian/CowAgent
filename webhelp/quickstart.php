<?php
declare(strict_types=1);

require_once __DIR__ . '/includes/bootstrap.php';

$pageTitle       = t('meta.title_quickstart') . ' · ' . (string) cfg('brand.name');
$pageDescription = t('quickstart.subtitle');

require __DIR__ . '/includes/header.php';
?>

<main id="main">

  <?php page_hero('meta.title_quickstart', 'quickstart.subtitle'); ?>

  <!-- ===== 安装命令 ===== -->
  <section class="section">
    <div class="section-container">
      <?= deploy_block() ?>

      <div style="max-width:640px;margin:32px auto 0" class="note reveal">
        <?= icon('shield') ?>
        <span><?= e(t('quickstart.port_note')) ?></span>
      </div>
    </div>
  </section>

  <!-- ===== 五步开始 ===== -->
  <section class="section section--soft">
    <div class="section-container">
      <?php section_heading('quickstart.steps_title', ''); ?>
      <div class="features-grid">
        <?php foreach ((array) content('quickstart_steps', []) as $index => $item): ?>
          <?php $id = (string) $item['id']; ?>
          <article class="flow-card reveal">
            <span class="flow-num"><?= e((string) ($index + 1)) ?></span>
            <h4><?= e(t('quickstart.steps.' . $id . '.title')) ?></h4>
            <p><?= e(t('quickstart.steps.' . $id . '.desc')) ?></p>
          </article>
        <?php endforeach; ?>
      </div>
    </div>
  </section>

  <!-- ===== 配置 ===== -->
  <section class="section">
    <div class="section-container">
      <?php section_heading('quickstart.access_title', 'quickstart.access_desc'); ?>
      <div class="split">
        <div class="stack">
          <h3 class="section-title section-title--left" style="font-size:18px">config.json</h3>
          <?= code_block('config.json', (string) cfg('config_sample')) ?>
        </div>
        <div class="stack">
          <h3 class="section-title section-title--left" style="font-size:18px"><?= e(t('quickstart.enterprise_title')) ?></h3>
          <p class="section-subtitle section-subtitle--left" style="margin-bottom:0"><?= e(t('quickstart.enterprise_desc')) ?></p>
          <?= code_block('bootstrap', (string) cfg('bootstrap_sample')) ?>
        </div>
      </div>
    </div>
  </section>

  <!-- ===== 命令速查 ===== -->
  <section class="section section--soft">
    <div class="section-container">
      <?php section_heading('cli.title', 'cli.subtitle'); ?>
      <div class="cmd-list">
        <?php foreach ((array) content('cli_commands', []) as $item): ?>
          <div class="cmd-row">
            <code><?= e((string) $item['cmd']) ?></code>
            <span><?= e(t('cli.commands.' . $item['id'])) ?></span>
          </div>
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
          <a class="btn btn-primary" href="<?= e(url('enterprise.php')) ?>"><?= e(t('cta.enterprise')) ?></a>
          <a class="btn btn-cloud" href="<?= e(url('features.php')) ?>"><?= e(t('nav.features')) ?></a>
        </div>
      </div>
    </div>
  </section>

</main>

<?php require __DIR__ . '/includes/footer.php'; ?>
