import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Filter, Settings2 } from 'lucide-react'
import { LANGUAGES, SAFESEARCH_OPTIONS, TIME_RANGES, parseDomains } from '@/lib/sdk'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

export interface FilterValues {
  /** time_range:'' 不限 | day | week | month | year */
  time: string
  /** safesearch 0/1/2 */
  safe: number
  /** language:auto 或 BCP47 */
  lang: string
  include: string[]
  exclude: string[]
  /** 自定义引擎点名(并入场景集) */
  engines: string[]
}

export const DEFAULT_FILTERS: FilterValues = {
  time: '',
  safe: 0,
  lang: 'auto',
  include: [],
  exclude: [],
  engines: [],
}

interface Props {
  value: FilterValues
  onChange: (next: FilterValues) => void
}

/**
 * 工具栏:时间 / 安全搜索 / 语言 / 域过滤 / 高级(引擎点名)。
 * 有激活过滤的控件亮信号色——状态一眼可见。
 */
export default function FilterBar({ value, onChange }: Props) {
  const domainsActive = value.include.length > 0 || value.exclude.length > 0
  const enginesActive = value.engines.length > 0

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Select value={value.time || 'any'} onValueChange={(v) => onChange({ ...value, time: v === 'any' ? '' : v })}>
        <SelectTrigger className={cn(value.time && 'border-signal/50 text-signal')} aria-label="时间范围">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {TIME_RANGES.map((t) => (
            <SelectItem key={t.id || 'any'} value={t.id || 'any'}>{t.label}</SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select value={String(value.safe)} onValueChange={(v) => onChange({ ...value, safe: Number(v) })}>
        <SelectTrigger className={cn(value.safe > 0 && 'border-signal/50 text-signal')} aria-label="安全搜索">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {SAFESEARCH_OPTIONS.map((s) => (
            <SelectItem key={s.value} value={String(s.value)}>{s.label}</SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select value={value.lang} onValueChange={(v) => onChange({ ...value, lang: v })}>
        <SelectTrigger className={cn(value.lang !== 'auto' && 'border-signal/50 text-signal')} aria-label="语言">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {LANGUAGES.map((l) => (
            <SelectItem key={l.id} value={l.id}>{l.label}</SelectItem>
          ))}
        </SelectContent>
      </Select>

      <DomainsPopover value={value} onChange={onChange} active={domainsActive} />
      <EnginesPopover value={value} onChange={onChange} active={enginesActive} />
    </div>
  )
}

/* ---------- 域过滤弹层:只保留/剔除域(逗号/空格分隔) ---------- */

function DomainsPopover({ value, onChange, active }: { value: FilterValues; onChange: (v: FilterValues) => void; active: boolean }) {
  const [open, setOpen] = useState(false)
  const [inc, setInc] = useState(value.include.join(' '))
  const [exc, setExc] = useState(value.exclude.join(' '))
  useEffect(() => {
    if (open) {
      setInc(value.include.join(' '))
      setExc(value.exclude.join(' '))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const apply = () => {
    onChange({ ...value, include: parseDomains(inc), exclude: parseDomains(exc) })
    setOpen(false)
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="subtle" size="sm" className={cn('font-mono', active && 'border-signal/50 text-signal')}>
          <Filter className="size-3.5" />
          域过滤{active && ` · ${value.include.length + value.exclude.length}`}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80">
        <p className="font-mono text-[11px] uppercase tracking-wider text-ink-2">域过滤(召回之后过滤)</p>
        <div className="mt-3 space-y-3">
          <div>
            <label className="mb-1 block text-[12px] text-ink-1">只保留这些域(含子域)</label>
            <Input
              value={inc}
              onChange={(e) => setInc(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && apply()}
              placeholder="如 wikipedia.org github.com"
            />
          </div>
          <div>
            <label className="mb-1 block text-[12px] text-ink-1">剔除这些域(含子域)</label>
            <Input
              value={exc}
              onChange={(e) => setExc(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && apply()}
              placeholder="如 pinterest.com"
            />
          </div>
          <div className="flex items-center justify-between">
            <p className="font-mono text-[10px] text-ink-2/60">空格/逗号分隔;自动去协议去路径</p>
            <Button size="sm" variant="primary" onClick={apply}>应用</Button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  )
}

/* ---------- 高级弹层:引擎点名 ---------- */

function EnginesPopover({ value, onChange, active }: { value: FilterValues; onChange: (v: FilterValues) => void; active: boolean }) {
  const [open, setOpen] = useState(false)
  const [eng, setEng] = useState(value.engines.join(', '))
  useEffect(() => {
    if (open) setEng(value.engines.join(', '))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const apply = () => {
    const list = eng.split(/[,;]/).map((s) => s.trim()).filter(Boolean)
    onChange({ ...value, engines: [...new Set(list)] })
    setOpen(false)
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="subtle" size="sm" className={cn('font-mono', active && 'border-signal/50 text-signal')}>
          <Settings2 className="size-3.5" />
          高级{active && ` · ${value.engines.length}`}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80">
        <p className="font-mono text-[11px] uppercase tracking-wider text-ink-2">自定义引擎点名</p>
        <p className="mt-1.5 text-[12px] leading-relaxed text-ink-2">
          并入场景集一起查;点名 removed 或未知名会被网关 400 拒绝(如实回报)。
          可用引擎见 <Link to="/?engines" className="text-cyan hover:underline">引擎注册表</Link>。
        </p>
        <Input
          className="mt-3"
          value={eng}
          onChange={(e) => setEng(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && apply()}
          placeholder="如 github, mdn, arxiv"
        />
        <div className="mt-3 flex justify-end">
          <Button size="sm" variant="primary" onClick={apply}>应用</Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}
