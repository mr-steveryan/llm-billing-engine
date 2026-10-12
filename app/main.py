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


@app.get("/checkout")
def checkout_page():
    return FileResponse(Path(__file__).parent / "checkout.html")


@app.post("/checkout")
def create_checkout(x_api_key: Annotated[str | None, Header()] = None):
    with connect() as conn:
        tenant_id = tenant_id_for(conn, x_api_key)
        plan = conn.execute("SELECT plan_id FROM tenants WHERE id = %s", (tenant_id,)).fetchone()["plan_id"]
    if plan == "pro":
        raise HTTPException(409, "Tenant is already on Pro.")  # a second subscription would double-charge
    client = razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret.get_secret_value()))
    # Server sets tenant_id from the authenticated key; the browser never chooses who gets upgraded.
    sub = client.subscription.create({
        "plan_id": settings.razorpay_plan_id,
        "total_count": 12,  # 12 monthly cycles; Razorpay requires an end
        "notes": {"tenant_id": str(tenant_id)},
    })
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
