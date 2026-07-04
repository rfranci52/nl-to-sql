"""Vercel serverless: the clickable example questions + shop name (static)."""

import json
from http.server import BaseHTTPRequestHandler

SHOP_NAME = "Tony's Pizza"
EXAMPLES = [
    "Who are our top 5 customers by how much they've spent?",
    "Which pizza sells the most?",
    "How many orders came from Brooklyn last month?",
    "What's the average order total?",
]


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"shop": SHOP_NAME, "examples": EXAMPLES}).encode())
