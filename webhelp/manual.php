<?php
declare(strict_types=1);

/**
 * 产品使用手册（截图与编号步骤为主的应用操作手册）。
 *
 * 每个主题一句话定位 + 若干编号步骤（目标、操作要点、真实界面截图）。
 * 结构与文案分离：主题 / 分组 / 步骤清单（id + 截图路径）在 includes/content.php
 * （manual_parts / manual_topics），全部文案在 lang/{zh,en}.php（manual.*），
 * 渲染助手在 includes/bootstrap.php（manual_nav / manual_step / manual_refs）。
 *
 * 手册只讲「怎么用」，不讲「怎么装」（安装部署见 quickstart.php）；步骤写界面上稳定的
 * 名称，需要原理与边界细节时深链既有的本地能力文档，不复制其正文。
 * 主题只收成员看得到、做得了的操作；截图取自当前版本真实界面，缺图由
 * tools/check-manual.php 在提交前拦下。
 */

require_once __DIR__ . '/includes/bootstrap.php';

$pageTitle       = t('manual.title') . ' · ' . (string) cfg('brand.name');
$pageDescription = t('manual.lead');

require __DIR__ . '/includes/header.php';

$topics = (array) content('manual_topics', []);
?>

<main id="main">

  <?php page_hero('manual.title', 'manual.lead', [
      'title_accent' => 'manual.title_accent',
      'extra'        => '<p class="manual-hint">' . icon('zoom') . e(t('manual.shot_hint')) . '</p>',
  ]); ?>

  <section class="section">
    <div class="section-container">
      <div class="doc-layout">

        <article class="doc-article manual-article">
          <?php foreach ($topics as $index => $item): ?>
            <?php
            $id    = (string) $item['id'];
            $steps = (array) ($item['steps'] ?? []);
            ?>
            <section class="manual-topic" id="manual-<?= e($id) ?>">
              <h2 class="manual-title">
                <span class="manual-title-num"><?= e((string) ($index + 1)) ?></span>
                <span class="manual-title-icon"><?= icon((string) $item['icon']) ?></span>
                <?= e(t('manual.topics.' . $id . '.title')) ?>
              </h2>

              <?php $lead = trim(t_opt('manual.topics.' . $id . '.lead')); ?>
              <?php if ($lead !== ''): ?>
                <p class="manual-lead"><?= e($lead) ?></p>
              <?php endif; ?>

              <div class="manual-steps">
                <?php foreach ($steps as $n => $step): ?>
                  <?= manual_step($id, (int) $n + 1, (array) $step) ?>
                <?php endforeach; ?>
              </div>

              <?= manual_refs($item) ?>
            </section>
          <?php endforeach; ?>
        </article>

        <aside class="doc-aside manual-aside">
          <?= manual_nav() ?>
        </aside>

      </div>
    </div>
  </section>

</main>

<?php require __DIR__ . '/includes/footer.php'; ?>
