"""
Shared eligibility layer for 24-0. Every script reads F1DB through this module so the
pool rules are applied in exactly one place.

Rules (see docs/DATA.md):
  * window: FIRST_SEASON..LAST_COMPLETE_SEASON only; in-progress seasons are excluded
  * Indianapolis 500 results are dropped everywhere
  * works entries only; each race result is mapped to its entrant via season_entrant_driver
  * a driver needs MIN_STARTS_PER_DRIVER starts for a constructor in a decade to be in the pool
  * one car card per constructor-season; F1DB cannot say which chassis was primary when a
    team ran more than one, so such cards carry every chassis name
Nothing here estimates or fills in a statistic; rows are filtered, never altered.
"""
import os
import re
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "f1db", "f1db.db")

# ---- configuration -------------------------------------------------------------------
FIRST_SEASON = 1950
LAST_COMPLETE_SEASON = 2025      # re-cut deliberately in the off-season
EXCLUDED_GRANDS_PRIX = ("indianapolis",)   # Indy 500, 1950-1960
MIN_STARTS_PER_DRIVER = 3        # backstop after the works filter
POOL_MIN_DRIVERS = 2             # playable floor
POOL_MIN_CARS = 1
WHEEL_WEIGHT_DRIVERS = 4         # combos at/above this get the higher wheel weight (not a gate)
RATING_PRIOR_RACES = 10          # driver rating shrinkage: pseudo-races at 50% (data implies ~7; see docs)
# A driver rating needs at least this many classified races with a teammate. Tied to the
# pool minimum: a driver at the 3-start minimum is rated if compared in 2 of those 3, and
# no single race can ever set a rating. Shrinkage, not the floor, limits small samples.
MIN_H2H_RACES = MIN_STARTS_PER_DRIVER - 1
NOT_STARTED = ("DNS", "DNQ", "DNPQ", "DNP", "EX")

# Works-entrant overrides for constructor-seasons where the name rule cannot decide.
# (constructor_id, entrant_id) -> True (works) / False (privateer). Reviewable, not data.
WORKS_OVERRIDES = {
    ("brm", "owen-racing-organisation"): True,
    ("brabham", "motor-racing-developments"): True,
    ("brabham", "martini-racing"): True,
    ("mclaren", "marlboro-team-texaco"): True,
    ("eagle", "anglo-american-racers"): True,
    ("talbot-lago", "automobiles-talbot-darracq"): True,
    ("hwm", "hw-motors"): True,
    ("simca-gordini", "equipe-gordini"): True,
    ("gordini", "equipe-gordini"): True,
    ("vanwall", "vandervell-products"): True,
    ("mercedes", "daimler-benz-ag"): True,
    ("ferrari", "north-american-racing-team"): True,   # works cars entered under NART, 1964 US/Mexico
    ("hesketh", "penthouse-rizla-racing"): True,          # Keegan's works Hesketh, 1977
    ("march", "team-rothmans-international"): True,       # works March, 1977
    ("williams", "ram-williams-grand-prix-engineering"): False,
}


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _name_matches(constructor_id, constructor_name, entrant_name):
    en = _norm(entrant_name)
    words = en.split()
    toks = [t for t in _norm(constructor_name).split() if len(t) > 2] or _norm(constructor_name).split()
    if toks and all(t in words for t in toks):
        return True
    return constructor_id.replace("-", " ") in en


def _rounds(text, all_rounds):
    if not text:
        return set(all_rounds)
    out = set()
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out


def decade_range(text):
    """'2000s' -> (2000, 2009) clipped to the window; '1990-1999' also accepted."""
    m = re.fullmatch(r"(\d{4})s?", text)
    if m:
        y0 = int(m.group(1)) // 10 * 10
        y1 = y0 + 9
    else:
        m = re.fullmatch(r"(\d{4})-(\d{4})", text)
        if not m:
            raise ValueError(f"decade must look like 2000s or 2000-2009, got {text!r}")
        y0, y1 = int(m.group(1)), int(m.group(2))
    return max(y0, FIRST_SEASON), min(y1, LAST_COMPLETE_SEASON)


def connect(db_path=DB_PATH):
    """Open F1DB read-only and build the eligibility tables in a temp schema."""
    if not os.path.exists(db_path):
        raise SystemExit(f"Database not found at {db_path}. Run scripts/fetch_data.sh first.")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, factory=PoolConnection)
    conn.row_factory = sqlite3.Row
    _build(conn)
    return conn


class PoolConnection(sqlite3.Connection):
    """sqlite3.Connection plus the mapping diagnostics collected while building."""
    unmapped_results = ()
    ambiguous_results = ()


def _build(conn):
    c = conn.cursor()
    ph = ",".join("?" * len(NOT_STARTED))
    exgp = ",".join("?" * len(EXCLUDED_GRANDS_PRIX))

    # 1. every race result in the window, Indy excluded, with qualifying position joined
    c.execute(f"""
      CREATE TEMP TABLE res AS
      SELECT d.race_id, r.year, r.round, r.grand_prix_id, d.driver_id, d.constructor_id,
             d.position_number, d.position_text,
             d.position_text NOT IN ({ph}) AS started,
             d.position_number IS NOT NULL AS classified,
             COALESCE(d.race_points, 0) AS points,
             COALESCE(d.race_pole_position, 0) AS pole,
             COALESCE(d.race_shared_car, 0) AS shared,
             d.race_reason_retired AS reason_retired,
             (SELECT MIN(q.position_number) FROM race_data q
               WHERE q.race_id = d.race_id AND q.driver_id = d.driver_id
                 AND q.type = 'QUALIFYING_RESULT') AS quali_pos,
             NULL AS entrant_id, 0 AS works
      FROM race_data d JOIN race r ON r.id = d.race_id
      WHERE d.type = 'RACE_RESULT'
        AND r.year BETWEEN ? AND ?
        AND r.grand_prix_id NOT IN ({exgp})
    """, (*NOT_STARTED, FIRST_SEASON, LAST_COMPLETE_SEASON, *EXCLUDED_GRANDS_PRIX))
    c.execute("CREATE INDEX temp.res_idx ON res(year, constructor_id, driver_id, round)")

    # 2. map each result to an entrant via season_entrant_driver round ranges
    rounds_by_year = {}
    for y, rd in c.execute("SELECT year, round FROM race WHERE year BETWEEN ? AND ?",
                           (FIRST_SEASON, LAST_COMPLETE_SEASON)):
        rounds_by_year.setdefault(y, set()).add(rd)
    entries = c.execute("""
      SELECT year, constructor_id, driver_id, entrant_id, rounds_text FROM season_entrant_driver
      WHERE year BETWEEN ? AND ?""", (FIRST_SEASON, LAST_COMPLETE_SEASON)).fetchall()
    emap = {}
    for y, cid, did, eid, rt in entries:
        for rd in _rounds(rt, rounds_by_year.get(y, ())):
            emap.setdefault((y, cid, did, rd), set()).add(eid)
    unmapped = []
    ambiguous = []
    updates = []
    for row in c.execute("SELECT rowid, year, constructor_id, driver_id, round FROM res").fetchall():
        key = tuple(row[1:])
        ents = emap.get(key)
        if not ents:
            # driver listed for the constructor that season but not for this round: fall back
            ents = {e for (y, cid, did, rd), es in emap.items() if (y, cid, did) == key[:3] for e in es}
            if not ents:
                unmapped.append(key)
                continue
        if len(ents) > 1:
            ambiguous.append((key, sorted(ents)))
        updates.append((sorted(ents)[0], row[0]))
    c.executemany("UPDATE res SET entrant_id = ? WHERE rowid = ?", updates)
    conn.unmapped_results = unmapped
    conn.ambiguous_results = ambiguous

    # 3. works decision per constructor-season-entrant
    c.execute("""
      CREATE TEMP TABLE works_decision (
        year INT, constructor_id TEXT, entrant_id TEXT, entrant_name TEXT,
        n_entrants INT, starts INT, is_works INT, rule TEXT)""")
    cons = {r[0]: r[1] for r in c.execute("SELECT id, name FROM constructor")}
    ent_names = {r[0]: r[1] for r in c.execute("SELECT id, name FROM entrant")}
    groups = {}
    for y, cid, eid, n in c.execute("""
        SELECT year, constructor_id, entrant_id, SUM(started) FROM res
        WHERE entrant_id IS NOT NULL GROUP BY 1, 2, 3"""):
        groups.setdefault((y, cid), []).append((eid, n))
    decisions = []
    for (y, cid), ents in groups.items():
        for eid, n in ents:
            ov = WORKS_OVERRIDES.get((cid, eid))
            if ov is not None:
                w, rule = ov, "override"
            elif len(ents) == 1:
                w, rule = True, "sole entrant"
            elif _name_matches(cid, cons[cid], ent_names.get(eid, eid)):
                w, rule = True, "name match"
            else:
                w, rule = False, "no match"
            decisions.append((y, cid, eid, ent_names.get(eid, eid), len(ents), n, int(w), rule))
    c.executemany("INSERT INTO works_decision VALUES (?,?,?,?,?,?,?,?)", decisions)
    c.execute("""
      UPDATE res SET works = 1 WHERE EXISTS (
        SELECT 1 FROM works_decision w WHERE w.year = res.year AND w.constructor_id = res.constructor_id
          AND w.entrant_id = res.entrant_id AND w.is_works = 1)""")

    # 4. one car card per constructor-season (works entrants' chassis)
    c.execute("""
      CREATE TEMP TABLE car AS
      SELECT sec.year, sec.constructor_id,
             GROUP_CONCAT(DISTINCT sec.chassis_id) AS chassis_ids
      FROM season_entrant_chassis sec
      JOIN works_decision w ON w.year = sec.year AND w.constructor_id = sec.constructor_id
                            AND w.entrant_id = sec.entrant_id AND w.is_works = 1
      GROUP BY sec.year, sec.constructor_id""")
    conn.commit()


def works_review_rows(conn):
    return conn.execute("""
      SELECT w.*, c.name AS constructor FROM works_decision w JOIN constructor c ON c.id = w.constructor_id
      ORDER BY w.year, w.constructor_id, w.is_works DESC, w.starts DESC""").fetchall()


def resolve_constructor(conn, team):
    row = conn.execute("SELECT id, name FROM constructor WHERE id = ?", (team,)).fetchone()
    if row:
        return row
    rows = conn.execute("SELECT id, name FROM constructor WHERE lower(name) = lower(?)", (team,)).fetchall()
    if len(rows) != 1:
        rows = conn.execute("SELECT id, name FROM constructor WHERE lower(name) LIKE lower(?) ORDER BY name",
                            (f"%{team}%",)).fetchall()
    if len(rows) == 1:
        return rows[0]
    if not rows:
        raise SystemExit(f"No constructor matches {team!r}")
    raise SystemExit(f"Ambiguous team {team!r}; candidates: " + ", ".join(r[0] for r in rows))


DRIVER_SQL = """
SELECT res.driver_id, dr.name AS driver,
       MIN(year) AS first_year, MAX(year) AS last_year,
       COUNT(DISTINCT race_id) AS entries,
       COUNT(DISTINCT CASE WHEN started THEN race_id END) AS starts,
       COUNT(DISTINCT CASE WHEN position_number = 1 THEN race_id END) AS wins,
       COUNT(DISTINCT CASE WHEN position_number <= 3 THEN race_id END) AS podiums,
       COUNT(DISTINCT CASE WHEN pole THEN race_id END) AS poles,
       COUNT(DISTINCT CASE WHEN classified THEN race_id END) AS classified,
       AVG(position_number) AS avg_finish,
       SUM(points) AS points
FROM res JOIN driver dr ON dr.id = res.driver_id
WHERE constructor_id = ? AND year BETWEEN ? AND ? AND works = 1
GROUP BY res.driver_id, dr.name
HAVING starts >= ?
ORDER BY starts DESC, wins DESC, driver
"""


def drivers_for(conn, constructor_id, y0, y1, min_starts=MIN_STARTS_PER_DRIVER):
    rows = [dict(r) for r in conn.execute(DRIVER_SQL, (constructor_id, y0, y1, min_starts))]
    for d in rows:
        d["finish_rate"] = d["classified"] / d["starts"] if d["starts"] else None
        if d["classified"] == 0:
            d["avg_finish"] = None
    return rows


def cars_for(conn, constructor_id, y0, y1):
    """One card per constructor-season with a works start. `name` joins every chassis the
    works team entered that season (e.g. 'Ferrari F2001B / F2002'); the data holds no
    per-race chassis, so no single 'primary' chassis is asserted."""
    names = dict(conn.execute("SELECT id, full_name FROM chassis"))
    rows = []
    for r in conn.execute("""
        SELECT car.year, car.chassis_ids FROM car
        WHERE car.constructor_id = ? AND car.year BETWEEN ? AND ?
          AND EXISTS (SELECT 1 FROM res WHERE res.constructor_id = car.constructor_id
                      AND res.year = car.year AND res.works = 1 AND res.started)
        ORDER BY car.year""", (constructor_id, y0, y1)):
        ids = sorted(r["chassis_ids"].split(","))
        full = [names.get(i, i) for i in ids]
        # 'Ferrari F2001B / F2002': keep the make once, then the model names
        make = full[0].split(" ")[0]
        models = [f[len(make) + 1:] if f.startswith(make + " ") else f for f in full]
        rows.append({"year": r["year"], "chassis_ids": ids, "name": f"{make} " + " / ".join(models)})
    return rows
