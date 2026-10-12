import psycopg
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


def record(conn: psycopg.Connection, tenant_id, idempotency_key: str, usage: Usage) -> tuple[dict, bool]:
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

    used = conn.execute(
        """SELECT count(*) AS calls,
                  coalesce(sum(input_tokens + output_tokens), 0) AS tokens,
                  (date_trunc('month', now(), 'UTC') + interval '1 month')::date AS resets_on
           FROM usage_events
           WHERE tenant_id = %s AND created_at >= date_trunc('month', now(), 'UTC')""",
        (tenant_id,),
    ).fetchone()

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
