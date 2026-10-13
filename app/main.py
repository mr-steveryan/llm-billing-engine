import hashlib
import json
from pathlib import Path
from typing import Annotated

import razorpay
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from app import billing, meter
from app.config import settings
from app.db import connect

app = FastAPI(title="LLM Billing Engine")


def tenant_id_for(conn, api_key: str | None):
    # The tenant comes only from the API key, never from the request body: that is the isolation boundary.
    if not api_key:
        raise HTTPException(401, "Missing X-API-Key header.")
    row = conn.execute(
        "SELECT id FROM tenants WHERE api_key_hash = %s", (hashlib.sha256(api_key.encode()).hexdigest(),)
    ).fetchone()
    if not row:
        raise HTTPException(401, "Invalid API key.")
    return row["id"]


@app.get("/health")
def health():
    with connect() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.post("/generate")
def generate(
    usage: meter.Usage,
    x_api_key: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header()] = None,
):
    # Tokens are simulated by the caller; no model is called. This endpoint only meters.
    with connect() as conn:  # one transaction: commits on success, rolls back on any exception
        tenant_id = tenant_id_for(conn, x_api_key)
        if not idempotency_key or len(idempotency_key) > 255:
            raise HTTPException(400, "Idempotency-Key header is required (1-255 characters).")
        try:
            event, replayed = meter.record(conn, tenant_id, idempotency_key, usage)
        except meter.BillingError as e:
            raise HTTPException(e.status_code, e.message)
    # str, not float: keeps the exact decimal value stored in Postgres.
    return {"event_id": event["id"], "cost_usd": str(event["cost_usd"]), "replayed": replayed}


@app.get("/usage")
def get_usage(x_api_key: Annotated[str | None, Header()] = None):
    with connect() as conn:
        tenant_id = tenant_id_for(conn, x_api_key)  # key -> tenant: you can only ever read your own usage
        tenant = conn.execute(
            """SELECT t.plan_id, t.status, p.monthly_calls, p.monthly_tokens
               FROM tenants t JOIN plans p ON p.id = t.plan_id WHERE t.id = %s""",
            (tenant_id,),
        ).fetchone()
        used = meter.monthly_usage(conn, tenant_id)
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
        "cost_usd": str(used["cost_usd"]),
    }


@app.get("/checkout")
def checkout_page():
    return FileResponse(Path(__file__).parent / "checkout.html")


@app.post("/checkout")
def create_checkout(x_api_key: Annotated[str | None, Header()] = None):
    with connect() as conn:  # one transaction: if Razorpay raises, nothing is stored
        tenant_id = tenant_id_for(conn, x_api_key)
        # Row lock serializes concurrent checkouts per tenant; held across the Razorpay call, acceptable at this volume.
        plan = conn.execute("SELECT plan_id FROM tenants WHERE id = %s FOR UPDATE", (tenant_id,)).fetchone()["plan_id"]
        if plan == "pro":
            raise HTTPException(409, "Tenant is already on Pro.")
        # A second Razorpay subscription would double-charge, so an earlier unpaid or authorised one blocks a new one.
        pending = conn.execute(
            "SELECT id, status, updated_at FROM subscriptions WHERE tenant_id = %s AND status IN ('created', 'authenticated')",
            (tenant_id,),
        ).fetchall()
        if any(s["status"] == "authenticated" for s in pending):
            raise HTTPException(409, "Payment mandate already authorised; awaiting the first charge to activate Pro.")
        client = razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret.get_secret_value()))
        if pending:  # unpaid checkout from an earlier click
            # Our row is only a mirror and may be stale if a webhook was missed, so ask Razorpay before reusing it.
            # Read-only: the mirror and the tenant's plan still change only through webhooks.
            newest = max(pending, key=lambda s: s["updated_at"])
            live = client.subscription.fetch(newest["id"])["status"]
            if live == "created":
                return {"key_id": settings.razorpay_key_id, "subscription_id": newest["id"]}
            if live not in ("cancelled", "completed", "expired"):  # live at Razorpay (e.g. paid): never create a second
                raise HTTPException(409, "A subscription is already in progress at Razorpay; "
                                         "your plan updates when its webhook arrives.")
            # Dead at Razorpay: fall through and start a fresh subscription.
        # Server sets tenant_id from the authenticated key; the browser never chooses who gets upgraded.
        sub = client.subscription.create({
            "plan_id": settings.razorpay_plan_id,
            "total_count": 12,  # 12 monthly cycles; Razorpay requires an end
            "notes": {"tenant_id": str(tenant_id)},
        })
        conn.execute(
            "INSERT INTO subscriptions (id, tenant_id, status, updated_at) VALUES (%s, %s, %s, to_timestamp(%s))",
            (sub["id"], tenant_id, sub["status"], sub["created_at"]),
        )
    # The browser's payment "success" callback is never trusted: only the signed webhook changes the plan.
    return {"key_id": settings.razorpay_key_id, "subscription_id": sub["id"]}


@app.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request):
    raw = await request.body()  # raw bytes: the signature is computed over these exact bytes
    if not billing.signature_is_valid(raw, request.headers.get("x-razorpay-signature")):
        raise HTTPException(400, "Invalid webhook signature.")
    event_id = request.headers.get("x-razorpay-event-id")
    if not event_id:
        raise HTTPException(400, "Missing x-razorpay-event-id header.")
    event = json.loads(raw)

    def apply():
        with connect() as conn:  # one transaction for dedup + plan sync
            return billing.apply_event(conn, event_id, event)

    return {"status": await run_in_threadpool(apply)}  # psycopg is blocking; keep it off the event loop
