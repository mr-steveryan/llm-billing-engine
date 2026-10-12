"""Replay a signed Razorpay webhook against the local API: valid, replayed, forged.

Razorpay has no CLI like `stripe trigger`, so this signs a subscription.activated event with your
RAZORPAY_WEBHOOK_SECRET (read from .env) exactly as Razorpay does (HMAC-SHA256 of the raw body).

    uv run scripts/replay_webhook.py [tenant_id]
"""
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScriptSettings(BaseSettings):
    # Only needs the webhook secret; ignore the other keys in .env.
    model_config = SettingsConfigDict(env_file=Path(__file__).parent.parent / ".env", extra="ignore", str_min_length=1)
    razorpay_webhook_secret: SecretStr


URL = "http://localhost:8000/webhooks/razorpay"
tenant_id = sys.argv[1] if len(sys.argv) > 1 else "00000000-0000-7000-8000-00000000000a"  # seeded Tenant A

event_id = f"evt_replay_{uuid.uuid4().hex[:12]}"
body = json.dumps({
    "entity": "event",
    "event": "subscription.activated",
    "created_at": int(time.time()),
    "payload": {"subscription": {"entity": {
        "id": f"sub_replay{uuid.uuid4().hex[:9]}", "status": "active", "notes": {"tenant_id": tenant_id},
    }}},
}).encode()
secret = ScriptSettings().razorpay_webhook_secret  # pyright: ignore[reportCallIssue] -- filled from .env
good_sig = hmac.new(secret.get_secret_value().encode(), body, hashlib.sha256).hexdigest()


def post(label, signature):
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Content-Type": "application/json", "X-Razorpay-Signature": signature, "X-Razorpay-Event-Id": event_id})
    try:
        with urllib.request.urlopen(req) as r:
            status, text = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read().decode()
    print(f"{label:<28} {event_id}  HTTP {status}  {text}")


post("1. valid", good_sig)
post("2. replay (same event id)", good_sig)
post("3. forged signature", "0" * 64)
