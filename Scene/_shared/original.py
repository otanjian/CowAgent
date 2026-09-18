"""Load untouched source modules under an isolated namespace.

Only imports and resource locations are adapted. The source bytes on disk,
business functions, classes, algorithms and UI remain the OneAgent versions.
"""
import builtins
import importlib
import importlib.abc
import importlib.util
import json
import os
import sys
import threading
import types
import datetime

import web

from common.log import logger
from Scene.catalog import ROOT, skill_path
from Scene._shared import host

PREFIX = "Scene._original"
MODULES = {
    "scheduling": "production_plan/backend/api.py",
    "scheduler_engine": "production_plan/backend/scheduler_engine.py",
    "gantt_generator": "production_plan/backend/gantt_generator.py",
    "bom_tree": "production_plan/backend/bom_tree.py",
    "airbag": "production_scheduling_airbag/backend/api.py",
    "sap_api": "sap_data_analysis/backend/api.py",
}
for folder, names in {
    "_shared": ["WorkbenchUploadHandler", "WorkbenchParseExcelHandler", "ErpConnectionsHandler", "ErpConnectionsOptionsHandler"],
    "procurement_analysis": ["WorkbenchGenerateReportHandler", "ProcurementImportHandler", "ProcurementErpSyncHandler"],
    "finance_voucher": ["VoucherTemplateHandler"],
}.items():
    MODULES.update({name: f"{folder}/backend/{name}.py" for name in names})


def resource_join(*parts):
    path = os.path.join(*parts)
    normalized = path.replace("\\", "/")
    marker = "/skills/" if "/skills/" in normalized else "skills/"
    if marker in normalized:
        name, _, rest = normalized.split(marker, 1)[1].partition("/")
        try:
            # Preserve all normal workspace paths; relocate shipped skill assets.
            if not os.path.exists(path):
                return str(skill_path(name) / rest)
        except KeyError:
            pass
    if "/vendor/sap-adt-cli/" in normalized:
        return str(ROOT / "sap_data_analysis/vendor/sap-adt-cli" / normalized.split("/vendor/sap-adt-cli/", 1)[1])
    return path


_path = types.SimpleNamespace(**{k: getattr(os.path, k) for k in dir(os.path)})
_path.join = resource_join
_os = types.SimpleNamespace(**{k: getattr(os, k) for k in dir(os)})
_os.path = _path


def source_import(name, globals=None, locals=None, fromlist=(), level=0):
    if not level:
        if name == "os":
            return _os
        if name in ("channel.web.web_channel_utils", "auth.tenant_context"):
            return host
        if name == "channel.web.sap" or name.startswith("channel.web.sap."):
            return importlib.import_module(PREFIX + ".sap" + name[len("channel.web.sap"):])
        if name in ("agent.tools.scheduler.scheduler_engine", "agent.tools.scheduler.gantt_generator", "agent.tools.scheduler.bom_tree"):
            return load(name.rsplit(".", 1)[1])
    return builtins.__import__(name, globals, locals, fromlist, level)


class SourceLoader(importlib.abc.Loader):
    def __init__(self, path):
        self.path = path

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        if self.path is None:
            return
        scope = module.__dict__
        scope.update({"__builtins__": dict(vars(builtins), __import__=source_import),
                      "__file__": str(self.path), "os": _os, "json": json,
                      "datetime": datetime, "web": web, "logger": logger})
        scope.update({name: getattr(host, name) for name in dir(host)
                      if name.startswith(("_require_", "_get_", "get_current_"))})
        key = module.__name__.removeprefix(PREFIX + ".")
        if key in ("ErpConnectionsOptionsHandler", "ProcurementErpSyncHandler"):
            scope["ErpConnectionsHandler"] = load("ErpConnectionsHandler").ErpConnectionsHandler
        if key == "ProcurementErpSyncHandler":
            scope["SAPProviderFactory"] = load("sap").SAPProviderFactory
            scope["RfcDependencyError"] = load("sap.rfc_provider").RfcDependencyError
        exec(compile(self.path.read_bytes(), str(self.path), "exec"), scope)
        # Path-only overrides for the original modules' host filesystem layout.
        if key == "airbag":
            scope["_project_root"] = lambda: str(ROOT / "production_scheduling_airbag")
        elif key == "sap.data_exporter":
            scope["DataExporter"]._analysis_dir = lambda self: str(
                __import__("pathlib").Path(host._get_workspace_root()) / ".one/sap_analysis")


class SourceFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == PREFIX:
            return importlib.util.spec_from_loader(fullname, SourceLoader(None), is_package=True)
        if not fullname.startswith(PREFIX + "."):
            return None
        key = fullname[len(PREFIX) + 1:]
        package = key == "sap"
        relative = ("sap_data_analysis/backend/sap/" +
                    ("__init__.py" if package else key[4:].replace(".", "/") + ".py")) if key == "sap" or key.startswith("sap.") else MODULES.get(key)
        if relative is None:
            return None
        return importlib.util.spec_from_loader(fullname, SourceLoader(ROOT / relative), is_package=package)


_install_lock = threading.Lock()


def load(name):
    with _install_lock:
        if not any(isinstance(item, SourceFinder) for item in sys.meta_path):
            sys.meta_path.insert(0, SourceFinder())
    return importlib.import_module(PREFIX + "." + name)
