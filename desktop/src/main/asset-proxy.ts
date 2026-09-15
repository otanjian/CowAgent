import { randomBytes } from 'crypto'
import * as http from 'http'
import type { AddressInfo } from 'net'

// A loopback-only asset/stream proxy.
//
// Why this exists
// ---------------
// Some Desktop transports cannot carry an Authorization header at all: an
// ``EventSource`` (chat stream, log stream, reconnects), an ``<img>``/``<audio>``
// preview, and a browser-initiated download. The old renderer solved that by
// putting the session token in the URL query string, which is exactly what
// design D8 forbids: a token in a URL leaks through history, Referer, logs and
// every crash report, and it hands the renderer a reusable credential.
//
// Instead, the main process -- which is the only place the Bearer ever lives --
// mints an opaque, high-entropy, short-lived id for one backend path and starts
// this server on ``127.0.0.1`` with an ephemeral port. The renderer only ever
// sees ``http://127.0.0.1:<port>/a/<id>``: no token, no tenant, no backend
// origin. When the request arrives, the broker attaches the *current* session
// bearer and tenant and streams the backend's answer through, so a stream that
// reconnects after a tenant switch uses the new context, and a revoked session
// is refused by the backend on the very next read.
//
// What it deliberately does not do
// --------------------------------
// * It never binds anything but ``127.0.0.1`` (no ``0.0.0.0``, no IPv6 wildcard).
// * It serves only ``GET`` and only ids it minted itself; there is no path, host
//   or header passthrough, so it cannot be used as a general proxy.
// * Ids are epoch-tagged and expire (``ASSET_TTL_MS``); a tenant switch drops the
//   previous epoch's ids, so a late response cannot re-populate a new tenant's
//   view with old data.
// * It never logs the id or the URL, and responses are marked ``no-store``.

/** Bound to the literal loopback address, never a wildcard. */
const BIND_HOST = '127.0.0.1'

/** How long a minted id stays valid. Long enough for a download to start. */
const ASSET_TTL_MS = 10 * 60 * 1000

/** What the proxy is allowed to ask the broker to fetch. */
export interface AssetTarget {
  /** Backend path, always absolute and same-origin (``/api/...``). */
  path: string
  /** ``get`` is a one-shot read; ``stream`` is piped and held open. */
  kind: 'get' | 'stream'
  /** The context epoch this id was minted under. */
  epoch: number
}

/** The broker's answer for one asset target, already credentialed. */
export interface UpstreamReply {
  status: number
  headers: http.IncomingHttpHeaders
  stream: NodeJS.ReadableStream
  /** Abort/cleanup hook, called when the client goes away first. */
  cancel: () => void
}

/** Provided by the broker: performs the credentialed backend request. */
export type UpstreamFetcher = (target: AssetTarget) => Promise<UpstreamReply>

//: Response headers safe to copy through. Deliberately a list, not "everything
//: except": a hop-by-hop or credential-ish header must never be forwarded.
const FORWARDED_HEADERS = [
  'content-type',
  'content-length',
  'content-disposition',
  'cache-control',
  'etag',
  'last-modified',
]

export class AssetProxy {
  private server: http.Server | null = null
  private port = 0
  private readonly assets = new Map<string, AssetTarget & { expiresAt: number }>()

  constructor(private readonly fetchUpstream: UpstreamFetcher) {}

  /** Whether the loopback listener is bound and ready to mint ids. */
  isRunning(): boolean {
    return this.server !== null
  }

  /** Bind the loopback server. Idempotent; returns the bound port. */
  async start(): Promise<number> {
    if (this.server) return this.port
    const server = http.createServer((req, res) => {
      void this.handle(req, res)
    })
    // A dead client must not take the main process down with it.
    server.on('clientError', (_err, socket) => {
      if (socket.writable) socket.end('HTTP/1.1 400 Bad Request\r\n\r\n')
    })
    await new Promise<void>((resolve, reject) => {
      server.once('error', reject)
      server.listen(0, BIND_HOST, () => {
        server.removeListener('error', reject)
        resolve()
      })
    })
    const address = server.address() as AddressInfo
    this.server = server
    this.port = address.port
    return this.port
  }

  /** Mint an opaque id for one backend path; returns the renderer-facing URL. */
  mint(path: string, kind: 'get' | 'stream', epoch: number): string {
    if (!this.server) throw new Error('asset proxy is not running')
    const id = randomBytes(24).toString('hex')
    this.assets.set(id, { path, kind, epoch, expiresAt: Date.now() + ASSET_TTL_MS })
    // Opportunistic sweep; the map is tiny and short-lived by construction.
    const now = Date.now()
    for (const [key, value] of this.assets) {
      if (value.expiresAt <= now) this.assets.delete(key)
    }
    return `http://${BIND_HOST}:${this.port}/a/${id}`
  }

  /** Drop every id minted under a previous context (tenant switch / logout). */
  dropEpochsExcept(epoch: number): void {
    for (const [key, value] of this.assets) {
      if (value.epoch !== epoch) this.assets.delete(key)
    }
  }

  /** Drop everything (logout, backend change). */
  clear(): void {
    this.assets.clear()
  }

  close(): void {
    this.assets.clear()
    this.server?.close()
    this.server = null
  }

  private async handle(req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
    const fail = (status: number, code: string) => {
      res.writeHead(status, {
        'Content-Type': 'application/json; charset=utf-8',
        'Cache-Control': 'no-store',
      })
      res.end(JSON.stringify({ status: 'error', code }))
    }
    if ((req.method || 'GET').toUpperCase() !== 'GET') return fail(405, 'method_not_allowed')
    // The server is bound to loopback, but keep the host check anyway so a
    // DNS-rebinding style request that reaches it is still refused.
    const host = (req.headers.host || '').toLowerCase()
    if (!host.startsWith(`${BIND_HOST}:`) && host !== BIND_HOST) return fail(403, 'bad_host')

    const match = /^\/a\/([0-9a-f]{48})$/.exec(req.url || '')
    if (!match) return fail(404, 'not_found')
    const target = this.assets.get(match[1])
    if (!target || target.expiresAt <= Date.now()) {
      this.assets.delete(match[1])
      return fail(404, 'not_found')
    }

    let reply: UpstreamReply
    try {
      reply = await this.fetchUpstream(target)
    } catch {
      return fail(503, 'asset_unavailable')
    }
    if (res.headersSent) {
      reply.cancel()
      return
    }
    const headers: Record<string, string> = {
      'Cache-Control': 'no-store',
      // The renderer document runs from `file://` (or the Vite dev server), so
      // an EventSource / fetch read of this loopback URL is cross-origin. The
      // id is a 24-byte secret that only this process minted, so allowing any
      // origin to *read* it grants nothing an attacker can guess -- and without
      // it EventSource (which cannot send a header) would be refused outright.
      'Access-Control-Allow-Origin': '*',
    }
    for (const name of FORWARDED_HEADERS) {
      const value = reply.headers[name]
      if (typeof value === 'string') headers[name] = value
    }
    // A ranged/streamed body whose length we cannot trust: let Node re-chunk it
    // rather than advertise a stale Content-Length.
    if (target.kind === 'stream') delete headers['content-length']
    res.writeHead(reply.status, headers)
    reply.stream.pipe(res)
    const cancel = () => reply.cancel()
    reply.stream.on('error', cancel)
    res.on('close', cancel)
  }
}
