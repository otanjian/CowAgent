import { ipcMain, net } from 'electron'

// A small HTTP relay so the renderer can reach external HTTPS endpoints from
// the main process (the file:// renderer origin is otherwise blocked by CORS).
// It's deliberately generic and carries no product-specific knowledge; any
// optional extension can use it. Requests are limited to https to avoid it
// becoming an open local proxy.
//
// Design D8 narrows it further: the relay is for *foreign* endpoints only. It
// must not become a side door around the auth broker, so it refuses
//   * anything that is not https (a backend on 127.0.0.1 is http),
//   * any loopback / private host (the backend, the callback listener and the
//     asset proxy all live there),
//   * the native auth paths (``/auth/desktop/*``, ``/auth/login``, ...) on any
//     host, since a relayed consent/token call would sidestep the exact-origin
//     and PKCE rules the broker enforces,
//   * any request that carries an Authorization/Cookie header, so the relay can
//     never spend the broker's credential.
// Business traffic goes through ``desktop-request``, which re-authorizes on the
// server for every call; the relay carries no credential of ours at all.

export interface RelayRequest {
  url: string
  method?: string
  headers?: Record<string, string>
  // Stringified body (callers serialize JSON/form themselves).
  body?: string
}

export interface RelayResponse {
  ok: boolean
  status: number
  headers: Record<string, string>
  body: string
}

const MAX_BODY_BYTES = 8 * 1024 * 1024
// Relay callers are all lightweight JSON endpoints (login, codes, balances,
// model lists); a 10s cap is generous while still preventing a stalled request
// from hanging forever (and, for pollers, piling up across ticks).
const REQUEST_TIMEOUT_MS = 10 * 1000

//: Header names the relay must never forward: they are the broker's credential
//: surface, and a relayed credential would make this an authenticated proxy.
const FORBIDDEN_HEADERS = ['authorization', 'cookie', 'proxy-authorization']

//: Paths that belong to the native authorization protocol. Refused on every
//: host, so a relayed call cannot impersonate the broker's exact-origin exchange.
const NATIVE_AUTH_PATHS = ['/auth/desktop/authorize', '/auth/desktop/token', '/auth/login', '/auth/logout']

/** Loopback and private literals: never a relay target. */
function isPrivateHost(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, '')
  if (host === 'localhost' || host === '::1') return true
  if (/^127\./.test(host)) return true
  if (/^10\./.test(host)) return true
  if (/^192\.168\./.test(host)) return true
  if (/^172\.(1[6-9]|2[0-9]|3[01])\./.test(host)) return true
  if (/^169\.254\./.test(host)) return true
  if (/\.local$/.test(host)) return true
  // IPv6 unique-local / link-local
  if (/^f[cd][0-9a-f]{2}:/.test(host)) return true
  if (/^fe80:/.test(host)) return true
  return false
}

/** The refusal reason for a relay target, or '' when it may be relayed. */
export function relayRefusal(req: RelayRequest, url: URL): string {
  if (url.protocol !== 'https:') return 'only https is allowed'
  if (isPrivateHost(url.hostname)) return 'loopback and private hosts are not relayable'
  const path = url.pathname.toLowerCase()
  if (NATIVE_AUTH_PATHS.some((p) => path === p || path.startsWith(`${p}/`))) {
    return 'native authorization paths are not relayable'
  }
  for (const name of Object.keys(req.headers || {})) {
    if (FORBIDDEN_HEADERS.includes(name.toLowerCase())) {
      return 'credential headers are not relayable'
    }
  }
  return ''
}

function relay(req: RelayRequest): Promise<RelayResponse> {
  return new Promise((resolve, reject) => {
    let parsed: URL
    try {
      parsed = new URL(req.url)
    } catch {
      reject(new Error('invalid url'))
      return
    }
    const refusal = relayRefusal(req, parsed)
    if (refusal) {
      reject(new Error(refusal))
      return
    }

    const request = net.request({
      method: req.method || 'GET',
      url: req.url,
    })
    if (req.headers) {
      for (const [k, v] of Object.entries(req.headers)) request.setHeader(k, v)
    }

    // Cap the whole request (connect + response) so a stalled endpoint can't
    // hang forever. On timeout we abort, which surfaces as an 'error' event.
    // `done` guards against settling twice once the timer has fired.
    let done = false
    const timer = setTimeout(() => {
      if (done) return
      request.abort()
      reject(new Error('request timeout'))
    }, REQUEST_TIMEOUT_MS)
    const settle = (fn: () => void) => {
      if (done) return
      done = true
      clearTimeout(timer)
      fn()
    }

    request.on('response', (response) => {
      const chunks: Buffer[] = []
      let size = 0
      let aborted = false
      response.on('data', (chunk: Buffer) => {
        if (aborted || done) return
        size += chunk.length
        if (size > MAX_BODY_BYTES) {
          aborted = true
          request.abort()
          settle(() => reject(new Error('response too large')))
          return
        }
        chunks.push(chunk)
      })
      response.on('end', () => {
        if (aborted) return
        const headers: Record<string, string> = {}
        for (const [k, v] of Object.entries(response.headers)) {
          headers[k] = Array.isArray(v) ? v.join(', ') : String(v)
        }
        const status = response.statusCode || 0
        settle(() =>
          resolve({
            ok: status >= 200 && status < 300,
            status,
            headers,
            body: Buffer.concat(chunks).toString('utf8'),
          }),
        )
      })
    })
    request.on('error', (err) => settle(() => reject(err)))

    if (req.body != null) request.write(req.body)
    request.end()
  })
}

export function setupHttpRelayIPC() {
  ipcMain.handle('http-relay', async (_event, req: RelayRequest) => {
    try {
      return await relay(req)
    } catch (e) {
      return { ok: false, status: 0, headers: {}, body: String((e as Error).message) }
    }
  })
}
