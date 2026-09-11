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
  z_scaled  z / sqrt(n_field - 1): the largest z a field of n allows is sqrt(n-1), so this
            is a field-size-independent scale where 1.0 = every point the field scored
            beyond an even split went to this team. Verified NOT era-neutral: z measures
            shape, so any leader over an even rest scores 1.0 whatever the margin, and
            tiny 1950s-60s fields make that trivial (1961 Ferrari, 45% share: 0.98).
  z_cdf     normal CDF of z, 0-100 (shown for comparison; compresses the top end)
  zs_uni    z_scaled recomputed after re-scoring every classified finish on one fixed scheme
            (25-18-15-12-10-8-6-4-2-1) so the season's own points table plays no part.
            Shown to expose the residual points-scheme effect; not the card rating.
  pct_field share of the field the team out-scored, 0-100
  share_of_max  team points / the most points its own started cars could have scored (per
            race: the points paid to P1..Pk, k = works cars the team started). 1.0 = every
            car it started finished at the top of the order, in every era. Magnitude, not
            shape: unlike z it does not saturate when the rest of the field happens to be
            even, and it is neutral to field size and to how many cars the team ran.
  som_uni   share_of_max with every classified finish re-scored on the fixed
            25-18-15-12-10-8-6-4-2-1 scheme, so the season's own points table (top-5, top-6,
            top-8, top-10 scoring) plays no part either. RECOMMENDED card rating.
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


ALL_PAIR_SQL = """
SELECT a.driver_id, a.constructor_id, (a.year/10)*10 AS dec, a.race_id,
       a.position_number AS pa, b.position_number AS pb, a.quali_pos AS qa, b.quali_pos AS qb
FROM res a
JOIN res b ON b.race_id = a.race_id AND b.constructor_id = a.constructor_id
          AND b.driver_id <> a.driver_id AND b.works = 1 AND b.shared = 0
WHERE a.works = 1 AND a.shared = 0
"""


def all_driver_ratings(conn, k=RATING_PRIOR_RACES, floor=MIN_H2H_RACES):
    """Rating inputs for every pool tenure (driver x constructor x decade with >= MIN_STARTS)."""
    per = defaultdict(lambda: {"race": defaultdict(list), "quali": defaultdict(list)})
    for r in conn.execute(ALL_PAIR_SQL):
        a = per[(r["driver_id"], r["constructor_id"], r["dec"])]
        if r["pa"] is not None and r["pb"] is not None and r["pa"] != r["pb"]:
            a["race"][r["race_id"]].append(r["pa"] < r["pb"])
        if r["qa"] is not None and r["qb"] is not None and r["qa"] != r["qb"]:
            a["quali"][r["race_id"]].append(r["qa"] < r["qb"])
    out = []
    for p in conn.execute("""
        SELECT r.driver_id, dr.name AS driver, r.constructor_id, (r.year/10)*10 AS dec,
               COUNT(DISTINCT CASE WHEN started THEN race_id END) AS starts,
               COUNT(DISTINCT CASE WHEN classified THEN race_id END) AS classified
        FROM res r JOIN driver dr ON dr.id = r.driver_id WHERE works = 1
        GROUP BY 1, 2, 3, 4 HAVING starts >= ?""", (f1pool.MIN_STARTS_PER_DRIVER,)):
        a = per.get((p["driver_id"], p["constructor_id"], p["dec"]))
        d = dict(p)
        for comp in ("race", "quali"):
            races = a[comp] if a else {}
            n = len(races)
            wins = sum(sum(v) / len(v) for v in races.values())
            pairs = [x for v in races.values() for x in v]
            d[f"{comp}_n"] = n
            d[f"{comp}_pairs"] = f"{sum(pairs)}-{len(pairs) - sum(pairs)}" if pairs else ""
            d[f"{comp}_shrunk"] = shrink(wins, n, k) if n else None
        new = [x for x in (d["race_shrunk"], d["quali_shrunk"]) if x is not None]
        d["rating"] = 100 * sum(new) / len(new) if new and d["race_n"] >= floor else None
        out.append(d)
    return out


def coverage_report(conn, k, floors=(1, 2, 3, 5)):
    base = all_driver_ratings(conn, k, 0)
    decs = sorted(set(d["dec"] for d in base))
    print(f"Pool tenures: {len(base)}  (works, >= {f1pool.MIN_STARTS_PER_DRIVER} starts)   prior k={k}")
    print("\nShare of pool tenures with a rating, by floor (min classified h2h races):")
    print("floor   all   " + "  ".join(f"{d}s" for d in decs))
    for f in floors:
        row = [sum(1 for d in base if d["race_n"] >= f) / len(base)]
        row += [sum(1 for d in base if d["dec"] == dec and d["race_n"] >= f) / sum(1 for d in base if d["dec"] == dec) for dec in decs]
        print(f"{f:>5}  " + "  ".join(f"{100*x:4.0f}%" for x in row))
    print("\nWorst case per floor: highest-rated tenures with exactly the minimum number of races")
    for f in floors:
        rows = [d for d in base if d["race_n"] == f]
        rows.sort(key=lambda d: -(d["rating"] or 0))
        print(f"  floor {f}: {len(rows)} tenures at n={f}")
        for d in rows[:4]:
            print(f"     {d['rating']:5.1f}  {d['driver']:<24} {d['constructor_id']:<14} {d['dec']}s  "
                  f"starts {d['starts']:>2}  race {d['race_pairs']:>5} (n={d['race_n']})  quali {d['quali_pairs']:>6} (n={d['quali_n']})")


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
         SUM(CASE position_number WHEN 1 THEN 25 WHEN 2 THEN 18 WHEN 3 THEN 15 WHEN 4 THEN 12 WHEN 5 THEN 10
                  WHEN 6 THEN 8 WHEN 7 THEN 6 WHEN 8 THEN 4 WHEN 9 THEN 2 WHEN 10 THEN 1 ELSE 0 END) AS upoints,
         AVG(position_number) AS avg_finish
  FROM res WHERE works = 1 GROUP BY constructor_id, year
),
season_utotal AS (
  SELECT year, SUM(CASE position_number WHEN 1 THEN 25 WHEN 2 THEN 18 WHEN 3 THEN 15 WHEN 4 THEN 12 WHEN 5 THEN 10
                  WHEN 6 THEN 8 WHEN 7 THEN 6 WHEN 8 THEN 4 WHEN 9 THEN 2 WHEN 10 THEN 1 ELSE 0 END) AS total_upoints
  FROM res GROUP BY year
)
SELECT t.*, s.total_points, s.races,
       t.points * 1.0 / NULLIF(s.total_points, 0) AS points_share,
       t.upoints * 1.0 / NULLIF(u.total_upoints, 0) AS upoints_share,
       t.starts >= s.races AS in_field
FROM team_season t JOIN season_total s ON s.year = t.year JOIN season_utotal u ON u.year = t.year
WHERE t.starts > 0
"""


MAX_SQL = """
WITH pos_pts AS (
  SELECT race_id, position_number, MAX(points) AS pts,
         CASE position_number WHEN 1 THEN 25 WHEN 2 THEN 18 WHEN 3 THEN 15 WHEN 4 THEN 12 WHEN 5 THEN 10
              WHEN 6 THEN 8 WHEN 7 THEN 6 WHEN 8 THEN 4 WHEN 9 THEN 2 WHEN 10 THEN 1 ELSE 0 END AS upts
  FROM res WHERE position_number IS NOT NULL GROUP BY race_id, position_number
),
cars AS (
  SELECT year, constructor_id, race_id, SUM(started) AS k FROM res WHERE works = 1 GROUP BY 1, 2, 3
)
SELECT c.year, c.constructor_id,
       SUM((SELECT COALESCE(SUM(pts), 0)  FROM pos_pts p WHERE p.race_id = c.race_id AND p.position_number <= c.k)) AS max_pts,
       SUM((SELECT COALESCE(SUM(upts), 0) FROM pos_pts p WHERE p.race_id = c.race_id AND p.position_number <= c.k)) AS max_upts
FROM cars c GROUP BY 1, 2
"""


def _phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _field_stats(value, values):
    """z, z/sqrt(n-1) and percentile of `value` within `values` (population sd)."""
    n = len(values)
    mean = sum(values) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in values) / n)
    z = (value - mean) / sd if sd else None
    return {"x_avg": value / mean if mean else None, "z": z,
            "z_scaled": z / math.sqrt(n - 1) if z is not None and n > 1 else None,
            "z_cdf": 100 * _phi(z) if z is not None else None,
            "pct": 100 * sum(1 for x in values if x < value) / (n - 1) if n > 1 else None}


def _with_max(conn, rows):
    mx = {(r["year"], r["constructor_id"]): (r["max_pts"], r["max_upts"]) for r in conn.execute(MAX_SQL)}
    for r in rows:
        m = mx.get((r["year"], r["constructor_id"]), (0, 0))
        r["share_of_max"] = r["points"] / m[0] if m[0] else None
        r["som_uni"] = r["upoints"] / m[1] if m[1] else None
    return rows


def car_ratings(conn, cid, y0, y1):
    all_rows = _with_max(conn, [dict(r) for r in conn.execute(CAR_SQL)])
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
            fs = _field_stats(sh, shares)
            d.update({"x_avg": fs["x_avg"], "z": fs["z"], "z_scaled": fs["z_scaled"], "z_cdf": fs["z_cdf"],
                      "pct_field": fs["pct"]})
            us = _field_stats(d["upoints_share"] or 0, [r["upoints_share"] or 0 for r in field])
            d["zs_uni"] = us["z_scaled"]
        else:   # part-time entry: not in the field
            for k in ("x_avg", "z", "z_scaled", "z_cdf", "pct_field", "zs_uni"):
                d[k] = None
        out.append(d)
    return out


def dominant_report(conn):
    """Most dominant car of each decade by z_scaled, to check the scale does not trend with era."""
    rows = _with_max(conn, [dict(r) for r in conn.execute(CAR_SQL)])
    by_year = defaultdict(list)
    for r in rows:
        by_year[r["year"]].append(r)
    best = {}
    best_som = {}
    for r in rows:
        if not r["in_field"] or r["year"] > f1pool.LAST_COMPLETE_SEASON:
            continue
        field = [x for x in by_year[r["year"]] if x["in_field"]]
        fs = _field_stats(r["points_share"] or 0, [x["points_share"] or 0 for x in field])
        us = _field_stats(r["upoints_share"] or 0, [x["upoints_share"] or 0 for x in field])
        d = (r["year"] // 10) * 10
        row = {"decade": f"{d}s", "year": r["year"], "constructor": r["constructor_id"], "n_field": len(field),
               "wins": r["wins"], "races": r["races"], "cars_per_race": r["starts"] / r["races_entered"],
               "points_share": 100 * (r["points_share"] or 0),
               "z": fs["z"], "z_scaled": fs["z_scaled"], "z_cdf": fs["z_cdf"], "zs_uni": us["z_scaled"],
               "share_of_max": r["share_of_max"], "som_uni": r["som_uni"]}
        if fs["z_scaled"] is not None and (d not in best or fs["z_scaled"] > best[d]["z_scaled"]):
            best[d] = row
        if r["som_uni"] is not None and (d not in best_som or r["som_uni"] > best_som[d]["som_uni"]):
            best_som[d] = row
    cols = [("decade", "Decade", "s", "l"), ("year", "Season", "s", "r"), ("constructor", "Constructor", "s", "l"),
            ("n_field", "Field", "s", "r"), ("cars_per_race", "Cars/race", "avg", "r"), ("wins", "Wins", "s", "r"),
            ("races", "Races", "s", "r"), ("points_share", "Share%", "avg", "r"), ("z", "z", "avg", "r"),
            ("z_scaled", "z_scaled", "avg", "r"), ("z_cdf", "z_cdf", "avg", "r"), ("zs_uni", "zs_uni", "avg", "r"),
            ("share_of_max", "shareMax", "avg", "r"), ("som_uni", "somUni", "avg", "r")]
    print("Most dominant car of each decade by z_scaled (field = full-time works teams)")
    print_table([best[d] for d in sorted(best)], cols)
    print("\nMost dominant car of each decade by som_uni")
    print_table([best_som[d] for d in sorted(best_som)], cols)


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
    ap.add_argument("team", nargs="?", default="ferrari")
    ap.add_argument("decade", nargs="?", default="2000s")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--prior", type=int, default=RATING_PRIOR_RACES, help="pseudo-races in the prior")
    ap.add_argument("--floor", type=int, default=MIN_H2H_RACES, help="min classified h2h races to show a rating")
    ap.add_argument("--prior-sweep", action="store_true", help="show ratings under several prior strengths")
    ap.add_argument("--coverage", action="store_true", help="rating coverage by decade for several floors (ignores team/decade)")
    ap.add_argument("--dominant", action="store_true", help="most dominant car per decade on each car scale (ignores team/decade)")
    ap.add_argument("--db", default=f1pool.DB_PATH)
    args = ap.parse_args()
    conn = f1pool.connect(args.db)

    if args.coverage:
        coverage_report(conn, args.prior)
        return
    if args.dominant:
        dominant_report(conn)
        return

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
    print("\nCARS  raw team results, old vs-best, then field-relative (field = full-time works teams); som_uni is the recommended card rating")
    print_table(cars, [
        ("year", "Season", "s", "r"), ("card", "Card", "s", "l"),
        ("starts", "Starts", "s", "r"), ("wins", "Wins", "s", "r"), ("podiums", "Pod", "s", "r"), ("poles", "Poles", "s", "r"),
        ("avg_finish", "AvgFin", "avg", "r"), ("finish_rate", "FinRate", "pct", "r"),
        ("points", "Pts", "s", "r"), ("points_share", "Share%", "avg", "r"),
        ("vs_best", "vsBest", "avg", "r"), ("n_all", "All", "s", "r"),
        ("n_field", "Field", "s", "r"), ("x_avg", "xAvg", "avg", "r"), ("z", "z", "avg", "r"),
        ("z_scaled", "z_scaled", "avg", "r"), ("z_cdf", "z_cdf", "avg", "r"), ("zs_uni", "zs_uni", "avg", "r"),
        ("pct_field", "PctField", "avg", "r"), ("share_of_max", "shareMax", "avg", "r"), ("som_uni", "somUni", "avg", "r")])


if __name__ == "__main__":
    main()
