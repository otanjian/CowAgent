// Per-domain i18n namespace: appearance
// Split out of console.js (change fork-decoupling-and-tenant-hardening,
// task 8.5) so fork and upstream key edits no longer collide in one literal.
// console.js merges every registered namespace into the single lookup table.
(function () {
    'use strict';
    var registry = window.__cowI18N__ = window.__cowI18N__ || {};
    registry["appearance"] = {
        "zh": {
            "appearance_title": "外观",
            "appearance_close": "关闭外观设置",
            "appearance_palette": "配色方案",
            "appearance_business": "商务青蓝",
            "appearance_slate": "深蓝侧栏",
            "appearance_classic": "经典配色",
            "appearance_recommended": "推荐",
            "appearance_mode": "明暗模式",
            "appearance_light": "浅色",
            "appearance_dark": "深色",
            "appearance_system": "跟随系统",
            "appearance_reset": "恢复默认",
            "appearance_instant": "选择后立即生效",
            "appearance_resolved_light": "当前显示：浅色",
            "appearance_resolved_dark": "当前显示：深色",
            "appearance_scope": "仅在当前浏览器生效，此浏览器中的不同账号共用外观设置。",
            "appearance_storage_failed": "仅本页生效，刷新后可能恢复之前设置。"
        },
        "zh-Hant": {
            "appearance_title": "外觀",
            "appearance_close": "關閉外觀設定",
            "appearance_palette": "配色方案",
            "appearance_business": "商務青藍",
            "appearance_slate": "深藍側欄",
            "appearance_classic": "經典配色",
            "appearance_recommended": "推薦",
            "appearance_mode": "明暗模式",
            "appearance_light": "淺色",
            "appearance_dark": "深色",
            "appearance_system": "跟隨系統",
            "appearance_reset": "恢復預設",
            "appearance_instant": "選擇後立即生效",
            "appearance_resolved_light": "目前顯示：淺色",
            "appearance_resolved_dark": "目前顯示：深色",
            "appearance_scope": "僅在目前瀏覽器生效，此瀏覽器中的不同帳號共用外觀設定。",
            "appearance_storage_failed": "僅本頁生效，重新整理後可能恢復先前設定。"
        },
        "en": {
            "appearance_title": "Appearance",
            "appearance_close": "Close appearance settings",
            "appearance_palette": "Color palette",
            "appearance_business": "Business blue",
            "appearance_slate": "Slate sidebar",
            "appearance_classic": "Classic",
            "appearance_recommended": "Recommended",
            "appearance_mode": "Appearance mode",
            "appearance_light": "Light",
            "appearance_dark": "Dark",
            "appearance_system": "System",
            "appearance_reset": "Restore defaults",
            "appearance_instant": "Changes apply immediately",
            "appearance_resolved_light": "Currently using light mode",
            "appearance_resolved_dark": "Currently using dark mode",
            "appearance_scope": "Saved in this browser only. Accounts using this browser share appearance settings.",
            "appearance_storage_failed": "Applied to this page only. Reloading may restore previous settings."
        }
    };
})();
