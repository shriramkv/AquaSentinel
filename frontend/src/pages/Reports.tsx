import { Check, Copy, Download, FileText, Megaphone } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, apiUrl } from '../api'
import { Card, ErrorNote, GeminiOff, Spinner, useAction } from '../components/ui'
import { useI18n } from '../i18n'
import { useLive } from '../store'

interface Report { id: number; ts: number; title: string; ai: number; markdown: string | null }
interface AlertText { title: string; body: string }
interface AlertRow { id: number; ts: number; anomaly_id: number; body: { english: AlertText; tamil: AlertText; hindi: AlertText; model?: string } }
const LANGS = [['english', 'English', 'en'], ['tamil', 'தமிழ் · Tamil', 'ta'], ['hindi', 'हिन्दी · Hindi', 'hi']] as const

export function Reports() {
  const { live } = useLive()
  const { t, tm, ago } = useI18n()
  const [reports, setReports] = useState<Report[]>([])
  const [alerts, setAlerts] = useState<AlertRow[]>([])
  const [hours, setHours] = useState(1)
  const [narrative, setNarrative] = useState(true)
  const [aid, setAid] = useState<number | ''>('')
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const load = useCallback(() => {
    Promise.all([api<{ reports: Report[] }>('/reports'), api<{ alerts: AlertRow[] }>('/alerts')])
      .then(([r, a]) => { setReports(r.reports); setAlerts(a.alerts); setLoadErr(null) }).catch((e: Error) => setLoadErr(e.message))
  }, [])
  useEffect(load, [load])
  const ready = !!live?.config.gemini_ready
  const make = useAction(async () => { await api('/reports', { body: { hours, narrative: narrative && ready } }); load() })
  const alert = useAction(async (id: number) => { await api('/alerts', { body: { anomaly_id: id } }); load() })
  if (!live) return null
  const diagnosed = live.anomalies.filter((a) => live.diagnoses.some((d) => d.anomaly_id === a.id))

  return (
    <div className="grid gap-3 sm:gap-4 grid-cols-1 lg:grid-cols-2 items-start">
      <Card title={<><FileText size={15} className="shrink-0" /> {t('rep.title')}</>}>
        <p className="text-xs text-ink-2 m-0 mb-3">{t('rep.intro')}</p>
        <div className="flex flex-wrap items-center gap-2.5">
          <label className="text-xs text-ink-2 inline-flex items-center gap-1.5">{t('rep.window')}
            <select className="btn btn-sm" value={hours} onChange={(e) => setHours(Number(e.target.value))}>
              <option value={0.25}>{t('rep.w15')}</option><option value={1}>{t('rep.w1')}</option><option value={6}>{t('rep.w6')}</option><option value={24}>{t('rep.w24')}</option><option value={168}>{t('rep.w168')}</option>
            </select>
          </label>
          <label className="text-xs text-ink-2 inline-flex items-center gap-1.5">
            <input type="checkbox" checked={narrative && ready} disabled={!ready} onChange={(e) => setNarrative(e.target.checked)} /> {t('rep.narrative')}
          </label>
          <button className="btn btn-primary" disabled={make.busy} onClick={() => make.run()}>{make.busy ? <Spinner /> : <FileText size={14} />} {t('rep.generate')}</button>
        </div>
        {!ready && <div className="mt-2"><GeminiOff what={t('rep.off')} /></div>}
        <div className="mt-2"><ErrorNote>{make.error ?? loadErr}</ErrorNote></div>
        <ul className="m-0 mt-3 p-0 list-none divide-y divide-line">
          {reports.length === 0 && <li className="text-sm text-ink-2 py-2">{t('rep.none')}</li>}
          {reports.map((r) => (
            <li key={r.id} className="py-2.5 flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="text-sm font-medium truncate">#{r.id} · {r.title}</div>
                <div className="text-[0.7rem] text-muted">{ago(r.ts)} · {t(r.ai ? 'rep.ai' : 'rep.tables')}</div>
              </div>
              <a className="btn btn-sm shrink-0" href={apiUrl(`/reports/${r.id}/pdf`)} target="_blank" rel="noreferrer"><Download size={12} /> {t('rep.pdf')}</a>
            </li>
          ))}
        </ul>
      </Card>

      <Card title={<><Megaphone size={15} className="shrink-0" /> {t('al.title')}</>}>
        <p className="text-xs text-ink-2 m-0 mb-3">{t('al.intro')}</p>
        {!ready ? <GeminiOff what={t('al.off')} /> : diagnosed.length === 0 ? (
          <div className="notice">{t('al.noDiag')}</div>
        ) : (
          <div className="flex flex-wrap gap-2 items-center">
            <select className="btn btn-sm max-w-full" aria-label={t('al.anomaly')} value={aid} onChange={(e) => setAid(e.target.value ? Number(e.target.value) : '')}>
              <option value="">{t('al.choose')}</option>
              {diagnosed.map((a) => <option key={a.id} value={a.id}>#{a.id} · {t('c.zone', { z: a.zone })} · {(() => { const c = live.diagnoses.find((d) => d.anomaly_id === a.id)?.body.probable_cause ?? ''; return tm(`cause.${c}`, c) })()}</option>)}
            </select>
            <button className="btn btn-primary" disabled={aid === '' || alert.busy} onClick={() => aid !== '' && alert.run(aid)}>{alert.busy ? <Spinner /> : <Megaphone size={14} />} {t('al.write')}</button>
          </div>
        )}
        <div className="mt-2"><ErrorNote>{alert.error}</ErrorNote></div>
        <div className="mt-3 space-y-4">
          {alerts.map((a) => (
            <div key={a.id}>
              <div className="eyebrow mb-1.5">{t('diag.anomaly', { id: a.anomaly_id })} · {ago(a.ts)}{a.body.model ? ` · ${a.body.model}` : ''}</div>
              <div className="space-y-2">
                {LANGS.map(([k, name, lang]) => a.body[k] && <AlertCard key={k} name={name} lang={lang} a={a.body[k]} />)}
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}

function AlertCard({ name, lang, a }: { name: string; lang: string; a: AlertText }) {
  const { t } = useI18n()
  const [copied, setCopied] = useState(false)
  const copy = async () => { await navigator.clipboard.writeText(`${a.title}\n${a.body}`); setCopied(true); setTimeout(() => setCopied(false), 1500) }
  return (
    <div className="rounded-xl border border-line p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="eyebrow">{name}</span>
        <button className="btn btn-sm" onClick={copy}>{copied ? <Check size={12} /> : <Copy size={12} />} {copied ? t('al.copied') : t('al.copy')}</button>
      </div>
      <div lang={lang} className="mt-1.5 text-sm font-semibold">{a.title}</div>
      <p lang={lang} className="m-0 mt-1 text-sm text-ink-2 leading-relaxed">{a.body}</p>
    </div>
  )
}
