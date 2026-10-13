# Evidence

One proof per requirement. Every block is real output captured on **2026-10-12** against the running
stack (`docker compose up`), using the seeded demo tenants:

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
tests/test_checkout.py::test_first_checkout_creates_and_stores_subscription_for_key_tenant PASSED
tests/test_checkout.py::test_repeat_checkout_reuses_unpaid_subscription PASSED
tests/test_checkout.py::test_checkout_refused_while_mandate_authenticated PASSED
tests/test_checkout.py::test_stale_authenticated_row_dead_at_razorpay_does_not_block_checkout PASSED
tests/test_checkout.py::test_razorpay_failure_stores_nothing PASSED
tests/test_checkout.py::test_later_activation_webhook_still_upgrades PASSED
tests/test_checkout.py::test_checkout_refuses_tenant_already_on_pro PASSED
tests/test_checkout.py::test_reuse_asks_razorpay_first PASSED
tests/test_checkout.py::test_missed_webhook_paid_subscription_blocks_a_second_one PASSED
tests/test_checkout.py::test_dead_subscription_at_razorpay_starts_a_fresh_one PASSED
tests/test_checkout.py::test_cancelling_after_a_stuck_authenticated_row_returns_tenant_to_free PASSED
tests/test_checkout.py::test_razorpay_calls_have_a_timeout PASSED
tests/test_checkout.py::test_simultaneous_checkouts_create_one_subscription PASSED
tests/test_metering.py::test_same_request_twice_creates_one_event PASSED
tests/test_metering.py::test_concurrent_retries_of_one_request_create_one_event PASSED
tests/test_metering.py::test_replay_returns_original_even_when_tenant_is_now_blocked PASSED
tests/test_metering.py::test_call_quota_boundary_returns_429 PASSED
tests/test_metering.py::test_token_quota_rejects_a_request_that_would_cross_the_limit PASSED
tests/test_metering.py::test_concurrent_requests_cannot_overshoot_quota PASSED
tests/test_metering.py::test_past_due_tenant_gets_402 PASSED
tests/test_metering.py::test_pro_plan_has_higher_limits PASSED
tests/test_metering.py::test_other_tenants_usage_does_not_count_against_you PASSED
tests/test_metering.py::test_same_idempotency_key_in_two_tenants_is_two_events PASSED
tests/test_metering.py::test_missing_or_bad_api_key_is_401 PASSED
tests/test_metering.py::test_missing_idempotency_key_is_400 PASSED
tests/test_metering.py::test_impossible_token_counts_are_422 PASSED
tests/test_metering.py::test_cost_uses_category_rates_not_a_token_sum PASSED
tests/test_usage.py::test_empty_month_shows_zero_usage_and_plan_limits PASSED
tests/test_usage.py::test_rollup_cost_matches_pinned_pricing_constants PASSED
tests/test_usage.py::test_last_months_usage_is_not_counted PASSED
tests/test_usage.py::test_tenant_sees_only_its_own_usage PASSED
tests/test_usage.py::test_usage_requires_api_key PASSED
tests/test_webhooks.py::test_activated_flips_tenant_free_to_pro PASSED
tests/test_webhooks.py::test_forged_signature_is_rejected_and_changes_nothing PASSED
tests/test_webhooks.py::test_non_ascii_signature_is_rejected_not_crashed PASSED
tests/test_webhooks.py::test_missing_signature_is_rejected PASSED
tests/test_webhooks.py::test_replayed_event_is_ignored PASSED
tests/test_webhooks.py::test_out_of_order_older_event_does_not_win PASSED
tests/test_webhooks.py::test_halted_subscription_blocks_usage_with_402 PASSED
tests/test_webhooks.py::test_cancelled_returns_tenant_to_free PASSED
tests/test_webhooks.py::test_old_subscriptions_late_cancel_does_not_downgrade_paying_tenant PASSED
tests/test_webhooks.py::test_same_second_authenticated_last_still_protects_paying_tenant PASSED
tests/test_webhooks.py::test_concurrent_old_cancel_cannot_downgrade_tenant_mid_activation PASSED
tests/test_webhooks.py::test_failed_apply_does_not_mark_event_seen PASSED
tests/test_webhooks.py::test_non_subscription_event_is_recorded_but_changes_nothing PASSED
tests/test_webhooks.py::test_subscription_not_created_by_checkout_is_acknowledged_not_crashed PASSED
============================== 46 passed in 1.89s ==============================
```
Tests run against a separate `billing_test` database, rebuilt from `schema.sql` on every run.

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
Before this, Tenant B's `usage_events` were deleted again and its status reset to `active` after section 3, hence the empty month.
The expected total was worked out by hand *before* reading `/usage` (rates per 1M tokens):

| Event | input | cached | output (reasoning) | Cost from the pinned rates |
|-------|------:|-------:|-------------------:|---------------------------:|
| e1 | 10,000 | 4,000 | 5,000 (2,000) | (6,000×2.00 + 4,000×0.10 + 5,000×10.00) / 1M = **$0.0624** |
| e2 | 20,000 | 15,000 | 3,000 (1,000) | (5,000×2.00 + 15,000×0.10 + 3,000×10.00) / 1M = **$0.0415** |
| e1 again | replay, must not count | | | – |
| **Expected** | **30,000** | **19,000** | **8,000 (3,000)** | **$0.1039**, 2 calls |

```
$ curl -s localhost:8000/usage -H 'X-API-Key: demo-key-tenant-b'        # before: empty month
{"plan": "free", "status": "active", "period": {"start": "2026-10-01", "resets_on": "2026-11-01"},
 "calls": {"used": 0, "limit": 1000},
 "tokens": {"used": 0, "limit": 100000, "input": 0, "cached_input": 0, "output": 0, "reasoning": 0},
 "cost_usd": "0"}

$ curl -s -X POST localhost:8000/generate -H 'X-API-Key: demo-key-tenant-b' -H 'Idempotency-Key: usage-e1' \
    -H 'Content-Type: application/json' -d '{"input_tokens":10000,"cached_input_tokens":4000,"output_tokens":5000,"reasoning_tokens":2000}'
{"event_id":16069,"cost_usd":"0.0624000000","replayed":false}
$ curl ... -H 'Idempotency-Key: usage-e2' -d '{"input_tokens":20000,"cached_input_tokens":15000,"output_tokens":3000,"reasoning_tokens":1000}'
{"event_id":16070,"cost_usd":"0.0415000000","replayed":false}
$ curl ... -H 'Idempotency-Key: usage-e1' (same body as e1)
{"event_id":16069,"cost_usd":"0.0624000000","replayed":true}

$ curl -s localhost:8000/usage -H 'X-API-Key: demo-key-tenant-b'        # after
{"plan": "free", "status": "active", "period": {"start": "2026-10-01", "resets_on": "2026-11-01"},
 "calls": {"used": 2, "limit": 1000},
 "tokens": {"used": 38000, "limit": 100000, "input": 30000, "cached_input": 19000, "output": 8000, "reasoning": 3000},
 "cost_usd": "0.1039000000"}

$ uv run python -c 'from app.pricing import cost_usd; print(cost_usd(30_000, 19_000, 8_000))'
0.1039
```
`/usage` matches the hand calculation and the pinned constants exactly. The replay was not counted, and
`tokens.used` = input + output = 38,000.

Tenant A (on Pro through its real Razorpay subscription) shows the Pro limits, and B's usage doesn't leak into A's:
```
$ curl -s localhost:8000/usage -H 'X-API-Key: demo-key-tenant-a'
{"plan": "pro", "status": "active", ..., "calls": {"used": 0, "limit": 50000},
 "tokens": {"used": 0, "limit": 10000000, ...}, "cost_usd": "0"}
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

Real flow on 2026-10-12. All old subscriptions were cancelled in the Razorpay dashboard, then Tenant A
went through `http://localhost:8000/checkout` → Razorpay Checkout (test card, mock bank "Success") →
webhooks via ngrok → `POST /webhooks/razorpay`.
```
$ docker compose exec -T db psql -U billing -c "SELECT received_at::time(0) AS received, event_type, event_id FROM webhook_events WHERE received_at >= '2026-10-12 16:27+00' ORDER BY received_at"
 received |         event_type         |    event_id
----------+----------------------------+----------------
 16:27:37 | subscription.cancelled     | Tn3wOvtnZxfN43     <- old subscriptions cancelled in the dashboard
 16:27:44 | subscription.cancelled     | Tn3wW8UxpLCyWj
 16:28:00 | subscription.cancelled     | Tn3wnmEoDRyxwb
 16:29:17 | payment.authorized         | Tn3y9anmNacGz7     <- Tenant A pays
 16:29:19 | payment.captured           | Tn3yBngKg7gwLQ
 16:29:23 | subscription.activated     | Tn3yGgP3fDWgQg     <- flips Tenant A to Pro
 16:29:23 | subscription.authenticated | Tn3yGl11aoEEvJ
 16:29:23 | subscription.charged       | Tn3yGoxLiWxTxg

$ docker compose exec -T db psql -U billing -c "SELECT id, tenant_id, status FROM subscriptions WHERE status = 'active'"
         id         |              tenant_id               | status
--------------------+--------------------------------------+--------
 sub_Tn3xdSmNYF8bJo | 00000000-0000-7000-8000-00000000000a | active

$ docker compose exec -T db psql -U billing -c "SELECT name, plan_id, status FROM tenants ORDER BY name"
   name   | plan_id | status
----------+---------+--------
 Tenant A | pro     | active      <- was free/active before checkout
 Tenant B | free    | active
```
Razorpay's API, queried at the same time, listed `sub_Tn3xdSmNYF8bJo active {'tenant_id': '…000a'}`: the mirror matches.
Earlier the same day, Tenant B completed the same flow (Free → Pro) and was moved back to Free by its `subscription.cancelled`.

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

Schema decisions and data-type reasons: [DESIGN.md](DESIGN.md), [schema.sql](schema.sql).

## 10. README, architecture diagram, setup ✅

[README.md](README.md): what the engine does, a mermaid architecture diagram (meter / read / payment-sync paths),
plans with the Pro numbers, pinned pricing, setup from a clean clone (`.env` keys, Razorpay plan and webhook, ngrok),
curl examples, endpoints and status codes, project layout, known limits.
The diagram was checked with mermaid's own parser (`flowchart-v2`, parses cleanly).
