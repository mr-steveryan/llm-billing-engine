import os

import psycopg
from psycopg.rows import dict_row


def connect() -> psycopg.Connection:
    # shortcut: one connection per request, no pool; add psycopg_pool if load ever matters.
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
