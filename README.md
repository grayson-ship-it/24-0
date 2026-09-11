# 24-0

An F1 draft game in the browser, modelled on the basketball game 82-0.

Spin a Team x Decade combo (say McLaren x 2000s). You see every driver who raced for that
team in that decade and every car they ran. Pick one thing, spin again. Four picks: Driver
1, Driver 2, Car, and Reserve Driver (who subs in if a starter misses a race). Then it
simulates a 24-race season.

Intended to ship as static files.

## Data

Historical results come from [F1DB](https://github.com/f1db/f1db) (CC BY 4.0), pinned to
a release. See [docs/DATA.md](docs/DATA.md) for why, what is live today, and exact stat
definitions. Nothing is ever estimated; missing numbers stay blank.

```
scripts/fetch_data.sh                 # download + verify the pinned F1DB release
python3 scripts/team_decade.py mclaren 2000s
python3 scripts/team_decade.py "Red Bull" 2010-2019 --json
```

Requires Python 3 (stdlib only), curl and unzip.
