import { Activity as ActivityIcon, Anchor, Bot, Camera, Crosshair, FlaskConical, Home, Navigation, Pause, Play, Radar, Route, Send, Sparkles, Trash2, Upload } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import { api, apiUrl, clock, fmt } from '../api'
import { LakeMap, RampLegend } from '../components/LakeMap'
import { LineChart } from '../components/LineChart'
import { Card, ErrorNote, GeminiOff, Spinner, StatusChip, ToolCalls, useAction } from '../components/ui'
import { VoiceButton } from '../components/VoiceButton'
import { useI18n } from '../i18n'
import { useLive, type Live as LiveState } from '../store'
import { activityText, missionSummary, replanReason, sourceNote, zoneName } from '../text'
import { PARAMS, type Anomaly, type Diagnosis, type ParamKey, type ToolCall, type VisualFinding } from '../types'

const WINDOWS = [{ key: 'tele.w2', s: 120 }, { key: 'tele.w10', s: 600 }, { key: 'tele.w60', s: 3600 }] as const

/**
 * Wide screens: two columns (map and charts | agent and control cards).
 * Phones and small tablets: one column, ordered so the controls sit right under the map.
 */
export function Live({ dark }: { dark: boolean }) {
  const { live } = useLive()
  const { t } = useI18n()
  const [param, setParam] = useState<ParamKey>('nitrate')
  const [win, setWin] = useState(600)
  const [showTruth, setShowTruth] = useState(false)

  const domain = useMemo<[number, number] | null>(() => {
    if (!live) return null
    const vs = live.readings.map((r) => r[param]).filter((v): v is number => v != null).sort((a, b) => a - b)
    if (vs.length < 2) return null
    const lo = vs[Math.floor(vs.length * 0.02)], hi = vs[Math.ceil(vs.length * 0.98) - 1]
    return hi - lo < 1e-6 ? [lo - 0.5, hi + 0.5] : [lo, hi]
  }, [live, param])

  if (!live) return null
  const latest = live.readings[live.readings.length - 1]
  const meta = live.config.params
  const since = Date.now() / 1000 - win
  const recent = live.readings.filter((r) => r.ts >= since)
  const label = (p: ParamKey) => t(`param.${p}`)

  return (
    <div className="grid gap-3 sm:gap-4 grid-cols-1 lg:grid-cols-12">
      {/* KPI tiles */}
      <div className="order-1 lg:col-span-12 grid gap-2.5 sm:gap-3 grid-cols-2 sm:grid-cols-3 xl:grid-cols-6">
        {PARAMS.map((p) => {
          const v = latest?.[p] ?? null, base = live.baseline[p]
          const delta = v != null && base != null ? v - base : null
          return (
            <button key={p} onClick={() => setParam(p)} aria-pressed={param === p}
              className="card p-3 sm:p-3.5 text-left transition-colors hover:bg-surface-2 min-w-0" style={param === p ? { borderColor: 'var(--accent)', boxShadow: '0 0 0 1px var(--accent)' } : undefined}>
              <div className="eyebrow truncate">{label(p)}</div>
              <div className="mt-1 flex items-baseline gap-1">
                <span className="text-xl sm:text-2xl font-semibold">{fmt(v, meta[p].digits)}</span>
                <span className="text-xs text-muted">{meta[p].unit}</span>
              </div>
              <div className="mt-1.5 flex items-center justify-between gap-x-2 gap-y-1 flex-wrap">
                {latest?.status && <StatusChip status={latest.status[p]} />}
                {delta != null && <span className="text-[0.7rem] text-ink-2 tnum">{delta >= 0 ? '+' : '−'}{Math.abs(delta).toFixed(meta[p].digits)} {t('c.vsBaseline')}</span>}
              </div>
            </button>
          )
        })}
      </div>

      <div className="contents lg:flex lg:flex-col lg:gap-4 lg:col-span-7 xl:col-span-8 lg:min-w-0 lg:order-2">
        <Card className="order-2" title={<><Radar size={15} className="shrink-0" /> {t('live.mapTitle', { param: label(param) })}</>} right={<BoatStatus live={live} />}>
          <div className="h-[46vh] min-h-[280px] max-h-[560px] lg:h-[520px] rounded-xl overflow-hidden border border-line">
            <LakeMap live={live} param={param} dark={dark} showTruth={showTruth} domain={domain} />
          </div>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
            <RampLegend dark={dark} domain={domain} label={label(param)} unit={meta[param].unit} digits={meta[param].digits} />
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[0.7rem] text-ink-2">
              <Legend swatch={<i className="inline-block w-2.5 h-2.5 rounded-full border-[1.5px]" style={{ borderColor: 'var(--critical)' }} />}>{t('live.flagged')}</Legend>
              <Legend swatch={<i className="inline-block w-4 border-t-2 border-dashed" style={{ borderColor: 'var(--muted)' }} />}>{t('live.route')}</Legend>
              <Legend swatch={<i className="inline-block w-4 border-t-[2.5px]" style={{ borderColor: 'var(--serious)' }} />}>{t('live.trace')}</Legend>
              <Legend swatch={<i className="inline-flex w-3 h-3 rounded-full items-center justify-center text-[8px] font-bold text-white not-italic" style={{ background: 'var(--critical)' }}>!</i>}>{t('live.source')}</Legend>
              {live.config.simulated && (
                <label className="inline-flex items-center gap-1.5 cursor-pointer">
                  <input type="checkbox" checked={showTruth} onChange={(e) => setShowTruth(e.target.checked)} /> {t('live.showTruth')}
                </label>
              )}
            </div>
          </div>
          {live.lake.demo && <p className="mt-2 mb-0 text-[0.7rem] text-muted">{t('live.demoOutline')}</p>}
        </Card>

        <Card className="order-6" title={<><ActivityIcon size={15} className="shrink-0" /> {t('tele.title')}</>}
          right={<div className="seg" role="group" aria-label={t('tele.window')}>{WINDOWS.map((o) => <button key={o.s} aria-pressed={win === o.s} onClick={() => setWin(o.s)}>{t(o.key)}</button>)}</div>}>
          <div className="grid gap-x-5 gap-y-4 grid-cols-1 sm:grid-cols-2">
            {PARAMS.map((p) => (
              <figure key={p} className="m-0 min-w-0">
                <figcaption className="flex items-baseline justify-between gap-2 mb-1">
                  <span className="text-xs font-semibold">{label(p)}{meta[p].unit && <span className="text-muted font-normal">, {meta[p].unit}</span>}</span>
                  <span className="text-[0.68rem] text-muted text-right">{t('tele.baseline', { v: fmt(live.baseline[p], meta[p].digits) })}</span>
                </figcaption>
                <LineChart label={label(p)} unit={meta[p].unit} digits={meta[p].digits} baseline={live.baseline[p] ?? null}
                  points={recent.map((r) => ({ t: r.ts, v: r[p], flagged: !!r.flagged }))} />
              </figure>
            ))}
          </div>
          <p className="mt-3 mb-0 text-[0.7rem] text-muted">{t('tele.note')}</p>
        </Card>
      </div>

      <div className="contents lg:flex lg:flex-col lg:gap-4 lg:col-span-5 xl:col-span-4 lg:min-w-0 lg:order-3">
        <DiagnosisCard live={live} className="order-3" />
        <MissionCard live={live} className="order-4" />
        {live.config.simulated && <ScenarioCard live={live} className="order-5" />}
        <CameraCard live={live} className="order-7" />
        <ActivityCard live={live} className="order-8" />
      </div>
    </div>
  )
}

const Legend = ({ swatch, children }: { swatch: React.ReactNode; children: React.ReactNode }) => <span className="inline-flex items-center gap-1.5">{swatch}{children}</span>
interface CardProps { live: LiveState; className?: string }

function BoatStatus({ live }: { live: LiveState }) {
  const { t, tm } = useI18n()
  const b = live.boat
  if (!b) return <span className="chip">{t('live.realBoat')}</span>
  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      <span className="chip" style={b.mode === 'trace' ? { background: 'var(--accent-soft)', color: 'var(--ink)' } : undefined}>
        {b.mode === 'trace' ? <Crosshair size={11} /> : <Navigation size={11} />}{tm(`mode.${b.mode}`, b.mode)}
      </span>
      <span className="chip tnum">{t('c.zone', { z: b.zone })}</span>
      <span className="chip tnum">{t('live.battery', { n: Math.round(b.battery) })}</span>
    </div>
  )
}

function DiagnosisCard({ live, className }: CardProps) {
  const { t, tm, ago } = useI18n()
  const anomaly: Anomaly | undefined = live.anomalies.find((a) => a.status !== 'linked') ?? live.anomalies[0]
  const diag: Diagnosis | undefined = anomaly && live.diagnoses.find((d) => d.anomaly_id === anomaly.id)
  const act = useAction((id: number) => api(`/anomalies/${id}/diagnose`, { method: 'POST' }))
  const meta = live.config.params
  const src = anomaly ? sourceNote(t, tm, anomaly) : ''
  return (
    <Card className={className} title={<><Sparkles size={15} className="shrink-0" /> {t('diag.title')}</>} right={live.config.gemini_ready ? <span className="chip">{live.config.gemini_model}</span> : undefined}>
      {!anomaly ? <p className="text-sm text-ink-2 m-0">{t('diag.none')}</p> : (
        <div className="space-y-3">
          <div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-semibold">{t('diag.anomaly', { id: anomaly.id })} · {t('c.zone', { z: anomaly.zone })}</span>
              <span className="text-[0.7rem] text-muted shrink-0">{ago(anomaly.started)}</span>
            </div>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {anomaly.params.map((p) => {
                const peak = anomaly.peak?.[p], base = anomaly.baseline?.[p]
                return <span key={p} className="chip tnum">{t(`param.${p}`)} {fmt(peak, meta[p].digits)}{base != null && <span className="text-muted font-normal"> {t('diag.from', { v: fmt(base, meta[p].digits) })}</span>}</span>
              })}
            </div>
            {anomaly.replan && <p className="mt-2 mb-0 text-xs text-ink-2"><b className="text-ink">{t('diag.replan', { by: tm(`by.${anomaly.replan.by}`, anomaly.replan.by) })}</b> {replanReason(t, anomaly)}</p>}
            {src && <p className="mt-1 mb-0 text-xs text-ink-2"><b className="text-ink">{t('diag.sourceLabel')}</b> {src}</p>}
          </div>

          {diag ? <DiagnosisBodyView d={diag} /> : !live.config.gemini_ready ? <GeminiOff what={t('diag.off')} /> : (
            <div className="notice">{anomaly.status === 'open' && !src ? t('diag.waiting') : t('diag.noneStored')}</div>
          )}
          {live.config.gemini_ready && (
            <button className="btn btn-sm" disabled={act.busy} onClick={() => act.run(anomaly.id)}>{act.busy ? <Spinner /> : <Bot size={13} />} {diag ? t('diag.again') : t('diag.now')}</button>
          )}
          <ErrorNote>{act.error}</ErrorNote>
        </div>
      )}
    </Card>
  )
}

export function DiagnosisBodyView({ d }: { d: Diagnosis }) {
  const { t, tm, ago } = useI18n()
  const b = d.body
  const present = (b.visual?.findings ?? []).filter((f: VisualFinding) => f.present)
  return (
    <div className="space-y-2.5">
      <div className="rounded-xl p-3" style={{ background: 'var(--accent-soft)' }}>
        <div className="eyebrow">{t('diag.likely')}</div>
        <div className="mt-0.5 flex items-baseline justify-between gap-2 flex-wrap">
          <span className="text-base font-semibold">{tm(`cause.${b.probable_cause}`, b.probable_cause)}</span>
          <span className="text-xs text-ink-2">{t('diag.confidence', { c: tm(`conf.${b.confidence}`, b.confidence) })}</span>
        </div>
        {b.headline && <p className="mt-1 mb-0 text-xs text-ink-2 leading-relaxed">{b.headline}</p>}
      </div>
      {b.raw_reply && <pre className="notice whitespace-pre-wrap text-[0.7rem] m-0">{b.raw_reply}</pre>}
      <List title={t('diag.evidence')} items={b.evidence} />
      <List title={t('diag.against')} items={b.against} />
      {present.length > 0 && (
        <div>
          <div className="eyebrow mb-1">{b.visual?.frame_is_simulated ? t('diag.cameraSim') : t('diag.camera')}</div>
          <div className="flex flex-wrap gap-1.5">{present.map((f) => <span key={f.label} className="chip" title={f.note}>{tm(`vl.${f.label}`, f.label)} · {Math.round(f.confidence * 100)}%</span>)}</div>
        </div>
      )}
      {b.visual_error && <p className="text-[0.7rem] text-muted m-0">{t('diag.cameraErr', { e: b.visual_error })}</p>}
      {b.cpcb_assessment && <p className="text-xs text-ink-2 m-0"><b className="text-ink">{t('diag.cpcb')}</b> {b.cpcb_assessment}</p>}
      {b.aquatic_life && <p className="text-xs text-ink-2 m-0"><b className="text-ink">{t('diag.aquatic')}</b> {b.aquatic_life}</p>}
      <List title={t('diag.actions')} items={b.recommended_actions} />
      <List title={t('diag.lab')} items={b.lab_tests_needed} inline />
      <p className="text-[0.68rem] text-muted m-0">{t('diag.written', { model: b.model ?? 'Gemini', ago: ago(d.ts) })}</p>
      <ToolCalls calls={b.tool_calls} />
    </div>
  )
}

function List({ title, items, inline }: { title: string; items?: string[]; inline?: boolean }) {
  if (!items?.length) return null
  return (
    <div>
      <div className="eyebrow mb-1">{title}</div>
      {inline ? <div className="flex flex-wrap gap-1.5">{items.map((x, i) => <span key={i} className="chip !whitespace-normal">{x}</span>)}</div>
        : <ul className="m-0 pl-4 list-disc text-xs text-ink-2 space-y-0.5 leading-relaxed">{items.map((x, i) => <li key={i}>{x}</li>)}</ul>}
    </div>
  )
}

function MissionCard({ live, className }: CardProps) {
  const { t, tm } = useI18n()
  const [text, setText] = useState('')
  const [voiceErr, setVoiceErr] = useState<string | null>(null)
  const [reply, setReply] = useState<{ text: string; tool_calls: ToolCall[] } | null>(null)
  const plan = useAction(async (request: string) => { setReply(null); setReply(await api('/missions', { body: { request } })); setText('') })
  const cmd = useAction((action: string, value?: number | string) => api('/boat/command', { body: { action, value } }))
  const preset = useAction((area: string) => api('/missions/preset', { body: { area, spacing_m: area === 'whole_lake' ? 110 : 60 } }))
  const b = live.boat, m = live.mission, sim = live.config.simulated, ready = live.config.gemini_ready
  return (
    <Card className={className} title={<><Route size={15} className="shrink-0" /> {t('mis.title')}</>}>
      {m && (
        <div className="mb-3 text-xs text-ink-2">
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <b className="text-ink">{t('mis.mission', { id: m.id })}</b>
            <span className="chip">{tm(`mis.st.${m.status}`, m.status)} · {t('mis.plannedBy', { p: tm(`pl.${m.planner}`, m.planner) })}</span>
          </div>
          <p className="m-0 mt-1">{missionSummary(t, tm, m)}</p>
          {b && m.status === 'active' && b.waypoints_total > 0 && (
            <div className="mt-2 h-1.5 rounded-full bg-surface-2 overflow-hidden" role="progressbar" aria-valuenow={b.waypoint_index} aria-valuemax={b.waypoints_total} aria-label={t('mis.progress')}>
              <div className="h-full rounded-full" style={{ width: `${(100 * b.waypoint_index) / b.waypoints_total}%`, background: 'var(--accent)' }} />
            </div>
          )}
        </div>
      )}
      <form onSubmit={(e) => { e.preventDefault(); if (text.trim()) plan.run(text.trim()) }} className="flex gap-2">
        <input className="field min-w-0" value={text} onChange={(e) => setText(e.target.value)} placeholder={t('mis.placeholder')} aria-label={t('mis.request')} disabled={!ready} maxLength={500} />
        <VoiceButton value={text} onChange={setText} onError={setVoiceErr} geminiReady={ready} disabled={!ready || plan.busy} />
        <button className="icon-btn btn-primary" disabled={!ready || plan.busy || text.trim().length < 3} aria-label={t('mis.plan')} title={t('mis.plan')}>{plan.busy ? <Spinner /> : <Send size={15} />}</button>
      </form>
      {ready ? (
        <div className="mt-2 flex flex-wrap gap-1.5">{(['mis.ex1', 'mis.ex2', 'mis.ex3'] as const).map((k) => <button key={k} className="chip border-0 hover:text-ink !whitespace-normal text-left" onClick={() => setText(t(k))}>{t(k)}</button>)}</div>
      ) : <div className="mt-2"><GeminiOff what={t('mis.off')} /></div>}
      {reply && <div className="notice mt-2"><span className="text-ink">{reply.text || t('mis.noSummary')}</span><ToolCalls calls={reply.tool_calls} /></div>}
      <div className="mt-2"><ErrorNote>{plan.error ?? voiceErr ?? cmd.error ?? preset.error}</ErrorNote></div>

      <div className="mt-3 pt-3 border-t border-line space-y-2.5">
        <Row label={t('mis.preset')}>
          <button className="btn btn-sm" disabled={preset.busy} onClick={() => preset.run('whole_lake')}>{t('mis.wholeLake')}</button>
          <select className="btn btn-sm" aria-label={t('mis.oneZone')} value="" disabled={preset.busy} onChange={(e) => e.target.value && preset.run(e.target.value)}>
            <option value="">{t('mis.oneZone')}</option>
            {live.lake.zones.map((z) => <option key={z.id} value={z.id}>{zoneName(tm, z.id)}</option>)}
          </select>
        </Row>
        {sim && b && (
          <>
            <Row label={t('mis.boat')}>
              {b.mode === 'paused'
                ? <button className="btn btn-sm" onClick={() => cmd.run('resume')}><Play size={12} /> {t('mis.resume')}</button>
                : <button className="btn btn-sm" onClick={() => cmd.run('pause')}><Pause size={12} /> {t('mis.pause')}</button>}
              <button className="btn btn-sm" onClick={() => cmd.run('home')}><Home size={12} /> {t('mis.toDock')}</button>
            </Row>
            <Row label={t('mis.speed')} title={t('mis.speedTitle')}>
              <div className="seg" role="group" aria-label={t('mis.speed')}>
                {[1, 4, 8, 16].map((x) => <button key={x} aria-pressed={b.speed_scale === x} onClick={() => cmd.run('speed', x)}>{x}×</button>)}
              </div>
            </Row>
            <Row label={t('mis.replanAfter')}>
              <div className="seg" role="group" aria-label={t('mis.replanAfter')}>
                <button aria-pressed={live.config.replan_mode === 'gemini'} disabled={!ready} onClick={() => cmd.run('replan_mode', 'gemini')}>{t('mis.geminiPlanner')}</button>
                <button aria-pressed={live.config.replan_mode === 'onboard'} onClick={() => cmd.run('replan_mode', 'onboard')}>{t('mis.onboardRule')}</button>
              </div>
            </Row>
          </>
        )}
      </div>
    </Card>
  )
}

const Row = ({ label, title, children }: { label: string; title?: string; children: React.ReactNode }) => (
  <div className="flex items-center justify-between gap-x-3 gap-y-1.5 flex-wrap">
    <span className="eyebrow" title={title}>{label}</span>
    <div className="flex gap-1.5 flex-wrap">{children}</div>
  </div>
)

function ScenarioCard({ live, className }: CardProps) {
  const { t, tm } = useI18n()
  const [hours, setHours] = useState(6)
  const add = useAction((kind: string) => api('/scenarios', { body: { kind, hours_ago: hours } }))
  const clear = useAction(() => api('/scenarios', { method: 'DELETE' }))
  const reset = useAction(() => api('/reset', { method: 'POST' }))
  return (
    <Card className={className} title={<><FlaskConical size={15} className="shrink-0" /> {t('sc.title')}</>} right={<span className="chip">{t('sc.chip')}</span>}>
      <p className="text-xs text-ink-2 m-0 mb-2.5">{t('sc.intro')}</p>
      <div className="grid grid-cols-2 gap-1.5">
        {live.config.scenario_kinds.map((s) => {
          const on = live.scenarios.some((x) => x.kind === s.kind)
          return (
            <button key={s.kind} className="btn btn-sm !justify-start !whitespace-normal text-left" disabled={add.busy} onClick={() => add.run(s.kind)} style={on ? { background: 'var(--accent-soft)' } : undefined}>
              <span>{tm(`sc.${s.kind}`, s.label)}{on && <span className="text-muted font-normal"> · {t('sc.active')}</span>}</span>
            </button>
          )
        })}
      </div>
      <div className="mt-2.5 flex items-center justify-between gap-2 flex-wrap">
        <label className="text-xs text-ink-2 inline-flex items-center gap-1.5 flex-wrap">{t('sc.began')}
          <select className="btn btn-sm" value={hours} onChange={(e) => setHours(Number(e.target.value))}>
            <option value={0}>{t('sc.now')}</option>
            {[3, 6, 12, 24].map((n) => <option key={n} value={n}>{t('sc.hAgo', { n })}</option>)}
          </select>
        </label>
        <div className="flex gap-1.5">
          <button className="btn btn-sm" disabled={!live.scenarios.length || clear.busy} onClick={() => clear.run()}>{t('sc.clear')}</button>
          <button className="btn btn-sm" disabled={reset.busy} onClick={() => { if (confirm(t('sc.resetConfirm'))) reset.run() }}><Trash2 size={12} /> {t('sc.reset')}</button>
        </div>
      </div>
      <p className="text-[0.68rem] text-muted m-0 mt-2">{t('sc.note')}</p>
      <div className="mt-2"><ErrorNote>{add.error ?? clear.error ?? reset.error}</ErrorNote></div>
    </Card>
  )
}

function CameraCard({ live, className }: CardProps) {
  const { t, tm } = useI18n()
  const [result, setResult] = useState<Diagnosis['body']['visual'] | null>(null)
  const file = useRef<HTMLInputElement>(null)
  const inspect = useAction(async (f?: File) => {
    setResult(null)
    const form = new FormData()
    if (f) form.append('file', f)
    setResult(await api('/camera/inspect', f ? { form } : { method: 'POST' }))
  })
  const ready = live.config.gemini_ready
  return (
    <Card className={className} title={<><Camera size={15} className="shrink-0" /> {t('cam.title')}</>} right={live.config.simulated ? <span className="chip">{t('cam.sim')}</span> : undefined}>
      <img src={apiUrl(`/camera/latest.jpg?t=${Math.floor(live.frameTs)}`)} alt={t('cam.alt')} className="w-full rounded-lg border border-line block aspect-[16/10] object-cover bg-surface-2" />
      {live.config.simulated && <p className="text-[0.68rem] text-muted m-0 mt-1.5">{t('cam.simNote')}</p>}
      <div className="mt-2 flex gap-1.5 flex-wrap">
        <button className="btn btn-sm" disabled={!ready || inspect.busy} onClick={() => inspect.run()}>{inspect.busy ? <Spinner /> : <Bot size={13} />} {t('cam.inspect')}</button>
        <button className="btn btn-sm" disabled={!ready || inspect.busy} onClick={() => file.current?.click()}><Upload size={13} /> {t('cam.photo')}</button>
        <input ref={file} type="file" accept="image/*" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) inspect.run(f); e.target.value = '' }} />
      </div>
      {!ready && <p className="text-[0.68rem] text-muted m-0 mt-1.5">{t('cam.needs')}</p>}
      {result && (
        <div className="notice mt-2">
          <p className="m-0 text-ink">{result.summary}</p>
          <p className="m-0 mt-1">{result.water_appearance}</p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {result.findings.map((f) => <span key={f.label} className="chip" style={f.present ? { background: 'var(--accent-soft)', color: 'var(--ink)' } : undefined} title={f.note}>{f.present ? '✓' : '–'} {tm(`vl.${f.label}`, f.label)}</span>)}
          </div>
        </div>
      )}
      <div className="mt-2"><ErrorNote>{inspect.error}</ErrorNote></div>
    </Card>
  )
}

function ActivityCard({ live, className }: CardProps) {
  const { t, tm } = useI18n()
  const items = [...live.activity].reverse().slice(0, 30)
  return (
    <Card className={className} title={<><Anchor size={15} className="shrink-0" /> {t('act.title')}</>}>
      {items.length === 0 ? <p className="text-sm text-ink-2 m-0">{t('act.empty')}</p> : (
        <ol className="m-0 p-0 list-none space-y-2 max-h-80 overflow-y-auto pr-1">
          {items.map((e, i) => (
            <li key={`${e.ts}-${i}`} className="text-xs leading-snug">
              <div className="flex items-baseline justify-between gap-2">
                <span className="font-semibold inline-flex items-center gap-1.5">
                  <i className="inline-block w-1.5 h-1.5 rounded-full" style={{ background: e.level === 'error' ? 'var(--critical)' : e.level === 'warn' ? 'var(--warning)' : 'var(--axis)' }} />
                  {tm(`who.${e.who}`, e.who)}
                </span>
                <time className="text-muted tnum text-[0.68rem]">{clock(e.ts)}</time>
              </div>
              <div className="text-ink-2 break-words">{activityText(t, tm, e)}</div>
              <ToolCalls calls={e.tool_calls} />
            </li>
          ))}
        </ol>
      )}
    </Card>
  )
}
