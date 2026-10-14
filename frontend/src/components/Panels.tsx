import { useEffect, useState } from 'react'
import {
  AGENT_LABEL, api, ApiError, firstSentences, money, plain,
  type AgentActivity, type ApprovalBrief, type ApprovalView, type CustomerMessage, type DecisionResult, type Expected,
} from '../api'
import { AgentBadge } from './DeliveryRoute'

const ALL = ['boss', 'inventory', 'accounting', 'facilities', 'customer_service'] as const
const KIND: Record<string, string> = {
  invoice_payment: 'Vendor payment', rent_payment: 'Rent payment', purchase_order: 'Purchase order', discount: 'Discount',
}

/** A short summary of what each agent did on this ticket. */
export function AgentSummaries({ agents, expected }: { agents: AgentActivity[]; expected: Expected | null }) {
  const byName = new Map(agents.map(a => [a.agent, a]))
  return (
    <div className="agents-grid">
      {ALL.map(name => {
        const a = byName.get(name)
        const active = !!a && (a.tool_calls > 0 || a.said.length > 0)
        const verdict = expected?.run_id ? expected.per_agent[name]?.verdict : undefined  // only after a run
        const said = a?.said.at(-1)?.text
        return (
          <div key={name} className={`agent-card ${active ? '' : 'idle'}`}>
            <div className="agent-head"><AgentBadge agent={name} /><b>{AGENT_LABEL[name]}</b>
              {verdict && <span className={`verdict ${/missed|should/.test(verdict) ? 'bad' : /order|helped/.test(verdict) ? 'warn' : 'ok'}`}>{verdict}</span>}
            </div>
            {active ? <>
              {said && <p className="said">“{firstSentences(said, 2)}”</p>}
              <div className="tools">{Object.entries(a!.tools_used).map(([t, n]) =>
                <span key={t} className="tool">{t}{n > 1 ? ` ×${n}` : ''}</span>)}</div>
              {a!.asked_others.length > 0 && <div className="asked">Asked: {[...new Set(a!.asked_others.map(x => AGENT_LABEL[x.to]))].join(', ')}</div>}
              {a!.errors > 0 && <div className="asked warn-text">{a!.errors} refused or failed step{a!.errors > 1 ? 's' : ''} (safety checks)</div>}
            </> : <p className="said muted">{expected?.per_agent[name]?.stay_out_reason ?? 'Not needed on this ticket.'}</p>}
          </div>
        )
      })}
    </div>
  )
}

/** Review, then approve or reject, the payments / orders / discounts this ticket queued. */
export function ApprovalDesk({ approvals, onDecided, ticketId, ticketStatus, onClosed }: {
  approvals: ApprovalBrief[]
  onDecided: (r: DecisionResult) => void
  ticketId?: number
  ticketStatus?: string
  onClosed?: () => void
}) {
  const [open, setOpen] = useState<ApprovalView | null>(null)
  const [name, setName] = useState(() => localStorage.getItem('cc-approver') ?? '')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<DecisionResult | null>(null)
  const [closeNote, setCloseNote] = useState('')
  const [closed, setClosed] = useState(false)

  useEffect(() => { localStorage.setItem('cc-approver', name) }, [name])
  const pending = approvals.filter(a => a.status === 'pending')
  const decided = approvals.filter(a => a.status !== 'pending')

  async function review(id: number) {
    setError(null); setResult(null); setNote('')
    try { setOpen(await api.approval(id)) } catch (e) { setError((e as Error).message) }
  }
  async function decide(approve: boolean) {
    if (!open) return
    setBusy(true); setError(null)
    try {
      const r = await api.decide(open.id, { approve, decided_by: name.trim(), seen_sha256: open.content_sha256,
        note: note.trim() || undefined })
      setResult(r); setOpen(null); onDecided(r)
    } catch (e) {
      setError(e instanceof ApiError && e.status === 422 ? 'Enter your name (at least 2 letters).' : (e as Error).message)
    } finally { setBusy(false) }
  }

  async function closeTicket() {
    if (ticketId == null) return
    setBusy(true); setError(null)
    try { await api.close(ticketId, { decided_by: name.trim(), note: closeNote.trim() }); setClosed(true); onClosed?.() }
    catch (e) { setError(e instanceof ApiError && e.status === 422 ? 'Enter your name and a short note.' : (e as Error).message) }
    finally { setBusy(false) }
  }
  const canClose = !pending.length && !open && (ticketStatus === 'waiting_on_human' || ticketStatus === 'in_progress')

  const d = (open?.details ?? {}) as Record<string, unknown>
  return (
    <div className="desk">
      {result && (
        <div className="decided-banner" role="status">
          <b>{result.approval.status === 'approved' ? 'Approved' : 'Rejected'}: approval #{result.approval.id}</b>
          <div className="balance-after">Checking balance now <span>{money(result.checking_balance)}</span></div>
          {result.tickets_resolved.length > 0 && <div className="resolved-note">Ticket {result.tickets_resolved.join(', ')} is now resolved ✓</div>}
        </div>
      )}
      {!pending.length && !open && !canClose && <p className="empty">Nothing waiting for your approval on this ticket.</p>}
      {canClose && !closed && (
        <div className="review">
          <h4>Nothing to approve — close this ticket?</h4>
          <p className="seal">The team left it with you but queued no payment, order or discount. Close it once you've handled it.</p>
          <label>Your name<input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Store manager" /></label>
          <label>Note<input value={closeNote} onChange={e => setCloseNote(e.target.value)} maxLength={500} placeholder="e.g. Reviewed the cash plan" /></label>
          <div className="review-actions">
            <button className="btn primary" disabled={busy || name.trim().length < 2 || closeNote.trim().length < 3} onClick={closeTicket}>Close ticket</button>
          </div>
        </div>
      )}
      {closed && <div className="decided-banner" role="status"><b>Ticket {ticketId} closed</b><div className="resolved-note">Resolved ✓</div></div>}
      {pending.map(a => (
        <div key={a.id} className={`approval ${open?.id === a.id ? 'active' : ''}`}>
          <div className="approval-top"><span className="kind">{KIND[a.kind] ?? a.kind}</span>
            <span className="amount">{money(a.amount)}</span></div>
          <div className="approval-summary">{a.summary}</div>
          <div className="approval-meta">#{a.id} · prepared by {AGENT_LABEL[a.requested_by] ?? a.requested_by}</div>
          {open?.id !== a.id && <button className="btn ghost" onClick={() => review(a.id)}>Review</button>}
        </div>
      ))}
      {open && (
        <div className="review" aria-label={`Review approval ${open.id}`}>
          <h4>Reviewing #{open.id} · {KIND[open.kind] ?? open.kind}</h4>
          <dl>
            <dt>Amount</dt><dd>{money(open.amount)}</dd>
            {typeof d.reason === 'string' && <><dt>Why</dt><dd>{plain(d.reason)}</dd></>}
            <dt>Checking now</dt><dd>{money(open.checking_balance_now)}</dd>
            <dt>Waiting for approval (all)</dt><dd>{money(open.payments_waiting_for_approval)}</dd>
            {typeof d.checking_after_all_pending === 'number' &&
              <><dt>Checking if all pending are paid</dt><dd>{money(d.checking_after_all_pending)}</dd></>}
          </dl>
          <label>Your name<input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Store manager" /></label>
          <label>Note (optional)<input value={note} onChange={e => setNote(e.target.value)} maxLength={500} /></label>
          <div className="review-actions">
            <button className="btn primary" disabled={busy || name.trim().length < 2} onClick={() => decide(true)}>Approve</button>
            <button className="btn danger" disabled={busy || name.trim().length < 2} onClick={() => decide(false)}>Reject</button>
            <button className="btn ghost" onClick={() => setOpen(null)}>Cancel</button>
          </div>
          <p className="seal">Sealed {open.content_sha256.slice(0, 12)}… · if this request changes before you decide, it will be refused.</p>
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      {decided.length > 0 && <div className="history">{decided.map(a =>
        <div key={a.id} className={`hist s-${a.status}`}>#{a.id} {KIND[a.kind] ?? a.kind} {money(a.amount)} · {a.status} by {a.decided_by}</div>)}</div>}
    </div>
  )
}

/** The playbook's expected flow vs what the team did (Problem 6), for this ticket. */
export function ExpectedFlow({ expected }: { expected: Expected | null }) {
  if (!expected) return null
  const f = expected.first_agent
  return (
    <div className="expected">
      <p><b>Boss's job:</b> read and understand the ticket, decide its priority, call the agent who can solve it first, hand out the rest in order, and make the final call.</p>
      <p>First call should go to <b>{AGENT_LABEL[f.expected]}</b> — {f.why}.{' '}
        {expected.run_id && (f.actual === f.expected
          ? <span className="ok-text">✓ It did.</span>
          : <span className="bad-text">✗ It called {f.actual ? AGENT_LABEL[f.actual] : 'no one'} first.</span>)}</p>
      <ol className="flow">
        {expected.flow.map(s => {
          const mark = !expected.run_id ? '·' : s.done ? (s.in_order === false ? '⚠' : '✓') : s.required ? '✗' : '○'
          return <li key={s.id} className={`m-${mark === '✓' ? 'ok' : mark === '⚠' ? 'warn' : mark === '✗' ? 'bad' : 'opt'}`}>
            <span className="mk">{mark}</span><AgentBadge agent={s.agent} small /><span>{s.action}{s.required ? '' : ' (optional)'}</span></li>
        })}
      </ol>
      {expected.run_id && <p className="score-line">Playbook match: <b>{expected.score.done}/{expected.score.required}</b> required steps
        {expected.score.out_of_order ? ` · ${expected.score.out_of_order} out of order` : ''}</p>}
    </div>
  )
}

export function Drafts({ messages }: { messages: CustomerMessage[] }) {
  if (!messages.length) return <p className="empty">No customer message on this ticket.</p>
  return <>{messages.map(m => (
    <div key={m.id} className="draft">
      <div className="draft-top"><b>To {m.to_name}</b><span className="pill s-open">{m.status}</span></div>
      <div className="draft-subject">{m.subject}</div>
      <p className="draft-body">{m.body}</p>
      <div className="seal">Passed the confidentiality and figure checks · a person sends it, never an agent</div>
    </div>
  ))}</>
}
