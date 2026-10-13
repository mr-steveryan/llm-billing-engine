from decimal import Decimal

from app.db import connect
from app.pricing import cost_usd
from tests.conftest import KEY_A, KEY_B, TENANT_A, generate


def usage(client, api_key=KEY_A):
    r = client.get("/usage", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    return r.json()


def test_empty_month_shows_zero_usage_and_plan_limits(client):
    u = usage(client)
    assert u["plan"] == "free" and u["status"] == "active"
    assert u["calls"] == {"used": 0, "limit": 1000}
    assert u["tokens"]["used"] == 0 and u["tokens"]["limit"] == 100_000
    assert u["cost_usd"] == "0"


def test_zero_cost_is_plain_decimal_not_scientific(client):
    # A stored numeric(18,10) zero is Decimal("0E-10"); str() of it leaked "0E-10" to clients.
    r = generate(client, "zero", input_tokens=0, output_tokens=0)
    assert r.status_code == 200 and r.json()["cost_usd"] == "0.0000000000"
    assert usage(client)["cost_usd"] == "0.0000000000"


def test_rollup_cost_matches_pinned_pricing_constants(client):
    generate(client, "r1", input_tokens=10_000, cached=4_000, output_tokens=5_000, reasoning=2_000)
    generate(client, "r2", input_tokens=3_000, cached=0, output_tokens=1_500, reasoning=1_000)
    generate(client, "r2", input_tokens=3_000, cached=0, output_tokens=1_500, reasoning=1_000)  # replay: no double count
    u = usage(client)
    t = u["tokens"]
    assert u["calls"]["used"] == 2
    assert (t["input"], t["cached_input"], t["output"], t["reasoning"]) == (13_000, 4_000, 6_500, 3_000)
    assert t["used"] == 13_000 + 6_500  # quota tokens = input + output (cached/reasoning are inside them)
    # The gate: the stored per-event costs add up to exactly what the pinned rates give for the totals.
    # (6,000+3,000) × $2.00/M + 4,000 × $0.10/M + 6,500 × $10.00/M = 0.018 + 0.0004 + 0.065 = 0.0834
    assert Decimal(u["cost_usd"]) == cost_usd(13_000, 4_000, 6_500) == Decimal("0.0834")


def test_last_months_usage_is_not_counted(client):
    with connect() as conn:  # one event one second before this UTC month started
        conn.execute(
            """INSERT INTO usage_events (tenant_id, idempotency_key, input_tokens, cached_input_tokens,
                                         output_tokens, reasoning_tokens, cost_usd, created_at)
               VALUES (%s, 'last-month', 500, 0, 500, 0, 1, date_trunc('month', now(), 'UTC') - interval '1 second')""",
            (TENANT_A,),
        )
    generate(client, "this-month")
    u = usage(client)
    assert u["calls"]["used"] == 1
    assert u["tokens"]["used"] == 200


def test_tenant_sees_only_its_own_usage(client):
    generate(client, "a-only", input_tokens=700, output_tokens=300)
    assert usage(client, KEY_B)["calls"]["used"] == 0
    assert usage(client, KEY_A)["calls"]["used"] == 1


def test_usage_requires_api_key(client):
    assert client.get("/usage").status_code == 401
