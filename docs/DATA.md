# Data source and definitions

## What is live today (checked 2026-09-11)

| Source | Status | Notes |
|---|---|---|
| **Ergast** (ergast.com) | Shut down | Stopped updating after the 2024 season and went offline. Not reachable. |
| **Jolpica-F1** (api.jolpi.ca) | Live, community-run | Ergast-compatible REST API at `api.jolpi.ca/ergast/f1/`. Requires a custom User-Agent, is rate limited, and volunteer-hosted. Offers CSV database dumps at `api.jolpi.ca/data/dumps/download/`: the free tier is delayed 14 days and non-commercial only. No GitHub releases. Its schema has no chassis (car model) data, only constructors. |
| **F1DB** (github.com/f1db/f1db) | Live, releases after every race | CC BY 4.0. Versioned GitHub releases (CalVer `YYYY.RR.MICRO`) in CSV, JSON, SQLite and SQL formats with published SHA-256 checksums. Covers 1950 to present and includes **chassis per season entry**, which the Car pick needs. |

This project uses **F1DB**, pinned to a specific release in `scripts/fetch_data.sh`.
The api.jolpi.ca domain was not reachable from the environment this was set up in, so
Jolpica's dump was not downloaded or inspected. F1DB was chosen on its own merits (chassis
data, pinned versioned releases, permissive licence) but that constraint is worth knowing.

Attribution required by the licence: "Data from F1DB (https://github.com/f1db/f1db), CC BY 4.0."

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
* `constructor_chronology`: team lineage (e.g. renames). Not yet used.

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
