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

## Pool rules (scripts/f1pool.py)

Every script reads F1DB through `scripts/f1pool.py`, so the rules live in one place.
Constants at the top of that file:

| Constant | Value | Meaning |
|---|---|---|
| `FIRST_SEASON` / `LAST_COMPLETE_SEASON` | 1950 / 2025 | Eligible window. In-progress seasons are excluded so card numbers do not move between races. Re-cut deliberately in the off-season. |
| `EXCLUDED_GRANDS_PRIX` | indianapolis | The Indianapolis 500 (championship round 1950-1960) is dropped from every pool and every stat. |
| `MIN_STARTS_PER_DRIVER` | 3 | Backstop after the works filter: a driver needs 3 starts for the constructor in the decade to appear. |
| `POOL_MIN_DRIVERS` / `POOL_MIN_CARS` | 2 / 1 | Playable floor for a Team x Decade spin. |
| `WHEEL_WEIGHT_DRIVERS` | 4 | Combos with at least this many pool drivers get the higher wheel weight. A weighting, not a gate. |

### Works entries only

F1DB attributes a race result to the chassis constructor, so privateer Lotuses count for
Lotus. The Team x Decade premise is "who drove for this team", so only works entries are
eligible. F1DB has **no works flag**, and race results carry no entrant, so:

1. Each race result is mapped to an entrant through `season_entrant_driver` and its
   `rounds_text` ranges (28 result rows have two possible entrants for the same round; the
   first by id is taken and the cases are listed by `connect()` as `ambiguous_results`).
2. Per constructor-season, an entrant is works if it is the **sole entrant**, or its
   **name contains the constructor name** (all tokens: "Team Lotus", "Scuderia Ferrari",
   "Vodafone McLaren Mercedes", "Brooke Bond Oxo Team Surtees"), or it is in
   `WORKS_OVERRIDES`. Everything else is a privateer.
3. `WORKS_OVERRIDES` covers works teams whose entrant name does not carry the marque
   (Owen Racing Organisation = BRM, Motor Racing Developments and Martini Racing = Brabham,
   Marlboro Team Texaco = McLaren 1974-75, Anglo American Racers = Eagle, Vandervell
   Products = Vanwall, Daimler Benz AG = Mercedes, HW Motors = HWM, Equipe Gordini,
   Automobiles Talbot-Darracq, NART's Ferrari works cars 1964-69, Penthouse Rizla Racing =
   Hesketh 1977, Team Rothmans International = March 1977) and one false positive
   (RAM/Williams 1980 is not Williams). This is a classification list, not data, and it
   is meant to be reviewed.

Every decision is written to **`data/audit/works_entrants.csv`** (year, constructor,
entrant, starts, is_works, rule). Constructor-seasons where no works entrant could be
identified are simply absent from the pool. Those with meaningful start counts:

* Maserati 1951 and 1958-1960 (works team absent; privateers only)
* Cooper 1952 and 1954, Connaught 1954, Talbot-Lago 1951 (privateers only)
* Lola 1963 (Reg Parnell Racing ran the Bowmaker Lolas; no name match, no override made)
* Penske 1977 (ATS ran the ex-Penske cars)

Known soft spots: 1970s works cars entered under sponsor names that omit the marque
(the pattern behind the Hesketh and March overrides) may still be classed as privateers;
1973 March (Clarke-Mordaunt-Guthrie, 14 starts) is one such case left as privateer.

### Car cards

One card per constructor-season with a works start, built from `season_entrant_chassis`
for the works entrant(s). F1DB has no chassis on individual race results, so when a team
ran two chassis in a season (266 of 941 cards) the data cannot say which was primary or
split the results between them. Such cards carry every chassis name ("Ferrari F2001B /
F2002", "Lotus 78 / 79") and are rated on the whole season's team results. Naming them
for a single "primary" chassis would need a curated list; none is asserted.

## Pool audit

`scripts/pool_audit.py` writes `data/audit/pool_audit.csv`: one row per constructor x
decade with `drivers_any_entrant` (Indy excluded, any entrant), `drivers_works`,
`drivers_pool` (works and min starts), `cars`, context columns, `playable` and
`wheel_weight`. The three driver columns show what the works filter and the backstop did.

## Era-relative ratings (scripts/ratings.py)

A card shows how a driver or car compared to its own field in its own era, never across
eras. Raw win%, podium% and average finish all fail that test because field size,
attrition and era dominance move underneath them.

**Driver rating: teammate comparison.** Same works car, same race, same season.

* race h2h: races where the driver and a teammate were both classified; ahead / (ahead +
  behind). Ties and shared drives are dropped.
* quali h2h: races where both have a qualifying classification (F1DB has one for every
  race 1950-2025); ahead / (ahead + behind).
* rating = mean of the two percentages (or the one that exists), 0-100. Blank when the
  driver never had a works teammate in the window.

This controls for the car by construction, which is what stops a driver pick and a car
pick double-counting the same season. Limitations, documented not solved: (1) it does not
weight the teammate's quality (beating Barrichello means more than beating a rookie; an
Elo-style network over all teammate pairs is the rigorous fix, out of scope for v1);
(2) small samples are shown as-is (Senna's 1994 Williams rating rests on three
qualifying sessions and no classified race), so the W-L counts must be shown with the
rating; (3) comparisons against non-pool teammates (fewer than 3 starts) still count.

**Car rating: per season, driver-neutral, field-relative.**

* points_share: works race points / all race points awarded that season (Indy excluded).
  Uses per-race points, so dropped-score championship rules do not distort it.
* share_vs_best: points_share / the best constructor's share that season, 0-100. The
  best car of every season is 100. Recommended card rating.
* pct_rank: share of that season's constructors (with a works start) the team out-scored,
  0-100. Rank only, loses the size of the gap.
* Raw context shown alongside: starts, wins, podiums, poles, average classified finish,
  finish rate.

Points share is still not fully era-neutral on its own (a 1-2 sweep is a larger share of
a 9-6-4-3-2-1 scheme than of 25-18-15...), which is why the card rating is expressed
against the season's best team. Full within-season normalization means a 95th-percentile
1970s car and a 95th-percentile 2010s car come out equal; that is intentional and is
what makes every spin viable. 1950s-60s works teams often ran three or four cars, which
raises their share; the normalization against the best team (which did the same) absorbs
most of that.

**Display rule.** Average finish and finish rate always appear together on a card, and a
teammate rating always appears with its W-L counts.
