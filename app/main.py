import hashlib
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException

from app import meter
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
