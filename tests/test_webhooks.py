import hashlib
import hmac
import json

from app.db import connect
from tests.conftest import TENANT_A, generate

SECRET = b"test-webhook-secret"


# Razorpay event name -> the subscription status that event carries (they differ: activated -> active).
STATUS = {"activated": "active", "halted": "halted", "cancelled": "cancelled"}


def send(client, event_id, event, created_at=1_000, sub_id="sub_TEST00000000001", signature=None):
    status = STATUS[event]
    body = json.dumps({
        "entity": "event",
        "event": f"subscription.{event}",
        "created_at": created_at,
        "payload": {"subscription": {"entity": {"id": sub_id, "status": status, "notes": {"tenant_id": TENANT_A}}}},
    }).encode()
    sig = signature or hmac.new(SECRET, body, hashlib.sha256).hexdigest()
    return client.post("/webhooks/razorpay", content=body,
                       headers={"X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": event_id})


def tenant_a():
    with connect() as conn:
        return conn.execute("SELECT plan_id, status FROM tenants WHERE id = %s", (TENANT_A,)).fetchone()


def test_activated_flips_tenant_free_to_pro(client):
    assert tenant_a() == {"plan_id": "free", "status": "active"}
    r = send(client, "evt_1", "activated")
    assert r.status_code == 200 and r.json()["status"] == "applied"
    assert tenant_a() == {"plan_id": "pro", "status": "active"}


def test_forged_signature_is_rejected_and_changes_nothing(client):
    r = send(client, "evt_1", "activated", signature="0" * 64)
    assert r.status_code == 400
    assert tenant_a()["plan_id"] == "free"


def test_missing_signature_is_rejected(client):
    r = client.post("/webhooks/razorpay", content=b"{}", headers={"X-Razorpay-Event-Id": "evt_1"})
    assert r.status_code == 400


def test_replayed_event_is_ignored(client):
    send(client, "evt_1", "activated", created_at=1_000)
    send(client, "evt_2", "cancelled", created_at=2_000)
    replay = send(client, "evt_1", "activated", created_at=1_000)  # same event id delivered again
    assert replay.status_code == 200 and replay.json()["status"] == "duplicate ignored"
    assert tenant_a()["plan_id"] == "free"  # the replay did not re-upgrade the cancelled tenant
    with connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM webhook_events").fetchone()["n"] == 2


def test_out_of_order_older_event_does_not_win(client):
    send(client, "evt_cancel", "cancelled", created_at=2_000)
    send(client, "evt_late_activate", "activated", created_at=1_000)  # new id, but older than what we applied
    assert tenant_a()["plan_id"] == "free"


def test_halted_subscription_blocks_usage_with_402(client):
    send(client, "evt_1", "activated", created_at=1_000)
    send(client, "evt_2", "halted", created_at=2_000)
    assert tenant_a() == {"plan_id": "pro", "status": "past_due"}
    assert generate(client, "req-1").status_code == 402


def test_cancelled_returns_tenant_to_free(client):
    send(client, "evt_1", "activated", created_at=1_000)
    send(client, "evt_2", "cancelled", created_at=2_000)
    assert tenant_a() == {"plan_id": "free", "status": "active"}


def test_non_subscription_event_is_recorded_but_changes_nothing(client):
    body = json.dumps({"event": "payment.captured", "created_at": 1_000, "payload": {}}).encode()
    sig = hmac.new(SECRET, body, hashlib.sha256).hexdigest()
    r = client.post("/webhooks/razorpay", content=body,
                    headers={"X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": "evt_pay"})
    assert r.status_code == 200
    assert tenant_a()["plan_id"] == "free"


def test_checkout_refuses_tenant_already_on_pro(client):
    send(client, "evt_1", "activated")
    r = client.post("/checkout", headers={"X-API-Key": "demo-key-tenant-a"})
    assert r.status_code == 409


def test_subscription_not_created_by_checkout_is_acknowledged_not_crashed(client):
    # Real case: a dashboard-made subscription arrives with Razorpay's empty-notes shape `[]` and no tenant_id.
    # Crashing made Razorpay retry it forever; it must be recorded and acknowledged, changing no tenant.
    body = json.dumps({
        "event": "subscription.cancelled", "created_at": 1_000,
        "payload": {"subscription": {"entity": {"id": "sub_DASHBOARD00001", "status": "cancelled", "notes": []}}},
    }).encode()
    headers = {"X-Razorpay-Signature": hmac.new(SECRET, body, hashlib.sha256).hexdigest(),
               "X-Razorpay-Event-Id": "evt_foreign"}
    first = client.post("/webhooks/razorpay", content=body, headers=headers)
    assert first.status_code == 200 and first.json()["status"] == "ignored: subscription has no tenant_id"
    retry = client.post("/webhooks/razorpay", content=body, headers=headers)
    assert retry.json()["status"] == "duplicate ignored"  # Razorpay's retry is now a no-op
    with connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM subscriptions").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM tenants WHERE plan_id <> 'free'").fetchone()["n"] == 0
