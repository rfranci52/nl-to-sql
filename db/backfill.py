"""One-off backfills for the live pizza data. Run by hand from a laptop; never deployed.

  retime-seed --backup old.csv     give the seeded orders the time of day gen_seed.py now
                                   writes; with --apply, first saves (id, old created_at)
  catch-up --start YYYY-MM-DD      add the nightly synthetic orders (frontend/api/restock.py)
           [--end YYYY-MM-DD]      for each day up to --end (default: yesterday in New York)

Both are dry runs (rolled back) unless --apply is given. Connects with PIZZA_RW_URL or --url.
A catch-up dry run still advances the order id sequences (rolled-back inserts leave id gaps).
"""
import argparse
import csv
import importlib.util
import os
import random
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

SHOP_TZ = "America/New_York"
GUEST_CITY = "Online"
SEED_MAX_ID = 90
RESTOCK_PY = Path(__file__).resolve().parent.parent / "frontend" / "api" / "restock.py"
OPEN_HOURS = [(11, 2), (12, 6), (13, 5), (14, 2), (15, 1), (16, 2), (17, 4), (18, 7), (19, 7), (20, 5), (21, 3), (22, 1)]


def time_of_day(rng):
    hour = rng.choices([h for h, _ in OPEN_HOURS], weights=[w for _, w in OPEN_HOURS])[0]
    return f"{hour:02d}:{rng.randrange(60):02d}:{rng.randrange(60):02d}"


def retime_seed(conn, backup, apply):
    ids = list(range(1, SEED_MAX_ID + 1))
    times = [time_of_day(random.Random(f"tonys-pizza-order-{i}")) for i in ids]
    rows = conn.execute(
        "SELECT o.id, o.created_at AS old, (o.order_date + v.t::time) AT TIME ZONE %s AS new "
        "FROM orders o JOIN customers c ON c.id = o.customer_id "
        "JOIN unnest(%s::int[], %s::text[]) AS v(id, t) ON v.id = o.id "
        "WHERE c.city <> %s ORDER BY o.id FOR UPDATE OF o",
        (SHOP_TZ, ids, times, GUEST_CITY),
    ).fetchall()
    change = [r for r in rows if r["old"] != r["new"]]
    print(f"retime-seed: {len(change)} of {len(rows)} seeded orders change")
    if not (apply and change):
        conn.rollback()
        print("dry run, nothing written" if not apply else "nothing to do")
        return
    with open(backup, "x", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "created_at"])
        w.writerows((r["id"], r["old"].isoformat()) for r in change)
    conn.execute(
        "UPDATE orders o SET created_at = v.new "
        "FROM unnest(%s::int[], %s::timestamptz[]) AS v(id, new) WHERE o.id = v.id",
        ([r["id"] for r in change], [r["new"] for r in change]),
    )
    conn.commit()
    print(f"applied; old values saved to {backup}")


def load_add_synthetic_orders():
    spec = importlib.util.spec_from_file_location("restock", RESTOCK_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.add_synthetic_orders


def catch_up(conn, start, end, apply):
    add_synthetic_orders = load_add_synthetic_orders()
    months = defaultdict(lambda: [0, 0, 0])  # days, days filled, orders
    day = start
    while day <= end:
        n = add_synthetic_orders(conn, day)
        if apply:
            conn.commit()
        else:
            conn.rollback()
        m = months[day.strftime("%Y-%m")]
        m[0] += 1
        m[1] += n > 0
        m[2] += n
        day += timedelta(days=1)
    print(f"catch-up {start} .. {end}{'' if apply else ' (dry run, rolled back)'}")
    print("month    days  filled  orders")
    for month, (days, filled, orders) in sorted(months.items()):
        print(f"{month}  {days:4d}  {filled:6d}  {orders:6d}")
    print(f"total    {sum(m[0] for m in months.values()):4d}  "
          f"{sum(m[1] for m in months.values()):6d}  {sum(m[2] for m in months.values()):6d}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=os.environ.get("PIZZA_RW_URL", ""),
                   help="pizza_rw connection URL (default: $PIZZA_RW_URL)")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("retime-seed")
    r.add_argument("--backup", required=True, type=Path, help="CSV for the old values; must not exist")
    r.add_argument("--apply", action="store_true")
    c = sub.add_parser("catch-up")
    c.add_argument("--start", required=True, type=date.fromisoformat)
    c.add_argument("--end", type=date.fromisoformat)
    c.add_argument("--apply", action="store_true")
    a = p.parse_args()
    if not a.url:
        sys.exit("no connection URL: set PIZZA_RW_URL or pass --url")
    if a.cmd == "retime-seed" and a.backup.exists():
        if a.apply:
            sys.exit(f"refusing to run: {a.backup} already exists")
        print(f"note: {a.backup} exists, so --apply would refuse")

    with psycopg.connect(a.url, row_factory=dict_row) as conn:
        conn.prepare_threshold = None
        if a.cmd == "retime-seed":
            return retime_seed(conn, a.backup, a.apply)
        yesterday = conn.execute(
            "SELECT (now() AT TIME ZONE %s)::date - 1 AS day", (SHOP_TZ,)
        ).fetchone()["day"]
        conn.rollback()
        end = a.end or yesterday
        if end > yesterday or a.start > end:
            sys.exit(f"need start <= end <= {yesterday} (yesterday in New York)")
        catch_up(conn, a.start, end, a.apply)


if __name__ == "__main__":
    main()
