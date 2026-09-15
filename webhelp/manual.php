<?php
declare(strict_types=1);

/**
 * 产品使用手册（视频为主的应用操作手册）。
 *
 * 每个主题一句话定位 + 若干视频条目（标题、时长、内容要点、视频位）。
 * 结构与文案分离：主题 / 分组 / 视频清单在 includes/content.php（manual_parts /
 * manual_topics），全部文案在 lang/{zh,en}.php（manual.*），渲染助手在
 * includes/bootstrap.php（manual_nav / manual_video / manual_refs / …）。
 *
 * 手册只讲「怎么用」，不讲「怎么装」（安装部署见 quickstart.php）；操作细节由视频
 * 承载，需要原理与边界细节时深链既有的本地能力文档，不复制其正文。
 * 视频录制完成后在 content.php 对应条目补 'src' 即由占位切换为播放器，无需改本页。
 */

require_once __DIR__ . '/includes/bootstrap.php';

$pageTitle       = t('manual.title') . ' · ' . (string) cfg('brand.name');
$pageDescription = t('manual.lead');

require __DIR__ . '/includes/header.php';

$topics = (array) content('manual_topics', []);
?>

<main id="main">

  <?php page_hero('manual.title', 'manual.lead', ['title_accent' => 'manual.title_accent']); ?>

  <section class="section">
    <div class="section-container">
      <div class="doc-layout">

        <article class="doc-article manual-article">
          <?php foreach ($topics as $index => $item): ?>
            <?php
            $id     = (string) $item['id'];
            $videos = (array) ($item['videos'] ?? []);
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

              <div class="manual-videos">
                <?php foreach ($videos as $video): ?>
                  <?= manual_video($id, (array) $video) ?>
                <?php endforeach; ?>
              </div>

              <?php if ($id === 'commands'): ?>
                <?= manual_commands() ?>
              <?php endif; ?>

              <?= manual_faq($id) ?>

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
