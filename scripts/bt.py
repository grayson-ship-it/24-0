"""
Bradley-Terry strengths for teammate comparisons, with pseudo-count regularization.

P(i beats j) = s_i / (s_i + s_j). Every driver also plays k pseudo-games against a
reference driver of strength 1 and wins half of them, which anchors the scale and shrinks
sparse records toward average exactly as the plain head-to-head rating does (k pseudo-races
at 50%). Fitted with the minorization-maximization update of Hunter (2004).

A strength s maps to the display scale as 100 * s / (s + 1): the probability of beating an
average driver, so 50 is average, consistent with the unadjusted rating.
"""
from collections import defaultdict


def fit(wins, k, iters=2000, tol=1e-9):
    """wins: dict (i, j) -> weighted number of wins of i over j. Returns dict driver -> strength."""
    W = defaultdict(float)
    games = defaultdict(lambda: defaultdict(float))
    for (i, j), w in wins.items():
        W[i] += w
        games[i][j] += w
        games[j][i] += w
    s = {d: 1.0 for d in games}
    for _ in range(iters):
        delta = 0.0
        for i in s:
            den = sum(n / (s[i] + s[j]) for j, n in games[i].items()) + k / (s[i] + 1.0)
            new = (W[i] + k / 2.0) / den
            delta = max(delta, abs(new - s[i]))
            s[i] = new
        if delta < tol:
            break
    return s


def fit_one(wins_by_opp, opp_strength, k, iters=500, tol=1e-10):
    """Strength of one subject against opponents of fixed strength.
    wins_by_opp: dict opponent -> (wins, games). Returns the strength."""
    W = sum(w for w, _ in wins_by_opp.values())
    s = 1.0
    for _ in range(iters):
        den = sum(n / (s + opp_strength.get(j, 1.0)) for j, (_, n) in wins_by_opp.items()) + k / (s + 1.0)
        new = (W + k / 2.0) / den
        if abs(new - s) < tol:
            s = new
            break
        s = new
    return s


def to_rating(s):
    return 100.0 * s / (s + 1.0)
