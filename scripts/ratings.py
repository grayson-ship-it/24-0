#!/usr/bin/env python3
"""
Era-relative ratings for a Team x Decade combo. Shows raw stats next to the normalized
numbers so the effect of normalization is visible.

    python3 scripts/ratings.py mclaren 2000s
    python3 scripts/ratings.py brawn 2000s --json

DRIVER rating: teammate comparison, same works car, same race, same season.
  race h2h   races where driver and a teammate were both classified: ahead / (ahead + behind)
  quali h2h  races where both have a qualifying classification: ahead / (ahead + behind)
  rating     mean of the two percentages (or the one available), 0-100; blank if no teammate
             comparison exists. Ties (shared cars excluded already) are dropped.
  This controls for the car by construction. Known limit: it does not weight the
  teammate's quality (v1; an Elo-style network across all pairs is the rigorous fix).

CAR rating: one card per constructor-season, driver-neutral and field-relative. A card
  that names two chassis (Ferrari F2001B / F2002) is one card: F1DB has no per-race
  chassis, so the season's team results cannot be split between them.
  points_share   works points / all race points awarded that season (Indy excluded)
  share_vs_best  points_share / the best constructor's share that season, 0-100 (best car = 100)
  pct_rank       share of that season's constructors (>=1 works start) the team out-scored, 0-100
  Raw context: starts, wins, podiums, poles, avg classified finish, finish rate.
  A car's numbers are the team's numbers for that season; the driver rating above is the
  piece that is NOT the car, which is what stops a driver pick and a car pick double-counting.
Nothing is estimated; a blank means the comparison does not exist in the data.
"""
import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import f1pool  # noqa: E402
from team_decade import print_table, DRIVER_COLS  # noqa: E402

PAIR_SQL = """
SELECT a.driver_id, b.driver_id AS mate, db.name AS mate_name, a.race_id,
       a.position_number AS pa, b.position_number AS pb, a.quali_pos AS qa, b.quali_pos AS qb
FROM res a
JOIN res b ON b.race_id = a.race_id AND b.constructor_id = a.constructor_id
          AND b.driver_id <> a.driver_id AND b.works = 1 AND b.shared = 0
JOIN driver db ON db.id = b.driver_id
WHERE a.constructor_id = ? AND a.year BETWEEN ? AND ? AND a.works = 1 AND a.shared = 0
"""


def driver_ratings(conn, cid, y0, y1):
    acc = defaultdict(lambda: {"race_ahead": 0, "race_behind": 0, "quali_ahead": 0, "quali_behind": 0,
                               "mates": defaultdict(int)})
    for r in conn.execute(PAIR_SQL, (cid, y0, y1)):
        a = acc[r["driver_id"]]
        if r["pa"] is not None and r["pb"] is not None and r["pa"] != r["pb"]:
            a["race_ahead" if r["pa"] < r["pb"] else "race_behind"] += 1
            a["mates"][r["mate_name"]] += 1
        if r["qa"] is not None and r["qb"] is not None and r["qa"] != r["qb"]:
            a["quali_ahead" if r["qa"] < r["qb"] else "quali_behind"] += 1
    out = {}
    for did, a in acc.items():
        rn = a["race_ahead"] + a["race_behind"]
        qn = a["quali_ahead"] + a["quali_behind"]
        race = a["race_ahead"] / rn if rn else None
        quali = a["quali_ahead"] / qn if qn else None
        parts = [p for p in (race, quali) if p is not None]
        out[did] = {
            "race_h2h": f"{a['race_ahead']}-{a['race_behind']}" if rn else "",
            "race_h2h_pct": race, "quali_h2h": f"{a['quali_ahead']}-{a['quali_behind']}" if qn else "",
            "quali_h2h_pct": quali,
            "driver_rating": 100 * sum(parts) / len(parts) if parts else None,
            "teammates": ", ".join(f"{m} {n}" for m, n in sorted(a["mates"].items(), key=lambda x: -x[1])),
        }
    return out


CAR_SQL = """
WITH season_total AS (
  SELECT year, SUM(points) AS total_points FROM res GROUP BY year
),
team_season AS (
  SELECT constructor_id, year,
         COUNT(DISTINCT race_id) AS races,
         SUM(started) AS starts, SUM(classified) AS classified,
         COUNT(DISTINCT CASE WHEN position_number = 1 THEN race_id END) AS wins,
         SUM(CASE WHEN position_number <= 3 THEN 1 ELSE 0 END) AS podiums,
         COUNT(DISTINCT CASE WHEN pole THEN race_id END) AS poles,
         SUM(points) AS points,
         AVG(position_number) AS avg_finish
  FROM res WHERE works = 1 GROUP BY constructor_id, year
),
shares AS (
  SELECT t.*, t.points * 1.0 / NULLIF(s.total_points, 0) AS points_share,
         (SELECT COUNT(*) FROM team_season o WHERE o.year = t.year AND o.starts > 0) AS n_teams,
         (SELECT COUNT(*) FROM team_season o WHERE o.year = t.year AND o.starts > 0
             AND o.points < t.points) AS n_below,
         (SELECT MAX(points) FROM team_season o WHERE o.year = t.year) AS best_points
  FROM team_season t JOIN season_total s ON s.year = t.year
)
SELECT * FROM shares WHERE constructor_id = ? AND year BETWEEN ? AND ? AND starts > 0 ORDER BY year
"""


def car_ratings(conn, cid, y0, y1):
    cards = {c["year"]: c for c in f1pool.cars_for(conn, cid, y0, y1)}
    out = []
    for r in conn.execute(CAR_SQL, (cid, y0, y1)):
        d = dict(r)
        card = cards.get(d["year"])
        d["card"] = card["name"] if card else ""
        d["finish_rate"] = d["classified"] / d["starts"] if d["starts"] else None
        d["share_vs_best"] = 100 * d["points"] / d["best_points"] if d["best_points"] else None
        d["pct_rank"] = 100 * d["n_below"] / (d["n_teams"] - 1) if d["n_teams"] > 1 else None
        d["points_share"] = 100 * d["points_share"] if d["points_share"] is not None else None
        if d["classified"] == 0:
            d["avg_finish"] = None
        out.append(d)
    return out


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
    dr = driver_ratings(conn, cid, y0, y1)
    for d in drivers:
        d.update(dr.get(d["driver_id"], {"race_h2h": "", "race_h2h_pct": None, "quali_h2h": "",
                                          "quali_h2h_pct": None, "driver_rating": None, "teammates": ""}))
    cars = car_ratings(conn, cid, y0, y1)

    if args.json:
        json.dump({"constructor_id": cid, "constructor": name, "years": [y0, y1],
                   "drivers": drivers, "cars": cars}, sys.stdout, indent=2)
        print()
        return

    print(f"=== {name} {y0}-{y1} ===")
    print("\nDRIVERS  raw (works entries, whole tenure)")
    print_table(drivers, DRIVER_COLS)
    print("\nDRIVERS  car-adjusted (teammate head-to-head, same car, same race)")
    print_table(drivers, [
        ("driver", "Driver", "s", "l"), ("race_h2h", "Race W-L", "s", "r"), ("race_h2h_pct", "Race%", "pct", "r"),
        ("quali_h2h", "Quali W-L", "s", "r"), ("quali_h2h_pct", "Quali%", "pct", "r"),
        ("driver_rating", "Rating", "avg", "r"), ("teammates", "Race comparisons vs", "s", "l")])
    print("\nCARS  one card per season: raw team results, then field-relative")
    print_table(cars, [
        ("year", "Season", "s", "r"), ("card", "Card", "s", "l"),
        ("starts", "Starts", "s", "r"), ("wins", "Wins", "s", "r"), ("podiums", "Pod", "s", "r"),
        ("poles", "Poles", "s", "r"), ("avg_finish", "AvgFin", "avg", "r"), ("finish_rate", "FinRate", "pct", "r"),
        ("points", "Pts", "s", "r"), ("points_share", "Share%", "avg", "r"),
        ("share_vs_best", "vsBest", "avg", "r"), ("pct_rank", "PctRank", "avg", "r"), ("n_teams", "Teams", "s", "r")])


if __name__ == "__main__":
    main()
