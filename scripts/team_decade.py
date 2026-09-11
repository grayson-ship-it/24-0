#!/usr/bin/env python3
"""
Team x Decade lookup against the local F1DB SQLite database.

    python3 scripts/team_decade.py mclaren 2000s
    python3 scripts/team_decade.py mclaren 2000-2009 --json

Returns every driver with at least one Grand Prix entry for the constructor in the
window, with stats aggregated over the whole tenure (one pass over race results,
never an average of per-season figures), plus every chassis the team entered.

Data rules
----------
* Every number is read straight from F1DB `race_data` rows of type RACE_RESULT.
  Nothing is estimated or back-filled. A stat that cannot be computed (e.g. an
  average over zero classified finishes) is printed blank.
* Counts are per distinct race, so a 1950s shared drive (two rows for one driver in
  one race) counts once. This matches F1DB's own per-driver career totals exactly.
* Definitions (see docs/DATA.md for the full rationale):
    entries    RACE_RESULT rows for driver+constructor, distinct races
    starts     entries whose position_text is not DNS/DNQ/DNPQ/DNP/EX
    wins       position_number = 1
    podiums    position_number in 1..3
    poles      race_pole_position flag on the race-result row
    classified position_number is not null (official classification; includes
               drivers classified despite retiring late in the race)
    avg_finish mean of position_number over classified results
    finish_rate classified / starts
* Sprint races are excluded (they are a separate type, SPRINT_RACE_RESULT).
"""
import argparse
import json
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "f1db", "f1db.db")

NOT_STARTED = ("DNS", "DNQ", "DNPQ", "DNP", "EX")

DRIVER_SQL = f"""
SELECT
  d.driver_id,
  dr.name                                                         AS driver,
  MIN(r.year)                                                     AS first_year,
  MAX(r.year)                                                     AS last_year,
  COUNT(DISTINCT r.id)                                            AS entries,
  COUNT(DISTINCT CASE WHEN d.position_text NOT IN ({",".join("?" * len(NOT_STARTED))})
                      THEN r.id END)                              AS starts,
  COUNT(DISTINCT CASE WHEN d.position_number = 1  THEN r.id END)  AS wins,
  COUNT(DISTINCT CASE WHEN d.position_number <= 3 THEN r.id END)  AS podiums,
  COUNT(DISTINCT CASE WHEN d.race_pole_position = 1 THEN r.id END) AS poles,
  COUNT(DISTINCT CASE WHEN d.position_number IS NOT NULL THEN r.id END) AS classified,
  AVG(d.position_number)                                          AS avg_finish
FROM race_data d
JOIN race   r  ON r.id = d.race_id
JOIN driver dr ON dr.id = d.driver_id
WHERE d.type = 'RACE_RESULT'
  AND d.constructor_id = ?
  AND r.year BETWEEN ? AND ?
GROUP BY d.driver_id, dr.name
ORDER BY starts DESC, wins DESC, driver
"""

CHASSIS_SQL = """
SELECT c.id AS chassis_id, c.full_name AS chassis,
       MIN(sec.year) AS first_year, MAX(sec.year) AS last_year
FROM season_entrant_chassis sec
JOIN chassis c ON c.id = sec.chassis_id
WHERE sec.constructor_id = ? AND sec.year BETWEEN ? AND ?
GROUP BY c.id, c.full_name
ORDER BY first_year, c.full_name
"""


def parse_decade(text):
    """'2000s' -> (2000, 2009); '2000-2009' -> (2000, 2009); '2000' -> (2000, 2009)."""
    m = re.fullmatch(r"(\d{4})s?", text)
    if m:
        start = int(m.group(1)) // 10 * 10
        return start, start + 9
    m = re.fullmatch(r"(\d{4})-(\d{4})", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    raise argparse.ArgumentTypeError(f"decade must look like 2000s or 2000-2009, got {text!r}")


def resolve_constructor(conn, team):
    """Accept an F1DB constructor id ('mclaren') or an exact/partial name ('McLaren')."""
    row = conn.execute("SELECT id, name, full_name FROM constructor WHERE id = ?", (team,)).fetchone()
    if row:
        return row
    rows = conn.execute(
        "SELECT id, name, full_name FROM constructor WHERE lower(name) = lower(?) OR lower(full_name) = lower(?)",
        (team, team),
    ).fetchall()
    if len(rows) == 1:
        return rows[0]
    rows = conn.execute(
        "SELECT id, name, full_name FROM constructor WHERE lower(name) LIKE lower(?) ORDER BY name",
        (f"%{team}%",),
    ).fetchall()
    if len(rows) == 1:
        return rows[0]
    if not rows:
        sys.exit(f"No constructor matches {team!r}")
    sys.exit(f"Ambiguous team {team!r}; candidates: " + ", ".join(r[0] for r in rows))


def query(conn, constructor_id, y0, y1):
    drivers = [
        dict(row)
        for row in conn.execute(DRIVER_SQL, (*NOT_STARTED, constructor_id, y0, y1))
    ]
    for d in drivers:
        d["finish_rate"] = (d["classified"] / d["starts"]) if d["starts"] else None
        if d["classified"] == 0:
            d["avg_finish"] = None  # AVG over no rows is NULL already; be explicit
    chassis = [dict(row) for row in conn.execute(CHASSIS_SQL, (constructor_id, y0, y1))]
    return drivers, chassis


def fmt(v, kind):
    if v is None:
        return ""
    if kind == "avg":
        return f"{v:.2f}"
    if kind == "pct":
        return f"{100 * v:.1f}%"
    return str(v)


def print_table(rows, cols):
    """cols: list of (key, header, kind, align)."""
    cells = [[fmt(r.get(k), kind) for k, _, kind, _ in cols] for r in rows]
    widths = [max(len(h), *(len(c[i]) for c in cells)) if cells else len(h)
              for i, (_, h, _, _) in enumerate(cols)]
    def line(vals):
        return "  ".join(
            v.rjust(w) if a == "r" else v.ljust(w) for v, w, (_, _, _, a) in zip(vals, widths, cols)
        ).rstrip()
    print(line([h for _, h, _, _ in cols]))
    print(line(["-" * w for w in widths]))
    for c in cells:
        print(line(c))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("team", help="F1DB constructor id (e.g. mclaren) or name")
    ap.add_argument("decade", type=parse_decade, help="e.g. 2000s or 2000-2009")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    ap.add_argument("--db", default=DB_PATH, help=f"path to f1db.db (default {DB_PATH})")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        sys.exit(f"Database not found at {args.db}. Run scripts/fetch_data.sh first.")
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    cid, name, full_name = resolve_constructor(conn, args.team)
    y0, y1 = args.decade
    drivers, chassis = query(conn, cid, y0, y1)
    version_file = os.path.join(os.path.dirname(args.db), "VERSION")
    version = open(version_file).read().strip() if os.path.exists(version_file) else None

    if args.json:
        json.dump(
            {"constructor_id": cid, "constructor": name, "years": [y0, y1],
             "f1db_version": version, "drivers": drivers, "chassis": chassis},
            sys.stdout, indent=2,
        )
        print()
        return

    print(f"{name} ({cid}) {y0}-{y1}   source: F1DB {version or '?'}   race results only (no sprints)")
    print()
    print_table(drivers, [
        ("driver", "Driver", "s", "l"),
        ("first_year", "From", "s", "r"),
        ("last_year", "To", "s", "r"),
        ("entries", "Entries", "s", "r"),
        ("starts", "Starts", "s", "r"),
        ("wins", "Wins", "s", "r"),
        ("podiums", "Podiums", "s", "r"),
        ("poles", "Poles", "s", "r"),
        ("classified", "Classified", "s", "r"),
        ("avg_finish", "AvgFinish", "avg", "r"),
        ("finish_rate", "FinishRate", "pct", "r"),
    ])
    print()
    print("Cars (chassis entered by the team in the window):")
    print_table(chassis, [
        ("chassis", "Chassis", "s", "l"),
        ("chassis_id", "Id", "s", "l"),
        ("first_year", "From", "s", "r"),
        ("last_year", "To", "s", "r"),
    ])


if __name__ == "__main__":
    main()
