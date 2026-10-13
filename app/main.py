from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import ValidationError

from app import billing, meter
from app.config import settings
from app.db import connect

# HTTP layer only: parse and check input, call app.meter / app.billing, map their errors to status codes.
app = FastAPI(title="LLM Billing Engine")


def tenant_id_for(conn, api_key: str | None):
    # The tenant comes only from the API key, never from the request body: that is the isolation boundary.
    if not api_key:
        raise HTTPException(401, "Missing X-API-Key header.")
    tenant_id = meter.tenant_for_key(conn, api_key)
    if not tenant_id:
        raise HTTPException(401, "Invalid API key.")
    return tenant_id


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
    # String, not float: keeps the exact decimal stored in Postgres. "f": plain notation, so zero is never "0E-10".
    return {"event_id": event["id"], "cost_usd": format(event["cost_usd"], "f"), "replayed": replayed}


@app.get("/usage")
def get_usage(x_api_key: Annotated[str | None, Header()] = None):
    with connect() as conn:
        tenant_id = tenant_id_for(conn, x_api_key)  # key -> tenant: you can only ever read your own usage
        return meter.usage_summary(conn, tenant_id)


@app.get("/checkout")
def checkout_page():
    return FileResponse(Path(__file__).parent / "checkout.html")


@app.post("/checkout")
def create_checkout(x_api_key: Annotated[str | None, Header()] = None):
    with connect() as conn:  # one transaction: if Razorpay raises, nothing is stored
        tenant_id = tenant_id_for(conn, x_api_key)
        try:
            subscription_id = billing.start_checkout(conn, tenant_id)
        except meter.BillingError as e:
            raise HTTPException(e.status_code, e.message)
    # The browser's payment "success" callback is never trusted: only the signed webhook changes the plan.
    return {"key_id": settings.razorpay_key_id, "subscription_id": subscription_id}


@app.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request):
    raw = await request.body()  # raw bytes: the signature is computed over these exact bytes
    if not billing.signature_is_valid(raw, request.headers.get("x-razorpay-signature")):
        raise HTTPException(400, "Invalid webhook signature.")
    event_id = request.headers.get("x-razorpay-event-id")
    if not event_id:
        raise HTTPException(400, "Missing x-razorpay-event-id header.")
    try:
        event = billing.RazorpayEvent.model_validate_json(raw)
    except ValidationError:
        raise HTTPException(400, "Malformed webhook event.")

    def apply():
        with connect() as conn:  # one transaction for dedup + plan sync
            return billing.apply_event(conn, event_id, event)

    return {"status": await run_in_threadpool(apply)}  # psycopg is blocking; keep it off the event loop
