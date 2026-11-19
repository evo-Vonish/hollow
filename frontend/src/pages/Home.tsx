import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import Logo from '@/components/Logo'
import SearchBar from '@/components/SearchBar'
import SceneChips from '@/components/SceneChips'

/** 主页 —— 标 + 输入框 + 场景 chips(搜索引擎本质,无多余卡片) */
export default function Home() {
  const navigate = useNavigate()
  const [scene, setScene] = useState('')

  const go = (q: string) => {
    navigate(`/?q=${encodeURIComponent(q)}${scene ? `&scene=${scene}` : ''}`)
  }

  return (
    <div className="flex min-h-screen flex-col">
      <main className="flex flex-1 flex-col items-center justify-center px-5 pb-24">
        <Logo size="lg" />
        <p className="mt-4 text-center text-[13px] text-ink-2">
          343 源召回 · 三档抓取 · 净化阅读
        </p>

        <div className="mt-9 w-full max-w-2xl">
          <SearchBar onSubmit={go} autoFocus size="lg" />
          <div className="mt-4 flex justify-center">
            <SceneChips value={scene} onChange={setScene} />
          </div>
        </div>
      </main>

      <footer className="flex items-center justify-center gap-3.5 pb-6 font-mono text-[11px] text-ink-2">
        <a href="/docs" className="transition-colors hover:text-signal">文档</a>
        <span className="text-line-strong">·</span>
        <a href="/docs/api" className="transition-colors hover:text-signal">API</a>
        <span className="text-line-strong">·</span>
        <Link to="/?engines" className="transition-colors hover:text-signal">引擎注册表</Link>
        <span className="text-line-strong">·</span>
        <span>AGPL-3.0</span>
      </footer>
    </div>
  )
}
