import { useState } from 'react'
import { AGENT_LABEL, firstSentences, plain, type AgentEvent } from '../api'

export function AgentBadge({ agent, small = false }: { agent: string; small?: boolean }) {
  const initials: Record<string, string> = { boss: 'B', inventory: 'IN', accounting: 'AC', facilities: 'FA',
    customer_service: 'CS', human: 'YOU', system: 'SYS' }
  return <span className={`badge a-${agent} ${small ? 'small' : ''}`} title={AGENT_LABEL[agent] ?? agent}>
    {initials[agent] ?? agent.slice(0, 2).toUpperCase()}</span>
}

function shortArgs(args?: Record<string, unknown>) {
  if (!args) return ''
  return Object.entries(args).filter(([k]) => k !== 'reason' && k !== 'body' && k !== 'notes')
    .map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`).join(', ').slice(0, 90)
}

function Said({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  const short = firstSentences(text, 2)
  const full = plain(text)
  return (
    <div className="bubble">
      {open ? full : short}
      {full.length > short.length + 5 &&
        <button className="link" onClick={() => setOpen(!open)}>{open ? 'less' : 'more'}</button>}
    </div>
  )
}

/** The live "delivery route": every step the team takes on a ticket, Boss first. */
export function DeliveryRoute({ events, running }: { events: AgentEvent[]; running: boolean }) {
  const steps = events.filter(e => !['tool_result', 'run_end'].includes(e.kind))
  const end = events.find(e => e.kind === 'run_end')
  // Who is working right now: anyone handed a task who hasn't replied yet.
  const waiting = new Set<string>()
  for (const e of events) {
    if (e.kind === 'handoff' && e.to) waiting.add(e.to)
    if (e.kind === 'handoff_reply') waiting.delete(e.agent)
  }
  const busy = running ? [...waiting].pop() ?? 'boss' : null

  if (!steps.length && !running) return <p className="empty">No run yet. Dispatch the team to watch the route unfold.</p>

  return (
    <ol className="route">
      {steps.map(e => {
        const indent = { marginLeft: `${Math.min(e.depth ?? 0, 3) * 26}px` }
        switch (e.kind) {
          case 'start':
            return <li key={e.seq} className="stop start" style={indent}><AgentBadge agent="boss" />
              <div><b>Boss picks up the ticket</b><span className="sub">{e.time.slice(11, 19)}</span></div></li>
          case 'handoff':
            return <li key={e.seq} className="stop handoff" style={indent}><AgentBadge agent={e.agent} />
              <div><b>{AGENT_LABEL[e.agent]} → {AGENT_LABEL[e.to ?? ''] ?? e.to}</b>
                <div className="task">{firstSentences(e.task, 2)}</div></div></li>
          case 'handoff_reply':
            return <li key={e.seq} className="stop reply" style={indent}><AgentBadge agent={e.agent} />
              <div><b>{AGENT_LABEL[e.agent]} replies to {AGENT_LABEL[e.to ?? ''] ?? e.to}</b><Said text={e.said ?? ''} /></div></li>
          case 'tool_call':
            return <li key={e.seq} className="parcel" style={indent}>
              <span className="box" aria-hidden>▣</span><AgentBadge agent={e.agent} small />
              <code>{e.tool}</code><span className="args">{shortArgs(e.args)}</span></li>
          case 'tool_error':
          case 'handoff_refused':
            return <li key={e.seq} className="stop warn" style={indent}><AgentBadge agent={e.agent} small />
              <div>{plain(e.summary).slice(0, 220)}</div></li>
          case 'final':
            return <li key={e.seq} className="stop final"><AgentBadge agent="boss" />
              <div><b>Boss's final call</b><Said text={String((e.output ?? {}).decision ?? e.summary)} /></div></li>
          default:
            return null
        }
      })}
      {busy && <li className="stop working"><AgentBadge agent={busy} /><div><b>{AGENT_LABEL[busy]} is on it</b>
        <span className="dots"><i /><i /><i /></span></div></li>}
      {end && !running && <li className="stop finish">Route complete · {(end.summary ?? '').replace('run ended ', '')}</li>}
    </ol>
  )
}
