# Build Log

A running record of how the engine was built: what each stage delivered, the decisions taken (and by whom),
what went wrong and how it was fixed. Newest stage last. Built with Claude Code:
every design decision below was proposed with its reasoning and approved or changed by me before it was built.

## AI usage summary

The AI tool was Claude Code. It wrote most of the code, tests and docs; I set the brief, made the decisions listed
below, ran the real Razorpay checkouts, and reviewed each stage before committing it. Details are in the dated entries.

**Where AI helped**
- Turned the brief into a staged plan and a schema, explaining every decision (data types, locking, idempotency).
- Researched Razorpay instead of guessing: its webhook event names, signature and event-id headers, and the
  required subscription fields, all checked in Razorpay's docs. It also found the Postgres 18 Docker volume-path change.
- Wrote tests against real Postgres, and proved they work by breaking each protection on purpose until its test failed.
- Debugged the real Razorpay run from logs and the database, e.g. found that only `payment.*` events were arriving.
- Ran a 3-agent review that found a real double-subscription bug in `/checkout`.

**Where AI was wrong**
- Picked Postgres 16 out of habit instead of checking the current release (I questioned it; moved to 18).
- Designed the tests to wipe the dev database (I caught it; tests now use a separate `billing_test` database).
- Mis-annotated the DB connection type (`db.py:9`, I spotted it) and needed two wrong attempts before reading psycopg's source.
- Wrote a test helper that used the event name `activated` as the subscription status, so two tests passed for the wrong reason.
- Gave a US test card (Razorpay's docs had geo-redirected); my Indian account rejected it as international.
- Wrote a webhook handler that crashed on Razorpay's empty-notes shape (`[]`), causing seven 500s in a real run.
- Claimed `/checkout`'s 409 prevented double charges; it didn't (found by the review). Its first fix reused a possibly
  stale subscription without asking Razorpay (I raised it). Its reviewer agent's own suggested fix would not have worked.
- Used "billed" for the USD token cost, which suggested customers are charged it (I questioned ₹ vs USD; wording fixed).
- Gave a wrong reason for `bigint` and left stale labels in the docs (found by the review).

**What I changed or decided**
- Razorpay instead of Stripe; 429 = quota exhausted, 402 = subscription halted (all payment retries failed); Pro = 50,000 calls / 10M tokens.
- OpenAI-style token fields (cached ⊂ input, reasoning ⊂ output), chosen over the AI's recommended disjoint fields.
- Mirror a real model's prices (OpenAI `gpt-6.1-sol`) instead of the AI's suggested round demo rates.
- Postgres 18, `pydantic-settings` for config, ngrok's fixed domain for webhooks, and `.gitignore` from `uv init`.
- Asked for: the separate test database, the type fix, the stale-checkout fix, the ₹-vs-USD clarification,
  the Stripe → Razorpay mapping in DESIGN.md, and this log. I made every commit myself.

## 2026-10-10: Planning

- Brief: tenant plans (Free/Pro), usage metering, quota enforcement (429/402), token cost rules, subscription payments.
- **Decided:** Razorpay test mode instead of Stripe. The brief's Stripe event names were mapped to Razorpay's
  (`subscription.activated` / `updated` / `halted` / `cancelled`), and a replay script stands in for the Stripe CLI.
- **Decided (changed from the brief's diagram):** check the quota *before* storing the usage event. Storing first would record and count rejected requests.
- **Decided:** 429 = quota exhausted, 402 = subscription halted (all payment retries failed). Each code means one thing.
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

## 2026-10-12: Stage 4, Cost rollups and finalization. Commit `e0dfbd6`

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

## 2026-10-13: Review and fixes. Commit `9c44613`

- Ran a 3-agent read-only review (logic bugs, doc accuracy, tests and setup). It reported 14 findings, and each one
  was checked against the code before fixing. The fixes were then made by 3 agents, each owning a separate set of files.
- **Double subscriptions (`/checkout`).** The 409 only fired once a tenant was already Pro, so a double-click before the
  webhook arrived could create two subscriptions and charge twice a month. Checkout now locks the tenant row,
  **reuses** an unpaid `created` subscription, refuses with 409 while one is `authenticated`, and records each new
  subscription immediately. The reviewer's suggested fix (checking the subscriptions table) could not have worked,
  because rows only appeared there when webhooks arrived. Added the missing test: `notes.tenant_id` comes from the API key.
- **Webhook signature:** a non-ASCII byte in `X-Razorpay-Signature` crashed the handler with a 500. Reproduced it, then
  switched to a byte-wise compare, so it now returns 400.
- **Out-of-order events** skipped by the guard reported `applied`; they now report `ignored: older than last applied event`.
- **Setup:** the healthcheck now uses TCP, because the socket answered during first-time init before `schema.sql`
  finished. The runtime image no longer contains pytest or httpx2, so tests run on the host only.
  `.gitignore` now follows `uv init`'s template; `.dockerignore` mirrors it (with `**/` for nested paths) and
  excludes `.git` again.
- **Checkout page:** shows an error instead of nothing when `/checkout` fails with a non-JSON body.
- **Weak test fixed:** the non-subscription webhook test now checks that the event is actually recorded.
- **Docs:** corrected the stale "Stage 4" pricing label, the leftover "billed" wording, the wrong `bigint` reason,
  the 429/402 message templates, "three paths, nothing else", and "402 = payment failed" (402 only follows a halted
  subscription). EVIDENCE now notes where Tenant B's data was reset between captures.
- **Follow-up (raised by me): a stale reused checkout.** Our `created` row could be out of date if a webhook was
  missed. The worst case is a customer who paid but whose webhook never arrived: reuse would send them back to
  Checkout, and "just create a new one" would charge twice. Before reusing, `/checkout` now **fetches the real status
  from Razorpay**: `created` → reuse; live (`active`, `authenticated`, ...) → 409; dead (`cancelled`, `expired`, ...)
  → new subscription. The fetch is read-only, so the mirror and the plan still change only via webhooks. Checked
  against the real test API (`fetch` of Tenant A's subscription returned `active`).
- 38 tests pass (9 new). Each new fix was broken on purpose and its test failed.
- Not verified: the healthcheck change on a truly fresh database init, because that needs `docker compose down -v`,
  which wipes dev data. The checkout page's error message was not tried in a browser.

## 2026-10-13: Submission requirements (brief Section 10). Commit `aef0d53`

- Added `capstone.yaml` (run, seed, test, base_url, endpoints to probe). The run command creates `.env` from
  `.env.example` if missing and uses `docker compose up --wait`, so the seed step never runs against an unready database.
- `seed.sql` is now safe to re-run (`ON CONFLICT DO NOTHING`), so "seed" is a real, repeatable step.
- README: one run command plus one seed command, a plain-text architecture sketch next to the mermaid diagram,
  and an honest Limitations section (including that caller-reported token counts are trusted).
- DESIGN.md: a "Brief → this build" table mapping the brief's Stripe names to Razorpay.
- BUILDLOG: an AI usage summary at the top (where AI helped, where it was wrong, what I changed).
- **Verified like a stranger would:** copied only the files git would ship (no `.env`) into a scratch folder, ran
  `capstone.yaml`'s run, seed (twice) and test commands on a brand-new database, and probed every listed endpoint.
  All matched their expected status, and 38 tests passed. This also confirmed the TCP healthcheck on a fresh init,
  which was left unverified earlier.
- Secret scan of the whole git history: no `.env` ever committed, no real-looking Razorpay keys, no secret values
  in `.env.example`.

## 2026-10-13: Ponytail audit fixes. (not committed yet)

Ran `/ponytail-audit` on the committed repo. It reported 4 findings, all fixed:
- **Database open to the network.** Compose published Postgres on all interfaces (`0.0.0.0:5432`), with the password
  public in this repo, so anyone on the same Wi-Fi could edit tenants. Postgres and the API are now bound to
  `127.0.0.1`. Checked: closed on the LAN address, open on localhost.
- **No timeout on Razorpay calls.** The SDK sets none (checked its source), and `/checkout` holds the tenant row lock
  during the call, so a stalled Razorpay API would also hang that tenant's `/generate`. Both calls now pass
  `timeout=10`; confirmed the SDK forwards it to the HTTP request.
- **The checkout lock had no test.** Removing `FOR UPDATE` left every test green. Added a test that sends 5
  simultaneous checkouts and expects exactly one subscription; it fails 3/3 times without the lock.
- **30 unused packages.** `fastapi[standard]` (added in Stage 3) pulled in CLIs, a template engine and telemetry
  libraries (`sentry-sdk`, `opentelemetry-*`) that nothing imports. Switched to plain `fastapi`: `uv.lock` went from
  70 to 40 packages.
- **Found while verifying: the API had no healthcheck,** so `docker compose up --wait` returned before Uvicorn accepted
  requests, and probes sent straight after it failed (curl 000). Added a healthcheck that calls `/health` (which also
  queries the DB) with Python's urllib. Now 3/3 recreate-and-probe-immediately runs return 200, and the same
  command fails (exit 1) when nothing is listening.
- 40 tests pass. Containers rebuilt; health, usage and webhook probes OK over localhost.
