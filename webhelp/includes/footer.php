<?php
declare(strict_types=1);

/**
 * 页面尾部：页脚 + 脚本。
 * 页脚仅保留品牌标识与版权行。
 */

$brand = (array) cfg('brand', []);
?>
  <footer class="footer">
    <div class="footer-container">
      <div class="footer-brand">
        <img src="<?= e(asset((string) ($brand['logo_dark'] ?? ''))) ?>" alt="<?= e((string) ($brand['name'] ?? '')) ?>" class="footer-logo logo-img--dark" />
        <img src="<?= e(asset((string) ($brand['logo_light'] ?? ''))) ?>" alt="" aria-hidden="true" class="footer-logo logo-img--light" />
      </div>

      <div class="footer-bottom">
        <p>© <?= e((string) date('Y')) ?> <?= e((string) ($brand['name'] ?? '')) ?> · <?= e(t('footer.copyright')) ?></p>
      </div>
    </div>
  </footer>

  <script src="<?= e(asset('assets/js/main.js')) ?>"></script>
</body>
</html>
