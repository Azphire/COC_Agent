export function hostToken() { return sessionStorage.getItem('coc.host') || '' }
export function authHeaders(token = hostToken()): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {}
}
export function socketUrl(path: string) {
  const url = new URL(path, window.location.href)
  url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.href
}
export function requestId() {
  // getRandomValues also works on HTTP LAN origins; randomUUID may not.
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 15) | 64
  bytes[8] = (bytes[8] & 63) | 128
  const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}
export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) { super(message); this.status = status }
}
export async function api<T>(path: string, token: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method, headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(path === '/model/test' || path.endsWith('/generate-draft') || /^\/party-batches\/[^/]+\/next$/.test(path) ? 600000 : path.endsWith('/structure/build') || path.startsWith('/launch-drafts/') || path === '/launch/rules/refresh' ? 180000 : 15000),
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    const issues = (data.detail?.issues || []).map((issue: { field: string; message: string }) => `${issue.field}：${issue.message}`).join('\n')
    const message = data.detail?.message || (typeof data.detail === 'string' ? data.detail : '') || (response.status >= 500 ? `连接或服务暂不可用（${response.status}），请稍后重试；已保存进度可以恢复` : `请求失败（${response.status}），请检查输入`)
    throw new ApiError([message, issues].filter(Boolean).join('\n'), response.status)
  }
  return response.json()
}
