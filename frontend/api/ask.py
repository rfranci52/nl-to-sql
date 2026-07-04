"""Vercel serverless: plain-English -> SQL for Tony's Pizza (read-only demo).

Self-contained and pure standard library (no requirements.txt), so it deploys the
same way your contact form does. It mirrors the local engine (~/pizza-sql/engine.py
+ db.py) — keep the schema description and safety rules in sync with those.

On cold start it rebuilds the synthetic database in /tmp from a fixed seed (so the
data is identical every time and there's no .db file to bundle), asks Claude for the
translation over HTTPS, enforces SELECT-only + read-only safety, runs the query, and
returns the rows.

Env vars (set in Vercel):
  ANTHROPIC_API_KEY            required
  UPSTASH_REDIS_REST_URL/TOKEN optional — when both are set, the per-visitor +
                               global daily rate limit turns on. Absent = no limit.
"""

import json
import os
import random
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler
from pathlib import Path

MODEL = "claude-haiku-4-5"
MAX_ROWS = 200
DB_PATH = Path("/tmp/pizza.db")  # writable tmp on Vercel; rebuilt on cold start
SHOP_NAME = "Tony's Pizza"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Rate limit (active only if both Upstash vars are present).
UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "").rstrip("/")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
PER_VISITOR_DAILY = int(os.environ.get("PER_VISITOR_DAILY", "15"))
GLOBAL_DAILY = int(os.environ.get("GLOBAL_DAILY", "500"))

# --------------------------------------------------------------------------
# Synthetic data — a faithful copy of db.py so the deployed data matches local.
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE customers (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, city TEXT NOT NULL, joined_date TEXT NOT NULL);
CREATE TABLE pizzas (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, size TEXT NOT NULL, price REAL NOT NULL);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
    order_date TEXT NOT NULL, status TEXT NOT NULL);
CREATE TABLE order_items (
    id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
    pizza_id INTEGER NOT NULL REFERENCES pizzas(id), quantity INTEGER NOT NULL);
"""
FIRST_NAMES = ["Maria", "James", "Aisha", "David", "Sofia", "Marcus", "Elena", "Tyrone",
               "Nina", "Carlos", "Grace", "Omar", "Lena", "Andre", "Priya", "Sam", "Jade",
               "Victor", "Rosa", "Kwame", "Hana", "Leo", "Fatima", "Dominic"]
LAST_NAMES = ["Rivera", "Chen", "Okafor", "Nguyen", "Patel", "Rossi", "Silva", "Kim",
              "Johnson", "Diaz", "Ali", "Brooks", "Cohen", "Santos", "Reyes", "Walsh"]
CITIES = ["Brooklyn", "Queens", "Bronx", "Manhattan", "Newark", "Jersey City", "Yonkers", "Hoboken"]
MENU = {
    "Margherita": 13.0, "Pepperoni": 14.0, "Veggie Supreme": 15.0, "Meat Lovers": 17.0,
    "Hawaiian": 14.5, "BBQ Chicken": 16.0, "White Pie": 15.5, "Buffalo Chicken": 16.5,
}
SIZES = [("Small", 0.8), ("Medium", 1.0), ("Large", 1.3)]
STATUSES = ["Delivered", "Delivered", "Delivered", "Delivered", "In Progress", "Cancelled"]


def build_db() -> None:
    """Rebuild the synthetic DB in /tmp (once per cold start). Deterministic."""
    if DB_PATH.exists():
        return
    random.seed(7)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    today = date.today()

    customers, used = [], set()
    for i in range(1, 25):
        while True:
            name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
            if name not in used:
                used.add(name)
                break
        joined = today - timedelta(days=random.randint(30, 720))
        customers.append((i, name, random.choice(CITIES), joined.isoformat()))
    conn.executemany("INSERT INTO customers VALUES (?,?,?,?)", customers)

    pizzas, pid = [], 1
    for pname, base in MENU.items():
        for sname, mult in SIZES:
            pizzas.append((pid, pname, sname, round(base * mult, 2)))
            pid += 1
    conn.executemany("INSERT INTO pizzas VALUES (?,?,?,?)", pizzas)
    pizza_ids = [p[0] for p in pizzas]

    orders, items, item_id = [], [], 1
    for oid in range(1, 91):
        odate = today - timedelta(days=random.randint(0, 90))
        orders.append((oid, random.randint(1, 24), odate.isoformat(), random.choice(STATUSES)))
        for _ in range(random.randint(1, 3)):
            items.append((item_id, oid, random.choice(pizza_ids), random.randint(1, 2)))
            item_id += 1
    conn.executemany("INSERT INTO orders VALUES (?,?,?,?)", orders)
    conn.executemany("INSERT INTO order_items VALUES (?,?,?,?)", items)
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------
# Translation — mirror of engine.py's schema description + system prompt.
# --------------------------------------------------------------------------
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
they asked — this is shown back to the visitor.
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


# --------------------------------------------------------------------------
# Read-only safety — enforced here, not trusted to the model.
# --------------------------------------------------------------------------
_FORBIDDEN = ("insert", "update", "delete", "drop", "alter", "create", "replace",
              "attach", "detach", "pragma", "vacuum", "begin", "commit", "grant")


def is_safe_select(sql: str) -> bool:
    s = sql.strip().rstrip(";").strip()
    if not s or ";" in s:
        return False
    lowered = s.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        return False
    words = set(lowered.replace("(", " ").replace(")", " ").replace(",", " ").split())
    return not any(bad in words for bad in _FORBIDDEN)


def run_query(sql: str):
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
    t = translate(question)
    if not t.get("understood"):
        return {"ok": False, "interpretation": t.get("interpretation", ""),
                "clarify": t.get("clarifying_question")
                or "Could you rephrase that as a question about the shop's data?"}
    sql = t.get("sql", "")
    if not is_safe_select(sql):
        return {"ok": False, "interpretation": t.get("interpretation", ""),
                "clarify": "I could only answer that with a read-only lookup — try "
                           "rephrasing as a question about the data."}
    columns, rows = run_query(sql)
    return {"ok": True, "interpretation": t.get("interpretation", ""), "sql": sql,
            "columns": columns, "rows": rows}


# --------------------------------------------------------------------------
# Rate limit — per-visitor + global daily, via Upstash Redis REST. Fails OPEN.
# --------------------------------------------------------------------------
def _redis(*parts) -> object:
    url = UPSTASH_URL + "/" + "/".join(urllib.parse.quote(str(p), safe="") for p in parts)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read()).get("result")


def check_limit(ip: str):
    """(allowed, message). No-op (allow) unless Upstash is configured."""
    if not (UPSTASH_URL and UPSTASH_TOKEN):
        return True, ""
    try:
        today = date.today().isoformat()
        gkey, vkey = f"pz:global:{today}", f"pz:ip:{ip}:{today}"
        g = _redis("INCR", gkey); _redis("EXPIRE", gkey, 172800)
        v = _redis("INCR", vkey); _redis("EXPIRE", vkey, 172800)
        if g and int(g) > GLOBAL_DAILY:
            return False, ("This live demo has hit its daily limit — check back tomorrow, "
                           "or reach out and I'll walk you through it directly.")
        if v and int(v) > PER_VISITOR_DAILY:
            return False, (f"You've used your {PER_VISITOR_DAILY} free questions. Want to see "
                           "more? Get in touch and I'll unlock a full walkthrough.")
        return True, ""
    except Exception:
        return True, ""  # never break the demo because the limiter had a hiccup


# --------------------------------------------------------------------------
# Vercel entry point.
# --------------------------------------------------------------------------
class handler(BaseHTTPRequestHandler):
    def _reply(self, status: int, payload: dict) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def do_POST(self) -> None:
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
            build_db()
            return self._reply(200, answer(question))
        except Exception:
            import traceback
            traceback.print_exc()
            return self._reply(200, {"ok": False,
                                     "clarify": "Something went wrong answering that — please try again."})


if __name__ == "__main__":
    # Local smoke test: builds /tmp/pizza.db and runs a few questions over HTTPS.
    build_db()
    for q in [
        "who are our top 5 customers by how much they've spent?",
        "what's the weather in Tokyo?",
    ]:
        print(f"Q: {q}")
        print("  ", json.dumps(answer(q))[:320], "\n")
