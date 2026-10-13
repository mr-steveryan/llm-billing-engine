# Evidence

One proof per requirement. Every block is real output against the running stack (`docker compose up`), using the
seeded demo tenants. Captured on **2026-10-12**, except the full test suite and sections 4 and 7, re-captured on **2026-10-13**
on a fresh database (after the schema moved to `migrations/`), so event ids there restart at 1:

| Tenant | id | API key (demo only) |
|--------|----|---------------------|
| Tenant A | `00000000-0000-7000-8000-00000000000a` | `demo-key-tenant-a` |
| Tenant B | `00000000-0000-7000-8000-00000000000b` | `demo-key-tenant-b` |

Status: ✅ proven.

| # | Requirement | Status |
|---|-------------|--------|
| 1 | Metering: one event per billable action, even under retries | ✅ |
| 2 | Quotas: checked against plan, over-limit rejected | ✅ |
| 3 | Quotas: correct 429 / 402 with a message explaining why | ✅ |
| 4 | Cost: monthly usage rolls up into a cost figure per tenant (`GET /usage`) | ✅ |
| 5 | Cost: cached input, reasoning and output priced correctly | ✅ |
| 6 | Cost: pricing constants pinned in config, totals proven | ✅ |
| 7 | Payments: subscription checkout works end-to-end in test mode | ✅ |
| 8 | Payments: webhooks verify signatures, ignore duplicates, update plan/status | ✅ |
| 9 | Data model: tenants, plans, subscriptions, usage events; tenant isolation | ✅ |
| 10 | README + architecture diagram + setup instructions | ✅ |

> Payments use **Razorpay** (test mode) instead of Stripe. Event mapping is in [DESIGN.md](DESIGN.md#payment-sync-razorpay-test-mode).

---

## Full test suite

```
$ uv run pytest -v
tests/test_checkout.py::test_first_checkout_creates_and_stores_subscription_for_key_tenant PASSED [  1%]
tests/test_checkout.py::test_repeat_checkout_reuses_unpaid_subscription PASSED [  3%]
tests/test_checkout.py::test_checkout_refused_while_mandate_authenticated PASSED [  5%]
tests/test_checkout.py::test_stale_authenticated_row_dead_at_razorpay_does_not_block_checkout PASSED [  7%]
tests/test_checkout.py::test_razorpay_failure_stores_nothing PASSED      [  9%]
tests/test_checkout.py::test_later_activation_webhook_still_upgrades PASSED [ 10%]
tests/test_checkout.py::test_checkout_refuses_tenant_already_on_pro PASSED [ 12%]
tests/test_checkout.py::test_reuse_asks_razorpay_first PASSED            [ 14%]
tests/test_checkout.py::test_missed_webhook_paid_subscription_blocks_a_second_one PASSED [ 16%]
tests/test_checkout.py::test_dead_subscription_at_razorpay_starts_a_fresh_one PASSED [ 18%]
tests/test_checkout.py::test_cancelling_after_a_stuck_authenticated_row_returns_tenant_to_free PASSED [ 20%]
tests/test_checkout.py::test_razorpay_calls_have_a_timeout PASSED        [ 21%]
tests/test_checkout.py::test_simultaneous_checkouts_create_one_subscription PASSED [ 23%]
tests/test_metering.py::test_same_request_twice_creates_one_event PASSED [ 25%]
tests/test_metering.py::test_concurrent_retries_of_one_request_create_one_event PASSED [ 27%]
tests/test_metering.py::test_replay_returns_original_even_when_tenant_is_now_blocked PASSED [ 29%]
tests/test_metering.py::test_call_quota_boundary_returns_429 PASSED      [ 30%]
tests/test_metering.py::test_token_quota_rejects_a_request_that_would_cross_the_limit PASSED [ 32%]
tests/test_metering.py::test_concurrent_requests_cannot_overshoot_quota PASSED [ 34%]
tests/test_metering.py::test_past_due_tenant_gets_402 PASSED             [ 36%]
tests/test_metering.py::test_pro_plan_has_higher_limits PASSED           [ 38%]
tests/test_metering.py::test_other_tenants_usage_does_not_count_against_you PASSED [ 40%]
tests/test_metering.py::test_same_idempotency_key_in_two_tenants_is_two_events PASSED [ 41%]
tests/test_metering.py::test_missing_or_bad_api_key_is_401 PASSED        [ 43%]
tests/test_metering.py::test_missing_idempotency_key_is_400 PASSED       [ 45%]
tests/test_metering.py::test_impossible_token_counts_are_422 PASSED      [ 47%]
tests/test_metering.py::test_cost_uses_category_rates_not_a_token_sum PASSED [ 49%]
tests/test_usage.py::test_empty_month_shows_zero_usage_and_plan_limits PASSED [ 50%]
tests/test_usage.py::test_rollup_cost_matches_pinned_pricing_constants PASSED [ 52%]
tests/test_usage.py::test_last_months_usage_is_not_counted PASSED        [ 54%]
tests/test_usage.py::test_tenant_sees_only_its_own_usage PASSED          [ 56%]
tests/test_usage.py::test_usage_requires_api_key PASSED                  [ 58%]
tests/test_webhooks.py::test_activated_flips_tenant_free_to_pro PASSED   [ 60%]
tests/test_webhooks.py::test_forged_signature_is_rejected_and_changes_nothing PASSED [ 61%]
tests/test_webhooks.py::test_non_ascii_signature_is_rejected_not_crashed PASSED [ 63%]
tests/test_webhooks.py::test_missing_signature_is_rejected PASSED        [ 65%]
tests/test_webhooks.py::test_replayed_event_is_ignored PASSED            [ 67%]
tests/test_webhooks.py::test_out_of_order_older_event_does_not_win PASSED [ 69%]
tests/test_webhooks.py::test_halted_subscription_blocks_usage_with_402 PASSED [ 70%]
tests/test_webhooks.py::test_cancelled_returns_tenant_to_free PASSED     [ 72%]
tests/test_webhooks.py::test_old_subscriptions_late_cancel_does_not_downgrade_paying_tenant PASSED [ 74%]
tests/test_webhooks.py::test_same_second_authenticated_last_still_protects_paying_tenant PASSED [ 76%]
tests/test_webhooks.py::test_concurrent_old_cancel_cannot_downgrade_tenant_mid_activation PASSED [ 78%]
tests/test_webhooks.py::test_failed_apply_does_not_mark_event_seen PASSED [ 80%]
tests/test_webhooks.py::test_event_for_unknown_tenant_is_acknowledged_not_crashed[00000000-0000-7000-8000-0000000000ff] PASSED [ 81%]
tests/test_webhooks.py::test_event_for_unknown_tenant_is_acknowledged_not_crashed[not-a-uuid] PASSED [ 83%]
tests/test_webhooks.py::test_event_for_unknown_tenant_is_acknowledged_not_crashed[42] PASSED [ 85%]
tests/test_webhooks.py::test_signed_but_malformed_event_is_400_and_changes_nothing[not json] PASSED [ 87%]
tests/test_webhooks.py::test_signed_but_malformed_event_is_400_and_changes_nothing[[]] PASSED [ 89%]
tests/test_webhooks.py::test_signed_but_malformed_event_is_400_and_changes_nothing[{"event": "subscription.activated", "created_at": 1000, "payload": {}}] PASSED [ 90%]
tests/test_webhooks.py::test_signed_but_malformed_event_is_400_and_changes_nothing[{"event": "subscription.activated", "created_at": 1000, "payload": {"subscription": {"entity": {"id": "s"}}}}] PASSED [ 92%]
tests/test_webhooks.py::test_signed_but_malformed_event_is_400_and_changes_nothing[{"event": "payment.captured", "created_at": 99999999999999, "payload": {}}] PASSED [ 94%]
tests/test_webhooks.py::test_signed_but_malformed_event_is_400_and_changes_nothing[{"created_at": 1000, "payload": {}}] PASSED [ 96%]
tests/test_webhooks.py::test_non_subscription_event_is_recorded_but_changes_nothing PASSED [ 98%]
tests/test_webhooks.py::test_subscription_not_created_by_checkout_is_acknowledged_not_crashed PASSED [100%]
============================== 55 passed in 2.00s ==============================
```
Tests run against a separate `billing_test` database, rebuilt from `migrations/` on every run.

**The tests catch real bugs.** Each protection was removed on purpose, and its test failed:

| Removed | Test that failed |
|---------|------------------|
| Row lock (`FOR UPDATE`) in `meter.record` | both `test_concurrent_*` tests (3/3 runs) |
| Webhook dedup | `test_replayed_event_is_ignored` |
| Out-of-order guard | `test_out_of_order_older_event_does_not_win` |
| Signature check | `test_forged_signature_*`, `test_missing_signature_is_rejected` |
| Empty-notes guard | `test_subscription_not_created_by_checkout_*` (same `TypeError` as production) |
| Month filter in `monthly_usage` | `test_last_months_usage_is_not_counted` |
| Checkout reuse of an unpaid subscription | `test_repeat_checkout_reuses_unpaid_subscription` |
| Razorpay status check before reuse | `test_reuse_asks_razorpay_first`, `test_missed_webhook_paid_subscription_blocks_a_second_one`, `test_dead_subscription_at_razorpay_starts_a_fresh_one` |
| Correcting a dead subscription's row in `/checkout` | `test_cancelling_after_a_stuck_authenticated_row_returns_tenant_to_free` (cancelled tenant kept Pro without it), `test_dead_subscription_at_razorpay_starts_a_fresh_one` |
| Tenant row lock in `/checkout` | `test_simultaneous_checkouts_create_one_subscription` (3/3 runs) |
| Razorpay call timeout | `test_razorpay_calls_have_a_timeout` |
| Byte-wise signature compare | `test_non_ascii_signature_is_rejected_not_crashed` (500 `TypeError` without it) |
| Out-of-order guard's status | `test_out_of_order_older_event_does_not_win` (reported `applied` without it) |
| Live-subscription check on downgrade | `test_old_subscriptions_late_cancel_does_not_downgrade_paying_tenant` (paying tenant dropped to Free without it) |
| `authenticated` counted as live in the downgrade check | `test_same_second_authenticated_last_still_protects_paying_tenant` (real 16:07:17 arrival order; paying tenant dropped to Free without it) |
| Tenant row lock in the webhook handler | `test_concurrent_old_cancel_cannot_downgrade_tenant_mid_activation` (3/3 runs; paying tenant dropped to Free without it) |
| Razorpay status check for `authenticated` rows | `test_stale_authenticated_row_dead_at_razorpay_does_not_block_checkout` (409 forever without it) |

---

## 1. Metering: one event per billable action, even under retries ✅

The same request sent twice with one `Idempotency-Key`:
```
$ curl -s -X POST localhost:8000/generate -H 'X-API-Key: demo-key-tenant-b' -H 'Idempotency-Key: evidence-req-1' \
    -H 'Content-Type: application/json' \
    -d '{"input_tokens":10000,"cached_input_tokens":4000,"output_tokens":5000,"reasoning_tokens":2000}'
{"event_id":15069,"cost_usd":"0.0624000000","replayed":false}

$ (same command again)
{"event_id":15069,"cost_usd":"0.0624000000","replayed":true}

$ docker compose exec -T db psql -U billing -c "SELECT count(*) AS events_for_key FROM usage_events WHERE idempotency_key = 'evidence-req-1'"
 events_for_key
----------------
              1
```
Same event id, `replayed: true`, one row. Double-counting is impossible for two reasons: a per-tenant
row lock serializes requests, and `UNIQUE (tenant_id, idempotency_key)` is the final guarantee (see section 9).
`test_concurrent_retries_of_one_request_create_one_event` fires 10 parallel retries and gets exactly 1 row.

## 2. Quotas: checked against the plan, over-limit rejected ✅

Plans: **Free** 1,000 calls / 100,000 tokens per month; **Pro** 50,000 calls / 10,000,000 tokens (calendar month, UTC).

Call quota boundary, Tenant B on Free. Setup: 998 calls preloaded by SQL, plus the event from section 1, which makes 999:
```
$ docker compose exec -T db psql -U billing -c "SELECT count(*) AS calls_this_month FROM usage_events WHERE tenant_id = '00000000-0000-7000-8000-00000000000b'"
 calls_this_month
------------------
              999

$ curl ... -H 'Idempotency-Key: evidence-call-1000' -d '{"input_tokens":10,"cached_input_tokens":0,"output_tokens":10,"reasoning_tokens":0}'
{"event_id":16068,"cost_usd":"0.0001200000","replayed":false} HTTP 200

$ curl ... -H 'Idempotency-Key: evidence-call-1001' -d '{"input_tokens":10,"cached_input_tokens":0,"output_tokens":10,"reasoning_tokens":0}'
{"detail":"Monthly API call quota of 1000 on plan 'free' exhausted; resets 2026-11-01 (UTC) or upgrade to Pro."} HTTP 429

$ (count again)
 calls_this_month
------------------
             1000
```
The 1,000th call is allowed; the 1,001st is rejected and **stores nothing** (the count stays 1,000).

Token quota: a single request that **would** cross 100,000 is rejected whole. (Tenant B's `usage_events` were deleted
after the call-quota proof, hence `0 used`.)
```
$ curl ... -H 'Idempotency-Key: evidence-big' -d '{"input_tokens":90000,"cached_input_tokens":0,"output_tokens":10001,"reasoning_tokens":0}'
{"detail":"Monthly token quota of 100000 on plan 'free' would be exceeded (0 used + 100001 requested); resets 2026-11-01 (UTC) or upgrade to Pro."} HTTP 429
```
`test_concurrent_requests_cannot_overshoot_quota`: 10 parallel calls with 2 calls of quota left → exactly 2 × 200, 8 × 429.

## 3. Quotas: correct status codes and messages ✅

| Code | When | Captured response |
|------|------|-------------------|
| **429** | Call or token quota exhausted | see section 2 |
| **402** | All subscription payment retries failed (tenant `past_due`, set by Razorpay `subscription.halted`) | below |
| 401 | Missing / wrong API key | `{"detail":"Invalid API key."} HTTP 401` |
| 400 | Missing `Idempotency-Key` | `test_missing_idempotency_key_is_400` |
| 422 | Impossible token counts | below |

402, with Tenant B set to `past_due`:
```
$ curl -s -w ' HTTP %{http_code}\n' -X POST localhost:8000/generate -H 'X-API-Key: demo-key-tenant-b' -H 'Idempotency-Key: evidence-402' ...
{"detail":"Payment failed for your subscription; update your payment method to continue."} HTTP 402
```
The full path from a webhook to a 402 is covered by `test_halted_subscription_blocks_usage_with_402`
(signed `subscription.halted` → tenant `past_due` → `/generate` returns 402).

422, cached input larger than input:
```
{"detail":[{"type":"value_error","loc":["body"],"msg":"Value error, cached_input_tokens cannot exceed input_tokens (cached is part of input)", ...
```

## 4. Cost: monthly rollup per tenant ✅

**Stage 4 gate: `/usage` numbers match the pinned pricing constants.** Tenant B (Free) sends two calls and one replay.
Fresh database, so Tenant B starts with an empty month.
The expected total was worked out by hand *before* reading `/usage` (rates per 1M tokens):

| Event | input | cached | output (reasoning) | Cost from the pinned rates |
|-------|------:|-------:|-------------------:|---------------------------:|
| e1 | 10,000 | 4,000 | 5,000 (2,000) | (6,000×2.00 + 4,000×0.10 + 5,000×10.00) / 1M = **$0.0624** |
| e2 | 20,000 | 15,000 | 3,000 (1,000) | (5,000×2.00 + 15,000×0.10 + 3,000×10.00) / 1M = **$0.0415** |
| e1 again | replay, must not count | | | – |
| **Expected** | **30,000** | **19,000** | **8,000 (3,000)** | **$0.1039**, 2 calls |

```
$ curl -s localhost:8000/usage -H 'X-API-Key: demo-key-tenant-b'; echo        # before: empty month
{"plan":"free","status":"active","period":{"start":"2026-10-01","resets_on":"2026-11-01"},"calls":{"used":0,"limit":1000},"tokens":{"used":0,"limit":100000,"input":0,"cached_input":0,"output":0,"reasoning":0},"cost_usd":"0"}

$ curl -s -X POST localhost:8000/generate -H 'X-API-Key: demo-key-tenant-b' -H 'Idempotency-Key: usage-e1' \
    -H 'Content-Type: application/json' -d '{"input_tokens":10000,"cached_input_tokens":4000,"output_tokens":5000,"reasoning_tokens":2000}'; echo
{"event_id":1,"cost_usd":"0.0624000000","replayed":false}
$ curl -s -X POST localhost:8000/generate -H 'X-API-Key: demo-key-tenant-b' -H 'Idempotency-Key: usage-e2' \
    -H 'Content-Type: application/json' -d '{"input_tokens":20000,"cached_input_tokens":15000,"output_tokens":3000,"reasoning_tokens":1000}'; echo
{"event_id":2,"cost_usd":"0.0415000000","replayed":false}
$ curl -s -X POST localhost:8000/generate -H 'X-API-Key: demo-key-tenant-b' -H 'Idempotency-Key: usage-e1' \
    -H 'Content-Type: application/json' -d '{"input_tokens":10000,"cached_input_tokens":4000,"output_tokens":5000,"reasoning_tokens":2000}'; echo   # replay of e1
{"event_id":1,"cost_usd":"0.0624000000","replayed":true}

$ curl -s localhost:8000/usage -H 'X-API-Key: demo-key-tenant-b'; echo        # after
{"plan":"free","status":"active","period":{"start":"2026-10-01","resets_on":"2026-11-01"},"calls":{"used":2,"limit":1000},"tokens":{"used":38000,"limit":100000,"input":30000,"cached_input":19000,"output":8000,"reasoning":3000},"cost_usd":"0.1039000000"}

$ uv run python -c 'from app.pricing import cost_usd; print(cost_usd(30_000, 19_000, 8_000))'
0.1039
```
`/usage` matches the hand calculation and the pinned constants exactly. The replay was not counted, and
`tokens.used` = input + output = 38,000.

Tenant A (on Pro through its real Razorpay test-mode subscription `sub_TnQWdwDwIA9Rwv`, paid 2026-10-13) shows the
Pro limits, and B's usage doesn't leak into A's:
```
$ curl -s localhost:8000/usage -H 'X-API-Key: demo-key-tenant-a'; echo
{"plan":"pro","status":"active","period":{"start":"2026-10-01","resets_on":"2026-11-01"},"calls":{"used":0,"limit":50000},"tokens":{"used":0,"limit":10000000,"input":0,"cached_input":0,"output":0,"reasoning":0},"cost_usd":"0"}
```

Automated: `test_rollup_cost_matches_pinned_pricing_constants` (the gate, $0.0834 case),
`test_last_months_usage_is_not_counted` (an event 1 second before the UTC month is excluded; fails if the month
filter is removed), `test_tenant_sees_only_its_own_usage`, `test_empty_month_shows_zero_usage_and_plan_limits`.

## 5 & 6. Cost: token pricing rules, pinned constants, proven totals ✅

Pinned in [`app/pricing.py`](app/pricing.py) (USD per 1M tokens):
```
$ grep -nE '^(MODEL|INPUT_PER_M|CACHED_INPUT_PER_M|OUTPUT_PER_M) =|^# Source' app/pricing.py
4:# Source: https://developers.openai.com/api/docs/pricing (read 2026-10-12).
6:MODEL = "gpt-6.1-sol"
7:INPUT_PER_M = Decimal("2.00")
8:CACHED_INPUT_PER_M = Decimal("0.10")
9:OUTPUT_PER_M = Decimal("10.00")
```

Rules: token fields follow OpenAI's usage shape, where **cached ⊂ input** and **reasoning ⊂ output**. Each category
is priced at its own rate and only then summed. Raw token counts are never added together.
```
cost = ( (input − cached) × 2.00  +  cached × 0.10  +  output × 10.00 ) / 1,000,000
```

Worked example: the request from section 1 (10,000 input of which 4,000 cached; 5,000 output of which 2,000 reasoning):

| Category | Tokens | Rate / 1M | Cost |
|----------|-------:|----------:|-----:|
| Uncached input | 6,000 | $2.00 | $0.0120 |
| Cached input | 4,000 | $0.10 | $0.0004 |
| Output (includes 2,000 reasoning, counted once, at the output rate) | 5,000 | $10.00 | $0.0500 |
| **Total** | | | **$0.0624** |

```
$ uv run python -c 'from app.pricing import cost_usd; print(cost_usd(10_000, 4_000, 5_000))'
0.0624
```
The API returned the same value, stored exactly as `numeric` (section 1: `"cost_usd":"0.0624000000"`).

Why the rules matter, on the same request:
- Adding raw tokens and pricing them all as input: 21,000 × $2.00/M = **$0.042** (wrong).
- Ignoring the cached discount: 10,000 × $2.00/M + 5,000 × $10.00/M = **$0.0700** (overstates the cost).
- Pricing reasoning separately as extra output: 7,000 × $10.00/M + input = **$0.0824** (counts reasoning twice).

## 7. Payments: checkout end-to-end in Razorpay test mode ✅

Real flow on 2026-10-13, on the fresh database from section 4 (migrations applied, then seeded: both tenants on Free).
Tenant A went through `http://localhost:8000/checkout` → Razorpay Checkout (test card, mock bank "Success") →
webhooks via ngrok → `POST /webhooks/razorpay`. Output below is pasted as printed.

```
$ docker compose exec -T db psql -U billing -c "SELECT received_at::time(0) AS received, event_type, event_id FROM webhook_events ORDER BY received_at, event_id"
 received |         event_type         |    event_id    
----------+----------------------------+----------------
 14:34:08 | payment.authorized         | TnQXdSkPbJZhCH
 14:34:09 | payment.captured           | TnQXfQ415pUgva
 14:34:17 | subscription.charged       | TnQXnFXLn5y0qG
 14:34:17 | subscription.authenticated | TnQXnHhANLQX83
 14:34:17 | subscription.activated     | TnQXnHpEaLRowQ
(5 rows)

$ docker compose exec -T db psql -U billing -c "SELECT id, tenant_id, status, updated_at FROM subscriptions"
         id         |              tenant_id               | status |       updated_at       
--------------------+--------------------------------------+--------+------------------------
 sub_TnQWdwDwIA9Rwv | 00000000-0000-7000-8000-00000000000a | active | 2026-10-13 14:34:16+00
(1 row)

$ docker compose exec -T db psql -U billing -c "SELECT name, plan_id, status FROM tenants ORDER BY name"
   name   | plan_id | status 
----------+---------+--------
 Tenant A | pro     | active
 Tenant B | free    | active
(2 rows)

$ docker compose exec -T api .venv/bin/python -c "import razorpay; from app.config import settings as s; c = razorpay.Client(auth=(s.razorpay_key_id, s.razorpay_key_secret.get_secret_value())); sub = c.subscription.fetch('sub_TnQWdwDwIA9Rwv'); plan = c.plan.fetch(sub['plan_id']); print(sub['id'], sub['status'], 'paid_count=%s' % sub['paid_count'], sub['notes']); print(plan['id'], plan['period'], plan['item']['amount'], plan['item']['currency'])"
sub_TnQWdwDwIA9Rwv active paid_count=1 {'tenant_id': '00000000-0000-7000-8000-00000000000a'}
plan_Tn27na0B04mkYA monthly 249900 INR
```
How to read it:
- **Webhooks:** the only five ever received by this database. Tenant A pays (`payment.*`, 14:34:08–09), then
  `subscription.activated` flips it to Pro (14:34:17).
- **Free before:** no "before" query was pasted. Tenant A was seeded on Free (the column default), and nothing but these
  five events has touched this database. So the move to `pro` above is the checkout's doing.
- **Our row matches Razorpay:** `sub_TnQWdwDwIA9Rwv` is `active` in our `subscriptions` mirror and at Razorpay
  (`paid_count=1`). Its `notes.tenant_id` is Tenant A, set server-side by `POST /checkout`.
- **Plan:** monthly, `249900` paise = ₹2,499. The Razorpay calls ran inside the API container with its own settings,
  so no key was printed.

Then `GET /usage` for Tenant A shows the Pro limits (50,000 calls, 10M tokens): see the end of section 4.

## 8. Payments: signature verification, dedup, plan/status sync ✅

Razorpay has no CLI equivalent to `stripe trigger`, so [`scripts/replay_webhook.py`](scripts/replay_webhook.py)
signs an event with the real `RAZORPAY_WEBHOOK_SECRET` and sends it three ways:
```
$ uv run scripts/replay_webhook.py 00000000-0000-7000-8000-00000000000b
1. valid                     evt_replay_fb80e22ed5b2  HTTP 200  {"status":"applied"}
2. replay (same event id)    evt_replay_fb80e22ed5b2  HTTP 200  {"status":"duplicate ignored"}
3. forged signature          evt_replay_fb80e22ed5b2  HTTP 400  {"detail":"Invalid webhook signature."}

$ docker compose exec -T db psql -U billing -c "SELECT name, plan_id, status FROM tenants WHERE name = 'Tenant B'"
   name   | plan_id | status
----------+---------+--------
 Tenant B | pro     | active
```
(Tenant B and the replay rows were reset afterwards.)

Plan/status sync covered by tests: `activated` → pro/active, `halted` → pro/past_due (402), `cancelled` → free,
an older event arriving late is ignored, a replay after a cancel does not re-upgrade, a late `cancelled` from an old
subscription does not downgrade a tenant paying on a newer one (even when both webhooks arrive at once, or the
new one's `authenticated` arrives last in the same second), and a webhook that fails to apply is not marked as seen.

A real production case: a subscription created in the Razorpay dashboard (not by `/checkout`) arrived with
`"notes": []` and crashed the handler (500) seven times while Razorpay retried. After the fix, the next real retry was acknowledged:
```
$ docker compose logs api --no-log-prefix | grep 'no notes.tenant_id'
Razorpay subscription.cancelled for sub_Tn28QmKCFnE40o has no notes.tenant_id; not created by /checkout, ignored
```

## 9. Data model and tenant isolation ✅

```
$ docker compose exec -T db psql -U billing -c '\dt'
 Schema |      Name      | Type  |  Owner
--------+----------------+-------+---------
 public | plans          | table | billing
 public | subscriptions  | table | billing
 public | tenants        | table | billing
 public | usage_events   | table | billing
 public | webhook_events | table | billing

$ docker compose exec -T db psql -U billing -c "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'usage_events'::regclass AND contype IN ('u','c') ORDER BY conname"
 usage_events_check                         | CHECK (((cached_input_tokens >= 0) AND (cached_input_tokens <= input_tokens)))
 usage_events_check1                        | CHECK (((reasoning_tokens >= 0) AND (reasoning_tokens <= output_tokens)))
 usage_events_cost_usd_check                | CHECK ((cost_usd >= (0)::numeric))
 usage_events_input_tokens_check            | CHECK ((input_tokens >= 0))
 usage_events_output_tokens_check           | CHECK ((output_tokens >= 0))
 usage_events_tenant_id_idempotency_key_key | UNIQUE (tenant_id, idempotency_key)
```
Isolation: the tenant comes only from the API key (stored as a SHA-256 hash), never from the request body.
- `test_other_tenants_usage_does_not_count_against_you`: Tenant B at its limit does not affect Tenant A.
- `test_same_idempotency_key_in_two_tenants_is_two_events`: idempotency keys are scoped per tenant.

Schema decisions and data-type reasons: [DESIGN.md](DESIGN.md), [migrations/](migrations/).

## 10. README, architecture diagram, setup ✅

[README.md](README.md): what the engine does, a mermaid architecture diagram (meter / read / payment-sync paths),
plans with the Pro numbers, pinned pricing, setup from a clean clone (`.env` keys, Razorpay plan and webhook, ngrok),
curl examples, endpoints and status codes, project layout, known limits.
The diagram was checked with mermaid's own parser (`flowchart-v2`, parses cleanly).
