<?php
declare(strict_types=1);

/**
 * 产品使用手册（应用操作手册）。
 *
 * 结构与文案分离：章节 / 分组 / 块清单在 includes/content.php（manual_parts /
 * manual_sections），全部文案在 lang/{zh,en}.php（manual.*），渲染助手在
 * includes/bootstrap.php（manual_nav / manual_block / manual_refs / …）。
 *
 * 手册只讲「怎么用」，不讲「怎么装」（安装部署见 quickstart.php）；需要原理与
 * 边界细节时深链既有的本地能力文档，不复制其正文。
 */

require_once __DIR__ . '/includes/bootstrap.php';

$pageTitle       = t('manual.title') . ' · ' . (string) cfg('brand.name');
$pageDescription = t('manual.lead');

require __DIR__ . '/includes/header.php';

$sections = (array) content('manual_sections', []);
?>

<main id="main">

  <?php page_hero('manual.title', 'manual.lead', ['title_accent' => 'manual.title_accent']); ?>

  <section class="section">
    <div class="section-container">
      <div class="doc-layout">

        <article class="doc-article manual-article">
          <?php foreach ($sections as $index => $item): ?>
            <?php
            $id     = (string) $item['id'];
            $blocks = (array) ($item['blocks'] ?? []);
            ?>
            <section class="manual-section" id="manual-<?= e($id) ?>">
              <h2 class="manual-title">
                <span class="manual-title-num"><?= e((string) ($index + 1)) ?></span>
                <span class="manual-title-icon"><?= icon((string) $item['icon']) ?></span>
                <?= e(t('manual.sections.' . $id . '.title')) ?>
              </h2>

              <?php $goal = trim(t_opt('manual.sections.' . $id . '.goal')); ?>
              <?php if ($goal !== ''): ?>
                <p class="manual-goal">
                  <span class="manual-block-label"><?= e(t('manual.goal_label')) ?></span>
                  <?= e($goal) ?>
                </p>
              <?php endif; ?>

              <?php foreach ($blocks as $blockId): ?>
                <?= manual_block($id, (string) $blockId) ?>
              <?php endforeach; ?>

              <?php if ($id === 'commands'): ?>
                <?= manual_commands() ?>
              <?php endif; ?>

              <?php if ($id === 'further'): ?>
                <?= manual_all_docs() ?>
              <?php endif; ?>

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
