# Design — LLM Usage Metering & Billing Engine

Three core paths: **meter** (`POST /generate`), **read** (`GET /usage`), **payment sync** (`POST /webhooks/razorpay`),
plus `GET`/`POST /checkout` to start a subscription and `GET /health`.
Razorpay is the source of truth for payments; our database mirrors it only through signature-verified webhooks.

## Brief → this build

The brief is written for Stripe. Payments here use **Razorpay** (test mode), so these names differ; the behaviour is the same.

| Brief says | This build | Notes |
|------------|------------|-------|
| `checkout.session.completed` | `subscription.activated` | Status `active` → tenant Pro |
| `customer.subscription.updated` | `subscription.updated`, plus `subscription.halted` | Status mirrored; `halted` (all retries failed) → `past_due` → 402 |
| `customer.subscription.deleted` | `subscription.cancelled` (also `completed`, `expired`) | Tenant back to Free |
| Signature header (`Stripe-Signature`) | `X-Razorpay-Signature`: HMAC-SHA256 of the raw body | Forged → 400 |
| Stripe event id for dedup | `x-razorpay-event-id` header → `webhook_events` PK | Replay → `"duplicate ignored"` |
| `POST /webhooks/stripe` | `POST /webhooks/razorpay` | |
| Stripe CLI to replay events | `uv run scripts/replay_webhook.py` | Razorpay has no CLI; the script signs events with your secret: valid → replayed → forged |
| Stripe test mode | Razorpay **test mode** (`rzp_test_` keys, test cards) | No real money moves |
| `MeterService.record(tenant, type, qty, idempotencyKey)` | `meter.record(conn, tenant_id, idempotency_key, usage)` | One event = one **API call** carrying its **AI token** counts, so both usage types are recorded by one call; the tenant comes from the API key |
| "Store usage event, then quota check" (diagram order) | Quota check **first**, then store | Storing first would record and count rejected requests |
| `GET /usage → { used, limit, cost }` | Same, split into `calls` and `tokens`, plus per-category token totals | Totals let anyone recompute `cost_usd` from the pinned rates |

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
- **`bigint` for tokens:** headroom for single values and plan limits beyond int32's ~2.1B, and cheap at this scale.
  (Monthly sums aren't the reason: Postgres widens `sum()` results anyway.)
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
| Tenant `past_due` (subscription `halted`: all payment retries failed) | **402** | "Payment failed for your subscription; update your payment method to continue." |
| Call quota would be exceeded | **429** | "Monthly API call quota of N on plan 'X' exhausted; resets <date> (UTC)." |
| Token quota would be exceeded | **429** | "Monthly token quota of N on plan 'X' would be exceeded (<used> used + <requested> requested); resets <date> (UTC)." |

For Free tenants both 429 messages end with " or upgrade to Pro" before the final period.
A failed charge alone doesn't block: while Razorpay retries (subscription `pending`) the tenant keeps access.

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

Rejected requests store nothing. We check the quota *before* inserting, unlike the brief's diagram, so a rejected call is never recorded or counted.
Known limit: a replay with the same key but a different body returns the original result without comparing bodies.

## Cost formula (rates pinned in `app/pricing.py`)

```
cost = ( (input − cached) × input_rate
       + cached           × cached_input_rate      # cheaper
       + output           × output_rate ) / 1,000,000   # reasoning ⊂ output, priced at output rate once
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
A subscription event first locks its tenant row, so one tenant's webhooks apply one at a time.

The tenant is set from the subscription **status** in the payload, not the event name, so every `subscription.*`
event (`activated`, `charged`, `updated`, `halted`, `cancelled`, ...) follows one rule:

| Subscription status | Tenant plan / status |
|---------------------|----------------------|
| `active` | pro / active |
| `halted` (all payment retries failed) | pro / past_due → 402 |
| `cancelled`, `completed`, `expired` | free / active, unless another of the tenant's subscriptions is `active`/`halted`/`authenticated` |
| `created`, `authenticated`, `pending`, `paused` | mirrored in `subscriptions`; tenant unchanged (`pending` = still retrying, keep access) |

- **Out-of-order delivery:** `subscriptions.updated_at` holds the Razorpay `created_at` of the last applied event.
  An older event is skipped (200 `"ignored: older than last applied event"`), so a late `activated` can't undo a `cancelled`.
  Timestamps are whole seconds and a tie is applied. In real runs `activated`, `authenticated` and `charged` arrive in the
  same second in any order, so a paid subscription's row can read `authenticated` until its next charge; that is why
  the downgrade check above counts `authenticated` as live.
- **Checkout:** `POST /checkout` locks the tenant row, then: 409 if the tenant is already Pro. If a `created` or
  `authenticated` subscription exists, the newest one's **real status is fetched from Razorpay** first, because our
  row is only a mirror and is stale if a webhook was missed:
  still `created` → **reused** (same `subscription_id`); live (`authenticated`/`active`/`pending`/`halted`/`paused`,
  e.g. paid but the webhook never arrived) → **409**, never a second subscription; dead (`cancelled`/`completed`/`expired`)
  → a fresh one is created. The fetch is read-only: the mirror and the tenant's plan still change only through webhooks.
  A new subscription gets `notes.tenant_id` set server-side from the API key and is recorded as `created` immediately,
  so repeat clicks find it. Double-clicks, two tabs, or a missed webhook can't create two live subscriptions.
  The browser's success callback is ignored; only the webhook changes the plan.
- **Replay locally:** `uv run scripts/replay_webhook.py` sends valid → replayed → forged.

## Known limits

- **Replay body not compared.** Same `Idempotency-Key` with a different body returns the original result.
- **Pricing tiers.** No cache-write or long-context (>272K) rates; usage events don't carry that information.

Out of scope: invoices, proration, overage billing, dashboard, admin API, migrations tool.
