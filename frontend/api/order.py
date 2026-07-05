"""Vercel serverless: place an order (write path, pizza_rw role).

Self-contained (stdlib + psycopg only). Validates items, decrements stock with a
guarded update, reuses the guest's customer row (find-or-create), and records the
order in one atomic transaction. Rate limited via Upstash (fails open).
"""
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import date
from http.server import BaseHTTPRequestHandler

import psycopg
from psycopg.rows import dict_row

RW_URL = os.environ.get("PIZZA_RW_URL", "")
MAX_QTY = 20
MAX_NAME_LEN = 30
GUEST_CITY = "Online"
_UNSAFE = re.compile(r"[\x00-\x1f\x7f<>]")

UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "").rstrip("/")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
PER_VISITOR = int(os.environ.get("ORDER_PER_VISITOR_DAILY", "25"))
GLOBAL = int(os.environ.get("ORDER_GLOBAL_DAILY", "1000"))


class OutOfStock(Exception):
    def __init__(self, item):
        self.item = item


def clean_name(raw):
    name = _UNSAFE.sub("", (raw or "").strip())[:MAX_NAME_LEN].strip()
    return name or "Guest"


def place_order(items, name=None):
    cleaned = []
    for it in items or []:
        try:
            mid, qty = int(it["menu_item_id"]), int(it["quantity"])
        except (KeyError, TypeError, ValueError):
            return {"ok": False, "error": "That order had an invalid item."}
        if not (1 <= qty <= MAX_QTY):
            return {"ok": False, "error": f"Quantity must be between 1 and {MAX_QTY}."}
        cleaned.append((mid, qty))
    if not cleaned:
        return {"ok": False, "error": "Your order is empty."}

    customer = clean_name(name)
    try:
        with psycopg.connect(RW_URL, row_factory=dict_row) as conn:
            conn.prepare_threshold = None
            menu = {}
            for mid, _q in cleaned:
                row = conn.execute(
                    "SELECT name, price::float AS price FROM menu_items WHERE id = %s",
                    (mid,),
                ).fetchone()
                if row is None:
                    raise ValueError("That order had an item that isn't on the menu.")
                menu[mid] = row
            for mid, qty in cleaned:
                got = conn.execute(
                    "UPDATE inventory SET quantity = quantity - %s "
                    "WHERE menu_item_id = %s AND quantity >= %s RETURNING quantity",
                    (qty, mid, qty),
                ).fetchone()
                if got is None:
                    raise OutOfStock(menu[mid]["name"])
            cust = conn.execute(
                "SELECT id FROM customers WHERE city = %s AND lower(name) = lower(%s) "
                "ORDER BY id LIMIT 1",
                (GUEST_CITY, customer),
            ).fetchone()
            if cust is None:
                cust = conn.execute(
                    "INSERT INTO customers (name, city) VALUES (%s, %s) RETURNING id",
                    (customer, GUEST_CITY),
                ).fetchone()
            order = conn.execute(
                "INSERT INTO orders (customer_id, status) VALUES (%s, 'Received') RETURNING id",
                (cust["id"],),
            ).fetchone()
            for mid, qty in cleaned:
                conn.execute(
                    "INSERT INTO order_items (order_id, menu_item_id, quantity) "
                    "VALUES (%s, %s, %s)",
                    (order["id"], mid, qty),
                )
    except OutOfStock as e:
        return {"ok": False, "error": f"Sorry, we just ran out of {e.item}. Try fewer, or something else."}
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    lines = [{"name": menu[mid]["name"], "quantity": qty, "price": menu[mid]["price"],
              "subtotal": round(menu[mid]["price"] * qty, 2)} for mid, qty in cleaned]
    return {"ok": True, "order_id": order["id"], "customer": customer,
            "lines": lines, "total": round(sum(li["subtotal"] for li in lines), 2)}


def _redis(*parts):
    url = UPSTASH_URL + "/" + "/".join(urllib.parse.quote(str(p), safe="") for p in parts)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read()).get("result")


def check_limit(ip):
    if not (UPSTASH_URL and UPSTASH_TOKEN):
        return True, ""
    try:
        today = date.today().isoformat()
        gkey, vkey = f"pz:order:g:{today}", f"pz:order:v:{ip}:{today}"
        g = _redis("INCR", gkey); _redis("EXPIRE", gkey, 172800)
        v = _redis("INCR", vkey); _redis("EXPIRE", vkey, 172800)
        if g and int(g) > GLOBAL:
            return False, "This live demo has taken a lot of orders today; check back tomorrow."
        if v and int(v) > PER_VISITOR:
            return False, "You've placed a lot of demo orders today. Take a break and come back tomorrow."
        return True, ""
    except Exception:
        return True, ""


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
        allowed, message = check_limit(ip)
        if not allowed:
            return self._reply(200, {"ok": False, "error": message})
        try:
            return self._reply(200, place_order(data.get("items") or [], data.get("name")))
        except Exception:
            import traceback
            traceback.print_exc()
            return self._reply(200, {"ok": False, "error": "Couldn't place the order, please try again."})
