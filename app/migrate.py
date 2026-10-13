"""Apply migrations/NNNN_*.sql in name order, each exactly once. The API container runs this before uvicorn:

    python -m app.migrate
"""
from pathlib import Path

import psycopg

MIGRATIONS = Path(__file__).parent.parent / "migrations"


def migrate(database_url: str) -> list[str]:
    """Apply every migration not yet recorded in schema_migrations. Returns the names applied now."""
    applied = []
    with psycopg.connect(database_url) as conn:  # one transaction: all pending files apply, or none do
        # Two API containers starting together must not apply the same file twice; the lock ends with the transaction.
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('schema_migrations'))")
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                            name       text        PRIMARY KEY,
                            applied_at timestamptz NOT NULL DEFAULT now())""")
        done = {row[0] for row in conn.execute("SELECT name FROM schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name not in done:
                conn.execute(path.read_text())
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
                applied.append(path.name)
    return applied


if __name__ == "__main__":
    from app.config import settings  # here, not at import: tests call migrate() with their own URL

    print("migrations applied:", ", ".join(migrate(settings.database_url)) or "none (up to date)")
