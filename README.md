# CentrePay

Billing, payments and reconciliation for multi-branch service centres. Django + DRF backend,
React Native staff app, Razorpay for payments. See [PROJECT_SPEC.md](PROJECT_SPEC.md) for the full
design, [docs/decisions.md](docs/decisions.md) for why things are the way they are, and
[docs/failure-modes.md](docs/failure-modes.md) for every payment edge case and its test.

## Status

| Week | Scope | State |
|---|---|---|
| 1 | Models, auth & roles, customers, services, invoices, state machine | ✅ done |
| 2 | Payment links + webhook receive/process | ✅ done |
| 3 | Refunds, ledger, reconciliation, audit log, day-close report | ✅ done (all 13 edge cases tested) |
| 4 | React Native app | ⏳ |
| 5 | Deploy, Sentry, README | ⏳ (CI, Docker, seed scaffolded) |

## Run locally

Zero-setup (SQLite, Celery tasks run inline):

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate            # Windows; use `source .venv/bin/activate` elsewhere
pip install -r requirements-dev.txt
python manage.py migrate
python manage.py seed             # 3 centres, staff, services, sample invoices
python manage.py runserver
```

Log in as `blr1_desk` / `blr1_manager` (password `centrepay123`), or `admin` at
http://localhost:8000/admin/.

Settings are read from `.env` at the repo root (copy `.env.example`).

### Payments without a Razorpay account (sandbox)

With no `RAZORPAY_KEY_ID` set, the dev server uses a **sandbox gateway**. "Collect" creates a
link to a local payment page (`/sandbox/pay/<id>/`; all links are listed at
http://localhost:8000/sandbox/), and pressing *Pay* sends properly signed webhooks through the
real webhook endpoint. The page also has buttons for failure scenarios: duplicate webhooks,
lost webhooks (then run `python manage.py reconcile --date <today>` to watch it recover),
short payment, and expiry. Adding Razorpay test keys to `.env` switches to the real gateway
with no code changes. To open the page from a phone on the same Wi-Fi, run
`python manage.py runserver 0.0.0.0:8000` and set `PUBLIC_BASE_URL=http://<your-LAN-IP>:8000`.

### Postgres without Docker (Windows)

```bash
cd backend
python scripts/local_postgres.py   # prints postgresql://postgres:@127.0.0.1:<port>/postgres
```

Use that URL as `DATABASE_URL` for tests (they create their own `test_*` database), or
replace `/postgres` with `/centrepay_dev` for the dev server (create it once with
`CREATE DATABASE centrepay_dev`). With Docker instead: `docker compose up`.

### Receiving real Razorpay webhooks locally

Razorpay has to reach your machine, so expose the dev server with a tunnel, e.g.
`cloudflared tunnel --url http://localhost:8000` (or ngrok), then in the Razorpay dashboard
(Test Mode) add a webhook to `https://<tunnel-host>/webhooks/razorpay/` with events
`payment.authorized`, `payment.captured`, `payment.failed`, `payment_link.paid`,
`payment_link.partially_paid`, `payment_link.expired`, `payment_link.cancelled`,
`refund.processed`, `refund.failed`, and put the same secret in `RAZORPAY_WEBHOOK_SECRET`.

Stuck events can be replayed: `python manage.py replay_webhooks --unprocessed --sync`.

### Reconciliation

`python manage.py reconcile --date 2026-10-01` (default: yesterday). With Redis and Celery Beat
running, it runs automatically at 02:00 IST. Issues show up in Django Admin → Reconciliation
issues, with a "resolve" action that requires a note.

## Tests

```bash
cd backend
pytest --cov=apps          # SQLite unless DATABASE_URL is set (concurrency tests then skip)
ruff check . && ruff format --check .
```

CI runs everything on Postgres 16.

## API (implemented so far)

```
POST   /api/v1/auth/token/            login (rate-limited)
POST   /api/v1/auth/token/refresh/
GET    /api/v1/auth/me/               role + centre, for the app
GET    /api/v1/customers/?search=
POST   /api/v1/customers/
GET    /api/v1/services/
GET    /api/v1/invoices/?status=&date=
POST   /api/v1/invoices/              create draft
PATCH  /api/v1/invoices/{id}/         edit draft (send `version` to detect conflicts)
GET    /api/v1/invoices/{id}/         includes payments, payment attempts, refunds
POST   /api/v1/invoices/{id}/issue/
POST   /api/v1/invoices/{id}/cancel/  {"reason": "..."}; manager-only once issued
POST   /api/v1/invoices/{id}/collect/ Idempotency-Key header; optional {"amount_paise": n}
POST   /api/v1/invoices/{id}/cash/    Idempotency-Key header; optional {"amount_paise": n}
POST   /api/v1/payments/{id}/refunds/ {"amount_paise": n, "reason": "..."}
GET    /api/v1/refunds/?status=requested   the managers' approval queue
POST   /api/v1/refunds/{id}/approve/  manager only
POST   /api/v1/refunds/{id}/reject/   manager only, {"note": "..."}
GET    /api/v1/reports/daily-collection/?date=YYYY-MM-DD
POST   /webhooks/razorpay/            Razorpay only (HMAC-verified)
GET    /healthz
```

All errors: `{"error": {"code": "INVALID_TRANSITION", "message": "...", "details": {...}}}`.
