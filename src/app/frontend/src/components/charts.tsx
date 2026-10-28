import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { C, Card, Legend, Status, Tile, hhmm, pct } from './ui'
import { useContext } from 'react'
import { HoursCtx, Rows } from '../useOps'

export type P = { rows?: Rows; error?: string; loading?: boolean; className?: string }

export const UTIL_BANDS: [number, number, Status][] = [[0, 0.75, 'good'], [0.75, 0.9, 'warning'], [0.9, 1, 'critical']]
export const AXIS = { fontSize: 11, stroke: C.axis, tickLine: false }
export const TIP = { contentStyle: { fontSize: 12, borderRadius: 6, border: '1px solid #e2e8f0' }, cursor: { fill: 'rgba(42,120,214,0.06)' } }

export function gateUtil(rows: Rows) {
  if (!rows.length) return null
  const gates = new Set(rows.map((r) => `${r.airport_iata} ${r.gate_id}`)).size
  const hours = new Set(rows.map((r) => r.hour_start_utc)).size
  const occ = rows.reduce((a, r) => a + (r.occupied_minutes || 0), 0)
  return { util: occ / (gates * hours * 60), gates }
}

// selected gate row is highlighted; clicking a gate label calls onSelect
export function Gates({ rows, error, loading, className, selected, onSelect, maxH = 360 }: P & { selected?: string; onSelect?: (gate: string) => void; maxH?: number }) {
  const hours = Array.from(new Set((rows || []).map((r) => r.hour_start_utc))).sort()
  const gates = Array.from(new Set((rows || []).map((r) => `${r.airport_iata} ${r.gate_id}`))).sort()
  const cell = new Map((rows || []).map((r) => [`${r.airport_iata} ${r.gate_id}|${r.hour_start_utc}`, r.occupied_minutes as number]))
  // sequential blue ramp, 100 -> 600
  const RAMP = ['#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95']
  const shade = (m?: number) => (m == null ? '#f6f6f4' : RAMP[Math.min(RAMP.length - 1, Math.floor((m / 60) * RAMP.length))])
  return (
    <Card title="Gate occupancy" subtitle="Occupied minutes per gate and hour · UTC" error={error} loading={loading} empty={!rows?.length} className={className}>
      <div className="overflow-auto" style={{ maxHeight: maxH }}>
        <table className="text-[11px] border-separate" style={{ borderSpacing: 2 }}>
          <thead className="sticky top-0 bg-white">
            <tr><th />{hours.map((h, i) => <th key={h} className="font-normal text-slate-500 w-5">{i % 2 === 0 ? hhmm(h).slice(0, 2) : ''}</th>)}</tr>
          </thead>
          <tbody>
            {gates.map((g) => (
              <tr key={g} className={selected && selected !== g ? 'opacity-40' : ''}>
                <td className="pr-2 text-slate-600 whitespace-nowrap">
                  {onSelect ? <button onClick={() => onSelect(g)} className={`hover:text-blue-700 ${selected === g ? 'font-semibold text-blue-700' : ''}`}>{g}</button> : g}
                </td>
                {hours.map((h) => {
                  const m = cell.get(`${g}|${h}`)
                  return <td key={h} title={`${g} ${hhmm(h)}Z: ${m ?? 0} min`} className="rounded-[3px]" style={{ background: shade(m), width: 20, height: 16 }} />
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex items-center gap-2 text-xs text-slate-500 mt-3">
        <span>Idle</span>
        {['#f6f6f4', ...RAMP].map((c) => <span key={c} className="inline-block w-5 h-3 rounded-sm" style={{ background: c }} />)}
        <span>60 min</span>
      </div>
    </Card>
  )
}

export function Connections({ rows, error, loading, className, title = 'Hub connections' }: P & { title?: string }) {
  const d = [...(rows || [])].sort((a, b) => a.hour_start_utc.localeCompare(b.hour_start_utc)).map((r): Record<string, any> => ({
    ...r, hour: hhmm(r.hour_start_utc).slice(0, 2),
    ok: Math.max(0, r.pax_connecting - r.pax_at_risk - r.pax_missed),
  }))
  const tot = (k: string) => d.reduce((a, r) => a + (r[k] || 0), 0)
  const pax = tot('pax_connecting'), risk = tot('pax_at_risk'), missed = tot('pax_missed')
  const h = useContext(HoursCtx)
  return (
    <Card title={title} subtitle={`Connecting passengers by outbound hour · UTC · last ${h} h`} error={error} loading={loading} empty={!d.length} className={className}>
      <div className="grid grid-cols-3 gap-3 mb-4">
        <Tile label="Connecting pax" value={pax.toLocaleString()} sub={`${tot('connections')} connections`} />
        <Tile label="Pax at risk" value={risk.toLocaleString()} status={risk ? 'warning' : 'good'} statusLabel={`${pct(pax ? risk / pax : 0)} of pax`} />
        <Tile label="Pax missed" value={missed.toLocaleString()} status={missed ? 'critical' : 'good'} statusLabel={`${tot('connections_missed')} connections`} />
      </div>
      <Legend items={[{ label: 'Protected', color: C.okFlow }, { label: 'At risk', color: C.warning }, { label: 'Missed', color: C.critical }]} />
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={d} barCategoryGap="18%">
          <CartesianGrid vertical={false} stroke={C.grid} />
          <XAxis dataKey="hour" {...AXIS} interval={1} />
          <YAxis {...AXIS} width={36} axisLine={false} />
          <Tooltip {...TIP} labelFormatter={(h) => `${h}:00 UTC`} />
          <Bar dataKey="ok" name="Protected" stackId="p" fill={C.okFlow} stroke="#fff" strokeWidth={1} isAnimationActive={false} />
          <Bar dataKey="pax_at_risk" name="At risk" stackId="p" fill={C.warning} stroke="#fff" strokeWidth={1} isAnimationActive={false} />
          <Bar dataKey="pax_missed" name="Missed" stackId="p" fill={C.critical} stroke="#fff" strokeWidth={1} radius={[3, 3, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </Card>
  )
}

