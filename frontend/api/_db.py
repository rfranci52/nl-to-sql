"""Postgres connections for the serverless functions. Deploy-side mirror of
~/pizza-sql/pgdb.py, adapted for Vercel + Neon's pooled endpoint.

Connection strings come from the environment (set in Vercel), and they must be the
POOLED Neon URLs. prepare_threshold=None disables psycopg's automatic prepared
statements, which otherwise collide with PgBouncer (Neon's pooler) in transaction
mode. This is the single most common reason a Neon app works locally but 500s on
serverless.
"""
import os

import psycopg
from psycopg.rows import dict_row

RO_URL = os.environ.get("PIZZA_RO_URL", "")
RW_URL = os.environ.get("PIZZA_RW_URL", "")


def _connect(url: str) -> psycopg.Connection:
    conn = psycopg.connect(url, row_factory=dict_row, options="-c search_path=pizza")
    conn.prepare_threshold = None  # required for the Neon pooler (PgBouncer)
    return conn


def ro_connect() -> psycopg.Connection:
    """Read-only connection (pizza_ro role, which physically cannot write)."""
    conn = _connect(RO_URL)
    conn.read_only = True
    return conn


def rw_connect() -> psycopg.Connection:
    """Read-write connection (pizza_rw role) for placing orders + restock."""
    return _connect(RW_URL)
