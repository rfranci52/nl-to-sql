"""Placing orders + the nightly restock, the write path (role: pizza_rw).

Public writes are deliberately constrained: the order is dropdown-driven (only
real menu-item ids and bounded quantities reach here), and the one free-text
field, an optional name, is saferailed. Inventory decrements are guarded so stock
can never go negative, even under concurrent orders, any ordered item left at or
under 20 percent of its baseline is topped back up, and the whole order is one atomic
transaction (any failure rolls the stock back too).
"""

import hashlib
import re
import unicodedata

import pgdb

MAX_QTY = 20          # per merged line item
MAX_LINES = 50
MAX_NAME_LEN = 30
GUEST_CITY = "Online"
SHOP_TZ = "America/New_York"

_LETTERS = re.compile(r"[a-z]+")
# hashed so the public repo doesn't spell them out
_BLOCKED = frozenset("""
06a1d8bf7fc210de23d9540ab35744ae9db310afa0afbabe9529333e175a850f
08a841e996781e9e77d30a4e4420a8f501a280b00624e6d1224bf54aaff73eba
0f28c4960d96647e77e7ab6d13b85bd16c7ca56f45df802cdc763a5e5c0c7863
120f6e5b4ea32f65bda68452fcfaaef06b0136e1d0e4a6f60bc3771fa0936dd6
12e6274e4309293e2d480272b49a6c7c73a6a6b22678ba226b533c67006c17d1
158869a97379229b7681efae9d7f9c9214134e836d649ba53477c0c111414d59
16ea09fc78ca83ca502cbcf2377acdf280bf18f61e259153f0868405eedab5ef
17bde8b4064612e266eaa1b5b1dfa054d1e6dd263f66b392159006d2c9a8ae8a
22fc75e65a0e9d34324092a7c6a8dba961853294abca4e5914e60c550f48e0c2
2a6937d721b14cdd7331c1ad1caf6a3cfa37a266650807101d44cdcd0acb132f
2b3240970c71346b98e00c5c83e8070484e9a1ac3d04d4488dedc85f454e6180
2f5f6ce5ae30b54aa5d7ced1ba566982bab34ba2814a51ce1865d2c2d8815cd4
31506a8448a761a448a08aa69d9116ea8a6cb1c6b3f4244b3043051f69c9cc3c
3314b0999a53aafeaaf0c0881eb2032a08c6dedcf9924c7a3da3cb1465ffac41
333f7618092958c75b8c5af6f1ec77b42803922a0fc6ff1570a8af3a3aab3b4a
3b1e0d7c5dd45583867e897943e37a940a7e7321022317dd3deea01963ee365e
566f532d486c947709d3d0e6b7575af8380248db66dada211d58eb00ad585297
594810adcbee20bd1d6f8e4cee51efdf1a235691e9d08fe96ce944d7fde34fd6
59cc3cb8a42973019cddacdee59e0aa7746ba6c203af78cefb453cb3a1b6610d
5b3ae48be122f7ed19b4cc587649f41f9d2565df51cfa332f8e7806f4ebb9032
6556c1a58444e10f074e59b510fc392d61dd39d04227671272a61617dcecc094
6ac3c336e4094835293a3fed8a4b5fedde1b5e2626d9838fed50693bba00af0e
796e43a5a8cdb73b92b5f59eb50610cea3efa8ce229cd7f0557983091b2b4552
83621b34ec5955e56c07c5b2ed2c87ed8050c3884cd89a1527d06579711e8906
85fc17f7069acd39a5c636cd0a6530651096128da447959f5e250824857dc559
886d51e97ad7931d0d2af8439ca6d9e4887e3c2b469ed247cbd68ceb3649ccde
8c5c04391361cbf4afd74c5ed8101ea4af881c4ee3b3df1d5b3716a19b1a834d
8f5083e3e5c7dc8932f2bf58212f963f3a44752618c96297f82623f736c52738
98b52c4b6b7d1f48e7477a5ccc10955dd195d0ac5a38c8281bfeb08762634909
9915ba2d822280f22c283df4e76584a40e0119fc58f73c5f84d4fdb04d04fa6f
9ae315a94e428a7ee3b5e48adae6541965d93b86acf10ffa1c45b93b6fe577b4
a76d588d0047e3b043406060ba6170e2313f46f15a084d680464f881f1efeeb9
ad505b0be8a49b89273e307106fa42133cbd804456724c5e7635bd953215d92a
b04a8a40063584477f690200cb4262563f36068984ab4f53a51f26bf8e120215
bb0689e399cca9d90283112d73aa6c899953c4c3cbd39a3c087b63de39f304aa
bb61ef40814ce34c1edf0edb609854be9793198a8f60b67d9fb26643c32281d3
bcd1642bca163da3c9d2903abf3663a79fe3295dd31a8e5b12705c6bd62611db
bd331fb1d24298f52943034a243a341877957b895f4372b11babb87262904ed6
bdb1cc258d6976aafd3a0c3399dfff42a486e63a6948d8bc2e10e7c36fab7611
c2c3b68b48832afd9a4dbdd474c1b6c81c8baecdb71446f9947dac72dd0fe93d
c3de533e9b7fe63b79f648687a30d2861edd92fe7c3cd1f2c485e0a605367624
cc02032349c833ac5e97bac094560ed40e09acf34cb1978ab7a9840b9bf15b4d
d75a838dc758ba17f28bd8dbac605cb70c35465263d5733164521de2f7ef7926
dd92623b0a4b255f87cc4aaee7990ee182d91db49189df6229ce65b5e9d960da
e512a05583448f44790783f986b1f36925c8cfc42338ca0e1caa637755bd15ae
e7b98c6aa5b944e0b315d350d423f895ac9e44fb84f1534b18c2572370a67b9e
eef3bd091670c3447022d619c06ad15de96da72b5a66f28bb8b75d1b1c12a05f
f50c51ed2315dcf3fa88181cf033f8029cac64f7dea4048327ca032ec102ea74
f9d0d9b18ae9033a5ea36df19bf279b059e887a9ae785db81117bceaecc95933
fd70ad909b94deb27b460692084d9f2b1dbc9df3c6bcfd3caee571e707031e3f
""".split())


class OutOfStock(Exception):
    def __init__(self, item: str):
        self.item = item


def clean_name(raw: str | None) -> str:
    """NFKC, drop control/invisible/format characters and angle brackets, collapse
    whitespace, cap the length; empty or blocked names become "Guest"."""
    # bound the input first: NFKC can expand one character into many
    name = unicodedata.normalize("NFKC", raw[:200] if isinstance(raw, str) else "")
    name = "".join(c for c in name if not unicodedata.category(c).startswith("C") and c not in "<>")
    name = " ".join(name.split())[:MAX_NAME_LEN].strip()
    # the blocked-word check ignores accents, so one accented letter can't slip a word through
    low = "".join(c for c in unicodedata.normalize("NFKD", name.lower()) if not unicodedata.combining(c))
    words = _LETTERS.findall(low) + ["".join(_LETTERS.findall(low))]
    visible = any(unicodedata.category(c)[0] in "LN" and c not in "\u115f\u1160\u3164\uffa0" for c in name)
    if not visible or low == "guest" or any(hashlib.sha256(w.encode()).hexdigest() in _BLOCKED for w in words):
        return "Guest"
    return name


def get_menu() -> list[dict]:
    """The menu + current stock, for the ordering dropdowns."""
    with pgdb.ro_connect() as conn:
        return conn.execute(
            "SELECT m.id, m.name, m.category, m.price::float AS price, "
            "       COALESCE(i.quantity, 0) AS in_stock "
            "FROM menu_items m LEFT JOIN inventory i ON i.menu_item_id = m.id "
            "ORDER BY m.category, m.id"
        ).fetchall()


def place_order(items: list[dict], name: str | None = None) -> dict:
    """items: [{'menu_item_id': int, 'quantity': int}, ...]. Returns a summary."""
    items = items or []
    if not isinstance(items, list):
        return {"ok": False, "error": "That order had an invalid item."}
    if len(items) > MAX_LINES:
        return {"ok": False, "error": "That order has too many lines."}
    merged: dict[int, int] = {}
    for it in items:
        try:
            mid, qty = int(it["menu_item_id"]), int(it["quantity"])
        except (KeyError, TypeError, ValueError):
            return {"ok": False, "error": "That order had an invalid item."}
        if not (1 <= qty <= MAX_QTY):
            return {"ok": False, "error": f"Quantity must be between 1 and {MAX_QTY}."}
        merged[mid] = merged.get(mid, 0) + qty
    if not merged:
        return {"ok": False, "error": "Your order is empty."}
    if any(qty > MAX_QTY for qty in merged.values()):
        return {"ok": False, "error": f"Quantity must be between 1 and {MAX_QTY}."}
    # id order keeps concurrent orders locking inventory rows in the same sequence (no deadlocks)
    cleaned = sorted(merged.items())
    ids = [mid for mid, _ in cleaned]

    customer = clean_name(name)
    try:
        with pgdb.rw_connect() as conn:
            # order_date / joined_date default to CURRENT_DATE: make that the shop's date
            conn.execute(f"SET LOCAL TIME ZONE '{SHOP_TZ}'")
            menu = {r["id"]: r for r in conn.execute(
                "SELECT id, name, price::float AS price FROM menu_items WHERE id = ANY(%s)",
                (ids,),
            ).fetchall()}
            if len(menu) != len(ids):
                raise ValueError("That order had an item that isn't on the menu.")
            # guarded decrement, returns no row if there isn't enough stock
            for mid, qty in cleaned:
                got = conn.execute(
                    "UPDATE inventory SET quantity = quantity - %s "
                    "WHERE menu_item_id = %s AND quantity >= %s RETURNING quantity",
                    (qty, mid, qty),
                ).fetchone()
                if got is None:
                    raise OutOfStock(menu[mid]["name"])
            # top up anything this order left at or under 20 percent, so a few big orders can't empty the shop
            restocked = [r["name"] for r in conn.execute(
                "UPDATE inventory i SET quantity = m.baseline_stock FROM menu_items m "
                "WHERE m.id = i.menu_item_id AND i.menu_item_id = ANY(%s) "
                "AND i.quantity <= m.baseline_stock * 0.2 RETURNING m.name",
                (ids,),
            ).fetchall()]
            # One customer per guest name: reuse an existing guest (matched
            # case-insensitively) so repeat visitors don't pile up duplicate
            # rows. An anonymous "Guest" always gets its own new row.
            cust = None if customer == "Guest" else conn.execute(
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
            "lines": lines, "total": round(sum(li["subtotal"] for li in lines), 2),
            "restocked": restocked}


def restock() -> int:
    """Refill every item's stock to its baseline. Sales are left untouched."""
    with pgdb.rw_connect() as conn:
        cur = conn.execute(
            "UPDATE inventory SET quantity = m.baseline_stock "
            "FROM menu_items m WHERE m.id = inventory.menu_item_id"
        )
        return cur.rowcount


if __name__ == "__main__":
    print("menu (first 3):")
    for row in get_menu()[:3]:
        print("  ", row)
    print("\nplace order:", place_order(
        [{"menu_item_id": 1, "quantity": 2}, {"menu_item_id": 15, "quantity": 3}], "Test Guest"))
