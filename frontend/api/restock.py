"""Vercel serverless: the daily cron. Refills inventory to baseline, then adds a few
synthetic orders for yesterday so the sales history stays current.

Self-contained (stdlib + psycopg only). Vercel Cron invokes this with GET and an
`Authorization: Bearer <CRON_SECRET>` header. Existing sales are left untouched.
db/backfill.py imports add_synthetic_orders from this file by path.
"""
import json
import os
import random
from http.server import BaseHTTPRequestHandler

import psycopg
from psycopg.rows import dict_row

RW_URL = os.environ.get("PIZZA_RW_URL", "")
CRON_SECRET = os.environ.get("CRON_SECRET", "")
SHOP_TZ = "America/New_York"
GUEST_CITY = "Online"
OPEN_HOURS = [(11, 2), (12, 6), (13, 5), (14, 2), (15, 1), (16, 2), (17, 4), (18, 7), (19, 7), (20, 5), (21, 3), (22, 1)]


def time_of_day(rng):
    hour = rng.choices([h for h, _ in OPEN_HOURS], weights=[w for _, w in OPEN_HOURS])[0]
    return f"{hour:02d}:{rng.randrange(60):02d}:{rng.randrange(60):02d}"


def restock():
    with psycopg.connect(RW_URL, row_factory=dict_row) as conn:
        conn.prepare_threshold = None
        cur = conn.execute(
            "UPDATE inventory SET quantity = m.baseline_stock "
            "FROM menu_items m WHERE m.id = inventory.menu_item_id"
        )
        return cur.rowcount


def add_synthetic_orders(conn, day):
    """Insert a few deterministic orders for one past day; returns how many. Runs in the
    caller's transaction (the caller commits). Never touches inventory."""
    cur = conn.cursor(row_factory=dict_row)
    cur.execute("SELECT pg_advisory_xact_lock(hashtext('tonys-pizza-synthetic'))")
    if cur.execute(
        "SELECT 1 FROM orders o JOIN customers c ON c.id = o.customer_id "
        "WHERE o.order_date = %s AND c.city <> %s LIMIT 1",
        (day, GUEST_CITY),
    ).fetchone():
        return 0
    customers = [r["id"] for r in cur.execute(
        "SELECT id FROM customers WHERE city <> %s ORDER BY id", (GUEST_CITY,)).fetchall()]
    menu_ids = [r["id"] for r in cur.execute("SELECT id FROM menu_items ORDER BY id").fetchall()]
    rng = random.Random(f"tonys-pizza-day-{day.isoformat()}")
    orders = []
    for _ in range(rng.randint(0, 2) if day.weekday() < 4 else rng.randint(1, 2)):
        customer = rng.choice(customers)
        status = "Cancelled" if rng.random() < 1 / 8 else "Delivered"
        at = time_of_day(rng)
        lines = [(mid, rng.randint(1, 3)) for mid in rng.sample(menu_ids, rng.randint(1, 3))]
        orders.append((at, customer, status, lines))
    for at, customer, status, lines in sorted(orders, key=lambda o: o[0]):  # ids follow time of day
        oid = cur.execute(
            "INSERT INTO orders (customer_id, order_date, status, created_at) "
            "VALUES (%s, %s, %s, (%s::date + %s::time) AT TIME ZONE %s) RETURNING id",
            (customer, day, status, day, at, SHOP_TZ),
        ).fetchone()["id"]
        for mid, qty in lines:
            cur.execute(
                "INSERT INTO order_items (order_id, menu_item_id, quantity) VALUES (%s, %s, %s)",
                (oid, mid, qty),
            )
    return len(orders)


def add_yesterdays_orders():
    with psycopg.connect(RW_URL, row_factory=dict_row) as conn:
        conn.prepare_threshold = None
        day = conn.execute(
            "SELECT (now() AT TIME ZONE %s)::date - 1 AS day", (SHOP_TZ,)
        ).fetchone()["day"]
        return add_synthetic_orders(conn, day)


class handler(BaseHTTPRequestHandler):
    def _reply(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def do_GET(self):
        if not CRON_SECRET:
            return self._reply(500, {"ok": False, "error": "cron not configured"})
        if self.headers.get("authorization") != f"Bearer {CRON_SECRET}":
            return self._reply(403, {"ok": False, "error": "unauthorized"})
        try:
            restocked = restock()
            added = add_yesterdays_orders()
            return self._reply(200, {"ok": True, "restocked": restocked, "synthetic_orders": added})
        except Exception:
            import traceback
            traceback.print_exc()
            return self._reply(500, {"ok": False, "error": "cron failed"})
