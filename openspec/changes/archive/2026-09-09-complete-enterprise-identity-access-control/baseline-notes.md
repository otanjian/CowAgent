# Baseline Notes — Task 1.1

Verified 2026-09-08 on branch `feat/complete-enterprise-identity-access-control`.
Command: `.venv/bin/python --version` → Python 3.14.3.

## Baseline test evidence

| Suite | Result |
| --- | --- |
| Core security/policy/context/revocation (http_policy, auth_security, policy, self_context, tenant_read_scoping, revocation_takes_effect) | 57 passed |
| Service writes / web handlers / consumer closure / chat-owner / audit (service_writes, web_handlers, consumer_closure_acceptance, web_consumer_closure, chat_identity_context, identity_audit) | 81 passed |
| Identity service/store/session/credential/password/runtime/scope-gate/state-dir-tenant/agent-bindings | 91 passed |
| Credential / temp-expiry / platform-admin / concurrency-acceptance | 65 passed |
| Rate-limit | 11 passed |
| **Backend total** | **275 passed** (1 deprecation warning: `setDaemon` in `chat_channel.py`) |
| `node --test tests/test_identity_admin_frontend.cjs` | 9 passed |

## Gap claims vs current code (G01–G09 from docs/design/user-role-permission-gap-and-plan.md)

| Gap | Status |
| --- | --- |
| G01 `_check_auth()` legacy-first, no unified processor | Fixed: `auth/http_policy.py` installed by `build_web_app` as shared processor; database auth branch independent, legacy preserved. |
| G02 Audit/TenantInfo over-permissive | Fixed: audit gated to platform/tenant_admin; tenant info field-whitelisted + permission-checked (see tenant_read_scoping + identity_audit tests). |
| G03 no unified Cookie/CSRF, no temp-expiry check, no login rate-limit | Fixed: unified credential selection + source/CSRF check; temp expiry validated; bounded login rate-limit (429) in `auth_handlers.py`. |
| G04 member list no role_codes, empty array wipes roles | Fixed: `role_codes` returned; omit roles preserves, explicit `[]` returns 400. |
| G05 no platform user maintenance/reset API | Fixed: `/api/platform/users` + `/password` reset with recent-password + last-admin + session revoke + audit. |
| G06 user UI create-new only | Fixed: bind-existing + search/filter + real pagination wired. |
| G07 tenant admin config, org tree, role copy unconnected | Fixed: tenant admin/name/status forms, org tree, role copy/associated-members. |
| G08 database UI default-chat + poll while backend 503 | Fixed: read-only/management home + unavailable-reason + capability-aware nav. |
| G09 register picks first platform admin | Fixed: exact `--admin-username` resolution + tenant qualification. |

## Key closure facts (verified in source)

- `bridge/agent_bridge.py:496` — in database identity mode the bridge returns early, NOT initializing registry/router/initializer (runtime chat/model execution closed server-side regardless of admin).
- `channel/web/web_channel.py:473` — chat handler `_authorize_chat` calls `_require_private_owner(ctx, agent_id)`, atomically claims durable owner.
- `auth/http_policy.py` — route-completeness gate: unregistered method → 405; unknown URL → 404; closed consumers → 503 in database mode. It is a completeness gate, NOT a replacement for handler session/tenant/owner checks (those remain in handlers).
- `auth/service.py:188 _tx()` — `BEGIN IMMEDIATE` acquires SQLite write lock up front; `login()` (line 566) re-reads account on the SAME connection inside the tx and re-verifies password hash/version/status/temp-deadline before issuing the session. `change_password()` (line 691) re-verifies old password against current hash on the same connection.
- `auth/credential.py` — fixed, non-fallback selection: cookie-only / bearer-only / same-value→cookie / different-value→`400 mixed_credentials`; malformed bearer treated as absent.

## Concurrency acceptance (new — closes the review's explicit gap)

The review stated "新增要求...必须新增能实际重现并发提交...的行为验收". The existing sequential suites did NOT contain a true multi-threaded race test. Added `tests/test_identity_concurrency_acceptance.py` (3 tests, stable across 5 runs):

- Login-after-reset never mints a session for a stale password (401 after reset).
- Concurrent login(old-password) + password-change leaves no reusable session for the superseded password.
- Concurrent double-demote never leaves zero completed platform admins.

Each thread opens its own SQLite connection (`check_same_thread` safe via per-call `connect()`, `busy_timeout=5000`). These pass with the correct BEGIN IMMEDIATE implementation.

## Caveats per proposal/review (updated)

Checked tasks (2.1–5.8) + these 275 passing tests do NOT by themselves prove acceptance. Still required: real browser acceptance of 4 pages (6.2), migration/recovery drill (6.3), cold-start/indirect-entry closure (6.4), plus final regression + strict validation (6.5).
