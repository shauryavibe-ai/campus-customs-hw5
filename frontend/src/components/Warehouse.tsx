// The Campus Customs warehouse: the Boss's glass office upstairs, four departments on
// the floor, watch lines between them, and a loading dock. Every department's state is
// worked out from the live audit events, so the scene shows what each agent is doing.

import { AGENT_LABEL, firstSentences, plain, type AgentEvent, type Expected } from '../api'
import { Avatar, type AvatarState, type Role } from './Avatars'
import { Mascot } from './Mascot'

const FLOOR: Role[] = ['inventory', 'accounting', 'facilities', 'customer_service']
const DEPT: Record<Role, { name: string; place: string }> = {
  boss: { name: "Boss's Office", place: '2nd floor · watches every department' },
  inventory: { name: 'Inventory Bay', place: 'Racks · scanner · pallets' },
  accounting: { name: 'Accounting Office', place: 'Ledgers · calculator · cash box' },
  facilities: { name: 'Facilities Workshop', place: 'Tools · leases · building plan' },
  customer_service: { name: 'Customer Service Desk', place: 'Headset · message tray' },
}
const LAMP: Record<string, string> = { idle: 'Idle', working: 'Working', done: 'Done', blocked: 'Blocked', stayout: 'Staying out', dance: 'Delivered!' }

interface Dept {
  status: 'idle' | 'working' | 'done' | 'blocked' | 'stayout'
  order?: { from: string; task: string; seq: number }
  doing?: string
  tools: [string, number][]
  said?: string
  verdict?: string
  stayOut?: string | null
}

function argsText(args?: Record<string, unknown>) {
  if (!args) return ''
  return Object.entries(args).filter(([k]) => !['reason', 'body', 'notes', 'decision', 'subject'].includes(k))
    .map(([, v]) => (typeof v === 'string' ? v : JSON.stringify(v))).join(' · ').slice(0, 70)
}

export function deriveDepartments(events: AgentEvent[], running: boolean, expected: Expected | null): Record<Role, Dept> {
  const out = {} as Record<Role, Dept>
  for (const role of ['boss', ...FLOOR] as Role[]) {
    const mine = events.filter(e => e.agent === role)
    const tools = new Map<string, number>()
    mine.filter(e => e.kind === 'tool_call').forEach(e => tools.set(e.tool!, (tools.get(e.tool!) ?? 0) + 1))
    const lastCall = [...mine].reverse().find(e => e.kind === 'tool_call')
    const lastErr = [...mine].reverse().find(e => e.kind === 'tool_error')
    const orders = events.filter(e => e.kind === 'handoff' && e.to === role)
    const replies = mine.filter(e => e.kind === 'handoff_reply')
    const lastOrder = orders.at(-1)
    const final = mine.find(e => e.kind === 'final')
    const open = orders.length > replies.length
    const active = mine.some(e => ['tool_call', 'handoff', 'handoff_reply'].includes(e.kind))
    const stayOut = expected?.per_agent[role]?.stay_out_reason ?? null
    let status: Dept['status'] = 'idle'
    if (role === 'boss' ? running : running && open) status = 'working'
    else if (active) status = 'done'
    else if (stayOut) status = 'stayout'
    if (status === 'working' && lastErr && (!lastCall || lastErr.seq > lastCall.seq)) status = 'blocked'
    out[role] = {
      status,
      order: lastOrder ? { from: lastOrder.agent, task: firstSentences(lastOrder.task, 1), seq: lastOrder.seq } : undefined,
      doing: lastCall ? `${lastCall.tool}${argsText(lastCall.args) ? ' · ' + argsText(lastCall.args) : ''}` : undefined,
      tools: [...tools.entries()],
      said: firstSentences(replies.at(-1)?.said ?? (final?.output?.decision as string | undefined), 2) || undefined,
      verdict: expected?.run_id ? expected.per_agent[role]?.verdict : undefined,
      stayOut,
    }
  }
  return out
}

function bossLine(events: AgentEvent[], running: boolean, ticketId: number | undefined, depts: Record<Role, Dept>) {
  const last = events.at(-1)
  if (!running) {
    const final = events.find(e => e.kind === 'final')
    return final ? `Final call: ${firstSentences(String(final.output?.decision ?? ''), 1)}` : `Ticket ${ticketId ?? ''} is on my desk. Dispatch when ready.`
  }
  if (!last || last.kind === 'start') return `Reading ticket ${ticketId}…`
  if (last.agent === 'boss' && last.kind === 'handoff') return `Assigning ${AGENT_LABEL[last.to!]}: ${firstSentences(last.task, 1)}`
  const busy = FLOOR.filter(r => depts[r].status === 'working')
  if (busy.length) return `Watching ${busy.map(r => AGENT_LABEL[r]).join(' & ')} work…`
  if (last.agent === 'boss' && last.kind === 'tool_call' && last.tool === 'update_ticket') return 'Making the final call…'
  return 'Reviewing what the team found…'
}

function Department({ role, d, celebrate, index }: { role: Role; d: Dept; celebrate: boolean; index: number }) {
  const avatarState: AvatarState = celebrate ? 'dance' : d.status === 'stayout' ? 'idle' : d.status
  return (
    <div className={`dept dept-${role} st-${celebrate ? 'dance' : d.status}`}>
      <div className="dept-sign"><b>{DEPT[role].name}</b><span>{DEPT[role].place}</span></div>
      <div className={`lamp l-${celebrate ? 'dance' : d.status}`} title={LAMP[d.status]}><i />{celebrate ? LAMP.dance : LAMP[d.status]}</div>
      {d.order && !celebrate && <div key={d.order.seq} className="work-order">
        <span className="wo-head">Work order from {AGENT_LABEL[d.order.from]}</span>{d.order.task}</div>}
      <div className="dept-stage">
        <div className={`decor decor-${role}`} aria-hidden />
        <Avatar role={role} state={avatarState} size={70} delay={index * 0.13} />
      </div>
      <div className="dept-info">
        {d.status === 'stayout' ? <p className="muted">{d.stayOut}</p> : <>
          {d.doing && <p className="doing"><span className="k">Doing</span>{d.doing}</p>}
          {d.tools.length > 0 && <div className="how"><span className="k">How</span>{d.tools.map(([t, n]) =>
            <span key={t} className="tool">{t}{n > 1 ? ` ×${n}` : ''}</span>)}</div>}
          {d.said && <p className="said">“{plain(d.said)}”</p>}
          {!d.doing && !d.said && <p className="muted">Waiting for a work order.</p>}
          {d.verdict && <span className={`verdict ${/missed|should/.test(d.verdict) ? 'bad' : /order|helped/.test(d.verdict) ? 'warn' : 'ok'}`}>Playbook: {d.verdict}</span>}
        </>}
      </div>
    </div>
  )
}

const CONFETTI = Array.from({ length: 46 }, (_, i) => ({
  left: (i * 37) % 100, delay: (i % 12) * 0.22, dur: 2.6 + (i % 5) * 0.35,
  color: ['#6fa8ff', '#ffffff', '#286dc0', '#a8c8ff', '#ffcc33', '#ff6fb1'][i % 6], rot: (i * 47) % 360,
}))

export function Warehouse({ events, running, expected, ticketId, resolved }: {
  events: AgentEvent[]; running: boolean; expected: Expected | null; ticketId?: number; resolved: boolean
}) {
  const depts = deriveDepartments(events, running, expected)
  const boss = depts.boss
  const celebrate = resolved && !running
  const lit = FLOOR.map(r => depts[r].status === 'working')
  return (
    <div className={`warehouse ${celebrate ? 'celebrate' : ''} ${running ? 'busy' : ''}`} aria-label="Campus Customs warehouse">
      <div className="roof"><span>CAMPUS CUSTOMS</span><small>WAREHOUSE · EST. YALE</small></div>

      <div className="floor2">
        <div className={`office st-${celebrate ? 'dance' : boss.status}`}>
          <div className="office-glass">
            <div className="office-sign"><b>{DEPT.boss.name}</b><span>{DEPT.boss.place}</span></div>
            <div className="office-room">
              <div className="boss-seat"><Avatar role="boss" state={celebrate ? 'dance' : boss.status === 'working' ? 'working' : 'idle'} size={64} /></div>
              <div className="boss-desk"><div className="monitor"><i /></div></div>
              <div className="boss-bubble" aria-live="polite">{celebrate ? `Ticket ${ticketId} delivered — great work, team!` : bossLine(events, running, ticketId, depts)}</div>
            </div>
          </div>
          {(boss.tools.length > 0 || boss.verdict) && <div className="office-how">
            {boss.tools.map(([t, n]) => <span key={t} className="tool">{t}{n > 1 ? ` ×${n}` : ''}</span>)}
            {boss.verdict && <span className={`verdict ${/missed|should/.test(boss.verdict) ? 'bad' : 'ok'}`}>Playbook: {boss.verdict}</span>}
          </div>}
        </div>
        <div className="railing" aria-hidden />
      </div>

      <svg className="watch-lines" viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden>
        {[12.5, 37.5, 62.5, 87.5].map((x, i) => <line key={x} x1="50" y1="0" x2={x} y2="40" className={lit[i] ? 'on' : ''} />)}
      </svg>

      <div className="floor1">
        {FLOOR.map((role, i) => <Department key={role} role={role} d={depts[role]} celebrate={celebrate} index={i + 1} />)}
      </div>

      <div className="dock">
        <div className={`dock-door ${celebrate ? 'open' : ''}`} aria-hidden><i /><i /><i /><i /><i /></div>
        <div className={`courier ${celebrate ? 'leaving' : ''}`}><Mascot mood={running ? 'running' : celebrate ? 'done' : 'idle'} size={86} /></div>
        <div className="dock-label">LOADING DOCK · {celebrate ? 'Out for delivery!' : running ? 'Packing…' : 'Ready'}</div>
      </div>

      {celebrate && <>
        <div className="confetti" aria-hidden>{CONFETTI.map((c, i) =>
          <i key={i} style={{ left: `${c.left}%`, background: c.color, animationDelay: `${c.delay}s`, animationDuration: `${c.dur}s`, transform: `rotate(${c.rot}deg)` }} />)}</div>
        <div className="party-banner" role="status">🎉 Ticket {ticketId} delivered! The whole warehouse is celebrating.</div>
      </>}
    </div>
  )
}
