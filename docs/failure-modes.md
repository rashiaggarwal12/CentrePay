# Failure modes

Every payment edge case from the spec (section 8), how it's handled, and the test that proves it.
Concurrency tests (marked †) only run on Postgres; SQLite has no row locks.

| # | Scenario | Handling | Test | Status |
|---|---|---|---|---|
| 1 | Same webhook delivered 3× | Unique `gateway_event_id`; later copies return 200 and are ignored | `test_webhooks.py::test_duplicate_webhook_creates_one_payment` | ✅ |
| 2 | Two copies arrive concurrently | DB unique constraint, not an app-level check | `test_concurrency.py::test_concurrent_duplicate_webhooks` † | ✅ |
| 3 | Invalid / missing signature | 400, logged, nothing stored; HMAC over the raw bytes | `test_webhooks.py::test_invalid_signature_rejected`, `test_signature_is_checked_on_raw_bytes` | ✅ |
| 4 | Refund event before payment event | Handler raises `DependencyNotReady`; the task retries on a slower backoff (~45 min in total), then gives up with a `refund_orphaned` issue | `test_refunds.py::test_out_of_order_refund_retries`, `test_orphaned_refund_gives_up_with_issue` | ✅ |
| 5 | `payment.failed` after `payment.captured` | Captured never moves backwards; stale event logged and skipped | `test_webhooks.py::test_stale_event_does_not_downgrade` | ✅ |
| 6 | Customer paid, our processing crashed | Raw event stored first; handler + `processed_at` commit atomically; Celery retries with backoff; `replay_webhooks` command | `test_webhooks.py::test_processing_failure_is_retried`, `test_failed_processing_rolls_back_partial_work`, `test_replay_command_processes_stuck_events`, `test_reconciliation.py::test_stuck_event` | ✅ |
| 7 | Webhook never arrives | Reconciliation lists the day's gateway payments and sweeps our open links, then applies the payment through the same idempotent service the webhook uses | `test_reconciliation.py::test_reconciliation_recovers_missing_payment`, `test_recovers_link_payment_even_without_notes` | ✅ |
| 8 | Two staff collect on the same invoice | Same-amount live link is reused; invoice row lock serialises payments | `test_collect.py::test_second_staff_member_gets_the_live_link`, `test_concurrency.py::test_concurrent_payments_locked` † | ✅ |
| 9 | Customer pays more than due | Recorded; `overpaid` issue with the refund due | `test_webhooks.py::test_overpayment_flagged` | ✅ |
| 10 | Partial payments | `partially_paid` until the sum reaches the total | `test_webhooks.py::test_partial_then_full_payment` | ✅ |
| 11 | Refund exceeds amount paid | Service-layer check that also counts pending refunds (plus a DB check constraint on the invoice) | `test_refunds.py::test_refund_cannot_exceed_paid`, `test_pending_refunds_reserve_the_amount` | ✅ |
| 12 | Duplicate "create payment link" | `Idempotency-Key` → unique `PaymentAttempt`; in-flight duplicates get 409 | `test_collect.py::test_collect_is_idempotent`, `test_concurrency.py::test_concurrent_collect_same_key_creates_one_link` † | ✅ |
| 13 | Webhook amount ≠ link amount | Gateway amount recorded; `amount_mismatch` issue | `test_webhooks.py::test_amount_mismatch_flagged` | ✅ |

## Found while building

| Scenario | Handling | Test |
|---|---|---|
| Our call to Razorpay times out *after* Razorpay created the link | Attempt row is saved before the call; retry looks the link up by `reference_id` before creating one | `test_collect.py::test_retry_recovers_link_created_before_timeout` |
| Double tap: 2nd request arrives while the 1st is still talking to Razorpay | Recovery needs an atomic claim; a fresh `pending` attempt → `409 REQUEST_IN_PROGRESS` | `test_collect.py::test_retry_while_first_request_in_flight_is_rejected` |
| `payment.captured` and `payment_link.paid` both carry the same payment | Both route to one idempotent call keyed by `gateway_payment_id` | `test_webhooks.py::test_captured_and_link_paid_for_same_payment_apply_once`, `test_concurrency.py::test_concurrent_same_payment_applied_once` † |
| Payment fails, then succeeds later (Razorpay late authorization) | `failed → captured` is allowed: money received is the truth | `test_webhooks.py::test_failed_then_captured_late_authorization` |
| Link paid just as the invoice was cancelled | Payment recorded, status kept, `payment_on_closed_invoice` issue; cancelling an invoice also cancels its live links | `test_webhooks.py::test_payment_on_cancelled_invoice_is_recorded_and_flagged`, `test_collect.py::test_cancelling_invoice_cancels_live_links` |
| Link expires, then a payment on it lands anyway | `paid` wins over `expired` | `test_webhooks.py::test_expired_link_then_paid_still_counts` |
| Full payment while another (partial) link is still live | Links for more than is now due are cancelled at Razorpay | `test_webhooks.py::test_full_payment_cancels_other_live_links` |
| Someone edits ledger rows with SQL / `queryset.update()` | Postgres trigger rejects UPDATE/DELETE on `ledger_ledgerentry` | `test_concurrency.py::test_ledger_trigger_blocks_bulk_update_and_delete` † |
| Refund API response and `refund.processed` webhook both report the same refund | One idempotent `apply_refund_processed`; unique ledger debit per refund | `test_refunds.py::test_refund_applied_once_from_api_response_and_webhook` |
| Refund call times out after the gateway already made the refund | Our `receipt` travels with the refund; the retry finds it before creating one | `test_refunds.py::test_gateway_timeout_then_retry_finds_refund_by_receipt` |
| Someone refunds from the gateway dashboard | Recorded (the money has left) and flagged, because it skipped approval | `test_refunds.py::test_out_of_order_refund_retries`, `test_reconciliation.py::test_refund_made_at_gateway_but_not_recorded` |
| Refunding an overpayment | Invoice stays `paid`; the open `overpaid` issue resolves itself | `test_refunds.py::test_refund_of_overpayment_keeps_invoice_paid` |

## Reconciliation checks

Each has a test in `test_reconciliation.py` that plants the mismatch and checks it's caught.

| Check | Issue kind | Test |
|---|---|---|
| Captured at gateway, unknown locally, invoice can't be identified | `missing_locally` | `test_unidentifiable_gateway_payment_raises_missing_locally` |
| Captured locally, unknown at gateway (cash excluded) | `missing_at_gateway` | `test_local_payment_missing_at_gateway`, `test_cash_payments_are_not_expected_at_gateway` |
| Amounts differ | `amount_mismatch` | `test_amount_mismatch_detected` |
| Gateway status or refunded total differs from ours | `status_mismatch` | `test_gateway_status_mismatch`, `test_refund_made_at_gateway_but_not_recorded` |
| `amount_paid` ≠ Σ captured, `amount_refunded` ≠ Σ processed refunds, or ledger ≠ net | `invoice_drift` | `test_invoice_drift_detected` |
| Settlement ≠ Σ(payment − fee) − Σ refunds by our records, or a fee differs | `settlement_mismatch` | `test_settlement_fee_mismatch`, `test_settlement_matches_next_day` |
| Webhook unprocessed for more than 1 hour | `stuck_event` | `test_stuck_event` |
| Re-runs don't duplicate issues; gateway down still runs local checks | — | `test_repeated_runs_do_not_duplicate_issues`, `test_gateway_down_still_runs_local_checks` |
