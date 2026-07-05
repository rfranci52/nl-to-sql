"""Vercel serverless: refill inventory to baseline. The daily cron target.

Self-contained (stdlib + psycopg only). Vercel Cron invokes this with GET and an
`Authorization: Bearer <CRON_SECRET>` header. Sales are left untouched.
"""
import json
import os
from http.server import BaseHTTPRequestHandler

import psycopg
from psycopg.rows import dict_row

RW_URL = os.environ.get("PIZZA_RW_URL", "")
CRON_SECRET = os.environ.get("CRON_SECRET", "")


def restock():
    with psycopg.connect(RW_URL, row_factory=dict_row) as conn:
        conn.prepare_threshold = None
        cur = conn.execute(
            "UPDATE inventory SET quantity = m.baseline_stock "
            "FROM menu_items m WHERE m.id = inventory.menu_item_id"
        )
        return cur.rowcount


class handler(BaseHTTPRequestHandler):
    def _reply(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def do_GET(self):
        if not CRON_SECRET:
            return self._reply(200, {"ok": False, "error": "restock not configured"})
        if self.headers.get("authorization") != f"Bearer {CRON_SECRET}":
            return self._reply(403, {"ok": False, "error": "unauthorized"})
        try:
            return self._reply(200, {"ok": True, "restocked": restock()})
        except Exception:
            import traceback
            traceback.print_exc()
            return self._reply(200, {"ok": False, "error": "restock failed"})
