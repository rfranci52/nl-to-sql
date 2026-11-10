"""Vercel serverless: place an order (write path, pizza_rw role).

Self-contained (stdlib + psycopg only). Validates and merges items, decrements stock
with a guarded update, tops up any ordered item left at or under 20 percent of its baseline,
reuses a named guest's customer row (find-or-create) until that name reaches a spend
ceiling, and records the order in one atomic transaction. Rate limited via Upstash
(fails open: orders cost nothing and the order and name caps still apply).
"""
import hashlib
import json
import os
import re
import unicodedata
import urllib.parse
import urllib.request
from datetime import date
from http.server import BaseHTTPRequestHandler

import psycopg
from psycopg.rows import dict_row

RW_URL = os.environ.get("PIZZA_RW_URL", "")
MAX_QTY = 20
MAX_LINES = 50
MAX_NAME_LEN = 30
MAX_ORDER_TOTAL = 200
# Below the seeded fifth-best customer, so a name typed on the site can't reach a top-five list.
MAX_NAME_SPEND = 250
MAX_BODY_BYTES = 16 * 1024
# Browsers attach Origin to every POST. Scripts can forge it, which is why the daily limits exist too.
ALLOWED_ORIGINS = {"https://demo.rakimfrancis.com", "https://nl-to-sql-eight.vercel.app"}
GUEST_CITY = "Online"
SHOP_TZ = "America/New_York"
_NAME_RULE = re.compile(r"[A-Za-z0-9 .'-]{1,30}")
_LETTERS = re.compile(r"[a-z]+")
_REPEATS = re.compile(r"(.)\1+")
_LONG_REPEATS = re.compile(r"(.)\1{2,}")
# Digits that stand in for letters. 1 reads as either i or l.
_DIGIT_SWAPS = ({}, str.maketrans("013456789", "oieasgtbg"), str.maketrans("013456789", "oleasgtbg"))
# Web addresses and phone numbers, the shapes an advert needs, plus common spam words.
_AD_SHAPE = re.compile(r"[a-z0-9]\.[a-z]{2}|\b(?:www|https?)\b|\bdot (?:com|net|org|io)\b|(?:\d[ .-]*){7}")
_AD_WORDS = ("bitcoin", "casino", "coupon", "crypto", "discount", "onlyfans", "promo", "telegram", "viagra", "whatsapp")
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

UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "").rstrip("/")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
PER_VISITOR = int(os.environ.get("ORDER_PER_VISITOR_DAILY", "10"))
GLOBAL = int(os.environ.get("ORDER_GLOBAL_DAILY", "1000"))


class OutOfStock(Exception):
    def __init__(self, item):
        self.item = item


def _has_blocked_word(low):
    """True if any run of 3+ letters hashes to a blocked word, once digit swaps and repeats are undone."""
    grams = set()
    for swaps in _DIGIT_SWAPS:
        letters = "".join(_LETTERS.findall(low.translate(swaps)))
        forms = {letters, _REPEATS.sub(r"\1", letters), _LONG_REPEATS.sub(r"\1\1", letters)}
        forms.update(letters[:m.start()] + m[1] + letters[m.end():] for m in _REPEATS.finditer(letters))
        grams.update(f[i:j] for f in forms for i in range(len(f)) for j in range(i + 3, len(f) + 1))
    return any(hashlib.sha256(g.encode()).hexdigest() in _BLOCKED for g in grams)


def clean_name(raw):
    # bound the input first: NFKC can expand one character into many
    name = unicodedata.normalize("NFKC", raw[:200] if isinstance(raw, str) else "")
    name = "".join(c for c in name if not unicodedata.category(c).startswith("C"))
    # accented Latin folds to its base letter and a phone's curly apostrophe to a straight one;
    # any other non-ASCII letter, including Cyrillic and Greek lookalikes, fails the rule below
    name = "".join(c for c in unicodedata.normalize("NFKD", name.replace("\u2019", "'"))
                   if not unicodedata.combining(c))
    name = " ".join(name.split())[:MAX_NAME_LEN].strip()
    low = name.lower()
    if (not _NAME_RULE.fullmatch(name) or not any(c.isalnum() for c in name) or low == "guest"
            or _AD_SHAPE.search(low) or any(w in low for w in _AD_WORDS) or _has_blocked_word(low)):
        return "Guest"
    return name


def name_spend(conn, name):
    """What this web name has spent so far. The lock lasts until commit, so simultaneous orders add up."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"tonys-pizza-name-{name.lower()}",))
    return conn.execute(
        "SELECT COALESCE(SUM(oi.quantity * m.price), 0)::float AS spent "
        "FROM customers c JOIN orders o ON o.customer_id = c.id "
        "JOIN order_items oi ON oi.order_id = o.id JOIN menu_items m ON m.id = oi.menu_item_id "
        "WHERE c.city = %s AND lower(c.name) = lower(%s) AND o.status <> 'Cancelled'",
        (GUEST_CITY, name),
    ).fetchone()["spent"]


def place_order(items, name=None):
    items = items or []
    if not isinstance(items, list):
        return {"ok": False, "error": "That order had an invalid item."}
    if len(items) > MAX_LINES:
        return {"ok": False, "error": "That order has too many lines."}
    merged = {}
    for it in items:
        # type() rather than isinstance(): JSON true and false would otherwise count as integers
        if not isinstance(it, dict) or type(it.get("menu_item_id")) is not int or type(it.get("quantity")) is not int:
            return {"ok": False, "error": "That order had an invalid item."}
        mid, qty = it["menu_item_id"], it["quantity"]
        if not (1 <= qty <= MAX_QTY):
            return {"ok": False, "error": f"Quantity must be between 1 and {MAX_QTY}."}
        merged[mid] = merged.get(mid, 0) + qty
    if not merged:
        return {"ok": False, "error": "Your order is empty."}
    if any(qty > MAX_QTY for qty in merged.values()):
        return {"ok": False, "error": f"Quantity must be between 1 and {MAX_QTY}."}
    # id order keeps concurrent orders locking inventory rows in the same sequence (no deadlocks)
    cleaned = sorted(merged.items())
    ids = [mid for mid, _q in cleaned]

    customer = clean_name(name)
    try:
        with psycopg.connect(RW_URL, row_factory=dict_row) as conn:
            conn.prepare_threshold = None
            conn.execute(f"SET LOCAL TIME ZONE '{SHOP_TZ}'")
            menu = {r["id"]: r for r in conn.execute(
                "SELECT id, name, price::float AS price FROM menu_items WHERE id = ANY(%s)",
                (ids,),
            ).fetchall()}
            if len(menu) != len(ids):
                raise ValueError("That order had an item that isn't on the menu.")
            order_total = round(sum(menu[mid]["price"] * qty for mid, qty in cleaned), 2)
            if order_total > MAX_ORDER_TOTAL:
                raise ValueError(f"Demo orders are capped at ${MAX_ORDER_TOTAL}. Try fewer items.")
            stock = {}
            for mid, qty in cleaned:
                got = conn.execute(
                    "UPDATE inventory SET quantity = quantity - %s "
                    "WHERE menu_item_id = %s AND quantity >= %s RETURNING quantity",
                    (qty, mid, qty),
                ).fetchone()
                if got is None:
                    raise OutOfStock(menu[mid]["name"])
                stock[mid] = got["quantity"]
            topped_up = conn.execute(
                "UPDATE inventory i SET quantity = m.baseline_stock FROM menu_items m "
                "WHERE m.id = i.menu_item_id AND i.menu_item_id = ANY(%s) "
                "AND i.quantity <= m.baseline_stock * 0.2 RETURNING i.menu_item_id, i.quantity, m.name",
                (ids,),
            ).fetchall()
            stock.update({r["menu_item_id"]: r["quantity"] for r in topped_up})
            restocked = [r["name"] for r in topped_up]
            # past the ceiling a name stops adding to its row; the order still goes through as a Guest
            if customer != "Guest" and name_spend(conn, customer) + order_total > MAX_NAME_SPEND:
                customer = "Guest"
            # an anonymous Guest always gets its own row; named guests are matched case-insensitively
            cust = None if customer == "Guest" else conn.execute(
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
            "lines": lines, "total": round(sum(li["subtotal"] for li in lines), 2),
            "restocked": restocked, "stock": stock}


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
        vkey, gkey = f"pz:order:v:{ip}:{today}", f"pz:order:g:{today}"
        v = int(_redis("INCR", vkey))
        if v == 1:
            _redis("EXPIRE", vkey, 172800)
        if v > PER_VISITOR:
            return False, "You've placed a lot of demo orders today. Take a break and come back tomorrow."
        g = int(_redis("INCR", gkey))
        if g == 1:
            _redis("EXPIRE", gkey, 172800)
        if g > GLOBAL:
            return False, "This live demo has taken a lot of orders today; check back tomorrow."
        return True, ""
    except Exception as e:
        print(f"order: rate limiter unavailable, failing open: {type(e).__name__}")
        return True, ""


class handler(BaseHTTPRequestHandler):
    def send_error(self, code, message=None, explain=None):
        # A short JSON reply in place of the standard library's HTML error page. A method with
        # no handler arrives here as 501; for a POST-only endpoint the right answer is 405.
        if code == 501:
            code = 405
        body = json.dumps({"error": "method not allowed" if code == 405 else "request refused"}).encode()
        self.close_connection = True
        self.send_response(code)
        if code == 405:
            self.send_header("Allow", "POST")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if getattr(self, "command", None) != "HEAD":
            self.wfile.write(body)

    def _reply(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def do_POST(self):
        refused = {"ok": False, "error": "Invalid request."}
        if self.headers.get("origin") not in ALLOWED_ORIGINS:
            return self._reply(403, refused)
        if (self.headers.get("content-type") or "").split(";")[0].strip().lower() != "application/json":
            return self._reply(415, refused)
        try:
            length = int(self.headers.get("content-length") or 0)
        except ValueError:
            return self._reply(400, refused)
        if not 0 <= length <= MAX_BODY_BYTES:
            return self._reply(413, refused)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._reply(400, refused)
        if not isinstance(data, dict) or not isinstance(data.get("items", []), list) \
                or not isinstance(data.get("name", ""), (str, type(None))):
            return self._reply(400, refused)
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
