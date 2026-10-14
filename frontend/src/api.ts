/** Backend origin. Empty in development (Vite proxy) and when the backend serves the built app itself. */
export const BACKEND: string = (import.meta.env.VITE_BACKEND_URL ?? '').replace(/\/$/, '')
export const apiUrl = (path: string) => `${BACKEND}/api${path}`
export const wsUrl = () => {
  const base = BACKEND || location.origin
  return `${base.replace(/^http/, 'ws')}/ws/live`
}

/** Small fetch wrapper: JSON in, JSON out, and the server's own error text on failure. */
export async function api<T = unknown>(path: string, init?: { method?: string; body?: unknown; form?: FormData }): Promise<T> {
  const res = await fetch(apiUrl(path), {
    method: init?.method ?? (init?.body !== undefined || init?.form ? 'POST' : 'GET'),
    headers: init?.body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: init?.form ?? (init?.body !== undefined ? JSON.stringify(init.body) : undefined),
  })
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const j = await res.json()
      if (j?.detail) detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch { /* body was not JSON */ }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export const fmt = (v: number | null | undefined, digits = 1) => (v == null || Number.isNaN(v) ? '–' : v.toFixed(digits))
export const clock = (ts: number) => new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
