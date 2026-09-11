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
  driver has at least `MIN_H2H_RACES` classified race comparisons. The floor is defined as
  `MIN_STARTS_PER_DRIVER - 1` = 2, so the two thresholds relate deliberately: a driver at
  the 3-start pool minimum is rated if compared in two of those three races, and no single
  race can ever set a rating. A qualifying-only record is still not a driver rating (Senna,
  Williams 1994, stays unrated). The floor is not what limits small samples; shrinkage is.
  Coverage of pool tenures and the highest rating found at exactly the minimum sample:

  | floor | all | 1950s | 1960s | 1970s | 1980s | 1990s | 2000s | 2010s | 2020s | worst case at n = floor |
  |---|---|---|---|---|---|---|---|---|---|---|
  | 1 | 86% | 83% | 81% | 78% | 74% | 89% | 100% | 100% | 100% | 67.7 (1-0 race, 16-0 quali) |
  | **2** | **76%** | 63% | 65% | 67% | 61% | 75% | 93% | 100% | 100% | 69.6 (Alesi, Tyrrell 1990s: 2-0, 16-0 quali) |
  | 3 | 66% | 50% | 52% | 54% | 51% | 62% | 87% | 99% | 100% | 65.6 |
  | 5 | 51% | 31% | 36% | 32% | 35% | 47% | 75% | 95% | 95% | 70.8 (Martini, Minardi 1980s: 5-0) |

  The worst case does not fall as the floor rises, which shows the k = 10 prior is doing
  the work: a perfect small record lands in the mid-60s at every floor, driven by an
  unbeaten qualifying run rather than the race count.
* Outcome check on the two truncated seasons, both handled by the general rule: Senna is
  suppressed (no classified race). Rindt (Lotus 1970, 4-1 in races, unbeaten in 10
  qualifying sessions) rates 67.5, level with Andretti's 67.6 rather than the 90 he showed
  unshrunk.
* Old definition (per-pair, unshrunk, no floor) is still printed beside the new one.

Limitations, documented not solved: (1) teammate quality is not weighted (an Elo-style
network across all pairs is the rigorous fix, out of scope for v1); (2) comparisons
against teammates who are themselves below the pool minimum still count.

**Car rating: per season, driver-neutral, field-relative.**

* points_share: works race points / all race points awarded that season (Indy excluded).
* The **field** for the z-based measures is the season's works teams with at least one
  full-time car (works car-starts >= races in the season), so part-time entries do not
  pad it: 1975 has 18 works constructors but a field of 10.
* x_avg, z, pct_field: multiple of the average team, z-score, percentile within the field.
* z_scaled = z / sqrt(n_field - 1) and z_cdf = normal CDF of z were built to remove the
  field-size ceiling of z (the largest z a field of n allows is sqrt(n-1)). **Verified and
  rejected as the card rating.** Checking the most dominant car of each decade showed
  1952 Ferrari at 0.99 and 1961 Ferrari (45% share, 4 wins from 8) at 0.98, above 1988
  McLaren (15 wins from 16) at 0.93. The cause is that a z-score measures shape, not
  margin: a leader over a perfectly even rest scores exactly sqrt(n-1) whatever its
  margin, so z_scaled hits 1.0 for any such season, and a field of four or five teams
  makes "the rest are even" trivial. z_cdf compresses every title car into 95.7-100.
  Both stay visible as context.
* **share_of_max**: team points / the most points its own started cars could have scored,
  computed per race as the points actually paid to positions 1..k where k is the number of
  works cars the team started. 1.0 means every car it started finished at the top of the
  order, in every era. It is a magnitude measure, neutral to field size and to the number
  of cars the team ran.
* **som_uni**: share_of_max with every classified finish re-scored on the fixed
  25-18-15-12-10-8-6-4-2-1 scheme, so the season's own points table (top-5, top-6, top-8 or
  top-10 scoring) plays no part either. **Recommended card rating.** Most dominant car per
  decade on this scale: 1952 Ferrari 0.81, 1960 Cooper 0.69, 1973 Tyrrell 0.61, 1988
  McLaren 0.84, 1996 Williams 0.70, 2002 Ferrari 0.87, 2015 Mercedes 0.86, 2023 Red Bull
  0.82. The 1960s-70s values are lower for reasons that are properties of those seasons,
  not of the scale: wins were spread across teams (Tyrrell 1973 won 5 of 15) and
  retirements, which score zero, were far more common. Reliability is a car property, so
  that is intended. Field size is not the driver of the pattern: the largest fields
  (1980s) produce the second-highest value.
* Sample cards on som_uni: Ferrari F2002 0.87, Ferrari F2004 0.82, McLaren MP4-22 0.73,
  Williams FW14B 0.66, Brawn BGP 001 0.61, Lotus 78 / 79 0.51, McLaren MP4-19 / 19B 0.24,
  Lotus 72E (1975) 0.11.
* Old measures, still printed: share vs the season's best team (100 for every leader) and
  percentile over all works teams.

**Two car values, two purposes (decision).** share_of_max is an absolute scale: every car
is measured against the same theoretical ceiling, so eras compete directly and a 1973
card carries the fragility of 1973 with it. That is what the simulation needs, because it
runs cards from different decades against each other in one currency. It is not what the
player should see, because it breaks "every era is viable" for the Car slot. So:

| value | scale | used by |
|---|---|---|
| `som_uni` | absolute, 0-1, same ceiling in every era | simulation input |
| `era_pct` / `era_rank` | percentile and rank of som_uni among the decade's full-time works cards (44-125 cards per decade) | card display |

Consequence to keep in view: the simulation will reproduce era reliability, so a 1970s
car will retire more often than a 2020s car of the same era percentile. That is the
trade the split makes explicit; it is not hidden in the display value.

**Sanity check against the constructors' championship** (`--champions`). The top car by
som_uni matches the constructors' champion in 63 of 76 seasons. The 13 disagreements:

* 1958, 1959, 1963, 1964, 1965, 1968, 1970, 1973, 1974: the champion's standing points
  differ from its summed race points (the data flags this for every champion 1958-1978:
  best-placed-car-only scoring and dropped scores), so the title and the metric were
  never counting the same thing. 1972 is in the same rule era but the rule happened not
  to change Lotus's total.
* 2007: McLaren scored more (218 vs 204) but is `EX` in the standings (excluded from the
  championship), so Ferrari is champion. Legitimate divergence.
* 1951 and 1957: no constructors' championship existed; the drivers' champion's team is
  the proxy.

The pattern inside the disagreements is worth knowing: the metric rewards consistent
scoring by every car the team runs over wins by one car. 1957 Ferrari (0 wins, 3-4 cars
finishing 2nd-5th) edges Maserati (4 wins), and 1963-65 BRM (2-3 wins) beats Lotus (6-7
wins, Clark) each year. Both are properties of share_of_max, not errors; a win-heavier
scheme would move them. Sprint points (2021 on) are in the standings but not in the
metric by rule; no disagreement arises from that.

Scale note: the rating is hidden from players, and its spacing will be set once the
simulation's mapping from rating to outcome exists. Nothing is rescaled yet.

**Race vs qualifying weight.** The two components were combined as an unweighted mean
(`RACE_WEIGHT` = 0.5), a default set without analysis. The qualifying component has more
comparisons (no classification needed) and less noise, so it spreads wider and, at 0.5,
tends to dominate: every small-sample worst case in the floor table is an unbeaten
qualifying run. Ratings by race weight, unadjusted | Bradley-Terry adjusted:

| driver | race W-L | quali W-L | U 1.0 | U 0.75 | U 0.5 | BT 1.0 | BT 0.75 | BT 0.5 |
|---|---|---|---|---|---|---|---|---|
| Schumacher, Ferrari 00s | 68-18 | 92-29 | 76.0 | 75.5 | 75.0 | 78.4 | 80.6 | 82.8 |
| Barrichello, Ferrari 00s | 14-57 | 25-79 | 23.5 | 24.2 | 24.9 | 46.4 | 52.1 | 57.7 |
| Hamilton, McLaren 00s | 22-17 | 35-17 | 55.1 | 57.5 | 59.8 | 71.0 | 74.1 | 77.2 |
| Räikkönen, McLaren 00s | 27-12 | 60-28 | 65.3 | 65.6 | 65.8 | 65.8 | 66.3 | 66.7 |
| Montoya, McLaren 00s | 7-6 | 8-19 | 52.2 | 47.9 | 43.7 | 57.3 | 54.1 | 50.9 |

The weight is a game-design choice, left at 0.5 until set; 0.75 is the recommendation
because the game simulates races, not qualifying, and it tempers the qualifying spread.

**Teammate quality: Bradley-Terry adjustment (built).** `scripts/bt.py` fits a global
strength per driver, separately for race and qualifying outcomes, over every works
teammate comparison in the window (per-race weighting, pairs in a race weighted
1/(pairs)). P(i beats j) = s_i / (s_i + s_j). Regularization is the same pseudo-count
idea as before: every driver plays k = 10 pseudo-games against a reference driver of
strength 1 and wins half, which anchors 1 = average and shrinks sparse records. Fitted by
the Hunter (2004) minorization-maximization update; the teammate graph is connected
(515 of 525 drivers), and the prior anchors the rest.

A tenure's adjusted rating is a one-parameter fit of the same model over that tenure's
comparisons with the teammates held at their global strength, displayed as
100 * s / (s + 1) = probability of beating an average driver. Against an exactly average
teammate this reduces to the unadjusted shrunk rating, so the two scales coincide at 50.
The floor applies unchanged. Known circularity: a teammate's global strength includes
the comparisons from this tenure.

Results (race weight 0.5): Hamilton/McLaren 59.8 to 77.2 and Räikkönen/McLaren 65.8 to
66.7, so the flagship order flips. Barrichello/Ferrari 24.9 to 57.7 (52.1 at race
weight 0.75): losing four in five to Schumacher is rated above average. Biggest movers up
are drivers paired with the strongest global strengths (Berger vs Senna +35, Sainz vs
Leclerc +34, Pérez vs Verstappen +32, Rosberg vs Hamilton +28); biggest movers down are
drivers who beat weak teammates in weak teams (Monteiro/Jordan -13, Piquet/Lotus 1980s
vs Nakajima -12, Russell/Williams vs Latifi -9). Moves up reach +35 while moves down stop
near -13, because the weakest global strengths sit around 28-38 while the strongest reach
87. Global top strengths (race): Verstappen 87, Senna 81, Leclerc 81, Alonso 80, Fangio
77, Schumacher 77, Russell 75, Ascari 73, Hamilton 73, Norris 73. Current drivers sit high
partly because 20-plus-race seasons leave less shrinkage; Mika Salo and Alexander Albon in
the top 20 are the entries that look like network artefacts. Qualifying strengths are
more extreme than race strengths (Senna 93), which is another reason to weight races.

**Teammate quality (earlier costing, kept for the record).** Hamilton (McLaren 2000s) rates 59.8 below
Räikkönen at 65.8 because Hamilton's comparisons are against Alonso and Kovalainen while
Räikkönen's are against Coulthard and Montoya. The teammate graph over the window is
connected (515 of 525 works drivers in one component), so a network model across eras is
feasible. Options, with the exploratory numbers from a one-pass career-rating adjustment
(not committed; shift each comparison by the teammate's career rating): Hamilton 59.8 to
about 67-70, Räikkönen 65.8 to about 60, Alonso 52 to 61, Barrichello 25 to 48.

| Option | What | Cost | Caveat |
|---|---|---|---|
| A. One-pass adjustment by teammates' career shrunk h2h, iterated 2-3 times | ~80 lines | 2-3 hours | Uncalibrated linear shift; iteration does not converge cleanly (Hamilton 67.5, 66.5, 70.0); large swings for drivers paired with a great (Barrichello +23). |
| B. Bradley-Terry global driver strengths (MM fit on per-race outcomes, pseudo-count regularization, race and quali separately), tenure rating = shrunk performance against expectation | ~200 lines + validation | about 1 day | One strength per driver across their career; era connectivity verified. Standard model, calibrated scale. |
| C. Hierarchical: global strength plus a shrunk per-tenure deviation | ~300 lines | about 2 days | The rigorous version of the deferred "full Elo network". |

Recommendation: B, before the simulation, because the simulation will be tuned to the
rating scale and re-rating afterwards means re-tuning.

**Display rule.** Average finish and finish rate always appear together on a card, and a
teammate rating always appears with its W-L counts and sample size, or as "unrated" with
the counts when it is below the floor.
