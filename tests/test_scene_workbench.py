"""tests for scenes/api_workbench.py — 场景工作台文件导入。

覆盖：成功导入（text/base64）、缺参、场景不存在、场景不支持导入、扩展名
非法、空内容、workbench 元数据剔除连接凭据。
"""
import base64
import contextlib
import json

import pytest
from types import SimpleNamespace
from unittest import mock

from scenes import api_workbench
from scenes.workbenches import base as wb_base


# ---------------------------------------------------------------------------
# 扩展名校验
# ---------------------------------------------------------------------------
def test_validate_import_extension_accepts_allowed():
    assert wb_base.validate_import_extension(None, "report.xlsx") == ".xlsx"
    assert wb_base.validate_import_extension({}, "data.csv") == ".csv"


def test_validate_import_extension_respects_accept():
    cfg = {"enabled": True, "accept": ".xlsx,.csv"}
    assert wb_base.validate_import_extension(cfg, "a.xlsx") == ".xlsx"
    assert wb_base.validate_import_extension(cfg, "a.CSV") == ".csv"


def test_validate_import_extension_rejects_unsupported():
    with pytest.raises(ValueError):
        wb_base.validate_import_extension({}, "a.exe")
    with pytest.raises(ValueError):
        wb_base.validate_import_extension({"accept": ".xlsx"}, "a.csv")
    with pytest.raises(ValueError):
        wb_base.validate_import_extension(None, "no_ext")


def test_validate_import_extension_requires_filename():
    with pytest.raises(ValueError):
        wb_base.validate_import_extension(None, "   ")
    with pytest.raises(ValueError):
        wb_base.validate_import_extension(None, "")


# ---------------------------------------------------------------------------
# 元数据负载剔除凭据
# ---------------------------------------------------------------------------
def test_build_workbench_payload_strips_connection_credentials():
    import_config = {
        "enabled": True, "accept": ".xlsx", "description": "上传",
        "template_headers": ["A", "B"], "conn_str": "mysql://secret@host/db",
    }
    erp_config = {
        "enabled": True, "systems": ["u9", "sap"], "username": "admin",
        "password": "p@55", "endpoint": "https://erp.example/api",
    }
    payload = wb_base.build_workbench_payload(import_config, erp_config)
    assert payload["import"]["enabled"] is True
    assert payload["import"]["accept"] == ".xlsx"
    assert "conn_str" not in payload["import"]
    assert payload["erp"]["systems"] == ["u9", "sap"]
    assert "password" not in payload["erp"]
    assert "endpoint" not in payload["erp"]


def test_build_workbench_payload_handles_none():
    payload = wb_base.build_workbench_payload(None, None)
    assert payload["import"] is None
    assert payload["erp"] is None


# ---------------------------------------------------------------------------
# HTTP 处理器
# ---------------------------------------------------------------------------
def _scene_with_import(scene_id="report_upload_parse"):
    return {"id": scene_id, "name": "报表上传解析",
            "import_config": {"enabled": True, "accept": ".xlsx,.csv",
                              "description": "上传财务文件"},
            "erp_config": {"enabled": True, "systems": ["kingdee", "sap"]}}


def _handler_patches(payload, tmp_path=None, find_scene=None):
    """Common patches for driving the import handler directly.

    The handler now runs inside the request scope and funnels its write through
    the unified management-write gate (group 4). A unit test of the handler's
    business logic stubs those two seams out and pins the workspace root, so it
    never needs a live ``web.ctx`` or an identity database; the scope/permission
    and CSRF behaviour itself is covered by ``test_scenes_tenant_scope.py``.
    """
    @contextlib.contextmanager
    def fake_scope():
        yield SimpleNamespace(tenant_id="tnt_test",
                              permissions={"chat.use"})

    patchers = [
        mock.patch("channel.web.web_channel._require_auth"),
        mock.patch("scenes.api_workbench._require_management_write"),
        mock.patch("scenes.api_workbench._db_scope", fake_scope),
        mock.patch("scenes.api_workbench._require_chat_use"),
        mock.patch("channel.web.web_channel.web.header"),
        mock.patch("channel.web.web_channel.web.data",
                   return_value=json.dumps(payload).encode()),
        mock.patch("scenes.service.find_scene", side_effect=find_scene),
    ]
    if tmp_path is not None:
        patchers.append(mock.patch("scenes.api_workbench._resolve_workspace_root",
                                   return_value=str(tmp_path)))
    return patchers


def _call_import(payload, tmp_path, scene=None):
    def fake_find_scene(sid, *a, **k):
        return (_scene_with_import() if scene is None else scene), False

    patchers = _handler_patches(payload, tmp_path, fake_find_scene)
    for p in patchers:
        p.start()
    try:
        return json.loads(api_workbench.SceneWorkbenchImportHandler().POST())
    finally:
        for p in reversed(patchers):
            p.stop()


def test_import_success_text(tmp_path):
    out = _call_import(
        {"scene_id": "report_upload_parse", "filename": "data.csv",
         "content": "a,b\n1,2", "content_encoding": "text"}, tmp_path
    )
    assert out["status"] == "success"
    assert out["filename"] == "data.csv"
    assert out["size"] > 0
    assert out["workbench"]["erp"]["systems"] == ["kingdee", "sap"]


def test_import_success_base64(tmp_path):
    b64 = base64.b64encode(b"hello").decode()
    out = _call_import(
        {"scene_id": "report_upload_parse", "filename": "data.xlsx",
         "content": b64, "content_encoding": "base64"}, tmp_path
    )
    assert out["status"] == "success"
    assert out["size"] == 5
    with open(out["path"], "rb") as f:
        assert f.read() == b"hello"


def test_import_rejects_missing_params(tmp_path):
    out = _call_import({"filename": "a.csv", "content": "x"}, tmp_path)
    assert out["status"] == "error"
    assert "required" in out["message"]


def test_import_rejects_unknown_scene(tmp_path):
    def fake_find_scene(sid, *a, **k):
        return None, False
    payload = {"scene_id": "nope", "filename": "a.csv", "content": "x"}
    patchers = _handler_patches(payload, find_scene=fake_find_scene)
    for p in patchers:
        p.start()
    try:
        out = json.loads(api_workbench.SceneWorkbenchImportHandler().POST())
    finally:
        for p in reversed(patchers):
            p.stop()
    assert out["status"] == "error"
    assert "not found" in out["message"]


def test_import_rejects_scene_without_import(tmp_path):
    scene = {"id": "s", "name": "x", "import_config": {"enabled": False}}
    out = _call_import(
        {"scene_id": "s", "filename": "a.csv", "content": "x"}, tmp_path,
        scene=scene,
    )
    assert out["status"] == "error"
    assert "does not support import" in out["message"]


def test_import_rejects_bad_extension(tmp_path):
    out = _call_import(
        {"scene_id": "report_upload_parse", "filename": "a.exe",
         "content": "x"}, tmp_path
    )
    assert out["status"] == "error"
    assert "unsupported extension" in out["message"]


def test_import_rejects_empty_content(tmp_path):
    out = _call_import(
        {"scene_id": "report_upload_parse", "filename": "a.csv",
         "content": "", "content_encoding": "text"}, tmp_path
    )
    assert out["status"] == "error"
    assert "content" in out["message"]
