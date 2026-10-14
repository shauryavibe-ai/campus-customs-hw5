// Typed client for the FastAPI backend (hw 5/backend/main.py), called directly at
// http://localhost:8000 (override with VITE_API_BASE). The backend allows this page's origin via CORS.

export const API_BASE = (import.meta.env.VITE_API_BASE ?? 'http://localhost:8000').replace(/\/$/, '')

export type AgentName = 'boss' | 'inventory' | 'accounting' | 'facilities' | 'customer_service'
export type TicketStatus = 'open' | 'in_progress' | 'waiting_on_human' | 'resolved'

export interface Health {
  ok: boolean
  business_date: string
  database: string
  agents: AgentName[]
  runs_in_progress: number
  audit: { ok: boolean; entries: number; problem: string | null }
}

export interface TicketRow {
  id: number
  type: string
  requester: string
  subject: string
  status: TicketStatus
  is_open: boolean
  running: boolean
  created_at: string
}

export interface PriorityRow {
  rank: number
  ticket_id: number
  subject: string
  type: string
  tier: number
  reason: string
  detail: string
  first_agent: AgentName | 'boss'
  first_agent_why: string
}

export interface ApprovalBrief {
  id: number
  kind: string
  ref_id: number
  amount: number
  summary: string
  status: 'pending' | 'approved' | 'rejected'
  requested_by: string
  decided_by: string | null
}

export interface CustomerMessage {
  id: number
  ticket_id?: number
  to_name: string
  subject: string
  body: string
  status: string
  drafted_by: string
  sealed_ok?: boolean
}

export interface TicketDetail {
  business_date: string
  safety: { untrusted_fields: string[]; rule: string; injection_flags: string[] }
  ticket: {
    id: number
    type: string
    requester: string
    subject: string
    sku: string | null
    size: string | null
    qty: number | null
    lease_id: number | null
    invoice_id: number | null
    status: TicketStatus
    notes: string | null
    created_at: string
  }
  routing: { lead: string; helpers: string[] } | null
  invoice: { id: number; vendor: string; amount: number; due_date: string; status: string; description: string } | null
  lease: { id: number; space_name: string; landlord: string; monthly_rent: number; next_due: string } | null
  approvals: ApprovalBrief[]
  purchase_orders: { id: number; qty: number; total: number; status: string; expected_date: string }[]
  customer_messages: CustomerMessage[]
  status_history: { old_status: string; new_status: string; decision: string; agent: string; created_at: string }[]
  is_open: boolean
  running: boolean
}

export interface AgentEvent {
  seq: number
  time: string
  run_id?: string
  ticket_id?: number | null
  agent: string
  depth?: number
  kind: string
  tool?: string
  to?: string
  status?: string
  summary: string
  args?: Record<string, unknown>
  task?: string
  said?: string
  output?: Record<string, unknown>
}

export interface AgentActivity {
  agent: AgentName
  tools_used: Record<string, number>
  tool_calls: number
  errors: number
  said: { seq: number; time: string; to: string; text: string | null }[]
  asked_others: { seq: number; to: string; task: string }[]
  last_active: string | null
}

export interface Outcome {
  ticket_id: number
  understanding: string
  assignments: { agent: string; task: string; why: string }[]
  lead_agent: string
  agents_consulted: string[]
  final_status: TicketStatus
  decision: string
  pending_approvals: string[]
  customer_message_ids: number[]
}

export interface RunJob {
  job_id: string
  ticket_id: number
  status: 'running' | 'done' | 'failed'
  started_at: string
  run_id: string | null
  outcome: Outcome | null
  error: string | null
  handoffs: number | null
  usage: { requests: number; input_tokens: number; output_tokens: number } | null
  seconds?: number
}

export interface FlowStep {
  id: string
  agent: string
  action: string
  why: string
  required: boolean
  done: boolean
  in_order: boolean | null
  after: string[]
  evidence: string[]
}

export interface Expected {
  ticket_id: number
  run_id: string | null
  score: { done: number; required: number; out_of_order: number }
  first_agent: { expected: string; why: string; actual: string | null }
  flow: FlowStep[]
  per_agent: Record<string, { verdict: string; extra_actions: string[]; stay_out_reason: string | null }>
}

export interface ApprovalView extends ApprovalBrief {
  details: Record<string, unknown>
  content_sha256: string
  created_at: string
  checking_balance_now: number
  payments_waiting_for_approval: number
}

export interface DecisionResult {
  approval: ApprovalBrief
  outcome: Record<string, unknown>
  tickets_resolved: number[]
  checking_balance: number
}

export interface Cash {
  checking_balance: number
  accounts: { name: string; balance: number; date: string }[]
  payments_waiting_for_approval: number
  checking_after_pending: number
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  // Content-Type only when there is a JSON body, so plain GETs don't need a CORS preflight.
  const headers = init?.body ? { 'Content-Type': 'application/json' } : undefined
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = typeof body.detail === 'string' ? body.detail : `Request failed (${res.status})`
    throw new ApiError(res.status, detail)
  }
  return body as T
}

const post = <T,>(path: string, data?: unknown) =>
  call<T>(path, { method: 'POST', body: data === undefined ? undefined : JSON.stringify(data) })

export const api = {
  health: () => call<Health>('/api/health'),
  tickets: () => call<{ tickets: TicketRow[]; open_count: number }>('/api/tickets'),
  priority: () => call<{ priority: PriorityRow[] }>('/api/priority'),
  ticket: (id: number) => call<TicketDetail>(`/api/tickets/${id}`),
  expected: (id: number) => call<Expected>(`/api/tickets/${id}/expected`),
  run: (id: number) => post<RunJob>(`/api/tickets/${id}/run`),
  job: (jobId: string) => call<RunJob>(`/api/runs/${jobId}`),
  runs: () => call<{ runs: RunJob[] }>('/api/runs'),
  events: (q: { ticket_id?: number; run_id?: string; after_seq?: number; limit?: number; kind?: string }) => {
    const p = new URLSearchParams()
    Object.entries(q).forEach(([k, v]) => v !== undefined && p.set(k, String(v)))
    return call<{ events: AgentEvent[]; last_seq: number }>(`/api/events?${p}`)
  },
  agents: (runId: string) => call<{ agents: AgentActivity[] }>(`/api/agents?run_id=${encodeURIComponent(runId)}`),
  approvals: () => call<{ approvals: ApprovalBrief[]; pending_payment_total: number }>('/api/approvals'),
  approval: (id: number) => call<ApprovalView>(`/api/approvals/${id}`),
  decide: (id: number, body: { approve: boolean; decided_by: string; seen_sha256: string; note?: string }) =>
    post<DecisionResult>(`/api/approvals/${id}/decision`, body),
  close: (id: number, body: { decided_by: string; note: string }) =>
    post<{ ticket_id: number; new_status: string; decision: string }>(`/api/tickets/${id}/resolve`, body),
  cash: () => call<Cash>('/api/cash'),
  reset: () => post<{ reset: boolean; backup: string; checking_balance: number; open_tickets: number }>('/api/reset',
    { confirm: 'RESET' }),
}

export const AGENT_LABEL: Record<string, string> = {
  boss: 'Boss', inventory: 'Inventory', accounting: 'Accounting', facilities: 'Facilities',
  customer_service: 'Customer Service', human: 'You', system: 'System',
}

export const money = (n: number | undefined | null) =>
  n == null ? '—' : n.toLocaleString('en-US', { style: 'currency', currency: 'USD' })

// Agents write light markdown (**bold**); show it as plain text.
export const plain = (s: string | null | undefined) => (s ?? '').replace(/\*\*/g, '').replace(/`/g, '')

export const firstSentences = (s: string | null | undefined, n = 2) => {
  const parts = plain(s).split(/(?<=[.!?])\s+/).filter(Boolean)
  return parts.slice(0, n).join(' ')
}
