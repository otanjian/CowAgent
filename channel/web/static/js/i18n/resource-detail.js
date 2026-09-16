// Per-domain i18n namespace: resource-detail
// The 工具与技能 detail component (change unify-console-by-data-scope, task 5.5):
// a member's personal parameters for a tool or a skill are edited here, in the
// same panel as the resource itself, because the standalone 我的资源 page is
// retired. Kept in its own namespace so this change's key edits do not collide
// with the console cores' literals (task 8.5 of
// fork-decoupling-and-tenant-hardening split them for exactly this reason).
(function () {
    'use strict';
    var registry = window.__cowI18N__ = window.__cowI18N__ || {};
    registry["resource-detail"] = {
        "zh": {
            "resource_detail_kind_tool": "工具",
            "resource_detail_kind_skill": "技能",
            "resource_detail_skill_definition": "查看定义",
            "resource_detail_personal_title": "个人参数",
            "resource_detail_personal_desc": "仅你本人使用，用于覆盖该资源的调用参数",
            "resource_detail_personal_params": "参数（JSON 对象，可为空）",
            "resource_detail_personal_secret_label": "密钥 / 令牌（可选）",
            "resource_detail_personal_secret": "填写后保存，仅你本人可用",
            "resource_detail_personal_secret_saved": "已保存密钥，留空则保持不变",
            "resource_detail_personal_readonly": "该资源的使用授权已收回，已保存的参数只能查看或清除",
            "resource_detail_personal_saved": "已保存",
            "resource_detail_personal_cleared": "已清除",
            "resource_detail_personal_invalid_json": "参数必须是合法的 JSON 对象",
            "resource_detail_personal_error": "操作失败，请稍后再试"
        },
        "zh-Hant": {
            "resource_detail_kind_tool": "工具",
            "resource_detail_kind_skill": "技能",
            "resource_detail_skill_definition": "檢視定義",
            "resource_detail_personal_title": "個人參數",
            "resource_detail_personal_desc": "僅你本人使用，用於覆寫該資源的呼叫參數",
            "resource_detail_personal_params": "參數（JSON 物件，可為空）",
            "resource_detail_personal_secret_label": "密鑰 / 權杖（可選）",
            "resource_detail_personal_secret": "填寫後儲存，僅你本人可用",
            "resource_detail_personal_secret_saved": "已儲存密鑰，留空則保持不變",
            "resource_detail_personal_readonly": "該資源的使用授權已收回，已儲存的參數只能檢視或清除",
            "resource_detail_personal_saved": "已儲存",
            "resource_detail_personal_cleared": "已清除",
            "resource_detail_personal_invalid_json": "參數必須是合法的 JSON 物件",
            "resource_detail_personal_error": "操作失敗，請稍後再試"
        },
        "en": {
            "resource_detail_kind_tool": "Tool",
            "resource_detail_kind_skill": "Skill",
            "resource_detail_skill_definition": "View definition",
            "resource_detail_personal_title": "My parameters",
            "resource_detail_personal_desc": "Only yours, used to override how this resource is called",
            "resource_detail_personal_params": "Parameters (JSON object, may be empty)",
            "resource_detail_personal_secret_label": "Secret / token (optional)",
            "resource_detail_personal_secret": "Saved for your use only",
            "resource_detail_personal_secret_saved": "A secret is saved; leave blank to keep it",
            "resource_detail_personal_readonly": "Your use grant for this resource was withdrawn; the saved parameters can only be read or cleared",
            "resource_detail_personal_saved": "Saved",
            "resource_detail_personal_cleared": "Cleared",
            "resource_detail_personal_invalid_json": "Parameters must be a valid JSON object",
            "resource_detail_personal_error": "Failed. Please try again later."
        }
    };
})();
