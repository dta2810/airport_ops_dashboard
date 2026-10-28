import React, { useEffect, useRef, useState } from 'react'

type Msg = {
  message_id: string; question: string; status: string; answer?: string | null; sql?: string | null
  columns?: string[] | null; rows?: string[][] | null; row_count?: number | null; suggestions?: string[] | null; error?: string | null; viz_attachment_id?: string | null
}
type Conv = { conversation_id: string; title: string; updated_at: string }
type Cfg = { enabled: boolean; samples: string[] }

const DONE = new Set(['COMPLETED', 'FAILED', 'CANCELLED', 'QUERY_RESULT_EXPIRED'])
const POLL_MS = 2000
const MAX_WAIT_MS = 180000
const STATUS: Record<string, string> = {
  PENDING: 'Sending…', SUBMITTED: 'Sending…', FILTERING_CONTEXT: 'Reading the data model…', ASKING_AI: 'Thinking…',
  PENDING_WAREHOUSE: 'Starting the warehouse…', EXECUTING_QUERY: 'Running the query…', FETCHING_METADATA: 'Reading the data model…',
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`/api/genie${path}`, { headers: { 'Content-Type': 'application/json' }, ...init })
  const body = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`)
  return body as T
}

// **bold** and "- " bullets only; rendered as React nodes (no HTML injection)
function Md({ text }: { text: string }) {
  const inline = (s: string) => s.split(/(\*\*[^*]+\*\*)/g).map((p, i) =>
    p.startsWith('**') && p.endsWith('**') ? <strong key={i}>{p.slice(2, -2)}</strong> : <React.Fragment key={i}>{p}</React.Fragment>)
  const blocks: React.ReactNode[] = []
  let list: string[] = []
  const flush = () => {
    if (list.length) blocks.push(<ul key={blocks.length} className="list-disc pl-5 space-y-0.5">{list.map((l, i) => <li key={i}>{inline(l)}</li>)}</ul>)
    list = []
  }
  for (const line of text.split('\n')) {
    const m = line.match(/^\s*[-*]\s+(.*)/)
    if (m) { list.push(m[1]); continue }
    flush()
    if (line.trim()) blocks.push(<p key={blocks.length}>{inline(line)}</p>)
  }
  flush()
  return <div className="space-y-2">{blocks}</div>
}

// decimals to 2 places; integers and codes (flight numbers) untouched
const fmt = (v: string | null) => (v != null && /^-?\d+\.\d+$/.test(v) ? Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 }) : v ?? '—')

function Result({ m, cid }: { m: Msg; cid: string | null }) {
  if (!m.columns?.length || !m.rows?.length) return null
  return (
    <div className="mt-2">
      <div className="max-h-56 overflow-auto border border-slate-200 rounded">
        <table className="w-full text-xs">
          <thead className="bg-slate-50 sticky top-0"><tr>{m.columns.map((c) => <th key={c} className="text-left font-medium text-slate-600 px-2 py-1 whitespace-nowrap">{c}</th>)}</tr></thead>
          <tbody>{m.rows.map((r, i) => <tr key={i} className="border-t border-slate-100">{r.map((v, j) => <td key={j} className="px-2 py-1 whitespace-nowrap tabular-nums">{fmt(v)}</td>)}</tr>)}</tbody>
        </table>
      </div>
      <div className="text-[11px] text-slate-500 mt-1 flex items-center gap-2">
        <span className="mr-auto">{m.row_count != null && m.row_count > m.rows.length ? `First ${m.rows.length} of ${m.row_count} rows` : `${m.rows.length} rows`}</span>
        {cid && <a href={`/api/genie/csv/${cid}/${m.message_id}`} download={`${slug(m.question)}.csv`} className={linkBtn} aria-label="Download data as CSV">Download CSV</a>}
      </div>
    </div>
  )
}

const slug = (q: string) => q.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 50) || 'genie'
const linkBtn = 'text-xs border border-slate-300 rounded px-2 py-0.5 bg-white/90 hover:bg-slate-100 text-slate-700'

function Chart({ cid, m }: { cid: string; m: Msg }) {
  const [state, setState] = useState<'loading' | 'ok' | 'failed'>('loading')
  const [big, setBig] = useState(false)
  useEffect(() => {
    if (!big) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setBig(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [big])
  if (!m.viz_attachment_id || state === 'failed') return null
  const src = `/api/genie/viz/${cid}/${m.message_id}`
  return (
    <div className="mt-2 relative group">
      {state === 'loading' && <div data-testid="viz-skeleton" className="w-full aspect-[800/380] rounded border border-slate-200 bg-slate-100 animate-pulse" />}
      <img src={src} alt={`Chart: ${m.question}`} onLoad={() => setState('ok')} onError={() => setState('failed')} onClick={() => setBig(true)}
        className={`w-full border border-slate-200 rounded bg-white cursor-zoom-in ${state === 'ok' ? '' : 'hidden'}`} />
      {state === 'ok' && (
        <div className="absolute top-1.5 right-1.5 flex gap-1 opacity-0 group-hover:opacity-100 focus-within:opacity-100">
          <button onClick={() => setBig(true)} className={linkBtn} aria-label="Expand chart">Expand</button>
          <a href={src} download={`${slug(m.question)}.png`} className={linkBtn} aria-label="Download chart as PNG">PNG</a>
        </div>
      )}
      {big && (
        <div role="dialog" aria-modal="true" aria-label="Chart" onClick={() => setBig(false)}
          className="fixed inset-0 z-50 bg-slate-900/70 flex items-center justify-center p-8">
          <div onClick={(e) => e.stopPropagation()} className="bg-white rounded-lg shadow-xl max-w-[90vw] max-h-[90vh] p-4 flex flex-col gap-3">
            <div className="flex items-center gap-2">
              <div className="text-sm font-medium text-slate-800 mr-auto">{m.question}</div>
              <a href={src} download={`${slug(m.question)}.png`} className={linkBtn}>Download PNG</a>
              <button onClick={() => setBig(false)} aria-label="Close" className="text-slate-500 hover:text-slate-900 px-1 text-lg leading-none">×</button>
            </div>
            <img src={src} alt={`Chart: ${m.question}`} className="max-w-[85vw] max-h-[78vh] object-contain" />
          </div>
        </div>
      )}
    </div>
  )
}

function Answer({ m, onAsk, last, cid }: { m: Msg; onAsk: (q: string) => void; last: boolean; cid: string | null }) {
  if (!DONE.has(m.status)) {
    return <div className="flex items-center gap-2 text-sm text-slate-500"><span className="h-2 w-2 rounded-full bg-blue-500 animate-pulse" />{STATUS[m.status] || 'Working…'}</div>
  }
  return (
    <div className="text-sm text-slate-800">
      {m.answer ? <Md text={m.answer} /> : m.status === 'COMPLETED' && !m.rows?.length && <p className="text-slate-500">No rows. Your role may not include this airport or section.</p>}
      {m.status !== 'COMPLETED' && <p className="text-red-700">Genie could not answer{m.error ? `: ${m.error}` : '.'}</p>}
      {m.status === 'COMPLETED' && m.error && <p className="text-amber-700 text-xs mt-1">{m.error}</p>}
      {cid && <Chart cid={cid} m={m} />}
      <Result m={m} cid={cid} />
      {m.sql && (
        <details className="mt-2 text-xs">
          <summary className="cursor-pointer text-blue-700 select-none">Show SQL</summary>
          <pre className="mt-1 p-2 bg-slate-50 border border-slate-200 rounded overflow-x-auto whitespace-pre-wrap">{m.sql}</pre>
        </details>
      )}
      {last && !!m.suggestions?.length && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {m.suggestions.map((s) => <button key={s} onClick={() => onAsk(s)} className="text-xs border border-blue-200 text-blue-800 bg-blue-50 rounded-full px-2.5 py-1 hover:bg-blue-100 text-left">{s}</button>)}
        </div>
      )}
    </div>
  )
}

const ago = (iso: string) => {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  return s < 60 ? 'just now' : s < 3600 ? `${Math.floor(s / 60)} min ago` : s < 86400 ? `${Math.floor(s / 3600)} h ago` : new Date(iso).toISOString().slice(0, 10)
}

export default function GeniePanel({ role, onClose }: { role: string; onClose: () => void }) {
  const [cfg, setCfg] = useState<Cfg | null>(null)
  const [view, setView] = useState<'chat' | 'history'>('chat')
  const [convs, setConvs] = useState<Conv[]>([])
  const [cid, setCid] = useState<string | null>(null)
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [error, setError] = useState<string | null>(null)
  const busy = msgs.some((m) => !DONE.has(m.status))
  const scrollRef = useRef<HTMLDivElement>(null)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    api<Cfg>('/config').then(setCfg).catch((e) => setError(e.message))
    return () => { alive.current = false }
  }, [])
  // scroll only the panel; scrollIntoView would also move the page behind it
  useEffect(() => { const el = scrollRef.current; if (el) el.scrollTop = el.scrollHeight }, [msgs])

  const loadHistory = () => api<{ conversations: Conv[] }>('/conversations').then((r) => setConvs(r.conversations)).catch((e) => setError(e.message))

  const poll = async (c: string, mid: string) => {
    const t0 = Date.now()
    while (alive.current && Date.now() - t0 < MAX_WAIT_MS) {
      await new Promise((r) => setTimeout(r, POLL_MS))
      try {
        const m = await api<Msg>(`/messages/${c}/${mid}`)
        setMsgs((ms) => ms.map((x) => (x.message_id === mid ? m : x)))
        if (DONE.has(m.status)) return
      } catch (e: any) {
        setMsgs((ms) => ms.map((x) => (x.message_id === mid ? { ...x, status: 'FAILED', error: e.message } : x)))
        return
      }
    }
    setMsgs((ms) => ms.map((x) => (x.message_id === mid && !DONE.has(x.status) ? { ...x, status: 'FAILED', error: 'timed out' } : x)))
  }

  const ask = async (q: string) => {
    const question = q.trim()
    if (!question || busy) return
    setInput(''); setError(null); setView('chat')
    const tmp = `tmp-${Date.now()}`
    setMsgs((ms) => [...ms, { message_id: tmp, question, status: 'PENDING' }])
    try {
      const r = await api<{ conversation_id: string; message_id: string }>('/ask', {
        method: 'POST', body: JSON.stringify({ question, conversation_id: cid, role }),
      })
      setCid(r.conversation_id)
      setMsgs((ms) => ms.map((x) => (x.message_id === tmp ? { ...x, message_id: r.message_id } : x)))
      await poll(r.conversation_id, r.message_id)
    } catch (e: any) {
      setMsgs((ms) => ms.map((x) => (x.message_id === tmp ? { ...x, status: 'FAILED', error: e.message } : x)))
    }
  }

  const open = async (c: string) => {
    try {
      const r = await api<{ conversation_id: string; messages: Msg[] }>(`/conversations/${c}`)
      setCid(r.conversation_id); setMsgs(r.messages); setView('chat')
      r.messages.filter((m) => !DONE.has(m.status)).forEach((m) => poll(c, m.message_id))
    } catch (e: any) { setError(e.message) }
  }

  const remove = async (c: string) => {
    try {
      await api(`/conversations/${c}`, { method: 'DELETE' })
      setConvs((cs) => cs.filter((x) => x.conversation_id !== c))
      if (c === cid) { setCid(null); setMsgs([]) }
    } catch (e: any) { setError(e.message) }
  }

  const fresh = () => { setCid(null); setMsgs([]); setView('chat'); setError(null) }

  return (
    <aside data-testid="genie-panel" aria-label="Ask Genie" className="w-[420px] shrink-0 border-l border-slate-200 bg-white sticky top-0 h-screen flex flex-col">
      <div className="px-4 py-3 border-b border-slate-200 flex items-center gap-2">
        <div className="font-semibold text-slate-900 mr-auto">Ask Genie</div>
        <button onClick={fresh} disabled={busy} className="text-xs border border-slate-300 rounded px-2 py-1 hover:bg-slate-100 disabled:opacity-50">New chat</button>
        <button onClick={() => { setView(view === 'history' ? 'chat' : 'history'); if (view !== 'history') loadHistory() }}
          aria-pressed={view === 'history'} className={`text-xs border rounded px-2 py-1 ${view === 'history' ? 'bg-slate-800 text-white border-slate-800' : 'border-slate-300 hover:bg-slate-100'}`}>History</button>
        <button onClick={onClose} aria-label="Close Genie panel" className="text-slate-500 hover:text-slate-900 px-1 text-lg leading-none">×</button>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-3">
        {error && <div className="mb-3 text-sm text-red-800 bg-red-50 border border-red-200 rounded p-2">{error}</div>}
        {cfg && !cfg.enabled && <div className="text-sm text-slate-500">Genie is not configured for this app.</div>}

        {view === 'history' ? (
          convs.length === 0 ? <div className="text-sm text-slate-500">No previous conversations.</div> : (
            <ul className="space-y-1">
              {convs.map((c) => (
                <li key={c.conversation_id} className="group flex items-start gap-2 rounded hover:bg-slate-50">
                  <button onClick={() => open(c.conversation_id)} className="flex-1 text-left px-2 py-2">
                    <div className="text-sm text-slate-800 line-clamp-2">{c.title}</div>
                    <div className="text-[11px] text-slate-500">{ago(c.updated_at)}</div>
                  </button>
                  <button onClick={() => remove(c.conversation_id)} aria-label={`Delete conversation ${c.title}`}
                    className="opacity-0 group-hover:opacity-100 focus:opacity-100 text-xs text-slate-500 hover:text-red-700 px-2 py-2">Delete</button>
                </li>
              ))}
            </ul>
          )
        ) : msgs.length === 0 ? (
          cfg?.enabled && (
            <div>
              <div className="text-sm text-slate-600 mb-2">Ask about flights, OTP, taxi times, gates or hub connections. Try:</div>
              <div className="space-y-1.5">
                {cfg.samples.map((s) => <button key={s} onClick={() => ask(s)} className="block w-full text-left text-sm border border-slate-200 rounded px-3 py-2 hover:bg-slate-50">{s}</button>)}
              </div>
            </div>
          )
        ) : (
          <div className="space-y-4">
            {msgs.map((m, i) => (
              <div key={m.message_id} className="space-y-2">
                <div className="ml-8 bg-blue-600 text-white text-sm rounded-lg rounded-br-sm px-3 py-2">{m.question}</div>
                <Answer m={m} onAsk={ask} last={i === msgs.length - 1} cid={cid} />
              </div>
            ))}
          </div>
        )}
      </div>

      <form onSubmit={(e) => { e.preventDefault(); ask(input) }} className="border-t border-slate-200 p-3 flex gap-2">
        <textarea id="genie-input" name="genie-input" aria-label="Question for Genie" rows={2} value={input} maxLength={1000}
          disabled={!cfg?.enabled || busy} onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(input) } }}
          placeholder={busy ? 'Waiting for Genie…' : 'Ask a question (Enter to send)'}
          className="flex-1 resize-none border border-slate-300 rounded-md px-2 py-1.5 text-sm disabled:bg-slate-50" />
        <button type="submit" disabled={!cfg?.enabled || busy || !input.trim()} className="self-end bg-blue-600 text-white text-sm rounded-md px-3 py-1.5 disabled:opacity-50">Send</button>
      </form>
    </aside>
  )
}
