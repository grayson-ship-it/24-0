#!/usr/bin/env python3
"""
Pool audit: every constructor x decade combo under the pool rules in scripts/f1pool.py.

    python3 scripts/pool_audit.py            # writes data/audit/pool_audit.csv and prints the distribution

Columns
  drivers_any_entrant  distinct drivers with >=1 start for the constructor, any entrant (Indy excluded)
  drivers_works        same, works entrants only
  drivers_pool         works entrants and >= MIN_STARTS_PER_DRIVER starts (this is the pool)
  cars                 constructor-seasons with a works start (one car card each)
  seasons, races, wins, podiums   context, works entrants only
  playable             drivers_pool >= POOL_MIN_DRIVERS and cars >= POOL_MIN_CARS
  wheel_weight         'high' if drivers_pool >= WHEEL_WEIGHT_DRIVERS else 'low'
"""
import csv
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import f1pool  # noqa: E402

OUT = os.path.join(f1pool.ROOT, "data", "audit", "pool_audit.csv")

SQL = """
WITH per_driver AS (
  SELECT constructor_id, (year/10)*10 AS dec, driver_id,
         MAX(works) AS any_works,
         COUNT(DISTINCT CASE WHEN started THEN race_id END) AS starts_any,
         COUNT(DISTINCT CASE WHEN started AND works THEN race_id END) AS starts_works
  FROM res GROUP BY 1, 2, 3
),
drv AS (
  SELECT constructor_id, dec,
         COUNT(DISTINCT CASE WHEN starts_any >= 1 THEN driver_id END) AS drivers_any_entrant,
         COUNT(DISTINCT CASE WHEN starts_works >= 1 THEN driver_id END) AS drivers_works,
         COUNT(DISTINCT CASE WHEN starts_works >= ? THEN driver_id END) AS drivers_pool
  FROM per_driver GROUP BY 1, 2
),
ctx AS (
  SELECT constructor_id, (year/10)*10 AS dec,
         MIN(year) AS first_year, MAX(year) AS last_year,
         COUNT(DISTINCT CASE WHEN works AND started THEN year END) AS cars,
         COUNT(DISTINCT CASE WHEN works AND started THEN year END) AS seasons,
         COUNT(DISTINCT CASE WHEN works THEN race_id END) AS races,
         COUNT(DISTINCT CASE WHEN works AND position_number = 1 THEN race_id END) AS wins,
         COUNT(DISTINCT CASE WHEN works AND position_number <= 3 THEN race_id || '/' || driver_id END) AS podiums
  FROM res GROUP BY 1, 2
)
SELECT d.constructor_id, c.name AS constructor, d.dec, x.first_year, x.last_year, x.seasons, x.races,
       d.drivers_any_entrant, d.drivers_works, d.drivers_pool, x.cars, x.wins, x.podiums
FROM drv d JOIN ctx x ON x.constructor_id = d.constructor_id AND x.dec = d.dec
JOIN constructor c ON c.id = d.constructor_id
ORDER BY d.drivers_pool DESC, x.cars DESC, d.constructor_id, d.dec
"""


def main():
    conn = f1pool.connect()
    rows = [dict(r) for r in conn.execute(SQL, (f1pool.MIN_STARTS_PER_DRIVER,))]
    for r in rows:
        r["decade"] = f"{r.pop('dec')}s"
        r["playable"] = int(r["drivers_pool"] >= f1pool.POOL_MIN_DRIVERS and r["cars"] >= f1pool.POOL_MIN_CARS)
        r["wheel_weight"] = "high" if r["drivers_pool"] >= f1pool.WHEEL_WEIGHT_DRIVERS else "low"
    cols = ["constructor_id", "constructor", "decade", "first_year", "last_year", "seasons", "races",
            "drivers_any_entrant", "drivers_works", "drivers_pool", "cars", "wins", "podiums", "playable", "wheel_weight"]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows({k: r[k] for k in cols} for r in rows)
    print(f"wrote {len(rows)} combos to {os.path.relpath(OUT, f1pool.ROOT)}   "
          f"(window {f1pool.FIRST_SEASON}-{f1pool.LAST_COMPLETE_SEASON}, Indy 500 excluded, works only, "
          f"min {f1pool.MIN_STARTS_PER_DRIVER} starts)")

    decs = sorted(set(r["decade"] for r in rows))
    print("\nCombos with at least N pool drivers, by decade:")
    print("  N   total  " + "  ".join(decs))
    for n in (1, 2, 3, 4, 5, 6, 8, 10):
        tot = sum(1 for r in rows if r["drivers_pool"] >= n)
        per = [sum(1 for r in rows if r["decade"] == d and r["drivers_pool"] >= n) for d in decs]
        print(f"{n:>3}   {tot:>5}  " + "  ".join(f"{p:>5}" for p in per))
    play = [r for r in rows if r["playable"]]
    hi = [r for r in play if r["wheel_weight"] == "high"]
    print(f"\nPlayable (>= {f1pool.POOL_MIN_DRIVERS} drivers, >= {f1pool.POOL_MIN_CARS} car): {len(play)} of {len(rows)}"
          f"   high wheel weight (>= {f1pool.WHEEL_WEIGHT_DRIVERS} drivers): {len(hi)}")
    print("  playable by decade: " + ", ".join(f"{d} {sum(1 for r in play if r['decade']==d)}" for d in decs))
    h = Counter(r["drivers_pool"] for r in rows)
    print("\nExact drivers_pool histogram: " + ", ".join(f"{k}:{h[k]}" for k in sorted(h)))

    print("\nLargest reductions from the works filter (drivers_any_entrant -> drivers_works -> drivers_pool):")
    for r in sorted(rows, key=lambda r: r["drivers_works"] - r["drivers_any_entrant"])[:15]:
        print(f"  {r['constructor']:<14} {r['decade']}  {r['drivers_any_entrant']:>3} -> {r['drivers_works']:>3} -> {r['drivers_pool']:>3}")
    print("\nLargest reductions from the min-starts backstop (drivers_works -> drivers_pool):")
    for r in sorted(rows, key=lambda r: r["drivers_pool"] - r["drivers_works"])[:10]:
        print(f"  {r['constructor']:<14} {r['decade']}  {r['drivers_works']:>3} -> {r['drivers_pool']:>3}")


if __name__ == "__main__":
    main()
