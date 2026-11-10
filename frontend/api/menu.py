"""Vercel serverless: the menu + current stock, for the ordering UI.

Self-contained (stdlib + psycopg only) so there are no local modules to bundle.
Reads through the read-only pizza_ro role on Neon. Good responses are cached at the
edge, so polling this endpoint can't keep the database compute awake.
"""
import json
import os
from http.server import BaseHTTPRequestHandler

import psycopg
from psycopg.rows import dict_row

RO_URL = os.environ.get("PIZZA_RO_URL", "")
# Longer than the database's idle window, so a slow poll still lets the compute suspend.
EDGE_CACHE = "public, max-age=0, s-maxage=900"


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
    def send_error(self, code, message=None, explain=None):
        # A short JSON reply in place of the standard library's HTML error page. A method with
        # no handler arrives here as 501; for a GET-only endpoint the right answer is 405.
        if code == 501:
            code = 405
        body = json.dumps({"error": "method not allowed" if code == 405 else "request refused"}).encode()
        self.close_connection = True
        self.send_response(code)
        if code == 405:
            self.send_header("Allow", "GET")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if getattr(self, "command", None) != "HEAD":
            self.wfile.write(body)

    def _reply(self, status, payload, cache="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def do_GET(self):
        # The edge cache keys on the full URL, so a query string would reach Postgres every time.
        if "?" in self.path:
            return self._reply(400, {"menu": [], "error": "Invalid request."})
        try:
            menu = get_menu()
            return self._reply(200, {"menu": menu}, EDGE_CACHE if menu else "no-store")
        except Exception:
            import traceback
            traceback.print_exc()
            return self._reply(200, {"menu": [], "error": "Couldn't load the menu right now."})
