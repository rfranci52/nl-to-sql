"""Placing orders + the nightly restock, the write path (role: pizza_rw).
Deploy-side mirror of ~/pizza-sql/orders.py; keep in sync with it.

Public writes are constrained: dropdown-driven items, bounded quantities, the one
free-text field (an optional name) sanitized, guarded inventory decrements that
cannot go negative, find-or-create so repeat guests reuse one customer, and the
whole order in a single atomic transaction.
"""
import re

import _db

MAX_QTY = 20          # per line item
MAX_NAME_LEN = 30
GUEST_CITY = "Online"

_UNSAFE = re.compile(r"[\x00-\x1f\x7f<>]")


class OutOfStock(Exception):
    def __init__(self, item: str):
        self.item = item


def clean_name(raw):
    name = _UNSAFE.sub("", (raw or "").strip())[:MAX_NAME_LEN].strip()
    return name or "Guest"


def get_menu():
    """The menu + current stock, for the ordering dropdowns."""
    with _db.ro_connect() as conn:
        return conn.execute(
            "SELECT m.id, m.name, m.category, m.price::float AS price, "
            "       COALESCE(i.quantity, 0) AS in_stock "
            "FROM menu_items m LEFT JOIN inventory i ON i.menu_item_id = m.id "
            "ORDER BY m.category, m.id"
        ).fetchall()


def place_order(items, name=None):
    """items: [{'menu_item_id': int, 'quantity': int}, ...]. Returns a summary."""
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
        with _db.rw_connect() as conn:
            menu = {}
            for mid, _ in cleaned:
                row = conn.execute(
                    "SELECT name, price::float AS price FROM menu_items WHERE id = %s",
                    (mid,),
                ).fetchone()
                if row is None:
                    raise ValueError("That order had an item that isn't on the menu.")
                menu[mid] = row
            # guarded decrement, returns no row if there isn't enough stock
            for mid, qty in cleaned:
                got = conn.execute(
                    "UPDATE inventory SET quantity = quantity - %s "
                    "WHERE menu_item_id = %s AND quantity >= %s RETURNING quantity",
                    (qty, mid, qty),
                ).fetchone()
                if got is None:
                    raise OutOfStock(menu[mid]["name"])
            # One customer per guest name: reuse an existing guest (case-insensitive)
            # so repeat visitors don't pile up duplicate rows.
            cust = conn.execute(
                "SELECT id FROM customers "
                "WHERE city = %s AND lower(name) = lower(%s) "
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
            # commits on clean exit of the connection context; rolls back on any raise
    except OutOfStock as e:
        return {"ok": False, "error": f"Sorry, we just ran out of {e.item}. Try fewer, or something else."}
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    lines = [{"name": menu[mid]["name"], "quantity": qty, "price": menu[mid]["price"],
              "subtotal": round(menu[mid]["price"] * qty, 2)} for mid, qty in cleaned]
    return {"ok": True, "order_id": order["id"], "customer": customer,
            "lines": lines, "total": round(sum(li["subtotal"] for li in lines), 2)}


def restock():
    """Refill every item's stock to its baseline. Sales are left untouched."""
    with _db.rw_connect() as conn:
        cur = conn.execute(
            "UPDATE inventory SET quantity = m.baseline_stock "
            "FROM menu_items m WHERE m.id = inventory.menu_item_id"
        )
        return cur.rowcount
