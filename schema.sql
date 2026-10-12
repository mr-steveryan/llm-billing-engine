-- Runs once, on an empty database (docker-entrypoint-initdb.d). Schema change => `docker compose down -v`.

-- Plans are reference data the engine needs to run, so they live here, not in seed data.
-- Text PK: a plan is a stable name ('free'), readable in every row that points at it.
CREATE TABLE plans (
    id             text    PRIMARY KEY,
    monthly_calls  integer NOT NULL CHECK (monthly_calls  > 0),
    monthly_tokens bigint  NOT NULL CHECK (monthly_tokens > 0)  -- bigint: token totals outgrow int32 (~2.1B)
);

INSERT INTO plans (id, monthly_calls, monthly_tokens) VALUES
    ('free',  1000,    100000),
    ('pro',  50000,  10000000);

-- Tenant = the paying customer. plan_id/status are a mirror of Razorpay, written only by verified webhooks.
CREATE TABLE tenants (
    id           uuid        PRIMARY KEY DEFAULT uuidv7(),  -- unguessable, but time-ordered so inserts stay index-friendly
    name         text        NOT NULL,
    api_key_hash text        NOT NULL UNIQUE,               -- sha256 hex of the key; a DB leak doesn't leak usable keys
    plan_id      text        NOT NULL DEFAULT 'free' REFERENCES plans (id),
    status       text        NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'past_due')),
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- One row per Razorpay subscription. id is Razorpay's own id (sub_...), so webhooks upsert by it directly.
-- status has no CHECK: it mirrors Razorpay's values verbatim, and we must not reject a status they add later.
-- updated_at = created_at of the last Razorpay event applied; older events arriving late are ignored.
CREATE TABLE subscriptions (
    id         text        PRIMARY KEY,
    tenant_id  uuid        NOT NULL REFERENCES tenants (id),
    status     text        NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- One row = one billable API call. Token counts follow OpenAI's shape: cached is part of input,
-- reasoning is part of output. The CHECKs make an impossible combination unstorable.
CREATE TABLE usage_events (
    id                  bigint        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id           uuid          NOT NULL REFERENCES tenants (id),
    idempotency_key     text          NOT NULL,
    input_tokens        bigint        NOT NULL CHECK (input_tokens >= 0),
    cached_input_tokens bigint        NOT NULL CHECK (cached_input_tokens BETWEEN 0 AND input_tokens),
    output_tokens       bigint        NOT NULL CHECK (output_tokens >= 0),
    reasoning_tokens    bigint        NOT NULL CHECK (reasoning_tokens BETWEEN 0 AND output_tokens),
    cost_usd            numeric(18,10) NOT NULL CHECK (cost_usd >= 0),  -- exact decimal; priced at write time, frozen
    created_at          timestamptz   NOT NULL DEFAULT now(),
    -- The idempotency guarantee. Scoped per tenant so two tenants can't collide on (or probe) each other's keys.
    UNIQUE (tenant_id, idempotency_key)
);

-- Every quota check and /usage read is "this tenant, this month": this index answers it without a table scan.
CREATE INDEX usage_events_tenant_month ON usage_events (tenant_id, created_at);

-- Razorpay event ids we've already applied. PK = the dedup: a replayed event fails to insert.
CREATE TABLE webhook_events (
    event_id    text        PRIMARY KEY,
    event_type  text        NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now()
);
