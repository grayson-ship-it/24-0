# Data source and definitions

## What is live today (checked 2026-09-11)

| Source | Status | Notes |
|---|---|---|
| **Ergast** (ergast.com) | Shut down | Stopped updating after the 2024 season and went offline. Not reachable. |
| **Jolpica-F1** (api.jolpi.ca) | Live, community-run | Ergast-compatible REST API at `api.jolpi.ca/ergast/f1/`. Requires a custom User-Agent, is rate limited, and volunteer-hosted. Offers CSV database dumps at `api.jolpi.ca/data/dumps/download/`: the free tier is delayed 14 days and non-commercial only. No GitHub releases. Its schema has no chassis (car model) data, only constructors. |
| **F1DB** (github.com/f1db/f1db) | Live, releases after every race | CC BY 4.0. Versioned GitHub releases (CalVer `YYYY.RR.MICRO`) in CSV, JSON, SQLite and SQL formats with published SHA-256 checksums. Covers 1950 to present and includes **chassis per season entry**, which the Car pick needs. |

**Decision: F1DB is the source of record.** Jolpica has no chassis data and the Car pick
depends on it. F1DB is pinned to a specific release in `scripts/fetch_data.sh`.
The api.jolpi.ca domain was not reachable from the environment this was set up in, so
Jolpica's dump was not downloaded or inspected. F1DB was chosen on its own merits (chassis
data, pinned versioned releases, permissive licence) but that constraint is worth knowing.

Attribution required by the licence is in [ATTRIBUTION.md](../ATTRIBUTION.md). It must also
appear in the app UI once one exists.

## Layout

```
data/f1db/f1db.db           SQLite database (gitignored; run scripts/fetch_data.sh)
data/f1db/VERSION           F1DB release tag the local db came from
data/f1db/checksums_sha256.txt  checksums published with that release
scripts/fetch_data.sh       download + verify
scripts/team_decade.py      Team x Decade query
```

## Tables used

* `race_data` with `type = 'RACE_RESULT'`: one row per car per Grand Prix, with
  `driver_id`, `constructor_id`, `position_number` (null when unclassified),
  `position_text` (`DNF`, `DNS`, `DNQ`, `DNPQ`, `DNP`, `DSQ`, `NC`, `EX` or a number),
  `race_pole_position` flag, `race_laps`, `race_reason_retired`.
* `race`: `year`, `round`, `grand_prix_id`.
* `driver`, `constructor`, `chassis`.
* `season_entrant_chassis`: which chassis each constructor entered each season.
* `constructor_chronology`: team lineage (e.g. renames). **Deliberately unused.** Team
  lineages are not merged: Brawn stays Brawn, Jordan stays Jordan, Toleman stays Toleman.
  A one-season team like Brawn x 2000s is its own spin.

## Stat definitions (per driver, per constructor, over the whole window)

All counts are over **distinct races**. In the 1950s a driver could appear twice in one
race (shared car); that race counts once. Under this rule the per-driver all-time totals
computed here match F1DB's own `driver.total_race_entries / starts / wins / podiums /
pole_positions` columns for all 860 drivers with race results.

| Stat | Rule |
|---|---|
| entries | distinct races with a RACE_RESULT row |
| starts | entries where `position_text` is not `DNS`, `DNQ`, `DNPQ`, `DNP`, `EX` |
| wins | `position_number = 1` |
| podiums | `position_number` in 1..3 |
| poles | `race_pole_position = 1` on the race-result row |
| classified | `position_number` is not null (official classification) |
| avg_finish | mean of `position_number` over classified results |
| finish_rate | classified / starts |

Things to be aware of:

* **Average finish flatters drivers who retire.** It is computed over classified results
  only, so a retirement simply drops out of the average instead of hurting it. McLaren
  2000s: Räikkönen has the best average finish on the board (3.67) with a 66.7% finish
  rate; Hamilton's 4.89 came with a 90.4% finish rate. **Rule: average finish and finish
  rate always appear together on a card, and any rating built later must combine them.
  Average finish is never used as a quality measure on its own.**
* **Finish rate uses the official classification (decision).** "Saw the flag" was
  considered and rejected; classified / starts is the definition.
* **Classified retirements.** A driver who retires late but is still classified (e.g.
  Häkkinen, Spain 2001, clutch on the last lap, classified 9th) has a numeric position and
  a `race_reason_retired`. They count as classified and their position feeds avg_finish.
  That follows the official classification. If you want a "saw the flag" finish rate
  instead, exclude rows where `race_reason_retired` is not null.
* `DSQ` and `NC` count as starts but not as classified.
* `EX` (excluded, 15 rows all 1983 to 2000) has no laps or grid data, so it is treated as a
  non-start.
* Sprint races (2021 onward) are a separate `type` and are excluded from every stat.
* No number is estimated or filled in. If a stat cannot be computed it is left blank
  (e.g. avg_finish for a driver with zero classified results).

## Pool audit

`scripts/pool_audit.py` writes `data/audit/pool_audit.csv`: one row per constructor x
decade with distinct drivers who started, distinct chassis entered, and context columns.
Things the audit surfaces that are properties of the data, not choices made here:

* **F1DB's `constructor_id` on a race result is the chassis constructor, including
  privateer entries.** Maserati x 1950s therefore lists 79 starters, most of them private
  entrants, and Lotus x 1960s lists 71. The `entrant` tables distinguish works from
  private teams if that ever matters; the audit does not use them.
* **The Indianapolis 500 counted for the championship from 1950 to 1960.** It is a race
  in F1DB like any other, so US constructors such as Kurtis Kraft appear as 1950s combos
  built entirely from Indy 500 starts. The `indy500_races` column makes them filterable.
* **The 2020s are in progress** (data through 2026 round 13), so those combos will grow.
* Three chassis appear in `season_entrant_chassis` for a constructor-season with no race
  results at all (First F189 1989, March CG911C 1993, Larrousse LH95 1995). They are
  counted as entered chassis because that is what the table records.
* Chassis are per season entry, not per race. F1DB has no chassis on individual race
  results, so "which car did this driver drive in this race" is not answerable for teams
  that ran two chassis in one season (e.g. McLaren MP4-19 and MP4-19B in 2004).
