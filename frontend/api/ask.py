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

MODEL = "claude-opus-5-5"
EFFORT = "low"  # Opus 5.5 always thinks; effort is the dial for latency and cost
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_ROWS = 200
MAX_QUESTION_CHARS = 500
SHOP_TZ = "America/New_York"
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
    One row per customer. joined_date is a date. city 'Online' marks guests who
    ordered through this website (including the visitor's own orders).

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
- For revenue, sales, spend, sold, popular and top-customer questions, count every
  order EXCEPT status 'Cancelled' as a sale (join to orders and filter
  orders.status <> 'Cancelled'). Orders placed on this website are saved as
  'Received' and count. Restrict to orders.status = 'Delivered' only when the user
  asks about delivered orders specifically.
- Group by menu_items.category for questions comparing categories (pies vs drinks).
- Time: the shop is in New York, and queries run with the session time zone set to
  America/New_York, so CURRENT_DATE, now() and created_at are all New York time.
  Today is CURRENT_DATE; use interval math like CURRENT_DATE - INTERVAL '30 days'.
- "Most recent" / "latest" / "last" order = the latest created_at: ORDER BY
  orders.created_at DESC, orders.id DESC LIMIT 1. orders.id is NOT in time order
  (older history was back-filled), so never use MAX(orders.id) or ORDER BY orders.id
  to find recent orders. order_date is a date only, so many orders share one date;
  never use MAX(order_date) to pick a single order.
- created_at is a full timestamp, so use it for time-of-day questions (busiest hour,
  orders in the last N minutes or hours, trends over time), e.g. WHERE created_at >=
  now() - interval '1 hour', or GROUP BY date_trunc('hour', created_at).
- An order has one or more order_items, so ALWAYS return exactly one row per order,
  never one row per line item. "The latest order" is 1 row; "the latest 5 orders" is 5
  rows (ORDER BY orders.created_at DESC, orders.id DESC LIMIT 5). Show each order's
  full contents in that single row by aggregating its items with
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

REFUSAL_CLARIFY = ("I can only answer questions about the shop's menu, orders, customers "
                   "and stock. Try asking about one of those.")
TOO_BIG_CLARIFY = ("That one needs more work than this demo allows in a single query. "
                   "Try a narrower question.")
TIMEOUT_CLARIFY = ("That query ran past the demo's 5-second limit, so the database stopped it. "
                   "Try a narrower question.")


def _decline(message):
    return {"understood": False, "interpretation": "", "sql": "", "clarifying_question": message}


def translate(question):
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 4096,  # includes thinking; with the question cap, bounds any one call's cost
        "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": question}],
        "output_config": {
            "effort": EFFORT,
            "format": {"type": "json_schema", "schema": SCHEMA_JSON},
        },
        # Most safety-classifier declines are re-run server-side on Anthropic's recommended model.
        "fallbacks": "default",
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        method="POST",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": FALLBACK_BETA,
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.loads(r.read())
    stop = payload.get("stop_reason")
    if stop != "end_turn" or payload.get("model") != MODEL:
        print(f"ask: stop_reason={stop} model={payload.get('model')} "
              f"stop_details={payload.get('stop_details')}")
    if stop == "refusal":
        return _decline(REFUSAL_CLARIFY)
    if stop == "max_tokens":
        return _decline(TOO_BIG_CLARIFY)
    # After a mid-answer fallback, the declined model's partial output comes first.
    blocks = payload.get("content", [])
    start = max((i + 1 for i, b in enumerate(blocks) if b.get("type") == "fallback"), default=0)
    text = next((b["text"] for b in blocks[start:] if b.get("type") == "text"), "{}")
    return json.loads(text)


_FORBIDDEN = ("insert", "update", "delete", "drop", "alter", "create", "replace",
              "truncate", "grant", "revoke", "copy", "into", "vacuum", "begin", "commit")
# Functions a rollback can't undo (session advisory locks outlive it on a pooled connection,
# and a terminated backend stays terminated). Substrings, so schema-qualified or quoted
# spellings are caught too.
_FORBIDDEN_CALLS = ("advisory_lock", "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf",
                    "pg_rotate_logfile", "dblink", "lo_import", "lo_export")


def is_safe_select(sql):
    s = sql.strip().rstrip(";").strip()
    if not s or ";" in s:
        return False
    lowered = s.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        return False
    if any(call in lowered for call in _FORBIDDEN_CALLS):
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
    sql = sql.strip().rstrip(";").strip()
    with psycopg.connect(RO_URL, row_factory=dict_row, connect_timeout=10) as conn:
        conn.prepare_threshold = None
        conn.read_only = True
        # Always rolled back: pooled server connections are shared, so no setting may outlive the query.
        with conn.transaction(force_rollback=True):
            conn.execute("SET LOCAL statement_timeout = '5s'")
            conn.execute(f"SET LOCAL TIME ZONE '{SHOP_TZ}'")
            # A named cursor fetches only MAX_ROWS + 1 rows; the rest never leave Postgres.
            with conn.cursor(name="ask") as cur:
                cur.execute(sql)
                columns = [c.name for c in cur.description]
                rows = cur.fetchmany(MAX_ROWS + 1)
    return (columns, [{k: _clean(v) for k, v in row.items()} for row in rows[:MAX_ROWS]],
            len(rows) > MAX_ROWS)


def answer(question):
    t = translate(question)
    if not t.get("understood"):
        return {"ok": False, "interpretation": t.get("interpretation", ""),
                "clarify": t.get("clarifying_question")
                or "Could you rephrase that as a question about the shop's data?"}
    sql = t.get("sql", "")
    if not is_safe_select(sql):
        return {"ok": False, "interpretation": t.get("interpretation", ""),
                "clarify": "I could only answer that with a read-only lookup. Try "
                           "rephrasing as a question about the data."}
    try:
        columns, rows, truncated = run_query(sql)
    except psycopg.errors.QueryCanceled:
        return {"ok": False, "interpretation": t.get("interpretation", ""), "sql": sql,
                "clarify": TIMEOUT_CLARIFY}
    return {"ok": True, "interpretation": t.get("interpretation", ""), "sql": sql,
            "columns": columns, "rows": rows, "truncated": truncated}


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
        vkey, gkey = f"pz:ask:v:{ip}:{today}", f"pz:ask:g:{today}"
        # Visitor first, so one visitor over their limit never spends the shared budget.
        v = int(_redis("INCR", vkey))
        if v == 1:
            _redis("EXPIRE", vkey, 172800)
        if v > PER_VISITOR:
            return False, (f"You've used your {PER_VISITOR} free questions for today. Want a full "
                           "walkthrough? Get in touch and I'll show you the rest.")
        g = int(_redis("INCR", gkey))
        if g == 1:
            _redis("EXPIRE", gkey, 172800)
        if g > GLOBAL:
            return False, ("This live demo has hit its daily limit; check back tomorrow, "
                           "or reach out and I'll walk you through it directly.")
        return True, ""
    except Exception as e:
        print(f"ask: rate limiter unavailable, failing open: {type(e).__name__}")
        return True, ""


class handler(BaseHTTPRequestHandler):
    def _reply(self, status, payload):
        # Serialize before sending headers (they can't be unsent); str() covers types like intervals.
        body = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._reply(400, {"ok": False, "clarify": "Invalid request."})

        question = (data.get("question") or "").strip()
        if not question:
            return self._reply(200, {"ok": False, "clarify": "Ask a question about the pizza shop."})
        if len(question) > MAX_QUESTION_CHARS:
            return self._reply(200, {"ok": False, "clarify":
                                     f"Please keep questions under {MAX_QUESTION_CHARS} characters."})

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
