/* scenes/workbenches/tax.js — 财税/会计准则工作台（tax）。
 *
 * 复用 base.js 通用渲染。财税场景（finance-expert/finance-analysis）子场景以
 * 咨询向导/指标展示为主，由 base.js 覆盖。
 */
(function () {
    'use strict';
    if (window.ScenesRegistry && window.ScenesRegistry.hasRenderer('base')) {
        window.ScenesRegistry.registerRenderer('tax', function (scene) {
            return window.ScenesRegistry.render('base', scene);
        });
    }
})();
