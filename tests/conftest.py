import os
from pathlib import Path

import psycopg
from psycopg.conninfo import make_conninfo

# Same Postgres server as dev, but a separate database, so tests never touch your dev data.
# Inside the api container DATABASE_URL points at host db; on your machine, at the port compose publishes.
DEV_URL = os.environ.get("DATABASE_URL", "postgresql://billing:billing@localhost:5432/billing")
TEST_URL = make_conninfo(DEV_URL, dbname="billing_test")
os.environ["DATABASE_URL"] = TEST_URL  # overwrite, not setdefault: the app under test must only see billing_test

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import connect  # noqa: E402
from app.main import app  # noqa: E402

TENANT_A = "00000000-0000-7000-8000-00000000000a"
TENANT_B = "00000000-0000-7000-8000-00000000000b"
KEY_A = "demo-key-tenant-a"
KEY_B = "demo-key-tenant-b"


@pytest.fixture(scope="session", autouse=True)
def test_database():
    # Rebuilt from schema.sql + seed.sql every run, so it always matches the current schema.
    root = Path(__file__).parent.parent
    with psycopg.connect(DEV_URL, autocommit=True) as admin:  # CREATE DATABASE can't run in a transaction
        admin.execute("DROP DATABASE IF EXISTS billing_test WITH (FORCE)")
        admin.execute("CREATE DATABASE billing_test")
    with psycopg.connect(TEST_URL) as conn:
        conn.execute((root / "schema.sql").read_text())
        conn.execute((root / "seed.sql").read_text())


@pytest.fixture(autouse=True)
def clean_db(test_database):
    # Wipes usage and resets plans in billing_test: every test starts from an empty month on Free.
    with connect() as conn:
        conn.execute("DELETE FROM usage_events")
        conn.execute("UPDATE tenants SET plan_id = 'free', status = 'active'")


@pytest.fixture
def client():
    return TestClient(app)


def generate(client, idem, api_key=KEY_A, input_tokens=100, cached=0, output_tokens=100, reasoning=0):
    return client.post(
        "/generate",
        headers={"X-API-Key": api_key, "Idempotency-Key": idem},
        json={"input_tokens": input_tokens, "cached_input_tokens": cached,
              "output_tokens": output_tokens, "reasoning_tokens": reasoning},
    )


def preload(tenant_id, calls, input_tokens_each=0):
    # Insert usage straight into the DB, so boundary tests don't need 1,000 HTTP calls.
    with connect() as conn:
        conn.execute(
            """INSERT INTO usage_events (tenant_id, idempotency_key, input_tokens, cached_input_tokens,
                                         output_tokens, reasoning_tokens, cost_usd)
               SELECT %s, 'preload-' || g, %s, 0, 0, 0, 0 FROM generate_series(1, %s) g""",
            (tenant_id, input_tokens_each, calls),
        )


def event_count(tenant_id):
    with connect() as conn:
        return conn.execute("SELECT count(*) AS n FROM usage_events WHERE tenant_id = %s", (tenant_id,)).fetchone()["n"]
