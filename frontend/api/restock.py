"""Vercel serverless: refill inventory to baseline. The daily cron target.

Vercel Cron invokes this with GET and an `Authorization: Bearer <CRON_SECRET>`
header (added automatically when CRON_SECRET is set in the project env). Sales are
left untouched; only inventory is topped back up.
"""
import json
import os
from http.server import BaseHTTPRequestHandler

import _orders

CRON_SECRET = os.environ.get("CRON_SECRET", "")


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
            return self._reply(200, {"ok": True, "restocked": _orders.restock()})
        except Exception:
            import traceback
            traceback.print_exc()
            return self._reply(200, {"ok": False, "error": "restock failed"})
