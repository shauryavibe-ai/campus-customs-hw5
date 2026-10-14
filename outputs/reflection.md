# Reflection: Campus Customs agent team

The agents performed really well. The expected plan was followed clearly: on every ticket the Boss read the ticket, called the right agent first and made the final call, every required step was done (27 of 27), and the tools we designed for each agent were the ones they actually used. The one exception to the order is described below.

This was partly because we kept conversing and adding things on the fly across the problems. Each time a run showed a gap, we closed it in the next prompt instead of designing everything up front.

One step ran out of order. On ticket 103 (the hoodie discount) the Boss sent Accounting its discount task at the same moment as it asked Inventory to check stock (audit entries 589 and 590), instead of waiting for the stock answer. Inventory's answer (8 of 20 in stock) went only to the Boss, so Accounting priced the discount without it. The Boss also asked for "the lowest permissible unit price / largest discount", so Accounting queued the margin-floor price of $31.43 a hoodie (about 46% off list). The ticket was still resolved, but this is the clearest thing to improve: the Boss should wait for Inventory's stock answer before asking Accounting about a discount, pass that answer along, and ask for a modest discount rather than the largest one allowed.

## Evidence from the run

| Ticket | Playbook steps done | Right agent called first | Status |
|---|---|---|---|
| 101 Bulldog tee | 1/8 | ✓ inventory | resolved |
| 102 Rent due | 0/6 | ✗ facilities | resolved |
| 103 Bulk hoodie discount | 0/13 | ✗ inventory | resolved |

Tools each agent used: **Boss**: ; **Inventory**: check_stock.

Cash: ending checking $3,400.00 matches cash_accounts.

## Why it worked: building it in conversation

- **Problem 3–4:** Per-agent MCP servers and tools, tested one by one through mcp.json before any agent used them
- **Problem 5:** The Boss became the sole decision maker with a live team roster (capability and capacity) — and Inventory now hands purchase orders to Accounting instead of stopping at a vendor name
- **Problem 5:** Guard rails added after live runs: no ping-pong hand-offs, one customer draft per ticket, restock sized to the shortfall, an automatic figure check, create_ticket, an append-only audit trail
- **Problem 5:** Safety: confidentiality check on customer drafts, untrusted ticket text, API key isolation, sealed approvals — proven with a live prompt-injection test
- **Problem 6–9:** The playbook (expected vs actual), the backend, the warehouse dashboard, auto-resolve after the last approval, a human close-ticket step, and the cash reconciliation

## Next: three Campus Customs problems these agents can take on

### 1. Receiving inventory — and paying only for what arrived

Goods come in against a purchase order: they must be counted into stock, and the payment must match what was actually received (short, damaged or extra units change what is owed).

1. Inventory counts the delivery against the purchase order and adds the received units to stock.
2. Inventory hands Accounting the received quantities (ask_agent).
3. Accounting works out the amount payable = received units × agreed unit cost, and queues only that payment for approval; any shortfall is flagged.
4. The Boss makes the call on disputes; the human approves the payment.

- Tools we already have: receive_purchase_order, check_stock, prepare_invoice_payment (partial amounts), ask_agent, approvals
- To add: record_goods_receipt (qty received, damaged, date), payable_for_receipt (received × unit cost vs invoice)

### 2. Reconciling PO, GRN and invoice (three-way match)

Before any vendor is paid, the purchase order (what we ordered), the goods received note (what arrived) and the vendor invoice (what they bill) must agree on items, quantities and prices.

1. Accounting runs the match line by line when a vendor invoice arrives.
2. Inventory confirms the goods received note for the same order.
3. If all three agree, Accounting queues the payment; if not, it holds the payment and raises a vendor_followup ticket with the exact differences.
4. The Boss decides what to accept; the human approves.

- Tools we already have: purchase_orders, receive_purchase_order, list_invoices, prepare_invoice_payment, create_ticket (vendor_followup), the automatic figure check
- To add: a goods-received table, a purchase-order link on invoices, three_way_match (per-line differences)

### 3. Back-in-stock messages for waiting customers

When stock arrives for an item customers were waiting on (like Tauhid's size-S tee), each of them should get a message that it's back — without anyone checking by hand.

1. When goods are received, Inventory finds open customer orders that were short for that SKU and size.
2. Inventory tells Customer Service who is waiting and how many units are now in stock (ask_agent).
3. Customer Service drafts a message to each waiting customer with only confirmed, public facts.
4. The confidentiality and figure checks run on every draft; a person sends them.

- Tools we already have: find_shortfalls (ticket shortfalls), check_stock, receive_purchase_order, ask_agent, draft_customer_message, confidentiality and figure checks
- To add: customers_waiting_for (open orders for a SKU and size), a trigger after receive_purchase_order
