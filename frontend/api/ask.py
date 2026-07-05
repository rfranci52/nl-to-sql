"""Vercel serverless: plain-English -> SQL for Tony's Pizza (read-only).

Thin handler: rate limit, then hand off to _engine.answer, which translates the
question to SQL over HTTPS, enforces SELECT-only safety, and runs it against Neon
through the read-only pizza_ro role.

Env vars (set in Vercel):
  ANTHROPIC_API_KEY            required
  PIZZA_RO_URL                 required (pooled Neon URL, pizza_ro role)
  UPSTASH_REDIS_REST_URL/TOKEN optional; when both are set, rate limiting turns on
"""
import json
import os
from http.server import BaseHTTPRequestHandler

import _engine
import _ratelimit

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
PER_VISITOR = int(os.environ.get("PER_VISITOR_DAILY", "15"))
GLOBAL = int(os.environ.get("GLOBAL_DAILY", "500"))
V_MSG = (f"You've used your {PER_VISITOR} free questions for today. Want a full "
         "walkthrough? Get in touch and I'll show you the rest.")
G_MSG = ("This live demo has hit its daily limit; check back tomorrow, or reach out "
         "and I'll walk you through it directly.")


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
            return self._reply(400, {"ok": False, "clarify": "Invalid request."})

        question = (data.get("question") or "").strip()
        if not question:
            return self._reply(200, {"ok": False, "clarify": "Ask a question about the pizza shop."})

        ip = (self.headers.get("x-forwarded-for") or "unknown").split(",")[0].strip()
        allowed, message = _ratelimit.check(ip, "ask", PER_VISITOR, GLOBAL, V_MSG, G_MSG)
        if not allowed:
            return self._reply(200, {"ok": False, "limit": True, "clarify": message})

        if not ANTHROPIC_API_KEY:
            return self._reply(200, {"ok": False, "clarify": "The demo isn't configured yet."})

        try:
            return self._reply(200, _engine.answer(question))
        except Exception:
            import traceback
            traceback.print_exc()
            return self._reply(200, {"ok": False,
                                     "clarify": "Something went wrong answering that, please try again."})
