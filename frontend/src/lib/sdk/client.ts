/**
 * HOLLOW API 底层 HTTP 客户端。
 * 生产:同源调用(hollow.vonish.dev/search → /v1/*,无 CORS)。
 * 开发/独立部署:用 VITE_API_BASE 指向任意 HOLLOW 实例(如 https://hollow.vonish.dev)。
 * 零 React 依赖——客户端(Tauri/Electron)可整体搬运 sdk/ 目录。
 */

export const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? ''

/** OpenAI 风格错误规约:{error:{message,type,param,code}} + HTTP 状态码 */
export class ApiError extends Error {
  code: string
  status: number
  type?: string
  param?: string | null
  constructor(message: string, code: string, status: number, type?: string, param?: string | null) {
    super(message)
    this.code = code
    this.status = status
    this.type = type
    this.param = param
  }
}

interface ErrorEnvelope {
  error?: { message?: string; code?: string; type?: string; param?: string | null }
}

function toApiError(e: unknown): ApiError {
  if (e instanceof ApiError) return e
  if (e instanceof DOMException && e.name === 'AbortError') {
    return new ApiError('请求超时(客户端)', 'client_error', 0)
  }
  return new ApiError(e instanceof Error ? e.message : '网络不可达', 'client_error', 0)
}

export async function post<T>(path: string, body: unknown, timeoutMs = 30000): Promise<T> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  let resp: Response
  try {
    resp = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    })
  } catch (e) {
    clearTimeout(timer)
    throw toApiError(e)
  }
  clearTimeout(timer)
  const data = (await resp.json().catch(() => null)) as (T & ErrorEnvelope) | null
  if (!resp.ok) {
    const err = (data as ErrorEnvelope | null)?.error
    throw new ApiError(err?.message ?? `HTTP ${resp.status}`, err?.code ?? 'unknown', resp.status, err?.type, err?.param)
  }
  return data as T
}

export async function get<T>(path: string, timeoutMs = 15000): Promise<T> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  let resp: Response
  try {
    resp = await fetch(`${API_BASE}${path}`, { signal: ctrl.signal })
  } catch (e) {
    clearTimeout(timer)
    throw toApiError(e)
  }
  clearTimeout(timer)
  const data = (await resp.json().catch(() => null)) as (T & ErrorEnvelope) | null
  if (!resp.ok) {
    const err = (data as ErrorEnvelope | null)?.error
    throw new ApiError(err?.message ?? `HTTP ${resp.status}`, err?.code ?? 'unknown', resp.status, err?.type, err?.param)
  }
  return data as T
}
