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
import bt  # noqa: E402
from team_decade import print_table, DRIVER_COLS  # noqa: E402

RATING_PRIOR_RACES = f1pool.RATING_PRIOR_RACES
MIN_H2H_RACES = f1pool.MIN_H2H_RACES
RACE_WEIGHT = f1pool.RACE_WEIGHT


def combine(race, quali, w=RACE_WEIGHT):
    """Weighted mix of the two components on the 0-100 scale; whichever exists if only one does."""
    if race is None and quali is None:
        return None
    if race is None:
        return quali
    if quali is None:
        return race
    return w * race + (1 - w) * quali

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


def _collect(rows):
    """Group pair rows into per-driver, per-component, per-race lists of (teammate, won)."""
    per = defaultdict(lambda: {"race": defaultdict(list), "quali": defaultdict(list), "mates": defaultdict(int)})
    for r in rows:
        a = per[r["driver_id"]]
        if r["pa"] is not None and r["pb"] is not None and r["pa"] != r["pb"]:
            a["race"][r["race_id"]].append((r["mate"], r["pa"] < r["pb"]))
            a["mates"][r["mate_name"] if "mate_name" in r.keys() else r["mate"]] += 1
        if r["qa"] is not None and r["qb"] is not None and r["qa"] != r["qb"]:
            a["quali"][r["race_id"]].append((r["mate"], r["qa"] < r["qb"]))
    return per


def global_strengths(conn, k=RATING_PRIOR_RACES):
    """Bradley-Terry strength per driver, per component, over every works teammate comparison
    in the window. Each race contributes one unit per driver (pairs weighted 1/(pairs in race))."""
    per = _collect(conn.execute(ALL_PAIR_SQL))
    out = {}
    for comp in ("race", "quali"):
        wins = defaultdict(float)
        for did, a in per.items():
            for race, comps in a[comp].items():
                w = 1.0 / len(comps)
                for mate, won in comps:
                    if won:
                        wins[(did, mate)] += w
        out[comp] = bt.fit(wins, k)
    return out


def _tenure(a, k, floor, w, strengths=None):
    """Rating fields for one driver tenure from its collected comparisons."""
    d = {"teammates": ", ".join(f"{m} {n}" for m, n in sorted(a["mates"].items(), key=lambda x: -x[1]))}
    for comp in ("race", "quali"):
        pairs = [won for v in a[comp].values() for _, won in v]
        pw, pl = sum(pairs), len(pairs) - sum(pairs)
        n = len(a[comp])
        wins = sum(sum(won for _, won in v) / len(v) for v in a[comp].values())
        d[f"{comp}_pairs"] = f"{pw}-{pl}" if pairs else ""
        d[f"{comp}_pair_pct"] = pw / len(pairs) if pairs else None
        d[f"{comp}_n"] = n
        d[f"{comp}_wins"] = wins
        d[f"{comp}_pct"] = wins / n if n else None
        d[f"{comp}_shrunk"] = shrink(wins, n, k) if n else None
        d[f"{comp}_bt"] = None
        if strengths is not None and n:
            by_opp = defaultdict(lambda: [0.0, 0.0])
            for v in a[comp].values():
                wt = 1.0 / len(v)
                for mate, won in v:
                    by_opp[mate][0] += wt * won
                    by_opp[mate][1] += wt
            s = bt.fit_one({m: tuple(x) for m, x in by_opp.items()}, strengths[comp], k)
            d[f"{comp}_bt"] = bt.to_rating(s)
            d[f"{comp}_opp"] = sum(bt.to_rating(strengths[comp].get(m, 1.0)) * x[1] for m, x in by_opp.items()) / n
    d["old_rating"] = combine(*(100 * x if x is not None else None for x in (d["race_pair_pct"], d["quali_pair_pct"])), w)
    ok = d["race_n"] >= floor
    d["rating"] = combine(*(100 * x if x is not None else None for x in (d["race_shrunk"], d["quali_shrunk"])), w) if ok else None
    d["bt_rating"] = combine(d["race_bt"], d["quali_bt"], w) if ok else None
    d["rating_note"] = "" if ok else (f"n<{floor}" if d["race_n"] or d["quali_n"] else "no teammate")
    return d


def driver_ratings(conn, cid, y0, y1, k=RATING_PRIOR_RACES, floor=MIN_H2H_RACES, w=RACE_WEIGHT, strengths=None):
    per = _collect(conn.execute(PAIR_SQL, (cid, y0, y1)))
    return {did: _tenure(a, k, floor, w, strengths) for did, a in per.items()}


ALL_PAIR_SQL = """
SELECT a.driver_id, b.driver_id AS mate, a.constructor_id, (a.year/10)*10 AS dec, a.race_id,
       a.position_number AS pa, b.position_number AS pb, a.quali_pos AS qa, b.quali_pos AS qb
FROM res a
JOIN res b ON b.race_id = a.race_id AND b.constructor_id = a.constructor_id
          AND b.driver_id <> a.driver_id AND b.works = 1 AND b.shared = 0
WHERE a.works = 1 AND a.shared = 0
"""


def all_driver_ratings(conn, k=RATING_PRIOR_RACES, floor=MIN_H2H_RACES, w=RACE_WEIGHT, strengths=None):
    """Rating fields for every pool tenure (driver x constructor x decade with >= MIN_STARTS)."""
    per = defaultdict(lambda: {"race": defaultdict(list), "quali": defaultdict(list), "mates": defaultdict(int)})
    for r in conn.execute(ALL_PAIR_SQL):
        key = (r["driver_id"], r["constructor_id"], r["dec"])
        a = per[key]
        if r["pa"] is not None and r["pb"] is not None and r["pa"] != r["pb"]:
            a["race"][r["race_id"]].append((r["mate"], r["pa"] < r["pb"]))
            a["mates"][r["mate"]] += 1
        if r["qa"] is not None and r["qb"] is not None and r["qa"] != r["qb"]:
            a["quali"][r["race_id"]].append((r["mate"], r["qa"] < r["qb"]))
    out = []
    for p in conn.execute("""
        SELECT r.driver_id, dr.name AS driver, r.constructor_id, (r.year/10)*10 AS dec,
               COUNT(DISTINCT CASE WHEN started THEN race_id END) AS starts,
               COUNT(DISTINCT CASE WHEN classified THEN race_id END) AS classified
        FROM res r JOIN driver dr ON dr.id = r.driver_id WHERE works = 1
        GROUP BY 1, 2, 3, 4 HAVING starts >= ?""", (f1pool.MIN_STARTS_PER_DRIVER,)):
        key = (p["driver_id"], p["constructor_id"], p["dec"])
        a = per.get(key) or {"race": {}, "quali": {}, "mates": {}}
        d = dict(p)
        d.update(_tenure(a, k, floor, w, strengths))
        out.append(d)
    return out


def coverage_report(conn, k, floors=(1, 2, 3, 5), w=RACE_WEIGHT):
    base = all_driver_ratings(conn, k, 0, w)
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


EMPTY = _tenure({"race": {}, "quali": {}, "mates": {}}, RATING_PRIOR_RACES, MIN_H2H_RACES, RACE_WEIGHT)

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


def _era_relative(all_rows):
    """Within each decade, percentile and rank of som_uni among full-time works cards."""
    by_dec = defaultdict(list)
    for r in all_rows:
        if r["in_field"] and r["som_uni"] is not None and r["year"] <= f1pool.LAST_COMPLETE_SEASON:
            by_dec[(r["year"] // 10) * 10].append(r["som_uni"])
    for r in all_rows:
        vals = by_dec.get((r["year"] // 10) * 10, [])
        if r["in_field"] and r["som_uni"] is not None and len(vals) > 1:
            r["era_pct"] = 100 * sum(1 for x in vals if x < r["som_uni"]) / (len(vals) - 1)
            r["era_rank"] = 1 + sum(1 for x in vals if x > r["som_uni"])
            r["era_n"] = len(vals)
        else:
            r["era_pct"] = r["era_rank"] = r["era_n"] = None
    return all_rows


def car_ratings(conn, cid, y0, y1):
    all_rows = _era_relative(_with_max(conn, [dict(r) for r in conn.execute(CAR_SQL)]))
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
        d["era_rank_txt"] = f"{d['era_rank']}/{d['era_n']}" if d.get("era_rank") else ""
        out.append(d)
    return out


def dominant_report(conn):
    """Most dominant car of each decade by z_scaled, to check the scale does not trend with era."""
    rows = _era_relative(_with_max(conn, [dict(r) for r in conn.execute(CAR_SQL)]))
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
               "share_of_max": r["share_of_max"], "som_uni": r["som_uni"],
               "era_pct": r["era_pct"], "era_rank_txt": f"{r['era_rank']}/{r['era_n']}" if r.get("era_rank") else ""}
        if fs["z_scaled"] is not None and (d not in best or fs["z_scaled"] > best[d]["z_scaled"]):
            best[d] = row
        if r["som_uni"] is not None and (d not in best_som or r["som_uni"] > best_som[d]["som_uni"]):
            best_som[d] = row
    cols = [("decade", "Decade", "s", "l"), ("year", "Season", "s", "r"), ("constructor", "Constructor", "s", "l"),
            ("n_field", "Field", "s", "r"), ("cars_per_race", "Cars/race", "avg", "r"), ("wins", "Wins", "s", "r"),
            ("races", "Races", "s", "r"), ("points_share", "Share%", "avg", "r"), ("z", "z", "avg", "r"),
            ("z_scaled", "z_scaled", "avg", "r"), ("z_cdf", "z_cdf", "avg", "r"), ("zs_uni", "zs_uni", "avg", "r"),
            ("share_of_max", "shareMax", "avg", "r"), ("som_uni", "somUni", "avg", "r"),
            ("era_pct", "eraPct", "avg", "r"), ("era_rank_txt", "eraRank", "s", "r")]
    print("Most dominant car of each decade by z_scaled (field = full-time works teams)")
    print_table([best[d] for d in sorted(best)], cols)
    print("\nMost dominant car of each decade by som_uni")
    print_table([best_som[d] for d in sorted(best_som)], cols)


def load(conn, team, decade, k=RATING_PRIOR_RACES, floor=MIN_H2H_RACES, w=RACE_WEIGHT, strengths=None):
    cid, name = f1pool.resolve_constructor(conn, team)
    y0, y1 = f1pool.decade_range(decade)
    drivers = f1pool.drivers_for(conn, cid, y0, y1)
    dr = driver_ratings(conn, cid, y0, y1, k, floor, w, strengths)
    for d in drivers:
        d.update(dr.get(d["driver_id"], EMPTY))
    return cid, name, y0, y1, drivers, car_ratings(conn, cid, y0, y1)


CHAMPION_SQL = """
SELECT s.year, s.constructor_id, s.points AS standing_points,
       (SELECT SUM(COALESCE(d.race_points, 0)) FROM race_data d JOIN race r ON r.id = d.race_id
         WHERE d.type = 'RACE_RESULT' AND r.year = s.year AND d.constructor_id = s.constructor_id) AS race_points_sum
FROM season_constructor_standing s WHERE s.position_number = 1 AND s.year BETWEEN ? AND ?
"""


def champions_report(conn):
    """Top car by som_uni vs the actual constructors' champion, every season 1958-window end.
    1950-1957 had no constructors' championship; the drivers' champion's team is shown as a proxy."""
    rows = _with_max(conn, [dict(r) for r in conn.execute(CAR_SQL)])
    by_year = defaultdict(list)
    for r in rows:
        by_year[r["year"]].append(r)
    champ = {r["year"]: dict(r) for r in conn.execute(CHAMPION_SQL, (f1pool.FIRST_SEASON, f1pool.LAST_COMPLETE_SEASON))}
    proxy = {}
    for r in conn.execute("""
        SELECT s.year, s.driver_id FROM season_driver_standing s
        WHERE s.position_number = 1 AND s.year BETWEEN ? AND 1957""", (f1pool.FIRST_SEASON,)):
        c = conn.execute("""SELECT constructor_id FROM res WHERE year = ? AND driver_id = ? AND works = 1
                            GROUP BY constructor_id ORDER BY SUM(started) DESC LIMIT 1""", (r["year"], r["driver_id"])).fetchone()
        proxy[r["year"]] = c[0] if c else None
    agree = disagree = 0
    out = []
    for y in sorted(by_year):
        if y > f1pool.LAST_COMPLETE_SEASON:
            continue
        top = max(by_year[y], key=lambda r: r["som_uni"] or 0)
        c = champ.get(y)
        if c:
            cid = c["constructor_id"]
            rule = "" if abs((c["standing_points"] or 0) - (c["race_points_sum"] or 0)) < 0.01 else "scoring rule"
        else:
            cid = proxy.get(y)
            rule = "no constructors' title (drivers' champion's team)"
        if cid == top["constructor_id"]:
            agree += 1
            continue
        disagree += 1
        ch = next((r for r in by_year[y] if r["constructor_id"] == cid), None)
        out.append({"year": y, "top": top["constructor_id"], "top_som": top["som_uni"], "top_wins": top["wins"],
                    "top_pts": top["points"], "top_fin": top["classified"] / top["starts"] if top["starts"] else None,
                    "champ": cid, "ch_som": ch["som_uni"] if ch else None, "ch_wins": ch["wins"] if ch else None,
                    "ch_pts": ch["points"] if ch else None, "ch_fin": (ch["classified"] / ch["starts"]) if ch and ch["starts"] else None,
                    "rule": rule})
    print(f"Top car by som_uni vs constructors' champion: agree {agree}, disagree {disagree}")
    print("'scoring rule' = the champion's standing points differ from its summed race points (dropped scores, "
          "best-car-only 1958-78, 1995 Brazil exclusion, sprint points 2021+)")
    print_table(out, [("year", "Season", "s", "r"), ("top", "Top by som_uni", "s", "l"), ("top_som", "som", "avg", "r"),
                      ("top_wins", "W", "s", "r"), ("top_pts", "Pts", "s", "r"), ("top_fin", "Fin", "pct", "r"),
                      ("champ", "Champion", "s", "l"), ("ch_som", "som", "avg", "r"), ("ch_wins", "W", "s", "r"),
                      ("ch_pts", "Pts", "s", "r"), ("ch_fin", "Fin", "pct", "r"), ("rule", "Note", "s", "l")])


def movers_report(conn, k, w, n=15):
    strengths = global_strengths(conn, k)
    names = dict(conn.execute("SELECT id, name FROM driver"))
    print("Global Bradley-Terry strengths, top 20 by race component (career, works comparisons in window):")
    top = sorted(strengths["race"].items(), key=lambda x: -x[1])[:20]
    print_table([{"driver": names.get(d, d), "race": bt.to_rating(s), "quali": bt.to_rating(strengths["quali"].get(d, 1.0))}
                 for d, s in top], [("driver", "Driver", "s", "l"), ("race", "Race", "avg", "r"), ("quali", "Quali", "avg", "r")])
    rows = [d for d in all_driver_ratings(conn, k, MIN_H2H_RACES, w, strengths) if d["rating"] is not None and d["bt_rating"] is not None]
    for d in rows:
        d["move"] = d["bt_rating"] - d["rating"]
        d["dec_txt"] = f"{d['dec']}s"
    cols = [("driver", "Driver", "s", "l"), ("constructor_id", "Team", "s", "l"), ("dec_txt", "Decade", "s", "l"),
            ("race_pairs", "Race W-L", "s", "r"), ("race_n", "n", "s", "r"), ("quali_pairs", "Quali W-L", "s", "r"),
            ("race_opp", "Opp(race)", "avg", "r"), ("rating", "Unadj", "avg", "r"), ("bt_rating", "BT", "avg", "r"), ("move", "Move", "avg", "r")]
    print(f"\nBiggest movers UP (Bradley-Terry adjusted minus unadjusted), {len(rows)} rated tenures:")
    print_table(sorted(rows, key=lambda d: -d["move"])[:n], cols)
    print("\nBiggest movers DOWN:")
    print_table(sorted(rows, key=lambda d: d["move"])[:n], cols)


def weights_report(conn, team, decade, k, strengths):
    ws = (1.0, 0.75, 0.5, 0.25)
    cid, name, y0, y1, drivers, _ = load(conn, team, decade, k, MIN_H2H_RACES, 0.5, strengths)
    table = {d["driver_id"]: {"driver": d["driver"], "race_pairs": d["race_pairs"], "race_n": d["race_n"], "quali_pairs": d["quali_pairs"]}
             for d in drivers}
    for w in ws:
        _, _, _, _, ds, _ = load(conn, team, decade, k, MIN_H2H_RACES, w, strengths)
        for d in ds:
            table[d["driver_id"]][f"u{w}"] = d["rating"]
            table[d["driver_id"]][f"b{w}"] = d["bt_rating"]
    print(f"=== {name} {y0}-{y1}: rating by race weight (unadjusted | Bradley-Terry adjusted) ===")
    print_table(list(table.values()), [("driver", "Driver", "s", "l"), ("race_pairs", "Race W-L", "s", "r"), ("race_n", "n", "s", "r"),
                                       ("quali_pairs", "Quali W-L", "s", "r")]
                + [(f"u{w}", f"U {w:g}", "avg", "r") for w in ws] + [(f"b{w}", f"BT {w:g}", "avg", "r") for w in ws])


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
    ap.add_argument("--champions", action="store_true", help="top car by som_uni vs the constructors' champion, every season")
    ap.add_argument("--movers", action="store_true", help="Bradley-Terry adjustment: global top 20 and biggest movers")
    ap.add_argument("--weights", action="store_true", help="ratings of this combo under several race/quali weights")
    ap.add_argument("--race-weight", type=float, default=RACE_WEIGHT, help="weight of the race component (0..1)")
    ap.add_argument("--no-bt", action="store_true", help="skip the Bradley-Terry adjustment")
    ap.add_argument("--db", default=f1pool.DB_PATH)
    args = ap.parse_args()
    conn = f1pool.connect(args.db)

    if args.coverage:
        coverage_report(conn, args.prior, w=args.race_weight)
        return
    if args.dominant:
        dominant_report(conn)
        return
    if args.champions:
        champions_report(conn)
        return
    if args.movers:
        movers_report(conn, args.prior, args.race_weight)
        return
    strengths = None if args.no_bt else global_strengths(conn, args.prior)
    if args.weights:
        weights_report(conn, args.team, args.decade, args.prior, strengths)
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

    cid, name, y0, y1, drivers, cars = load(conn, args.team, args.decade, args.prior, args.floor, args.race_weight, strengths)
    if args.json:
        json.dump({"constructor_id": cid, "constructor": name, "years": [y0, y1],
                   "prior_races": args.prior, "min_h2h_races": args.floor,
                   "drivers": drivers, "cars": cars}, sys.stdout, indent=2)
        print()
        return

    print(f"=== {name} {y0}-{y1} ===   prior k={args.prior} races, floor {args.floor} classified h2h races, race weight {args.race_weight:g}")
    print("\nDRIVERS  raw (works entries, whole tenure)")
    print_table(drivers, DRIVER_COLS)
    print("\nDRIVERS  teammate head-to-head: per-pair W-L, per-race n; Unadj = shrunk, BT = Bradley-Terry teammate-adjusted; Opp = mean BT rating of race teammates")
    print_table(drivers, [
        ("driver", "Driver", "s", "l"),
        ("race_pairs", "Race W-L", "s", "r"), ("race_n", "n", "s", "r"),
        ("quali_pairs", "Quali W-L", "s", "r"), ("quali_n", "n", "s", "r"),
        ("race_shrunk", "Race~", "pct", "r"), ("quali_shrunk", "Quali~", "pct", "r"), ("rating", "Unadj", "avg", "r"),
        ("race_opp", "Opp", "avg", "r"), ("race_bt", "RaceBT", "avg", "r"), ("quali_bt", "QualiBT", "avg", "r"), ("bt_rating", "BT", "avg", "r"),
        ("rating_note", "", "s", "l"), ("teammates", "Race comparisons vs", "s", "l")])
    print("\nCARS  raw team results, old vs-best, then field-relative (field = full-time works teams); som_uni = ABSOLUTE (simulation), eraPct/eraRank = ERA-RELATIVE (card display)")
    print_table(cars, [
        ("year", "Season", "s", "r"), ("card", "Card", "s", "l"),
        ("starts", "Starts", "s", "r"), ("wins", "Wins", "s", "r"), ("podiums", "Pod", "s", "r"), ("poles", "Poles", "s", "r"),
        ("avg_finish", "AvgFin", "avg", "r"), ("finish_rate", "FinRate", "pct", "r"),
        ("points", "Pts", "s", "r"), ("points_share", "Share%", "avg", "r"),
        ("vs_best", "vsBest", "avg", "r"), ("n_all", "All", "s", "r"),
        ("n_field", "Field", "s", "r"), ("x_avg", "xAvg", "avg", "r"), ("z", "z", "avg", "r"),
        ("z_scaled", "z_scaled", "avg", "r"), ("z_cdf", "z_cdf", "avg", "r"), ("zs_uni", "zs_uni", "avg", "r"),
        ("pct_field", "PctField", "avg", "r"), ("share_of_max", "shareMax", "avg", "r"), ("som_uni", "somUni", "avg", "r"),
        ("era_pct", "eraPct", "avg", "r"), ("era_rank_txt", "eraRank", "s", "r")])


if __name__ == "__main__":
    main()
