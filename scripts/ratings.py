#!/usr/bin/env python3
"""
Era-relative ratings for a Team x Decade combo, with raw stats beside the normalized numbers.

    python3 scripts/ratings.py mclaren 2000s
    python3 scripts/ratings.py williams 1990s --prior-sweep     # shrinkage sensitivity
    python3 scripts/ratings.py brawn 2000s --json

DRIVER rating: teammate comparison, same works car, same race, same season.
  Counting is PER RACE: a race with a teammate comparison contributes 1 unit, and the
  driver's score for that race is the fraction of compared teammates they beat (1.0 when
  ahead of both teammates in a three-car team, 0.5 when ahead of one). Per-pair W-L is
  still shown, but sample size means races, so three-car teams do not accumulate evidence
  faster and the prior below is in race units for every era.
  race:   races where the driver and >=1 teammate were both classified
  quali:  races where both have a qualifying classification
  Shrinkage (empirical Bayes): each component is pulled toward 50% by a prior worth
  RATING_PRIOR_RACES races:  (wins + k/2) / (n + k).
  rating = mean of the two shrunk components, 0-100, and is SUPPRESSED (blank) unless the
  driver has at least MIN_H2H_RACES classified race comparisons. A qualifying-only record
  is not a driver rating. Old (per-pair, unshrunk, no floor) is shown for comparison.

CAR rating: one card per constructor-season, driver-neutral, field-relative.
  points_share  works race points / all race points awarded that season (Indy excluded)
  The FIELD for the comparisons below is the season's works teams with at least one
  full-time car (works car-starts >= races in the season), so part-time entries do not
  pad it.
  x_avg     points_share / the mean share of the field (1.0 = an average team)
  z         (points_share - field mean) / field sd, population sd over the field
  pct_field share of the field the team out-scored, 0-100
  Old measures (share vs best team, percentile over every works team) are shown too.
Nothing is estimated; blanks mean the comparison does not exist in the data.
"""
import argparse
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import f1pool  # noqa: E402
from team_decade import print_table, DRIVER_COLS  # noqa: E402

RATING_PRIOR_RACES = f1pool.RATING_PRIOR_RACES
MIN_H2H_RACES = f1pool.MIN_H2H_RACES

PAIR_SQL = """
SELECT a.driver_id, b.driver_id AS mate, db.name AS mate_name, a.race_id,
       a.position_number AS pa, b.position_number AS pb, a.quali_pos AS qa, b.quali_pos AS qb
FROM res a
JOIN res b ON b.race_id = a.race_id AND b.constructor_id = a.constructor_id
          AND b.driver_id <> a.driver_id AND b.works = 1 AND b.shared = 0
JOIN driver db ON db.id = b.driver_id
WHERE a.constructor_id = ? AND a.year BETWEEN ? AND ? AND a.works = 1 AND a.shared = 0
"""


def shrink(wins, n, k):
    return (wins + k / 2) / (n + k) if (n + k) > 0 else None


def driver_ratings(conn, cid, y0, y1, k=RATING_PRIOR_RACES, floor=MIN_H2H_RACES):
    per = defaultdict(lambda: {"race": defaultdict(list), "quali": defaultdict(list), "mates": defaultdict(int)})
    for r in conn.execute(PAIR_SQL, (cid, y0, y1)):
        a = per[r["driver_id"]]
        if r["pa"] is not None and r["pb"] is not None and r["pa"] != r["pb"]:
            a["race"][r["race_id"]].append(r["pa"] < r["pb"])
            a["mates"][r["mate_name"]] += 1
        if r["qa"] is not None and r["qb"] is not None and r["qa"] != r["qb"]:
            a["quali"][r["race_id"]].append(r["qa"] < r["qb"])
    out = {}
    for did, a in per.items():
        d = {"teammates": ", ".join(f"{m} {n}" for m, n in sorted(a["mates"].items(), key=lambda x: -x[1]))}
        for comp in ("race", "quali"):
            pairs = [x for v in a[comp].values() for x in v]
            pw, pl = sum(pairs), len(pairs) - sum(pairs)
            n = len(a[comp])
            wins = sum(sum(v) / len(v) for v in a[comp].values())
            d[f"{comp}_pairs"] = f"{pw}-{pl}" if pairs else ""
            d[f"{comp}_pair_pct"] = pw / len(pairs) if pairs else None
            d[f"{comp}_n"] = n
            d[f"{comp}_wins"] = wins
            d[f"{comp}_pct"] = wins / n if n else None
            d[f"{comp}_shrunk"] = shrink(wins, n, k) if n else None
        old = [p for p in (d["race_pair_pct"], d["quali_pair_pct"]) if p is not None]
        d["old_rating"] = 100 * sum(old) / len(old) if old else None
        new = [p for p in (d["race_shrunk"], d["quali_shrunk"]) if p is not None]
        d["rating"] = 100 * sum(new) / len(new) if new and d["race_n"] >= floor else None
        d["rating_note"] = "" if d["rating"] is not None else (f"n<{floor}" if new else "no teammate")
        out[did] = d
    return out


EMPTY = {"teammates": "", "race_pairs": "", "race_pair_pct": None, "race_n": 0, "race_wins": 0, "race_pct": None,
         "race_shrunk": None, "quali_pairs": "", "quali_pair_pct": None, "quali_n": 0, "quali_wins": 0,
         "quali_pct": None, "quali_shrunk": None, "old_rating": None, "rating": None, "rating_note": "no teammate"}

CAR_SQL = """
WITH season_total AS (
  SELECT year, SUM(points) AS total_points, COUNT(DISTINCT race_id) AS races FROM res GROUP BY year
),
team_season AS (
  SELECT constructor_id, year,
         COUNT(DISTINCT race_id) AS races_entered,
         SUM(started) AS starts, SUM(classified) AS classified,
         COUNT(DISTINCT CASE WHEN position_number = 1 THEN race_id END) AS wins,
         SUM(CASE WHEN position_number <= 3 THEN 1 ELSE 0 END) AS podiums,
         COUNT(DISTINCT CASE WHEN pole THEN race_id END) AS poles,
         SUM(points) AS points,
         AVG(position_number) AS avg_finish
  FROM res WHERE works = 1 GROUP BY constructor_id, year
)
SELECT t.*, s.total_points, s.races,
       t.points * 1.0 / NULLIF(s.total_points, 0) AS points_share,
       t.starts >= s.races AS in_field
FROM team_season t JOIN season_total s ON s.year = t.year
WHERE t.starts > 0
"""


def car_ratings(conn, cid, y0, y1):
    all_rows = [dict(r) for r in conn.execute(CAR_SQL)]
    by_year = defaultdict(list)
    for r in all_rows:
        by_year[r["year"]].append(r)
    cards = {c["year"]: c for c in f1pool.cars_for(conn, cid, y0, y1)}
    out = []
    for d in sorted((r for r in all_rows if r["constructor_id"] == cid and y0 <= r["year"] <= y1), key=lambda r: r["year"]):
        season = by_year[d["year"]]
        field = [r for r in season if r["in_field"]]
        shares = [r["points_share"] or 0 for r in field]
        best = max(r["points"] for r in season)
        card = cards.get(d["year"])
        d["card"] = card["name"] if card else ""
        d["finish_rate"] = d["classified"] / d["starts"] if d["starts"] else None
        if d["classified"] == 0:
            d["avg_finish"] = None
        sh = d["points_share"] or 0
        d["points_share"] = 100 * sh
        d["vs_best"] = 100 * d["points"] / best if best else None
        n_all = len(season)
        d["pct_all"] = 100 * sum(1 for r in season if r["points"] < d["points"]) / (n_all - 1) if n_all > 1 else None
        d["n_all"] = n_all
        d["n_field"] = len(field)
        if field and d["in_field"]:
            mean = sum(shares) / len(shares)
            sd = math.sqrt(sum((x - mean) ** 2 for x in shares) / len(shares))
            d["x_avg"] = sh / mean if mean else None
            d["z"] = (sh - mean) / sd if sd else None
            d["pct_field"] = 100 * sum(1 for x in shares if x < sh) / (len(shares) - 1) if len(shares) > 1 else None
        else:
            d["x_avg"] = d["z"] = d["pct_field"] = None   # part-time entry: not in the field
        out.append(d)
    return out


def load(conn, team, decade, k=RATING_PRIOR_RACES, floor=MIN_H2H_RACES):
    cid, name = f1pool.resolve_constructor(conn, team)
    y0, y1 = f1pool.decade_range(decade)
    drivers = f1pool.drivers_for(conn, cid, y0, y1)
    dr = driver_ratings(conn, cid, y0, y1, k, floor)
    for d in drivers:
        d.update(dr.get(d["driver_id"], EMPTY))
    return cid, name, y0, y1, drivers, car_ratings(conn, cid, y0, y1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("team")
    ap.add_argument("decade")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--prior", type=int, default=RATING_PRIOR_RACES, help="pseudo-races in the prior")
    ap.add_argument("--floor", type=int, default=MIN_H2H_RACES, help="min classified h2h races to show a rating")
    ap.add_argument("--prior-sweep", action="store_true", help="show ratings under several prior strengths")
    ap.add_argument("--db", default=f1pool.DB_PATH)
    args = ap.parse_args()
    conn = f1pool.connect(args.db)

    if args.prior_sweep:
        ks = (0, 5, 10, 15, 20)
        cid, name, y0, y1, drivers, _ = load(conn, args.team, args.decade, ks[0], 0)
        table = {d["driver_id"]: {"driver": d["driver"], "race_pairs": d["race_pairs"], "race_n": d["race_n"],
                                  "quali_pairs": d["quali_pairs"], "quali_n": d["quali_n"]} for d in drivers}
        for k in ks:
            _, _, _, _, ds, _ = load(conn, args.team, args.decade, k, 0)
            for d in ds:
                table[d["driver_id"]][f"k{k}"] = d["rating"]
        print(f"=== {name} {y0}-{y1}: rating under prior strength k (no floor applied; per-race counting) ===")
        print_table(list(table.values()), [("driver", "Driver", "s", "l"), ("race_pairs", "Race W-L", "s", "r"),
                                           ("race_n", "Races", "s", "r"), ("quali_pairs", "Quali W-L", "s", "r"),
                                           ("quali_n", "Q races", "s", "r")]
                    + [(f"k{k}", f"k={k}", "avg", "r") for k in ks])
        return

    cid, name, y0, y1, drivers, cars = load(conn, args.team, args.decade, args.prior, args.floor)
    if args.json:
        json.dump({"constructor_id": cid, "constructor": name, "years": [y0, y1],
                   "prior_races": args.prior, "min_h2h_races": args.floor,
                   "drivers": drivers, "cars": cars}, sys.stdout, indent=2)
        print()
        return

    print(f"=== {name} {y0}-{y1} ===   prior k={args.prior} races, floor {args.floor} classified h2h races")
    print("\nDRIVERS  raw (works entries, whole tenure)")
    print_table(drivers, DRIVER_COLS)
    print("\nDRIVERS  teammate head-to-head: per-pair W-L, per-race sample n and win fraction, old vs new rating")
    print_table(drivers, [
        ("driver", "Driver", "s", "l"),
        ("race_pairs", "Race W-L", "s", "r"), ("race_n", "n", "s", "r"), ("race_pct", "Race%", "pct", "r"),
        ("quali_pairs", "Quali W-L", "s", "r"), ("quali_n", "n", "s", "r"), ("quali_pct", "Quali%", "pct", "r"),
        ("old_rating", "Old", "avg", "r"), ("race_shrunk", "Race~", "pct", "r"), ("quali_shrunk", "Quali~", "pct", "r"),
        ("rating", "NEW", "avg", "r"), ("rating_note", "", "s", "l"), ("teammates", "Race comparisons vs", "s", "l")])
    print("\nCARS  raw team results, then old (vs best / percentile of all) and re-based (field = full-time works teams)")
    print_table(cars, [
        ("year", "Season", "s", "r"), ("card", "Card", "s", "l"),
        ("starts", "Starts", "s", "r"), ("wins", "Wins", "s", "r"), ("podiums", "Pod", "s", "r"), ("poles", "Poles", "s", "r"),
        ("avg_finish", "AvgFin", "avg", "r"), ("finish_rate", "FinRate", "pct", "r"),
        ("points", "Pts", "s", "r"), ("points_share", "Share%", "avg", "r"),
        ("vs_best", "vsBest", "avg", "r"), ("pct_all", "Pct(all)", "avg", "r"), ("n_all", "All", "s", "r"),
        ("n_field", "Field", "s", "r"), ("x_avg", "xAvg", "avg", "r"), ("z", "z", "avg", "r"), ("pct_field", "PctField", "avg", "r")])


if __name__ == "__main__":
    main()
