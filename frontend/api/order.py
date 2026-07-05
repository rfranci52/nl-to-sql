"""Vercel serverless: place an order (write path, pizza_rw role).

Rate limited by bucket, then hands off to _orders.place_order, which validates the
items, decrements stock with a guarded update, reuses the guest's customer row
(find-or-create), and records the order in one atomic transaction.

Env vars: PIZZA_RW_URL (pooled Neon URL, pizza_rw role) required.
"""
import json
import os
from http.server import BaseHTTPRequestHandler

import _orders
import _ratelimit

PER_VISITOR = int(os.environ.get("ORDER_PER_VISITOR_DAILY", "25"))
GLOBAL = int(os.environ.get("ORDER_GLOBAL_DAILY", "1000"))
V_MSG = "You've placed a lot of demo orders today. Take a break and come back tomorrow."
G_MSG = "This live demo has taken a lot of orders today; check back tomorrow."


class handler(BaseHTTPRequestHandler):
    def _reply(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._reply(400, {"ok": False, "error": "Invalid request."})

        ip = (self.headers.get("x-forwarded-for") or "unknown").split(",")[0].strip()
        allowed, message = _ratelimit.check(ip, "order", PER_VISITOR, GLOBAL, V_MSG, G_MSG)
        if not allowed:
            return self._reply(200, {"ok": False, "error": message})

        items = data.get("items") or []
        name = data.get("name")
        try:
            return self._reply(200, _orders.place_order(items, name))
        except Exception:
            import traceback
            traceback.print_exc()
            return self._reply(200, {"ok": False, "error": "Couldn't place the order, please try again."})
