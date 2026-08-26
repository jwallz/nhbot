"""Thin Postgres access — a lazy connection pool over config.DSN, dict rows.

No ORM by design. Query functions live in repo.py and return plain dicts.
"""
from contextlib import contextmanager
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extras import RealDictCursor
from nhbot.config import DSN

_pool = None

def _get_pool():
    global _pool
    if _pool is None:
        _pool = SimpleConnectionPool(1, 8, dsn=DSN, cursor_factory=RealDictCursor)
    return _pool

@contextmanager
def cursor():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SET search_path = nh, public")
            yield cur
    finally:
        pool.putconn(conn)

def query(sql, params=None):
    with cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()

def query_one(sql, params=None):
    rows = query(sql, params)
    return rows[0] if rows else None
