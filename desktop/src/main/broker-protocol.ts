// Wire shapes shared by the main-process broker and the preload bridge.
//
// The renderer mirrors these in ``src/renderer/src/types.ts`` (it cannot import
// from this directory: the renderer and the main process are compiled by two
// separate tsconfigs with different roots). Keep the two in step; the mirror is
// checked by tests/test_desktop_context_frontend.cjs.

/** The desensitized native session the renderer may see. Never a token. */
export interface BrokerSessionWire {
  userId: string
  username: string
  displayName: string
  isPlatformAdmin: boolean
  mustChangePassword: boolean
  tenants: Array<{ id: string; code: string; name: string }>
  tenantId: string | null
  /** Monotonic context id; a response from another epoch must be discarded. */
  epoch: number
}

export interface BrokerFailure {
  ok: false
  code: string
  message: string
  status: number
}

export interface BrokerVoidReply {
  ok: boolean
  code?: string
  message?: string
  status?: number
}

export interface BrokerProbe extends BrokerVoidReply {
  authRequired?: boolean
  identityMode?: string
}

export interface BrokerStatusReply extends BrokerVoidReply {
  session?: BrokerSessionWire | null
  /** Non-empty when the app must stop business requests (failed sign-out). */
  blockedReason?: string
  authRequired?: boolean
  identityMode?: string
}

export interface BrokerSessionReply extends BrokerVoidReply {
  session?: BrokerSessionWire
}

export interface BrokerLogoutReply extends BrokerVoidReply {
  revoked?: boolean
}

export interface BrokerRequestReply extends BrokerVoidReply {
  status?: number
  statusText?: string
  contentType?: string
  body?: string
}

export interface BrokerAssetReply extends BrokerVoidReply {
  /** Opaque loopback URL: carries no token, no tenant and no backend origin. */
  url?: string
}
