/* scenes/workbenches/scheduling.js — 生产计划/排产工作台（scheduling）。
 *
 * 复用 base.js 通用渲染。排产场景子场景以产能/物料需求规划为主，部分含
 * ``import_config``（订单/产能表）与 ``indicators``，由 base.js 覆盖。
 */
(function () {
    'use strict';
    if (window.ScenesRegistry && window.ScenesRegistry.hasRenderer('base')) {
        window.ScenesRegistry.registerRenderer('scheduling', function (scene) {
            return window.ScenesRegistry.render('base', scene);
        });
    }
})();
