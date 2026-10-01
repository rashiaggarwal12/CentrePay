# CentrePay

**Billing, payments and reconciliation for multi-branch service centres.** Front-desk staff
bill customers and collect UPI/card payments from a React Native app. A Django backend
handles webhooks, refunds and daily reconciliation against the payment gateway, and it is
built to stay correct when payments go wrong.

Django 5 · DRF · PostgreSQL · Celery + Redis · Razorpay · React Native (Expo) · TypeScript

<!-- After pushing to GitHub: ![CI](https://github.com/<you>/centrepay/actions/workflows/ci.yml/badge.svg) -->
<!-- After deploying: **Live API:** https://<service>.onrender.com · **Demo APK:** <EAS link> -->

## What makes it interesting

Most billing demos only work on the happy path. This one is built around the failures:

| What goes wrong | What CentrePay does |
|---|---|
| The gateway sends the same webhook 3 times, or 3 at once | A unique constraint on the event id; one payment, one ledger entry |
| The refund event arrives before the payment event | Waits with backoff, then escalates to a human |
| The customer paid, but our server crashed before recording it | Raw event stored first; retries; replay command; nightly job recovers it from the gateway |
| Two staff collect on the same invoice | The live link is reused; payments serialised by a row lock; overpayment flagged |
| The gateway's settlement doesn't match our records | The nightly job raises an issue with expected vs actual |
| A double tap on "Collect", or a timeout after the gateway already acted | Idempotency key plus a gateway-side `reference_id`/`receipt`; never two links or two refunds |

Each of the 13 core payment edge cases has a named test:
**[docs/failure-modes.md](docs/failure-modes.md)**.

## By the numbers

- **229 backend tests**, 92% coverage, run on PostgreSQL 16, including thread-based races
  against real row locks. **36 mobile unit tests.**
- **13/13** core failure scenarios covered, plus 12 more found while building.
- Money is **integer paise** end to end, with DB check constraints on every total, and an
  **append-only ledger** enforced by a Postgres trigger.

## Try it in 2 minutes (no accounts needed)

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate            # Windows; `source .venv/bin/activate` elsewhere
pip install -r requirements-dev.txt
python manage.py migrate
python manage.py seed             # 3 centres, staff, services, sample invoices
python manage.py runserver
```

- Staff logins: `blr1_desk` / `blr1_manager`, password `centrepay123`. Django Admin
  (`admin` / `centrepay123`) is at http://localhost:8000/admin/.
- With no Razorpay keys, a built-in **sandbox gateway** stands in: "Collect" creates a link to
  http://localhost:8000/sandbox/, where *Pay* sends properly signed webhooks. It also has
  failure buttons: duplicate webhooks, lost webhooks, short payment, expiry.
- **Staff app:** see [mobile/README.md](mobile/README.md) (Expo Go on your phone, same Wi-Fi).

## Docs

| | |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Components, the three core flows, invariants, a file-by-file code tour |
| [docs/failure-modes.md](docs/failure-modes.md) | Every edge case → how it's handled → its test |
| [docs/decisions.md](docs/decisions.md) | Why each design choice was made |
| [docs/deployment.md](docs/deployment.md) | GitHub, Render, Sentry, APK, Razorpay: step by step |

## Development

### Tests and checks

```bash
cd backend
pytest --cov=apps                  # SQLite unless DATABASE_URL is set (concurrency tests skip)
ruff check . && ruff format --check .

cd ../mobile
npx tsc --noEmit && npx expo lint && npx jest
```

CI runs all of this on every push, with the backend on Postgres 16.

### Postgres without Docker (Windows)

```bash
cd backend
python scripts/local_postgres.py          # start (creates the cluster + centrepay_dev once)
python scripts/local_postgres.py status   # / stop
```

It always listens on **127.0.0.1:54329** (no password), so put this in the repo-root `.env`:

```
DATABASE_URL=postgresql://postgres@127.0.0.1:54329/centrepay_dev
```

Then `migrate`, `seed` and `runserver` use Postgres, and `pytest` runs every test, including
the concurrency ones, in its own throwaway `test_centrepay_dev` database. After a PC
restart, just run the script again. With Docker instead: `docker compose up`.

### Settings

Read from `.env` at the repo root (copy `.env.example`). Key switches:

| Variable | Effect |
|---|---|
| `DATABASE_URL` | Unset in dev → SQLite |
| `REDIS_URL` | Unset in dev → Celery tasks run inline |
| `PAYMENT_GATEWAY` | `fake` (sandbox) or `razorpay`; dev picks `fake` when no keys are set |
| `PUBLIC_BASE_URL` | Where phones reach this server (sandbox payment links) |
| `RAZORPAY_KEY_ID` / `_SECRET` / `_WEBHOOK_SECRET` | Razorpay test mode |
| `SENTRY_DSN` | Error reporting (prod settings) |

### Operations

```bash
python manage.py reconcile --date 2026-10-01      # default: yesterday; Beat runs it at 02:00 IST
python manage.py replay_webhooks --unprocessed --sync
```

Django Admin has the reconciliation issue queue (resolve with a note), webhook events
(replay action), and read-only invoices with their payments and refunds.

## API (v1)

```
POST   /api/v1/auth/token/               login (rate-limited)
POST   /api/v1/auth/token/refresh/
GET    /api/v1/auth/me/                  role + centre
GET    /api/v1/customers/?search=        (all lists are scoped to the caller's centre)
POST   /api/v1/customers/
GET    /api/v1/services/
GET    /api/v1/invoices/?status=&date=
POST   /api/v1/invoices/                 create draft
POST   /api/v1/invoices/preview/         totals without saving (for live UI)
PATCH  /api/v1/invoices/{id}/            edit draft (send `version` to detect conflicts)
GET    /api/v1/invoices/{id}/            includes items, payments, links, refunds
GET    /api/v1/invoices/{id}/timeline/
POST   /api/v1/invoices/{id}/issue/
POST   /api/v1/invoices/{id}/cancel/     {"reason"}; manager-only once issued
POST   /api/v1/invoices/{id}/collect/    Idempotency-Key header; optional {"amount_paise"}
POST   /api/v1/invoices/{id}/cash/       Idempotency-Key header; optional {"amount_paise"}
POST   /api/v1/payments/{id}/refunds/    {"amount_paise", "reason"}
GET    /api/v1/refunds/?status=requested approval queue
POST   /api/v1/refunds/{id}/approve/     manager only
POST   /api/v1/refunds/{id}/reject/      manager only, {"note"}
GET    /api/v1/reports/daily-collection/?date=YYYY-MM-DD
POST   /webhooks/razorpay/               gateway only (HMAC-SHA256 verified)
GET    /healthz
```

Errors always look like `{"error": {"code": "INVALID_TRANSITION", "message": "...", "details": {...}}}`.

## Project status

| Area | State |
|---|---|
| Auth & roles, customers, services, invoices, state machine | ✅ |
| Payment links, webhook receive/process | ✅ |
| Refunds, ledger, reconciliation, audit log, day-close report | ✅ |
| Expo staff app (8 screens) | ✅ |
| CI, Sentry (API + app), Render blueprint, Docker, demo seed data | ✅ built; live deploy and APK: see [docs/deployment.md](docs/deployment.md) |

**Not yet verified against the real world:**
- **Razorpay test mode:** webhook payloads and the settlement report format follow Razorpay's
  docs but haven't been checked against real traffic.
- **Real Android device:** the app has been exercised end to end in a phone-sized browser,
  but not on a phone yet.
- **Docker image:** hasn't been built on the dev machine, which has no Docker.
