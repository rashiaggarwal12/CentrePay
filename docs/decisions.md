# Decisions log

Short notes on why each design choice was made, and what it protects against.

## Invoice numbers are assigned on issue, not on create
GST requires invoice numbers to be consecutive and without gaps. Drafts get abandoned or
cancelled all the time; numbering them would leave holes. So `number` is NULL until issue
(Postgres unique constraints ignore NULLs), then taken from a per-centre, per-financial-year
counter row that is locked with `select_for_update`, so two invoices issued at the same moment
get consecutive numbers instead of the same one.

Format: `BLR1/2627/000042` = centre code / FY 2026-27 / sequence. Centre codes are capped at
4 chars so the whole number fits GST's 16-character limit. The financial year is computed in IST,
so an invoice issued at 00:30 IST on 1 April belongs to the new FY even though it is still
31 March in UTC.

## Invoice-level discount is spread across lines before GST
GST is charged on the discounted value, and lines can have different GST rates (physiotherapy
is exempt; massage is 18%). A single invoice-level discount is therefore split across lines in
proportion to their value (largest-remainder method, so the parts always sum to exactly the
discount), and each line is then taxed at its own rate, rounded half-up per line. Each line's
share of the discount and its tax are stored on `InvoiceItem`, so the printed invoice can show
them.

## Prices and GST rates are copied onto the invoice item
`InvoiceItem.unit_price_paise` and `gst_rate_bps` are copied from the `Service` when the line is
written. Changing the catalogue later never alters an existing invoice.

## Only managers can cancel an issued invoice
An issued invoice is a legal document with a number. Letting front desk void one would allow
"collect cash, cancel invoice". Drafts can be cancelled by anyone. Invoices with any money
received can't be cancelled at all; they must be refunded.

## Optimistic `version` on top of row locks
Row locks stop two requests from interleaving, but they don't stop a *lost update*: two staff open
the same draft, both edit, and the second save silently overwrites the first. Clients send back
the `version` they loaded; a mismatch returns `409 STALE_VERSION`. It is optional so that simple
clients still work.

## Other centres' objects return 404, not 403
Querysets are filtered by the staff member's centre before lookup, so an ID from another centre
simply doesn't exist from the caller's point of view. A 403 would confirm that the ID exists.

## Invoices are read-only in Django Admin
Admin edits would bypass the state machine, row locks and audit log. Managers use admin to
browse and search, and change invoices only through the API's service layer.

## Database check constraints as a second guard
`total = subtotal − discount + tax`, `discount ≤ subtotal`, `refunded ≤ paid` and
`line_total = qty × unit_price` are enforced by Postgres, not just by Python. A bug in a service
function, or a stray `.update()`, fails loudly instead of corrupting money fields.

## Payment link shown as a QR, not a separate UPI QR API
The collect endpoint creates a Razorpay **payment link**; the app renders its `short_url` as a
QR code. The link's page offers UPI (any app), cards and netbanking, works in test mode, and
gives us one object with one lifecycle (created → paid / expired / cancelled) instead of two
different gateway products to reconcile.

## The PaymentAttempt row is written before calling Razorpay
If we called Razorpay first and crashed before saving, a live link would exist that we know
nothing about. Instead: save a `pending` attempt with a random `reference_id`, then call the
gateway outside the DB transaction (never hold a row lock across a network call). A retry that
finds a stale `pending`/`failed` attempt first asks Razorpay "is there a link with this
reference_id?" and only creates one if not.

## Idempotency-Key is required on collect
The app generates one per tap of "Collect" and reuses it on retries. Same key → same attempt.
Concurrent requests with the same key race on a unique constraint; the loser either gets the
finished attempt or `409 REQUEST_IN_PROGRESS`, never a second link.

## Two staff collecting the same amount get the same link
Without this, two live QRs for one invoice lets the customer pay twice. A new key for the same
invoice and amount returns the link already showing, if it has more than 2 minutes left.

## One idempotent entry point for captured payments
`payment.captured` and `payment_link.paid` both carry the same payment. Both call
`apply_payment_captured`, which locks the invoice, then checks the payment by its unique
Razorpay id. Whichever event arrives first applies it; the other is a no-op. The ledger also
has a unique constraint on (payment, entry_type=payment), so a bug here can't double-credit.

## Payment status only moves forward, except failed → captured
Stale events (a `payment.failed` delivered after `payment.captured`) are logged and skipped.
The one exception is Razorpay's "late authorization": a payment reported failed can later be
captured. Money received is the truth, so `failed → captured` is allowed.

## Money that arrives for a closed invoice is recorded, not rejected
If a link is paid just as its invoice is cancelled, rejecting the event would make the task
retry forever and hide real money. The payment is recorded, the invoice status is left alone,
and a `payment_on_closed_invoice` issue asks a manager to refund it. Cancelling an invoice also
cancels its live links at Razorpay to make this rare.

## Handler changes and `processed_at` commit in one transaction
The event row is locked, the handler runs, and `processed_at` is set, all in one transaction.
If the handler fails halfway, nothing it did is kept and the event stays unprocessed for the
retry. Failure bookkeeping (`attempts`, `last_error`) is written separately after the rollback.

## Ledger append-only is enforced by a Postgres trigger too
`LedgerEntry.save()` refuses updates, but `queryset.update()` and manual SQL bypass model
methods. A `BEFORE UPDATE OR DELETE` trigger doesn't.

## Issues raised inline, not only by the nightly job
Overpayments and amount mismatches are known the moment the webhook is processed, so they
go into the `ReconciliationIssue` queue immediately. A partial unique index on
(kind, gateway_ref) where status = open keeps retries from creating duplicates.

## A sandbox gateway for local development, not just mocks in tests
Razorpay asks for a PAN at sign-up, and reviewers of this project won't have keys. `FakeGateway`
has the same methods as `RazorpayClient` and returns Razorpay-shaped data. It keeps its own
state in separate tables, playing the part of Razorpay's database. It sends signed webhooks
through the real `/webhooks/razorpay/` view; only the network hop is skipped. Everything
downstream (signature check, dedup, Celery, idempotent services, reconciliation) is the real
code. Setting `PAYMENT_GATEWAY=razorpay` and adding keys switches over with no code change. The
sandbox pages return 404 unless the fake gateway is enabled.

## Refunds need a manager, and pending refunds reserve the amount
Front desk requests; a manager approves, or rejects with a note. A requested refund already
counts against what's refundable, so two requests can't together exceed the payment. Money only
moves once the gateway confirms: the ledger debit and `amount_refunded` are written in
`apply_refund_processed`, which both the API response and the `refund.processed` webhook call.
Cash refunds skip the gateway and are processed on approval.

## Refunds carry a `receipt` for crash recovery
This is the same idea as `reference_id` on payment links. If the refund call times out after
Razorpay made the refund, the retry lists the payment's refunds and finds ours by receipt. Only
if it isn't there does it create one, so a customer is never refunded twice.

## Out-of-order events wait, then escalate
A `refund.processed` for a payment we haven't recorded means the payment's webhook is behind.
The handler raises `DependencyNotReady`. The task retries on a slower schedule (30s, doubling,
capped at 10 min: about 45 min in total), then gives up and opens a `refund_orphaned` issue
instead of retrying forever.

## Reconciliation recovers through the same code as webhooks
When the job finds a captured payment we missed, it calls `apply_payment_captured`, the same
idempotent, locking function the webhook uses. There's no second "repair" implementation to
keep correct. For payment-link payments it also asks the gateway about our open and expired
links, so recovery works even if the gateway's payment doesn't carry our notes.

## The settlement check uses our numbers, not the gateway's
For each settlement, expected = Σ(our payment amount − our recorded fee) − Σ(our refunds).
Comparing the gateway's settlement with the gateway's own line items would only check
Razorpay's arithmetic.

## Cash payments reuse the gateway payment path
Cash is a `Payment` with `method=cash` and an id derived from the Idempotency-Key, recorded
through `apply_payment_captured`. It gets the same locking, ledger, state machine and audit as
online payments. Reconciliation skips it because no gateway has a record of it.

## A server-side preview endpoint for live invoice totals
The new-invoice screen shows the total with GST as staff tap services. Computing it in the
app would mean a second implementation of discount allocation and per-line GST rounding,
which could drift from the server's. Instead the app calls `POST /invoices/preview/` (same
`compute_totals`, nothing saved) on every change and shows the server's figure. It also
gets `refundable_paise` on each payment, so the refund screen doesn't subtract either.

## Idempotency keys belong to the user's intent, not the HTTP request
The collect screen makes one key when it opens and reuses it for retries after a network
error, so the server returns the same link. Only "Create new link" (after expiry) makes a
new key. The same goes for recording cash. A `409 REQUEST_IN_PROGRESS` makes the app wait
and ask again with the same key.

## Polling, not push, for "paid"
The collect screen re-fetches the invoice every 3 seconds and stops once it is paid. It's
simple, works through any network, and the delay is at most one interval. Push
(WebSockets or FCM) would add infrastructure for a saving of about 2 seconds.

## The app finds the backend by itself in development
In Expo Go, the app uses the dev server's host (`expoConfig.hostUri`) with port 8000, so a
phone on the same Wi-Fi reaches the laptop's Django without configuration.
`EXPO_PUBLIC_API_URL` overrides it for release builds.
