/* scenes/registry.js — 工作台分发映射（前端）。
 *
 * ``openSceneById`` 的分发映射（与 ``scenes/renderer.py`` 对应）。场景中心
 * 点击卡片 / ``/场景`` 选择器选择后，按场景类型分发到对应工作台渲染器；
 * 无专用渲染器（或未注册）时回退到通用激活流程（进入对话上下文）。
 *
 * 分发规则（与设计文档一致）：
 *   凭证/财务会计   → voucher
 *   财税/会计准则   → tax
 *   财务报表审查    → financial_audit
 *   SAP 数据分析   → sap_analysis
 *   质量追溯        → quality_trace
 *   生产计划/排产   → scheduling
 *   其余            → base（通用工作台 / 直接激活）
 *
 * 各专业工作台渲染器在 Phase 4 由 ``scenes/workbenches/*.js`` 通过
 * ``registerRenderer`` 注册。本文件只负责类型映射与渲染器登记，不触碰 DOM。
 */
(function () {
    'use strict';

    // skill_name -> workbench 类型（语义键 → 专业工作台，与 renderer.py 保持一致）。
    const SKILL_TO_WORKBENCH = {
        'finance-voucher': 'voucher',
        'finance-expert': 'tax',
        'finance-analysis': 'tax',
        'financial-report-audit': 'financial_audit',
        'sap-integration': 'sap_analysis',
        'quality-trace': 'quality_trace',
        'production-plan': 'scheduling',
        'pmc-scheduler-hmt-qd': 'scheduling',
    };

    // scene id -> workbench 类型（显式覆盖，优先于 skill_name 推断）。
    const SCENE_TO_WORKBENCH = {};

    // workbench 类型 -> 渲染器函数（由各 workbenches/*.js 注册）。
    const renderers = {};

    function resolveWorkbenchType(scene) {
        if (!scene) return 'base';
        const id = scene.id || '';
        if (SCENE_TO_WORKBENCH[id]) return SCENE_TO_WORKBENCH[id];
        return SKILL_TO_WORKBENCH[scene.skill_name || ''] || 'base';
    }

    function registerRenderer(type, renderFn) {
        if (type && typeof renderFn === 'function') renderers[type] = renderFn;
    }

    function hasRenderer(type) {
        return typeof renderers[type] === 'function';
    }

    function render(type, scene) {
        if (hasRenderer(type)) return renderers[type](scene);
        return false;
    }

    window.ScenesRegistry = {
        resolveWorkbenchType: resolveWorkbenchType,
        registerRenderer: registerRenderer,
        hasRenderer: hasRenderer,
        render: render,
    };
})();
