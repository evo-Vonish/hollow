/**
 * POST 式 SSE 读取器 —— /v1/research stream:true 专用。
 *
 * 为什么手写:EventSource 只支持 GET,而深研端点是 POST + JSON body。
 * 网关帧格式(v1.py _sse):
 *   data: {json}\n\n        —— 语义化事件(research.search.completed / item.completed / completed)
 *   : heartbeat\n\n         —— 注释心跳(防反代掐空闲连接),客户端忽略
 *   data: [DONE]\n\n        —— 结束哨兵
 */
import { API_BASE, ApiError } from './client'

interface ErrorEnvelope {
  error?: { message?: string; code?: string; type?: string; param?: string | null }
}

export interface SSEStreamOptions {
  /** 中途取消(用户点停止 / 页面卸载) */
  signal?: AbortSignal
  /** 连接阶段超时(默认 30s;开流后不设限,心跳保活) */
  connectTimeoutMs?: number
}

/**
 * 开流并逐事件回调。resolve 于 [DONE] 或流正常结束;
 * reject 于:HTTP 错误(开流前的标准错误体)/ 网络断 / 客户端取消。
 */
export async function postSSE<TEvent>(
  path: string,
  body: unknown,
  onEvent: (evt: TEvent) => void,
  opts: SSEStreamOptions = {},
): Promise<void> {
  const ctrl = new AbortController()
  const outer = opts.signal
  const onOuterAbort = () => ctrl.abort()
  outer?.addEventListener('abort', onOuterAbort)
  const timer = setTimeout(() => ctrl.abort(), opts.connectTimeoutMs ?? 30000)

  let resp: Response
  try {
    resp = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    })
  } catch (e) {
    clearTimeout(timer)
    outer?.removeEventListener('abort', onOuterAbort)
    if (e instanceof DOMException && e.name === 'AbortError') {
      throw new ApiError(outer?.aborted ? '已取消' : '连接超时(客户端)', outer?.aborted ? 'cancelled' : 'client_error', 0)
    }
    throw new ApiError('网络不可达', 'client_error', 0)
  }
  clearTimeout(timer)

  // 开流前错误走标准 JSON 错误体(搜索类错误仍是 HTTP 错误响应,不是事件)
  if (!resp.ok) {
    outer?.removeEventListener('abort', onOuterAbort)
    const data = (await resp.json().catch(() => null)) as ErrorEnvelope | null
    const err = data?.error
    throw new ApiError(err?.message ?? `HTTP ${resp.status}`, err?.code ?? 'unknown', resp.status, err?.type, err?.param)
  }
  if (!resp.body) {
    outer?.removeEventListener('abort', onOuterAbort)
    throw new ApiError('响应无流体(浏览器不支持 ReadableStream?)', 'client_error', 0)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buf = ''

  const flushFrame = (frame: string): boolean => {
    // 返回 true = 收到 [DONE],调用方收尾
    const dataLines = frame
      .split('\n')
      .filter((l) => l.startsWith('data:'))
      .map((l) => l.slice(5).trimStart())
    if (dataLines.length === 0) return false // 纯心跳/注释帧
    const payload = dataLines.join('\n')
    if (payload === '[DONE]') return true
    try {
      onEvent(JSON.parse(payload) as TEvent)
    } catch {
      /* 单帧坏不毁整流(底线:不静默——console 留痕) */
      console.warn('[hollow-sdk] bad SSE frame:', payload.slice(0, 200))
    }
    return false
  }

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      // SSE 帧以空行(\n\n)分隔;网关用 \n 不换 \r\n,但防御兼容
      let idx: number
      while ((idx = buf.search(/\r?\n\r?\n/)) !== -1) {
        const frame = buf.slice(0, idx)
        buf = buf.slice(idx).replace(/^\r?\n\r?\n/, '')
        if (flushFrame(frame)) return
      }
    }
    // 流结束:尾帧可能没有收尾空行,补处理
    if (buf.trim()) flushFrame(buf)
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      throw new ApiError('已取消', 'cancelled', 0)
    }
    throw new ApiError(e instanceof Error ? e.message : '流中断', 'stream_error', 0)
  } finally {
    outer?.removeEventListener('abort', onOuterAbort)
    reader.cancel().catch(() => undefined)
  }
}
