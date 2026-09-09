# Brand Settings with Audit in Database Identity Mode

**Date:** 2026-09-09
**Status:** Implemented
**Problem:** In `identity_mode=database`, the brand settings page is hard read-only. `_branding_write_allowed()` returns `(False, "branding_enterprise_unavailable")` for anything that is not the legacy single-instance mode, so the UI shows the banner "企业品牌授权与审计尚未接入，暂不可修改" and every save/reset is rejected with 403. There is no way to maintain the instance brand when multi-user identity is enabled.

## 1. Goals and non-goals

### Goals

In database identity mode, a **platform admin** can:

- Read the brand settings management payload (same as today, but `can_manage`/`can_reset` now true for a platform admin).
- Save a brand change (`POST /api/branding`) and reset to defaults (`POST /api/branding/reset`).
- Every successful save/reset is recorded as a sanitized audit event in `identity.db` (`audit_events` table), with a generated actor (`actor_user_id` / `actor_username`, tenant-scope `NULL` since the brand is instance-wide).

Legacy single-instance mode behavior is **unchanged** (password-gated write, no audit dependency on identity.db).

### Non-goals (this slice)

- **Per-tenant brand isolation.** The brand remains an instance-wide asset (menu lives under 平台运维 / platform ops). Making the brand tenant-scoped is a much larger change and is not requested.
- **New permission codes.** No `branding.*` permission is introduced. Access is the existing `is_platform_admin` qualification, matching how the platform console (`/config`, `/api/models`) is guarded.
- **New audit storage.** Reuses the existing `AuditStore` / `audit_events` table.
- Changing where the brand assets live.

## 2. Storage model

Unchanged. Brand data stays under `<data_root>/branding/`. The only change is *who* may write and *that writes are audited*.

Audit events are stored in the existing `identity.db` `audit_events` table (append-only, `tenant_id` `NULL` = platform domain), exactly like the other platform-admin writes (`user.set_status`, `tenant.rename`, etc.).

## 3. Authorization rules

`_branding_write_allowed()` is the single server-side gate. It becomes:

| `identity_mode` | Allowed? | `readonly_reason` | Notes |
|---|---|---|---|
| `legacy` | yes, iff a console password is set | `web_console_password_required` | unchanged |
| `database` | yes, iff the resolved context is a platform admin | `""` (allow) or raise 401/403 | new |
| anything else | no | `branding_enterprise_unavailable` | unchanged (fail-closed) |

Because `database` mode must resolve the per-request context to decide, the `allow`/`deny` decision is split:

- `_branding_write_allowed()` keeps returning a tuple for the *static* part only where it can (legacy password, unknown modes), and for `database` mode it signals "platform-admin gate required" rather than guessing.
- The context resolution happens in the two entry points that already know how to behave per mode:
  - `_branding_require_write()` (for save/reset)
  - `BrandingManageHandler.GET` (for the management read)

See §5 for the concrete wiring, which mirrors the existing `_require_platform_console()` helper.

## 4. Audit event contract

Each save/reset produces one event:

| Field | Value |
|---|---|
| `action` | `branding.update` (save) or `branding.reset` (reset) |
| `target` | `brand` |
| `tenant_id` / `target_tenant_id` | `NULL` (platform domain, instance-wide brand) |
| `actor_user_id` / `actor_username` | the resolved platform admin (from the request context) |
| `redacted_changes` | `{ "brand_name": ..., "logo_description": ... }` (no secrets; logo is content-addressed and not stored in the event) |
| `result` | `success` |

The audit write must not break a successful brand commit: it is recorded after the version is published, and a failure to record is logged but does not roll back the brand (consistent with how `_login_denied_audit` never changes an auth outcome). The event is committed on its own connection (not inside the branding file lock).

## 5. Implementation details

### 5.1 `channel/web/web_channel.py`

- Replace the hard `database` rejection in `_branding_write_allowed()` with a `PlatformAdminRequired` signal (a sentinel `readonly_reason`, e.g. `"branding_platform_admin_required"`), or split the function so callers know to run the context gate.
- Add an internal helper `_branding_require_platform_admin()` that:
  - resolves the context via `_require_context()` and calls `_require_platform_admin(ctx)` (reusing `channel.web.admin_handlers`),
  - returns the `(user_id, username)` of the actor for audit attribution.
- Add a helper `_branding_record_audit(ctx, action, record, *, reset=False)` that records a sanitized `branding.update` / `branding.reset` event via the same identity service that authenticated the request (`auth_handlers._get_service()._audit.record(...)`), with `tenant_id IS NULL`. It is best-effort and never rolls back the committed brand.
- `BrandingManageHandler.GET`:
  - `database` → resolve platform-admin context to compute `can_manage`/`can_reset`; do **not** issue the legacy `csrf_token` (DB mode uses bearer/origin CSRF like other admin writes).
  - `legacy` → unchanged.
- `BrandingManageHandler.POST` and `BrandingResetHandler.POST`:
  - capture the actor (`user_id`, `username`) from the resolved context in `database` mode and pass it to `service.save(..., operator=...)` / `service.reset(..., operator=...)`, then call `_branding_record_audit(...)`.
  - In legacy mode no actor is available; `operator` stays `"console"` and no audit is recorded.

### 5.2 `channel/web/branding.py`

- `save(...)` / `reset(...)` already accept `operator: str`. In database mode the handler passes the acting platform admin's username; in legacy mode it stays `"console"`.
- The `BrandingService` is kept free of any `auth.audit` import. Audit recording lives in the handler layer (see `_branding_record_audit` in §5.1), which reuses the existing `AuthStore.record(...)` on its own connection after the brand version is published. A failed audit write is logged, never surfaced, and never rolls back the brand.
- `management_payload(...)` unchanged except that in database mode the handler passes `can_manage=True`/`can_reset=True` when the caller is a platform admin, so `readonly_reason` is empty and the UI enables the form.

### 5.3 Frontend `channel/web/static/js/console.js`

- No markup change needed: the banner and readonly state are driven by `readonly_reason` / `can_manage`. In `database` mode with a platform admin, `readonly_reason=""` and `can_manage=True` already render the editable form and hide the red banner.
- The DB-mode write uses `credentials: 'same-origin'` + `X-Branding-CSRF` header (already sent), which the DB gate treats as origin-checked; no change required.

## 6. Error handling

- A non-admin (or no session) in `database` mode gets the standard 401/403 from `_require_context()` / `_require_platform_admin()` — the same semantics as the platform console.
- An unknown `identity_mode` stays hard read-only (`branding_enterprise_unavailable`).
- A missing/invalid brand still surfaces the existing `branding_storage_corrupt` recovery path.
- Audit-record failure is logged, never raised to the client.

## 7. Testing

- `tests/test_branding.py`:
  - Remove / rewrite the assertion that `database` mode must always fail closed for writes (`test_enterprise_or_unknown_modes_fail_closed_for_both_writes` keeps `enterprise`/`unknown`, drops `database`).
  - Update `test_database_management_uses_real_database_session_and_stays_readonly` → now a platform admin session gets `can_manage=True`, `can_reset=True`, `readonly_reason=""`, and a save succeeds and writes an audit event.
  - Add a non-admin database-session case that must remain read-only (or 403).
- New `tests/test_branding_audit.py`: assert a save emits one `branding.update` event (and reset emits `branding.reset`) with the expected actor and `tenant_id IS NULL`, and that no secret is persisted.

## 8. Risks / open questions

- Whether to keep the brand **instance-wide** vs tenant-scoped: this slice keeps it instance-wide per the approved decision. If a future requirement needs per-tenant brands, this design does not impose a migration burden beyond moving the storage root + adding `tenant_id` to events.
- The DB-mode write no longer uses the `cow_auth_token` brand CSRF token; it relies on the admin API's bearer/origin CSRF. Must confirm the frontend's `X-Branding-CSRF` header is harmless when present on a DB-mode request (it is simply ignored by the DB gate).
