"""Plain-English to SQL for Tony's Pizza (read-only). Deploy-side mirror of
~/pizza-sql/engine.py; keep the schema description + safety rules in sync with it.

Translation goes to Claude over raw HTTPS (no SDK dependency, and this exact path
is already proven in production). Reads run through the pizza_ro role, and every
generated query is validated to be a single SELECT before it runs.
"""
import json
import os
import urllib.request
from datetime import date, datetime
from decimal import Decimal

import _db

MODEL = "claude-haiku-4-5"
MAX_ROWS = 200
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

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

orders(id, customer_id, order_date, status)
    One row per order. customer_id -> customers.id. order_date is a date. status is
    one of 'Received', 'In Progress', 'Delivered', 'Cancelled'.

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


def translate(question: str) -> dict:
    """Ask Claude for SQL + a restatement, over raw HTTPS (structured output)."""
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


# Read-only safety: enforced here, not trusted to the model.
_FORBIDDEN = ("insert", "update", "delete", "drop", "alter", "create", "replace",
              "truncate", "grant", "revoke", "copy", "into", "vacuum", "begin", "commit")


def is_safe_select(sql: str) -> bool:
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


def run_query(sql: str):
    with _db.ro_connect() as conn:
        cur = conn.execute(sql)
        columns = [c.name for c in cur.description]
        rows = [{k: _clean(v) for k, v in row.items()} for row in cur.fetchmany(MAX_ROWS)]
    return columns, rows


def answer(question: str) -> dict:
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
