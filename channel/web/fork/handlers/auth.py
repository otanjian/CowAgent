"""Fork web layer (change adopt-upstream-web-split, design D2).

Fork-owned implementation, moved verbatim out of the former
channel/web/web_channel.py monolith. Upstream's api/ modules are not
edited. Imports inside function bodies are lazy so these modules can
reference each other without import cycles.
"""

from __future__ import annotations
from bridge.context import *
from channel.web.auth_handlers import (
    DbAuthCheckHandler,
    DbAuthLoginHandler,
    DbAuthContextHandler,
    DbAuthLogoutHandler,
    DbAuthMeHandler,
    DbAuthPasswordHandler,
    DbSelfProfileHandler,
    DbSelfAvatarHandler,
    DbUserAvatarHandler,
    DesktopAuthorizeHandler,
    DesktopTokenHandler,
)
from common.log import logger
import web


class McpOAuthCallbackHandler:
    """OAuth redirect target for MCP servers requiring authorization.

    The browser lands here after the user authorizes a remote MCP server.
    We exchange the authorization code for tokens and bring the server
    online. Unauthenticated by design: the OAuth `state` param is the
    single-use secret that binds this request to a pending authorization.

    The state alone answers "did this flow exist"; it cannot answer "is the
    request completing it still the request that started it". That second
    question is what ``take_pending``'s verifier answers, against the store:
    the connection must still exist, at the same version, in the same scope,
    for a subject that still holds the authority they started with. A callback
    that fails is refused *and* has consumed its state, so a refused
    authorization cannot be retried against the same one-time value
    (spec ``mcp-connection-integration``: 回调时已切换租户).
    """

    def GET(self):
        web.header('Content-Type', 'text/html; charset=utf-8')
        params = web.input(code="", state="", error="", error_description="")

        def _page(title: str, message: str) -> str:
            return (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                f"<title>{title}</title></head>"
                "<body style='font-family:-apple-system,Segoe UI,Roboto,sans-serif;"
                "max-width:520px;margin:64px auto;padding:0 20px;text-align:center;color:#1f2328'>"
                f"<h2>{title}</h2><p style='color:#57606a'>{message}</p></body></html>"
            )

        if params.error:
            logger.warning(f"[MCP-OAuth] callback error: {params.error} {params.error_description}")
            return _page("授权失败", f"{params.error}: {params.error_description or ''}")

        if not params.code or not params.state:
            return _page("参数缺失", "回调缺少 code 或 state 参数。")

        try:
            from agent.tools.mcp.mcp_oauth import take_pending
            from agent.tools.mcp.mcp_client import notify_server_authorized
        except Exception as e:
            logger.warning(f"[MCP-OAuth] callback import failed: {e}")
            return _page("内部错误", "OAuth 模块不可用。")

        handler = take_pending(params.state, verify=_verify_oauth_callback)
        if handler is None:
            # Deliberately one message for "expired", "unknown" and "refused":
            # distinguishing them would tell a caller whether a state value ever
            # existed, and which check a refusal came from.
            return _page("会话已过期", "授权请求不存在、已过期，或与发起时的连接不再匹配。")

        try:
            ok = handler.finish_authorization(params.code)
        except Exception as e:
            logger.warning(f"[MCP-OAuth] token exchange crashed: {e}")
            ok = False

        if not ok:
            return _page("授权失败", "换取令牌失败，请重试。")

        notify_server_authorized(handler.server_name)
        logger.info(f"[MCP-OAuth] Server '{handler.server_name}' authorized via web callback")
        return _page(
            "授权成功",
            f"MCP 服务 “{handler.server_name}” 已授权，可以返回聊天继续使用了。",
        )


def _verify_oauth_callback(binding: dict):
    """Is the request completing this flow still the one that started it?

    A thin adapter: the decision lives in
    ``integrations.external.oauth_binding`` so it can be tested without a
    browser, a server or a token endpoint. Imported lazily because this module
    is loaded by the web process while the binding module reaches into the
    connection service, and a failure to import must be a refusal rather than a
    500 — an unverifiable callback is exactly what must not be accepted.
    """
    try:
        from integrations.external.oauth_binding import verify_callback
    except Exception as exc:  # noqa: BLE001 - no verifier, no authorization
        return False, "the callback verifier is unavailable: %s" % exc
    return verify_callback(binding)


class AuthCheckHandler:
    def GET(self):
        return DbAuthCheckHandler().GET()


class AuthLoginHandler:
    def POST(self):
        return DbAuthLoginHandler().POST()


class AuthLogoutHandler:
    def POST(self):
        return DbAuthLogoutHandler().POST()


def _register_owner_scope() -> Tuple[str, str]:
    """拥有本次注册会话的 ``(user_id, tenant_id)``。

    database 模式下绑定到已验证的调用者及其选中的租户；legacy 模式是单用户
    部署，任何通过认证的调用者都是同一所有者。
    """
    from channel.web.web_channel import _is_database_identity
    if not _is_database_identity():
        return ("", "")
    from channel.web.auth_handlers import _require_context
    ctx = _require_context(require_tenant=True)
    return (ctx.user_id, ctx.tenant_id or "")


def _verified_auth_session_id() -> str:
    """本次请求背后**已验证**登录会话的 id。

    扫码授权绑定到登录会话：同一账号换一次登录就不再承认上一张票，
    句柄被别的会话拾取也无法兑换。id 非秘密（聊天委派快照里本来就带它），
    这里取的是服务端验证后的值，不是客户端字段。取不到时返回空串并按
    「未绑定」处理：无法读到会话时不把一次无关的写入变成 401，而是让绑定
    校验自行拒绝（对绑定到会话的授权即失败关闭）。

    实现放在 ``auth_handlers``：公开创建路径（``admin_handlers``）与个人创建
    路径都取同一个事实，两处不能各写一份。
    """
    from channel.web.web_channel import _is_database_identity
    if not _is_database_identity():
        return ""
    from channel.web.auth_handlers import verified_auth_session_id

    return verified_auth_session_id()


