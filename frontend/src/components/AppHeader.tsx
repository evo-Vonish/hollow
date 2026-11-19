import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import Logo from '@/components/Logo'
import SearchBar from '@/components/SearchBar'
import ModeTabs from '@/components/ModeTabs'

interface Props {
  q: string
  mode: 'search' | 'research'
  /** 模式切换目标 URL 构造(保留 scene/filters) */
  buildModeUrl: (mode: 'search' | 'research') => string
  onSearch: (q: string) => void
  searching?: boolean
  /** 第二行:场景 chips(跟在模式 tabs 后,可横向滚动) */
  chips?: ReactNode
  /** 第三行:工具行(过滤器 / 深研控制台) */
  toolbar?: ReactNode
}

/**
 * Results / Research 共用顶栏 —— 三行结构,与正文严格同栏(page-col):
 *   行1 logo + 搜索框
 *   行2 模式 tabs + 场景 chips(溢出横向滚动,不换行)
 *   行3 工具行(过滤器/深研控制台,允许换行)
 */
export default function AppHeader({ q, mode, buildModeUrl, onSearch, searching, chips, toolbar }: Props) {
  return (
    <header className="sticky top-0 z-40 border-b border-line bg-bg-0/90 backdrop-blur">
      <div className="page-col flex items-center gap-3 pt-3">
        <Link to="/" className="shrink-0" aria-label="回主页">
          <Logo size="md" />
        </Link>
        <div className="min-w-0 flex-1">
          <SearchBar initial={q} onSubmit={onSearch} size="sm" loading={searching} />
        </div>
      </div>
      <div className="page-col mt-2.5 flex items-center gap-2 overflow-x-auto pb-1 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        <ModeTabs mode={mode} buildUrl={buildModeUrl} />
        {chips}
      </div>
      {toolbar && <div className="page-col mt-1.5 pb-2.5">{toolbar}</div>}
    </header>
  )
}
