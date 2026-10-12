import psycopg
from psycopg.rows import DictRow, dict_row

from app.config import settings


def connect() -> psycopg.Connection[DictRow]:
    # shortcut: one connection per request, no pool; add psycopg_pool if load ever matters.
    return psycopg.Connection[DictRow].connect(settings.database_url, row_factory=dict_row)
