import React, { useContext } from 'react'
import { HoursCtx } from '../useOps'

// palette roles (dataviz reference instance; status steps reserved for state)
export const C = {
  actual: '#2a78d6',
  plan: '#b7d3f6',
  planStroke: '#5598e7',
  navy: '#0d366b',
  grid: '#e1e0d9',
  axis: '#898781',
  good: '#0ca30c',
  warning: '#fab219',
  critical: '#d03b3b',
  okFlow: '#256abf',
}

export const pct = (v: number | null | undefined) => (v == null || Number.isNaN(v) ? '–' : `${Math.round(v * 100)}%`)
export const num = (v: number | null | undefined, d = 1) => (v == null || Number.isNaN(v) ? '–' : Number(v).toFixed(d))
export const hhmm = (iso: string) => new Date(iso).toISOString().slice(11, 16)

export function Card({ title, subtitle, error, empty, loading, className = '', children }: {
  title: string; subtitle?: string; error?: string; empty?: boolean; loading?: boolean; className?: string; children: React.ReactNode
}) {
  const h = useContext(HoursCtx)
  return (
    <section className={`bg-white rounded-lg border border-slate-200 p-5 flex flex-col ${className}`}>
      <div className="mb-4">
        <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
        {subtitle && <p className="text-xs text-slate-500 mt-0.5">{subtitle}</p>}
      </div>
      {loading ? <Skeleton />
        : error ? <div role="alert" className="text-sm text-red-700">Could not load: {error}</div>
          : empty ? <div className="text-sm text-slate-500">No data in the last {h} h.</div> : children}
    </section>
  )
}

export function Skeleton() {
  return (
    <div className="space-y-2 animate-pulse" aria-label="Loading">
      <div className="h-4 bg-slate-100 rounded w-3/4" />
      <div className="h-4 bg-slate-100 rounded w-1/2" />
      <div className="h-24 bg-slate-100 rounded" />
    </div>
  )
}

export type Status = 'good' | 'warning' | 'critical' | 'none'
const STATUS_TEXT: Record<Status, string> = { good: 'text-green-800', warning: 'text-amber-800', critical: 'text-red-700', none: 'text-slate-500' }
const STATUS_LABEL: Record<Status, string> = { good: 'On target', warning: 'Watch', critical: 'Below target', none: '' }

export function Tile({ label, value, unit, sub, status = 'none', statusLabel }: {
  label: string; value: string; unit?: string; sub?: string; status?: Status; statusLabel?: string
}) {
  return (
    <div className="bg-white rounded-lg border border-slate-200 px-4 py-3 border-l-4" style={{ borderLeftColor: status === 'none' ? C.navy : C[status] }}>
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 flex items-baseline gap-1">
        <span className="text-2xl font-semibold text-slate-900">{value}</span>
        {unit && <span className="text-xs text-slate-500">{unit}</span>}
      </div>
      <div className="text-xs mt-1 min-h-[1rem]">
        {status !== 'none' && <span className={STATUS_TEXT[status]}>{statusLabel || STATUS_LABEL[status]}</span>}
        {sub && <span className="text-slate-500">{status !== 'none' ? ' · ' : ''}{sub}</span>}
      </div>
    </div>
  )
}

// semi-circle meter: bands [from, to, status], value 0..1, optional target tick
export function Gauge({ label, value, bands, target, caption }: {
  label: string; value: number | null; bands: [number, number, Status][]; target?: number; caption?: string
}) {
  const R = 70, CX = 90, CY = 86, W = 14
  const pt = (f: number, r = R) => {
    const a = Math.PI * (1 - f)
    return [CX + r * Math.cos(a), CY - r * Math.sin(a)]
  }
  const arc = (f0: number, f1: number) => {
    const [x0, y0] = pt(f0), [x1, y1] = pt(f1)
    return `M ${x0} ${y0} A ${R} ${R} 0 0 1 ${x1} ${y1}`
  }
  const v = value == null ? null : Math.max(0, Math.min(1, value))
  const status = v == null ? 'none' : (bands.find(([a, b]) => v >= a && v <= b)?.[2] ?? 'none')
  const [tx0, ty0] = target != null ? pt(target, R - W / 2 - 3) : [0, 0]
  const [tx1, ty1] = target != null ? pt(target, R + W / 2 + 3) : [0, 0]
  return (
    <figure className="flex flex-col items-center" title={`${label}: ${pct(value)}${target != null ? ` · target ${pct(target)}` : ''}`}>
      <svg viewBox="0 0 180 104" className="w-full max-w-[220px]" role="img" aria-label={`${label} ${pct(value)}`}>
        {bands.map(([a, b, s]) => <path key={a} d={arc(a, b)} stroke={C[s as Exclude<Status, 'none'>]} strokeOpacity={0.28} strokeWidth={W} fill="none" />)}
        {v != null && <path d={arc(0, Math.max(0.001, v))} stroke={status === 'none' ? C.actual : C[status]} strokeWidth={W - 6} fill="none" strokeLinecap="round" />}
        {target != null && <line x1={tx0} y1={ty0} x2={tx1} y2={ty1} stroke="#0b0b0b" strokeWidth={2} />}
        <text x={CX} y={CY - 6} textAnchor="middle" className="fill-slate-900" fontSize={24} fontWeight={600}>{pct(value)}</text>
      </svg>
      <figcaption className="text-center -mt-1">
        <div className="text-xs font-medium text-slate-700">{label}</div>
        <div className={`text-xs ${STATUS_TEXT[status]}`}>{caption ?? (status !== 'none' ? STATUS_LABEL[status] : '')}</div>
      </figcaption>
    </figure>
  )
}

export function Legend({ items }: { items: { label: string; color: string; hollow?: boolean }[] }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600 mb-2">
      {items.map((i) => (
        <span key={i.label} className="inline-flex items-center gap-1.5">
          <span className="inline-block w-3 h-3 rounded-sm" style={i.hollow ? { background: C.plan, border: `1px solid ${C.planStroke}` } : { background: i.color }} />
          {i.label}
        </span>
      ))}
    </div>
  )
}

const PHASE_STYLE: Record<string, string> = {
  SCHEDULED: 'bg-slate-100 text-slate-700 border-slate-200',
  BOARDING: 'bg-sky-50 text-sky-800 border-sky-200',
  TAXI_OUT: 'bg-blue-50 text-blue-800 border-blue-200',
  AIRBORNE: 'bg-blue-100 text-blue-900 border-blue-300',
  TAXI_IN: 'bg-indigo-50 text-indigo-800 border-indigo-200',
  AT_GATE: 'bg-white text-slate-700 border-slate-300',
  CANCELLED: 'bg-red-50 text-red-800 border-red-200',
  DIVERTED: 'bg-amber-50 text-amber-900 border-amber-200',
}

export function PhaseBadge({ phase }: { phase: string }) {
  return <span className={`inline-block px-1.5 py-0.5 rounded border text-[10px] font-medium ${PHASE_STYLE[phase] || PHASE_STYLE.SCHEDULED}`}>{phase.replace('_', ' ')}</span>
}

export function delayStatus(m: number | null | undefined): Status {
  if (m == null) return 'none'
  return m < 1 ? 'good' : m < 15 ? 'warning' : 'critical'
}

export function DelayCell({ m }: { m: number | null | undefined }) {
  const s = delayStatus(m)
  if (s === 'none') return <span className="text-slate-400">–</span>
  return (
    <span className="inline-flex items-center gap-1.5 justify-end">
      <span className="inline-block w-2 h-2 rounded-full" style={{ background: C[s] }} aria-hidden />
      <span className={STATUS_TEXT[s]}>{m! > 0 ? `+${m}` : m} min</span>
    </span>
  )
}

export type Tone = 'good' | 'warning' | 'critical' | 'info' | 'neutral'
const TONE: Record<Tone, string> = {
  good: 'bg-green-50 text-green-800 border-green-200',
  warning: 'bg-amber-50 text-amber-900 border-amber-200',
  critical: 'bg-red-50 text-red-700 border-red-200',
  info: 'bg-blue-50 text-blue-800 border-blue-200',
  neutral: 'bg-slate-100 text-slate-600 border-slate-200',
}

export function Pill({ tone, children, className = '' }: { tone: Tone; children: React.ReactNode; className?: string }) {
  return <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[11px] font-medium whitespace-nowrap ${TONE[tone]} ${className}`}>{children}</span>
}

// departure status pill: on time / +n min / pending / cancelled
export function DelayPill({ f }: { f: Record<string, any> }) {
  if (f.phase === 'CANCELLED') return <Pill tone="critical">Cancelled</Pill>
  if (f.phase === 'DIVERTED') return <Pill tone="warning">Diverted</Pill>
  const m = f.dep_delay_min
  if (m == null) return <Pill tone="neutral">Pending</Pill>
  const s = delayStatus(m)
  return <Pill tone={s === 'good' ? 'good' : s === 'warning' ? 'warning' : 'critical'}>{m < 1 ? 'On time' : `+${m} min`}</Pill>
}

const ICONS: Record<string, string> = {
  overview: 'M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z',
  flights: 'M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z',
  list: 'M4 6h16M4 12h16M4 18h16',
  kanban: 'M4 4h4v16H4zM10 4h4v10h-4zM16 4h4v13h-4z',
  back: 'M15 18l-6-6 6-6',
  search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zm10 17-4.35-4.35',
  clock: 'M12 7v5l3 2M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z',
  gate: 'M4 21V5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v16M9 21v-6h6v6',
  plane: 'M2 19h20M5 15l14-6-2-3-5 2-4-4-2 1 2 4-4 2-2-1-1 1z',
  connections: 'M7 7h10l-3-3M17 17H7l3 3M4 12h16',
  gates: 'M4 21V5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v16M9 21v-6h6v6',
  rotations: 'M4 12a8 8 0 0 1 14-5.3M20 12a8 8 0 0 1-14 5.3M18 3v4h-4M6 21v-4h4',
  alert: 'M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z',
}

export function Icon({ name, className = 'w-4 h-4' }: { name: keyof typeof ICONS | string; className?: string }) {
  const filled = name === 'overview' || name === 'flights'
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden fill={filled ? 'currentColor' : 'none'} stroke={filled ? 'none' : 'currentColor'} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <path d={ICONS[name]} />
    </svg>
  )
}
