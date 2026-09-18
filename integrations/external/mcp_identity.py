# encoding:utf-8
"""What an MCP tool is called, and every id a grant may have recorded for it.

Why this module exists
----------------------
An MCP tool has been named two ways in this codebase's life, and both spellings
are still live at once:

* **legacy** — an ``mcp.json`` entry is trusted as configuration and its tools
  are registered as ``<tool_name_prefix><remote name>``, which the identity layer
  records as the resource id ``mcp:<server>:<tool>`` (see
  ``agent.protocol.agent_stream._tool_resource_id`` and
  ``auth.service._project_tools``). An administrator grants that id and an Agent
  profile quotes it in its allow/deny list;
* **control plane** — a connection is a row with a stable id, and a discovered
  tool belongs to *the connection* rather than to a server name, so its identity
  is ``mcp:<connection id>:<remote name>``.

The migration moved a server from the first world to the second, and the spec
requires the move to be invisible to authorization (``mcp-connection-integration``:
迁移工具身份 SHALL 保留已有显式授权映射，不能批量放权). Two things are needed for
that and neither is a rename:

1. the composed *name* must come out byte-identical, which means
   ``tool_name_prefix`` has to survive as a real connection field — it is not a
   secret, not a display field and not droppable, because the name it produces is
   the key an existing grant is filed under;
2. a tool must be *matchable* under both spellings, so a grant written before the
   migration still authorizes the tool after it. That is :func:`aliases`, and it
   is the reason :func:`stable_id` and :func:`legacy_id` live in one place rather
   than one per caller.

``mcp:`` is a **namespace, not a permission**
---------------------------------------------
Nothing here decides whether a call is allowed. Composing an id that starts with
``mcp:`` proves only that someone can spell the prefix, and the spec says that
explicitly (工具名称的 mcp 前缀 MUST NOT 作为执行授权). The actual gate is
:func:`integrations.external.authorization.may_execute` /
``IdentityService.check_resource_action``, and the tenant-admin exemption behind
it is closed by an Agent binding rather than by the id's shape. Keeping the
prefix arithmetic in one small module is what makes that separation auditable:
every id that exists is produced here, and no branch here returns a boolean.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Tuple

#: The namespace every MCP tool's resource id carries. ``auth.policy`` documents
#: it as the origin mark that distinguishes an MCP tool from a built-in one.
LEGACY_PREFIX = "mcp:"

#: Separator between the server/connection name and the tool name in an id.
ID_SEPARATOR = ":"

#: Bound on a ``tool_name_prefix``. A tool name ends up in a prompt, a log line
#: and a grant id, so an unbounded prefix is a way to make all three unusable.
MAX_PREFIX = 64

#: What a prefix may contain. Deliberately excludes whitespace and the id
#: separator: ``fs_`` and ``gh-`` are real legacy values, while a ``:`` would
#: make ``mcp:<server>:<tool>`` ambiguous and a newline would break a grant list.
_PREFIX_RE = re.compile(r"^[A-Za-z0-9_.-]*$")


def prefix_of(config: Optional[Mapping[str, Any]]) -> str:
    """The validated prefix on a connection config, or ``""``.

    An unusable value is treated as absent rather than escaping into a tool name:
    :func:`integrations.external.registry.validate_config` refuses one at the
    schema, so reaching here means a row written before that check existed.
    """
    value = "" if not isinstance(config, Mapping) else config.get("tool_name_prefix")
    text = "" if value is None else str(value)
    if not text:
        return ""
    if len(text) > MAX_PREFIX or not _PREFIX_RE.match(text):
        return ""
    return text


def registered_name(config: Optional[Mapping[str, Any]], remote_name: str) -> str:
    """The tool name this connection's tool registers under.

    ``McpTool`` composes exactly this, so reproducing the prefix reproduces the
    name — and the name is the identity an existing grant was filed under.
    """
    return "%s%s" % (prefix_of(config), str(remote_name or ""))


def legacy_id(server_name: str, tool_name: str) -> str:
    """``mcp:<server>:<tool>`` — the id a pre-migration grant carries."""
    return "%s%s%s%s" % (LEGACY_PREFIX, str(server_name or ""),
                         ID_SEPARATOR, str(tool_name or ""))


def stable_id(connection_id: str, remote_name: str) -> str:
    """``mcp:<connection id>:<remote name>`` — the id keyed on the stable row.

    Not the tool's *registered* name: a discovered tool is reached through its
    connection, and the connection id is what survives the server behind it being
    re-created, re-credentialed or replaced by a platform template.
    """
    return "%s%s%s%s" % (LEGACY_PREFIX, str(connection_id or ""),
                         ID_SEPARATOR, str(remote_name or ""))


def split_legacy_id(resource_id: str) -> Optional[Tuple[str, str]]:
    """``(server, tool)`` for an ``mcp:`` id, or ``None``.

    Splits on the *first* separator only, because a prefix like ``fs_`` may
    itself contain a dot but the server segment never contains a colon. An id
    with no tool segment, or one outside the namespace, is answered as ``None``
    rather than guessed: this is used to match grants, and a guess would match
    the wrong one.
    """
    text = str(resource_id or "")
    if not text.startswith(LEGACY_PREFIX):
        return None
    rest = text[len(LEGACY_PREFIX):]
    server, separator, tool = rest.partition(ID_SEPARATOR)
    if not separator or not server or not tool:
        return None
    return (server, tool)


def aliases(*, connection_id: str, server_name: str = "",
            config: Optional[Mapping[str, Any]] = None,
            remote_name: str) -> Tuple[str, ...]:
    """Every resource id this tool may be granted under, stable one first.

    The stable id is what a *new* grant names. The legacy id is kept because a
    grant written before the migration is still a grant — re-granting the same
    capability under a new name is what 不能批量放权 forbids, and refusing the old
    id would be the same failure with the opposite sign.

    Empty or duplicate entries are dropped so a caller can iterate the result
    without re-checking: an anonymous connection contributes no stable id, and a
    missing prefix makes the two spellings one.
    """
    out = []
    stable = stable_id(connection_id, remote_name)
    if connection_id:
        out.append(stable)
    if server_name:
        out.append(legacy_id(server_name, registered_name(config, remote_name)))
    seen = set()
    unique = []
    for item in out:
        if item and item not in seen:
            seen.add(item)
            unique.append(item)
    return tuple(unique)
