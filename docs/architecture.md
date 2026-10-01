# Architecture

## The pieces

```
  ┌──────────────────────┐        ┌──────────────────────┐
  │  Expo staff app      │        │  Django Admin        │
  │  (front desk, mgr)   │        │  (ops, issue queue)  │
  └──────────┬───────────┘        └──────────┬───────────┘
             │ REST + JWT                    │ session
             ▼                               ▼
  ┌─────────────────────────────────────────────────────┐        ┌────────────────┐
  │                 Django + DRF (web)                  │◀───────│ Razorpay       │
  │  accounts · customers · billing · payments ·        │webhooks│ (or sandbox)   │
  │  webhooks · ledger · reconciliation · audit ·       │───────▶│                │
  │  reports · sandbox                                  │  API   └────────────────┘
  └──────┬──────────────────┬───────────────────────────┘                ▲
         │                  │ enqueue                                     │ list payments,
         ▼                  ▼                                             │ refunds, settlements
  ┌─────────────┐   ┌──────────────────────────┐                         │
  │ PostgreSQL  │◀──│ Celery worker + Beat     │─────────────────────────┘
  └─────────────┘   │ (Redis broker)           │  02:00 IST reconciliation
                    └──────────────────────────┘
```

- The app and Admin talk **directly** to Django.
- The gateway **pushes** webhooks; Django stores them raw and hands them to Celery.
- The reconciliation job **pulls** from the gateway daily and compares with our records.
- `PAYMENT_GATEWAY=fake` swaps Razorpay for the in-repo sandbox (same interface, same
  webhooks), so the whole system runs with no external accounts.

## Layers inside the backend

```
views / serializers   thin: parse input, check permissions, call a service, shape output
        │
services              all business rules; one transaction per command; locks; audit
        │
state_machine         the only code that changes Invoice.status
        │
models + DB           unique constraints, check constraints, append-only trigger
```

Rules of thumb:
- A view never writes a model directly; it calls `services.*`.
- Every money change runs inside `transaction.atomic()` with the **invoice row locked**
  (`select_for_update`), in the order WebhookEvent → Invoice → Payment/Refund.
- Every externally caused change is idempotent, keyed by something unique in the database
  (`gateway_event_id`, `gateway_payment_id`, `idempotency_key`, `receipt`).

## Money invariants (checked nightly)

For every invoice:

```
total          = subtotal − discount + tax                      (DB check constraint)
amount_paid    = Σ payments where status = captured
amount_refunded= Σ refunds  where status = processed            (≤ amount_paid: DB check)
ledger credits − ledger debits = amount_paid − amount_refunded
```

`reconciliation.engine.check_invoice_drift` raises an `invoice_drift` issue if any of these
don't hold.

## Flow 1: collect a payment

```
App                        Django                          Gateway              Customer
 │ POST /invoices/7/collect  │                                │                     │
 │  Idempotency-Key: K       │ lock invoice, check due,       │                     │
 │──────────────────────────▶│ save PaymentAttempt(pending,   │                     │
 │                           │   reference_id=R) and commit   │                     │
 │                           │ create link (reference_id=R) ──▶│  (no DB lock held)  │
 │                           │◀──────────────── plink, url ───│                     │
 │◀── 201 {short_url, …} ────│ attempt → created              │                     │
 │ show QR, poll GET /7 (3s) │                                │◀── scans, pays ─────│
 │                           │◀── payment.captured (signed) ──│                     │
 │                           │ verify HMAC → insert WebhookEvent (unique event id)  │
 │                           │ → 200 → Celery: lock invoice, Payment (unique id),   │
 │                           │   ledger credit, state machine → paid, audit         │
 │ GET /7 → status: paid ✓   │                                │                     │
```

Code: `payments/services.py::collect` → `webhooks/views.py::razorpay_webhook` →
`webhooks/tasks.py::process_webhook_event` → `webhooks/handlers.py` →
`payments/services.py::apply_payment_captured` → `billing/state_machine.py`.

## Flow 2: refund

```
desk: POST /payments/{id}/refunds/     → Refund(requested)   (amount reserved)
mgr:  POST /refunds/{id}/approve/      → Refund(approved) → on commit: Celery process_refund
task: claim approved→processing; look up gateway refunds by receipt; else create one
gateway response "processed"  ─┐
webhook refund.processed      ─┴→ apply_refund_processed (idempotent): ledger debit,
                                   amount_refunded, state machine → (partially_)refunded
```

Code: `payments/refunds.py`, `payments/tasks.py::process_refund`,
`webhooks/handlers.py::handle_refund_processed`.

## Flow 3: nightly reconciliation

`reconciliation/engine.py::run_reconciliation(day)`:

1. `sweep_open_links`: ask the gateway about our open or expired links, and apply payments we missed.
2. `check_gateway_payments`: the gateway's payments for the day vs ours (missing, amount, refunds).
3. `check_local_payments`: ours vs the gateway (missing at the gateway, status).
4. `check_settlements`: each settlement vs Σ(our payment − our fee) − Σ(our refunds).
5. `check_invoice_drift`: the invariants above.
6. `check_stuck_events`: webhooks unprocessed for more than 1 hour.

Recoveries go through the same `apply_payment_captured` the webhook uses. Issues are deduped
by a partial unique index on (kind, gateway_ref) while open, so re-runs are safe.

## Code tour

### Backend (`backend/`)

| Path | What it's for |
|---|---|
| `config/settings/{base,dev,test,prod}.py` | Settings per environment. `dev` falls back to SQLite and the sandbox; `prod` adds TLS, Sentry and the Redis cache |
| `config/celery.py` | Celery app; tasks autodiscovered from each app's `tasks.py` |
| `config/urls.py` | `/api/v1/…`, `/webhooks/razorpay/`, `/sandbox/`, `/admin/`, `/healthz` |
| `apps/common/exceptions.py` | Domain errors + the `{"error": {code, message}}` envelope |
| `apps/common/money.py` | `percent_of` (bps, half-up) and `allocate` (largest remainder): integer paise only |
| `apps/common/management/commands/seed.py` | Demo centres, staff, services, invoices |
| `apps/accounts/models.py` | `Centre`, `Staff` (role: front_desk / manager) |
| `apps/accounts/permissions.py` | `IsCentreStaff`, `IsManager`, `CentreScopedMixin` (404 across centres) |
| `apps/customers/` | Customers per centre; phone normalised to +91… |
| `apps/billing/models.py` | `Service`, `Invoice`, `InvoiceItem`, `InvoiceCounter` + check constraints |
| `apps/billing/state_machine.py` | The transition table; the only writer of `Invoice.status` |
| `apps/billing/services.py` | `compute_totals`, create/update draft, issue (gap-free number), cancel |
| `apps/billing/views.py` | Invoice API incl. `collect`, `cash`, `preview`, `timeline` actions |
| `apps/billing/timeline.py` | Human-readable history from the audit log |
| `apps/payments/models.py` | `PaymentAttempt`, `Payment`, `Refund` |
| `apps/payments/gateway.py` | `RazorpayClient` (the only HTTP to Razorpay) and `get_gateway()` |
| `apps/payments/services.py` | `collect` (idempotent, crash-recoverable), `apply_payment_*`, cash |
| `apps/payments/refunds.py` | Request/approve/reject, `submit_to_gateway`, `apply_refund_*` |
| `apps/payments/tasks.py` | Celery: refund submission, cancelling superseded links |
| `apps/webhooks/views.py` | Verify HMAC → store raw → enqueue → 200 |
| `apps/webhooks/handlers.py` | One idempotent handler per event type |
| `apps/webhooks/services.py` | `process_event` (locks the event row), failure bookkeeping, give-up |
| `apps/webhooks/tasks.py` | Retries with backoff; slower backoff for out-of-order events |
| `apps/webhooks/management/commands/replay_webhooks.py` | Re-run stored events |
| `apps/ledger/models.py` + `migrations/0003_…` | Append-only ledger; Postgres trigger blocks UPDATE/DELETE |
| `apps/reconciliation/engine.py` | The nightly checks above |
| `apps/reconciliation/services.py` | `raise_issue` (idempotent) |
| `apps/reconciliation/admin.py` | Issue queue with "resolve (needs a note)" action |
| `apps/audit/` | Append-only `AuditLog` + `audit()` helper, written in the same transaction |
| `apps/reports/views.py` | Day-close report |
| `apps/sandbox/` | Fake gateway: state tables, Razorpay-shaped client, payment page, webhook delivery |
| `tests/` | pytest; `test_concurrency.py` needs Postgres (threads + real row locks) |
| `scripts/local_postgres.py` | Local Postgres without Docker on a fixed port (start/stop/status) |
| `bin/start-web.sh`, `bin/start-worker.sh` | Process entry points for Render/Docker |

### Mobile (`mobile/`)

| Path | What it's for |
|---|---|
| `src/app/_layout.tsx` | Providers: React Query, auth, Sentry; query retry policy |
| `src/app/login.tsx` | Sign in |
| `src/app/(app)/_layout.tsx` | Guard: signed-in only; the stack of invoice screens |
| `src/app/(app)/(tabs)/…` | Today, Customers, Approvals (managers), Day close |
| `src/app/(app)/invoice/new.tsx` | Build an invoice; totals from `/invoices/preview/` |
| `src/app/(app)/invoice/[id]/collect.tsx` | QR, polling, expiry and renewal, success |
| `src/app/(app)/invoice/[id]/index.tsx` | Detail, timeline, issue, cash, cancel |
| `src/app/(app)/invoice/[id]/refund.tsx` | Refund request |
| `src/api/client.ts` | fetch wrapper: JWT, single-flight refresh, error envelope, timeouts |
| `src/api/hooks.ts` | One React Query hook per endpoint; cache invalidation after writes |
| `src/auth/` | Session state; tokens in SecureStore |
| `src/lib/money.ts` | Format/parse paise with string maths (no floats) |
| `src/components/` | Buttons (disabled while busy), states, badges, dialog |

## At 100× scale (what would change)

- **Webhook table**: partition `WebhookEvent` by month and archive old partitions; it's the
  fastest-growing table and only recent rows are hot.
- **Outbox**: calls to the gateway currently happen in `on_commit` hooks and Celery tasks. An
  outbox table written in the same transaction would guarantee "committed ⇒ eventually sent"
  even if the process dies between commit and enqueue.
- **Reads**: send the reports and reconciliation queries to a read replica.
- **Reconciliation**: shard it by centre, and use the gateway's settlement reports (files) rather
  than paginating the payments API.
- **Invoice numbering**: the per-centre counter row is a deliberate hotspot. It's fine at
  thousands of invoices a day per centre; beyond that, pre-allocate blocks of numbers.
