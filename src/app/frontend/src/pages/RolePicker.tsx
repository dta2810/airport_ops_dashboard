import { RoleInfo } from '../useOps'

export default function RolePicker({ roles, onSelect }: { roles: RoleInfo[]; onSelect: (r: RoleInfo) => void }) {
  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center p-6">
      <div className="max-w-xl w-full">
        <h1 className="text-2xl font-semibold text-slate-900">aeroLatinos · Airport Operations</h1>
        <p className="text-sm text-slate-600 mt-1 mb-6">You belong to several teams. Pick the view to open; data access follows the team you pick.</p>
        <div className="space-y-3">
          {roles.map((r) => (
            <button key={r.role} onClick={() => onSelect(r)}
              className="w-full text-left p-4 bg-white border border-slate-200 rounded-lg hover:border-blue-500">
              <div className="font-medium text-slate-900">{r.label}</div>
              <div className="text-xs text-slate-600 mt-1">Airports: {r.airports.includes('*') ? 'all' : r.airports.join(', ')} · Sections: {r.sections.join(', ')}</div>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
