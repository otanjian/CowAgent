#!/usr/bin/env python3
"""Build the fork web module domain map (change tasks 2.2-2.4).

Domain rule:
  * shared handler  -> the upstream api/ module that defines it (mirror upstream's view split)
  * fork-only handler -> assigned by name prefix/suffix

Also decides the home of every fork-only helper: helpers whose names belong to a
view domain move with that view; cross-cutting authorization/scope helpers go to
``authorization``; generic request plumbing goes to ``common``.
"""
from __future__ import annotations
import os

import ast
import json
import subprocess
from collections import defaultdict
from typing import Dict, List, Set

import importlib.util

# Scratch directory for the intermediate maps these steps hand to each other.
# The migration is a pipeline (symbol map -> domain map -> emit), and its
# outputs are large enough to keep out of the tree; override to run elsewhere.
WORKDIR = os.environ.get("FORK_MIGRATION_WORKDIR", "/tmp")


def _work(name: str) -> str:
    return os.path.join(WORKDIR, name)

spec = importlib.util.spec_from_file_location(
    "afw", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "analyze_fork_web_symbols.py")
)
afw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(afw)

UPSTREAM_API_MODULES = [
    "agents", "auth", "channels", "chat", "config", "files", "knowledge",
    "logs", "memory", "models", "openai_compat", "pages", "scheduler",
    "sessions", "skills", "update", "workspace",
]

# fork-only handlers -> domain
FORK_ONLY_DOMAIN = {
    "Branding": "branding",
    "Memory": "memory",
    "_MemoryWriteHandler": "memory",
    "PersonalMemory": "memory",
    "PersonalChannel": "channels",
    "ProjectImport": "workspace",
}

# helper name -> domain, by intent (prefix/substring). Order matters: first match wins.
HELPER_DOMAIN_RULES: List[tuple] = [
    ("_branding", "branding"),
    ("_project_", "workspace"),
    ("_knowledge", "knowledge"),
    ("_scheduler", "scheduler"),
    ("_workspace", "workspace"),
    ("_visible_entries", "workspace"),
    ("_memory", "memory"),
    ("_personal_memory", "memory"),
    ("_personal_channel", "channels"),
    ("_channel_target", "channels"),
    ("_iter_tenant_agents", "agents"),
    ("_tenant_agent", "agents"),
    ("_agent_", "agents"),
    ("_adopt_created_agent", "agents"),
    ("_rollback_created_agent", "agents"),
    ("_creation_scope", "agents"),
    ("_skill", "skills"),
    ("_annotate_skill", "skills"),
    ("_filter_tool_catalog", "skills"),
    ("_attach_personal_states", "skills"),
    ("_chat_", "chat"),
    ("_authorize_chat", "chat"),
    ("_owned_chat", "chat"),
    ("_stream_identity", "chat"),
    ("_require_chat", "chat"),
    ("_session", "sessions"),
    ("_conversation_store_for", "sessions"),
    ("_drop_team_runtimes", "sessions"),
    ("_db_file", "files"),
    ("_serve_allowed_roots", "files"),
    ("_preview", "files"),
    ("_file_identity_scope", "files"),
    ("_uploads_identity_scope", "files"),
    ("_owner_of_db_path", "files"),
    ("_authorize_db_file_path", "files"),
    ("_db_path", "files"),
    ("_static_path_private_owner", "files"),
    ("_platform_file_root", "files"),
    ("_get_workspace_root", "common"),
    ("_get_preview_secret", "files"),
    ("_encode_dir_token", "files"),
    ("_decode_dir_token", "files"),
    ("_tenant_workspace_root", "files"),
    ("_redact_log", "logs"),
    ("_verify_oauth_callback", "auth"),
    ("_register_owner_scope", "auth"),
    ("_verified_auth_session_id", "auth"),
    ("_permission_mode_projection", "config"),
    ("_project_brand_name", "config"),
    ("_is_database_identity", "common"),
    ("_web_navigation_mode", "common"),
    ("_guard_not_database", "common"),
    ("_unavailable", "common"),
]

AUTHORIZATION_PREFIXES = (
    "_require_", "_authorize_", "_refuse_", "_raise_forbidden",
    "_raise_if_", "_dependency_is_granted", "_capability_name_list",
    "_resource_ids", "_resolved_skill", "_tenant_admin_owns_agent",
    "_private_agent_owned_by_another", "_db_scope", "_current_db_identity",
    "_authorized_model_codes", "_web_runtime_identity_snapshot",
    "_require_context",
)


def upstream_handler_domain() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for mod in UPSTREAM_API_MODULES:
        try:
            src = afw.git_show(f"origin/master:channel/web/api/{mod}.py")
        except subprocess.CalledProcessError:
            continue
        for name in afw.handler_names(src):
            out.setdefault(name, mod)
    # Handlers still in upstream's web_channel.py (URL table) / core/channel.py
    for path in ("channel/web/web_channel.py", "channel/web/core/channel.py"):
        try:
            src = afw.git_show(f"origin/master:{path}")
        except subprocess.CalledProcessError:
            continue
        from urllib.parse import urlparse  # noqa: F401  (unused; keeps import surface explicit)
        for name in afw.handler_names(src):
            out.setdefault(name, "pages")
    return out


def main() -> int:
    fork_src = open("channel/web/web_channel.py", encoding="utf-8").read()
    fork_syms = afw.module_level_symbols(fork_src)
    fork_handlers = afw.handler_names(fork_src)
    up_domain = upstream_handler_domain()

    up_helpers: Set[str] = set()
    up_handlers: Set[str] = set()
    for mod in UPSTREAM_API_MODULES:
        try:
            src = afw.git_show(f"origin/master:channel/web/api/{mod}.py")
        except subprocess.CalledProcessError:
            continue
        up_helpers |= set(afw.module_level_symbols(src))
        up_handlers |= afw.handler_names(src)
    for path in ("channel/web/web_channel.py", "channel/web/core/_common.py",
                 "channel/web/core/channel.py", "channel/web/core/providers.py",
                 "channel/web/core/template.py"):
        try:
            src = afw.git_show(f"origin/master:{path}")
        except subprocess.CalledProcessError:
            continue
        up_helpers |= set(afw.module_level_symbols(src))
        up_handlers |= afw.handler_names(src)
    base = afw.git_show("e5e2a52d:channel/web/web_channel.py")
    up_helpers |= set(afw.module_level_symbols(base))
    up_handlers |= afw.handler_names(base)

    handler_domain: Dict[str, str] = {}
    for h in sorted(fork_handlers):
        if h in up_domain:
            handler_domain[h] = up_domain[h]
            continue
        if h in FORK_ONLY_DOMAIN:
            handler_domain[h] = FORK_ONLY_DOMAIN[h]
            continue
        for prefix, dom in FORK_ONLY_DOMAIN.items():
            if h.startswith(prefix):
                handler_domain[h] = dom
                break
        else:
            handler_domain[h] = "misc"

    fork_only = sorted(
        s for s in fork_syms
        if s not in up_helpers and not s.startswith("__")
        and isinstance(fork_syms[s], (ast.FunctionDef, ast.ClassDef))
    )
    fork_only_helpers = [s for s in fork_only if not s.endswith("Handler")]

    helper_domain: Dict[str, str] = {}
    for h in fork_only_helpers:
        for prefix, dom in HELPER_DOMAIN_RULES:
            if h.startswith(prefix) or h == prefix:
                helper_domain[h] = dom
                break
        else:
            helper_domain[h] = "authorization" if h.startswith(AUTHORIZATION_PREFIXES) else "common"

    by_domain: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: {"handlers": [], "helpers": []})
    for h, d in handler_domain.items():
        by_domain[d]["handlers"].append(h)
    for h, d in helper_domain.items():
        by_domain[d]["helpers"].append(h)

    print("=== handler count per domain ===")
    for d in sorted(by_domain):
        print(f"  {d:<16} handlers={len(by_domain[d]['handlers']):<3} helpers={len(by_domain[d]['helpers'])}")
    print()
    print("=== fork-only handler -> domain ===")
    for h in sorted(h for h in fork_handlers if h not in up_handlers):
        print(f"  {h:<44} {handler_domain[h]}")
    print()
    print("=== helper -> domain (fork-only helpers) ===")
    for h in sorted(helper_domain):
        print(f"  {h:<44} {helper_domain[h]}")
    print()
    print(f"total handlers={len(handler_domain)} helpers={len(helper_domain)}")

    json.dump({"handler_domain": handler_domain, "helper_domain": helper_domain},
              open(_work("fork_domain_map.json"), "w"), indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
