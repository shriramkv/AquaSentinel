export type ParamKey = 'ph' | 'do' | 'temp' | 'ec' | 'nitrate' | 'turbidity'
export const PARAMS: ParamKey[] = ['ph', 'do', 'temp', 'ec', 'nitrate', 'turbidity']
export type Status = 'ok' | 'watch' | 'breach' | 'none'

export interface ParamMeta { label: string; unit: string; digits: number }
export interface Reading {
  id: number; ts: number; lat: number; lon: number; zone: string; flagged: number
  ph: number | null; do: number | null; temp: number | null; ec: number | null; nitrate: number | null; turbidity: number | null
  status?: Record<ParamKey, Status>
}
export interface Boat {
  lat: number; lon: number; mode: string; bearing: number; speed_scale: number; speed_ms: number
  battery: number; distance_m: number; waypoint_index: number; waypoints_total: number; zone: string
}
export interface ToolCall { name: string; args: Record<string, unknown>; result?: string }
export interface MissionMeta { code: 'whole_lake' | 'zone' | 'dock'; zone?: string | null; spacing_m?: number }
export interface Mission { id: number; ts: number; request: string; summary: string; planner: string; waypoints: [number, number][]; status: string; meta?: MissionMeta | null }
export interface SourceInfo { reason: string; snapped: boolean; place_id: string | null; place: string | null; distance_m: number | null }
export interface Anomaly {
  id: number; started: number; ended: number | null; zone: string; lat: number; lon: number
  params: ParamKey[]; peak: Partial<Record<ParamKey, number>>; baseline: Partial<Record<ParamKey, number>>
  status: string; linked_to: number | null; source_lat: number | null; source_lon: number | null; source_note: string | null
  source_info?: SourceInfo | null
  replan: { by: string; action: string; reason: string; tool_calls?: ToolCall[] } | null
}
export interface VisualFinding { label: string; present: boolean; confidence: number; note: string }
export interface DiagnosisBody {
  probable_cause: string; confidence: string; headline?: string; evidence?: string[]; against?: string[]
  cpcb_assessment?: string; aquatic_life?: string; recommended_actions?: string[]; lab_tests_needed?: string[]
  visual?: { findings: VisualFinding[]; water_appearance: string; summary: string; frame_is_simulated: boolean } | null
  visual_error?: string | null; tool_calls?: ToolCall[]; model?: string; raw_reply?: string
}
export interface Diagnosis { id: number; anomaly_id: number; ts: number; body: DiagnosisBody }
/** `text` is English; `code` and the extra fields let the dashboard show the entry in the viewer's language. */
export interface Activity { ts: number; who: string; text: string; level: string; code?: string | null; tool_calls?: ToolCall[]; [extra: string]: unknown }
export interface Factor { feature: string; description: string; value: number | null; impact: number }
export interface ZonePrediction {
  zone: string; available: boolean; reason?: string
  health_status: 'Healthy' | 'Stressed' | 'Critical'; confidence: number; reliable: boolean
  probabilities: Record<string, number>; top_factors: Factor[]; advice: string[]
  latest_readings: Record<string, number | null>
  data_quality: { history_hours: number; missing_fraction_last_hour: number; warnings: string[] }
}
export interface ZonesState {
  updated: number; zones: Record<string, ZonePrediction>; error: string | null
  model: { name?: string; version?: string; trained_at?: string; n_features?: number; critical_weight?: number; note?: string }
}
export interface Place { id: string; name: string; kind: string; lat: number; lon: number }
export interface LakeInfo {
  name: string; demo: boolean; outline: [number, number][]; centre: [number, number]
  size_m: { east_west: number; north_south: number }; places: Place[]
  zones: { id: string; name: string; lat: number; lon: number }[]
}
export interface Scenario { id: number; kind: string; label: string; place: string; place_name: string; lat: number; lon: number; started: number }
export interface Config {
  simulated: boolean; gemini_ready: boolean; gemini_model: string; replan_mode: string; language?: string
  params: Record<ParamKey, ParamMeta>; scenario_kinds: { kind: string; label: string; default_place: string }[]
}
export interface Snapshot {
  lake: LakeInfo; config: Config; boat: Boat | null; mission: Mission | null; scenarios: Scenario[]
  readings: Reading[]; anomalies: Anomaly[]; diagnoses: Diagnosis[]; activity: Activity[]
  zones: ZonesState; baseline: Partial<Record<ParamKey, number>>; trace_path: [number, number][]
}
