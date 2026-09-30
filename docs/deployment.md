# Deployment

Everything is prepared in the repo. These are the steps that need your accounts, in order.
Each is a one-time setup.

## 1. GitHub (needed for everything else)

```bash
# from the repo root, after creating an EMPTY repo on github.com (no README/licence)
git remote add origin https://github.com/<you>/centrepay.git
git push -u origin main
```

CI (`.github/workflows/ci.yml`) then runs on every push: backend lint, migrations check and
tests on Postgres 16; mobile typecheck, lint and tests. Add the badge to the README:
`![CI](https://github.com/<you>/centrepay/actions/workflows/ci.yml/badge.svg)`.

## 2. Backend on Render

1. Sign up at render.com with your GitHub account.
2. **New → Blueprint**, pick the repo. Render reads `render.yaml` and proposes: `centrepay-api`
   (web), `centrepay-worker` (Celery + Beat), `centrepay-redis` (Key Value), and `centrepay-db`
   (Postgres).
3. It asks for the `sync: false` values:
   - `SEED_ADMIN_PASSWORD`: a strong password for the `admin` Django Admin user.
   - `SENTRY_DSN`: from step 3 (can be added later).
   - `RAZORPAY_*`: leave empty while `PAYMENT_GATEWAY=fake`.
4. Deploy. `bin/start-web.sh` runs migrations, seeds demo data (`SEED_DEMO_DATA=true`) and
   starts gunicorn. Check `https://<your-service>.onrender.com/healthz` returns `{"status": "ok"}`.

**Free-only option:** the worker has no free plan. Delete the `centrepay-worker` block from
`render.yaml` and set `CELERY_TASK_ALWAYS_EAGER=true` on the web service. Webhooks and refund
calls then run inside the request (still correct, just slower). Run
`python manage.py reconcile` from the web service's Shell tab when you want a reconciliation.

**After the first deploy:** turn `SEED_DEMO_DATA` off if you don't want demo invoices
recreated for centres that have none.

## 3. Sentry

1. sentry.io → create a **Django** project and a **React Native** project.
2. Backend: put the Django DSN in `SENTRY_DSN` on both Render services.
3. App: put the React Native DSN in `mobile/.env` as `EXPO_PUBLIC_SENTRY_DSN=...` (and in
   `eas.json` → `build.preview.env` for the APK). In `mobile/app.json`, replace
   `YOUR_SENTRY_ORG` with your org slug.
4. Optional, readable stack traces: create a Sentry auth token, add it to EAS as a secret
   `SENTRY_AUTH_TOKEN`, and remove `SENTRY_DISABLE_AUTO_UPLOAD` from `eas.json`.

Check it: `python manage.py shell -c "1/0"` doesn't reach Sentry (it's a shell, not a
request). Instead hit a URL that errors, or use Sentry's "Send test event" in project
settings.

## 4. Demo APK (Expo EAS)

1. expo.dev → sign up (free).
2. ```bash
   cd mobile
   npx eas-cli@latest login
   npx eas-cli@latest init          # links the project, writes the projectId into app.json
   ```
3. In `eas.json`, set `EXPO_PUBLIC_API_URL` to your Render URL (the `preview` profile).
4. ```bash
   npx eas-cli@latest build --platform android --profile preview
   ```
   It builds in the cloud (about 10–20 min on the free tier) and prints a link to the `.apk`.
   Put that link in the README.

## 5. Razorpay test mode (optional, whenever you have the account)

1. Dashboard (Test Mode) → API Keys → generate. Put `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET`
   on **both** Render services and set `PAYMENT_GATEWAY=razorpay`.
2. Dashboard → Webhooks → add `https://<your-service>.onrender.com/webhooks/razorpay/`, with
   events `payment.authorized`, `payment.captured`, `payment.failed`, `payment_link.paid`,
   `payment_link.partially_paid`, `payment_link.expired`, `payment_link.cancelled`,
   `refund.processed` and `refund.failed`. Choose a secret and put the same value in
   `RAZORPAY_WEBHOOK_SECRET`.
3. Make a test payment. In Django Admin → Webhook events, open each event, copy its
   `raw_payload` into `backend/tests/fixtures/razorpay/<event>.json`, and point the webhook
   tests at them (spec section 14: tests on recorded real payloads).
4. Verify the settlement check against a real settlement (`python manage.py reconcile`
   two days after a test payment). The sandbox follows Razorpay's documented format, but
   that hasn't been confirmed against real data yet.

## Running it all with Docker (alternative to Render)

```bash
cp .env.example .env          # set DJANGO_SECRET_KEY at least
docker compose up --build     # postgres, redis, backend :8000, worker, beat
docker compose exec backend python manage.py seed
```

(The Dockerfile and compose file haven't been run on this machine, which has no Docker.)
