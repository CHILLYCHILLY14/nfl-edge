"""
Self-updating team power ratings.

This is the piece the spreadsheet could never do. The workbook derived every
rating from one hand-typed number per team -- the market's season win total --
and then left it frozen all year. Half of those numbers were wrong the day the
file was made, and all of them were stale by October. Here the win totals are
only the PRESEASON PRIOR, and from Week 1 onward the ratings are solved from
results, so they update themselves every time a game goes final.

Method: ridge-regularised least squares on margin of victory.

    margin(home - away)  ~=  r_home - r_away + hfa * (0 if neutral else 1)

Solved jointly across every game in the sample, so a team's rating is its
strength net of who it played -- strength of schedule is handled structurally
rather than bolted on as a fudge factor. Four details do most of the work:

  * Ridge penalty. Without it, a 3-0 team that played nobody gets an absurd
    rating and the system is near-singular in Week 1. The penalty pulls each
    rating toward its prior in proportion to how little evidence supports it,
    which is correct Bayesian behaviour rather than a hack.
  * MOV cap. A 44-6 Thursday night game is not six times more informative than a
    24-18 one. Capping margin keeps garbage time from setting the ratings.
  * Recency half-life, measured in days. September tells you less about December
    than November does.
  * Market win totals as the anchor. The market's August opinion beats last
    season's results on its own, because the market has already priced the
    coaching change, the draft, the free agency class and the quarterback.

Totals get their own solve: points scored is modelled as league average plus the
scoring team's offence minus the conceding team's defence, which projects each
side of a matchup individually instead of averaging two season scoring rates.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from collections import defaultdict

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_win_totals() -> dict:
    p = os.path.join(ROOT, "config", "win_totals.json")
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def market_prior() -> dict[str, float]:
    """
    Preseason ratings from the market's season win totals.

    (win_total - 8.5) * points_per_win, the workbook's own formula -- it was the
    one genuinely good idea in that file. The difference is that here it is a
    starting point that decays, not a permanent input.
    """
    wt = load_win_totals()
    per = float(wt.get("points_per_win", 1.6))
    return {t: (float(v) - 8.5) * per for t, v in (wt.get("totals") or {}).items()}


def align_to_league(prior: dict[str, float], teams_seen: set[str]) -> dict[str, float]:
    """
    Reconcile the prior's team codes with the ones the schedule actually uses.

    ESPN calls Washington WSH; other sources say WAS. Carrying both through the
    solve would invent a thirty-third team with half a season of evidence split
    across two rows, which is exactly the kind of silent corruption that is
    impossible to spot in a ratings table. So aliases are resolved against what
    the schedule really contains, and anything the league does not recognise is
    dropped rather than quietly rated.
    """
    aliases = (load_win_totals().get("aliases") or {})
    out = dict(prior)
    for alias, real in aliases.items():
        if alias in teams_seen and real not in teams_seen and real in out:
            out[alias] = out.pop(real)
    if teams_seen:
        out = {t: v for t, v in out.items() if t in teams_seen}
    return out


# --------------------------------------------------------------------------- #

def _ridge_solve(rows: list[tuple[dict[int, float], float, float]],
                 n_params: int, lam: float,
                 unpenalised: tuple[int, ...] = ()) -> np.ndarray:
    """
    Solve (X'WX + lam*I) b = X'Wy without ever materialising a dense X.

    rows: (sparse column->coefficient map, target y, weight w)

    `unpenalised` names columns that must NOT be shrunk toward zero. Home-field
    advantage is one: it is a real league-wide effect estimated off thousands of
    games, and penalising it only biases it downward for no benefit.
    """
    A = np.zeros((n_params, n_params), dtype=float)
    b = np.zeros(n_params, dtype=float)
    for coefs, y, w in rows:
        items = list(coefs.items())
        for i, ci in items:
            b[i] += w * ci * y
            for j, cj in items:
                A[i, j] += w * ci * cj
    pen = np.full(n_params, lam, dtype=float)
    for c in unpenalised:
        pen[c] = 0.0
    A += np.diag(pen)
    try:
        return np.linalg.solve(A, b)
    except np.linalg.LinAlgError:
        return np.linalg.lstsq(A, b, rcond=None)[0]


def _weights(games: list[dict], halflife_games: float) -> list[float]:
    """
    Exponential recency decay measured in CALENDAR DAYS, not list position.

    Measuring decay by position in the list is a trap the college version of
    this project fell into once: the list holds every game in the league, so a
    half-life of "8 games" silently became a half-life of half a week. Days are
    what actually matter, and an NFL team plays about once a week, so a
    half-life of N team-games is N*7 days.
    """
    if not games or halflife_games <= 0:
        return [1.0] * len(games)
    days: list[float] = []
    for g in games:
        d = (g.get("date_utc") or "")[:10]
        try:
            days.append(dt.date.fromisoformat(d).toordinal())
        except ValueError:
            days.append(0.0)
    latest = max(days) if days else 0.0
    hl = halflife_games * 7.0
    return [0.5 ** ((latest - d) / hl) if d else 0.25 for d in days]


def _played(games: list[dict], include_preseason: bool = False) -> list[dict]:
    """
    Completed games worth learning from.

    Preseason is excluded on purpose. Preseason results measure how long the
    starters played, not how good the team is, and letting them into the solve
    would have the model rating a team on the strength of its third-string
    offensive line in August.
    """
    out = []
    for g in games:
        if not g.get("completed"):
            continue
        if g.get("home_score") is None or g.get("away_score") is None:
            continue
        if not (g.get("home", {}).get("abbr") and g.get("away", {}).get("abbr")):
            continue
        if not include_preseason and int(g.get("season_type") or 2) == 1:
            continue
        out.append(g)
    return out


def solve_margin_ratings(games: list[dict], cfg: dict,
                         prior: dict[str, float] | None = None) -> tuple[dict[str, float], float]:
    """
    Returns ({team_abbr: rating_in_points}, solved_home_field_advantage).

    A rating is "points better than an average team in this sample on a neutral
    field". The difference between two ratings is a projected margin.
    """
    r = cfg["ratings"]
    lam = float(r["ridge_lambda"])
    cap = float(r["mov_cap"])
    halflife = float(r["recency_halflife_games"])

    played = _played(games)
    if not played:
        return (dict(prior or {}), float(cfg["model"]["home_field_fallback"]))

    seen = {g["home"]["abbr"] for g in played} | {g["away"]["abbr"] for g in played}
    prior = align_to_league(prior or {}, seen)
    teams = sorted(seen)
    idx = {t: i for i, t in enumerate(teams)}
    hfa_col = len(teams)
    n_params = len(teams) + 1

    ws = _weights(played, halflife)
    rows: list[tuple[dict[int, float], float, float]] = []
    for g, w in zip(played, ws):
        margin = max(-cap, min(cap, g["home_score"] - g["away_score"]))
        coefs = {idx[g["home"]["abbr"]]: 1.0, idx[g["away"]["abbr"]]: -1.0}
        if not g.get("neutral"):
            coefs[hfa_col] = 1.0
        rows.append((coefs, float(margin), float(w)))

    # Anchor each rating to its preseason prior rather than to zero. Early in the
    # year that is the difference between "we know nothing" and "we know what the
    # market knew in August", which is most of what there is to know.
    if prior:
        n_weeks = max(1.0, len(played) / 16.0)
        prior_w = float(r["prior_season_weight"]) * max(1.0, 16.0 / n_weeks) * 0.5
        for t, p in prior.items():
            if t in idx:
                rows.append(({idx[t]: 1.0}, float(p), prior_w))

    beta = _ridge_solve(rows, n_params, lam, unpenalised=(hfa_col,))

    # Shrink the solved home-field number toward the configured fallback in
    # proportion to how many non-neutral games it came from. In Week 2 a raw
    # solve can land anywhere; by December it should stand on its own.
    hfa = float(beta[hfa_col])
    n_home_games = sum(1 for g in played if not g.get("neutral"))
    k = 90.0
    hfa = (n_home_games * hfa + k * float(cfg["model"]["home_field_fallback"])) / (n_home_games + k)
    hfa = min(max(hfa, 0.0), 4.0)

    ratings = {t: float(beta[idx[t]]) for t in teams}
    mean = sum(ratings.values()) / len(ratings)
    return ({t: v - mean for t, v in ratings.items()}, hfa)


def per_team_home_field(games: list[dict], league_hfa: float, cfg: dict) -> dict[str, float]:
    """
    Each stadium's own home-field value, shrunk hard toward the league number.

    Denver's altitude and Seattle's noise are real and worth about a point over
    the league baseline. The other thirty stadiums are, to a very good
    approximation, identical, and any difference a season of data shows is
    noise. The whole job here is telling those two cases apart, which is what
    the shrinkage constant does: with k=45 a team needs a lot of home games
    before its own number moves far from the league's.

    Measured as each home team's average margin RELATIVE to what the ratings
    already predicted, so a good team is not credited with a big home field
    simply for being good.
    """
    if not cfg["model"].get("per_team_home_field", False):
        return {}
    played = _played(games)
    if not played:
        return {}
    ratings, _ = solve_margin_ratings(games, cfg)
    resid: dict[str, list[float]] = defaultdict(list)
    for g in played:
        if g.get("neutral"):
            continue
        h, a = g["home"]["abbr"], g["away"]["abbr"]
        if h not in ratings or a not in ratings:
            continue
        expected = ratings[h] - ratings[a] + league_hfa
        resid[h].append((g["home_score"] - g["away_score"]) - expected)
    k = 45.0
    out = {}
    for t, rs in resid.items():
        n = len(rs)
        mean = sum(rs) / n
        out[t] = round(league_hfa + (n * mean) / (n + k), 2)
    return out


def solve_scoring_ratings(games: list[dict], cfg: dict) -> tuple[dict[str, dict], float, float]:
    """
    Offence / defence ratings for the totals model.

        points_for(t vs o)  ~=  league_avg + off[t] - def[o] + home_bump*(home?)

    Returns ({team: {"off": x, "def": y}}, league_avg_points, home_scoring_bump).
    """
    r = cfg["ratings"]
    lam = float(r["ridge_lambda"])
    halflife = float(r["recency_halflife_games"])

    played = _played(games)
    if not played:
        return ({}, 22.6, 0.9)

    pts = [g["home_score"] for g in played] + [g["away_score"] for g in played]
    league = float(np.mean(pts))

    teams = sorted({g["home"]["abbr"] for g in played} | {g["away"]["abbr"] for g in played})
    off = {t: i for i, t in enumerate(teams)}
    dfn = {t: i + len(teams) for i, t in enumerate(teams)}
    home_col = 2 * len(teams)
    n_params = home_col + 1

    ws = _weights(played, halflife)
    rows: list[tuple[dict[int, float], float, float]] = []
    for g, w in zip(played, ws):
        h, a = g["home"]["abbr"], g["away"]["abbr"]
        rows.append(({off[h]: 1.0, dfn[a]: -1.0, home_col: 1.0},
                     float(g["home_score"]) - league, float(w)))
        rows.append(({off[a]: 1.0, dfn[h]: -1.0},
                     float(g["away_score"]) - league, float(w)))

    beta = _ridge_solve(rows, n_params, lam, unpenalised=(home_col,))
    bump = float(beta[home_col])
    if not (-2.5 <= bump <= 4.0):
        bump = 0.9

    return ({t: {"off": float(beta[off[t]]), "def": float(beta[dfn[t]])} for t in teams},
            league, bump)


def regress_to_prior(last_season: dict[str, float], factor: float) -> dict[str, float]:
    """Carry last year's solved ratings into this year's preseason prior."""
    return {t: v * factor for t, v in last_season.items()}


def preseason_prior(prior_season_games: list[dict], cfg: dict) -> tuple[dict[str, float], str]:
    """
    Blend last season's solved ratings with the market's win totals.

    Neither source is enough alone. Last season's ratings know nothing about the
    draft or a new quarterback; the win totals know nothing about how a team
    actually played. Averaging them beats either one, and it is what the sharper
    public models do in August.
    """
    market = market_prior() if cfg["ratings"].get("use_market_win_totals", True) else {}
    solved, _ = solve_margin_ratings(prior_season_games, cfg)
    regressed = regress_to_prior(solved, float(cfg["ratings"]["prior_regression"]))
    if not market:
        return regressed, "last season only"
    if not regressed:
        return market, "market win totals only"
    teams = set(market) | set(regressed)
    blended = {t: 0.55 * market.get(t, 0.0) + 0.45 * regressed.get(t, 0.0) for t in teams}
    mean = sum(blended.values()) / len(blended)
    return {t: v - mean for t, v in blended.items()}, "55% market win totals / 45% last season"


def games_played(games: list[dict]) -> dict[str, int]:
    n: dict[str, int] = defaultdict(int)
    for g in _played(games):
        n[g["home"]["abbr"]] += 1
        n[g["away"]["abbr"]] += 1
    return dict(n)


def ats_form(games: list[dict], lookback: int = 5) -> dict[str, dict]:
    """
    Recent against-the-spread form, straight from graded results.

    Kept as a REPORTING number rather than a model input, on purpose. ATS record
    is the most seductive and least predictive statistic in sports betting: the
    margin solve already knows a team is playing well, so adding ATS form on top
    double-counts it, and worse, ATS record is the market's own residual --
    chasing it is chasing noise by construction. It is shown because it is
    useful context for a person, not because the model leans on it.
    """
    hist: dict[str, list[str]] = defaultdict(list)
    ou: dict[str, list[str]] = defaultdict(list)
    for g in sorted(_played(games), key=lambda x: x.get("date_utc") or ""):
        odds = g.get("odds") or {}
        sp, tot = odds.get("spread_home"), odds.get("total")
        margin = g["home_score"] - g["away_score"]
        if sp is not None:
            cover = margin + float(sp)
            hist[g["home"]["abbr"]].append("W" if cover > 0 else ("L" if cover < 0 else "P"))
            hist[g["away"]["abbr"]].append("L" if cover > 0 else ("W" if cover < 0 else "P"))
        if tot is not None:
            combined = g["home_score"] + g["away_score"]
            r = "O" if combined > float(tot) else ("U" if combined < float(tot) else "P")
            ou[g["home"]["abbr"]].append(r)
            ou[g["away"]["abbr"]].append(r)

    out: dict[str, dict] = {}
    for t in set(hist) | set(ou):
        recent = hist[t][-lookback:]
        w = recent.count("W")
        l = recent.count("L")
        o = ou[t]
        out[t] = {
            "w": w, "l": l, "p": recent.count("P"),
            "pct": (w / (w + l)) if (w + l) else None,
            "n": len(recent),
            "season_ats": f'{hist[t].count("W")}-{hist[t].count("L")}-{hist[t].count("P")}' if hist[t] else None,
            "season_ou": f'{o.count("O")}-{o.count("U")}-{o.count("P")}' if o else None,
        }
    return out
