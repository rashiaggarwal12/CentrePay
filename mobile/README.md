# CentrePay staff app

Expo (SDK 57) + TypeScript + Expo Router + TanStack Query. The front-desk and manager app
for the CentrePay backend.

## Run it on your phone

1. **Backend, reachable over Wi-Fi.** In `backend/`:
   ```bash
   python manage.py runserver 0.0.0.0:8000
   ```
   and in the repo-root `.env` set `PUBLIC_BASE_URL=http://<your laptop's LAN IP>:8000`, so
   payment-link QR codes open on a phone. Find the IP with `ipconfig` (look for IPv4 Address
   under Wi-Fi). If Windows asks whether Python may use the network, allow it on
   **private** networks.
2. **The app.** In `mobile/`:
   ```bash
   npm install
   npx expo start
   ```
3. Install **Expo Go** from the Play Store (or App Store), make sure the phone is on the same
   Wi-Fi, and scan the QR code the terminal shows.
4. Sign in as `blr1_desk` or `blr1_manager` (password `centrepay123`, from `manage.py seed`).

The app finds the backend automatically: it uses the same host as the Expo dev server, on
port 8000. To point it somewhere else (e.g. the deployed backend), set
`EXPO_PUBLIC_API_URL=https://...` before `npx expo start`.

## Screens

| Screen | File | Notes |
|---|---|---|
| Login | `src/app/login.tsx` | JWT kept in SecureStore (OS keystore); refresh is automatic |
| Today | `src/app/(app)/(tabs)/index.tsx` | Today's invoices, status filter, pull to refresh |
| Customers | `src/app/(app)/(tabs)/customers.tsx` | Search by name/phone, create, then start an invoice |
| New invoice | `src/app/(app)/invoice/new.tsx` | Live total from the server's preview endpoint |
| Collect payment | `src/app/(app)/invoice/[id]/collect.tsx` | QR + link, polls every 3s, success animation, expiry countdown and renewal |
| Invoice detail | `src/app/(app)/invoice/[id]/index.tsx` | Items, payments, refunds, timeline; issue, cash, cancel |
| Refund request | `src/app/(app)/invoice/[id]/refund.tsx` | Amount capped at the server-provided refundable amount |
| Approvals | `src/app/(app)/(tabs)/approvals.tsx` | Managers only (tab hidden for front desk), badge with pending count |
| Day close | `src/app/(app)/(tabs)/day-close.tsx` | By method, refunds, fees, net; browse previous days |

## Rules the app follows

- **The server computes money.** The app formats and parses paise (string maths, no
  floats) but never adds up totals. The new-invoice total comes from
  `POST /api/v1/invoices/preview/`.
- **No double taps.** Buttons disable while their request runs, and "Collect" and "Cash"
  send an Idempotency-Key that is reused on retries. So a retry after a timeout gets the
  original result instead of a second payment link.
- **Loading, empty and error states on every screen**, with a retry button on errors.

## Checks

```bash
npx tsc --noEmit     # types
npx expo lint        # lint (includes React Compiler rules)
npx jest             # unit tests: money formatting/parsing, API client (refresh, errors)
```
