import hashlib

import psycopg
from psycopg.rows import DictRow
from pydantic import BaseModel, Field, model_validator

from app.pricing import cost_usd


class Usage(BaseModel):
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def subsets_fit(self):
        # Same rule as the DB CHECKs, enforced at the API boundary so bad input is a 422, not a 500.
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached_input_tokens cannot exceed input_tokens (cached is part of input)")
        if self.reasoning_tokens > self.output_tokens:
            raise ValueError("reasoning_tokens cannot exceed output_tokens (reasoning is part of output)")
        return self


class BillingError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def tenant_for_key(conn: psycopg.Connection[DictRow], api_key: str):
    """The tenant id owning this API key, or None. Keys are stored only as sha256 hashes."""
    row = conn.execute(
        "SELECT id FROM tenants WHERE api_key_hash = %s", (hashlib.sha256(api_key.encode()).hexdigest(),)
    ).fetchone()
    return row["id"] if row else None


def usage_summary(conn: psycopg.Connection[DictRow], tenant_id) -> dict:
    """GET /usage: the tenant's plan limits next to this month's usage and cost."""
    tenant = conn.execute(
        """SELECT t.plan_id, t.status, p.monthly_calls, p.monthly_tokens
           FROM tenants t JOIN plans p ON p.id = t.plan_id WHERE t.id = %s""",
        (tenant_id,),
    ).fetchone()
    used = monthly_usage(conn, tenant_id)
    return {
        "plan": tenant["plan_id"],
        "status": tenant["status"],
        "period": {"start": used["period_start"], "resets_on": used["resets_on"]},
        "calls": {"used": used["calls"], "limit": tenant["monthly_calls"]},
        "tokens": {
            "used": used["tokens"],
            "limit": tenant["monthly_tokens"],
            # Per-category totals, so anyone can recompute cost_usd from the pinned rates in app/pricing.py.
            "input": used["input_tokens"],
            "cached_input": used["cached_input_tokens"],
            "output": used["output_tokens"],
            "reasoning": used["reasoning_tokens"],
        },
        "cost_usd": format(used["cost_usd"], "f"),  # string, not float: exact decimal; "f" so zero is "0.0000000000", not "0E-10"
    }


def monthly_usage(conn: psycopg.Connection[DictRow], tenant_id) -> DictRow:
    """This calendar month's (UTC) usage for one tenant. The single definition used by both the quota check
    and GET /usage, so what a tenant is shown and what is enforced can never disagree."""
    row = conn.execute(
        """SELECT count(*) AS calls,
                  coalesce(sum(input_tokens + output_tokens), 0) AS tokens,  -- cached/reasoning are inside these
                  coalesce(sum(input_tokens), 0) AS input_tokens,
                  coalesce(sum(cached_input_tokens), 0) AS cached_input_tokens,
                  coalesce(sum(output_tokens), 0) AS output_tokens,
                  coalesce(sum(reasoning_tokens), 0) AS reasoning_tokens,
                  coalesce(sum(cost_usd), 0) AS cost_usd,  -- each event was priced when recorded
                  (date_trunc('month', now(), 'UTC') AT TIME ZONE 'UTC')::date AS period_start,
                  (date_trunc('month', now(), 'UTC') AT TIME ZONE 'UTC' + interval '1 month')::date AS resets_on
           FROM usage_events
           WHERE tenant_id = %s AND created_at >= date_trunc('month', now(), 'UTC')""",
        (tenant_id,),
    ).fetchone()
    assert row is not None  # an aggregate without GROUP BY always returns exactly one row
    return row


def record(conn: psycopg.Connection[DictRow], tenant_id, idempotency_key: str, usage: Usage) -> tuple[DictRow, bool]:
    """Record one billable call. Returns (event, replayed). Raises BillingError for 402/429.

    Runs inside the caller's transaction; a raised error rolls everything back, so rejected calls store nothing.
    """
    # Lock first: every request for this tenant, replays included, runs one at a time from here on.
    # That makes both the duplicate lookup and the quota check race-free.
    tenant = conn.execute(
        """SELECT t.status, t.plan_id, p.monthly_calls, p.monthly_tokens
           FROM tenants t JOIN plans p ON p.id = t.plan_id
           WHERE t.id = %s FOR UPDATE OF t""",
        (tenant_id,),
    ).fetchone()

    existing = conn.execute(
        "SELECT id, cost_usd FROM usage_events WHERE tenant_id = %s AND idempotency_key = %s",
        (tenant_id, idempotency_key),
    ).fetchone()
    if existing:
        return existing, True

    if tenant["status"] == "past_due":
        raise BillingError(402, "Payment failed for your subscription; update your payment method to continue.")

    used = monthly_usage(conn, tenant_id)

    upgrade = " or upgrade to Pro" if tenant["plan_id"] == "free" else ""
    # Reject a request that *would* cross the limit, so usage never ends above the quota.
    if used["calls"] + 1 > tenant["monthly_calls"]:
        raise BillingError(429, f"Monthly API call quota of {tenant['monthly_calls']} on plan '{tenant['plan_id']}' "
                                f"exhausted; resets {used['resets_on']} (UTC){upgrade}.")
    request_tokens = usage.input_tokens + usage.output_tokens
    if used["tokens"] + request_tokens > tenant["monthly_tokens"]:
        raise BillingError(429, f"Monthly token quota of {tenant['monthly_tokens']} on plan '{tenant['plan_id']}' "
                                f"would be exceeded ({used['tokens']} used + {request_tokens} requested); "
                                f"resets {used['resets_on']} (UTC){upgrade}.")

    # Plain INSERT, no ON CONFLICT: under the lock a duplicate is impossible, so if one ever happens
    # the unique constraint should fail loudly rather than be silently ignored.
    event = conn.execute(
        """INSERT INTO usage_events (tenant_id, idempotency_key, input_tokens, cached_input_tokens,
                                     output_tokens, reasoning_tokens, cost_usd)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id, cost_usd""",
        (tenant_id, idempotency_key, usage.input_tokens, usage.cached_input_tokens,
         usage.output_tokens, usage.reasoning_tokens,
         cost_usd(usage.input_tokens, usage.cached_input_tokens, usage.output_tokens)),
    ).fetchone()
    return event, False
