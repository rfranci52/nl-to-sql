"""Vercel serverless: the clickable example questions + shop name (static)."""
import json
from http.server import BaseHTTPRequestHandler

SHOP_NAME = "Tony's Pizza"
EXAMPLES = [
    "Which category brings in the most revenue?",
    "Which item sells the most?",
    "Who are our top 5 customers by spend?",
    "How many drinks do we have in stock?",
]


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
        self.end_headers()
        if getattr(self, "command", None) != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"shop": SHOP_NAME, "examples": EXAMPLES}).encode())
