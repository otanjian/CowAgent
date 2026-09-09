/* scenes/workbenches/quality_trace.js — 质量追溯工作台（quality_trace）。
 *
 * 复用 base.js 通用渲染。质量追溯场景子场景以追溯查询/批次管理为主，由
 * base.js 覆盖。
 */
(function () {
    'use strict';
    if (window.ScenesRegistry && window.ScenesRegistry.hasRenderer('base')) {
        window.ScenesRegistry.registerRenderer('quality_trace', function (scene) {
            return window.ScenesRegistry.render('base', scene);
        });
    }
})();
