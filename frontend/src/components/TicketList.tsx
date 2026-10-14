import { AGENT_LABEL, type PriorityRow, type TicketRow } from '../api'

const TYPE: Record<string, { label: string; glyph: string }> = {
  customer_order: { label: 'Customer order', glyph: '🛍' },
  rent_notice: { label: 'Rent notice', glyph: '🏠' },
  price_override: { label: 'Discount request', glyph: '🏷' },
  restock: { label: 'Restock', glyph: '📦' },
  vendor_followup: { label: 'Vendor follow-up', glyph: '🚚' },
  facilities_issue: { label: 'Facilities', glyph: '🔧' },
  cash_issue: { label: 'Cash risk', glyph: '💵' },
  other: { label: 'Other', glyph: '📝' },
}

export const STATUS_LABEL: Record<string, string> = {
  open: 'Open', in_progress: 'In progress', waiting_on_human: 'Needs your approval', resolved: 'Resolved',
}
const WHY_UNRESOLVED: Record<string, string> = {
  open: 'not started', in_progress: 'in progress', waiting_on_human: 'needs your approval',
}

export function TicketList({ tickets, priority, selected, runningId, onSelect }: {
  tickets: TicketRow[]
  priority: PriorityRow[]
  selected: number | null
  runningId: number | null
  onSelect: (id: number) => void
}) {
  const rank = new Map(priority.map(p => [p.ticket_id, p]))
  const open = tickets.filter(t => t.is_open)
    .sort((a, b) => (rank.get(a.id)?.rank ?? 99) - (rank.get(b.id)?.rank ?? 99))
  const done = tickets.filter(t => !t.is_open)

  const card = (t: TicketRow) => {
    const p = rank.get(t.id)
    const type = TYPE[t.type] ?? TYPE.other
    return (
      <button key={t.id} className={`ticket ${selected === t.id ? 'selected' : ''} ${t.is_open ? '' : 'delivered'}`}
              onClick={() => onSelect(t.id)} aria-pressed={selected === t.id}>
        <div className="ticket-top">
          {p ? <span className="rank" title={p.reason}>#{p.rank}</span> : <span className="rank done">✓</span>}
          <span className="glyph" aria-hidden>{type.glyph}</span>
          <span className="ticket-id">Ticket {t.id}</span>
          <span className={`tag ${t.is_open ? 'unresolved' : 'resolved'}`}>{t.is_open ? 'Unresolved' : 'Resolved ✓'}</span>
        </div>
        <div className="ticket-subject">{t.subject}</div>
        <div className="ticket-meta">{t.requester} · {type.label}</div>
        {t.is_open && <div className={`why s-${t.status}`}>{runningId === t.id ? 'Team working on it now…' : WHY_UNRESOLVED[t.status]}</div>}
        {p && <div className="ticket-first">Boss should call <b>{AGENT_LABEL[p.first_agent]}</b> first</div>}
      </button>
    )
  }

  return (
    <aside className="panel tickets" aria-label="Tickets">
      <h2>Ticket rack</h2>
      <h4 className="rack-head unresolved">Unresolved <span className="count">{open.length}</span></h4>
      <p className="hint">In the order the Boss should take them: keep the shop open, then waiting customers, then sales with no deadline.</p>
      {open.length ? open.map(card) : <p className="empty">Nothing unresolved. Every parcel is delivered.</p>}
      <h4 className="rack-head resolved">Resolved <span className="count ok">{done.length}</span></h4>
      {done.length ? done.map(card) : <p className="empty">None yet.</p>}
    </aside>
  )
}
