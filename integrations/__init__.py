# encoding:utf-8
"""External-system integration modules (change ``add-external-system-access``).

The control plane for MCP / ERP / OA / personal-email connections lives here
rather than in ``channel/web/web_channel.py`` or ``auth/service.py``: the
identity database keeps the *authority* (tables, secrets, audit) while this
package owns the connection lifecycle, the per-type validation and — in later
task groups — the type adapters, so the giant channel module does not grow a
fifth responsibility.
"""

from integrations.external.service import (
    ExternalConnectionService,
    get_external_connection_service,
)

__all__ = ["ExternalConnectionService", "get_external_connection_service"]
