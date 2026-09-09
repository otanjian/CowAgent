/* scenes/workbenches/financial_audit.js — 财务报表审查工作台（financial_audit）。
 *
 * 复用 base.js 通用渲染。审查场景子场景以「报表上传解析/勾稽关系审查」为主，
 * 均含 ``import_config`` 与 ``indicators``，由 base.js 覆盖。
 */
(function () {
    'use strict';
    if (window.ScenesRegistry && window.ScenesRegistry.hasRenderer('base')) {
        window.ScenesRegistry.registerRenderer('financial_audit', function (scene) {
            return window.ScenesRegistry.render('base', scene);
        });
    }
})();
