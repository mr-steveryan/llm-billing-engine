from fastapi import FastAPI

from app.db import connect

app = FastAPI(title="LLM Billing Engine")


@app.get("/health")
def health():
    with connect() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}
