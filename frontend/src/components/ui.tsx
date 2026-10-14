import { AlertTriangle, Check, ChevronDown, CircleAlert, Loader2, Minus, OctagonAlert, Wrench } from 'lucide-react'
import { useCallback, useState, type ReactNode } from 'react'
import { useI18n } from '../i18n'
import type { Status, ToolCall } from '../types'

/** Run an async action with loading and error state. */
export function useAction<A extends unknown[], R>(fn: (...a: A) => Promise<R>) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const run = useCallback(async (...a: A): Promise<R | undefined> => {
    setBusy(true); setError(null)
    try { return await fn(...a) } catch (e) { setError((e as Error).message); return undefined } finally { setBusy(false) }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fn])
  return { run, busy, error, clear: () => setError(null) }
}

export function Spinner({ size = 14 }: { size?: number }) {
  const { t } = useI18n()
  return <Loader2 size={size} className="animate-spin" aria-label={t('c.working')} />
}

export function ErrorNote({ children }: { children: ReactNode }) {
  if (!children) return null
  return (
    <div role="alert" className="notice flex gap-2" style={{ borderColor: 'var(--critical)' }}>
      <CircleAlert size={15} className="mt-0.5 shrink-0" style={{ color: 'var(--critical)' }} />
      <span className="break-words min-w-0">{children}</span>
    </div>
  )
}

const STATUS = {
  ok: { color: 'var(--good)', Icon: Check },
  watch: { color: 'var(--warning)', Icon: AlertTriangle },
  breach: { color: 'var(--critical)', Icon: OctagonAlert },
  none: { color: 'var(--muted)', Icon: Minus },
} as const

/** Status is always icon + label; the colour is never the only signal. */
export function StatusChip({ status }: { status: Status }) {
  const { t } = useI18n()
  const s = STATUS[status]
  return <span className="chip"><s.Icon size={11} style={{ color: s.color }} strokeWidth={2.6} />{t(`status.${status}`)}</span>
}

const HEALTH = {
  Healthy: { color: 'var(--good)', Icon: Check },
  Stressed: { color: 'var(--warning)', Icon: AlertTriangle },
  Critical: { color: 'var(--critical)', Icon: OctagonAlert },
} as const
export const healthColor = (h: string) => HEALTH[h as keyof typeof HEALTH]?.color ?? 'var(--muted)'

export function HealthBadge({ status, large }: { status: string; large?: boolean }) {
  const { tm } = useI18n()
  const h = HEALTH[status as keyof typeof HEALTH]
  if (!h) return <span className="chip">{status}</span>
  return (
    <span className={`inline-flex items-center gap-1.5 font-semibold ${large ? 'text-lg' : 'text-xs'}`}>
      <span className="inline-flex items-center justify-center rounded-full shrink-0" style={{ background: h.color, width: large ? 22 : 16, height: large ? 22 : 16 }}>
        <h.Icon size={large ? 13 : 10} color="#fff" strokeWidth={3} />
      </span>
      {tm(`health.${status}`, status)}
    </span>
  )
}

/** Expandable list of the tool calls behind an agent's answer. */
export function ToolCalls({ calls }: { calls?: ToolCall[] }) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  if (!calls?.length) return null
  return (
    <div className="mt-2 text-xs">
      <button className="inline-flex items-center gap-1 text-muted hover:text-ink bg-transparent border-0 p-0 min-h-0" onClick={() => setOpen(!open)} aria-expanded={open}>
        <Wrench size={12} /> {t('c.toolCalls', { n: calls.length })}
        <ChevronDown size={12} className={open ? 'rotate-180 transition-transform' : 'transition-transform'} />
      </button>
      {open && (
        <ol className="mt-1.5 space-y-1.5 m-0 p-0 list-none">
          {calls.map((c, i) => (
            <li key={i} className="rounded-lg bg-surface-2 px-2 py-1.5 font-mono text-[0.68rem] leading-snug break-words">
              <span className="font-semibold">{c.name}</span>({Object.entries(c.args).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(', ').slice(0, 220)})
              {c.result && <div className="text-muted mt-0.5">→ {c.result}</div>}
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

/** "Gemini is not configured, <what>. Add the key…" */
export function GeminiOff({ what }: { what: string }) {
  const { t } = useI18n()
  return <div className="notice"><b className="text-ink">{t('gem.lead')}</b> {what} {t('gem.tail')}</div>
}

export function Card({ title, right, children, className = '' }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card p-3.5 sm:p-4 min-w-0 ${className}`}>
      {(title || right) && (
        <header className="flex items-center justify-between gap-x-3 gap-y-2 mb-3 min-h-6 flex-wrap">
          <h2 className="card-title flex items-center gap-1.5 m-0">{title}</h2>
          {right}
        </header>
      )}
      {children}
    </section>
  )
}
