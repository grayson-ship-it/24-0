#!/usr/bin/env python3
"""
Pool audit: every constructor x decade combo, with how many distinct drivers started a
race for the constructor in that window and how many distinct chassis it entered.

    python3 scripts/pool_audit.py                 # writes data/audit/pool_audit.csv, prints summary
    python3 scripts/pool_audit.py --min-drivers 4 --min-chassis 2

Every column is computed directly from F1DB rows; nothing is estimated.

Columns
  constructor_id, constructor  F1DB constructor
  decade                       1950s .. 2020s (window = first year .. first year + 9)
  first_year, last_year        first/last season with a race result in the window
  seasons                      distinct seasons with a race result
  races                        distinct Grands Prix with a race result row
  indy500_races                of those, Indianapolis 500s (1950-1960 only; counted for the
                               championship, run to different rules, mostly US constructors)
  drivers_entered              distinct drivers with a race result row
  drivers_started              distinct drivers with at least one start (not DNS/DNQ/DNPQ/DNP/EX)
  chassis                      distinct chassis in season_entrant_chassis for the window
  wins, podiums                context only
"""
import argparse
import csv
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "f1db", "f1db.db")
OUT_PATH = os.path.join(ROOT, "data", "audit", "pool_audit.csv")
NOT_STARTED = ("DNS", "DNQ", "DNPQ", "DNP", "EX")

SQL = f"""
WITH res AS (
  SELECT d.constructor_id, d.driver_id, r.id AS race_id, r.year,
         (r.year / 10) * 10 AS decade_start,
         r.grand_prix_id = 'indianapolis' AS indy,
         d.position_text NOT IN ({",".join("?" * len(NOT_STARTED))}) AS started,
         d.position_number
  FROM race_data d JOIN race r ON r.id = d.race_id
  WHERE d.type = 'RACE_RESULT'
),
per_combo AS (
  SELECT constructor_id, decade_start,
         MIN(year) AS first_year, MAX(year) AS last_year,
         COUNT(DISTINCT year) AS seasons,
         COUNT(DISTINCT race_id) AS races,
         COUNT(DISTINCT CASE WHEN indy THEN race_id END) AS indy500_races,
         COUNT(DISTINCT driver_id) AS drivers_entered,
         COUNT(DISTINCT CASE WHEN started THEN driver_id END) AS drivers_started,
         COUNT(DISTINCT CASE WHEN position_number = 1 THEN race_id END) AS wins,
         COUNT(DISTINCT CASE WHEN position_number <= 3 THEN race_id || '/' || driver_id END) AS podiums
  FROM res GROUP BY constructor_id, decade_start
),
chassis AS (
  SELECT constructor_id, (year / 10) * 10 AS decade_start, COUNT(DISTINCT chassis_id) AS chassis
  FROM season_entrant_chassis GROUP BY constructor_id, decade_start
)
SELECT p.constructor_id, c.name AS constructor, p.decade_start,
       p.first_year, p.last_year, p.seasons, p.races, p.indy500_races,
       p.drivers_entered, p.drivers_started,
       COALESCE(ch.chassis, 0) AS chassis, p.wins, p.podiums
FROM per_combo p
JOIN constructor c ON c.id = p.constructor_id
LEFT JOIN chassis ch ON ch.constructor_id = p.constructor_id AND ch.decade_start = p.decade_start
ORDER BY p.drivers_started DESC, chassis DESC, p.constructor_id, p.decade_start
"""


def run(db_path):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(SQL, NOT_STARTED)]
    for r in rows:
        r["decade"] = f"{r.pop('decade_start')}s"
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--out", default=OUT_PATH)
    ap.add_argument("--min-drivers", type=int, default=None, help="report how many combos have at least this many starters")
    ap.add_argument("--min-chassis", type=int, default=1)
    args = ap.parse_args()
    if not os.path.exists(args.db):
        sys.exit(f"Database not found at {args.db}. Run scripts/fetch_data.sh first.")

    rows = run(args.db)
    cols = ["constructor_id", "constructor", "decade", "first_year", "last_year", "seasons", "races",
            "indy500_races", "drivers_entered", "drivers_started", "chassis", "wins", "podiums"]
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows({k: r[k] for k in cols} for r in rows)
    print(f"wrote {len(rows)} combos to {os.path.relpath(args.out, ROOT)}")

    print("\nDistribution of drivers_started per combo:")
    print(f"{'>= N drivers':>13}  {'combos':>6}  {'with >=1 chassis':>16}  {'with >=2 chassis':>16}")
    for n in [1, 2, 3, 4, 5, 6, 8, 10, 15, 20]:
        a = sum(1 for r in rows if r["drivers_started"] >= n)
        b = sum(1 for r in rows if r["drivers_started"] >= n and r["chassis"] >= 1)
        c = sum(1 for r in rows if r["drivers_started"] >= n and r["chassis"] >= 2)
        print(f"{n:>13}  {a:>6}  {b:>16}  {c:>16}")

    print("\nExact drivers_started histogram:")
    from collections import Counter
    h = Counter(r["drivers_started"] for r in rows)
    for k in sorted(h):
        print(f"  {k:>3} drivers: {h[k]:>3} combos")

    if args.min_drivers is not None:
        ok = [r for r in rows if r["drivers_started"] >= args.min_drivers and r["chassis"] >= args.min_chassis]
        print(f"\nThreshold drivers_started >= {args.min_drivers} and chassis >= {args.min_chassis}: {len(ok)} of {len(rows)} combos")
        by_dec = Counter(r["decade"] for r in ok)
        for d in sorted(by_dec):
            print(f"  {d}: {by_dec[d]}")


if __name__ == "__main__":
    main()
