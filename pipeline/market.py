"""
Market-implied power ratings: what the betting market thinks each team is worth.

Every point spread is a statement about two teams at once. A season of spreads
is therefore an over-determined system that can be solved for a rating per team,
exactly the way the results-based solve works -- only the target is the number
the market posted rather than the score that happened.

Two reasons this earns its place.

**It is a far better early-season prior than anything else available.** In Week 2
the results-based ratings have one game each to work with and are mostly noise.
The market, by contrast, has already priced the draft, free agency, the new
coordinator and the quarterback's ankle, and it has done so with real money. Its
Week 2 opinion is worth more than a whole month of the model's own.

**It makes disagreement legible at the team level.** The board already shows
where the model disagrees with the market on a single game. This shows where it
disagrees about a *team*: "we have Detroit 1.8 points above where the market has
them" is a much more useful thing to know than sixteen separate game-level gaps,
because it tells you whether an edge is one situation or one opinion showing up
sixteen times. If every play on the card is powered by the same team-level
disagreement, that is one bet, not six -- and this is where you see it.

Deliberately NOT folded into the game projection. The per-game anchor in
model.blend_to_market already pulls the line toward the market's number for that
game; adding a market-derived rating on top would apply the same correction
twice and leave the model with no opinion at all.
"""

from __future__ import annotations

from . import ratings as R


def solve(games: list[dict], cfg: dict) -> tuple[dict[str, float], float]:
    """
    Ratings implied by the point spreads. Returns ({team: rating}, market HFA).

    Uses every game with a posted spread -- finished or upcoming -- weighted by
    recency, so the answer tracks the market as it moves through the season
    rather than averaging September into December.
    """
    rows_in = [g for g in games
               if (g.get("odds") or {}).get("spread_home") is not None
               and int(g.get("season_type") or 2) not in (1, 4)
               and R.real_matchup(g)]
    if len(rows_in) < 8:
        return {}, float(cfg["model"]["home_field_fallback"])

    teams = sorted({g["home"]["abbr"] for g in rows_in} | {g["away"]["abbr"] for g in rows_in})
    idx = {t: i for i, t in enumerate(teams)}
    hfa_col = len(teams)

    weights = R._weights(rows_in, float(cfg["ratings"]["recency_halflife_games"]))
    rows: list[tuple[dict[int, float], float, float]] = []
    for g, w in zip(rows_in, weights):
        # A spread of -3 means the market projects the home team by 3.
        target = -float(g["odds"]["spread_home"])
        coefs = {idx[g["home"]["abbr"]]: 1.0, idx[g["away"]["abbr"]]: -1.0}
        if not g.get("neutral"):
            coefs[hfa_col] = 1.0
        rows.append((coefs, target, float(w)))

    # Light ridge: the market's own numbers are already smooth, so this only
    # needs to keep the system well conditioned, not to fight noise.
    beta = R._ridge_solve(rows, len(teams) + 1, lam=3.0, unpenalised=(hfa_col,))
    hfa = float(beta[hfa_col])
    if not (0.0 <= hfa <= 5.0):
        hfa = float(cfg["model"]["home_field_fallback"])

    out = {t: float(beta[idx[t]]) for t in teams}
    mean = sum(out.values()) / len(out)
    return {t: round(v - mean, 3) for t, v in out.items()}, round(hfa, 2)


def blend_prior(model_prior: dict[str, float], market: dict[str, float],
                games_played: dict[str, int], cfg: dict) -> tuple[dict[str, float], str]:
    """
    Fold the market's ratings into the preseason prior, fading them out as real
    results arrive.

    The weight is per team and falls with that team's own games played, so a club
    that has had two byes and a postponement keeps leaning on the market longer
    than one that has played six times. By the time a team is at
    `min_games_for_full_confidence`, the market prior is contributing almost
    nothing and the results-based solve owns the rating.
    """
    if not market:
        return model_prior, "no market ratings yet"
    need = max(1.0, float(cfg["model"]["min_games_for_full_confidence"]))
    out: dict[str, float] = {}
    for t in set(model_prior) | set(market):
        n = float(games_played.get(t, 0))
        w = max(0.0, 1.0 - (n / (need * 1.5)))       # 1.0 preseason -> 0 by ~6 games
        out[t] = w * market.get(t, 0.0) + (1 - w) * model_prior.get(t, 0.0)
    mean = sum(out.values()) / len(out) if out else 0.0
    played_any = sum(1 for v in games_played.values() if v)
    note = ("market spreads only" if not played_any
            else "market spreads fading out as results arrive")
    return {t: v - mean for t, v in out.items()}, note


def disagreement(model_ratings: dict[str, float], market: dict[str, float]) -> dict[str, float]:
    """Per team: how many points above the market's opinion the model has them."""
    return {t: round(model_ratings[t] - market[t], 2)
            for t in model_ratings if t in market}
