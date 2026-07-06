"""Plain-English -> SQL: the risky core of Tony's Pizza.

A visitor types a question in plain English. We hand Claude a described schema and
ask for three things: an interpretation, a single read-only SELECT, and (when the
question can't be answered) a clarifying question instead of a guess.

Safety is enforced here, not trusted to the model: every generated query is checked
to be a single SELECT and run through a read-only connection (the pizza_ro role,
which physically cannot write). If the model ever emitted an INSERT/DROP/etc., it
would be rejected before it ran.

Reads run against the shared Postgres platform (the `pizza` schema on Neon).
Model: claude-haiku-4-5: cheap, fast, plenty for this schema (one-line swap).
"""

from datetime import date, datetime
from decimal import Decimal

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel

import pgdb

load_dotenv()  # ANTHROPIC_API_KEY + PIZZA_RO_URL from a gitignored .env

SHOP_NAME = "Tony's Pizza"
MODEL = "claude-haiku-4-5"
MAX_ROWS = 200  # never flood the UI, however broad the question

# Written for Claude, not for humans; every note here exists because it makes the
# generated SQL correct.
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


class Translation(BaseModel):
    """The structured contract we get back from Claude."""
    understood: bool
    interpretation: str
    sql: str
    clarifying_question: str


def translate(question: str) -> Translation:
    """Ask Claude to turn a plain-English question into SQL + a restatement."""
    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM,
        messages=[{"role": "user", "content": question}],
        output_format=Translation,
    )
    return response.parsed_output


# --- read-only safety: enforced by us, not the model ------------------------

_FORBIDDEN = (
    "insert", "update", "delete", "drop", "alter", "create", "replace",
    "truncate", "grant", "revoke", "copy", "into", "vacuum", "begin", "commit",
)


def is_safe_select(sql: str) -> bool:
    """A single SELECT (or CTE), no stacked statements, no write keywords."""
    s = sql.strip().rstrip(";").strip()
    if not s or ";" in s:
        return False
    lowered = s.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        return False
    words = set(lowered.replace("(", " ").replace(")", " ").replace(",", " ").split())
    return not any(bad in words for bad in _FORBIDDEN)


def _clean(v):
    """Make Postgres values JSON-serializable (Decimal -> float, dates -> string)."""
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", "replace")
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def run_query(sql: str):
    """Execute a vetted SELECT through the read-only pizza_ro connection."""
    with pgdb.ro_connect() as conn:
        cur = conn.execute(sql)
        columns = [c.name for c in cur.description]
        rows = [{k: _clean(v) for k, v in row.items()} for row in cur.fetchmany(MAX_ROWS)]
    return columns, rows


def answer(question: str) -> dict:
    """Full path: translate -> validate -> run. The result shape the UI needs."""
    t = translate(question)
    if not t.understood:
        return {"ok": False, "interpretation": t.interpretation,
                "clarify": t.clarifying_question}
    if not is_safe_select(t.sql):
        return {"ok": False, "interpretation": t.interpretation,
                "clarify": "I could only answer that with a read-only lookup. "
                           "Try rephrasing as a question about the data."}
    columns, rows = run_query(t.sql)
    return {"ok": True, "interpretation": t.interpretation, "sql": t.sql,
            "columns": columns, "rows": rows}


if __name__ == "__main__":
    import sys

    questions = sys.argv[1:] or [
        "which category brings in the most revenue?",
        "which item sells the most?",
        "who are our top 5 customers by how much they've spent?",
        "how many drinks do we have in stock?",
        "what's the weather in Tokyo?",  # off-topic -> should ask to clarify
    ]
    print(f"== {SHOP_NAME}: plain-English -> SQL (Postgres) ==\n")
    for q in questions:
        print(f"Q: {q}")
        res = answer(q)
        print(f"   understood: {res['ok']}")
        print(f"   reading it as: {res['interpretation']}")
        if not res["ok"]:
            print(f"   clarify: {res['clarify']}\n")
            continue
        print(f"   SQL: {res['sql']}")
        for row in res["rows"][:5]:
            print("     " + "  ".join(f"{k}={v}" for k, v in row.items()))
        print()
