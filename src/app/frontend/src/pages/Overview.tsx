import { useContext } from 'react'
import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { C, Card, DelayPill, Gauge, Icon, Legend, PhaseBadge, Status, Tile, hhmm, num, pct } from '../components/ui'
import { PageHeader } from '../components/Shell'
import { AXIS, Connections, Gates, P, TIP, UTIL_BANDS, gateUtil } from '../components/charts'
import { HoursCtx, Ops, Rows, RoleInfo, go, goWith } from '../useOps'

const OTP_TARGET = 0.8
const OTP_BANDS: [number, number, Status][] = [[0, 0.7, 'critical'], [0.7, OTP_TARGET, 'warning'], [OTP_TARGET, 1, 'good']]
function otpStatus(v: number | null): Status {
  if (v == null) return 'none'
  return v >= OTP_TARGET ? 'good' : v >= 0.7 ? 'warning' : 'critical'
}

// network totals weighted like the backend (OTP by movements, taxi by movements)
function aggregate(rows: Rows) {
  const sum = (f: (r: any) => number) => rows.reduce((a, r) => a + (f(r) || 0), 0)
  const wavg = (col: string, w: string) => {
    const d = sum((r) => (r[col] == null ? 0 : r[w]))
    return d ? sum((r) => (r[col] == null ? 0 : r[col] * r[w])) / d : null
  }
  return {
    departed: sum((r) => r.departed), arrived: sum((r) => r.arrived),
    cancellations: sum((r) => r.cancellations), diversions: sum((r) => r.diversions),
    dep_otp_d15: wavg('dep_otp_d15', 'departed'), arr_otp_d15: wavg('arr_otp_d15', 'arrived'),
    dep_otp_d0: wavg('dep_otp_d0', 'departed'), avg_taxi_out: wavg('avg_taxi_out', 'departed'),
  }
}

export default function Overview({ info, ops }: { info: RoleInfo; ops: Ops }) {
  const { data, errors, loading, allowed } = ops
  // gates exist only at the hub: hide gate views when the role/airport has none
  const hasGates = allowed.includes('gates') && (loading || !!data.gates?.length)
  const multi = (data.kpis?.length || 0) > 1
  return (
    <>
      <PageHeader title="Overview" subtitle={`${info.label} · ${ops.airport || (info.airports.includes('*') ? 'all airports' : info.airports.join(', '))} · last ${ops.hours} h · click a chart to filter flights`} />
      <div className="space-y-5">
        {allowed.includes('kpis') && <KpiStrip rows={data.kpis} error={errors.kpis} loading={loading} />}

        <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
          {allowed.includes('kpis') && <Gauges kpis={data.kpis} gates={hasGates ? data.gates : undefined} error={errors.kpis} loading={loading} />}
          {allowed.includes('hourly') && <Hourly rows={data.hourly} error={errors.hourly} loading={loading} className="xl:col-span-2" />}
          {!allowed.includes('hourly') && allowed.includes('connections') && <Connections rows={data.connections} error={errors.connections} loading={loading} className="xl:col-span-2" />}
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
          {allowed.includes('delays') && <Delays rows={data.delays} error={errors.delays} loading={loading} />}
          {hasGates && <Gates rows={data.gates} error={errors.gates} loading={loading} className="xl:col-span-2" />}
          {!hasGates && allowed.includes('flights') && <Attention rows={data.flights} error={errors.flights} loading={loading} className={allowed.includes('delays') ? 'xl:col-span-2' : 'xl:col-span-3'} />}
        </div>

        {allowed.includes('hourly') && allowed.includes('connections') && <Connections rows={data.connections} error={errors.connections} loading={loading} />}

        {(hasGates || multi) && (
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
            {hasGates && allowed.includes('flights') && <Attention rows={data.flights} error={errors.flights} loading={loading} className={multi ? 'xl:col-span-2' : 'xl:col-span-3'} />}
            {multi && <AirportTable rows={data.kpis} error={errors.kpis} loading={loading} onPick={ops.setAirport} className={hasGates ? '' : 'xl:col-span-3'} />}
          </div>
        )}
      </div>
    </>
  )
}

function KpiStrip({ rows, error, loading }: P) {
  const h = useContext(HoursCtx)
  if (error) return <Card title={`Network KPIs · last ${h} h`} error={error}>{null}</Card>
  const a = aggregate(rows || [])
  const empty = !loading && !rows?.length
  const v = (x: any, f: (y: any) => string) => (loading || empty ? '–' : f(x))
  return (
    <section aria-label="KPIs" className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-3">
      <Tile label="Departed" value={v(a.departed, String)} unit="flights" sub={`last ${h} h`} />
      <Tile label="Arrived" value={v(a.arrived, String)} unit="flights" sub={`last ${h} h`} />
      <Tile label="Departure OTP D15" value={v(a.dep_otp_d15, pct)} status={empty ? 'none' : otpStatus(a.dep_otp_d15)} sub={`target ${pct(OTP_TARGET)}`} />
      <Tile label="Arrival OTP D15" value={v(a.arr_otp_d15, pct)} status={empty ? 'none' : otpStatus(a.arr_otp_d15)} sub={`target ${pct(OTP_TARGET)}`} />
      <Tile label="Cancelled" value={v(a.cancellations, String)} unit="flights" status={empty ? 'none' : a.cancellations ? 'warning' : 'good'} statusLabel={a.cancellations ? 'Review' : 'None'} />
      <Tile label="Diverted" value={v(a.diversions, String)} unit="flights" status={empty ? 'none' : a.diversions ? 'warning' : 'good'} statusLabel={a.diversions ? 'Review' : 'None'} />
      <Tile label="Avg taxi-out" value={v(a.avg_taxi_out, (x) => num(x))} unit="min" sub="per departure" />
    </section>
  )
}

function Gauges({ kpis, gates, error, loading }: { kpis?: Rows; gates?: Rows; error?: string; loading?: boolean }) {
  const h = useContext(HoursCtx)
  const a = aggregate(kpis || [])
  const g = gates ? gateUtil(gates) : null
  return (
    <Card title="Performance vs target" subtitle={gates ? `OTP: movements within 15 min · gates: occupied / available minutes · last ${h} h` : `Share of movements within 15 min · last ${h} h`} error={error} loading={loading} empty={!kpis?.length}>
      <div className={`grid ${gates ? 'grid-cols-3' : 'grid-cols-2'} gap-2 items-end`}>
        <Gauge label="Departure OTP D15" value={a.dep_otp_d15} bands={OTP_BANDS} target={OTP_TARGET} />
        <Gauge label="Arrival OTP D15" value={a.arr_otp_d15} bands={OTP_BANDS} target={OTP_TARGET} />
        {gates && <Gauge label="Gate utilisation" value={g?.util ?? null} bands={UTIL_BANDS}
          caption={g ? `${g.gates} gates` : 'No gate data'} />}
      </div>
      <div className="flex gap-4 text-xs text-slate-500 mt-3 justify-center">
        <span className="inline-flex items-center gap-1"><span className="w-3 h-[2px] bg-black inline-block" />Target</span>
        <span className="inline-flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm inline-block" style={{ background: C.good }} />On target</span>
        <span className="inline-flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm inline-block" style={{ background: C.warning }} />Watch</span>
        <span className="inline-flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm inline-block" style={{ background: C.critical }} />Below / congested</span>
      </div>
    </Card>
  )
}

function Hourly({ rows, error, loading, className }: P) {
  const h = useContext(HoursCtx)
  const d = (rows || []).map((r) => ({ ...r, hour: hhmm(r.hour_start_utc).slice(0, 2) }))
  const chart = (sched: string, act: string, title: string) => (
    <div>
      <div className="text-xs font-medium text-slate-700 mb-1">{title}</div>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={d} barGap={2} barCategoryGap="20%" className="cursor-pointer"
          onClick={(e: any) => e?.activePayload?.[0] && goWith({ hour: e.activePayload[0].payload.hour_start_utc, dir: sched.startsWith('dep') ? 'dep' : 'arr', window: 'all' }, 'flights')}>
          <CartesianGrid vertical={false} stroke={C.grid} />
          <XAxis dataKey="hour" {...AXIS} interval={2} />
          <YAxis {...AXIS} allowDecimals={false} width={28} axisLine={false} />
          <Tooltip {...TIP} labelFormatter={(h) => `${h}:00 UTC`} />
          <Bar dataKey={sched} name="Scheduled" fill={C.plan} stroke={C.planStroke} radius={[3, 3, 0, 0]} isAnimationActive={false} />
          <Bar dataKey={act} name="Actual" fill={C.actual} radius={[3, 3, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
  return (
    <Card title="Movements per hour" subtitle={`Scheduled vs actual · UTC · last ${h} h · click an hour for its flights`} error={error} loading={loading} empty={!d.length} className={className}>
      <Legend items={[{ label: 'Scheduled', color: C.plan, hollow: true }, { label: 'Actual', color: C.actual }]} />
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {chart('dep_scheduled', 'departed', 'Departures')}
        {chart('arr_scheduled', 'arrived', 'Arrivals')}
      </div>
    </Card>
  )
}

function Delays({ rows, error, loading, className }: P) {
  const h = useContext(HoursCtx)
  const d = [...(rows || [])].sort((a, b) => b.total_delay_minutes - a.total_delay_minutes)
    .map((r): Record<string, any> => ({ ...r, cause: String(r.delay_cause).replace('_', ' ') }))
  const total = d.reduce((a, r) => a + r.total_delay_minutes, 0)
  return (
    <Card title="Departure delay by cause" subtitle={`Delay minutes · last ${h} h · ${total.toLocaleString()} min total · click a cause`} error={error} loading={loading} empty={!d.length} className={className}>
      <ResponsiveContainer width="100%" height={Math.max(200, d.length * 40)}>
        <BarChart data={d} layout="vertical" margin={{ left: 8, right: 16 }} className="cursor-pointer"
          onClick={(e: any) => e?.activePayload?.[0] && goWith({ cause: e.activePayload[0].payload.delay_cause, window: 'all' }, 'flights')}>
          <CartesianGrid horizontal={false} stroke={C.grid} />
          <XAxis type="number" {...AXIS} />
          <YAxis type="category" dataKey="cause" {...AXIS} width={92} axisLine={false} />
          <Tooltip {...TIP} formatter={(v: number, _n, p: any) => [`${v} min · ${p.payload.flights} flights`, 'Delay']} />
          <Bar dataKey="total_delay_minutes" name="Delay minutes" fill={C.actual} radius={[0, 4, 4, 0]} barSize={18} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </Card>
  )
}

function Attention({ rows, error, loading, className }: P) {
  const d = (rows || []).filter((f) => f.phase === 'CANCELLED' || f.phase === 'DIVERTED' || (f.dep_delay_min ?? 0) >= 15)
    .sort((a, b) => b.std_utc.localeCompare(a.std_utc)).slice(0, 8)
  return (
    <Card title="Needs attention" subtitle="Latest flights delayed 15 min or more, cancelled or diverted · click a flight for detail" error={error} loading={loading} empty={!rows?.length} className={className}>
      {!d.length ? <div className="text-sm text-slate-500">No disrupted flights in the window.</div> : (
        <ul className="divide-y divide-slate-100 -mx-2">
          {d.map((f) => (
            <li key={f.flight_id}>
              <button onClick={() => go('flights', f.flight_id)} className="w-full grid grid-cols-[5rem_1fr_5rem_6rem_6rem] items-center gap-3 px-2 py-2 text-left text-xs hover:bg-slate-50 rounded tabular-nums">
                <span className="font-semibold text-sm">AL{f.flight_number}</span>
                <span className="text-slate-600">{f.origin_iata} → {f.destination_iata}{f.delay_cause ? ` · ${String(f.delay_cause).replace('_', ' ').toLowerCase()}` : ''}</span>
                <span className="text-slate-500">STD {hhmm(f.std_utc)}Z</span>
                <PhaseBadge phase={f.phase} />
                <span className="text-right"><DelayPill f={f} /></span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <button onClick={() => goWith({ status: 'disrupted', window: 'all' }, 'flights')} className="mt-3 self-start inline-flex items-center gap-1 text-sm text-blue-700 hover:underline"><Icon name="flights" className="w-3.5 h-3.5" />View all disrupted flights</button>
    </Card>
  )
}

function AirportTable({ rows, error, loading, className, onPick }: P & { onPick?: (a: string) => void }) {
  const h = useContext(HoursCtx)
  const cls = (v: number | null) => ({ good: 'text-green-800', warning: 'text-amber-800', critical: 'text-red-700', none: 'text-slate-400' }[otpStatus(v)])
  return (
    <Card title="By airport" subtitle={`OTP D0 / D15 · taxi minutes · last ${h} h · click an airport to focus`} error={error} loading={loading} empty={!rows?.length} className={className}>
      <div className="overflow-auto max-h-[420px]">
        <table className="w-full text-xs tabular-nums">
          <thead className="text-slate-500 sticky top-0 bg-white">
            <tr>
              <th className="text-left py-1.5 font-medium">Airport</th><th className="text-right font-medium">Dep</th>
              <th className="text-right font-medium">Dep D0</th><th className="text-right font-medium">Dep D15</th>
              <th className="text-right font-medium">Arr D15</th><th className="text-right font-medium">Taxi out</th>
              <th className="text-right font-medium">Canc.</th>
            </tr>
          </thead>
          <tbody>
            {(rows || []).map((k) => (
              <tr key={k.airport_iata} onClick={() => onPick?.(k.airport_iata)} className="border-t border-slate-100 cursor-pointer hover:bg-slate-50">
                <td className="py-1.5 font-semibold">{k.airport_iata}</td>
                <td className="text-right">{k.departed}</td>
                <td className={`text-right ${cls(k.dep_otp_d0)}`}>{pct(k.dep_otp_d0)}</td>
                <td className={`text-right ${cls(k.dep_otp_d15)}`}>{pct(k.dep_otp_d15)}</td>
                <td className={`text-right ${cls(k.arr_otp_d15)}`}>{pct(k.arr_otp_d15)}</td>
                <td className="text-right">{num(k.avg_taxi_out)}</td>
                <td className="text-right">{k.cancellations}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
