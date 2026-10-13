import hashlib
import hmac
import json
import threading
import time

import pytest

from app import billing
from app.db import connect
from tests.conftest import TENANT_A, generate

SECRET = b"test-webhook-secret"


# Razorpay event name -> the subscription status that event carries (they differ: activated -> active).
STATUS = {"activated": "active", "charged": "active", "authenticated": "authenticated",
          "halted": "halted", "cancelled": "cancelled"}


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


def test_non_ascii_signature_is_rejected_not_crashed(client):
    # Bytes header: the test client would choke on a str with \xe9 before reaching the app.
    r = client.post("/webhooks/razorpay", content=b"{}",
                    headers={"X-Razorpay-Signature": b"\xe9", "X-Razorpay-Event-Id": "evt_1"})
    assert r.status_code == 400


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
    late = send(client, "evt_late_activate", "activated", created_at=1_000)  # new id, but older than what we applied
    assert late.json()["status"] == "ignored: older than last applied event"
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


def test_old_subscriptions_late_cancel_does_not_downgrade_paying_tenant(client):
    # /checkout started sub_B because sub_A was dead at Razorpay; sub_A's cancel webhook is retried after B is paid.
    send(client, "evt_b_act", "activated", created_at=2_000, sub_id="sub_B")
    r = send(client, "evt_a_cancel", "cancelled", created_at=1_500, sub_id="sub_A")
    assert r.json()["status"] == "applied"
    assert tenant_a() == {"plan_id": "pro", "status": "active"}


def test_same_second_authenticated_last_still_protects_paying_tenant(client):
    # Real arrival order seen at 16:07:17 on 2026-10-12: all three in one second, 'authenticated' last,
    # so sub_B's row ends 'authenticated' although it is paid. An old subscription's late cancel must not downgrade.
    for event_id, event in [("evt_b_chg", "charged"), ("evt_b_act", "activated"), ("evt_b_auth", "authenticated")]:
        send(client, event_id, event, created_at=2_000, sub_id="sub_B")
    send(client, "evt_a_cancel", "cancelled", created_at=1_500, sub_id="sub_A")
    assert tenant_a() == {"plan_id": "pro", "status": "active"}


def test_concurrent_old_cancel_cannot_downgrade_tenant_mid_activation():
    # sub_B's activation is applied but not committed when sub_A's cancel arrives; without the tenant lock,
    # the cancel's live-subscription check misses sub_B and overwrites Pro with Free once sub_B commits.
    def event(name, sub_id, status, created_at):
        return billing.RazorpayEvent.model_validate(
            {"event": f"subscription.{name}", "created_at": created_at,
             "payload": {"subscription": {"entity": {"id": sub_id, "status": status, "notes": {"tenant_id": TENANT_A}}}}})

    results = []  # a thread's exception is only a pytest warning, so the result is checked explicitly

    def cancel_old():
        with connect() as conn:
            results.append(billing.apply_event(conn, "evt_a_cancel", event("cancelled", "sub_A", "cancelled", 1_500)))

    with connect() as conn:
        billing.apply_event(conn, "evt_b_act", event("activated", "sub_B", "active", 2_000))
        other = threading.Thread(target=cancel_old)
        other.start()
        time.sleep(0.5)  # let the cancel reach the database while sub_B is uncommitted
    other.join()  # sub_B committed when the with-block closed
    assert results == ["applied"]  # the cancel really ran
    assert tenant_a() == {"plan_id": "pro", "status": "active"}


def signed_post(client, event_id, body: bytes):
    headers = {"X-Razorpay-Signature": hmac.new(SECRET, body, hashlib.sha256).hexdigest(), "X-Razorpay-Event-Id": event_id}
    return client.post("/webhooks/razorpay", content=body, headers=headers)


def activated_for(tenant_id):
    return json.dumps({
        "event": "subscription.activated", "created_at": 1_000,
        "payload": {"subscription": {"entity": {"id": "sub_UNKNOWN0000001", "status": "active", "notes": {"tenant_id": tenant_id}}}},
    }).encode()


def test_failed_apply_does_not_mark_event_seen(client, monkeypatch):
    # Dedup row and plan change share one transaction: if applying fails, Razorpay's retry must not be "duplicate".
    monkeypatch.setitem(billing.TENANT_STATE, "active", None)  # forces a crash after the dedup row is inserted
    with pytest.raises(TypeError):  # TestClient re-raises unhandled app errors
        signed_post(client, "evt_crash", activated_for(TENANT_A))
    with connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM webhook_events").fetchone()["n"] == 0


@pytest.mark.parametrize("tenant_id", ["00000000-0000-7000-8000-0000000000ff", "not-a-uuid", 42])
def test_event_for_unknown_tenant_is_acknowledged_not_crashed(client, tenant_id):
    # Can never succeed on retry, so it is recorded and acknowledged (200) instead of a 500 Razorpay retries forever.
    r = signed_post(client, "evt_unknown_tenant", activated_for(tenant_id))
    assert r.status_code == 200 and r.json()["status"] == "ignored: unknown tenant"
    with connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM subscriptions").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM tenants WHERE plan_id <> 'free'").fetchone()["n"] == 0


@pytest.mark.parametrize("body", [
    b"not json",
    b"[]",
    b'{"event": "subscription.activated", "created_at": 1000, "payload": {}}',  # no subscription entity
    b'{"event": "subscription.activated", "created_at": 1000, "payload": {"subscription": {"entity": {"id": "s"}}}}',
    b'{"event": "payment.captured", "created_at": 99999999999999, "payload": {}}',  # timestamp out of range
    b'{"created_at": 1000, "payload": {}}',  # no event name
])
def test_signed_but_malformed_event_is_400_and_changes_nothing(client, body):
    r = signed_post(client, "evt_malformed", body)
    assert r.status_code == 400 and r.json()["detail"] == "Malformed webhook event."
    with connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM webhook_events").fetchone()["n"] == 0


def test_non_subscription_event_is_recorded_but_changes_nothing(client):
    body = json.dumps({"event": "payment.captured", "created_at": 1_000, "payload": {}}).encode()
    sig = hmac.new(SECRET, body, hashlib.sha256).hexdigest()
    headers = {"X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": "evt_pay"}
    r = client.post("/webhooks/razorpay", content=body, headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "applied"
    assert tenant_a()["plan_id"] == "free"
    with connect() as conn:
        assert conn.execute("SELECT event_id FROM webhook_events").fetchall() == [{"event_id": "evt_pay"}]
    assert client.post("/webhooks/razorpay", content=body, headers=headers).json()["status"] == "duplicate ignored"


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
