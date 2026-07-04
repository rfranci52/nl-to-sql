"""Plain-English → SQL — the risky core of Tony's Pizza.

A visitor types a question in plain English. We hand Claude a *described* schema
(tables, columns, relationships, and the notes it needs to write correct SQL) and
ask for three things back:

  • interpretation        — a plain-English restatement of what we think you asked
  • sql                   — a single read-only SELECT that answers it
  • understood / clarify  — when the question can't be answered from this schema,
                            we return a clarifying question instead of a guess

Safety is enforced here, not trusted to the model: every generated query is
checked to be a single SELECT and run against a read-only connection. If the
model ever emitted an INSERT/DROP/etc., it would be rejected before it ran.

Model: claude-haiku-4-5 — cheap and fast, and plenty for a small schema. It's a
one-line swap (MODEL) if you ever want more muscle.
"""

import sqlite3

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel

from db import DB_PATH, SHOP_NAME

load_dotenv()  # read ANTHROPIC_API_KEY from a local .env — gitignored, never shipped

MODEL = "claude-haiku-4-5"
MAX_ROWS = 200  # never flood the UI, however broad the question

# What the model is allowed to reason about. Written for Claude, not for humans —
# every note here exists because it makes the generated SQL correct.
SCHEMA_DESCRIPTION = """\
SQLite database for a pizza shop. Tables and columns:

customers(id, name, city, joined_date)
    One row per customer. joined_date is an ISO date string 'YYYY-MM-DD'.

pizzas(id, name, size, price)
    The menu. Each pizza name exists in three rows — size is 'Small', 'Medium',
    or 'Large' — each with its own price. So "the Pepperoni" is 3 rows.

orders(id, customer_id, order_date, status)
    One row per order. customer_id -> customers.id. order_date is 'YYYY-MM-DD'.
    status is exactly one of: 'Delivered', 'In Progress', 'Cancelled'.

order_items(id, order_id, pizza_id, quantity)
    Line items. order_id -> orders.id, pizza_id -> pizzas.id. An order has one
    or more line items.

Notes for writing correct SQL:
- Revenue / spend / how much = SUM(order_items.quantity * pizzas.price), so you
  must join order_items -> pizzas to get price, and -> orders for the customer
  or the date.
- "Sold" / "popular" / "how many" of a pizza = SUM(order_items.quantity).
- To count real sales, filter to status = 'Delivered' unless the user clearly
  wants all orders (e.g. "including cancelled").
- Dates: compare with the ISO string, or use SQLite date functions like
  date('now', '-30 days').
- Use SQLite syntax only.
"""

SYSTEM = f"""You translate a customer's plain-English question into ONE SQLite \
SELECT query against this exact schema.

{SCHEMA_DESCRIPTION}

Rules:
- Return a single read-only SELECT (a WITH ... SELECT common table expression is \
fine). Never write to the database.
- Use only the tables and columns above. Never invent a column.
- Always fill "interpretation" with a plain-English restatement of what you think \
they asked — this is shown back to the visitor to confirm before running.
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
    "attach", "detach", "pragma", "vacuum", "begin", "commit", "grant",
)


def is_safe_select(sql: str) -> bool:
    """A single SELECT (or CTE), no stacked statements, no write keywords."""
    s = sql.strip().rstrip(";").strip()
    if not s:
        return False
    if ";" in s:  # a second statement snuck in after the first
        return False
    lowered = s.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        return False
    words = set(lowered.replace("(", " ").replace(")", " ").replace(",", " ").split())
    return not any(bad in words for bad in _FORBIDDEN)


def run_query(sql: str):
    """Execute a vetted SELECT against a read-only connection to pizza.db."""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description]
        rows = [dict(r) for r in cur.fetchmany(MAX_ROWS)]
        return columns, rows
    finally:
        conn.close()


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
        "who are our top 5 customers by how much they've spent?",
        "which pizza sells the most?",
        "how many orders came from Brooklyn last month?",
        "what's the weather in Tokyo?",  # off-topic -> should ask to clarify
    ]
    print(f"== {SHOP_NAME}: plain-English -> SQL ==\n")
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
