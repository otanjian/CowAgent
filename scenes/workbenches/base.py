"""工作台基础组件。

场景工作台（Workbench）提供子场景面板 + 功能模块卡片 + 文件导入 +
ERP 元数据展示，v1 以纯前端面板为主，本模块仅承载共享底座（类型解析、
文件导入校验），不依赖 ERP 真实连接与凭据。
"""
from typing import Optional


# 支持的文件导入扩展名（按子场景 ``import_config.accept`` 校验，空表示不限）。
# 文本/CSV/Excel/PDF/Word 由前端按 base64/文本提交后端落盘。
ALLOWED_EXTENSIONS = {
    ".xlsx", ".xls", ".xlsm", ".csv", ".pdf", ".docx", ".doc", ".txt", ".json",
}


def validate_import_extension(import_config: Optional[dict], filename: str) -> str:
    """根据子场景 ``import_config.accept`` 校验文件名扩展名。

    返回规范化后的扩展名（小写、含点）；扩展名不在允许集内时抛出
    ``ValueError``。``import_config`` 缺失或 ``accept`` 为空表示不限（仅受
    全局 ``ALLOWED_EXTENSIONS`` 约束）。
    """
    name = (filename or "").strip()
    if not name:
        raise ValueError("filename is required")
    dot = name.rfind(".")
    if dot <= 0:
        raise ValueError(f"unsupported file: {name}")
    ext = name[dot:].lower()
    allow = set(ALLOWED_EXTENSIONS)
    if import_config and isinstance(import_config, dict):
        accept = import_config.get("accept")
        if accept:
            allow = set(
                a.strip().lower() if a.strip().startswith(".") else "." + a.strip().lower()
                for a in str(accept).split(",")
                if a.strip()
            )
    if ext not in allow:
        raise ValueError(f"unsupported extension: {ext}")
    return ext


def build_workbench_payload(import_config: Optional[dict], erp_config: Optional[dict]) -> dict:
    """构造工作台元数据负载（供前端渲染），不携带真实连接凭据。

    - ``import_config``：保留 ``enabled/accept/description/template_headers``。
    - ``erp_config``：保留 ``enabled/systems``，剔除任何连接串字段。
    """
    payload = {"import": None, "erp": None}

    if import_config and isinstance(import_config, dict):
        payload["import"] = {
            "enabled": bool(import_config.get("enabled")),
            "accept": import_config.get("accept", ""),
            "description": import_config.get("description", ""),
            "template_headers": import_config.get("template_headers", []),
        }

    if erp_config and isinstance(erp_config, dict):
        # 仅保留 systems 展示信息，绝不透传连接配置/凭据。
        payload["erp"] = {
            "enabled": bool(erp_config.get("enabled")),
            "systems": erp_config.get("systems", []),
        }

    return payload
