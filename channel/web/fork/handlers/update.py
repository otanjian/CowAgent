"""Fork web layer (change adopt-upstream-web-split, design D2).

Fork-owned implementation, moved verbatim out of the former
channel/web/web_channel.py monolith. Upstream's api/ modules are not
edited. Imports inside function bodies are lazy so these modules can
reference each other without import cycles.
"""

from __future__ import annotations
from bridge.context import *
import json
import web


class VersionHandler:
    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        from cli import __version__
        return json.dumps({"version": __version__})


