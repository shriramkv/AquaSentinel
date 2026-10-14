import { createContext, useCallback, useContext, useEffect, useReducer, useRef, useState, type ReactNode } from 'react'
import { api, wsUrl } from './api'
import type { Activity, Anomaly, Boat, Diagnosis, Mission, ParamKey, Reading, Scenario, Snapshot, ZonesState } from './types'

const MAX_READINGS = 1800

export interface Live extends Snapshot {
  frameTs: number
  z: Partial<Record<ParamKey, number>>
}

type Msg =
  | { type: 'snapshot'; snapshot: Snapshot }
  | { type: 'reading'; reading: Reading; z: Partial<Record<ParamKey, number>> }
  | { type: 'boat'; boat: Boat; trace_path?: [number, number][] }
  | { type: 'anomaly'; anomaly: Anomaly; trace_path?: [number, number][] }
  | { type: 'diagnosis'; diagnosis: Diagnosis }
  | { type: 'mission'; mission: Mission }
  | { type: 'zones'; zones: ZonesState }
  | { type: 'scenarios'; scenarios: Scenario[] }
  | { type: 'activity'; entry: Activity }
  | { type: 'frame'; ts: number }
  | { type: 'config'; replan_mode: string }

function reduce(s: Live | null, m: Msg): Live | null {
  if (m.type === 'snapshot') return { ...m.snapshot, frameTs: Date.now() / 1000, z: {} }
  if (!s) return s
  switch (m.type) {
    case 'reading': {
      const readings = s.readings.length >= MAX_READINGS ? s.readings.slice(-MAX_READINGS + 1) : s.readings.slice()
      readings.push(m.reading)
      return { ...s, readings, z: m.z }
    }
    case 'boat':
      return { ...s, boat: m.boat, trace_path: m.trace_path ?? s.trace_path }
    case 'anomaly': {
      const rest = s.anomalies.filter((a) => a.id !== m.anomaly.id)
      return { ...s, anomalies: [m.anomaly, ...rest].sort((a, b) => b.id - a.id), trace_path: m.trace_path ?? s.trace_path }
    }
    case 'diagnosis':
      return { ...s, diagnoses: [m.diagnosis, ...s.diagnoses.filter((d) => d.id !== m.diagnosis.id)] }
    case 'mission':
      return { ...s, mission: m.mission }
    case 'zones':
      return { ...s, zones: m.zones }
    case 'scenarios':
      return { ...s, scenarios: m.scenarios }
    case 'activity':
      return { ...s, activity: [...s.activity.slice(-79), m.entry] }
    case 'frame':
      return { ...s, frameTs: m.ts }
    case 'config':
      return { ...s, config: { ...s.config, replan_mode: m.replan_mode } }
  }
}

interface Ctx { live: Live | null; connected: boolean; error: string | null; latency: () => { p50: number; p95: number; n: number } | null; reload: () => void }
const LiveContext = createContext<Ctx>({ live: null, connected: false, error: null, latency: () => null, reload: () => {} })

export function LiveProvider({ children }: { children: ReactNode }) {
  const [live, dispatch] = useReducer(reduce, null)
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const lat = useRef<number[]>([])

  const reload = useCallback(() => {
    api<Snapshot>('/state')
      .then((snapshot) => { dispatch({ type: 'snapshot', snapshot }); setError(null) })
      .catch((e: Error) => setError(e.message))
  }, [])

  useEffect(() => {
    let ws: WebSocket | null = null
    let closed = false
    let timer: ReturnType<typeof setTimeout>
    const connect = () => {
      reload()
      ws = new WebSocket(wsUrl())
      ws.onopen = () => setConnected(true)
      ws.onmessage = (ev) => {
        const m = JSON.parse(ev.data)
        if (m.type === 'reset') return reload()
        if (m.type === 'reading') {
          // End-to-end latency: sensor timestamp on the boat to arrival in this browser
          lat.current.push(Date.now() - m.reading.ts * 1000)
          if (lat.current.length > 400) lat.current.shift()
        }
        dispatch(m)
      }
      ws.onclose = () => { setConnected(false); if (!closed) timer = setTimeout(connect, 2000) }
    }
    connect()
    return () => { closed = true; clearTimeout(timer); ws?.close() }
  }, [reload])

  const latency = useCallback(() => {
    const a = [...lat.current].sort((x, y) => x - y)
    return a.length ? { p50: a[Math.floor(a.length / 2)], p95: a[Math.floor(a.length * 0.95)], n: a.length } : null
  }, [])

  return <LiveContext.Provider value={{ live, connected, error, latency, reload }}>{children}</LiveContext.Provider>
}

export const useLive = () => useContext(LiveContext)
