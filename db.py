"""Tony's Pizza — synthetic database for the plain-English → SQL demo.

A small, relatable pizza-shop schema (customers, menu, orders, line items) with
sensible fake data, so a visitor can ask questions in plain English and see them
answered against a live database. Everything here is synthetic.
"""

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "pizza.db"
SHOP_NAME = "Tony's Pizza"  # display name only — one-line swap (e.g. "Rakim's Pizza")

SCHEMA = """
CREATE TABLE customers (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    city        TEXT NOT NULL,
    joined_date TEXT NOT NULL
);
CREATE TABLE pizzas (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL,
    size  TEXT NOT NULL,
    price REAL NOT NULL
);
CREATE TABLE orders (
    id          INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    order_date  TEXT NOT NULL,
    status      TEXT NOT NULL
);
CREATE TABLE order_items (
    id       INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    pizza_id INTEGER NOT NULL REFERENCES pizzas(id),
    quantity INTEGER NOT NULL
);
"""

FIRST_NAMES = ["Maria", "James", "Aisha", "David", "Sofia", "Marcus", "Elena", "Tyrone",
               "Nina", "Carlos", "Grace", "Omar", "Lena", "Andre", "Priya", "Sam", "Jade",
               "Victor", "Rosa", "Kwame", "Hana", "Leo", "Fatima", "Dominic"]
LAST_NAMES = ["Rivera", "Chen", "Okafor", "Nguyen", "Patel", "Rossi", "Silva", "Kim",
              "Johnson", "Diaz", "Ali", "Brooks", "Cohen", "Santos", "Reyes", "Walsh"]
CITIES = ["Brooklyn", "Queens", "Bronx", "Manhattan", "Newark", "Jersey City", "Yonkers", "Hoboken"]

# pizza name -> Medium price; Small = -20%, Large = +30%
MENU = {
    "Margherita": 13.0, "Pepperoni": 14.0, "Veggie Supreme": 15.0, "Meat Lovers": 17.0,
    "Hawaiian": 14.5, "BBQ Chicken": 16.0, "White Pie": 15.5, "Buffalo Chicken": 16.5,
}
SIZES = [("Small", 0.8), ("Medium", 1.0), ("Large", 1.3)]
STATUSES = ["Delivered", "Delivered", "Delivered", "Delivered", "In Progress", "Cancelled"]


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def seed(force: bool = False) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists() and not force:
        return
    if DB_PATH.exists():
        DB_PATH.unlink()
    random.seed(7)
    conn = connect()
    conn.executescript(SCHEMA)
    today = date.today()

    # customers
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

    # menu (each pizza name in three sizes)
    pizzas, pid = [], 1
    for pname, base in MENU.items():
        for sname, mult in SIZES:
            pizzas.append((pid, pname, sname, round(base * mult, 2)))
            pid += 1
    conn.executemany("INSERT INTO pizzas VALUES (?,?,?,?)", pizzas)
    pizza_ids = [p[0] for p in pizzas]

    # orders + line items
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


if __name__ == "__main__":
    seed(force=True)
    conn = connect()
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("customers", "pizzas", "orders", "order_items")}
    print(f"seeded {SHOP_NAME}: {counts}")
    print("\nsample — top 5 customers by total spend (delivered orders):")
    rows = conn.execute("""
        SELECT c.name, ROUND(SUM(oi.quantity * p.price), 2) AS spent
        FROM order_items oi
        JOIN orders o    ON o.id = oi.order_id
        JOIN pizzas p    ON p.id = oi.pizza_id
        JOIN customers c ON c.id = o.customer_id
        WHERE o.status = 'Delivered'
        GROUP BY c.id ORDER BY spent DESC LIMIT 5
    """).fetchall()
    for r in rows:
        print(f"  {r['name']:<22} ${r['spent']}")
    conn.close()
