# Design — LLM Usage Metering & Billing Engine

Three paths, nothing else: **meter** (`POST /generate`), **read** (`GET /usage`), **payment sync** (`POST /webhooks/razorpay`).
Razorpay is the source of truth for payments; our database mirrors it only through signature-verified webhooks.

## Plans

| Plan | API calls / month | Tokens / month | Price |
|------|------------------:|---------------:|-------|
| Free | 1,000             | 100,000        | ₹0 |
| Pro  | 50,000            | 10,000,000     | set on the Razorpay test plan |

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
- **`numeric` for money, never `float`:** floats can't store 0.1 exactly, and invoices must add up to the cent.
  10 decimal places, because one cached token costs fractions of a micro-dollar.
- **`timestamptz`:** stores an absolute instant, so "this month in UTC" is unambiguous regardless of server time zone.
- **Cost frozen per event:** if prices change, past usage keeps the price it was charged at.

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

`GET /usage` → `{ period_start, plan, calls: {used, limit}, tokens: {used, limit}, cost_usd }`, summed from this month's events.

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

## Payment sync (Razorpay test mode)

`POST /webhooks/razorpay`: verify `X-Razorpay-Signature` (HMAC-SHA256 of the **raw** body), returning 400 on a forged one →
insert `x-razorpay-event-id` into `webhook_events` (conflict → 200 "duplicate ignored") → upsert subscription + tenant,
**in the same transaction** as the dedup insert, so an event can never be marked seen without being applied.

| Razorpay event | Tenant plan / status |
|----------------|----------------------|
| `subscription.activated` | pro / active |
| `subscription.halted` | pro / past_due → 402 |
| `subscription.cancelled` | free / active |
| `subscription.updated` | subscription status mirrored |

Out of scope: invoices, proration, overage billing, dashboard, admin API, migrations tool.
