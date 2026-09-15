import type {
  BrokerProbe,
  BrokerRequestReply,
  BrokerSessionReply,
  BrokerSessionWire,
  BrokerStatusReply,
} from '../types'

/**
 * The Desktop request context: one authoritative identity/tenant/epoch state and
 * the single decorator every request path goes through (design D8, tasks 8.1,
 * 8.4, 8.5, 8.6).
 *
 * Why a decorator instead of per-method plumbing
 * ----------------------------------------------
 * The session bearer lives in the main process now (``auth-broker.ts``), so the
 * renderer does not "attach authentication" at all -- it declares *what* the
 * request is and the broker decides how to credential it. That is what makes a
 * newly added business method safe by construction: a caller cannot forget a
 * header it never had, and the codebase does not grow a fork branch per method.
 *
 * What lives here
 * ---------------
 * * the authoritative self projection (user, tenants, selected tenant) -- never
 *   a role or permission truth, which stays server-side;
 * * the monotonic ``epoch``: every account/tenant switch increments it and every
 *   response that resolves under a stale epoch is rejected as
 *   :class:`StaleContextError` instead of being rendered into the new context
 *   (task 8.5). An in-flight *write* is never replayed under the new tenant.
 * * the gate the UI is in: signed out, forced password change, tenant selection,
 *   ready, or blocked by a failed sign-out;
 * * the error mapping (401 -> sign in, forced password change -> change page,
 *   invalid tenant -> re-select, 403 -> concrete refusal, 503 -> recoverable
 *   state, 409 -> conflict). Refusals are never turned into empty data or a fake
 *   success (task 8.6).
 */

/** Refusals the UI maps to a concrete screen. */
export type ContextFailureKind =
  | 'unauthorized'
  | 'password_change_required'
  | 'invalid_tenant'
  | 'forbidden'
  | 'conflict'
  | 'unavailable'
  | 'stale_context'
  | 'network'
  | 'blocked'

export class ContextError extends Error {
  constructor(
    public readonly kind: ContextFailureKind,
    message: string,
    public readonly code = '',
    public readonly status = 0,
  ) {
    super(message)
    this.name = 'ContextError'
  }
}

/** The gate the shell must render. */
export type ContextGate = 'checking' | 'need_login' | 'password_change' | 'tenant_select' | 'ready' | 'blocked'

export interface ContextSnapshot {
  gate: ContextGate
  session: BrokerSessionWire | null
  /** Non-empty when business requests must stop (failed server revocation). */
  blockedReason: string
  /** Last refusal, for an in-place message (never a silently empty view). */
  lastError: { kind: ContextFailureKind; code: string; message: string } | null
  identityMode: string
  /** True when the backend itself cannot be reached / resolved. */
  probeFailed: boolean
}

type Listener = () => void

//: Statuses the broker answers with, so an expired native session is recognized
//: without guessing from a body.
const STATUS_UNAUTHORIZED = 401
const STATUS_FORBIDDEN = 403
const STATUS_CONFLICT = 409

/** Codes that mean "this tenant is not valid for the current account". */
const TENANT_CODES = new Set(['invalid_tenant', 'tenant_invalid', 'missing_tenant', 'tenant_not_found'])
const PASSWORD_CODES = new Set(['password_change_required', 'must_change_password'])

interface RequestPlan {
  path: string
  method: string
  body?: string
  /** The context epoch this request belongs to. */
  epoch: number
}

export class DesktopContext {
  private state: ContextSnapshot = {
    gate: 'checking',
    session: null,
    blockedReason: '',
    lastError: null,
    identityMode: '',
    probeFailed: false,
  }

  private listeners = new Set<Listener>()
  private baseUrl = 'http://127.0.0.1:9876'
  private probing: Promise<void> | null = null

  // ----------------------------------------------------------------------- //
  // Observable state
  // ----------------------------------------------------------------------- //

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  getSnapshot = (): ContextSnapshot => this.state

  private emit(patch: Partial<ContextSnapshot>): void {
    this.state = { ...this.state, ...patch }
    for (const listener of this.listeners) listener()
  }

  setBaseUrl(url: string): void {
    this.baseUrl = url || this.baseUrl
  }

  getBaseUrl(): string {
    return this.baseUrl
  }

  /** The current epoch; used to reject late responses (task 8.5). */
  get epoch(): number {
    return this.state.session?.epoch ?? 0
  }

  get session(): BrokerSessionWire | null {
    return this.state.session
  }

  get isBrokerAvailable(): boolean {
    return typeof window !== 'undefined' && !!window.electronAPI?.desktopAuthStatus
  }

  // ----------------------------------------------------------------------- //
  // Gate resolution
  // ----------------------------------------------------------------------- //

  /**
   * Derive the gate from the authoritative projection.
   *
   * The order is the design's: backend reachable, then sign-in, then the
   * applicable forced password change, then tenant selection. A restricted
   * (must_change_password) account has no platform exception -- only the change
   * page and sign-out.
   */
  private gateFor(session: BrokerSessionWire | null, blockedReason: string, probeFailed: boolean): ContextGate {
    if (blockedReason) return 'blocked'
    if (!session) return 'need_login'
    if (session.mustChangePassword) return 'password_change'
    // More than one tenant and none chosen: the member must select before any
    // tenant business starts. Zero tenants is NOT this gate -- an account with no
    // membership keeps its account/platform surfaces.
    if (session.tenants.length > 1 && !session.tenantId) return 'tenant_select'
    return 'ready'
  }

  private applySession(session: BrokerSessionWire | null, blockedReason = ''): void {
    this.emit({
      session,
      blockedReason,
      gate: this.gateFor(session, blockedReason, this.state.probeFailed),
      lastError: null,
    })
  }

  private fail(kind: ContextFailureKind, code: string, message: string, status = 0): never {
    const error = new ContextError(kind, message, code, status)
    if (kind === 'unauthorized') {
      // A rejected session returns the shell to the sign-in flow instead of
      // retrying a dead credential or rendering an empty page.
      this.applySession(null)
      this.emit({ gate: 'need_login' })
    } else if (kind === 'password_change_required') {
      const session = this.state.session
      this.emit({
        session: session ? { ...session, mustChangePassword: true } : session,
        gate: 'password_change',
        lastError: { kind, code, message },
      })
    } else if (kind === 'invalid_tenant') {
      const session = this.state.session
      this.emit({
        session: session ? { ...session, tenantId: null } : session,
        gate: session && session.tenants.length > 1 ? 'tenant_select' : this.state.gate,
        lastError: { kind, code, message },
      })
    } else {
      this.emit({ lastError: { kind, code, message } })
    }
    throw error
  }

  // ----------------------------------------------------------------------- //
  // Operations (all through the broker; no credential in the renderer)
  // ----------------------------------------------------------------------- //

  /** Unauthenticated ``/auth/check`` probe. Called once the backend is ready. */
  async probe(): Promise<void> {
    if (!this.isBrokerAvailable) {
      // No broker (a plain browser dev session): the historical cookie-based
      // behavior applies, so the shell does not hard-block on a missing bridge.
      this.emit({ gate: 'ready', probeFailed: false })
      return
    }
    if (this.probing) return this.probing
    this.probing = (async () => {
      try {
        const probe: BrokerProbe = await window.electronAPI!.desktopAuthProbe!()
        const status: BrokerStatusReply = await window.electronAPI!.desktopAuthStatus!()
        const session = status.session ?? null
        this.emit({
          identityMode: probe.identityMode || status.identityMode || '',
          probeFailed: !probe.ok,
        })
        if (session) this.applySession(session, status.blockedReason || '')
        else if (probe.ok && probe.authRequired === false) {
          // Legacy/shared-password mode: no database account to sign in with.
          this.emit({ gate: 'ready', session: null, lastError: null })
        } else {
          this.emit({ gate: 'need_login', session: null })
        }
      } catch {
        // A failed probe is never "no login required": a down identity store
        // must not open the app.
        this.emit({ gate: 'need_login', probeFailed: true })
      } finally {
        this.probing = null
      }
    })()
    return this.probing
  }

  /** Open the system browser and complete the authorization-code + PKCE flow. */
  async beginAuthorization(): Promise<void> {
    if (!this.isBrokerAvailable) throw new ContextError('unavailable', 'the sign-in bridge is unavailable')
    const reply: BrokerSessionReply = (await window.electronAPI!.desktopAuthBegin!()) as BrokerSessionReply
    if (!reply.ok || !reply.session) {
      if (reply.code === 'unauthorized' || reply.code === 'authorization_cancelled') {
        throw new ContextError('unauthorized', reply.message || 'sign-in was not completed', reply.code || '')
      }
      if (reply.code === 'password_change_required') {
        this.fail('password_change_required', reply.code, reply.message || 'password change required')
      }
      throw new ContextError('unavailable', reply.message || 'sign-in could not be completed', reply.code || '')
    }
    this.applySession(reply.session)
  }

  async cancelAuthorization(): Promise<void> {
    await window.electronAPI?.desktopAuthCancel?.()
  }

  /**
   * Sign out. Revokes the server session first; a failure is surfaced and
   * freezes business traffic rather than pretending the session is gone.
   */
  async logout(): Promise<void> {
    if (!this.isBrokerAvailable) {
      this.applySession(null)
      return
    }
    const reply = await window.electronAPI!.desktopAuthLogout!()
    if (!reply.ok) {
      const message = reply.message || 'the server did not confirm the sign-out'
      this.emit({
        blockedReason: reply.code || 'logout_incomplete',
        gate: 'blocked',
        lastError: { kind: 'blocked', code: reply.code || 'logout_incomplete', message },
      })
      throw new ContextError('blocked', message, reply.code || 'logout_incomplete')
    }
    this.applySession(null)
  }

  /** Switch the business tenant: new epoch, old requests are invalidated. */
  async selectTenant(tenantId: string): Promise<void> {
    if (!this.isBrokerAvailable) return
    const reply: BrokerSessionReply = (await window.electronAPI!.desktopTenantSelect!(tenantId)) as BrokerSessionReply
    if (!reply.ok || !reply.session) {
      if (TENANT_CODES.has(reply.code || '')) {
        this.fail('invalid_tenant', reply.code || 'invalid_tenant', reply.message || 'tenant not available')
      }
      if (reply.code === 'unauthorized') {
        this.fail('unauthorized', 'unauthorized', reply.message || 'session is no longer valid', STATUS_UNAUTHORIZED)
      }
      throw new ContextError('unavailable', reply.message || 'the tenant could not be selected', reply.code || '')
    }
    this.applySession(reply.session)
  }

  /** Change the forced password; the server then requires a new sign-in. */
  async changePassword(oldPassword: string, newPassword: string): Promise<void> {
    const reply = await window.electronAPI?.desktopPasswordChange?.({ oldPassword, newPassword })
    if (!reply || !reply.ok) {
      const code = reply?.code || 'password_change_failed'
      if (code === 'unauthorized') {
        this.emit({ lastError: { kind: 'unauthorized', code, message: reply?.message || 'not signed in' } })
        throw new ContextError('unauthorized', reply?.message || 'not signed in', code, STATUS_UNAUTHORIZED)
      }
      throw new ContextError('forbidden', reply?.message || 'the password could not be changed', code, reply?.status || 0)
    }
    // The change revoked the session: authorize again.
    this.applySession(null)
    this.emit({ gate: 'need_login' })
  }

  // ----------------------------------------------------------------------- //
  // The request decorator
  // ----------------------------------------------------------------------- //

  /**
   * Build the plan for one request from the authoritative context.
   *
   * This is the single place that decides how a request is credentialed, so
   * every business method -- JSON, upload, voice, stream, preview, download --
   * shares it. Two invariants are enforced here rather than trusted to callers:
   *
   * * no session token in a URL: a ``token`` query parameter is stripped (the
   *   historical ``withToken`` helper is gone, and this is the backstop for a
   *   stale call site);
   * * the request carries the epoch it was issued under, so a response that
   *   lands after a tenant/account switch is discarded.
   */
  plan(path: string, method = 'GET', body?: string): RequestPlan {
    if (this.state.blockedReason) {
      throw new ContextError('blocked', 'requests are stopped until the sign-out completes', this.state.blockedReason, 503)
    }
    return { path: this.stripToken(path), method, body, epoch: this.epoch }
  }

  /** Remove a session token from a URL (defensive; no caller should add one). */
  private stripToken(path: string): string {
    if (!/[?&]token=/.test(path)) return path
    return path
      .replace(/([?&])token=[^&]*&?/g, '$1')
      .replace(/[?&]$/, '')
      .replace('?&', '?')
  }

  /**
   * Execute a planned request and map the answer onto the context.
   *
   * On a browser-only session (no broker) this falls back to a plain
   * same-origin-style fetch with cookies, which is the historical Web behavior;
   * the desktop build always has the broker.
   */
  async send<T>(plan: RequestPlan, parse: (reply: BrokerRequestReply) => T): Promise<T> {
    if (!this.isBrokerAvailable) {
      const res = await fetch(`${this.baseUrl}${plan.path}`, {
        method: plan.method,
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: plan.body,
      })
      if (!res.ok) throw this.mapHttp(res.status, res.statusText, '')
      return (await res.json()) as T
    }
    const reply: BrokerRequestReply = (await window.electronAPI!.desktopRequest!({
      path: plan.path,
      method: plan.method,
      body: plan.body,
    })) as BrokerRequestReply
    this.assertCurrent(plan)
    if (!reply.ok) {
      throw this.mapBrokerFailure(reply.code || '', reply.message || '', reply.status || 0)
    }
    if (!reply.status || reply.status >= 400) {
      throw this.mapHttp(reply.status || 0, reply.statusText || '', reply.body || '')
    }
    return parse(reply)
  }

  /** Multipart upload through the same seam (voice clips, attachments). */
  async sendForm<T>(path: string, formData: FormData, parse: (reply: BrokerRequestReply) => T): Promise<T> {
    const plan = this.plan(path, 'POST')
    if (!this.isBrokerAvailable) {
      const res = await fetch(`${this.baseUrl}${plan.path}`, {
        method: 'POST',
        credentials: 'include',
        body: formData,
      })
      if (!res.ok) throw this.mapHttp(res.status, res.statusText, await res.text())
      return (await res.json()) as T
    }
    const form = {
      fields: [] as Array<{ name: string; value: string }>,
      files: [] as Array<{ name: string; filename: string; contentType: string; bytes: ArrayBuffer }>,
    }
    const entries = Array.from(formData.entries())
    for (const [name, value] of entries) {
      if (typeof value === 'string') {
        form.fields.push({ name, value })
      } else {
        form.files.push({
          name,
          filename: value.name || 'upload',
          contentType: value.type || 'application/octet-stream',
          bytes: await value.arrayBuffer(),
        })
      }
    }
    const reply = (await window.electronAPI!.desktopUpload!({ path: plan.path, method: 'POST', form })) as BrokerRequestReply
    this.assertCurrent(plan)
    if (!reply.ok) throw this.mapBrokerFailure(reply.code || '', reply.message || '', reply.status || 0)
    if (!reply.status || reply.status >= 400) {
      throw this.mapHttp(reply.status || 0, reply.statusText || '', reply.body || '')
    }
    return parse(reply)
  }

  /**
   * A short-lived, token-free URL for a transport that cannot send a header
   * (EventSource, ``<img>``, download). The main process attaches the current
   * credential when the URL is actually requested.
   */
  assetUrl(path: string, kind: 'get' | 'stream' = 'get'): string {
    const clean = this.stripToken(path)
    const mint = typeof window !== 'undefined' ? window.electronAPI?.desktopAssetUrlSync : undefined
    if (mint) {
      if (this.state.blockedReason) {
        // Stopped business traffic must not silently fall back to a direct URL.
        return ''
      }
      if (this.state.session) {
        const reply = mint({ path: clean, kind })
        if (reply.ok && reply.url) return reply.url
      }
    }
    // Browser/dev fallback: the historical cookie-authenticated URL.
    return `${this.baseUrl}${clean}`
  }

  // ----------------------------------------------------------------------- //
  // Failure mapping
  // ----------------------------------------------------------------------- //

  /** Discard a response that belongs to a context we have already left. */
  private assertCurrent(plan: RequestPlan): void {
    if (plan.epoch !== this.epoch && this.state.session) {
      throw new ContextError('stale_context', 'the context changed while this request was in flight', 'stale_context', 409)
    }
  }

  private mapBrokerFailure(code: string, message: string, status: number): ContextError {
    if (code === 'stale_context') {
      return new ContextError('stale_context', message, code, STATUS_CONFLICT)
    }
    if (code === 'unauthorized') {
      return this.fail('unauthorized', code, message, STATUS_UNAUTHORIZED)
    }
    if (code === 'invalid_tenant') {
      return this.fail('invalid_tenant', code, message, STATUS_FORBIDDEN)
    }
    if (code === 'blocked' || code === 'logout_incomplete') {
      return this.fail('blocked', code, message, 503)
    }
    return this.fail('network', code, message || 'the request could not be completed', status)
  }

  private mapHttp(status: number, statusText: string, body: string): ContextError {
    let message = statusText
    let code = ''
    try {
      const parsed = JSON.parse(body) as { message?: string; code?: string }
      if (parsed?.message) message = parsed.message
      if (parsed?.code) code = parsed.code
    } catch {
      /* non-JSON error body */
    }
    if (status === STATUS_UNAUTHORIZED) {
      return this.fail('unauthorized', code || 'unauthorized', message || 'unauthorized', status)
    }
    if (PASSWORD_CODES.has(code) || status === 428) {
      return this.fail('password_change_required', code || 'password_change_required', message || 'password change required', status)
    }
    if (TENANT_CODES.has(code)) {
      return this.fail('invalid_tenant', code, message || 'that tenant is not available', status)
    }
    if (status === STATUS_CONFLICT) {
      return this.fail('conflict', code, message || 'conflict', status)
    }
    if (status === STATUS_FORBIDDEN) {
      return this.fail('forbidden', code, message || 'forbidden', status)
    }
    if (status >= 500) {
      return this.fail('unavailable', code, message || 'temporarily unavailable', status)
    }
    return this.fail('forbidden', code, message || `HTTP ${status}`, status)
  }

  /** Map a thrown error to a user-facing message without hiding the state. */
  static describe(e: unknown): string {
    if (e instanceof ContextError) {
      switch (e.kind) {
        case 'unauthorized':
          return 'signed out'
        case 'password_change_required':
          return 'password change required'
        case 'invalid_tenant':
          return 'tenant no longer available'
        case 'stale_context':
          return 'the selected tenant changed'
        case 'blocked':
          return e.message
        case 'unavailable':
          return 'temporarily unavailable'
        case 'conflict':
          return 'conflict'
        default:
          return e.message
      }
    }
    return e instanceof Error ? e.message : String(e)
  }
}

export const desktopContext = new DesktopContext()
export default desktopContext
