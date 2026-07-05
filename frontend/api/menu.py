"""Vercel serverless: the menu + current stock, for the ordering UI.

Self-contained (stdlib + psycopg only) so there are no local modules to bundle.
Reads through the read-only pizza_ro role on Neon.
"""
import json
import os
from http.server import BaseHTTPRequestHandler

import psycopg
from psycopg.rows import dict_row

RO_URL = os.environ.get("PIZZA_RO_URL", "")


def get_menu():
    # prepare_threshold=None is required for Neon's pooled endpoint (PgBouncer).
    with psycopg.connect(RO_URL, row_factory=dict_row) as conn:
        conn.prepare_threshold = None
        conn.read_only = True
        return conn.execute(
            "SELECT m.id, m.name, m.category, m.price::float AS price, "
            "       COALESCE(i.quantity, 0) AS in_stock "
            "FROM menu_items m LEFT JOIN inventory i ON i.menu_item_id = m.id "
            "ORDER BY m.category, m.id"
        ).fetchall()


class handler(BaseHTTPRequestHandler):
    def _reply(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def do_GET(self):
        try:
            return self._reply(200, {"menu": get_menu()})
        except Exception:
            import traceback
            traceback.print_exc()
            return self._reply(200, {"menu": [], "error": "Couldn't load the menu right now."})
