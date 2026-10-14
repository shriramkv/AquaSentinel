import { Fish, Info } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmt } from '../api'
import { LineChart } from '../components/LineChart'
import { Card, ErrorNote, HealthBadge, healthColor } from '../components/ui'
import { useI18n } from '../i18n'
import { useLive } from '../store'
import { zoneName } from '../text'
import type { ZonePrediction } from '../types'

interface History {
  zone: string
  series: { t: string; status: string; p: Record<string, number> }[]
  inputs: { timestamp: string; temperature_c: number | null; tds_ppm: number | null; turbidity_ntu: number | null; ph: number | null }[]
}
const CLASSES = ['Healthy', 'Stressed', 'Critical']
const INPUTS = [
  { key: 'temperature_c', label: 'param.temp', unit: '°C', digits: 1 },
  { key: 'ph', label: 'param.ph', unit: '', digits: 2 },
  { key: 'tds_ppm', label: 'z.tds', unit: 'ppm', digits: 0 },
  { key: 'turbidity_ntu', label: 'param.turbidity', unit: 'NTU', digits: 1 },
] as const
const hhmm = (t: number) => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

export function Zones() {
  const { live } = useLive()
  const { t, tm, ago, lang } = useI18n()
  const [sel, setSel] = useState<string | null>(null)
  const [hist, setHist] = useState<History | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const zones = live?.zones
  const updated = zones?.updated
  const worst = zones && Object.values(zones.zones).filter((z) => z.available).sort((a, b) => CLASSES.indexOf(b.health_status) - CLASSES.indexOf(a.health_status))[0]
  const active = sel ?? worst?.zone ?? null

  useEffect(() => {
    if (!active) return
    let stale = false
    api<History>(`/zones/${active}/history`).then((h) => { if (!stale) { setHist(h); setErr(null) } }).catch((e: Error) => !stale && setErr(e.message))
    return () => { stale = true }
  }, [active, updated])

  if (!live || !zones) return null
  const cur = active ? zones.zones[active] : undefined

  return (
    <div className="space-y-3 sm:space-y-4">
      <div className="notice flex gap-2.5">
        <Info size={16} className="shrink-0 mt-0.5" />
        <div>
          <b className="text-ink">{t('z.lead')}</b> {t('z.body', { v: zones.model.version ?? '?' })} {t(live.config.simulated ? 'z.sim' : 'z.boat')} {t('z.blind')}
        </div>
      </div>
      {zones.error && <ErrorNote>{t('z.modelErr', { e: zones.error })}</ErrorNote>}

      <div className="grid gap-2.5 sm:gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
        {live.lake.zones.map((z) => {
          const p: ZonePrediction | undefined = zones.zones[z.id]
          return (
            <button key={z.id} onClick={() => setSel(z.id)} aria-pressed={active === z.id} className="card p-4 text-left hover:bg-surface-2 transition-colors"
              style={active === z.id ? { borderColor: 'var(--accent)', boxShadow: '0 0 0 1px var(--accent)' } : undefined}>
              <div className="flex items-center justify-between gap-2">
                <span className="eyebrow">{zoneName(tm, z.id)} · {z.id}</span>
                {p?.available && !p.reliable && <span className="chip">{t('c.provisional')}</span>}
              </div>
              {!p ? <p className="text-sm text-muted m-0 mt-2">{t('z.waitFirst')}</p> : !p.available ? <p className="text-sm text-ink-2 m-0 mt-2">{p.reason}</p> : (
                <>
                  <div className="mt-2 flex items-baseline justify-between gap-2">
                    <HealthBadge status={p.health_status} large />
                    <span className="text-xs text-ink-2 tnum">{t('z.confidence', { n: Math.round(p.confidence * 100) })}</span>
                  </div>
                  <ProbBar p={p.probabilities} />
                  <div className="mt-2.5 text-xs text-ink-2 truncate" title={p.top_factors[0]?.description}>{t('z.mainFactor', { f: p.top_factors[0]?.description ?? '–' })}</div>
                </>
              )}
            </button>
          )
        })}
      </div>

      {cur?.available && (
        <div className="grid gap-3 sm:gap-4 grid-cols-1 lg:grid-cols-12">
          <Card className="lg:col-span-5" title={<><Fish size={15} className="shrink-0" /> {t('z.why', { z: cur.zone, s: tm(`health.${cur.health_status}`, cur.health_status) })}</>}>
            <div className="eyebrow mb-1.5">{t('z.topFactors', { s: tm(`health.${cur.health_status}`, cur.health_status) })}</div>
            <FactorBars p={cur} />
            <p className="text-[0.68rem] text-muted mt-2 mb-0">{t('z.bars', { s: tm(`health.${cur.health_status}`, cur.health_status) })}{lang !== 'en' && ` ${t('z.english')}`}</p>
            {cur.advice.length > 0 && (
              <>
                <div className="eyebrow mt-4 mb-1">{t('z.advice')}</div>
                <ul className="m-0 pl-4 list-disc text-xs text-ink-2 space-y-1 leading-relaxed">{cur.advice.map((a, i) => <li key={i}>{a}</li>)}</ul>
              </>
            )}
            {cur.data_quality.warnings.length > 0 && (
              <>
                <div className="eyebrow mt-4 mb-1">{t('z.quality')}</div>
                <ul className="m-0 pl-4 list-disc text-xs text-ink-2 space-y-1">{cur.data_quality.warnings.map((a, i) => <li key={i}>{a}</li>)}</ul>
              </>
            )}
            <p className="text-[0.68rem] text-muted mt-3 mb-0">{t('z.history', { h: cur.data_quality.history_hours, ago: ago(zones.updated) })}</p>
          </Card>

          <Card className="lg:col-span-7" title={t('z.last24', { z: cur.zone })}>
            <ErrorNote>{err}</ErrorNote>
            {hist && hist.zone === cur.zone && (
              <>
                <div className="flex items-center justify-between gap-x-3 gap-y-1 flex-wrap mb-1">
                  <span className="text-xs font-semibold">{t('z.statusEvery')}</span>
                  <span className="flex gap-3 text-[0.7rem] text-ink-2 flex-wrap">{CLASSES.map((c) => <span key={c} className="inline-flex items-center gap-1"><i className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: healthColor(c) }} />{tm(`health.${c}`, c)}</span>)}</span>
                </div>
                <Timeline series={hist.series} />
                <div className="grid gap-x-5 gap-y-3 grid-cols-1 sm:grid-cols-2 mt-4">
                  {INPUTS.map((f) => (
                    <figure key={f.key} className="m-0 min-w-0">
                      <figcaption className="text-xs font-semibold mb-1">{t(f.label)}{f.unit && <span className="text-muted font-normal">, {f.unit}</span>}</figcaption>
                      <LineChart label={t(f.label)} unit={f.unit} digits={f.digits} height={104} formatTime={hhmm}
                        points={hist.inputs.map((r) => ({ t: new Date(r.timestamp).getTime() / 1000, v: r[f.key] }))} />
                    </figure>
                  ))}
                </div>
              </>
            )}
          </Card>
        </div>
      )}
    </div>
  )
}

function ProbBar({ p }: { p: Record<string, number> }) {
  const { tm } = useI18n()
  const text = CLASSES.map((c) => `${tm(`health.${c}`, c)} ${Math.round((p[c] ?? 0) * 100)}%`).join(' · ')
  return (
    <div className="mt-3 flex h-2 gap-0.5" role="img" aria-label={text} title={text}>
      {CLASSES.map((c) => (p[c] ?? 0) > 0.004 && <div key={c} className="rounded-sm" style={{ width: `${(p[c] ?? 0) * 100}%`, background: healthColor(c), minWidth: 3 }} />)}
    </div>
  )
}

function FactorBars({ p }: { p: ZonePrediction }) {
  const max = Math.max(...p.top_factors.map((f) => Math.abs(f.impact)), 1e-6)
  return (
    <div className="space-y-2">
      {p.top_factors.map((f) => {
        const w = (Math.abs(f.impact) / max) * 50
        return (
          <div key={f.feature} title={`${f.feature}: impact ${f.impact}`}>
            <div className="flex justify-between gap-2 text-xs"><span className="text-ink-2 truncate">{f.description}</span><span className="tnum text-ink shrink-0">{fmt(f.value, 2)}</span></div>
            <div className="relative h-2.5 mt-0.5">
              <div className="absolute inset-y-0 left-1/2 w-px" style={{ background: 'var(--axis)' }} />
              <div className="absolute inset-y-0 rounded-sm" style={{ width: `${w}%`, left: f.impact >= 0 ? '50%' : `${50 - w}%`, background: f.impact >= 0 ? 'var(--series)' : 'var(--neg)' }} />
            </div>
          </div>
        )
      })}
    </div>
  )
}

function Timeline({ series }: { series: History['series'] }) {
  const { t, tm } = useI18n()
  const [hover, setHover] = useState<number | null>(null)
  if (!series.length) return <p className="text-xs text-muted">{t('z.noHistory')}</p>
  const h = hover != null ? series[hover] : null
  return (
    <div>
      <div className="flex h-7 rounded-md overflow-hidden" style={{ touchAction: 'pan-y' }} onPointerLeave={() => setHover(null)}
        onPointerMove={(e) => { const r = e.currentTarget.getBoundingClientRect(); setHover(Math.min(series.length - 1, Math.max(0, Math.floor(((e.clientX - r.left) / r.width) * series.length)))) }}>
        {series.map((s, i) => <div key={i} className="flex-1" style={{ background: healthColor(s.status), opacity: hover === i ? 0.6 : 1 }} />)}
      </div>
      <div className="flex justify-between gap-2 text-[0.68rem] text-muted mt-1 tnum">
        <span>{new Date(series[0].t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
        <span className="text-ink-2 text-center">{h ? `${new Date(h.t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}: ${tm(`health.${h.status}`, h.status)} (${Math.round((h.p[h.status] ?? 0) * 100)}%)` : t('z.hover')}</span>
        <span>{new Date(series[series.length - 1].t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
      </div>
    </div>
  )
}
