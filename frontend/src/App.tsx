import { Component, Suspense, lazy } from 'react'
import type { ErrorInfo, ReactNode } from 'react'
import { Route, Routes, useSearchParams } from 'react-router-dom'
import { Toaster } from 'sonner'
import Home from '@/pages/Home'
import Results from '@/pages/Results'

/* 路由级代码分割:热路径(Home/Results)直载,其余按需(react-markdown 链随之拆出主包) */
const Reader = lazy(() => import('@/pages/Reader'))
const Research = lazy(() => import('@/pages/Research'))
const Batch = lazy(() => import('@/pages/Batch'))
const Engines = lazy(() => import('@/pages/Engines'))

function PageFallback() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-bg-0">
      <p className="font-mono text-[12px] text-ink-2">加载页面<span className="animate-pulse">…</span></p>
    </div>
  )
}

/**
 * 单路径门路由(主流搜索引擎式 URL 设计,2026-07 定稿):
 *   /search                     → 主页(输入框 + 标题)
 *   /search?q=...               → 搜索结果页(&scene&t&safe&lang&inc&exc&eng&page)
 *   /search?q=...&mode=research → 深研模式(SSE 流式;&rmode&n&budget)
 *   /search?url=...             → 阅读模式(双路赛制);url 与 q 可共存,q 用于"返回结果"
 *   /search?urls=a,b,c          → 批量净化(≤10)
 *   /search?engines             → 引擎注册表浏览
 */
function Gate() {
  const [params] = useSearchParams()
  if (params.get('urls')) return <Suspense fallback={<PageFallback />}><Batch /></Suspense>
  if (params.get('url')) return <Suspense fallback={<PageFallback />}><Reader /></Suspense>
  if (params.get('q')) {
    return params.get('mode') === 'research'
      ? <Suspense fallback={<PageFallback />}><Research /></Suspense>
      : <Results />
  }
  if (params.has('engines')) return <Suspense fallback={<PageFallback />}><Engines /></Suspense>
  return <Home />
}

/** 渲染崩溃兜底:任何页面组件抛错时给诚实卡,而不是黑屏(2026-07-27 黑屏事故后的保险) */
class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }
  static getDerivedStateFromError(error: Error) {
    return { error }
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[hollow-search] render crash:', error, info.componentStack)
  }
  render() {
    if (this.state.error) {
      return (
        <div className="flex min-h-screen items-center justify-center bg-bg-0 px-4">
          <div className="w-full max-w-md rounded-md border border-line bg-bg-1 p-6">
            <p className="font-mono text-[13px] text-amber">页面渲染出错</p>
            <p className="mt-2 break-all font-mono text-[12px] leading-relaxed text-ink-2">
              {String(this.state.error).slice(0, 300)}
            </p>
            <a
              href="/search"
              className="mt-4 inline-block rounded-md border border-line px-4 py-1.5 text-[13px] text-ink-1 transition-colors hover:border-signal hover:text-signal"
            >
              ← 回主页
            </a>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

export default function App() {
  return (
    <div className="min-h-screen bg-bg-0 text-ink-0">
      <ErrorBoundary>
        <Routes>
          <Route path="/" element={<Gate />} />
          <Route path="*" element={<Gate />} />
        </Routes>
      </ErrorBoundary>
      <Toaster
        theme="light"
        position="bottom-right"
        toastOptions={{
          style: { background: '#FEFDFB', border: '1px solid #E6E1D6', color: '#29251F', fontSize: 13 },
        }}
      />
    </div>
  )
}
