import { ReactNode } from 'react'
import { PageHeader } from '../components/Shell'
import { Card, DelayPill, Icon, PhaseBadge, Pill, hhmm } from '../components/ui'
import { Ops } from '../useOps'

const label = (p: string) => p.charAt(0) + p.slice(1).toLowerCase().replace('_', '-')
const t = (iso?: string | null) => (iso ? `${hhmm(iso)}Z` : '–')
const mins = (m?: number | null, signed = false) => (m == null ? '–' : `${signed && m > 0 ? '+' : ''}${m} min`)

export default function FlightDetail({ ops, id }: { ops: Ops; id: string }) {
  const f = (ops.data.flights || []).find((x) => x.flight_id === id)
  if (ops.loading) return <Card title="Flight" loading>{null}</Card>
  if (!f) {
    return (
      <>
        <PageHeader title="Flight not found" back="flights" />
        <Card title="Not in the current window">
          <p className="text-sm text-slate-600">This flight is outside the last 24 h + next 6 h window, or outside the airports your role can see.</p>
        </Card>
      </>
    )
  }

  // planned vs actual milestones; boarding planned 40 min before STD (simulator rule)
  const steps: { name: string; plan?: string; actual?: string | null }[] = [
    { name: 'Boarding', plan: f.std_utc && new Date(new Date(f.std_utc).getTime() - 40 * 60e3).toISOString(), actual: f.boarding_start_ts },
    { name: 'Off-block', plan: f.std_utc, actual: f.aobt },
    { name: 'Take-off', actual: f.atot },
    { name: 'Landing', actual: f.aldt },
    { name: 'In-block', plan: f.sta_utc, actual: f.aibt },
  ]
  const last = steps.reduce((i, s, n) => (s.actual ? n : i), -1)

  return (
    <>
      <PageHeader back="flights"
        title={<span className="inline-flex items-center gap-3">Flight AL{f.flight_number}<PhaseBadge phase={f.phase} /><DelayPill f={f} /></span>}
        subtitle={`${f.origin_iata} → ${f.destination_iata} · STD ${t(f.std_utc)} · last event ${t(f.last_event_ts)}`} />

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
        <Card title="Milestones" subtitle="Planned vs actual · UTC" className="xl:col-span-2">
          <ol className="grid grid-cols-5 gap-2" aria-label="Milestones">
            {steps.map((s, n) => {
              const done = !!s.actual
              const late = done && s.plan && new Date(s.actual!).getTime() - new Date(s.plan).getTime() >= 15 * 60e3
              return (
                <li key={s.name} className="flex flex-col items-center text-center">
                  <div className="flex items-center w-full">
                    <span className={`flex-1 h-0.5 ${n === 0 ? 'invisible' : n <= last ? 'bg-blue-600' : 'bg-slate-200'}`} />
                    <span className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-semibold border-2 ${done ? (late ? 'bg-red-600 border-red-600 text-white' : 'bg-blue-600 border-blue-600 text-white') : 'bg-white border-slate-300 text-slate-400'}`}>
                      {done ? '✓' : n + 1}
                    </span>
                    <span className={`flex-1 h-0.5 ${n === steps.length - 1 ? 'invisible' : n < last ? 'bg-blue-600' : 'bg-slate-200'}`} />
                  </div>
                  <div className="text-sm font-medium text-slate-800 mt-2">{s.name}</div>
                  <div className="text-xs text-slate-500 tabular-nums">{s.plan ? `plan ${t(s.plan)}` : 'no plan'}</div>
                  <div className={`text-xs font-medium tabular-nums ${late ? 'text-red-700' : done ? 'text-slate-900' : 'text-slate-400'}`}>{done ? t(s.actual) : 'pending'}</div>
                </li>
              )
            })}
          </ol>
        </Card>

        <Card title="Punctuality">
          <Fields rows={[
            ['Departure delay', <DelayPill f={f} />],
            ['Arrival delay', mins(f.arr_delay_min, true)],
            ['Delay cause', f.delay_cause ? <Pill tone="warning">{label(f.delay_cause)}</Pill> : '–'],
            ['Scheduled departure', t(f.std_utc)],
            ['Scheduled arrival', t(f.sta_utc)],
          ]} />
        </Card>

        <Card title="Aircraft and turnaround">
          <Fields rows={[
            ['Tail number', f.tail_number || '–'],
            ['Turnaround', mins(f.turnaround_min)],
            ['Turnaround within target', f.turnaround_ok == null ? '–' : f.turnaround_ok ? <Pill tone="good">Yes</Pill> : <Pill tone="critical">No</Pill>],
          ]} />
        </Card>

        <Card title="Gates and taxi">
          <Fields rows={[
            ['Departure gate', f.gate_dep ? <span className="inline-flex items-center gap-1"><Icon name="gate" className="w-3.5 h-3.5" />{f.gate_dep}</span> : '–'],
            ['Arrival gate', f.gate_arr ? <span className="inline-flex items-center gap-1"><Icon name="gate" className="w-3.5 h-3.5" />{f.gate_arr}</span> : '–'],
            ['Taxi-out', mins(f.taxi_out_min)],
            ['Taxi-in', mins(f.taxi_in_min)],
          ]} />
        </Card>

        <Card title="Record">
          <Fields rows={[
            ['Flight id', <code className="text-xs">{f.flight_id}</code>],
            ['Last operational event', t(f.last_event_ts)],
            ['Source', 'Lakebase · ops.flight_state (refreshed by the gold pipeline)'],
          ]} />
        </Card>
      </div>
    </>
  )
}

function Fields({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="divide-y divide-slate-100 text-sm">
      {rows.map(([k, v]) => (
        <div key={k} className="flex items-center justify-between py-2 gap-4">
          <dt className="text-slate-500">{k}</dt>
          <dd className="text-slate-900 font-medium text-right tabular-nums">{v}</dd>
        </div>
      ))}
    </dl>
  )
}
