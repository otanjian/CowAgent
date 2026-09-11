/**
 * 容大AI 产品介绍站点交互：
 * 主题切换、移动端导航、代码标签页、复制、滚动显现。
 */
(function () {
  'use strict';

  var root = document.documentElement;
  var THEME_KEY = 'webhelp-theme';

  /* ===== 主题切换 ===== */
  // 站点默认深色（与上游一致），仅在显式选择后才跟随 data-theme
  function currentTheme() {
    return root.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  }

  var themeToggle = document.getElementById('themeToggle');
  if (themeToggle) {
    themeToggle.addEventListener('click', function () {
      var next = currentTheme() === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      try {
        localStorage.setItem(THEME_KEY, next);
      } catch (err) { /* 忽略 */ }
    });
  }

  /* ===== 移动端导航 ===== */
  var navToggle = document.getElementById('navToggle');
  var navLinks = document.getElementById('navLinks');

  function closeNav() {
    if (!navLinks) return;
    navLinks.classList.remove('is-open');
    if (navToggle) navToggle.setAttribute('aria-expanded', 'false');
  }

  if (navToggle && navLinks) {
    navToggle.addEventListener('click', function () {
      var open = navLinks.classList.toggle('is-open');
      navToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });

    navLinks.addEventListener('click', function (event) {
      if (event.target.closest('a')) closeNav();
    });

    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') closeNav();
    });

    window.addEventListener('resize', function () {
      if (window.innerWidth > 860) closeNav();
    });
  }

  /* ===== 代码标签页 ===== */
  document.querySelectorAll('.code-tabs').forEach(function (group) {
    var block = group.closest('.code-block');
    if (!block) return;

    group.addEventListener('click', function (event) {
      var tab = event.target.closest('.code-tab');
      if (!tab) return;

      var target = tab.getAttribute('data-code-tab');

      group.querySelectorAll('.code-tab').forEach(function (item) {
        item.classList.toggle('is-active', item === tab);
      });

      block.querySelectorAll('.code-content').forEach(function (panel) {
        panel.classList.toggle('is-active', panel.getAttribute('data-code-panel') === target);
      });
    });
  });

  /* ===== 复制代码 ===== */
  document.querySelectorAll('.copy-btn').forEach(function (button) {
    button.addEventListener('click', function () {
      var block = button.closest('.code-block');
      if (!block) return;

      var panel = block.querySelector('.code-content.is-active') || block.querySelector('.code-content');
      if (!panel) return;

      var text = panel.innerText.replace(/^\$\s?/gm, '').replace(/^>\s?/gm, '');
      var done = button.getAttribute('data-copy-done') || 'Copied!';
      var label = button.getAttribute('data-copy-label') || button.textContent;

      function flash(ok) {
        button.textContent = ok ? done : 'Error';
        button.classList.toggle('is-copied', ok);
        window.setTimeout(function () {
          button.textContent = label;
          button.classList.remove('is-copied');
        }, 1800);
      }

      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () { flash(true); }, function () { flash(false); });
        return;
      }

      var area = document.createElement('textarea');
      area.value = text;
      area.setAttribute('readonly', '');
      area.style.position = 'fixed';
      area.style.opacity = '0';
      document.body.appendChild(area);
      area.select();
      try {
        flash(document.execCommand('copy'));
      } catch (err) {
        flash(false);
      }
      document.body.removeChild(area);
    });
  });

  /* ===== 滚动显现 ===== */
  var revealItems = document.querySelectorAll('.reveal');
  if (revealItems.length) {
    if ('IntersectionObserver' in window) {
      var observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add('is-visible');
            observer.unobserve(entry.target);
          }
        });
      }, { rootMargin: '0px 0px -8% 0px', threshold: 0.06 });

      revealItems.forEach(function (item) {
        observer.observe(item);
      });
    } else {
      revealItems.forEach(function (item) {
        item.classList.add('is-visible');
      });
    }
  }
})();
