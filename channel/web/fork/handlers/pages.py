"""Fork web layer (change adopt-upstream-web-split, design D2).

Fork-owned implementation, moved verbatim out of the former
channel/web/web_channel.py monolith. Upstream's api/ modules are not
edited. Imports inside function bodies are lazy so these modules can
reference each other without import cycles.
"""

from __future__ import annotations
from bridge.context import *
from common import i18n
from common.log import logger
import json
import mimetypes
import os
import time
import web


# Adapted on move (not verbatim, see the emitter): ``__file__``
# now points at the fork package, so asset paths anchor at the web
# package root instead of the monolith's own directory.
_WEB_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class RootHandler:
    def GET(self):
        raise web.seeother('/chat')


class HealthHandler:
    # Unauthenticated liveness probe. The desktop shell polls this to know the
    # backend is up; it must never require a session. Returns no sensitive data.
    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        web.header('Cache-Control', 'no-store')
        return json.dumps({"status": "ok"})


class ChatHandler:
    def GET(self):
        # Content-Type must be explicit: behind a reverse proxy that sends
        # X-Content-Type-Options: nosniff, a missing type makes browsers
        # refuse to sniff and render the page as plain text source.
        from channel.web.web_channel import _web_navigation_mode
        web.header('Content-Type', 'text/html; charset=utf-8')
        web.header('Cache-Control', 'no-cache, no-store, must-revalidate')
        web.header('Pragma', 'no-cache')
        file_path = os.path.join(_WEB_ROOT, 'chat.html')
        with open(file_path, 'r', encoding='utf-8') as f:
            html = f.read()
        cache_bust = str(int(time.time()))
        # Every first-party asset the page pulls in, so an upgraded console is
        # never left running against a browser-cached copy of the old scripts.
        # identity-admin.js carries the tabbed editors: if it is missed here a
        # browser can keep rendering the previous (per-tab save) editor even
        # though the server already ships the unified-save one.
        assets = ['js/console.js', 'js/workspace.js', 'js/doc-editor.js',
                  'js/appearance.js', 'js/scenes/index.js',
                  'js/identity-admin.js', 'js/todos.js', 'js/fragments.js',
                  'css/console.css', 'css/appearance.css']
        # The per-domain i18n namespaces are discovered rather than listed: the
        # split (task 8.5) adds files over time, and a name missed here would
        # leave a browser rendering an upgraded console with a stale dictionary.
        try:
            i18n_dir = os.path.join(_WEB_ROOT, 'static', 'js', 'i18n')
            assets += [f'js/i18n/{name}' for name in sorted(os.listdir(i18n_dir))
                       if name.endswith('.js')]
        except OSError:
            pass
        # Fork fragments (task 8.8) are fetched at runtime by fragments.js, so
        # they need the same cache-busting as the scripts: a browser-cached copy
        # would keep mounting stale fork markup after an upgrade. Discovered
        # rather than listed so a new fragment needs no server edit.
        try:
            fragments_dir = os.path.join(_WEB_ROOT, 'static', 'fragments')
            assets += [f'fragments/{name}' for name in sorted(os.listdir(fragments_dir))
                       if name.endswith('.html')]
        except OSError:
            pass
        for asset in assets:
            html = html.replace(f'assets/{asset}', f'assets/{asset}?v={cache_bust}')
        # Inject the backend-resolved default language for first-load fallback.
        html = html.replace("{{COW_DEFAULT_LANG}}", i18n.get_language())
        # Inject the validated console navigation presentation switch (layout
        # only): "classic" (single sidebar) or "split" (area switch). Invalid
        # config falls back to "classic"; this never alters authorization or
        # consumer open/closed state.
        html = html.replace(
            "{{COW_NAVIGATION_MODE}}",
            _web_navigation_mode(),
        )
        return html


class AssetsHandler:
    def GET(self, file_path):  # 修改默认参数
        try:
            # 如果请求是/static/，需要处理
            if file_path == '':
                # 返回目录列表...
                pass

            # 获取当前文件的绝对路径
            current_dir = _WEB_ROOT
            static_dir = os.path.join(current_dir, 'static')

            full_path = os.path.normpath(os.path.join(static_dir, file_path))

            # 安全检查：确保请求的文件在static目录内
            if not os.path.abspath(full_path).startswith(os.path.abspath(static_dir)):
                logger.error(f"Security check failed for path: {full_path}")
                raise web.notfound()

            if not os.path.exists(full_path) or not os.path.isfile(full_path):
                # Browsers routinely probe optional asset variants (e.g. a
                # .ttf fallback declared alongside .woff2 in @font-face);
                # logging these as errors floods the console with harmless
                # noise. Keep it at debug level — real misconfigurations
                # will still surface via the network panel.
                logger.debug(f"Static file not found: {full_path}")
                raise web.notfound()

            # 设置正确的Content-Type
            content_type = mimetypes.guess_type(full_path)[0]
            if content_type:
                web.header('Content-Type', content_type)
            else:
                # 默认为二进制流
                web.header('Content-Type', 'application/octet-stream')

            # 读取并返回文件内容
            with open(full_path, 'rb') as f:
                return f.read()

        except web.HTTPError:
            # The 404 path above already logged at debug; re-raise as-is so
            # web.py returns the original status to the client.
            raise
        except Exception as e:
            logger.error(f"Error serving static file: {e}", exc_info=True)
            raise web.notfound()


