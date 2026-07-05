"""Postgres connections to the shared data platform (the `pizza` schema on Neon).

Two roles, two connection strings, read from server-side env vars only:
  PIZZA_RO_URL  read-only  : the NL->SQL /ask endpoint
  PIZZA_RW_URL  read-write : placing orders, the nightly restock

Both connect with search_path=pizza, so queries use unqualified table names
(orders, menu_items, ...) and resolve to the pizza schema.
"""

import os

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv()

RO_URL = os.environ.get("PIZZA_RO_URL", "")
RW_URL = os.environ.get("PIZZA_RW_URL", "")


def ro_connect() -> psycopg.Connection:
    """Read-only connection for NL->SQL queries. read_only is belt-and-suspenders
    on top of the pizza_ro role, which already cannot write anything."""
    conn = psycopg.connect(RO_URL, row_factory=dict_row, options="-c search_path=pizza")
    conn.read_only = True
    return conn


def rw_connect() -> psycopg.Connection:
    """Read-write connection for placing orders and the nightly restock (pizza_rw)."""
    return psycopg.connect(RW_URL, row_factory=dict_row, options="-c search_path=pizza")
