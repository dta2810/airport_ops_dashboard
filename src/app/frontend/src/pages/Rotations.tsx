import { Fragment, useMemo, useState } from 'react'
import { PageHeader } from '../components/Shell'
import { C, Card, Legend, Tile, delayStatus, hhmm } from '../components/ui'
import { Ops, Rows, go, opsNow } from '../useOps'

const MIN = 60e3, H = 60 * MIN
const WIN_BACK = 24 * H, WIN_AHEAD = 6 * H, SPAN = WIN_BACK + WIN_AHEAD
const TICK = 3 * H
const LANE_H = 22, BAR_H = 16, GAP_H = 7, LABEL_W = 136, TRACK_MIN = 1000
const EXC = ['CANCELLED', 'DIVERTED']
const GROUND = '#94a3b8'

type Bucket = 'good' | 'warning' | 'critical' | 'exc' | 'pending'
const FILL: Record<Bucket, string> = {
  good: C.good,
  warning: C.warning,
  critical: C.critical,
  exc: `repeating-linear-gradient(45deg, ${C.critical} 0 3px, #f4b4b4 3px 6px)`,
  pending: C.plan,
}
const TEXT: Record<Bucket, string> = { good: '#fff', warning: '#0f172a', critical: '#fff', exc: '#7f1d1d', pending: '#0f172a' }
const BUCKET_TEXT: Record<Bucket, string> = { good: 'On time', warning: 'Delayed < 15 min', critical: 'Delayed ≥ 15 min', exc: 'Cancelled / diverted', pending: 'Scheduled, not departed' }

type Item = { f: Record<string, any>; ps: number; pe: number; s: number; e: number; ghost: boolean; prop: boolean; b: Bucket; lane: number }
type Gap = { a: number; b: number; min: number; short: boolean }
type Tail = { tail: string; items: Item[]; gaps: Gap[]; lanes: number; delayed: boolean }

const at = (iso?: string | null) => (iso ? Date.parse(iso) : NaN)
const label = (p: string) => p.charAt(0) + p.slice(1).toLowerCase().replace('_', '-')
const bucket = (f: Record<string, any>): Bucket =>
  EXC.includes(f.phase) ? 'exc' : f.dep_delay_min == null ? 'pending' : (delayStatus(f.dep_delay_min) as Bucket)
const hm = (t: number) => `${hhmm(new Date(t).toISOString())}Z`
const mins = (m?: number | null) => (m == null ? '–' : `${m > 0 ? '+' : ''}${m} min`)

// group the full feed by tail first so propagation sees the previous flight even if it starts before the window
function build(rows: Rows, t0: number, t1: number) {
  const byTail = new Map<string, Rows>()
  for (const f of rows) {
    if (!f.tail_number || Number.isNaN(Date.parse(f.std_utc))) continue
    byTail.set(f.tail_number, [...(byTail.get(f.tail_number) || []), f])
  }
  const tails: Tail[] = []
  for (const [tail, fs] of Array.from(byTail.entries()).sort(([a], [b]) => a.localeCompare(b))) {
    fs.sort((a, b) => a.std_utc.localeCompare(b.std_utc))
    const items: Item[] = []
    fs.forEach((f, i) => {
      const ps = Date.parse(f.std_utc), pe = at(f.sta_utc) || ps + H
      const a0 = at(f.aobt), a1 = at(f.aibt)
      const s = a0 || ps
      let e = a1 || pe
      if (!(e > s)) e = s + 10 * MIN
      const prev = fs[i - 1]
      const prop = (f.dep_delay_min ?? -1) >= 15 && (prev?.arr_delay_min ?? -1) >= 15
      items.push({ f, ps, pe, s, e, ghost: (!!f.aobt && s !== ps) || (!!f.aibt && e !== pe), prop, b: bucket(f), lane: 0 })
    })
    // keep only bars that overlap the window
    const vis = items.filter((it) => it.s <= t1 && it.e >= t0)
    if (!vis.length) continue
    // greedy lane packing so overlapping plan/actual bars stay readable
    vis.sort((a, b) => a.s - b.s)
    const ends: number[] = []
    for (const it of vis) {
      let l = ends.findIndex((x) => x <= it.s)
      if (l < 0) { l = ends.length; ends.push(it.e) } else ends[l] = it.e
      it.lane = l
    }
    // ground gap = time between one bar ending and the next starting
    const gaps: Gap[] = []
    let maxE = -Infinity
    for (const it of vis) {
      if (maxE > -Infinity && it.s > maxE) {
        const min = Math.round((it.s - maxE) / MIN)
        const short = it.f.turnaround_ok === false
        if (short || min <= 240) gaps.push({ a: maxE, b: it.s, min, short })
      }
      maxE = Math.max(maxE, it.e)
    }
    const delayed = vis.some((it) => (it.f.dep_delay_min ?? 0) >= 15 || (it.f.arr_delay_min ?? 0) >= 15)
    tails.push({ tail, items: vis, gaps, lanes: ends.length, delayed })
  }
  return tails
}

function tooltip(f: Record<string, any>) {
  const cause = f.delay_cause ? label(f.delay_cause) : '–'
  return [
    `AL${f.flight_number} ${f.origin_iata} → ${f.destination_iata} · ${f.tail_number}`,
    `STD ${f.std_utc ? hhmm(f.std_utc) : '–'}Z · STA ${f.sta_utc ? hhmm(f.sta_utc) : '–'}Z`,
    `Off-block ${f.aobt ? hhmm(f.aobt) + 'Z' : '–'} · In-block ${f.aibt ? hhmm(f.aibt) + 'Z' : '–'}`,
    `Dep ${mins(f.dep_delay_min)} · Arr ${mins(f.arr_delay_min)} · Cause ${cause}`,
    `Phase ${label(f.phase)}`,
  ].join('\n')
}

export default function Rotations({ ops }: { ops: Ops }) {
  const rows = ops.data.flights || []
  const loading = ops.loading, error = ops.errors.flights
  const [onlyDelayed, setOnlyDelayed] = useState(false)
  const [q, setQ] = useState('')

  const now = useMemo(() => opsNow(rows), [rows])
  const t0 = now - WIN_BACK, t1 = now + WIN_AHEAD
  const tails = useMemo(() => build(rows, t0, t1), [rows, t0, t1])

  const all = tails.flatMap((t) => t.items)
  const aircraft = tails.length
  const short = all.filter((it) => it.f.turnaround_ok === false).length
  const propagated = all.filter((it) => it.prop).length
  const turns = all.map((it) => it.f.turnaround_min).filter((m): m is number => typeof m === 'number')
  const avgTurn = turns.length ? turns.reduce((a, b) => a + b, 0) / turns.length : null

  const needle = q.trim().toUpperCase()
  const shown = tails.filter((t) => (!onlyDelayed || t.delayed) && (!needle || t.tail.toUpperCase().includes(needle)))
  const shownFlights = shown.reduce((a, t) => a + t.items.length, 0)

  const pct = (t: number) => Math.min(100, Math.max(0, ((t - t0) / SPAN) * 100))
  const ticks: number[] = []
  for (let t = Math.ceil(t0 / TICK) * TICK; t <= t1; t += TICK) ticks.push(t)
  const nowPct = pct(now)
  const ready = !loading && !error

  return (
    <>
      <PageHeader title="Rotations" subtitle="Aircraft rotations · last 24 h + next 6 h · UTC" />

      <section aria-label="Rotation KPIs" className="grid grid-cols-2 xl:grid-cols-4 gap-3 mb-5">
        <Tile label="Aircraft flying today" value={ready ? String(aircraft) : '–'} sub="distinct tails" />
        <Tile label="Short turnarounds" value={ready ? String(short) : '–'} status={short ? 'warning' : 'none'} statusLabel={short ? 'Watch' : undefined} sub="turnaround target missed" />
        <Tile label="Propagated delays" value={ready ? String(propagated) : '–'} status={propagated ? 'critical' : 'none'} statusLabel={propagated ? 'Action' : undefined} sub="late departure after a late arrival" />
        <Tile label="Avg turnaround" value={ready && avgTurn != null ? String(Math.round(avgTurn)) : '–'} unit={ready && avgTurn != null ? 'min' : undefined} />
      </section>

      <section className="bg-white border border-slate-200 rounded-lg p-4 mb-4 flex flex-wrap items-end gap-4 text-sm" aria-label="Filters">
        <label className="flex items-center gap-2 py-2 cursor-pointer select-none">
          <input type="checkbox" checked={onlyDelayed} onChange={(e) => setOnlyDelayed(e.target.checked)} className="w-4 h-4 accent-blue-600" />
          <span className="text-slate-800">Only tails with delays ≥ 15 min</span>
        </label>
        <label className="flex flex-col gap-1 w-full sm:w-72">
          <span className="text-xs font-medium text-slate-600">Tail</span>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. 1835" aria-label="Search tails"
            className="border border-slate-300 rounded-md px-2 py-2" />
        </label>
      </section>

      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 mb-4 text-sm text-slate-600" data-testid="summary">
        <span className="text-2xl font-semibold text-slate-900">{shown.length}</span> tails
        <span className="text-slate-300">·</span><span className="font-semibold text-slate-900">{shownFlights}</span> flights
      </div>

      <Card title="Rotation timeline" subtitle="One row per tail · bar = off-block → in-block (scheduled if not yet actual) · dashed outline = scheduled window · navy ring = propagated delay"
        error={error} loading={loading} empty={!loading && !error && !rows.length}>
        <Legend items={[
          { label: BUCKET_TEXT.good, color: FILL.good },
          { label: BUCKET_TEXT.warning, color: FILL.warning },
          { label: BUCKET_TEXT.critical, color: FILL.critical },
          { label: BUCKET_TEXT.exc, color: FILL.exc },
          { label: BUCKET_TEXT.pending, color: C.plan, hollow: true },
          { label: 'Ground time (red = short turnaround)', color: GROUND },
        ]} />

        {!shown.length ? <p className="text-sm text-slate-500 py-6 text-center">No tails match these filters.</p> : (
          <div className="overflow-auto max-h-[640px] border border-slate-200 rounded-md" data-testid="gantt">
            <div style={{ minWidth: LABEL_W + TRACK_MIN }}>
              <div className="flex sticky top-0 z-20 bg-white border-b border-slate-200 text-[11px] text-slate-500 h-7">
                <div className="shrink-0 sticky left-0 z-30 bg-white border-r border-slate-100" style={{ width: LABEL_W }} />
                <div className="relative flex-1">
                  {ticks.filter((t) => Math.abs(t - now) >= 45 * MIN).map((t) => (
                    <span key={t} className={`absolute top-1.5 tabular-nums ${pct(t) > 95 ? '-translate-x-full' : '-translate-x-1/2'}`} style={{ left: `${pct(t)}%` }}>{hm(t)}</span>
                  ))}
                  <span className="absolute top-1.5 -translate-x-1/2 font-semibold text-blue-700 tabular-nums" style={{ left: `${nowPct}%` }}>now</span>
                </div>
              </div>

              {shown.map((t) => (
                <div key={t.tail} className="flex border-t border-slate-100" style={{ height: t.lanes * LANE_H + GAP_H }} data-tail={t.tail}>
                  <div className="shrink-0 sticky left-0 z-10 bg-white border-r border-slate-100 px-3 flex flex-col justify-center text-xs" style={{ width: LABEL_W }}>
                    <span className="font-semibold text-slate-900 tabular-nums">{t.tail}</span>
                    <span className="text-slate-500">{t.items.length} flights{t.delayed && <span className="text-red-700"> · delayed</span>}</span>
                  </div>
                  <div className="relative flex-1">
                    {ticks.map((tk) => <div key={tk} className="absolute top-0 bottom-0 w-px bg-slate-100" style={{ left: `${pct(tk)}%` }} />)}
                    <div className="absolute top-0 bottom-0 w-px bg-blue-600 z-10 opacity-70" style={{ left: `${nowPct}%` }} />

                    {t.items.map((it) => {
                      const top = it.lane * LANE_H + (LANE_H - BAR_H) / 2
                      const flight = `AL${it.f.flight_number}`
                      return (
                        <Fragment key={it.f.flight_id}>
                        {it.ghost && (
                          <span className="absolute rounded-sm border border-dashed border-slate-400 pointer-events-none"
                            style={{ left: `${pct(it.ps)}%`, width: `${pct(it.pe) - pct(it.ps)}%`, top: it.lane * LANE_H + 2, height: LANE_H - 4 }} />
                        )}
                        <button onClick={() => go('flights', it.f.flight_id)} title={tooltip(it.f)} aria-label={`${flight} ${it.f.origin_iata} to ${it.f.destination_iata}`}
                          className="absolute rounded-sm overflow-hidden text-left text-[10px] font-semibold leading-none flex items-center px-1 hover:ring-2 hover:ring-blue-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 z-[1]"
                          style={{
                            left: `${pct(it.s)}%`, width: `${Math.max(0.2, pct(it.e) - pct(it.s))}%`, top, height: BAR_H, minWidth: 4,
                            background: FILL[it.b], color: TEXT[it.b],
                            border: it.b === 'pending' ? `1px solid ${C.planStroke}` : undefined,
                            boxShadow: it.prop ? `0 0 0 2px ${C.navy}` : undefined,
                          }}>
                          <span className="truncate">{flight}</span>
                        </button>
                        </Fragment>
                      )
                    })}

                    {t.gaps.map((g) => (
                      <span key={`${g.a}-${g.b}`} title={`Ground ${g.min} min${g.short ? ' · turnaround target missed' : ''}`}
                        className="absolute rounded-full z-[1]"
                        style={{ left: `${pct(g.a)}%`, width: `${Math.max(0.2, pct(g.b) - pct(g.a))}%`, minWidth: 3, top: t.lanes * LANE_H + 2, height: 3, background: g.short ? C.critical : GROUND }} />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </Card>
    </>
  )
}
