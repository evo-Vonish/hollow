import { Activity, AlertTriangle, CheckCircle2, MinusCircle } from 'lucide-react'
import type { SearchMeta } from '@/lib/sdk'
import { fmtMs } from '@/lib/sdk'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Drawer, DrawerContent, DrawerTrigger } from '@/components/ui/drawer'

interface Props {
  meta: SearchMeta
  /** 触发器自定义;不传用默认按钮 */
  trigger?: React.ReactNode
}

/**
 * 引擎账目抽屉 —— 每次搜索的引擎级明细(底线②:不静默丢弃)。
 * requested / used / failed(带原因)/ no_results / took_ms / q_sanitized 如实列出。
 */
export default function EngineLedgerDrawer({ meta, trigger }: Props) {
  const failedCount = meta.engines_failed.length
  return (
    <Drawer direction="right">
      <DrawerTrigger asChild>
        {trigger ?? (
          <Button variant="subtle" size="sm" className="font-mono">
            <Activity className="size-3.5" />
            引擎账目
            {failedCount > 0 && <span className="text-amber">{failedCount}✕</span>}
          </Button>
        )}
      </DrawerTrigger>
      <DrawerContent title="引擎账目 · engine ledger">
        <div className="space-y-6">
          {/* 总览 */}
          <section>
            <div className="grid grid-cols-3 gap-2 text-center">
              <Stat label="请求" value={meta.engines_requested.length} />
              <Stat label="有产出" value={meta.engines_used.length} tone="signal" />
              <Stat label="失败" value={failedCount} tone={failedCount > 0 ? 'amber' : undefined} />
              <Stat label="零结果" value={meta.engines_no_results.length} />
              <Stat label="召回总数" value={meta.results_total} />
              <Stat label="耗时" value={fmtMs(meta.took_ms)} raw />
            </div>
            {meta.q_sanitized && (
              <p className="mt-3 rounded-md border border-amber/30 bg-amber/5 px-3 py-2 font-mono text-[11px] leading-relaxed text-amber">
                bang/filter 防护改写过查询词(q_sanitized)
              </p>
            )}
            {/* 健康熔断(2026-07-30):连续失败的引擎被自适应剔除,到期探测自动恢复 */}
            {(meta.engines_degraded?.length ?? 0) > 0 && (
              <div className="mt-3 rounded-md border border-line bg-bg-2/60 px-3 py-2">
                <p className="font-mono text-[11px] uppercase tracking-wider text-ink-2">熔断退避 · {meta.engines_degraded.length}</p>
                <ul className="mt-1.5 space-y-1">
                  {meta.engines_degraded.map((d) => (
                    <li key={d.engine} className="font-mono text-[11px] leading-relaxed text-ink-2">
                      <span className="text-amber">{d.engine}</span>
                      {d.probing
                        ? <span className="text-signal"> · 探测恢复中</span>
                        : <span> · {d.retry_after_s}s 后探测</span>}
                      <span className="block break-all text-ink-2/60">{d.reason}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>

          <LedgerSection
            icon={<CheckCircle2 className="size-3.5 text-signal" />}
            title={`有产出 · ${meta.engines_used.length}`}
          >
            {meta.engines_used.length ? (
              <ChipRow items={meta.engines_used} variant="signal" />
            ) : (
              <p className="text-[12px] text-ink-2">(无)</p>
            )}
          </LedgerSection>

          <LedgerSection
            icon={<AlertTriangle className="size-3.5 text-amber" />}
            title={`失败 · ${meta.engines_failed.length}`}
          >
            {meta.engines_failed.length ? (
              <ul className="space-y-1.5">
                {meta.engines_failed.map((f) => (
                  <li key={f.engine} className="rounded-md border border-line bg-bg-0 px-3 py-2">
                    <span className="font-mono text-[12px] text-amber">{f.engine}</span>
                    <span className="mt-0.5 block break-all font-mono text-[11px] leading-relaxed text-ink-2">{f.reason}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[12px] text-ink-2">(无)</p>
            )}
          </LedgerSection>

          <LedgerSection
            icon={<MinusCircle className="size-3.5 text-ink-2" />}
            title={`零结果 · ${meta.engines_no_results.length}`}
            hint="请求了但零产出零报错(零匹配或静默失败,不可区分;非确定失败)"
          >
            {meta.engines_no_results.length ? (
              <ChipRow items={meta.engines_no_results} />
            ) : (
              <p className="text-[12px] text-ink-2">(无)</p>
            )}
          </LedgerSection>

          <LedgerSection title={`请求了 · ${meta.engines_requested.length}`}>
            <ChipRow items={meta.engines_requested} />
          </LedgerSection>
        </div>
      </DrawerContent>
    </Drawer>
  )
}

function Stat({ label, value, tone, raw }: { label: string; value: number | string; tone?: 'signal' | 'amber'; raw?: boolean }) {
  const cls = tone === 'signal' ? 'text-signal' : tone === 'amber' ? 'text-amber' : 'text-ink-0'
  return (
    <div className="rounded-md border border-line bg-bg-0 px-2 py-2.5">
      <p className={`font-mono ${raw ? 'text-[13px]' : 'text-[16px]'} tabular ${cls}`}>{value}</p>
      <p className="mt-0.5 text-[10px] text-ink-2">{label}</p>
    </div>
  )
}

function LedgerSection({ icon, title, hint, children }: { icon?: React.ReactNode; title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section>
      <p className="flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-wider text-ink-2">
        {icon}
        {title}
      </p>
      {hint && <p className="mt-1 text-[11px] leading-relaxed text-ink-2/70">{hint}</p>}
      <div className="mt-2">{children}</div>
    </section>
  )
}

function ChipRow({ items, variant }: { items: string[]; variant?: 'signal' }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((e) => (
        <Badge key={e} variant={variant === 'signal' ? 'signal' : 'default'}>{e}</Badge>
      ))}
    </div>
  )
}
