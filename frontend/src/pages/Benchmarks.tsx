import { Gauge, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { Card, ErrorNote, Spinner, useAction } from '../components/ui'
import { useI18n } from '../i18n'
import { useLive } from '../store'

interface Bench {
  simulated: boolean; since: number; readings: number; readings_per_hour: number
  process_ms: { p50: number; p95: number } | null
  detector: { tp: number; fp: number; fn: number; tn: number; n: number; precision: number | null; recall: number | null } | null
  localisation: { anomaly_id: number; scenario: string; error_m: number }[]
}
interface Visual { frames: { frame: number; scenario: string; drawn?: string[]; reported?: string[]; labels_correct?: number; labels_total?: number; error?: string }[]; label_accuracy: number | null; model: string }
type Verdict = 'met' | 'not met' | 'no data' | 'not measurable here'
interface Row { metric: string; how: string; target: string; result: string; verdict: Verdict }

const pct = (v: number | null | undefined) => (v == null ? '–' : `${(v * 100).toFixed(0)}%`)
const MANUAL_PER_DAY = 20        // assumption shown to the reader: a field team's grab samples per day

export function Benchmarks() {
  const { live, latency } = useLive()
  const { t, tm } = useI18n()
  const [b, setB] = useState<Bench | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [visual, setVisual] = useState<Visual | null>(null)
  const load = useCallback(() => { api<Bench>('/benchmarks').then((x) => { setB(x); setErr(null) }).catch((e: Error) => setErr(e.message)) }, [])
  useEffect(() => { load(); const id = setInterval(load, 5000); return () => clearInterval(id) }, [load])
  const runVisual = useAction(async () => setVisual(await api<Visual>('/benchmarks/visual', { method: 'POST' })))
  if (!live || !b) return <ErrorNote>{err}</ErrorNote>

  const lat = latency()
  const d = b.detector
  const loc = b.localisation
  const worstLoc = loc.length ? Math.max(...loc.map((l) => l.error_m)) : null
  const labels = (xs?: string[]) => (xs?.length ? xs.map((x) => tm(`vl.${x}`, x)).join(', ') : t('b.nothing'))
  const rows: Row[] = [
    { metric: t('b.m1'), how: t('b.h1'), target: t('b.t1'), result: t('b.r1'), verdict: 'not measurable here' },
    { metric: t('b.m2'), how: t('b.h2'), target: t('b.t2'),
      result: lat ? t('b.r2', { p50: (lat.p50 / 1000).toFixed(2), p95: (lat.p95 / 1000).toFixed(2), n: lat.n }) : t('b.r2none'),
      verdict: lat ? (lat.p95 < 10000 ? 'met' : 'not met') : 'no data' },
    { metric: t('b.m3'), how: t('b.h3'), target: t('b.t3'),
      result: d && d.tp + d.fp + d.fn > 0 ? t('b.r3', { p: pct(d.precision), r: pct(d.recall), tp: d.tp, fp: d.fp, fn: d.fn }) : t('b.r3none'),
      verdict: d && d.precision != null && d.recall != null ? (d.precision >= 0.85 && d.recall >= 0.85 ? 'met' : 'not met') : 'no data' },
    { metric: t('b.m4'), how: t('b.h4'), target: t('b.t4'),
      result: loc.length ? loc.map((l) => `#${l.anomaly_id} ${tm(`sc.${l.scenario}`, l.scenario)}: ${l.error_m} m`).join(' · ') : t('b.r4none'),
      verdict: worstLoc == null ? 'no data' : worstLoc < 25 ? 'met' : 'not met' },
    { metric: t('b.m5'), how: t('b.h5'), target: t('b.t5'),
      result: visual?.label_accuracy != null ? t('b.r5', { p: pct(visual.label_accuracy), model: visual.model }) : t('b.r5none'),
      verdict: visual?.label_accuracy != null ? (visual.label_accuracy >= 0.8 ? 'met' : 'not met') : 'no data' },
    { metric: t('b.m6'), how: t('b.h6'), target: t('b.t6'), result: t('b.r6'), verdict: 'not measurable here' },
    { metric: t('b.m7'), how: t('b.h7', { n: MANUAL_PER_DAY }), target: t('b.t7'), result: t('b.r7', { n: b.readings_per_hour.toLocaleString() }),
      verdict: b.readings ? (b.readings_per_hour >= MANUAL_PER_DAY * 10 ? 'met' : 'not met') : 'no data' },
  ]

  return (
    <div className="space-y-3 sm:space-y-4">
      {b.simulated && <div className="notice"><b className="text-ink">{t('b.simLead')}</b> {t('b.simBody')}</div>}
      <Card title={<><Gauge size={15} className="shrink-0" /> {t('b.title')}</>} right={<button className="btn btn-sm" onClick={load}><RefreshCw size={12} /> {t('b.refresh')}</button>}>
        {/* Table from tablet width up */}
        <table className="hidden md:table w-full text-sm border-collapse">
          <thead><tr className="text-left eyebrow">{[t('b.metric'), t('b.how'), t('b.target'), t('b.result'), ''].map((h, i) => <th key={i} className="py-2 pr-4 font-semibold border-b border-line">{h}</th>)}</tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.metric} className="align-top">
                <td className="py-2.5 pr-4 font-semibold border-b border-line">{r.metric}</td>
                <td className="py-2.5 pr-4 text-ink-2 border-b border-line text-xs leading-relaxed">{r.how}</td>
                <td className="py-2.5 pr-4 border-b border-line tnum">{r.target}</td>
                <td className="py-2.5 pr-4 border-b border-line tnum text-xs leading-relaxed">{r.result}</td>
                <td className="py-2.5 border-b border-line whitespace-nowrap"><VerdictChip v={r.verdict} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        {/* The same rows as a list on phones */}
        <ul className="md:hidden m-0 p-0 list-none divide-y divide-line">
          {rows.map((r) => (
            <li key={r.metric} className="py-3 first:pt-0">
              <div className="flex items-start justify-between gap-2">
                <span className="font-semibold text-sm">{r.metric}</span>
                <VerdictChip v={r.verdict} />
              </div>
              <p className="m-0 mt-1 text-xs text-ink-2 leading-relaxed">{r.how}</p>
              <dl className="m-0 mt-1.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
                <dt className="text-muted">{t('b.target')}</dt><dd className="m-0 tnum">{r.target}</dd>
                <dt className="text-muted">{t('b.result')}</dt><dd className="m-0 tnum">{r.result}</dd>
              </dl>
            </li>
          ))}
        </ul>
        {d && <p className="text-[0.7rem] text-muted mt-3 mb-0">{t('b.foot', { ms: b.process_ms?.p50 ?? '–' })}</p>}
        <ErrorNote>{err}</ErrorNote>
      </Card>

      <Card title={t('b.vTitle')}>
        <p className="text-xs text-ink-2 m-0 mb-2.5">{t('b.vIntro')}</p>
        <button className="btn" disabled={!live.config.gemini_ready || !b.simulated || runVisual.busy} onClick={() => runVisual.run()}>{runVisual.busy ? <Spinner /> : null} {t('b.vRun')}</button>
        {!live.config.gemini_ready && <p className="text-[0.7rem] text-muted mt-1.5 mb-0">{t('b.vNeeds')}</p>}
        <div className="mt-2"><ErrorNote>{runVisual.error}</ErrorNote></div>
        {visual && (
          <>
            <ul className="m-0 mt-3 p-0 list-none divide-y divide-line text-xs">
              {visual.frames.map((f) => (
                <li key={f.frame} className="py-1.5 flex flex-wrap gap-x-4 gap-y-0.5">
                  <b>{t('b.frame', { n: f.frame })} · {f.scenario === 'clean water' ? t('b.clean') : tm(`sc.${f.scenario}`, f.scenario)}</b>
                  {f.error ? <span className="text-ink-2">{t('b.failed', { e: f.error })}</span> : <>
                    <span className="text-ink-2">{t('b.drawn', { x: labels(f.drawn) })}</span>
                    <span className="text-ink-2">{t('b.reported', { x: labels(f.reported) })}</span>
                    <span className="tnum">{t('b.labels', { a: f.labels_correct, b: f.labels_total })}</span></>}
                </li>
              ))}
            </ul>
            <p className="text-[0.7rem] text-muted mt-2 mb-0">{t('b.vNote')}</p>
          </>
        )}
      </Card>
    </div>
  )
}

function VerdictChip({ v }: { v: Verdict }) {
  const { t } = useI18n()
  const c = v === 'met' ? 'var(--good)' : v === 'not met' ? 'var(--critical)' : 'var(--axis)'
  return <span className="chip shrink-0"><i className="inline-block w-1.5 h-1.5 rounded-full" style={{ background: c }} />{v === 'met' ? '✓ ' : v === 'not met' ? '✕ ' : ''}{t(`v.${v}`)}</span>
}
