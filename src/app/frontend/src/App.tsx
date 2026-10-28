import { useEffect, useState } from 'react'
import Shell, { navFor } from './components/Shell'
import Connections from './pages/Connections'
import FlightDetail from './pages/FlightDetail'
import Flights from './pages/Flights'
import Gates from './pages/Gates'
import Overview from './pages/Overview'
import RolePicker from './pages/RolePicker'
import Rotations from './pages/Rotations'
import { RoleInfo, useOps, useRoute } from './useOps'

export default function App() {
  const [roles, setRoles] = useState<RoleInfo[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<RoleInfo | null>(null)

  useEffect(() => {
    // only roles the platform accepted for this user (group-scoped mint succeeded)
    fetch('/api/roles')
      .then(async (r) => (r.ok ? r.json() : Promise.reject((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`)))
      .then((d: { roles: RoleInfo[] }) => {
        setRoles(d.roles)
        if (d.roles.length === 1) setSelected(d.roles[0])
      })
      .catch((e) => setError(String(e)))
  }, [])

  if (error) return <Centered><p className="text-red-700">Could not load your roles: {error}</p></Centered>
  if (!roles) return <Centered><p className="text-slate-500">Checking your access…</p></Centered>
  if (!roles.length) return <Centered><p className="text-slate-700">You are not a member of any airport-ops group. Ask an administrator for access.</p></Centered>
  if (!selected) return <RolePicker roles={roles} onSelect={setSelected} />
  return <Workspace key={selected.role} info={selected} canSwitch={roles.length > 1} onSwitchRole={() => setSelected(null)} />
}

function Workspace({ info, canSwitch, onSwitchRole }: { info: RoleInfo; canSwitch: boolean; onSwitchRole: () => void }) {
  const ops = useOps(info)
  const [page, id] = useRoute()
  // unknown or not-allowed pages fall back to overview
  const allowed = navFor(info, ops).map((n) => n.id)
  const current = allowed.includes(page) ? page : 'overview'
  return (
    <Shell info={info} ops={ops} page={current} canSwitch={canSwitch} onSwitchRole={onSwitchRole}>
      {current === 'flights' && id ? <FlightDetail ops={ops} id={id} />
        : current === 'flights' ? <Flights ops={ops} />
          : current === 'rotations' ? <Rotations ops={ops} />
          : current === 'gates' ? <Gates ops={ops} />
          : current === 'connections' ? <Connections info={info} ops={ops} />
          : <Overview info={info} ops={ops} />}
    </Shell>
  )
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="min-h-screen flex items-center justify-center bg-slate-50 p-6">{children}</div>
}
