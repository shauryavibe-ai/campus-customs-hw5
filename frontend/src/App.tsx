import { useCallback, useEffect, useRef, useState } from 'react'
import {
  API_BASE, api, money,
  type AgentEvent, type ApprovalBrief, type Cash, type DecisionResult, type Expected, type Health,
  type PriorityRow, type RunJob, type TicketDetail, type TicketRow,
} from './api'
import { TicketList } from './components/TicketList'
import { DeliveryRoute } from './components/DeliveryRoute'
import { ApprovalDesk, Drafts, ExpectedFlow } from './components/Panels'
import { Warehouse } from './components/Warehouse'

const POLL_MS = 1500

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [tickets, setTickets] = useState<TicketRow[]>([])
  const [priority, setPriority] = useState<PriorityRow[]>([])
  const [cash, setCash] = useState<Cash | null>(null)
  const [pendingAll, setPendingAll] = useState<ApprovalBrief[]>([])
  const [selected, setSelected] = useState<number | null>(null)
  const [detail, setDetail] = useState<TicketDetail | null>(null)
  const [expected, setExpected] = useState<Expected | null>(null)
  const [events, setEvents] = useState<AgentEvent[]>([])
  const [job, setJob] = useState<RunJob | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [justResolved, setJustResolved] = useState<number | null>(null)
  const [resets, setResets] = useState(0)
  const cursor = useRef(0)
  const runId = useRef<string | null>(null)
  const loadToken = useRef(0)   // bumps on every load/switch; a slower, older answer is then ignored

  const refreshGlobal = useCallback(async () => {
    try {
      const [h, t, p, c, a] = await Promise.all([api.health(), api.tickets(), api.priority(), api.cash(), api.approvals()])
      setHealth(h); setTickets(t.tickets); setPriority(p.priority); setCash(c); setPendingAll(a.approvals); setError(null)
    } catch (e) {
      setError(`Can't reach the backend at ${API_BASE}: ${(e as Error).message}. Start it from hw 5/backend with: uvicorn main:app --reload --port 8000`)
    }
  }, [])

  const loadTicket = useCallback(async (id: number) => {
    const token = ++loadToken.current
    const [d, x] = await Promise.all([api.ticket(id), api.expected(id)])
    const ev = x.run_id ? (await api.events({ run_id: x.run_id, limit: 1000 })).events : []
    if (token !== loadToken.current) return     // another ticket or run took over meanwhile
    setDetail(d); setExpected(x); setEvents(ev)
  }, [])

  useEffect(() => { refreshGlobal() }, [refreshGlobal])
  useEffect(() => {
    if (selected == null && tickets.length) {
      const first = priority[0]?.ticket_id ?? tickets.find(t => t.is_open)?.id ?? tickets[0].id
      setSelected(first)
    }
  }, [tickets, priority, selected])
  useEffect(() => {
    if (selected != null && job?.ticket_id !== selected) loadTicket(selected).catch(e => setError((e as Error).message))
  }, [selected, loadTicket, job])

  // Dispatch: start the team on the ticket, then follow its events live.
  async function dispatch() {
    if (selected == null) return
    setError(null); setJustResolved(null)
    try {
      cursor.current = (await api.events({ limit: 1 })).last_seq
      runId.current = null
      loadToken.current++
      const j = await api.run(selected)
      setEvents([]); setExpected(null); setJob(j)
      refreshGlobal()
    } catch (e) { setError((e as Error).message) }
  }

  useEffect(() => {
    if (!job || job.status !== 'running') return
    let stopped = false
    const tick = async () => {
      try {
        const ev = await api.events({ ticket_id: job.ticket_id, after_seq: cursor.current, limit: 500 })
        if (ev.events.length) {
          cursor.current = ev.last_seq
          const start = ev.events.find(e => e.kind === 'start')
          if (start?.run_id && !runId.current) runId.current = start.run_id
          const mine = ev.events.filter(e => !runId.current || e.run_id === runId.current)
          if (!stopped) setEvents(prev => [...prev, ...mine])
        }
        const j = await api.job(job.job_id)
        if (j.status !== 'running' && !stopped) {
          setJob(j)
          await Promise.all([loadTicket(j.ticket_id), refreshGlobal()])
        }
      } catch (e) { setError((e as Error).message) }
    }
    const id = setInterval(tick, POLL_MS)
    return () => { stopped = true; clearInterval(id) }
  }, [job, loadTicket, refreshGlobal])

  // Follow runs started elsewhere (another tab, a script) and keep the rack, cash and
  // approvals fresh: every few seconds, adopt any run in progress that we aren't following.
  useEffect(() => {
    const id = setInterval(async () => {
      try {
        if (job?.status === 'running') return
        const { runs } = await api.runs()
        const live = runs.find(r => r.status === 'running')
        if (live) {
          const start = (await api.events({ ticket_id: live.ticket_id, kind: 'start', limit: 1 })).events[0]
          cursor.current = start ? start.seq - 1 : (await api.events({ limit: 1 })).last_seq
          runId.current = null
          loadToken.current++
          setSelected(live.ticket_id); setEvents([]); setExpected(null); setJustResolved(null); setJob(live)
          api.ticket(live.ticket_id).then(setDetail).catch(() => {})
          api.expected(live.ticket_id).then(setExpected).catch(() => {})
        }
        refreshGlobal()
        if (selected != null) loadTicket(selected).catch(() => {})
      } catch { /* backend briefly unavailable; try again next tick */ }
    }, 4000)
    return () => clearInterval(id)
  }, [job, selected, refreshGlobal, loadTicket])

  async function onDecided(r: DecisionResult) {
    if (r.tickets_resolved.includes(selected ?? -1)) setJustResolved(selected)
    setCash(c => (c ? { ...c, checking_balance: r.checking_balance } : c))
    await Promise.all([refreshGlobal(), selected != null ? loadTicket(selected) : null])
  }

  async function reset() {
    if (!window.confirm('Reset the shop to the original data for a fresh run?\n\nApprovals, payments, drafts and ticket decisions are cleared. The current database is backed up first, and the audit trail is kept.')) return
    try {
      await api.reset()
      setDetail(null); setEvents([]); setExpected(null); setJob(null); setJustResolved(null)
      setResets(n => n + 1)
      await refreshGlobal()          // fresh priority first, so the #1 ticket gets selected
      setSelected(null)
    } catch (e) { setError((e as Error).message) }
  }

  const running = job?.status === 'running'
  const runningHere = running && job?.ticket_id === selected
  const t = detail?.ticket
  const ticketApprovals = detail?.approvals ?? []
  const waiting = ticketApprovals.filter(a => a.status === 'pending').length

  return (
    <div className="app">
      <header className="hero">
        <div className="logo" aria-hidden><span>CC</span></div>
        <div className="hero-title">
          <h1>Campus Customs <span>Warehouse Operations</span></h1>
          <p>The Boss watches from the second floor and sends work orders down to Inventory, Accounting, Facilities and Customer Service.</p>
          <div className="chips">
            {health && <span className="chip">Shop date <b>{health.business_date}</b></span>}
            {health && <span className={`chip ${health.audit.ok ? 'ok' : 'bad'}`}>Audit trail <b>{health.audit.ok ? `${health.audit.entries} entries ✓` : 'broken'}</b></span>}
            <span className="chip">Waiting for approval <b>{pendingAll.length}</b></span>
            <button className="btn ghost small" onClick={reset} disabled={running}>Reset to fresh run</button>
          </div>
        </div>
        <div className="cash-card" aria-label="Checking balance">
          <div className="label">Checking balance</div>
          <div className="big">{money(cash?.checking_balance)}</div>
          <div className="cash-sub">Pending payments {money(cash?.payments_waiting_for_approval)}</div>
          <div className="cash-sub">After all pending <b className={cash && cash.checking_after_pending < 0 ? 'bad-text' : ''}>{money(cash?.checking_after_pending)}</b></div>
        </div>
      </header>

      {error && <div className="error-bar" role="alert">{error}</div>}

      <main className="layout">
        <TicketList tickets={tickets} priority={priority} selected={selected} runningId={running ? job!.ticket_id : null}
                    onSelect={id => { setSelected(id); setJustResolved(null) }} />

        <section className="panel center" aria-label="Warehouse">
          {t ? <>
            <div className="ticket-head">
              <div>
                <div className="eyebrow">Ticket {t.id} · {t.type.replace('_', ' ')}</div>
                <h2>{t.subject} <span className="who">from {t.requester}</span></h2>
                <div className="notes" title="Written by the requester; shown as plain text">“{t.notes}”</div>
                <div className="facts">
                  {t.sku && <span className="chip">{t.sku} · {t.size} × {t.qty}</span>}
                  {detail?.invoice && <span className="chip warn">Invoice #{detail.invoice.id} {money(detail.invoice.amount)} {detail.invoice.status}</span>}
                  {detail?.lease && <span className="chip">{detail.lease.space_name} · {money(detail.lease.monthly_rent)} due {detail.lease.next_due}</span>}
                  {detail?.safety.injection_flags.length ? <span className="chip bad">Suspicious text flagged</span> : null}
                </div>
              </div>
              <button className="btn primary big-btn" onClick={dispatch} disabled={running || t.status === 'resolved'}>
                {runningHere ? 'Team en route…' : t.status === 'resolved' ? 'Delivered' : events.length ? 'Dispatch again' : 'Dispatch the team'}
              </button>
            </div>

            <div className={`status-banner s-${runningHere ? 'running' : t.status}`} role="status">
              {runningHere ? 'The team is working — Boss first, then whoever the Boss calls.'
                : t.status === 'resolved' ? `Resolved ✓ ${justResolved === t.id ? '— you just closed it. Party on the warehouse floor!' : ''}`
                : t.status === 'waiting_on_human' ? `Waiting for your approval (${waiting}) — review on the right.`
                : t.status === 'in_progress' ? 'In progress — the Boss left work for the team.'
                : 'Ready to dispatch.'}
              {!runningHere && job?.ticket_id === t.id && job.status !== 'running' && job.seconds != null &&
                <span className="sub"> · run took {job.seconds}s, {job.usage?.requests ?? '?'} model calls, {job.handoffs ?? 0} hand-offs</span>}
            </div>

            <Warehouse events={events} running={!!runningHere} expected={expected} ticketId={t.id}
                       resolved={t.status === 'resolved'} />

            <details className="dispatch-log" open={!!runningHere || undefined}>
              <summary>Dispatch log — every step in order ({events.filter(e => !['tool_result', 'run_end'].includes(e.kind)).length})</summary>
              <DeliveryRoute events={events} running={!!runningHere} />
            </details>
          </> : <p className="empty">Choose a ticket.</p>}
        </section>

        <aside className="panel right" aria-label="Approvals and details">
          <h3>Signing desk · your approvals</h3>
          <ApprovalDesk key={`${selected}-${resets}`} approvals={ticketApprovals} onDecided={onDecided}
                        ticketId={t?.id} ticketStatus={t?.status}
                        onClosed={async () => { setJustResolved(selected); await Promise.all([refreshGlobal(), selected != null ? loadTicket(selected) : null]) }} />
          <h3>Customer message</h3>
          <Drafts messages={detail?.customer_messages ?? []} />
          <h3>Expected vs actual</h3>
          <ExpectedFlow expected={expected} />
        </aside>
      </main>
      <footer>Agents run on gpt-6-luna and only touch the shop through their own MCP tools. Money moves only when a person approves it. Resolved tickets move to the “Resolved” rack.</footer>
    </div>
  )
}
