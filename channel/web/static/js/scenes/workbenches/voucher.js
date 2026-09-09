/* scenes/workbenches/voucher.js — 凭证辅助工作台（voucher）。
 *
 * 复用 base.js 的通用渲染（子场景面板/表单/导入/ERP 元数据）。凭证场景的子
 * 场景均含 ``params`` 表单（智能分录生成、凭证模板下载），由 base.js 覆盖。
 */
(function () {
    'use strict';
    if (window.ScenesRegistry && window.ScenesRegistry.hasRenderer('base')) {
        window.ScenesRegistry.registerRenderer('voucher', function (scene) {
            return window.ScenesRegistry.render('base', scene);
        });
    }
})();
