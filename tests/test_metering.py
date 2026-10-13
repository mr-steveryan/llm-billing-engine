from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from fastapi.testclient import TestClient

from app.db import connect
from app.main import app
from app.pricing import cost_usd
from tests.conftest import KEY_B, TENANT_A, TENANT_B, event_count, generate, preload


# --- Idempotency -----------------------------------------------------------------------------

def test_same_request_twice_creates_one_event(client):
    first = generate(client, "req-1")
    second = generate(client, "req-1")
    assert first.status_code == second.status_code == 200
    assert first.json()["replayed"] is False
    assert second.json() == {**first.json(), "replayed": True}  # same event id and cost
    assert event_count(TENANT_A) == 1


def test_concurrent_retries_of_one_request_create_one_event():
    def send(_):
        return generate(TestClient(app), "same-key")
    with ThreadPoolExecutor(10) as pool:
        results = list(pool.map(send, range(10)))
    assert all(r.status_code == 200 for r in results)
    assert [r.json()["replayed"] for r in results].count(False) == 1
    assert event_count(TENANT_A) == 1


def test_replay_returns_original_even_when_tenant_is_now_blocked(client):
    original = generate(client, "req-1")
    with connect() as conn:
        conn.execute("UPDATE tenants SET status = 'past_due' WHERE id = %s", (TENANT_A,))
    replay = generate(client, "req-1")
    assert replay.status_code == 200
    assert replay.json()["event_id"] == original.json()["event_id"]


# --- Quotas ----------------------------------------------------------------------------------

def test_call_quota_boundary_returns_429(client):
    preload(TENANT_A, 999)
    assert generate(client, "call-1000").status_code == 200  # exactly at the limit: allowed
    over = generate(client, "call-1001")
    assert over.status_code == 429
    assert "API call quota of 1000 on plan 'free' exhausted" in over.json()["detail"]
    assert "upgrade to Pro" in over.json()["detail"]
    assert event_count(TENANT_A) == 1000  # the rejected call stored nothing


def test_token_quota_rejects_a_request_that_would_cross_the_limit(client):
    preload(TENANT_A, 1, input_tokens_each=99_000)
    assert generate(client, "fits", input_tokens=900, output_tokens=100).status_code == 200  # lands on 100,000
    over = generate(client, "one-more", input_tokens=1, output_tokens=0)
    assert over.status_code == 429
    assert "token quota of 100000" in over.json()["detail"]
    assert event_count(TENANT_A) == 2


def test_concurrent_requests_cannot_overshoot_quota():
    preload(TENANT_A, 998)  # 2 calls left
    def send(i):
        return generate(TestClient(app), f"race-{i}")
    with ThreadPoolExecutor(10) as pool:
        codes = sorted(r.status_code for r in pool.map(send, range(10)))
    assert codes == [200] * 2 + [429] * 8
    assert event_count(TENANT_A) == 1000


def test_past_due_tenant_gets_402(client):
    with connect() as conn:
        conn.execute("UPDATE tenants SET status = 'past_due' WHERE id = %s", (TENANT_A,))
    r = generate(client, "req-1")
    assert r.status_code == 402
    assert "Payment failed" in r.json()["detail"]
    assert event_count(TENANT_A) == 0


def test_pro_plan_has_higher_limits(client):
    preload(TENANT_A, 1000)
    with connect() as conn:
        conn.execute("UPDATE tenants SET plan_id = 'pro' WHERE id = %s", (TENANT_A,))
    assert generate(client, "call-1001").status_code == 200


# --- Tenant isolation ------------------------------------------------------------------------

def test_other_tenants_usage_does_not_count_against_you(client):
    preload(TENANT_B, 1000)  # B is out of quota
    assert generate(client, "req-1").status_code == 200  # A is unaffected
    assert generate(client, "req-1", api_key=KEY_B).status_code == 429


def test_same_idempotency_key_in_two_tenants_is_two_events(client):
    a = generate(client, "shared-key")
    b = generate(client, "shared-key", api_key=KEY_B)
    assert a.json()["replayed"] is b.json()["replayed"] is False
    assert a.json()["event_id"] != b.json()["event_id"]


# --- Input validation at the boundary --------------------------------------------------------

def test_missing_or_bad_api_key_is_401(client):
    body = {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1, "reasoning_tokens": 0}
    assert client.post("/generate", headers={"Idempotency-Key": "x"}, json=body).status_code == 401
    assert generate(client, "req-1", api_key="not-a-key").status_code == 401


def test_missing_idempotency_key_is_400(client):
    r = client.post("/generate", headers={"X-API-Key": "demo-key-tenant-a"},
                    json={"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1, "reasoning_tokens": 0})
    assert r.status_code == 400


def test_impossible_token_counts_are_422(client):
    assert generate(client, "a", input_tokens=10, cached=11).status_code == 422
    assert generate(client, "b", output_tokens=10, reasoning=11).status_code == 422
    assert generate(client, "c", input_tokens=-1).status_code == 422
    assert event_count(TENANT_A) == 0


# --- Cost ------------------------------------------------------------------------------------

def test_cost_uses_category_rates_not_a_token_sum(client):
    # 10,000 input of which 4,000 cached; 5,000 output of which 2,000 reasoning.
    #   6,000 uncached × $2.00/M  = $0.012
    #   4,000 cached   × $0.10/M  = $0.0004
    #   5,000 output   × $10.00/M = $0.05   (reasoning is inside output, counted once)
    expected = Decimal("0.0624")
    assert cost_usd(10_000, 4_000, 5_000) == expected
    r = generate(client, "priced", input_tokens=10_000, cached=4_000, output_tokens=5_000, reasoning=2_000)
    assert Decimal(r.json()["cost_usd"]) == expected
