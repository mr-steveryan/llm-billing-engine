# LLM Usage Metering & Billing Engine

A small, deliberately minimal billing engine for LLM usage. Tenants get a monthly quota from their plan (Free / Pro).
Every billable call is metered exactly once, checked against the quota, and valued at the model provider's token rates.
Tenants **pay** only the subscription price; the token cost is a figure, not a charge (see
[What the customer pays vs. what usage costs](#what-the-customer-pays-vs-what-usage-costs)).
Pro subscriptions are paid through **Razorpay** (test mode); the database mirrors Razorpay only through signed webhooks.

- **Meter:** `POST /generate` records one usage event per `Idempotency-Key`. Over quota → **429**; subscription halted after all payment retries failed → **402**.
- **Read:** `GET /usage` shows this month's calls, tokens and cost against the plan's limits.
- **Pay:** `/checkout` → Razorpay Checkout → signed webhook → tenant plan updated.

AI tokens are simulated: the caller sends token counts, and no model is called.

| Doc | What's in it |
|-----|--------------|
| [DESIGN.md](DESIGN.md) | Schema, API contract, idempotency strategy, cost formula, webhook handling, and the reasons behind each |
| [EVIDENCE.md](EVIDENCE.md) | One captured proof per requirement |
| [BUILDLOG.md](BUILDLOG.md) | Stage-by-stage build record: decisions, bugs, fixes |

## Architecture

```mermaid
flowchart LR
    client([Client app])
    browser([Browser])
    rzp[(Razorpay<br/>test mode)]

    subgraph api [FastAPI service]
        gen["POST /generate"]
        meter["meter.record()<br/>1. lock tenant row<br/>2. same key? return original<br/>3. past_due? 402<br/>4. over quota? 429<br/>5. price + insert event"]
        usage["GET /usage<br/>meter.monthly_usage()"]
        co["GET/POST /checkout"]
        wh["POST /webhooks/razorpay<br/>1. verify HMAC (forged → 400)<br/>2. dedup event id (replay → ignored)<br/>3. sync subscription + tenant"]
    end

    db[(Postgres 18<br/>plans · tenants · subscriptions<br/>usage_events · webhook_events)]

    client -- "X-API-Key + Idempotency-Key<br/>token counts" --> gen --> meter --> db
    client -- X-API-Key --> usage --> db
    browser --> co -- "create subscription<br/>notes.tenant_id" --> rzp
    browser -- pays in Razorpay Checkout --> rzp
    rzp -- "signed webhook<br/>(via ngrok locally)" --> wh --> db
```

Plain-text version:
```
 Client app ──X-API-Key, Idempotency-Key, token counts──▶ POST /generate
                                                            └─ meter.record(): lock tenant → same key? return original
                                                               → past_due? 402 → over quota? 429 → price + store event
 Client app ──X-API-Key──▶ GET /usage ── this month's calls, tokens, cost vs. plan limits
 Browser ──▶ /checkout ──create subscription (notes.tenant_id)──▶ Razorpay (test mode)
 Razorpay ──signed webhook (ngrok locally)──▶ POST /webhooks/razorpay
                                               └─ verify HMAC (forged → 400) → dedup event id (replay → ignored)
                                                  → sync subscription + tenant plan
 All of the above ──▶ Postgres 18: plans · tenants · subscriptions · usage_events · webhook_events
```

One transaction per request: a rejected call stores nothing, and a webhook's dedup row commits together with the plan change.

## Plans

| Plan | API calls / month | Tokens / month | Price | How a tenant gets it |
|------|------------------:|---------------:|-------|----------------------|
| Free | 1,000 | 100,000 | ₹0 | default |
| **Pro** | **50,000** | **10,000,000** | ₹2,499 / month, set on the Razorpay plan (not in code) | Razorpay subscription (`subscription.activated`) |

Months are calendar months in **UTC**. Token quota counts `input + output` tokens (cached and reasoning tokens are
already part of those). A request that **would** cross a limit is rejected whole.

## Token cost (provider rates)

The cost figure reported by `/usage` (not charged to anyone). Pinned in [`app/pricing.py`](app/pricing.py): OpenAI **gpt-6.1-sol**, Standard tier, short context
([source](https://developers.openai.com/api/docs/pricing), read 2026-10-12), USD per 1M tokens:

| Input | Cached input | Output (includes reasoning) |
|------:|-------------:|----------------------------:|
| $2.00 | $0.10 | $10.00 |

```
cost = ((input − cached) × 2.00 + cached × 0.10 + output × 10.00) / 1,000,000
```
Token fields follow OpenAI's usage shape: `cached_input_tokens ⊂ input_tokens` and `reasoning_tokens ⊂ output_tokens`.
Each category is priced at its own rate before summing, and reasoning is counted once, as output.

## What the customer pays vs. what usage costs

Two different numbers, in two currencies. Only the first is ever charged.

| | Subscription price | `cost_usd` in `/usage` |
|---|---|---|
| What it is | What the tenant **pays** for the plan | What the tenant's usage **would cost** at the model provider's token rates |
| Amount | Free ₹0; Pro **₹2,499 / month** (our test setup) | Sum of the per-event costs this month |
| Currency | INR, the Razorpay plan's currency | USD, the provider's price currency |
| Where it's set | On the **Razorpay plan** in the dashboard; not stored or known by this engine | `app/pricing.py` (pinned) |
| Charged? | **Yes**, automatically every month by Razorpay through the mandate | **No.** No invoice, no overage charge (out of scope per the brief) |

- **The subscription buys quota.** ₹2,499/month gives 50,000 calls and 10,000,000 tokens. The engine only learns
  *whether* the tenant paid (webhooks: `charged` keeps Pro, `halted` → 402, `cancelled` → Free), never *how much*.
  Changing the price in the Razorpay dashboard needs no code change.
- **`cost_usd` is a cost figure, not a bill.** It answers "what did this tenant's usage cost us at the provider's
  rates?", which is what the brief's "monthly usage rolls up into a cost figure per tenant" asks for. It stays in USD
  because the provider prices in USD; converting would mean pinning an exchange rate for a number nobody is charged.
- **Why it matters: margin.** At the pinned rates, a Pro tenant using its full 10M tokens costs between **$1**
  (all cached input) and **$100** (all output). Compare that with ₹2,499 at the current exchange rate: a heavy,
  output-heavy Pro tenant can cost more than they pay. `cost_usd` is how you would see that.

## Setup

**Prerequisites:** Docker (OrbStack on macOS) and [uv](https://docs.astral.sh/uv/) for tests. Everything is free;
no step needs a credit card. For real test-mode payments you also need a Razorpay account in **Test Mode** and
[ngrok](https://ngrok.com) (free plan, no payment method needed).

**Run** (one command; the same one is in [`capstone.yaml`](capstone.yaml)):
```
[ -f .env ] || cp .env.example .env; docker compose up -d --build --wait
```
It creates `.env` from the placeholders if you don't have one, builds the image, and returns once Postgres is healthy and
the API is up. On every start the API first applies any new migrations from `migrations/` (each exactly once).

**Stop** (keeps your data): `docker compose down`. Start again with the Run command.
`docker compose down -v` also deletes the database; schema changes don't need it (they are migrations).

**Seed** demo data (two tenants, both on Free). Safe to run any number of times; existing tenants are left as they are:
```
docker compose exec -T db psql -U billing -d billing -v ON_ERROR_STOP=1 -f /seed/seed.sql
```
Demo API keys: `demo-key-tenant-a`, `demo-key-tenant-b`. Check: `curl localhost:8000/health` → `{"status":"ok"}`.

**All stack commands** (run from the repo root):

| To | Command | Database data |
|----|---------|---------------|
| Start, or rebuild after code changes | `docker compose up -d --build --wait` | kept |
| Pause (containers stay, nothing runs) | `docker compose stop` | kept |
| Resume after a pause | `docker compose start` | kept |
| Stop and remove containers and network | `docker compose down` | kept |
| Apply `.env` changes | `docker compose up -d --force-recreate api` | kept |
| See what is running and healthy | `docker compose ps` | kept |
| Follow the API's logs | `docker compose logs -f api` | kept |
| Wipe and start over | `docker compose down -v`, then Run and Seed | **deleted** |

**Test** (on the host; the runtime image has no test tools):
```
uv run pytest
```
Tests use a separate `billing_test` database, rebuilt on every run, so your dev data is never touched.
The `db` container must be running. Don't run two test sessions at once.

**Configuration (`.env`).** With the placeholder values from `.env.example`, metering, quotas and `/usage` work fully.
Checkout and real webhooks need real Razorpay **test-mode** values:

| Key | Where it comes from |
|-----|---------------------|
| `RAZORPAY_KEY_ID` | Dashboard (Test Mode) → Account & Settings → API Keys (`rzp_test_…`) |
| `RAZORPAY_KEY_SECRET` | shown once, next to the key id |
| `RAZORPAY_WEBHOOK_SECRET` | any long random string; paste the same value in the dashboard webhook (below) |
| `RAZORPAY_PLAN_ID` | Dashboard → Subscriptions → Plans → create "Pro", monthly (`plan_…`) |
| `DATABASE_URL` | only used when running the API outside Docker; compose sets its own |

The app refuses to start if a key is missing, empty or misspelled, and `.env` must contain only these keys.
After `.env` changes: `docker compose up -d --force-recreate api`. Schema changes: add a new
`migrations/NNNN_name.sql` (never edit an applied one), then `docker compose up -d --build --wait`; data is kept.

**Webhooks (only for real payments).** Razorpay must reach your laptop:
```
ngrok http --url=<your-ngrok-dev-domain> 8000
```
In the Razorpay dashboard → Webhooks, add `https://<your-ngrok-dev-domain>/webhooks/razorpay` (no `:8000`),
use your `RAZORPAY_WEBHOOK_SECRET`, and tick **all `subscription.*` events**. Without those, only `payment.*`
events arrive and plans never change.

## Try it

```
# Meter a call (same Idempotency-Key again → same event, "replayed": true)
curl -X POST localhost:8000/generate \
  -H 'X-API-Key: demo-key-tenant-a' -H 'Idempotency-Key: req-1' -H 'Content-Type: application/json' \
  -d '{"input_tokens":10000,"cached_input_tokens":4000,"output_tokens":5000,"reasoning_tokens":2000}'
# {"event_id":…,"cost_usd":"0.0624000000","replayed":false}

# This month's usage and cost
curl localhost:8000/usage -H 'X-API-Key: demo-key-tenant-a'

# Upgrade to Pro: open in a browser, pay with the domestic test card 4718 6091 0820 4366,
# any CVV and future expiry, then click "Success" on the mock bank page
open http://localhost:8000/checkout

# Replay a signed webhook without Razorpay: valid → duplicate ignored → forged 400
uv run scripts/replay_webhook.py [tenant_id]
```

## Endpoints

| Method | Path | Auth | Responses |
|--------|------|------|-----------|
| POST | `/generate` | `X-API-Key`, `Idempotency-Key` | 200 event · 400 no idempotency key · 401 bad key · 402 subscription halted (all payment retries failed) · 422 invalid tokens · 429 quota |
| GET | `/usage` | `X-API-Key` | 200 usage for the current month · 401 |
| GET | `/checkout` | – | Checkout page |
| POST | `/checkout` | `X-API-Key` | 200 `{key_id, subscription_id}` (an unpaid subscription is reused after checking its real status at Razorpay) · 401 · 409 already Pro, or a subscription already authorised/live at Razorpay · 502 Razorpay unreachable or erroring (nothing stored) |
| POST | `/webhooks/razorpay` | Razorpay signature | 200 applied / duplicate ignored / ignored: older than last applied event / ignored: subscription has no tenant_id / ignored: unknown tenant · 400 bad signature, missing event id or malformed event |
| GET | `/health` | – | 200 when the database is reachable |

## Project layout

```
app/main.py        HTTP only: input checks, status codes
app/meter.py       metering: record() + monthly_usage(), quota rules
app/pricing.py     pinned token prices + cost formula
app/billing.py     Razorpay checkout + webhook: signature, dedup, plan sync
app/config.py      settings from .env (pydantic-settings)
app/db.py          Postgres connection
app/migrate.py     applies new migrations once, at API startup
app/checkout.html  Razorpay checkout page
migrations/        schema as numbered SQL files: tables, constraints, plan limits, indexes
seed.sql           demo tenants (dev only), safe to re-run
scripts/replay_webhook.py
tests/             pytest against real Postgres
capstone.yaml      evaluator manifest: run, seed, test, base_url, endpoints
.env.example       every environment variable, with placeholder values
```

## Limitations

Honest list of what this build does not do, or does only partly:

- **Test mode only.** Payments run in Razorpay test mode; no real money has moved and live mode was never tried.
- **Token counts are trusted.** The caller reports its own token counts (the brief allows simulated tokens) and no model is
  called, so a client could under-report. A real deployment would meter at the model proxy instead.
- **The token cost is a figure, not a charge.** No invoices, proration or overage billing (out of scope per the brief).
  An output-heavy Pro tenant can cost more at provider rates than the ₹ subscription brings in.
- **One pricing tier.** No cache-write or long-context (>272K) rates; usage events don't carry that information.
- **Idempotent replays aren't compared.** The same `Idempotency-Key` with a different body returns the original result.
- **Local webhooks need a tunnel** (ngrok) so Razorpay can reach the laptop.
- **No admin API.** Tenants come only from `seed.sql`, and the demo API keys there are public on purpose.
- **Small-scale choices.** One database connection per request (no pool), and `/checkout` holds the tenant's row lock
  during the Razorpay API call. Fine for a capstone, not for heavy traffic.

More detail and the reasoning: [DESIGN.md](DESIGN.md#known-limits).
