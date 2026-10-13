from concurrent.futures import ThreadPoolExecutor

import pytest
import requests
from fastapi.testclient import TestClient

import app.main
from app import billing
from app.config import settings
from app.db import connect
from tests.conftest import KEY_A, TENANT_A
from tests.test_webhooks import send, tenant_a

SUB_ID = "sub_FAKE0000000001"


class FakeRazorpay:
    # Stands in for razorpay.Client: records create payloads and fetches, never calls the network.
    # live_status is what Razorpay reports for an existing subscription (set it to simulate a missed webhook).
    def __init__(self, fail=False):
        self.calls, self.fetched, self.fail, self.live_status = [], [], fail, "created"
        self.kwargs = []  # extra arguments per call, e.g. timeout
        self.subscription = self

    def __call__(self, auth):
        return self

    def create(self, payload, **kwargs):
        self.calls.append(payload)
        self.kwargs.append(kwargs)
        if self.fail:
            raise requests.exceptions.ConnectionError("razorpay down")
        n = len(self.calls)  # a new id per subscription: sub_FAKE0000000001, sub_FAKE0000000002, ...
        return {"id": f"sub_FAKE{n:010d}", "status": "created", "created_at": 1_000 * n}

    def fetch(self, subscription_id, **kwargs):
        self.fetched.append(subscription_id)
        self.kwargs.append(kwargs)
        return {"id": subscription_id, "status": self.live_status}


@pytest.fixture
def fake(monkeypatch):
    f = FakeRazorpay()
    monkeypatch.setattr(billing.razorpay, "Client", f)
    return f


def checkout(client):
    return client.post("/checkout", headers={"X-API-Key": KEY_A})


def stored_subscriptions():
    with connect() as conn:
        return conn.execute("SELECT id, tenant_id::text, status FROM subscriptions").fetchall()


def test_first_checkout_creates_and_stores_subscription_for_key_tenant(client, fake):
    r = checkout(client)
    assert r.status_code == 200
    assert r.json() == {"key_id": settings.razorpay_key_id, "subscription_id": SUB_ID}
    assert len(fake.calls) == 1
    assert fake.calls[0]["notes"]["tenant_id"] == TENANT_A  # set by the server from the API key
    assert stored_subscriptions() == [{"id": SUB_ID, "tenant_id": TENANT_A, "status": "created"}]


def test_repeat_checkout_reuses_unpaid_subscription(client, fake):
    checkout(client)
    r = checkout(client)  # double-click / second tab before paying
    assert r.status_code == 200 and r.json()["subscription_id"] == SUB_ID
    assert len(fake.calls) == 1


def test_checkout_refused_while_mandate_authenticated(client, fake):
    checkout(client)
    with connect() as conn:
        conn.execute("UPDATE subscriptions SET status = 'authenticated' WHERE id = %s", (SUB_ID,))
    fake.live_status = "authenticated"
    assert checkout(client).status_code == 409
    assert len(fake.calls) == 1


def test_stale_authenticated_row_dead_at_razorpay_does_not_block_checkout(client, fake):
    # Mandate cancelled at Razorpay, webhook missed: our row still says 'authenticated'.
    checkout(client)
    with connect() as conn:
        conn.execute("UPDATE subscriptions SET status = 'authenticated' WHERE id = %s", (SUB_ID,))
    fake.live_status = "cancelled"
    r = checkout(client)
    assert r.status_code == 200 and r.json()["subscription_id"] == "sub_FAKE0000000002"
    assert fake.fetched == [SUB_ID]


def test_razorpay_failure_stores_nothing(client, monkeypatch):
    monkeypatch.setattr(billing.razorpay, "Client", FakeRazorpay(fail=True))
    r = checkout(client)
    assert r.status_code == 502 and "Payment provider" in r.json()["detail"]  # a clean error, never a 500
    assert stored_subscriptions() == []


def test_later_activation_webhook_still_upgrades(client, fake):
    checkout(client)
    r = send(client, "evt_act", "activated", created_at=2_000, sub_id=SUB_ID)
    assert r.json()["status"] == "applied"
    assert tenant_a() == {"plan_id": "pro", "status": "active"}


def test_checkout_refuses_tenant_already_on_pro(client, fake):
    send(client, "evt_1", "activated")
    assert checkout(client).status_code == 409
    assert fake.calls == []


def test_reuse_asks_razorpay_first(client, fake):
    checkout(client)
    checkout(client)
    assert fake.fetched == [SUB_ID]  # the mirror row may be stale, so Razorpay is checked before reusing


def test_missed_webhook_paid_subscription_blocks_a_second_one(client, fake):
    # The tenant paid, but the webhook never reached us: our row still says 'created', Razorpay says 'active'.
    checkout(client)
    fake.live_status = "active"
    r = checkout(client)
    assert r.status_code == 409 and "already in progress" in r.json()["detail"]
    assert len(fake.calls) == 1  # no second subscription, so no double charge
    assert tenant_a()["plan_id"] == "free"  # the plan still changes only through webhooks


def test_dead_subscription_at_razorpay_starts_a_fresh_one(client, fake):
    checkout(client)
    fake.live_status = "expired"  # expired or cancelled at Razorpay without a webhook telling us
    r = checkout(client)
    assert r.status_code == 200 and r.json()["subscription_id"] == "sub_FAKE0000000002"
    assert len(fake.calls) == 2
    assert {"id": SUB_ID, "tenant_id": TENANT_A, "status": "expired"} in stored_subscriptions()  # stale row corrected
    fake.live_status = "created"
    assert checkout(client).json()["subscription_id"] == "sub_FAKE0000000002"  # the newest one is reused next
    assert fake.fetched[-1] == "sub_FAKE0000000002"


def test_cancelling_after_a_stuck_authenticated_row_returns_tenant_to_free(client, fake):
    # sub 1 was authorised, then cancelled at Razorpay with the webhook lost: our row would stay 'authenticated',
    # count as live, and keep the tenant on Pro after they cancel sub 2.
    checkout(client)
    with connect() as conn:
        conn.execute("UPDATE subscriptions SET status = 'authenticated' WHERE id = %s", (SUB_ID,))
    fake.live_status = "cancelled"
    sub2 = checkout(client).json()["subscription_id"]
    send(client, "evt_2_act", "activated", created_at=5_000, sub_id=sub2)
    assert tenant_a()["plan_id"] == "pro"
    send(client, "evt_2_can", "cancelled", created_at=6_000, sub_id=sub2)
    assert tenant_a() == {"plan_id": "free", "status": "active"}


def test_razorpay_calls_have_a_timeout(client, fake):
    checkout(client)  # create
    checkout(client)  # fetch before reuse
    assert fake.kwargs == [{"timeout": billing.RAZORPAY_TIMEOUT}] * 2  # never wait forever while holding the lock


def test_simultaneous_checkouts_create_one_subscription(fake):
    # Double-click or two tabs: the tenant row lock makes them run one at a time, so later ones reuse the first.
    def click(_):
        return TestClient(app.main.app).post("/checkout", headers={"X-API-Key": KEY_A})
    with ThreadPoolExecutor(5) as pool:
        results = list(pool.map(click, range(5)))
    assert [r.status_code for r in results] == [200] * 5
    assert {r.json()["subscription_id"] for r in results} == {SUB_ID}
    assert len(fake.calls) == 1
