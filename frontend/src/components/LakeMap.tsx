import L from 'leaflet'
import { useEffect, useMemo } from 'react'
import { CircleMarker, MapContainer, Marker, Polygon, Polyline, TileLayer, Tooltip, useMap } from 'react-leaflet'
import { fmt } from '../api'
import { useI18n } from '../i18n'
import { placeName, sourceNote } from '../text'
import type { Live } from '../store'
import type { ParamKey } from '../types'

// Sequential blue ramp (one hue, light to dark) for the value of the selected parameter
const RAMP = ['#cde2fb', '#b7d3f6', '#9ec5f4', '#86b6ef', '#6da7ec', '#5598e7', '#3987e5', '#2a78d6', '#256abf', '#1c5cab', '#184f95', '#104281', '#0d366b']

export function useRamp(dark: boolean) {
  // On a dark surface the ramp is reversed so "more" is always further from the background
  return useMemo(() => (dark ? [...RAMP].reverse() : RAMP), [dark])
}

function Fit({ outline }: { outline: [number, number][] }) {
  const map = useMap()
  useEffect(() => {
    const fit = () => map.fitBounds(L.latLngBounds(outline), { padding: [18, 18] })
    fit()
    const ro = new ResizeObserver(() => { map.invalidateSize(); fit() })
    ro.observe(map.getContainer())
    return () => ro.disconnect()
  }, [map, outline])
  return null
}

const label = (html: string, w = 120) => L.divIcon({ className: 'map-label', html, iconSize: [w, 20], iconAnchor: [w / 2, 10] })

const boatIcon = (bearing: number, tracing: boolean) => L.divIcon({
  className: 'map-label',
  iconSize: [34, 34], iconAnchor: [17, 17],
  html: `<div style="position:relative;width:34px;height:34px">
    ${tracing ? '<div class="pulse" style="position:absolute;inset:0;border-radius:50%;border:2px solid var(--serious)"></div>' : ''}
    <svg class="boat-icon" width="34" height="34" viewBox="-17 -17 34 34" style="transform:rotate(${bearing}deg)">
      <path d="M0,-13 C5,-6 6,2 5,10 L-5,10 C-6,2 -5,-6 0,-13Z" fill="var(--accent)" stroke="var(--surface)" stroke-width="2"/>
      <circle r="2.2" cy="2" fill="var(--surface)"/>
    </svg></div>`,
})

const pin = (color: string, glyph: string) => L.divIcon({
  className: 'map-label', iconSize: [22, 22], iconAnchor: [11, 11],
  html: `<div style="width:22px;height:22px;border-radius:50%;background:${color};border:2px solid var(--surface);display:flex;align-items:center;justify-content:center;color:#fff;font-size:12px;font-weight:700;line-height:1">${glyph}</div>`,
})

interface Props { live: Live; param: ParamKey; dark: boolean; showTruth: boolean; domain: [number, number] | null }

// One finger scrolls the page on touch screens; pinch and the +/- buttons still zoom the map
const COARSE = typeof matchMedia !== 'undefined' && matchMedia('(pointer: coarse)').matches

export function LakeMap({ live, param, dark, showTruth, domain }: Props) {
  const { t, tm, lang } = useI18n()
  const ramp = useRamp(dark)
  const { lake, boat, mission, readings, anomalies, scenarios, trace_path, zones } = live
  const colour = (v: number | null) => {
    if (v == null || !domain) return 'var(--muted)'
    const f = Math.min(1, Math.max(0, (v - domain[0]) / Math.max(1e-9, domain[1] - domain[0])))
    return ramp[Math.round(f * (ramp.length - 1))]
  }
  // Draw at most ~700 track points: every reading from the last few minutes, thinned further back
  const track = useMemo(() => {
    const n = readings.length
    const step = Math.ceil(Math.max(1, n - 300) / 400)
    return readings.filter((_, i) => i >= n - 300 || i % step === 0)
  }, [readings])
  const meta = { ...live.config.params[param], label: t(`param.${param}`) }
  const remaining = mission && boat && mission.status === 'active' ? mission.waypoints.slice(Math.max(0, boat.waypoint_index)) : []

  return (
    <MapContainer center={lake.centre} zoom={16} zoomSnap={0.25} attributionControl={!lake.demo} className="h-full w-full" scrollWheelZoom dragging={!COARSE}>
      <Fit outline={lake.outline} />
      {!lake.demo && <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" attribution="&copy; OpenStreetMap contributors" />}
      <Polygon positions={lake.outline} pathOptions={{ color: 'var(--axis)', weight: 1.5, fillColor: 'var(--water)', fillOpacity: lake.demo ? 1 : 0.35 }} interactive={false} />

      {mission && remaining.length > 0 && boat && (
        <Polyline positions={[[boat.lat, boat.lon], ...remaining]} pathOptions={{ color: 'var(--muted)', weight: 1.5, dashArray: '5 6' }} interactive={false} />
      )}
      {track.map((r) => (
        <CircleMarker key={r.id} center={[r.lat, r.lon]} radius={r.flagged ? 5 : 4}
          pathOptions={{ color: r.flagged ? 'var(--critical)' : 'var(--surface)', weight: r.flagged ? 1.5 : 0.5, fillColor: colour(r[param]), fillOpacity: 1 }}>
          <Tooltip direction="top" offset={[0, -4]}>
            <b>{meta.label} {fmt(r[param], meta.digits)} {meta.unit}</b>{r.flagged ? ` · ${t('c.flagged')}` : ''}<br />{t('c.zone', { z: r.zone })}
          </Tooltip>
        </CircleMarker>
      ))}
      {trace_path.length > 1 && <Polyline positions={trace_path} pathOptions={{ color: 'var(--serious)', weight: 2.5 }} interactive={false} />}

      {lake.zones.map((z) => {
        const p = zones.zones[z.id]
        const c = p?.available ? (p.health_status === 'Healthy' ? 'var(--good)' : p.health_status === 'Stressed' ? 'var(--warning)' : 'var(--critical)') : 'var(--muted)'
        return (
          <Marker key={z.id} position={[z.lat, z.lon]} interactive={false} zIndexOffset={-500}
            icon={label(`<div style="display:flex;justify-content:center"><span style="display:inline-flex;align-items:center;gap:4px;font-size:10px;font-weight:650;color:var(--ink-2);background:color-mix(in srgb,var(--surface) 78%,transparent);padding:1px 6px;border-radius:99px"><i style="width:7px;height:7px;border-radius:50%;background:${c};display:inline-block"></i>${z.id}${p?.available ? ' · ' + tm('health.' + p.health_status, p.health_status) : ''}</span></div>`)} />
        )
      })}
      {lake.places.map((p) => (
        <CircleMarker key={`${p.id}-${lang}`} center={[p.lat, p.lon]} radius={5} pathOptions={{ color: 'var(--ink-2)', weight: 2, fillColor: 'var(--surface)', fillOpacity: 1 }}>
          <Tooltip direction="top" offset={[0, -4]} permanent={p.kind !== 'bay'} className="!text-[0.65rem] !py-0 !px-1.5">{placeName(tm, p)}</Tooltip>
        </CircleMarker>
      ))}
      {anomalies.filter((a) => a.source_lat != null).slice(0, 6).map((a) => (
        <Marker key={a.id} position={[a.source_lat!, a.source_lon!]} icon={pin('var(--critical)', '!')} zIndexOffset={500}>
          <Tooltip direction="top" offset={[0, -10]}><b>{t('live.sourceEst', { id: a.id })}</b><br />{sourceNote(t, tm, a)}</Tooltip>
        </Marker>
      ))}
      {showTruth && scenarios.map((s) => (
        <Marker key={s.id} position={[s.lat, s.lon]} icon={pin('var(--ink)', '×')} zIndexOffset={400}>
          <Tooltip direction="bottom" offset={[0, 10]}>{t('live.trueSource', { label: tm(`sc.${s.kind}`, s.label) })}</Tooltip>
        </Marker>
      ))}
      {boat && (
        <Marker position={[boat.lat, boat.lon]} icon={boatIcon(boat.bearing, boat.mode === 'trace')} zIndexOffset={1000}>
          <Tooltip direction="top" offset={[0, -14]}>{t('live.boat')} · {tm(`mode.${boat.mode}`, boat.mode)} · {boat.speed_ms} m/s</Tooltip>
        </Marker>
      )}
    </MapContainer>
  )
}

export function RampLegend({ dark, domain, label, unit, digits }: { dark: boolean; domain: [number, number] | null; label: string; unit: string; digits: number }) {
  const ramp = useRamp(dark)
  return (
    <div className="flex items-center gap-2 text-[0.7rem] text-ink-2">
      <span className="tnum">{domain ? domain[0].toFixed(digits) : '–'}</span>
      <span className="h-2 w-20 sm:w-28 rounded-full" style={{ background: `linear-gradient(to right, ${ramp.join(',')})` }} aria-hidden />
      <span className="tnum">{domain ? domain[1].toFixed(digits) : '–'}</span>
      <span className="text-muted">{label}{unit ? `, ${unit}` : ''}</span>
    </div>
  )
}
