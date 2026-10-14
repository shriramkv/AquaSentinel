/** Turns structured data from the backend into text in the viewer's language. */
import type { T, TMaybe } from './i18n'
import type { Activity, Anomaly, Mission, MissionMeta, SourceInfo } from './types'

export const zoneName = (tm: TMaybe, id: string) => tm(`zone.${id}`, id)
export const placeName = (tm: TMaybe, p: { id?: string | null; name?: string | null }) => tm(`place.${p.id}`, p.name ?? p.id ?? '')

export function sourceNote(t: T, tm: TMaybe, a: Pick<Anomaly, 'source_info' | 'source_note'>): string {
  const i: SourceInfo | null | undefined = a.source_info
  if (!i) return a.source_note ?? ''
  const parts = [tm(`src.${i.reason}`, t('src.other')), t(i.snapped ? 'src.snapped' : 'src.open')]
  if (i.place_id && i.distance_m != null) parts.push(t('src.near', { d: i.distance_m, place: placeName(tm, { id: i.place_id, name: i.place }) }))
  return parts.join(' ')
}

export function metaSummary(t: T, tm: TMaybe, meta: MissionMeta): string {
  if (meta.code === 'dock') return t('mis.dock')
  if (meta.code === 'zone' && meta.zone) return t('mis.zone', { zone: zoneName(tm, meta.zone), n: meta.spacing_m })
  return t('mis.whole', { n: meta.spacing_m })
}
export const missionSummary = (t: T, tm: TMaybe, m: Mission) => (m.meta ? metaSummary(t, tm, m.meta) : m.summary)

export function replanReason(t: T, a: Anomaly): string {
  const r = a.replan
  if (!r) return ''
  if (r.by === 'gemini') return r.reason                        // written by Gemini, already in the chosen language
  if (r.by === 'system' && a.linked_to) return t('replan.linked', { prev: a.linked_to })
  return t(r.action === 'trace' ? 'replan.onboard' : 'replan.busy')
}

export function activityText(t: T, tm: TMaybe, e: Activity): string {
  const v = e as unknown as Record<string, never>
  switch (e.code) {
    case 'anomaly_detected':
      return t('a.anomaly_detected', { ...v, params: ((e.param_keys as string[]) ?? []).map((p) => tm(`param.${p}`, p)).join(', ') })
    case 'source_estimate':
      return t('a.source_estimate', { ...v, note: sourceNote(t, tm, { source_info: e.source_info as SourceInfo, source_note: null }) })
    case 'diag_done':
      return t('a.diag_done', { ...v, cause: tm(`cause.${e.cause}`, String(e.cause ?? '')), confidence: tm(`conf.${e.confidence}`, String(e.confidence ?? '')) })
    case 'mission_set':
      return t('a.mission_set', { ...v, planner: tm(`pl.${e.planner}`, String(e.planner)), summary: e.meta ? metaSummary(t, tm, e.meta as MissionMeta) : String(e.summary ?? '') })
    case 'scenario_started':
      return t('a.scenario_started', {
        kind: tm(`sc.${e.kind}`, String(e.kind)), place: placeName(tm, { id: e.place_id as string, name: e.place as string }),
        when: e.hours ? t('a.startedAgo', { n: e.hours as number }) : t('a.startingNow'),
      })
    case 'inspect_start':
      return t(e.uploaded ? 'a.inspect_start_photo' : 'a.inspect_start_frame')
    case 'report_ready':
      return t(e.ai ? 'a.report_ready' : 'a.report_ready_tables', v)
    default:
      return e.code ? tm(`a.${e.code}`, e.text, v) : e.text
  }
}
