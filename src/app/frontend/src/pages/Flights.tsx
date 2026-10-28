import { ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import { PageHeader } from '../components/Shell'
import { Card, DelayPill, Icon, PhaseBadge, Pill, delayStatus, hhmm, num } from '../components/ui'
import { Ops, Rows, go, opsNow, query, setQuery } from '../useOps'

const BOARD = ['SCHEDULED', 'BOARDING', 'TAXI_OUT', 'AIRBORNE', 'TAXI_IN', 'AT_GATE']
const EXCEPTIONS = ['CANCELLED', 'DIVERTED']
const COL_LIMIT = 25
const PAGE = 25
const colName = (p: string) => (p === 'EXCEPTIONS' ? 'Cancelled / diverted' : label(p))
const label = (p: string) => p.charAt(0) + p.slice(1).toLowerCase().replace('_', '-')

type Filters = { window: 'active' | 'all'; status: string; cause: string; phase: string; q: string; route: string; min: string; hour: string; dir: string }
const KEYS: (keyof Filters)[] = ['window', 'status', 'cause', 'phase', 'q', 'route', 'min', 'hour', 'dir']
const fromUrl = (): Filters => {
  const q = query()
  const f = Object.fromEntries(KEYS.map((k) => [k, (q.get(k) || '').slice(0, 80)])) as Filters
  // shared links are untrusted: drop values the filters cannot use
  const ok = (v: string, allowed: string[]) => (allowed.includes(v) ? v : '')
  return {
    ...f, window: f.window === 'all' ? 'all' : 'active',
    status: ok(f.status, ['good', 'warning', 'critical', 'disrupted', 'pending']),
    min: ok(f.min, ['15', '30', '60']), dir: ok(f.dir, ['dep', 'arr']),
    hour: Number.isFinite(Date.parse(f.hour)) ? f.hour : '',
    route: /^[A-Z]{3}-[A-Z]{3}$/.test(f.route) ? f.route : '',
  }
}
const hourOf = (iso?: string) => (iso ? iso.slice(0, 13) : '')
type SortKey = 'std_utc' | 'dep_delay_min' | 'flight_number'

function matches(f: Record<string, any>, flt: Filters, now: number) {
  if (flt.window === 'active') {
    const recent = (iso?: string, h = 2) => !!iso && new Date(iso).getTime() >= now - h * 3600e3
    if (f.phase === 'AT_GATE' && !recent(f.aibt)) return false
    if (EXCEPTIONS.includes(f.phase) && !recent(f.std_utc, 6)) return false
  }
  if (flt.phase && f.phase !== flt.phase) return false
  if (flt.route && `${f.origin_iata}-${f.destination_iata}` !== flt.route) return false
  if (flt.min && !((f.dep_delay_min ?? -1) >= Number(flt.min))) return false
  if (flt.hour && hourOf(flt.dir === 'arr' ? f.sta_utc : f.std_utc) !== hourOf(flt.hour)) return false
  if (flt.cause && f.delay_cause !== flt.cause) return false
  if (flt.status) {
    const s = f.dep_delay_min == null ? 'pending' : delayStatus(f.dep_delay_min)
    if (flt.status === 'disrupted' ? !(s === 'critical' || EXCEPTIONS.includes(f.phase)) : s !== flt.status) return false
  }
  if (flt.q) {
    const q = flt.q.trim().toUpperCase()
    const hay = [`AL${f.flight_number}`, f.tail_number, f.gate_dep, f.gate_arr, f.origin_iata, f.destination_iata].filter(Boolean).join(' ').toUpperCase()
    if (!hay.includes(q)) return false
  }
  return true
}

export default function Flights({ ops }: { ops: Ops }) {
  const rows = ops.data.flights
  const [view, setView] = useState<'kanban' | 'list'>('kanban')
  const [flt, setFlt] = useState<Filters>(fromUrl)
  const set = (k: keyof Filters) => (v: string) => setFlt((p) => ({ ...p, [k]: v }))
  // filters live in the URL so a view can be shared
  useEffect(() => { setQuery({ ...Object.fromEntries(KEYS.map((k) => [k, flt[k]])), window: flt.window === 'all' ? 'all' : '' }) }, [flt])
  const [copied, setCopied] = useState(false)
  const active = KEYS.filter((k) => k !== 'window' && k !== 'dir' && flt[k])

  // flights whose phase changed since the previous poll get highlighted once
  const prev = useRef<Map<string, string>>(new Map())
  const [moved, setMoved] = useState<Set<string>>(new Set())
  useEffect(() => {
    if (!rows) return
    const m = new Set<string>()
    for (const f of rows) {
      const p = prev.current.get(f.flight_id)
      if (p && p !== f.phase) m.add(f.flight_id)
    }
    prev.current = new Map(rows.map((f) => [f.flight_id, f.phase]))
    setMoved(m)
  }, [rows])

  const now = useMemo(() => opsNow(rows || []), [rows])
  const shown = useMemo(() => (rows || []).filter((f) => matches(f, flt, now)), [rows, flt, now])
  const causes = useMemo(() => Array.from(new Set((rows || []).map((f) => f.delay_cause).filter(Boolean))).sort(), [rows])
  const routes = useMemo(() => Array.from(new Set((rows || []).map((f) => `${f.origin_iata}-${f.destination_iata}`))).sort(), [rows])

  const departed = shown.filter((f) => f.dep_delay_min != null)
  const late = departed.filter((f) => f.dep_delay_min >= 15).length
  const avg = departed.length ? departed.reduce((a, f) => a + Math.max(0, f.dep_delay_min), 0) / departed.length : null
  const exc = shown.filter((f) => EXCEPTIONS.includes(f.phase)).length

  return (
    <>
      <PageHeader title="Flights" subtitle="Live flight board · phases update from operational events every refresh"
        actions={
          <div className="inline-flex rounded-md border border-slate-300 bg-white p-0.5" role="tablist" aria-label="View">
            {(['list', 'kanban'] as const).map((v) => (
              <button key={v} role="tab" aria-selected={view === v} onClick={() => setView(v)}
                className={`flex items-center gap-2 px-3 py-1.5 rounded text-sm ${view === v ? 'bg-blue-600 text-white' : 'text-slate-700 hover:bg-slate-100'}`}>
                <Icon name={v} />{v === 'list' ? 'List' : 'Board'}
              </button>
            ))}
          </div>
        } />

      <section className="bg-white border border-slate-200 rounded-lg p-4 mb-4 grid grid-cols-2 md:grid-cols-3 xl:grid-cols-[repeat(6,minmax(0,1fr))_minmax(0,1.6fr)] gap-3 text-sm" aria-label="Filters">
        <Select label="Window" value={flt.window} onChange={set('window')} options={[['active', 'Active + arrived last 2 h'], ['all', `Last ${ops.hours} h + next 6 h`]]} />
        <Select label="Departure status" value={flt.status} onChange={set('status')} options={[['', 'All'], ['good', 'On time'], ['warning', 'Delayed < 15 min'], ['critical', 'Delayed ≥ 15 min'], ['disrupted', 'Disrupted (≥ 15, cancelled, diverted)'], ['pending', 'Not departed']]} />
        <Select label="Delay cause" value={flt.cause} onChange={set('cause')} options={[['', 'All'], ...causes.map((c) => [c, label(c)] as [string, string])]} />
        <Select label="Route" value={flt.route} onChange={set('route')} options={[['', 'All'], ...routes.map((r) => [r, r.replace('-', ' → ')] as [string, string])]} />
        <Select label="Departure delay" value={flt.min} onChange={set('min')} options={[['', 'Any'], ['15', '≥ 15 min'], ['30', '≥ 30 min'], ['60', '≥ 60 min']]} />
        <Select label="Phase" value={flt.phase} onChange={set('phase')} disabled={view === 'kanban'} options={[['', view === 'kanban' ? 'All (board columns)' : 'All'], ...[...BOARD, ...EXCEPTIONS].map((p) => [p, label(p)] as [string, string])]} />
        <label className="flex flex-col gap-1 col-span-2 md:col-span-1">
          <span className="text-xs font-medium text-slate-600">Search</span>
          <span className="relative">
            <Icon name="search" className="w-4 h-4 absolute left-2.5 top-2.5 text-slate-400" />
            <input value={flt.q} onChange={(e) => set('q')(e.target.value)} placeholder="Flight, tail, gate, airport…" aria-label="Search flights"
              className="w-full border border-slate-300 rounded-md pl-8 pr-2 py-2" />
          </span>
        </label>
      </section>

      {(active.length > 0 || flt.window === 'all') && (
        <div className="flex flex-wrap items-center gap-2 mb-3 text-xs" data-testid="active-filters">
          {flt.hour && <Chip onClear={() => setFlt((p) => ({ ...p, hour: '', dir: '' }))}>{flt.dir === 'arr' ? 'Arrivals' : 'Departures'} {hhmm(flt.hour)}–{hhmm(new Date(new Date(flt.hour).getTime() + 3600e3).toISOString())}Z</Chip>}
          {flt.cause && <Chip onClear={() => set('cause')('')}>Cause: {label(flt.cause)}</Chip>}
          {flt.route && <Chip onClear={() => set('route')('')}>Route: {flt.route.replace('-', ' → ')}</Chip>}
          {flt.min && <Chip onClear={() => set('min')('')}>Delay ≥ {flt.min} min</Chip>}
          {flt.status && <Chip onClear={() => set('status')('')}>Status: {flt.status}</Chip>}
          {flt.phase && <Chip onClear={() => set('phase')('')}>Phase: {label(flt.phase)}</Chip>}
          {flt.q && <Chip onClear={() => set('q')('')}>“{flt.q}”</Chip>}
          {active.length > 0 && <button onClick={() => setFlt((p) => ({ ...p, status: '', cause: '', phase: '', q: '', route: '', min: '', hour: '', dir: '' }))} className="text-blue-700 hover:underline">Clear filters</button>}
          <button onClick={() => navigator.clipboard?.writeText(window.location.href).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) })}
            className="ml-auto border border-slate-300 rounded px-2 py-1 hover:bg-slate-100">{copied ? 'Link copied' : 'Copy link to this view'}</button>
        </div>
      )}

      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 mb-4 text-sm text-slate-600" data-testid="summary">
        <span className="text-2xl font-semibold text-slate-900">{shown.length}</span> flights
        <span className="text-slate-300">·</span><span className="font-semibold text-red-700">{late}</span> departed ≥ 15 min late
        <span className="text-slate-300">·</span>avg departure delay <span className="font-semibold text-slate-900">{num(avg)} min</span>
        <span className="text-slate-300">·</span><span className="font-semibold text-slate-900">{exc}</span> cancelled / diverted
      </div>

      {ops.errors.flights ? <Card title="Flights" error={ops.errors.flights}>{null}</Card>
        : ops.loading ? <Card title="Flights" loading>{null}</Card>
          : view === 'kanban' ? <Board rows={shown} moved={moved} onMore={(p) => { setFlt((f) => ({ ...f, phase: p })); setView('list') }} />
            : <List rows={shown} moved={moved} />}
    </>
  )
}

function Chip({ children, onClear }: { children: ReactNode; onClear: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-blue-50 text-blue-800 border border-blue-200 pl-2.5 pr-1 py-0.5">
      {children}
      <button onClick={onClear} aria-label="Remove filter" className="rounded-full w-4 h-4 leading-none hover:bg-blue-100">×</button>
    </span>
  )
}

function Select({ label: l, value, onChange, options, disabled }: { label: string; value: string; onChange: (v: string) => void; options: [string, string][]; disabled?: boolean }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs font-medium text-slate-600">{l}</span>
      <select value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} aria-label={l}
        className="border border-slate-300 rounded-md px-2 py-2 bg-white disabled:bg-slate-50 disabled:text-slate-400">
        {options.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
      </select>
    </label>
  )
}

const HEAD: Record<string, string> = {
  SCHEDULED: 'bg-slate-100', BOARDING: 'bg-sky-50', TAXI_OUT: 'bg-blue-50', AIRBORNE: 'bg-blue-100',
  TAXI_IN: 'bg-indigo-50', AT_GATE: 'bg-green-50', EXCEPTIONS: 'bg-red-50',
}

function Board({ rows, moved, onMore }: { rows: Rows; moved: Set<string>; onMore: (phase: string) => void }) {
  // cancelled + diverted share one column, shown only when present
  const cols = [...BOARD, ...(rows.some((f) => EXCEPTIONS.includes(f.phase)) ? ['EXCEPTIONS'] : [])]
  const inCol = (f: Record<string, any>, p: string) => (p === 'EXCEPTIONS' ? EXCEPTIONS.includes(f.phase) : f.phase === p)
  return (
    <div className="flex gap-3 overflow-x-auto pb-2" data-testid="board">
      {cols.map((p) => {
        // newest first at the gate, next to depart first elsewhere
        const items = rows.filter((f) => inCol(f, p)).sort((a, b) => (p === 'AT_GATE' ? b.std_utc.localeCompare(a.std_utc) : a.std_utc.localeCompare(b.std_utc)))
        return (
          <section key={p} className="flex-1 min-w-[160px] bg-slate-100/70 rounded-lg flex flex-col max-h-[calc(100vh-330px)] min-h-[200px]" aria-label={colName(p)}>
            <header className={`px-3 py-2.5 rounded-t-lg flex items-center justify-between ${HEAD[p]}`}>
              <span className="text-sm font-semibold text-slate-800">{colName(p)}</span>
              <span className="text-xs font-semibold text-slate-600 bg-white/70 rounded-full px-2 py-0.5">{items.length}</span>
            </header>
            <div className="p-2 space-y-2 overflow-y-auto">
              {items.slice(0, COL_LIMIT).map((f) => <FlightCard key={f.flight_id} f={f} moved={moved.has(f.flight_id)} />)}
              {!items.length && <div className="text-xs text-slate-400 text-center py-6">No flights</div>}
              {items.length > COL_LIMIT && (
                <button onClick={() => onMore(p === 'EXCEPTIONS' ? '' : p)} className="w-full text-xs text-blue-700 hover:underline py-2">+{items.length - COL_LIMIT} more · open in list</button>
              )}
            </div>
          </section>
        )
      })}
    </div>
  )
}

function FlightCard({ f, moved }: { f: Record<string, any>; moved: boolean }) {
  const gate = f.gate_dep || f.gate_arr
  return (
    <button onClick={() => go('flights', f.flight_id)} data-flight={f.flight_id}
      className={`w-full text-left bg-white rounded-md border p-2.5 shadow-sm hover:border-blue-400 hover:shadow transition ${moved ? 'border-blue-500 ring-2 ring-blue-200' : 'border-slate-200'}`}>
      <div className="flex items-start justify-between gap-2">
        <span className="font-semibold text-slate-900">AL{f.flight_number}</span>
        <DelayPill f={f} />
      </div>
      <div className="text-sm text-slate-700">{f.origin_iata} → {f.destination_iata}</div>
      <div className="flex items-center justify-between text-xs text-slate-500 mt-1.5 tabular-nums">
        <span className="inline-flex items-center gap-1 whitespace-nowrap" title="Scheduled departure"><Icon name="clock" className="w-3.5 h-3.5" />{hhmm(f.std_utc)}Z</span>
        {gate && <span className="inline-flex items-center gap-1"><Icon name="gate" className="w-3.5 h-3.5" />{gate}</span>}
      </div>
      <div className="flex items-center justify-between text-xs mt-1">
        <span className="text-slate-400">{f.tail_number || ''}</span>
        {moved ? <Pill tone="info">Phase updated</Pill> : f.delay_cause ? <span className="text-slate-500">{label(f.delay_cause)}</span> : null}
      </div>
    </button>
  )
}

function List({ rows, moved }: { rows: Rows; moved: Set<string> }) {
  const [sort, setSort] = useState<{ k: SortKey; asc: boolean }>({ k: 'std_utc', asc: false })
  const [page, setPage] = useState(0)
  useEffect(() => setPage(0), [rows.length, sort])
  const sorted = useMemo(() => [...rows].sort((a, b) => {
    const va = a[sort.k] ?? -9999, vb = b[sort.k] ?? -9999
    const c = typeof va === 'number' && typeof vb === 'number' ? va - vb : String(va).localeCompare(String(vb))
    return sort.asc ? c : -c
  }), [rows, sort])
  const pages = Math.max(1, Math.ceil(sorted.length / PAGE))
  const slice = sorted.slice(page * PAGE, page * PAGE + PAGE)
  const Th = ({ k, children, right }: { k?: SortKey; children: ReactNode; right?: boolean }) => (
    <th className={`py-2.5 px-3 font-medium ${right ? 'text-right' : 'text-left'}`} aria-sort={k && sort.k === k ? (sort.asc ? 'ascending' : 'descending') : undefined}>
      {k ? <button onClick={() => setSort((s) => ({ k, asc: s.k === k ? !s.asc : false }))} className="hover:text-slate-900">
        {children}{sort.k === k ? (sort.asc ? ' ▲' : ' ▼') : ' ↕'}</button> : children}
    </th>
  )
  return (
    <section className="bg-white border border-slate-200 rounded-lg overflow-hidden" data-testid="list">
      <div className="overflow-x-auto">
        <table className="w-full text-sm tabular-nums">
          <thead className="bg-slate-50 text-xs text-slate-500">
            <tr>
              <Th k="dep_delay_min">Departure</Th><Th k="flight_number">Flight</Th><Th>Route</Th><Th>Tail</Th>
              <Th k="std_utc">STD</Th><Th>Gate</Th><Th>Phase</Th><Th>Cause</Th><Th right>Arr delay</Th><Th right>Taxi out</Th>
            </tr>
          </thead>
          <tbody>
            {slice.map((f) => (
              <tr key={f.flight_id} onClick={() => go('flights', f.flight_id)} className={`border-t border-slate-100 cursor-pointer hover:bg-blue-50/50 ${moved.has(f.flight_id) ? 'bg-blue-50' : ''}`}>
                <td className="py-2.5 px-3"><DelayPill f={f} /></td>
                <td className="px-3 font-semibold text-blue-700">AL{f.flight_number}</td>
                <td className="px-3">{f.origin_iata} → {f.destination_iata}</td>
                <td className="px-3 text-slate-500">{f.tail_number || '–'}</td>
                <td className="px-3">{hhmm(f.std_utc)}Z</td>
                <td className="px-3 text-slate-600">{f.gate_dep || f.gate_arr || '–'}</td>
                <td className="px-3"><PhaseBadge phase={f.phase} /></td>
                <td className="px-3 text-slate-600">{f.delay_cause ? label(f.delay_cause) : ''}</td>
                <td className="px-3 text-right">{f.arr_delay_min == null ? '–' : `${f.arr_delay_min > 0 ? '+' : ''}${f.arr_delay_min} min`}</td>
                <td className="px-3 text-right">{f.taxi_out_min == null ? '–' : `${f.taxi_out_min} min`}</td>
              </tr>
            ))}
            {!slice.length && <tr><td colSpan={10} className="text-center text-slate-500 py-8">No flights match these filters.</td></tr>}
          </tbody>
        </table>
      </div>
      <footer className="flex items-center justify-between px-4 py-3 border-t border-slate-100 text-xs text-slate-500 flex-wrap gap-2">
        <div className="flex gap-4">
          <span className="inline-flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-[#0ca30c]" />On time</span>
          <span className="inline-flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-[#fab219]" />&lt; 15 min</span>
          <span className="inline-flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-[#d03b3b]" />≥ 15 min / cancelled</span>
        </div>
        <div className="flex items-center gap-2">
          <span>Showing {sorted.length ? page * PAGE + 1 : 0}–{Math.min(sorted.length, (page + 1) * PAGE)} of {sorted.length}</span>
          <button disabled={page === 0} onClick={() => setPage(page - 1)} className="border border-slate-300 rounded px-2 py-1 disabled:opacity-40" aria-label="Previous page">‹</button>
          <span className="text-slate-700">{page + 1} / {pages}</span>
          <button disabled={page >= pages - 1} onClick={() => setPage(page + 1)} className="border border-slate-300 rounded px-2 py-1 disabled:opacity-40" aria-label="Next page">›</button>
        </div>
      </footer>
    </section>
  )
}
