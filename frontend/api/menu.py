"""Vercel serverless: the menu + current stock, for the ordering UI."""
import json
from http.server import BaseHTTPRequestHandler

import _orders


class handler(BaseHTTPRequestHandler):
    def _reply(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def do_GET(self):
        try:
            return self._reply(200, {"menu": _orders.get_menu()})
        except Exception:
            import traceback
            traceback.print_exc()
            return self._reply(200, {"menu": [], "error": "Couldn't load the menu right now."})
