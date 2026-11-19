/**
 * 会话级缓存 + inflight 去重(延迟优化:hover 预取 + 二次打开秒开)。
 * 存储后端可注入——Web 用 sessionStorage;客户端(Tauri)可换成磁盘/内存实现,
 * SDK 其余部分不变。
 */

export interface KVStorage {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
  keys(): string[]
}

/** sessionStorage 适配(浏览器默认可用;隐私模式下 setItem 会抛,全部防御) */
function sessionAdapter(): KVStorage {
  return {
    getItem: (k) => sessionStorage.getItem(k),
    setItem: (k, v) => sessionStorage.setItem(k, v),
    removeItem: (k) => sessionStorage.removeItem(k),
    keys: () => Object.keys(sessionStorage),
  }
}

/** 内存兜底(无 sessionStorage 环境/客户端初期) */
export function memoryAdapter(): KVStorage {
  const m = new Map<string, string>()
  return {
    getItem: (k) => m.get(k) ?? null,
    setItem: (k, v) => void m.set(k, v),
    removeItem: (k) => void m.delete(k),
    keys: () => [...m.keys()],
  }
}

let store: KVStorage
try {
  store = sessionAdapter()
  store.setItem('__hollow_probe__', '1')
  store.removeItem('__hollow_probe__')
} catch {
  store = memoryAdapter()
}

/** 允许宿主替换存储(客户端接入点) */
export function setStorage(s: KVStorage) {
  store = s
}

const CACHE_TTL = 10 * 60 * 1000 // 10 分钟
const CACHE_PREFIX = 'hollow:'
const inflight = new Map<string, Promise<unknown>>()

export function cacheGet<T>(key: string): T | null {
  try {
    const raw = store.getItem(CACHE_PREFIX + key)
    if (!raw) return null
    const { t, v } = JSON.parse(raw) as { t: number; v: T }
    return Date.now() - t < CACHE_TTL ? v : null
  } catch {
    return null
  }
}

export function cacheSet(key: string, v: unknown) {
  try {
    store.setItem(CACHE_PREFIX + key, JSON.stringify({ t: Date.now(), v }))
  } catch {
    // 超容量:清掉一半再试;仍失败则放弃缓存,不影响功能
    try {
      const keys = store.keys().filter((k) => k.startsWith(CACHE_PREFIX))
      keys.slice(0, Math.ceil(keys.length / 2)).forEach((k) => store.removeItem(k))
      store.setItem(CACHE_PREFIX + key, JSON.stringify({ t: Date.now(), v }))
    } catch {
      /* 放弃缓存 */
    }
  }
}

export function cached<T>(key: string, fn: () => Promise<T>): Promise<T> {
  const hit = cacheGet<T>(key)
  if (hit) return Promise.resolve(hit)
  const pending = inflight.get(key) as Promise<T> | undefined
  if (pending) return pending
  const p = fn()
    .then((v) => {
      cacheSet(key, v)
      inflight.delete(key)
      return v
    })
    .catch((e) => {
      inflight.delete(key)
      throw e
    })
  inflight.set(key, p)
  return p
}

export function isInflight(key: string): boolean {
  return inflight.has(key)
}
