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

* Counting is **per race**, not per teammate pair. A race with at least one comparison
  contributes one unit of sample, and the driver's score for that race is the fraction of
  compared teammates they beat (1.0 for ahead of both in a three-car team, 0.5 for ahead
  of one). Per-pair W-L is still displayed. Reason: sample size should measure independent
  evidence, and a three-car 1970 Lotus should not accumulate it faster than a two-car 2009
  Brawn. It also keeps the prior below in race units for every era. (245 of 1015 pool
  tenures include at least one three-or-more-car race.)
* race component: races where the driver and a teammate were both classified. Ties and
  shared drives are dropped.
* quali component: races where both have a qualifying classification (F1DB has one for
  every race 1950-2025).
* **Shrinkage** (empirical Bayes): each component is pulled toward 50% by a prior worth
  `RATING_PRIOR_RACES` races: (wins + k/2) / (n + k). With k = 10, Schumacher's 68-18
  moves from 79.1% to 76.0%; Rindt's 4-1 moves from 80% to 60%. A method-of-moments fit
  across all pool tenures (observed variance of win fractions minus binomial noise) implies
  k of about 6-8 for races and 3-5 for qualifying, so k = 10 shrinks slightly more than the
  data strictly requires. That is deliberate: the observed spread partly reflects teammate
  quality (unweighted in v1), and an over-rated small sample is a drafting exploit while an
  under-rated one is only a bargain.
* rating = mean of the two shrunk components, 0-100.
* **Floor:** the rating is suppressed (blank, with the W-L counts still shown) unless the
  driver has at least `MIN_H2H_RACES` = 5 classified race comparisons. A qualifying-only
  record is not a driver rating. Five is where a record can first establish a direction
  (a 4-0 split happens one time in sixteen between equal drivers) and where the k = 10
  prior already caps a perfect record at 66.7%. Cost of the floor, given the 3-start
  backstop: about half of all pool tenures carry a rating; 75-99% from the 2000s on, but
  only about a third before 1990, where finishing rates were low. Those drivers stay in the
  pool with raw stats and no number. The simulation will need an explicit rule for unrated
  drivers; that is a separate decision, not a statistic to fill in.
* Outcome check on the two truncated seasons, both handled by the general rule: Senna
  (Williams 1994, 3 starts, 0 classified) is suppressed. Rindt (Lotus 1970, 5 classified
  comparisons, 4-1 races, unbeaten in 10 qualifying sessions) rates 67.5, level with
  Andretti's 67.6 rather than the 90 he showed unshrunk; raising the floor to 6 would
  suppress him too, at the cost of a further tenth of 1970s coverage.
* Old definition (per-pair, unshrunk, no floor) is still printed beside the new one.

Limitations, documented not solved: (1) teammate quality is not weighted (an Elo-style
network across all pairs is the rigorous fix, out of scope for v1); (2) comparisons
against teammates who are themselves below the pool minimum still count.

**Car rating: per season, driver-neutral, field-relative.**

* points_share: works race points / all race points awarded that season (Indy excluded).
  Per-race points, so dropped-score championship rules do not distort it.
* The **field** for the measures below is the season's works teams with at least one
  full-time car (works car-starts >= races in the season). Part-time entries do not pad
  it: 1975 has 18 works constructors but a field of 10, and Lotus's 2.8% share moves from
  the 65th percentile of all teams to the 33rd of the field.
* x_avg: points_share / mean share of the field. 1.0 is an average team. Ferrari 2002 is
  5.50, Brawn 2009 is 2.67, Lotus 1975 is 0.29.
* z: (points_share - field mean) / field standard deviation. Ferrari 2002 is 2.84, Brawn
  2009 is 2.01, Williams 1992 is 2.75, McLaren 2007 is 2.07. **Recommended card rating.**
* pct_field: share of the field the team out-scored, 0-100. Saturates at 100 for every
  season leader, so it is context, not the rating.
* Old measures, still printed: share vs the season's best team (100 for every leader,
  which is why it was replaced) and percentile over all works teams.

Caveats: every measure's ceiling still depends slightly on era. A 1-2 sweep is a larger
share of a 9-6-4-3-2-1 scheme than of 25-18-15-..., and the largest possible z in a field
of n is sqrt(n-1) (3.0 for 10 teams, 3.6 for 14). Within-season normalization is
intentional: it is what makes every spin viable. 1950s-60s works teams often ran three
or four cars, which raises their share against two-car rivals in the same season.

**Display rule.** Average finish and finish rate always appear together on a card, and a
teammate rating always appears with its W-L counts and sample size, or as "unrated" with
the counts when it is below the floor.
