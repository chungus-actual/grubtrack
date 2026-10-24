// Every call goes through here. The server stamps X-GT-Build on each response; when a deploy
// changes it, an open tab reloads itself at the next idle moment instead of running old code
// against a new API.

let build = null
let stale = false

function noteBuild(res) {
  const b = res.headers.get('X-GT-Build')
  if (!b) return
  if (build === null) build = b
  else if (b !== build) stale = true
}

export function reloadIfStale() {
  if (!stale) return false
  const busy =
    document.querySelector('.sheet') ||
    (document.activeElement && /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName))
  if (busy) return false
  try {
    if (sessionStorage.getItem('gt.reloadedFor') === 'pending') return false
    sessionStorage.setItem('gt.reloadedFor', 'pending')
  } catch {}
  location.reload()
  return true
}
try { sessionStorage.removeItem('gt.reloadedFor') } catch {}

export class ApiError extends Error {
  constructor(status, message) {
    super(message)
    this.status = status
  }
}

async function check(res) {
  noteBuild(res)
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`
    try {
      const j = await res.json()
      if (j.detail) msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch {}
    throw new ApiError(res.status, msg)
  }
  return res
}

async function call(method, path, body) {
  const res = await check(await fetch(`/api${path}`, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  }))
  return res.status === 204 ? null : res.json()
}

export const get = (p) => call('GET', p)
export const post = (p, b = {}) => call('POST', p, b)
export const patch = (p, b) => call('PATCH', p, b)
export const put = (p, b) => call('PUT', p, b)
export const del = (p) => call('DELETE', p)

// one file as multipart form data, e.g. upload('/receipts/read', 'photo', blob, 'receipt.jpg')
export async function upload(path, field, blob, filename) {
  const fd = new FormData()
  fd.append(field, blob, filename)
  const res = await check(await fetch(`/api${path}`, { method: 'POST', body: fd }))
  return res.json()
}

// Server-sent events from the AI: calls onText for each chunk, resolves when done.
export async function stream(path, body, onText, signal) {
  const res = await check(await fetch(`/api${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
    signal,
  }))
  const reader = res.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    const parts = buf.split('\n\n')
    buf = parts.pop()
    for (const p of parts) {
      const line = p.replace(/^data: /, '')
      if (line === '[DONE]') return
      try {
        const j = JSON.parse(line)
        if (j.error) throw new ApiError(500, j.error)
        if (j.text) onText(j.text)
      } catch (e) {
        if (e instanceof ApiError) throw e
      }
    }
  }
}

export const img = (path) => (path ? `/api/images/${String(path).replace(/^\/+/, '')}` : null)
