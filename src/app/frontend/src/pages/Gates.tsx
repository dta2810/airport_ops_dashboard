import { useMemo, useState } from 'react'
import { PageHeader } from '../components/Shell'
import { Gates as Heatmap, UTIL_BANDS, gateUtil } from '../components/charts'
import { C, Card, DelayPill, Gauge, Icon, Pill, Status, Tile, hhmm, pct } from '../components/ui'
import { Ops, Rows, go } from '../useOps'

const utilStatus = (u: number | null): Status => (u == null ? 'none' : (UTIL_BANDS.find(([a, b]) => u >= a && u <= b)?.[2] ?? 'none'))

// per-gate rollup from the hourly occupancy rows + live flights at that gate
function rollup(gates: Rows, flights: Rows) {
  const hours = Array.from(new Set(gates.map((r) => r.hour_start_utc))).sort()
  const lastHour = hours[hours.length - 1]
  const by = new Map<string, { gate: string; airport: string; id: string; occ: number; flights: number; peak: { h: string; m: number }; last: number }>()
  for (const r of gates) {
    const k = `${r.airport_iata} ${r.gate_id}`
    const g = by.get(k) || { gate: k, airport: r.airport_iata, id: r.gate_id, occ: 0, flights: 0, peak: { h: '', m: -1 }, last: 0 }
    g.occ += r.occupied_minutes || 0
    g.flights += r.flights || 0
    if ((r.occupied_minutes || 0) > g.peak.m) g.peak = { h: r.hour_start_utc, m: r.occupied_minutes }
    if (r.hour_start_utc === lastHour) g.last = r.occupied_minutes || 0
    by.set(k, g)
  }
  const atGate = (g: { airport: string; id: string }) => ({
    // departures leave from gate_dep at origin; arrivals block in at gate_arr at destination
    next: flights.filter((f) => f.origin_iata === g.airport && f.gate_dep === g.id && (f.phase === 'SCHEDULED' || f.phase === 'BOARDING'))
      .sort((a, b) => a.std_utc.localeCompare(b.std_utc)),
    recent: flights.filter((f) => (f.origin_iata === g.airport && f.gate_dep === g.id && f.aobt) || (f.destination_iata === g.airport && f.gate_arr === g.id && f.aibt))
      .sort((a, b) => (b.aibt || b.aobt).localeCompare(a.aibt || a.aobt)),
  })
  const list = Array.from(by.values()).sort((a, b) => a.gate.localeCompare(b.gate))
    .map((g) => ({ ...g, util: hours.length ? g.occ / (hours.length * 60) : null, ...atGate(g) }))
  // hour with the most occupied minutes across all gates
  const perHour = hours.map((h) => ({ h, m: gates.filter((r) => r.hour_start_utc === h).reduce((a, r) => a + (r.occupied_minutes || 0), 0) }))
  const peak = perHour.reduce((a, b) => (b.m > a.m ? b : a), { h: '', m: -1 })
  return { list, hours, peak, lastHour }
}

export default function Gates({ ops }: { ops: Ops }) {
  const gates = ops.data.gates || []
  const flights = ops.data.flights || []
  const [sel, setSel] = useState<string | undefined>()
  const r = useMemo(() => rollup(gates, flights), [gates, flights])
  const g = gateUtil(gates)
  const inUse = r.list.filter((x) => x.last > 0).length
  const busiest = r.list.reduce<(typeof r.list)[number] | null>((a, b) => (!a || b.occ > a.occ ? b : a), null)
  const selected = r.list.find((x) => x.gate === sel)
  const loading = ops.loading, error = ops.errors.gates

  return (
    <>
      <PageHeader title="Gates" subtitle={`Gate occupancy at the hub · last ${ops.hours} h · UTC · click a gate for detail`} />
      <section aria-label="Gate KPIs" className="grid grid-cols-2 xl:grid-cols-4 gap-3 mb-5">
        <Tile label="Gate utilisation" value={pct(g?.util)} status={utilStatus(g?.util ?? null)} statusLabel={{ good: 'Normal', warning: 'Busy', critical: 'Congested', none: '' }[utilStatus(g?.util ?? null)]} sub="occupied / available min" />
        <Tile label="Gates in use" value={loading ? '–' : `${inUse} / ${r.list.length}`} sub={r.lastHour ? `hour ${hhmm(r.lastHour)}Z` : ''} />
        <Tile label="Peak hour" value={r.peak.h ? `${hhmm(r.peak.h)}Z` : '–'} sub={r.peak.h && r.list.length ? `${pct(r.peak.m / (r.list.length * 60))} of gate minutes` : ''} />
        <Tile label="Busiest gate" value={busiest ? busiest.gate : '–'} sub={busiest ? `${pct(busiest.util)} · ${busiest.flights} flight-hours` : ''} />
      </section>

      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_340px] gap-5 mb-5">
        <Heatmap rows={gates} error={error} loading={loading} selected={sel} onSelect={(x) => setSel(sel === x ? undefined : x)} maxH={520} />
        <GatePanel g={selected} onClose={() => setSel(undefined)} />
      </div>

      <Card title="All gates" subtitle="Status from the latest hour · utilisation over the window" error={error} loading={loading} empty={!r.list.length}>
        <div className="overflow-x-auto -mx-5">
          <table className="w-full text-sm tabular-nums">
            <thead className="bg-slate-50 text-xs text-slate-500">
              <tr>
                {['Gate', 'Status', 'Utilisation', 'Occupied', 'Flight-hours', 'Peak hour', 'Next departure'].map((h, i) => (
                  <th key={h} className={`py-2.5 px-5 font-medium ${i >= 2 && i <= 4 ? 'text-right' : 'text-left'}`}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {r.list.map((x) => {
                const s = utilStatus(x.util)
                const boarding = x.next.find((f) => f.phase === 'BOARDING')
                return (
                  <tr key={x.gate} onClick={() => { setSel(x.gate); document.getElementById('gate-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }} className={`border-t border-slate-100 cursor-pointer hover:bg-blue-50/50 ${sel === x.gate ? 'bg-blue-50' : ''}`}>
                    <td className="py-2.5 px-5 font-semibold">{x.gate}</td>
                    <td className="px-5">{boarding ? <Pill tone="info">Boarding AL{boarding.flight_number}</Pill> : x.last > 0 ? <Pill tone="warning">Occupied</Pill> : <Pill tone="good">Free</Pill>}</td>
                    <td className="px-5 text-right"><span className="inline-flex items-center gap-2 justify-end">
                      <span className="w-20 h-1.5 bg-slate-100 rounded-full overflow-hidden"><span className="block h-full" style={{ width: pct(x.util), background: s === 'none' ? '#94a3b8' : C[s] }} /></span>
                      {pct(x.util)}</span></td>
                    <td className="px-5 text-right">{x.occ} min</td>
                    <td className="px-5 text-right">{x.flights}</td>
                    <td className="px-5">{x.peak.h ? `${hhmm(x.peak.h)}Z · ${x.peak.m} min` : '–'}</td>
                    <td className="px-5">{x.next[0] ? <button onClick={(e) => { e.stopPropagation(); go('flights', x.next[0].flight_id) }} className="text-blue-700 hover:underline">AL{x.next[0].flight_number} · {hhmm(x.next[0].std_utc)}Z</button> : <span className="text-slate-400">–</span>}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <div className="flex gap-4 text-xs text-slate-500 mt-3">
          <span className="inline-flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-[#0ca30c]" />&lt; 75% normal</span>
          <span className="inline-flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-[#fab219]" />75–90% busy</span>
          <span className="inline-flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-[#d03b3b]" />≥ 90% congested</span>
        </div>
      </Card>
    </>
  )
}

function GatePanel({ g, onClose }: { g?: ReturnType<typeof rollup>['list'][number]; onClose: () => void }) {
  if (!g) {
    return (
      <section id="gate-panel" className="scroll-mt-20 bg-white rounded-lg border border-dashed border-slate-300 p-5 flex flex-col items-center justify-center text-center text-sm text-slate-500">
        <Icon name="gate" className="w-8 h-8 text-slate-300 mb-2" />
        Select a gate in the heatmap or the table to see its flights.
      </section>
    )
  }
  const s = utilStatus(g.util)
  return (
    <section id="gate-panel" className="scroll-mt-20 bg-white rounded-lg border border-slate-200 p-5 flex flex-col" aria-label={`Gate ${g.gate}`}>
      <div className="flex items-start justify-between mb-2">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">Gate {g.gate}</h2>
          <p className="text-xs text-slate-500">{g.occ} occupied min · {g.flights} flight-hours · window</p>
        </div>
        <button onClick={onClose} aria-label="Close" className="text-slate-400 hover:text-slate-700 text-xl leading-none">×</button>
      </div>
      <Gauge label="Utilisation" value={g.util} bands={UTIL_BANDS} caption={{ good: 'Normal', warning: 'Busy', critical: 'Congested', none: '' }[s]} />
      <FlightList title="Next departures" rows={g.next.slice(0, 4)} time={(f) => `STD ${hhmm(f.std_utc)}Z`} empty="No scheduled departures" />
      <FlightList title="Recent movements" rows={g.recent.slice(0, 4)} time={(f) => (f.aibt && f.gate_arr ? `in ${hhmm(f.aibt)}Z` : `off ${hhmm(f.aobt)}Z`)} empty="No movements in the window" />
    </section>
  )
}

function FlightList({ title, rows, time, empty }: { title: string; rows: Rows; time: (f: Record<string, any>) => string; empty: string }) {
  return (
    <div className="mt-4">
      <div className="text-xs font-semibold text-slate-600 uppercase tracking-wide mb-1">{title}</div>
      {!rows.length ? <div className="text-xs text-slate-400 py-1">{empty}</div> : (
        <ul className="divide-y divide-slate-100">
          {rows.map((f) => (
            <li key={f.flight_id}>
              <button onClick={() => go('flights', f.flight_id)} className="w-full flex items-center justify-between gap-2 py-1.5 text-xs hover:bg-slate-50 rounded tabular-nums">
                <span className="font-semibold text-sm text-slate-900">AL{f.flight_number}</span>
                <span className="text-slate-500">{f.origin_iata} → {f.destination_iata}</span>
                <span className="text-slate-500">{time(f)}</span>
                <DelayPill f={f} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
