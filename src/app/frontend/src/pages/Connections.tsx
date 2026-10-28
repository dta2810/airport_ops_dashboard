import { useContext, useMemo } from 'react'
import { PageHeader } from '../components/Shell'
import { Connections as Chart } from '../components/charts'
import { Card, DelayPill, PhaseBadge, Pill, hhmm, pct } from '../components/ui'
import { HoursCtx, Ops, RoleInfo, Rows, go } from '../useOps'

const HUB = 'PTY'

export default function Connections({ info, ops }: { info: RoleInfo; ops: Ops }) {
  const rows = ops.data.connections
  const flights = ops.data.flights || []
  const hours = useContext(HoursCtx)
  const byHour = useMemo(() => [...(rows || [])].sort((a, b) => b.hour_start_utc.localeCompare(a.hour_start_utc)), [rows])
  // inbound delays are what put connecting passengers at risk
  const inbound = useMemo(() => flights.filter((f) => f.destination_iata === HUB && f.origin_iata !== HUB
    && ((f.dep_delay_min ?? 0) >= 15 || (f.arr_delay_min ?? 0) >= 15) && (f.phase !== 'AT_GATE' || recent(f.aibt, flights)))
    .sort((a, b) => a.sta_utc.localeCompare(b.sta_utc)), [flights])

  return (
    <>
      <PageHeader title="Hub connections" subtitle={`${HUB} · connecting passengers by outbound hour · last ${hours} h · UTC`} />
      <div className="space-y-5">
        <Chart title="Protected, at risk and missed" rows={rows} error={ops.errors.connections} loading={ops.loading} />
        <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] gap-5">
          <Card title="By outbound hour" subtitle="Newest first · at risk: inbound ready + min connection time is after the outbound departure · missed: outbound left first" error={ops.errors.connections} loading={ops.loading} empty={!byHour.length}>
            <div className="overflow-auto max-h-[460px] -mx-5">
              <table className="w-full text-sm tabular-nums">
                <thead className="bg-slate-50 text-xs text-slate-500 sticky top-0">
                  <tr>
                    <th className="text-left py-2.5 px-5 font-medium">Hour</th>
                    <th className="text-right px-3 font-medium">Connections</th>
                    <th className="text-right px-3 font-medium">Pax</th>
                    <th className="text-right px-3 font-medium">At risk</th>
                    <th className="text-right px-3 font-medium">Missed</th>
                    <th className="text-right px-5 font-medium">Protected</th>
                  </tr>
                </thead>
                <tbody>
                  {byHour.map((r) => {
                    const prot = r.pax_connecting ? 1 - (r.pax_at_risk + r.pax_missed) / r.pax_connecting : null
                    return (
                      <tr key={r.hub_iata + r.hour_start_utc} className="border-t border-slate-100">
                        <td className="py-2 px-5 font-medium">{hhmm(r.hour_start_utc)}Z</td>
                        <td className="text-right px-3">{r.connections}</td>
                        <td className="text-right px-3">{r.pax_connecting.toLocaleString()}</td>
                        <td className="text-right px-3">{r.pax_at_risk ? <Pill tone="warning">{r.pax_at_risk} pax · {r.connections_at_risk}</Pill> : <span className="text-slate-400">0</span>}</td>
                        <td className="text-right px-3">{r.pax_missed ? <Pill tone="critical">{r.pax_missed} pax · {r.connections_missed}</Pill> : <span className="text-slate-400">0</span>}</td>
                        <td className={`text-right px-5 font-medium ${prot != null && prot < 0.95 ? 'text-amber-800' : 'text-green-800'}`}>{pct(prot)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </Card>
          <InboundList rows={inbound} loading={ops.loading} error={ops.errors.flights} show={info.sections.includes('flights')} />
        </div>
      </div>
    </>
  )
}

// arrived within 2 h of the latest event in the data
function recent(iso: string | null, flights: Rows) {
  if (!iso) return false
  const now = flights.reduce((m, f) => (f.last_event_ts && f.last_event_ts > m ? f.last_event_ts : m), '')
  return new Date(iso).getTime() >= (now ? new Date(now).getTime() : Date.now()) - 2 * 3600e3
}

function InboundList({ rows, loading, error, show }: { rows: Rows; loading: boolean; error?: string; show: boolean }) {
  if (!show) return null
  return (
    <Card title="Late inbound flights to the hub" subtitle="Delayed 15 min or more · en route or arrived in the last 2 h · click for detail" loading={loading} error={error} empty={false}>
      {!rows.length ? <div className="text-sm text-slate-500">No late inbound flights right now.</div> : (
        <ul className="divide-y divide-slate-100 -mx-2 overflow-auto max-h-[460px]">
          {rows.map((f) => (
            <li key={f.flight_id}>
              <button onClick={() => go('flights', f.flight_id)} className="w-full grid grid-cols-[4.5rem_1fr_auto_auto] items-center gap-3 px-2 py-2 text-left text-xs hover:bg-slate-50 rounded tabular-nums">
                <span className="font-semibold text-sm">AL{f.flight_number}</span>
                <span className="text-slate-600">{f.origin_iata} → {f.destination_iata} · STA {hhmm(f.sta_utc)}Z</span>
                <PhaseBadge phase={f.phase} />
                <span className="justify-self-end">{f.arr_delay_min != null ? <Pill tone={f.arr_delay_min >= 15 ? 'critical' : 'warning'}>arr +{f.arr_delay_min} min</Pill> : <DelayPill f={f} />}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}
