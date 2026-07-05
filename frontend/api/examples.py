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
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"shop": SHOP_NAME, "examples": EXAMPLES}).encode())
