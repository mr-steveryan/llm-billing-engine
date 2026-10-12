# Design — LLM Usage Metering & Billing Engine

Three paths, nothing else: **meter** (`POST /generate`), **read** (`GET /usage`), **payment sync** (`POST /webhooks/razorpay`).
Razorpay is the source of truth for payments; our database mirrors it only through signature-verified webhooks.

## Plans

| Plan | API calls / month | Tokens / month | Price |
|------|------------------:|---------------:|-------|
| Free | 1,000             | 100,000        | ₹0 |
| Pro  | 50,000            | 10,000,000     | ₹2,499 / month, set on the Razorpay plan (not stored here) |

Month = calendar month in **UTC**, resetting at 00:00 UTC on the 1st. Token quota counts `input_tokens + output_tokens`
(cached and reasoning tokens are already inside those, so adding them would double-count).

## Data model (`schema.sql`)

| Table | Purpose | Key decisions |
|-------|---------|---------------|
| `plans` | Free/Pro limits | Text PK (`'free'`): stable, readable name. Limits live in the DB so quota checks are one join. |
| `tenants` | The paying customer | `uuidv7()` PK: unguessable but time-ordered. API key stored as **sha256 hash** only. `plan_id`/`status` mirror Razorpay. |
| `subscriptions` | Mirror of Razorpay subscriptions | PK is Razorpay's own `sub_…` id → webhook upserts need no lookup. `status` stored verbatim, no CHECK. |
| `usage_events` | One row per billable call | `UNIQUE (tenant_id, idempotency_key)`. Tokens `bigint`. `cost_usd numeric(18,10)` priced at write time. |
| `webhook_events` | Razorpay events already applied | PK `event_id` = deduplication. |

Data-type choices:
- **`bigint` for tokens:** int32 tops out at ~2.1B, and one busy month of sums passes that.
- **`numeric` for money, never `float`:** floats can't store 0.1 exactly, and money totals must add up exactly.
  10 decimal places, because one cached token costs fractions of a micro-dollar.
- **`timestamptz`:** stores an absolute instant, so "this month in UTC" is unambiguous regardless of server time zone.
- **Cost frozen per event:** if token rates change, past usage keeps the rate it was valued at.

**Tenant isolation:** the tenant is derived from `X-API-Key` → `api_key_hash` lookup, never from the request body.
Every usage query filters by that tenant id. Idempotency keys are scoped per tenant, so tenants can't collide or probe each other's keys.

## Metering API contract

```
POST /generate
X-API-Key: <tenant key>              required → 401 if missing/unknown
Idempotency-Key: <client-chosen id>  required → 400 if missing
{ "input_tokens": 1200, "cached_input_tokens": 200, "output_tokens": 2500, "reasoning_tokens": 500 }
```
Tokens are simulated by the caller. No model is called. Invalid shape (negative, cached > input, reasoning > output) → **422**.

| Outcome | Status | Body |
|---------|--------|------|
| New event recorded | 200 | `{event_id, cost_usd, replayed: false}` |
| Same Idempotency-Key again | 200 | the **original** event, `replayed: true`, no new row |
| Tenant `past_due` (Razorpay payment failed) | **402** | "Payment failed for your subscription; update your payment method." |
| Call or token quota would be exceeded | **429** | "Monthly <calls/tokens> quota of N on plan X exhausted; resets <date> or upgrade to Pro." |

`GET /usage` → `{ plan, status, period: {start, resets_on}, calls: {used, limit},
tokens: {used, limit, input, cached_input, output, reasoning}, cost_usd }`, summed from this month's events.
The per-category token totals let anyone recompute `cost_usd` from the pinned rates. Quota checks and `/usage` read the
same function (`meter.monthly_usage`), so what a tenant is shown and what is enforced can't disagree.

## Idempotency strategy (one transaction)

1. `SELECT … FROM tenants WHERE id = $1 FOR UPDATE`: row lock **first**, so every request for one tenant,
   replays included, runs one at a time. Both the duplicate lookup and the quota check are then race-free.
2. Look up `(tenant_id, idempotency_key)`. If it exists, return that event (replay: no quota charge, no 402).
3. Check status (402), then `used + this_request > limit` (429). A request that **would** cross the limit is rejected whole.
4. Plain `INSERT`. Under the lock a duplicate can't happen; if a bug ever caused one, `UNIQUE (tenant_id, idempotency_key)`
   fails loudly (500) instead of being silently ignored the way `ON CONFLICT DO NOTHING` would.

Proven by tests: removing the `FOR UPDATE` makes both concurrency tests fail (10 parallel requests overshoot the quota / double-insert).

Rejected requests store nothing. We check the quota *before* inserting, unlike the brief's diagram, so a rejected call is never billed.
Known limit: a replay with the same key but a different body returns the original result without comparing bodies.

## Cost formula (rates pinned in `app/pricing.py`, Stage 4)

```
cost = ( (input − cached) × input_rate
       + cached           × cached_input_rate      # cheaper
       + output           × output_rate ) / 1,000,000   # reasoning ⊂ output, billed at output rate once
```
Each category is priced at its own rate first, then summed. Raw token counts are never added before pricing.

## What the customer pays vs. what usage costs

| | Subscription price | `cost_usd` |
|---|---|---|
| Meaning | What the tenant **pays** for the plan (buys quota) | Usage valued at the model provider's rates |
| Amount / currency | Pro ₹2,499 / month (test setup), INR | USD, summed from per-event costs |
| Source of truth | The Razorpay plan | `app/pricing.py` |
| Charged? | Yes, monthly by Razorpay through the mandate | **No.** Not invoiced, no overage billing (out of scope) |

Why it's designed this way:
- **No price column in our DB.** Payment truth lives at Razorpay. The engine reacts only to *whether* the tenant paid
  (`active` / `halted` / `cancelled`), never to the amount, so a price change in the dashboard needs no code change
  and the two can't drift apart.
- **`cost_usd` stays in USD.** The provider prices tokens in USD. Converting to INR would need a pinned exchange rate
  that goes stale, for a figure nobody is charged.
- **What `cost_usd` is for: margin.** A full Pro month (10M tokens) costs $1 (all cached input) to $100 (all output)
  at the pinned rates. Set against ₹2,499, an output-heavy Pro tenant can cost more than they pay. Overage billing
  would be the fix, and is a stretch goal outside the brief.

## Payment sync (Razorpay test mode)

`POST /webhooks/razorpay`: verify `X-Razorpay-Signature` (HMAC-SHA256 of the **raw** body), returning 400 on a forged one →
insert `x-razorpay-event-id` into `webhook_events` (conflict → 200 "duplicate ignored") → upsert subscription + tenant,
**in the same transaction** as the dedup insert, so an event can never be marked seen without being applied.

The tenant is set from the subscription **status** in the payload, not the event name, so every `subscription.*`
event (`activated`, `charged`, `updated`, `halted`, `cancelled`, ...) follows one rule:

| Subscription status | Tenant plan / status |
|---------------------|----------------------|
| `active` | pro / active |
| `halted` (all payment retries failed) | pro / past_due → 402 |
| `cancelled`, `completed`, `expired` | free / active |
| `created`, `authenticated`, `pending`, `paused` | mirrored in `subscriptions`; tenant unchanged (`pending` = still retrying, keep access) |

- **Out-of-order delivery:** `subscriptions.updated_at` holds the Razorpay `created_at` of the last applied event.
  An older event is skipped, so a late `activated` can't undo a `cancelled`.
- **Checkout:** `POST /checkout` creates the subscription with `notes.tenant_id` set server-side from the API key, and
  returns 409 if the tenant is already Pro (a second subscription would double-charge). The browser's success
  callback is ignored; only the webhook changes the plan.
- **Replay locally:** `uv run scripts/replay_webhook.py` sends valid → replayed → forged.

## Known limits

- **One tenant, several subscriptions.** Any `cancelled` event moves its tenant to Free, even when another of that
  tenant's subscriptions is still active. `/checkout` refuses tenants already on Pro, but abandoned (unpaid) checkouts
  leave `created` subscriptions behind; cancelling one of those while the tenant is Pro would downgrade it.
  Fix if needed: derive the plan from *all* of the tenant's subscriptions (Pro if any is active).
- **Replay body not compared.** Same `Idempotency-Key` with a different body returns the original result.
- **Pricing tiers.** No cache-write or long-context (>272K) rates; usage events don't carry that information.

Out of scope: invoices, proration, overage billing, dashboard, admin API, migrations tool.
