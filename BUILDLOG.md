# Build Log

A running record of how the engine was built: what each stage delivered, the decisions taken (and by whom),
what went wrong and how it was fixed. Newest stage last. Built with Claude Code:
every design decision below was proposed with its reasoning and approved or changed by me before it was built.

## 2026-10-10: Planning

- Brief: tenant plans (Free/Pro), usage metering, quota enforcement (429/402), token cost rules, subscription payments.
- **Decided:** Razorpay test mode instead of Stripe. The brief's Stripe event names were mapped to Razorpay's
  (`subscription.activated` / `updated` / `halted` / `cancelled`), and a replay script stands in for the Stripe CLI.
- **Decided (changed from the brief's diagram):** check the quota *before* storing the usage event. Storing first would bill rejected requests.
- **Decided:** 429 = quota exhausted, 402 = subscription payment failed. Each code means one thing.
- **Decided:** token fields use OpenAI's shape (cached ⊂ input, reasoning ⊂ output).
- **Decided:** Pro = 50,000 calls / 10,000,000 tokens per month. Tenant = customer, no separate customers table.
- Stack: FastAPI + uv, Postgres in Docker, docker compose on OrbStack, psycopg with plain SQL (no ORM, no migrations tool).

## 2026-10-12: Stage 0, Skeleton. Commit `b5c0303`

- Built: uv project (Python 3.12 pinned to match the Docker image), Dockerfile, compose (api + Postgres), `GET /health` doing `SELECT 1`.
- **Changed: Postgres 16 → 18.** 16 had been picked out of habit; 18 is the current release and has built-in `uuidv7()`.
  - Pitfall avoided: from Postgres 18 the Docker image stores data under `/var/lib/postgresql/18/docker` and expects the
    volume at `/var/lib/postgresql`. Only changing the tag would have silently written data to a throwaway volume.
    Verified by recreating the container and checking a table survived.
  - Reused the locally cached `postgres:18` image from another project. Volumes stay separate per compose project.
- Hiccup: the first Docker Hub pull returned "Bad Gateway". A retry worked.

## 2026-10-12: Stage 1, Design. Commit `9d13a7f`

- Built: `schema.sql` (plans, tenants, subscriptions, usage_events, webhook_events), `seed.sql` (2 demo tenants), `DESIGN.md`.
- Key decisions (reasons are in `schema.sql` comments and `DESIGN.md`):
  - `uuidv7()` tenant ids: unguessable, but time-ordered so inserts stay index-friendly.
  - API keys stored only as SHA-256 hashes.
  - `bigint` tokens, `numeric(18,10)` money (never float), `timestamptz` with UTC months.
  - `UNIQUE (tenant_id, idempotency_key)` as the database-level idempotency guarantee.
  - Cost frozen per event at write time.
- Verified by hand in psql: every constraint rejects bad data (duplicate key, cached > input, unknown plan, duplicate
  webhook id), and the monthly query uses the index.
- Learned: failed inserts still consume identity ids, so ids are never used as counts.

## 2026-10-12: Stage 2, Core billing logic. Commit `2908ca0`

- Built: `app/pricing.py` (pinned rates), `app/meter.py` (`record()`), `POST /generate`, 14 tests against real Postgres.
- **Decided:** mirror a real model's prices: OpenAI `gpt-6.1-sol`, $2.00 / $0.10 cached / $10.00 per 1M tokens, from
  OpenAI's pricing page read 2026-10-12. Pricing moved here from Stage 4 because every event stores its cost.
- **Changed from DESIGN.md:** lock the tenant row *first*, then look for the duplicate key. Otherwise two simultaneous
  retries could both miss each other. A plain `INSERT` replaces `ON CONFLICT DO NOTHING`, so a bug fails loudly instead of being hidden.
- Verified the lock matters: with `FOR UPDATE` removed, both concurrency tests failed 3 out of 3 times.
- Fixed: Starlette deprecated `httpx` for its test client, so switched the dev dependency to `httpx2`.
- **Bug (raised by me): tests wiped the dev database.** The cleanup fixture ran against `billing`, and inside the
  container `setdefault` would have hit the dev DB too. Fix: tests now use a separate `billing_test` database,
  dropped and rebuilt from `schema.sql` on every run. `DATABASE_URL` is now overwritten, not defaulted.
  Verified a dev marker row survived local and in-container test runs.

## 2026-10-12: Stage 3, Razorpay. Commit `5926b2e` (+ docs `567a5c5`)

- Built: `app/billing.py` (HMAC signature check, dedup, plan sync), `GET`/`POST /checkout`, `POST /webhooks/razorpay`,
  `app/checkout.html`, `scripts/replay_webhook.py`, 10 webhook tests.
- Key decisions:
  - The signature is checked with stdlib `hmac.compare_digest` over the **raw** body.
  - Tenant plan is derived from the subscription **status**, not the event name.
  - Out-of-order guard: `subscriptions.updated_at` holds the last applied event's time; older events are skipped.
  - `pending` keeps access; only `halted` blocks (402).
  - The browser's payment "success" callback is never trusted; only the signed webhook changes the plan.
  - `POST /checkout` returns 409 if the tenant is already Pro.
- **Bug in the AI-written test helper:** it used the event name `activated` as the subscription status (Razorpay's status is
  `active`), so two tests passed for the wrong reason. Fixed, then each protection (dedup, ordering, signature) was
  removed in turn to confirm its test fails.
- **Bug (raised by me): type error at `db.py:9`.** `connect()` was annotated as returning tuple rows while
  `row_factory=dict_row` returns dicts. Fix: `psycopg.Connection[DictRow].connect(...)`, found by reading psycopg's source
  after two wrong attempts. The 26 remaining pyright warnings are `fetchone()`-might-be-None cases that can't occur; left as is.
- **Changed: config via `pydantic-settings`** (`app/config.py`). It fails at startup on missing, empty or misspelled keys,
  and secrets are `SecretStr`. Tests set fake Razorpay values, so they never see real keys and don't need a `.env`.
- Setup issues hit during the real checkout:
  - Webhooks need a public URL. Used ngrok's free fixed dev domain (no payment method needed for HTTP).
  - **Wrong test card:** the first card came from Razorpay's *US* docs (the docs geo-redirect), and the Indian account
    rejected it as international. The domestic test card `4718 6091 0820 4366` worked.
  - **Webhook not subscribed to subscription events:** only `payment.*` arrived, so the tenant stayed Free (correctly).
    Fixed in the dashboard by ticking all `subscription.*` events.
- **Production bug: a subscription created in the dashboard arrived with `"notes": []`** (Razorpay's empty-notes shape),
  crashed the handler with a 500, and was retried 7 times. Fix: signed subscription events with no `tenant_id` are
  recorded, logged as a warning, acknowledged with a 200, and change no tenant. A regression test reproduces the exact
  `TypeError`. Confirmed on Razorpay's next real retry.
- **Gate passed:** real test-mode checkout → `subscription.activated` → Tenant A free → pro. Earlier, Tenant B did the
  same and was moved back to Free by a real `subscription.cancelled`.
- Known limit, still open: one tenant with several subscriptions. Cancelling an *old* subscription after a new one
  is active would downgrade the tenant. The 409 only protects tenants that are already Pro.
- `EVIDENCE.md` written for every requirement provable so far; `/usage` and README are pending (Stage 4).

## 2026-10-12: Stage 4, Cost rollups and finalization. (not committed yet)

- Built: `GET /usage` (this month's calls and tokens vs. plan limits, per-category token totals, `cost_usd`),
  `README.md` (setup, plans, pricing, endpoints, mermaid architecture diagram), 5 `/usage` tests (29 total).
- **Decided:** one shared `meter.monthly_usage()` for both the quota check and `/usage`, so what a tenant is shown and
  what is enforced can never disagree.
- **Decided:** `/usage` returns per-category token totals (input, cached, output, reasoning), so anyone can recompute
  `cost_usd` from the pinned rates. That is what the gate asks for.
- Fixed while extracting the query: the month dates were cast with the DB session's time zone. That's UTC in Docker,
  but it would shift by a day on a server set to another zone. They now use `AT TIME ZONE 'UTC'` explicitly.
- **Gate passed:** against the running API, two calls plus a replay for Tenant B. `/usage` showed 2 calls, tokens
  30,000 / 19,000 / 8,000 / 3,000 and `cost_usd` 0.1039, matching the hand calculation and `cost_usd()` from the pinned constants.
- Verified the month boundary test catches a missing month filter (removed it on purpose; the test failed).
- README diagram checked with mermaid's own parser in Node (jsdom). Rendering with the mermaid CLI needed a headless
  Chrome download, which was skipped. The visual layout is unchecked until viewed on GitHub.
- Documented known limits in `DESIGN.md`: multiple subscriptions per tenant, replayed bodies not compared, pricing tiers.
- `EVIDENCE.md`: all 10 requirement boxes now proven.
