/* scenes/workbenches/sap_analysis.js — SAP 数据分析工作台（sap_analysis）。
 *
 * 复用 base.js 通用渲染。SAP 场景以数据查询/指标展示为主，由 base.js 覆盖。
 * 注意：本场景外部依赖（SAP 连接）在 CowAgent 不可用，仅承载元数据展示与
 * 查询入口，不携带真实连接凭据。
 */
(function () {
    'use strict';
    if (window.ScenesRegistry && window.ScenesRegistry.hasRenderer('base')) {
        window.ScenesRegistry.registerRenderer('sap_analysis', function (scene) {
            return window.ScenesRegistry.render('base', scene);
        });
    }
})();
