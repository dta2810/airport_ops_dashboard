import { createContext, useEffect, useMemo, useRef, useState } from 'react'

export interface RoleInfo {
  role: string
  label: string
  airports: string[]
  sections: string[]
  priority: number
}

export type Rows = Record<string, any>[]

export const REFRESH_S = 30
export const WINDOWS = [6, 12, 24]
// selected window, read by card subtitles
export const HoursCtx = createContext(24)
const STALE_S = 60
// retry delays (s) while every endpoint is failing; then back to REFRESH_S
const BACKOFF_S = [5, 10, 20]
// endpoint -> section (contract §7); only allowed sections are fetched
const ENDPOINTS: Record<string, string> = {
  kpis: 'overview', flights: 'flights', hourly: 'airport_kpis', gates: 'gates', delays: 'delays', connections: 'connections',
}

class SessionExpired extends Error {}

// 401 or an HTML login page instead of JSON = Apps session gone
const expired = (r: Response) =>
  r.status === 401 || r.type === 'opaqueredirect' || (r.ok && !(r.headers.get('content-type') || '').includes('application/json'))

// one poll shared by every page
export function useOps(info: RoleInfo) {
  const [data, setData] = useState<Record<string, Rows>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [writtenAt, setWrittenAt] = useState<Date | null>(null)
  const [now, setNow] = useState(Date.now())
  const [airport, setAirportState] = useState<string>(() => (/^[A-Z]{3}$/.test(query().get('ap') || '') ? query().get('ap')! : ''))
  const [hours, setHoursState] = useState<number>(() => (WINDOWS.includes(Number(query().get('h'))) ? Number(query().get('h')) : 24))
  const setAirport = (a: string) => { setAirportState(a); setQuery({ ap: a }) }
  const setHours = (h: number) => { setHoursState(h); setQuery({ h: h === 24 ? '' : String(h) }) }
  const [loadedAt, setLoadedAt] = useState<number | null>(null)
  const [nextAt, setNextAt] = useState<number | null>(null)
  const [tick, setTick] = useState(0)
  const [failures, setFailures] = useState(0)
  const [sessionExpired, setSessionExpired] = useState(false)
  const [lastSuccessAt, setLastSuccessAt] = useState<number | null>(null)
  const allowed = useMemo(() => Object.entries(ENDPOINTS).filter(([, s]) => info.sections.includes(s)).map(([e]) => e), [info])
  // last good rows, kept across manual refresh, dropped on role/airport change
  const cache = useRef<{ key: string; rows: Record<string, Rows> }>({ key: '', rows: {} })

  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout> | undefined
    let fails = 0
    const qs = new URLSearchParams({ role: info.role })
    if (airport) qs.set('airport', airport)
    if (hours !== 24) qs.set('hours', String(hours))
    if (cache.current.key !== qs.toString()) cache.current = { key: qs.toString(), rows: {} }
    const last = cache.current.rows

    const load = async () => {
      let stop = false
      const results = await Promise.all(allowed.map(async (ep) => {
        try {
          const r = await fetch(`/api/${ep}?${qs}`, { redirect: 'manual' })
          if (expired(r)) throw new SessionExpired()
          if (!r.ok) return [ep, null, (await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`, r.status >= 500] as const
          return [ep, await r.json(), null, false] as const
        } catch (x) {
          if (x instanceof SessionExpired) stop = true
          return [ep, null, 'network error', true] as const
        }
      }))
      if (!alive) return
      if (stop) { setSessionExpired(true); setLoading(false); setNextAt(null); return }

      const d: Record<string, Rows> = {}
      const e: Record<string, string> = {}
      let ok = 0
      for (const [ep, rows, err, transient] of results) {
        if (rows) { d[ep] = last[ep] = rows; ok++ }
        else if (transient && last[ep]) d[ep] = last[ep]   // keep last data, banner shows reconnecting
        else if (err) e[ep] = err                          // 4xx or never loaded
      }
      fails = ok || !allowed.length ? 0 : fails + 1
      setData(d)
      setErrors(e)
      setFailures(fails)
      if (ok) {
        setLastSuccessAt(Date.now())
        const f = await fetch(`/api/freshness?${new URLSearchParams({ role: info.role })}`).then((r) => (r.ok ? r.json() : null)).catch(() => null)
        if (alive && f?.written_at) setWrittenAt(new Date(f.written_at))
      }
      if (!alive) return
      setLoading(false)
      setLoadedAt(Date.now())
      const wait = (fails ? BACKOFF_S[Math.min(fails, BACKOFF_S.length) - 1] : REFRESH_S) * 1000
      setNextAt(Date.now() + wait)
      timer = setTimeout(load, wait)
    }
    load()
    return () => { alive = false; clearTimeout(timer) }
  }, [info, airport, hours, allowed, tick])

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])

  const ageS = writtenAt ? Math.max(0, Math.round((now - writtenAt.getTime()) / 1000)) : null
  const airports = info.airports.includes('*') ? Array.from(new Set((data.kpis || []).map((k) => k.airport_iata))).sort() : info.airports
  return {
    data, errors, loading, allowed, airport, setAirport, hours, setHours, airports, loadedAt,
    ageS, stale: ageS == null || ageS > STALE_S,
    nextS: nextAt == null ? null : Math.max(0, Math.ceil((nextAt - now) / 1000)),
    refresh: () => setTick((n) => n + 1),
    consecutiveFailures: failures, sessionExpired, lastSuccessAt,
  }
}

export type Ops = ReturnType<typeof useOps>

// ops clock = latest event in the data (simulator runs on its own clock)
export function opsNow(rows: Rows) {
  const t = rows.reduce((m, f) => (f.last_event_ts && f.last_event_ts > m ? f.last_event_ts : m), '')
  return t ? new Date(t).getTime() : Date.now()
}

// hash routes: #/overview, #/flights, #/flights/<id>, optional ?query (filters, shareable)
const hashPath = () => window.location.hash.replace(/^#\/?/, '').split('?')[0]
export const query = () => new URLSearchParams(window.location.hash.split('?')[1] || '')
// merge into the hash query without a navigation (empty value = remove)
export function setQuery(patch: Record<string, string>) {
  const q = query()
  for (const [k, v] of Object.entries(patch)) v ? q.set(k, v) : q.delete(k)
  const qs = q.toString()
  window.history.replaceState(null, '', `#/${hashPath()}${qs ? `?${qs}` : ''}`)
}
const GLOBAL = ['ap', 'h']

export function useRoute() {
  const read = () => hashPath().split('/').map(decodeURIComponent)
  const [parts, setParts] = useState<string[]>(read)
  useEffect(() => {
    const on = () => setParts(read())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return parts
}

// navigate keeping the global filters (airport, window); extra = page filters
export function goWith(extra: Record<string, string>, ...parts: string[]) {
  const q = new URLSearchParams()
  const cur = query()
  for (const k of GLOBAL) if (cur.get(k)) q.set(k, cur.get(k)!)
  for (const [k, v] of Object.entries(extra)) if (v) q.set(k, v)
  const qs = q.toString()
  window.location.hash = '/' + parts.map(encodeURIComponent).join('/') + (qs ? `?${qs}` : '')
}
export const go = (...parts: string[]) => goWith({}, ...parts)
