#!/usr/bin/env python3
"""
Team x Decade pool: the drivers and car cards a spin of this combo would show.

    python3 scripts/team_decade.py mclaren 2000s
    python3 scripts/team_decade.py "Red Bull" 2010s --json

Applies every pool rule from scripts/f1pool.py (complete seasons only, Indy 500 excluded,
works entries only, minimum starts per driver, one car card per constructor-season).
Stats are aggregated over the whole tenure. Nothing is estimated; blanks mean no data.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import f1pool  # noqa: E402


def fmt(v, kind="s"):
    if v is None:
        return ""
    if kind == "avg":
        return f"{v:.2f}"
    if kind == "pct":
        return f"{100 * v:.1f}%"
    return str(v)


def print_table(rows, cols):
    cells = [[fmt(r.get(k), kind) for k, _, kind, _ in cols] for r in rows]
    widths = [max([len(h)] + [c[i] and len(c[i]) or 0 for c in cells]) for i, (_, h, _, _) in enumerate(cols)]
    line = lambda vals: "  ".join(v.rjust(w) if a == "r" else v.ljust(w)
                                  for v, w, (_, _, _, a) in zip(vals, widths, cols)).rstrip()
    print(line([h for _, h, _, _ in cols]))
    print(line(["-" * w for w in widths]))
    for c in cells:
        print(line(c))


DRIVER_COLS = [
    ("driver", "Driver", "s", "l"), ("first_year", "From", "s", "r"), ("last_year", "To", "s", "r"),
    ("entries", "Entries", "s", "r"), ("starts", "Starts", "s", "r"), ("wins", "Wins", "s", "r"),
    ("podiums", "Podiums", "s", "r"), ("poles", "Poles", "s", "r"), ("classified", "Classified", "s", "r"),
    ("avg_finish", "AvgFinish", "avg", "r"), ("finish_rate", "FinishRate", "pct", "r"),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("team")
    ap.add_argument("decade")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--db", default=f1pool.DB_PATH)
    args = ap.parse_args()

    conn = f1pool.connect(args.db)
    cid, name = f1pool.resolve_constructor(conn, args.team)
    y0, y1 = f1pool.decade_range(args.decade)
    drivers = f1pool.drivers_for(conn, cid, y0, y1)
    below = [d for d in f1pool.drivers_for(conn, cid, y0, y1, min_starts=1)
             if d["starts"] < f1pool.MIN_STARTS_PER_DRIVER]
    cars = f1pool.cars_for(conn, cid, y0, y1)
    version_file = os.path.join(os.path.dirname(args.db), "VERSION")
    version = open(version_file).read().strip() if os.path.exists(version_file) else None
    playable = len(drivers) >= f1pool.POOL_MIN_DRIVERS and len(cars) >= f1pool.POOL_MIN_CARS

    if args.json:
        json.dump({"constructor_id": cid, "constructor": name, "years": [y0, y1], "f1db_version": version,
                   "playable": playable, "drivers": drivers, "cars": cars,
                   "excluded_below_min_starts": below}, sys.stdout, indent=2)
        print()
        return

    print(f"{name} ({cid}) {y0}-{y1}   F1DB {version or '?'}   works entries, Indy 500 excluded, "
          f"min {f1pool.MIN_STARTS_PER_DRIVER} starts   playable: {'yes' if playable else 'NO'}")
    print()
    print_table(drivers, DRIVER_COLS)
    if below:
        print(f"\nNot in pool (fewer than {f1pool.MIN_STARTS_PER_DRIVER} starts): "
              + ", ".join(f"{d['driver']} ({d['starts']})" for d in below))
    print("\nCar cards (one per season; several names = several chassis, F1DB cannot say which was primary):")
    print_table([{"year": c["year"], "card": c["name"]} for c in cars],
                [("year", "Season", "s", "r"), ("card", "Card", "s", "l")])


if __name__ == "__main__":
    main()
