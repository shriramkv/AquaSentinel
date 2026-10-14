import { useEffect, useMemo, useRef, useState } from 'react'
import { useI18n } from '../i18n'

export interface Pt { t: number; v: number | null; flagged?: boolean }
interface Props {
  points: Pt[]
  baseline?: number | null
  unit: string
  digits: number
  height?: number
  label: string
  formatTime?: (t: number) => string
}

const defaultTime = (t: number) => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })

/** One series, one axis. 2px line, hairline grid, dashed baseline, crosshair tooltip on hover. */
export function LineChart({ points, baseline, unit, digits, height = 120, label, formatTime = defaultTime }: Props) {
  const { t } = useI18n()
  const box = useRef<HTMLDivElement>(null)
  const [w, setW] = useState(320)
  const [hover, setHover] = useState<number | null>(null)
  useEffect(() => {
    if (!box.current) return
    const ro = new ResizeObserver(([e]) => setW(Math.max(160, e.contentRect.width)))
    ro.observe(box.current)
    return () => ro.disconnect()
  }, [])

  const pts = useMemo(() => points.filter((p): p is Pt & { v: number } => p.v != null), [points])
  const m = useMemo(() => ({ l: 40, r: 8, t: 8, b: 18 }), [])
  const iw = w - m.l - m.r, ih = height - m.t - m.b

  const scale = useMemo(() => {
    if (pts.length < 2) return null
    const vs = pts.map((p) => p.v)
    let lo = Math.min(...vs, baseline ?? Infinity), hi = Math.max(...vs, baseline ?? -Infinity)
    const pad = Math.max((hi - lo) * 0.12, Math.abs(hi) * 0.004, 10 ** -digits * 2)
    lo -= pad; hi += pad
    const t0 = pts[0].t, t1 = pts[pts.length - 1].t
    return {
      lo, hi, t0, t1,
      x: (t: number) => m.l + ((t - t0) / Math.max(1e-6, t1 - t0)) * iw,
      y: (v: number) => m.t + (1 - (v - lo) / (hi - lo)) * ih,
    }
  }, [pts, baseline, digits, iw, ih, m])

  if (!scale) return <div ref={box} style={{ height }} className="flex items-center justify-center text-xs text-muted">{t('tele.waiting')}</div>

  const path = pts.map((p, i) => `${i ? 'L' : 'M'}${scale.x(p.t).toFixed(1)},${scale.y(p.v).toFixed(1)}`).join('')
  const ticks = [scale.lo + (scale.hi - scale.lo) * 0.1, (scale.lo + scale.hi) / 2, scale.hi - (scale.hi - scale.lo) * 0.1]
  const h = hover != null ? pts[hover] : null
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    const t = scale.t0 + ((e.clientX - r.left - m.l) / iw) * (scale.t1 - scale.t0)
    let lo = 0, hi = pts.length - 1
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (pts[mid].t < t) lo = mid; else hi = mid }
    setHover(Math.abs(pts[lo].t - t) < Math.abs(pts[hi].t - t) ? lo : hi)
  }
  const tipLeft = h ? Math.min(Math.max(scale.x(h.t), m.l + 50), w - 60) : 0

  return (
    <div ref={box} className="relative" style={{ height }}>
      <svg width={w} height={height} role="img" aria-label={label} onPointerMove={onMove} onPointerLeave={() => setHover(null)} style={{ touchAction: 'pan-y', display: 'block' }}>
        {ticks.map((v, i) => (
          <g key={i}>
            <line x1={m.l} x2={w - m.r} y1={scale.y(v)} y2={scale.y(v)} stroke="var(--grid)" strokeWidth={1} />
            <text x={m.l - 6} y={scale.y(v) + 3} textAnchor="end" fontSize={10} fill="var(--muted)" style={{ fontVariantNumeric: 'tabular-nums' }}>{v.toFixed(digits)}</text>
          </g>
        ))}
        {baseline != null && (
          <line x1={m.l} x2={w - m.r} y1={scale.y(baseline)} y2={scale.y(baseline)} stroke="var(--axis)" strokeWidth={1.5} strokeDasharray="4 4" />
        )}
        <text x={m.l} y={height - 4} fontSize={10} fill="var(--muted)">{formatTime(scale.t0)}</text>
        <text x={w - m.r} y={height - 4} fontSize={10} fill="var(--muted)" textAnchor="end">{formatTime(scale.t1)}</text>
        <path d={path} fill="none" stroke="var(--series)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {pts.map((p, i) => p.flagged && (i % Math.ceil(pts.length / 240) === 0) && (
          <circle key={i} cx={scale.x(p.t)} cy={scale.y(p.v)} r={2.6} fill="var(--critical)" stroke="var(--surface)" strokeWidth={1} />
        ))}
        {h && (
          <g pointerEvents="none">
            <line x1={scale.x(h.t)} x2={scale.x(h.t)} y1={m.t} y2={m.t + ih} stroke="var(--axis)" strokeWidth={1} />
            <circle cx={scale.x(h.t)} cy={scale.y(h.v)} r={4.5} fill="var(--series)" stroke="var(--surface)" strokeWidth={2} />
          </g>
        )}
      </svg>
      {h && (
        <div className="absolute pointer-events-none card px-2 py-1 text-[0.7rem] leading-tight whitespace-nowrap" style={{ left: tipLeft, top: 0, transform: 'translate(-50%, -4px)' }}>
          <span className="font-semibold tnum">{h.v.toFixed(digits)} {unit}</span>
          <span className="text-muted"> · {formatTime(h.t)}{h.flagged ? ` · ${t('c.flagged')}` : ''}</span>
        </div>
      )}
    </div>
  )
}
