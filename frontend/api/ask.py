"""Vercel serverless: plain-English -> SQL for Tony's Pizza (read-only).

Self-contained (stdlib + psycopg only). Translates the question to SQL over HTTPS
to Claude, enforces SELECT-only safety here (not trusted to the model), and runs it
through the read-only pizza_ro role on Neon. Rate limited via Upstash (fails open).
"""
import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler

import psycopg
from psycopg.rows import dict_row

MODEL = "claude-haiku-4-5"
MAX_ROWS = 200
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
RO_URL = os.environ.get("PIZZA_RO_URL", "")

UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "").rstrip("/")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
PER_VISITOR = int(os.environ.get("PER_VISITOR_DAILY", "15"))
GLOBAL = int(os.environ.get("GLOBAL_DAILY", "500"))

SCHEMA_DESCRIPTION = """\
PostgreSQL database for a pizza shop (schema: pizza; unqualified table names resolve there). Tables:

menu_items(id, name, category, price, baseline_stock)
    The menu. category is one of 'Whole Pie', 'Slice', 'Wings', 'Drink', 'Side'.
    price is the price per item. baseline_stock is the nightly restock target
    (NOT the current stock).

inventory(menu_item_id, quantity)
    Current stock, per menu item. menu_item_id -> menu_items.id. quantity is how
    many are in stock right now.

customers(id, name, city, joined_date)
    One row per customer. joined_date is a date.

orders(id, customer_id, order_date, status, created_at)
    One row per order. customer_id -> customers.id. order_date is a date.
    created_at is a timestamptz for when the order was placed (date and time of day).
    status is one of 'Received', 'In Progress', 'Delivered', 'Cancelled'.

order_items(id, order_id, menu_item_id, quantity)
    Line items. order_id -> orders.id, menu_item_id -> menu_items.id.

Notes for writing correct SQL:
- Revenue / sales / spend = SUM(order_items.quantity * menu_items.price); join
  order_items -> menu_items for price, and -> orders for the customer or date.
- "Sold" / "popular" / "how many ordered" of an item = SUM(order_items.quantity).
- "In stock" / "how many left" = inventory.quantity (join inventory -> menu_items
  for the item name).
- Match text the visitor typed case-insensitively, and allow partial matches: use
  ILIKE with wildcards, never plain =. This matters most for customer names, e.g.
  WHERE customers.name ILIKE '%rakim%'. Their capitalization won't match what's
  stored (someone who orders as "rakim" is saved exactly as they typed it).
- For completed sales, filter orders.status = 'Delivered' unless the user clearly
  wants all orders (e.g. "including cancelled", "pending orders").
- Group by menu_items.category for questions comparing categories (pies vs drinks).
- Dates: today is CURRENT_DATE; use interval math like CURRENT_DATE - INTERVAL '30 days'.
- "Most recent" / "latest" / "last" order = the order with the largest orders.id, or
  equivalently the latest created_at. order_date is a date only, so many orders share
  one date; never use MAX(order_date) to pick a single order. Use MAX(orders.id) or
  ORDER BY orders.id DESC LIMIT 1 for that one order's rows.
- created_at is a full timestamp, so use it for time-of-day questions (busiest hour,
  orders in the last N minutes or hours, trends over time), e.g. WHERE created_at >=
  now() - interval '1 hour', or GROUP BY date_trunc('hour', created_at).
- An order has one or more order_items, so ALWAYS return exactly one row per order,
  never one row per line item. "The latest order" is 1 row; "the latest 5 orders" is 5
  rows. Show each order's full contents in that single row by aggregating its items with
  GROUP BY orders.id, e.g. STRING_AGG(oi.quantity || 'x ' || mi.name, ', ') AS items,
  SUM(oi.quantity) AS item_count, SUM(oi.quantity * mi.price) AS total. Only go to
  line-item level (several rows per order) when the user explicitly asks for the items,
  the line items, or what is "in" an order.
- Use standard PostgreSQL syntax.
"""

SYSTEM = f"""You translate a customer's plain-English question into ONE PostgreSQL \
SELECT query against this exact schema.

{SCHEMA_DESCRIPTION}

Rules:
- Return a single read-only SELECT (a WITH ... SELECT common table expression is \
fine). Never write to the database.
- Use only the tables and columns above. Never invent a column.
- Always fill "interpretation" with a plain-English restatement of what you think \
they asked; this is shown back to the visitor.
- If the question is ambiguous, or can't be answered from this schema, or isn't \
about the pizza shop at all: set understood=false, leave sql empty, and put a \
short, specific question in "clarifying_question".
- When understood=true, leave clarifying_question empty. When understood=false, \
leave sql empty."""

SCHEMA_JSON = {
    "type": "object",
    "properties": {
        "understood": {"type": "boolean"},
        "interpretation": {"type": "string"},
        "sql": {"type": "string"},
        "clarifying_question": {"type": "string"},
    },
    "required": ["understood", "interpretation", "sql", "clarifying_question"],
    "additionalProperties": False,
}


def translate(question):
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 1024,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": question}],
        "output_config": {"format": {"type": "json_schema", "schema": SCHEMA_JSON}},
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        method="POST",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.loads(r.read())
    text = next((b["text"] for b in payload.get("content", []) if b.get("type") == "text"), "{}")
    return json.loads(text)


_FORBIDDEN = ("insert", "update", "delete", "drop", "alter", "create", "replace",
              "truncate", "grant", "revoke", "copy", "into", "vacuum", "begin", "commit")


def is_safe_select(sql):
    s = sql.strip().rstrip(";").strip()
    if not s or ";" in s:
        return False
    lowered = s.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        return False
    words = set(lowered.replace("(", " ").replace(")", " ").replace(",", " ").split())
    return not any(bad in words for bad in _FORBIDDEN)


def _clean(v):
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", "replace")
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def run_query(sql):
    with psycopg.connect(RO_URL, row_factory=dict_row) as conn:
        conn.prepare_threshold = None
        conn.read_only = True
        cur = conn.execute(sql)
        columns = [c.name for c in cur.description]
        rows = [{k: _clean(v) for k, v in row.items()} for row in cur.fetchmany(MAX_ROWS)]
    return columns, rows


def answer(question):
    t = translate(question)
    if not t.get("understood"):
        return {"ok": False, "interpretation": t.get("interpretation", ""),
                "clarify": t.get("clarifying_question")
                or "Could you rephrase that as a question about the shop's data?"}
    sql = t.get("sql", "")
    if not is_safe_select(sql):
        return {"ok": False, "interpretation": t.get("interpretation", ""),
                "clarify": "I could only answer that with a read-only lookup, try "
                           "rephrasing as a question about the data."}
    columns, rows = run_query(sql)
    return {"ok": True, "interpretation": t.get("interpretation", ""), "sql": sql,
            "columns": columns, "rows": rows}


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
        gkey, vkey = f"pz:ask:g:{today}", f"pz:ask:v:{ip}:{today}"
        g = _redis("INCR", gkey); _redis("EXPIRE", gkey, 172800)
        v = _redis("INCR", vkey); _redis("EXPIRE", vkey, 172800)
        if g and int(g) > GLOBAL:
            return False, ("This live demo has hit its daily limit; check back tomorrow, "
                           "or reach out and I'll walk you through it directly.")
        if v and int(v) > PER_VISITOR:
            return False, (f"You've used your {PER_VISITOR} free questions for today. Want a full "
                           "walkthrough? Get in touch and I'll show you the rest.")
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
            return self._reply(400, {"ok": False, "clarify": "Invalid request."})

        question = (data.get("question") or "").strip()
        if not question:
            return self._reply(200, {"ok": False, "clarify": "Ask a question about the pizza shop."})

        ip = (self.headers.get("x-forwarded-for") or "unknown").split(",")[0].strip()
        allowed, message = check_limit(ip)
        if not allowed:
            return self._reply(200, {"ok": False, "limit": True, "clarify": message})

        if not ANTHROPIC_API_KEY:
            return self._reply(200, {"ok": False, "clarify": "The demo isn't configured yet."})

        try:
            return self._reply(200, answer(question))
        except Exception:
            import traceback
            traceback.print_exc()
            return self._reply(200, {"ok": False,
                                     "clarify": "Something went wrong answering that, please try again."})
