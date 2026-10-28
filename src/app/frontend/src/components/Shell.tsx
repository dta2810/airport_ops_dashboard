import React, { useState } from 'react'
import { HoursCtx, Ops, REFRESH_S, RoleInfo, WINDOWS, go } from '../useOps'
import { Icon } from './ui'
import GeniePanel from './GeniePanel'

// data: page shown only once that endpoint returns rows (gates exist only at the hub)
export const NAV: { id: string; label: string; section: string; data?: string }[] = [
  { id: 'overview', label: 'Overview', section: 'overview' },
  { id: 'flights', label: 'Flights', section: 'flights' },
  { id: 'rotations', label: 'Rotations', section: 'flights', data: 'flights' },
  { id: 'gates', label: 'Gates', section: 'gates', data: 'gates' },
  { id: 'connections', label: 'Connections', section: 'connections' },
]

export const navFor = (info: RoleInfo, ops: Ops) =>
  NAV.filter((n) => info.sections.includes(n.section) && (!n.data || ops.loading || !!ops.data[n.data]?.length))

export default function Shell({ info, ops, page, canSwitch, onSwitchRole, children }: {
  info: RoleInfo; ops: Ops; page: string; canSwitch: boolean; onSwitchRole: () => void; children: React.ReactNode
}) {
  const nav = navFor(info, ops)
  // Genie panel pushes the page; open state survives reloads
  const [genie, setGenie] = useState(() => localStorage.getItem('aops.genie.open') === '1')
  const toggleGenie = (v: boolean) => { setGenie(v); localStorage.setItem('aops.genie.open', v ? '1' : '0') }
  const showBanner = ops.consecutiveFailures > 0 || ops.sessionExpired
  const bannerMsg = ops.sessionExpired
    ? 'Your session expired — reload to sign in again'
    : ops.lastSuccessAt
      ? `Reconnecting… showing data from ${new Date(ops.lastSuccessAt).toISOString().slice(11, 19)} UTC`
      : 'Reconnecting…'

  return (
    <div className="min-h-screen flex bg-slate-50 text-slate-900">
      <aside className="w-56 shrink-0 bg-[#0d2a4f] text-slate-200 flex flex-col sticky top-0 h-screen">
        <div className="px-5 py-5 border-b border-white/10">
          <div className="text-white font-semibold text-lg leading-tight">aeroLatinos</div>
          <div className="text-xs text-slate-400">Airport Operations</div>
        </div>
        <nav className="flex-1 p-3 space-y-1" aria-label="Main">
          {nav.map((n) => (
            <a key={n.id} href={`#/${n.id}`} aria-current={page === n.id ? 'page' : undefined}
              className={`flex items-center gap-3 px-3 py-2 rounded-md text-sm ${page === n.id ? 'bg-blue-600 text-white' : 'hover:bg-white/10'}`}>
              <Icon name={n.id} />{n.label}
            </a>
          ))}
        </nav>
        <div className="p-4 border-t border-white/10 text-xs">
          <div className="text-slate-400">Signed in as</div>
          <div className="text-white font-medium mt-0.5">{info.label}</div>
          <div className="text-slate-400 mt-0.5">{info.airports.includes('*') ? 'All airports' : info.airports.join(', ')}</div>
          {canSwitch && <button onClick={onSwitchRole} className="mt-3 w-full border border-white/20 rounded px-2 py-1 hover:bg-white/10">Switch role</button>}
        </div>
      </aside>

      <div className="flex-1 min-w-0">
        <header className="bg-white border-b border-slate-200 sticky top-0 z-10">
          <div className="px-6 py-3 flex items-center justify-end gap-3 text-sm">
            <span className="mr-auto text-[11px] font-semibold tracking-wide px-2.5 py-1 rounded-full bg-teal-50 text-teal-800 border border-teal-200">DEMO · SYNTHETIC DATA</span>
            <select id="airport" name="airport" aria-label="Airport" value={ops.airport} onChange={(e) => ops.setAirport(e.target.value)}
              className="border border-slate-300 rounded-md px-2 py-1.5 bg-white">
              <option value="">{info.airports.includes('*') ? 'All airports' : 'All my airports'}</option>
              {ops.airports.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
            <select aria-label="Time window" value={ops.hours} onChange={(e) => ops.setHours(Number(e.target.value))}
              className="border border-slate-300 rounded-md px-2 py-1.5 bg-white">
              {WINDOWS.map((h) => <option key={h} value={h}>Last {h} h</option>)}
            </select>
            <button data-testid="live" onClick={ops.refresh} title="Refresh now"
              className="flex items-center gap-2 border border-slate-300 rounded-md px-2.5 py-1.5 hover:bg-slate-100">
              <span className="relative flex h-2.5 w-2.5">
                {!ops.stale && ops.consecutiveFailures === 0 && <span className="absolute inline-flex h-full w-full rounded-full bg-green-500 opacity-75 animate-ping" />}
                <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${ops.consecutiveFailures > 0 ? 'bg-amber-500' : ops.stale ? 'bg-amber-500' : 'bg-green-600'}`} />
              </span>
              <span className="text-slate-700">{ops.consecutiveFailures > 0 ? 'Reconnecting' : 'Live'} · next refresh {ops.nextS ?? REFRESH_S} s</span>
            </button>
            <span data-testid="freshness" className={ops.stale || ops.consecutiveFailures > 0 ? 'text-amber-700' : 'text-slate-600'}>
              {ops.ageS == null ? 'No data yet' : `Updated ${ops.ageS} s ago`}{(ops.stale || ops.consecutiveFailures > 0) && ops.ageS != null ? ' · stale' : ''}
            </span>
            <button data-testid="genie-toggle" onClick={() => toggleGenie(!genie)} aria-expanded={genie}
              className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 font-medium ${genie ? 'bg-slate-800 text-white' : 'bg-blue-600 text-white hover:bg-blue-700'}`}>
              <span aria-hidden>✦</span>Ask Genie
            </button>
          </div>
        </header>

        {showBanner && (
          <div className={`px-6 py-3 flex items-center justify-between border-b ${ops.sessionExpired ? 'bg-red-50 border-red-200' : 'bg-amber-50 border-amber-200'}`}>
            <span className={`text-sm ${ops.sessionExpired ? 'text-red-900' : 'text-amber-900'}`}>{bannerMsg}</span>
            <button
              onClick={ops.sessionExpired ? () => window.location.reload() : ops.refresh}
              className={`ml-4 px-3 py-1 rounded text-sm font-medium ${ops.sessionExpired ? 'bg-red-200 hover:bg-red-300 text-red-900' : 'bg-amber-200 hover:bg-amber-300 text-amber-900'}`}
            >
              {ops.sessionExpired ? 'Reload' : 'Retry now'}
            </button>
          </div>
        )}

        <main className="px-6 py-6 max-w-[1500px]"><HoursCtx.Provider value={ops.hours}>{children}</HoursCtx.Provider></main>
      </div>
      {genie && <GeniePanel role={info.role} onClose={() => toggleGenie(false)} />}
    </div>
  )
}

export function PageHeader({ title, subtitle, back, actions }: { title: React.ReactNode; subtitle?: React.ReactNode; back?: string; actions?: React.ReactNode }) {
  return (
    <div className="mb-5">
      {back && <button onClick={() => go(back)} className="flex items-center gap-1 text-sm text-blue-700 hover:underline mb-2"><Icon name="back" />Back to {back}</button>}
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">{title}</h1>
          {subtitle && <div className="text-sm text-slate-500 mt-1">{subtitle}</div>}
        </div>
        {actions}
      </div>
    </div>
  )
}
