import hashlib
import hmac
import logging

import psycopg
from psycopg.rows import DictRow

from app.config import settings

log = logging.getLogger(__name__)

# Razorpay subscription status -> (tenant plan, tenant status). Decided by status, not event name,
# so every subscription.* event (activated, charged, halted, cancelled, updated, ...) follows one rule.
# 'pending' (a charge failed, Razorpay is still retrying) keeps access: only 'halted' (retries exhausted) blocks.
# Statuses not listed (created, authenticated, pending, paused) mirror into subscriptions but leave the tenant as is.
TENANT_STATE = {
    "active": ("pro", "active"),
    "halted": ("pro", "past_due"),
    "cancelled": ("free", "active"),
    "completed": ("free", "active"),
    "expired": ("free", "active"),
}


def signature_is_valid(raw_body: bytes, signature: str | None) -> bool:
    # HMAC-SHA256 over the exact bytes Razorpay sent; re-serialized JSON would not match.
    expected = hmac.new(settings.razorpay_webhook_secret.get_secret_value().encode(), raw_body, hashlib.sha256).hexdigest()
    # Bytes, not str: compare_digest raises TypeError on non-ASCII str. Starlette decodes headers as latin-1.
    return signature is not None and hmac.compare_digest(expected.encode(), signature.encode("latin-1"))  # constant-time


def apply_event(conn: psycopg.Connection[DictRow], event_id: str, event: dict) -> str:
    """Apply one verified Razorpay event. Returns what happened: applied / duplicate ignored / ignored: <why>.

    Runs in the caller's transaction: the dedup row and the plan change commit together or not at all,
    so an event can never be marked seen without being applied.
    """
    # Duplicates are expected here (Razorpay retries), so DO NOTHING is the intended path, not a hidden error.
    first_time = conn.execute(
        "INSERT INTO webhook_events (event_id, event_type) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING event_id",
        (event_id, event["event"]),
    ).fetchone()
    if not first_time:
        return "duplicate ignored"
    if not event["event"].startswith("subscription."):
        return "applied"  # recorded, nothing to sync

    sub = event["payload"]["subscription"]["entity"]
    # tenant_id was put in notes by our own POST /checkout and arrives here inside a signed payload.
    # No tenant_id (Razorpay sends empty notes as []) = a subscription this engine didn't create, e.g. one made
    # in the dashboard. Acknowledge it so Razorpay stops retrying (and doesn't disable the webhook), touch no tenant.
    notes = sub["notes"]
    if not isinstance(notes, dict) or "tenant_id" not in notes:
        log.warning("Razorpay %s for %s has no notes.tenant_id; not created by /checkout, ignored", event["event"], sub["id"])
        return "ignored: subscription has no tenant_id"
    tenant_id = notes["tenant_id"]
    # Razorpay doesn't guarantee delivery order. Only apply an event at least as new as the last one applied,
    # so a late 'activated' can't undo a 'cancelled'.
    newer = conn.execute(
        """INSERT INTO subscriptions (id, tenant_id, status, updated_at) VALUES (%s, %s, %s, to_timestamp(%s))
           ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, updated_at = EXCLUDED.updated_at
           WHERE subscriptions.updated_at <= EXCLUDED.updated_at
           RETURNING id""",
        (sub["id"], tenant_id, sub["status"], event["created_at"]),
    ).fetchone()
    if not newer:
        return "ignored: older than last applied event"
    if sub["status"] in TENANT_STATE:
        plan_id, status = TENANT_STATE[sub["status"]]
        # A downgrade must not undo another live subscription of the tenant: /checkout starts a new one when the old one
        # died at Razorpay, and the old one's 'cancelled' webhook can arrive (retried) after the new one is paid.
        conn.execute(
            """UPDATE tenants SET plan_id = %s, status = %s WHERE id = %s
               AND (%s = 'pro' OR NOT EXISTS (SELECT 1 FROM subscriptions
                    WHERE tenant_id = %s AND id <> %s AND status IN ('active', 'halted')))""",
            (plan_id, status, tenant_id, plan_id, tenant_id, sub["id"]),
        )
    return "applied"
