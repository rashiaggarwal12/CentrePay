# CentrePay

Billing, payments and reconciliation for multi-branch service centres. Django + DRF backend,
React Native staff app, Razorpay for payments. See [PROJECT_SPEC.md](PROJECT_SPEC.md) for the full
design, [docs/decisions.md](docs/decisions.md) for why things are the way they are, and
[docs/failure-modes.md](docs/failure-modes.md) for every payment edge case and its test.

## Status

| Week | Scope | State |
|---|---|---|
| 1 | Models, auth & roles, customers, services, invoices, state machine | ✅ done |
| 2 | Payment links + webhook receive/process (edge cases 1–3, 8, 10, 12) | ✅ done (+ 5, 6, 9, 13) |
| 3 | Refunds, reconciliation job, reports | ⏳ (ledger + issue queue already in) |
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
`payment_link.partially_paid`, `payment_link.expired`, `payment_link.cancelled`, and put the
same secret in `RAZORPAY_WEBHOOK_SECRET`.

Stuck events can be replayed: `python manage.py replay_webhooks --unprocessed --sync`.

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
GET    /api/v1/invoices/{id}/         includes payments + payment attempts
POST   /api/v1/invoices/{id}/issue/
POST   /api/v1/invoices/{id}/cancel/  {"reason": "..."}; manager-only once issued
POST   /api/v1/invoices/{id}/collect/ Idempotency-Key header; optional {"amount_paise": n}
POST   /webhooks/razorpay/            Razorpay only (HMAC-verified)
GET    /healthz
```

All errors: `{"error": {"code": "INVALID_TRANSITION", "message": "...", "details": {...}}}`.
