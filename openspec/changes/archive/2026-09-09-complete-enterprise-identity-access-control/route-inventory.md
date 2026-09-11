# Route Inventory — Task 1.2

Verified 2026-09-08. Method: extracted `_WEB_URLS` from `channel/web/web_channel.py` and cross-referenced against `auth/http_policy.ROUTE_POLICY`, plus AST walk of each handler class for supported HTTP methods across `web_channel.py`, `auth_handlers.py`, `admin_handlers.py`.

## Completeness (completeness-gate boundary)

- **92 routes** defined in `_WEB_URLS`.
- **0 routes** missing from `ROUTE_POLICY` → no URL silently reaches a handler past the gate.
- **0 policy entries** with no matching route → no orphan/phantom policy.
- Unknown URL → 404 (web.py notfound preserved). Registered URL with an unregistered method → 405 by the gate.

## Identity/admin domain — exact method match (authoritative)

| Route | Policy | Handler methods | Match |
| --- | --- | --- | --- |
| `/auth/login` | public POST | POST | ✅ |
| `/auth/check` | public GET | GET | ✅ |
| `/auth/logout` | public POST | POST | ✅ |
| `/auth/password` | personal POST | POST | ✅ |
| `/auth/me` | personal GET | GET | ✅ |
| `/auth/context` | tenant GET | GET | ✅ |
| `/api/platform/users` | platform GET | GET(list) | ✅ |
| `/api/platform/users/([^/]+)` | platform PATCH | PATCH(id) | ✅ (GET on detail deliberately 405) |
| `/api/platform/users/([^/]+)/password` | platform POST | POST | ✅ |
| `/api/platform/tenants` | platform GET+POST | GET+POST | ✅ |
| `/api/platform/tenants/([^/]+)/admins` | platform POST | POST | ✅ |
| `/api/platform/tenants/([^/]+)` | platform GET+POST | GET+POST | ✅ |
| `/api/tenant` | tenant GET | GET | ✅ |
| `/api/tenant/members` | tenant GET+POST | GET+POST | ✅ |
| `/api/tenant/members/([^/]+)` | tenant POST | POST | ✅ |
| `/api/tenant/roles` | tenant GET+POST | GET+POST | ✅ |
| `/api/tenant/roles/([^/]+)` | tenant POST+DELETE | POST+DELETE | ✅ |
| `/api/tenant/permissions` | tenant GET | GET | ✅ |
| `/api/tenant/departments` | tenant GET+POST | GET+POST | ✅ |
| `/api/tenant/departments/([^/]+)` | tenant PUT+DELETE | PUT+DELETE | ✅ |
| `/api/identity/audit` | tenant GET | GET | ✅ |

## Policy→handler method divergences observed (pre-existing, mostly legacy/chat domain, NOT this change's identity domain)

These are cases where `ROUTE_POLICY` lists a method the handler does not implement in its class body (AST-observed). They are gated by the completeness gate (will be 405/503 rather than silently invoking), so they don't bypass authorization, but some may indicate a handler that relies on a base-class/inherited method or a route whose intended method is mislabeled. Documented for 6.1 review:

- `/api/sessions/(.*)` policy GET; `SessionDetailHandler` class defines DELETE/PUT only.
- `/api/projects/manage` policy POST; `ProjectManageHandler` defines PUT/DELETE.
- `/api/todos(/, /summary, /(.*)/events, /(.*)/source, /(.*))` policy GET; handlers define no GET in class body (likely inherit from a base handler).
- `/v1/chat/completions` policy POST; class defines no POST in body (legacy path).

## Domain classification (from ROUTE_POLICY)

- **public**: `/`, `/api/health`, `/assets/(.*)`, `/mcp/oauth/callback`, `/auth/login|check|logout`, `/api/version`, `/api/branding/public`, `/api/branding/assets/(.*)`.
- **personal**: `/auth/password`, `/auth/me`.
- **platform**: all `/api/platform/*`.
- **tenant**: `/api/tenant/*`, `/api/identity/audit`, chat transport `/message|/stream|/poll|/cancel|/chat|/v1/chat/completions`, and read-side `/api/todos|/api/agents|/api/sessions|/api/history|/api/logs|/api/branding`.
- **closed** (503 in database mode): `/upload`, `/uploads/(.*)`, `/api/file`, `/preview/(.+)`, `/api/workspace/*`, `/api/projects/*`, `/api/voice/*`, `/config`, `/api/models`, `/api/channels`, `/api/weixin/qrlogin`, `/api/feishu/register`, `/api/tools`, `/api/skills*`, `/api/memory*`, `/api/knowledge*`, `/api/scheduler*`.

## Backstage / indirect entries

- `build_web_app()` (line 1181) installs `enforce_http_policy` processor + `reject_multi_worker_identity()` (single-process baseline). A multi-worker database deployment is rejected, not silently degraded.
- `bridge/agent_bridge.py:496` — in database mode the bridge returns early, not initializing registry/router/initializer (runtime/model execution closed server-side).
