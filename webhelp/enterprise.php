<?php
declare(strict_types=1);

require_once __DIR__ . '/includes/bootstrap.php';

$pageTitle       = t('meta.title_enterprise') . ' · ' . (string) cfg('brand.name');
$pageDescription = t('enterprise.banner_desc');

require __DIR__ . '/includes/header.php';
?>

<main id="main">

  <?php page_hero('perm.title', 'perm.lead', [
    'breadcrumb'   => '',
    'title_accent' => 'perm.title_accent',
    'extra'        => perm_stats(),
  ]); ?>

  <!-- ===== 企业级权限管控 ===== -->
  <section class="section perm">
    <div class="section-container">

      <!-- 核心设计原则 -->
      <div class="perm-section">
        <div class="perm-section-head">
          <h2 class="perm-section-title"><?= e(t('perm.principles_label')) ?></h3>
          <p class="perm-section-desc"><?= e(t('perm.principles_desc')) ?></p>
        </div>
        <div class="perm-principles">
          <?php foreach ((array) content('perm_principles', []) as $item): ?>
            <?php $id = (string) $item['id']; ?>
            <article class="perm-principle reveal">
              <span class="perm-principle-icon"><?= icon((string) $item['icon']) ?></span>
              <h3 class="perm-principle-title"><?= e(t('perm.principles.' . $id . '.title')) ?></h4>
              <p class="perm-principle-desc"><?= e(t('perm.principles.' . $id . '.desc')) ?></p>
            </article>
          <?php endforeach; ?>
        </div>
      </div>

      <!-- 3.1.1 三级权限管控架构 -->
      <div class="perm-section">
        <div class="perm-section-head">
          <span class="perm-section-no"><?= e(t('perm.tiers_no')) ?></span>
          <h2 class="perm-section-title"><?= e(t('perm.tiers_title')) ?></h3>
          <p class="perm-section-desc"><?= e(t('perm.tiers_desc')) ?></p>
        </div>
        <?= perm_tiers() ?>
      </div>

      <!-- 3.1.2 资源授权流转机制 -->
      <div class="perm-section">
        <div class="perm-section-head">
          <span class="perm-section-no"><?= e(t('perm.flow_no')) ?></span>
          <h2 class="perm-section-title"><?= e(t('perm.flow_title')) ?></h3>
          <p class="perm-section-desc"><?= e(t('perm.flow_desc')) ?></p>
        </div>

        <?= perm_flow_panel() ?>

        <!-- 关键规则 -->
        <h3 class="perm-rules-title"><?= e(t('perm.rules_label')) ?></h4>
        <div class="perm-rules">
          <?php foreach (t_list('perm.rules') as $index => $rule): ?>
            <article class="perm-rule reveal">
              <span class="perm-rule-no"><?= e((string) ($index + 1)) ?></span>
              <h4 class="perm-rule-title"><?= e((string) ($rule['title'] ?? '')) ?></h4>
              <p class="perm-rule-desc"><?= e((string) ($rule['desc'] ?? '')) ?></p>
            </article>
          <?php endforeach; ?>
        </div>
      </div>

      <!-- 3.1.3 平台角色与职责 -->
      <div class="perm-section">
        <div class="perm-section-head">
          <span class="perm-section-no"><?= e(t('perm.roles_no')) ?></span>
          <h2 class="perm-section-title"><?= e(t('perm.roles_title')) ?></h3>
          <p class="perm-section-desc"><?= e(t('perm.roles_desc')) ?></p>
        </div>
        <?= perm_roles() ?>
      </div>

    </div>
  </section>

  <!-- ===== 管控链路 ===== -->
  <section class="section">
    <div class="section-container">
      <?php section_heading('enterprise.flow_title', 'enterprise.flow_subtitle'); ?>
      <div class="governance-flow">
        <?php foreach ((array) content('governance_flow', []) as $index => $item): ?>
          <?php $id = (string) $item['id']; ?>
          <article class="flow-card reveal">
            <span class="flow-num"><?= e((string) ($index + 1)) ?></span>
            <h4><?= e(t('enterprise.flow.' . $id . '.title')) ?></h4>
            <p><?= e(t('enterprise.flow.' . $id . '.desc')) ?></p>
          </article>
        <?php endforeach; ?>
      </div>
    </div>
  </section>

  <!-- ===== 九大模块 ===== -->
  <section class="section section--soft">
    <div class="section-container">
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
    </div>
  </section>

  <!-- ===== 管控台 ===== -->
  <section class="section">
    <div class="section-container">
      <?php section_heading('enterprise.console_title', 'enterprise.console_subtitle'); ?>
      <div class="features-grid features-grid--4">
        <?php foreach ((array) content('console_menus', []) as $item): ?>
          <?php $id = (string) $item['id']; ?>
          <article class="feature-card feature-card--compact reveal">
            <span class="feature-icon"><?= icon((string) $item['icon']) ?></span>
            <h3 class="feature-title"><?= e(t('enterprise.console.' . $id . '.title')) ?></h3>
            <p class="feature-desc"><?= e(t('enterprise.console.' . $id . '.desc')) ?></p>
          </article>
        <?php endforeach; ?>
      </div>
    </div>
  </section>

  <!-- ===== 设计原则 ===== -->
  <section class="section section--soft">
    <div class="section-container">
      <h2 class="section-title section-title--left"><?= e(t('enterprise.guarantee_title')) ?></h2>
      <p class="section-subtitle section-subtitle--left"><?= e(t('enterprise.guarantee_desc')) ?></p>

      <div class="stack-sm">
        <?php foreach (t_list('enterprise.guarantee') as $item): ?>
          <div class="note reveal">
            <?= icon('check-circle') ?>
            <span><?= e($item) ?></span>
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
          <a class="btn btn-primary" href="<?= e(url('quickstart.php')) ?>"><?= e(t('cta.primary')) ?></a>
          <a class="btn btn-secondary" href="<?= e(url('features.php')) ?>"><?= e(t('nav.features')) ?></a>
        </div>
      </div>
    </div>
  </section>

</main>

<?php require __DIR__ . '/includes/footer.php'; ?>
