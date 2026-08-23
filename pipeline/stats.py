"""
Team statistics, derived from results and enriched from ESPN.

Two sources, on purpose.

DERIVED stats are computed from the game log this pipeline already holds: points
for and against, margin, home/away splits, recent form, ATS and over/under
records, scoring by half of the season. These can never break, never need a key,
and are exactly the numbers used to justify a projection -- so the reasoning
shown on a game card is always backed by something the pipeline can prove.

ENRICHED stats come from ESPN's team statistics feed: yards per play, third-down
rate, turnover margin, sacks, red-zone efficiency. Richer, but a third-party
schema that can change shape without warning, so every failure degrades to an
empty panel instead of a broken build.

Neither set feeds the ratings. The ridge solve learns team strength from margins
already; bolting yards-per-play on top would double-count the same evidence and
make the model look better informed than it is. These exist to EXPLAIN a
projection to a person, which is a different job from making one.
"""

from __future__ import annotations

from collections import defaultdict


def _completed(games: list[dict], season_type: int | None = 2) -> list[dict]:
    out = []
    for g in games:
        if not g.get("completed") or g.get("home_score") is None or g.get("away_score") is None:
            continue
        if season_type is not None and int(g.get("season_type") or 2) != season_type:
            continue
        out.append(g)
    return sorted(out, key=lambda x: x.get("date_utc") or "")


def derived(games: list[dict], season_type: int | None = 2) -> dict[str, dict]:
    """Everything the model's own reasoning is allowed to cite."""
    rows: dict[str, dict] = defaultdict(lambda: {
        "games": 0, "wins": 0, "losses": 0, "ties": 0,
        "pf": 0, "pa": 0, "home_games": 0, "home_pf": 0, "home_pa": 0,
        "away_games": 0, "away_pf": 0, "away_pa": 0,
        "last5": [], "margins": [], "totals": [],
    })

    for g in _completed(games, season_type):
        hs, as_ = g["home_score"], g["away_score"]
        for side, opp_score, own_score in (("home", as_, hs), ("away", hs, as_)):
            t = g[side]["abbr"]
            if not t:
                continue
            r = rows[t]
            r["games"] += 1
            r["pf"] += own_score
            r["pa"] += opp_score
            r["margins"].append(own_score - opp_score)
            r["totals"].append(hs + as_)
            if own_score > opp_score:
                r["wins"] += 1
                r["last5"].append("W")
            elif own_score < opp_score:
                r["losses"] += 1
                r["last5"].append("L")
            else:
                r["ties"] += 1
                r["last5"].append("T")
            if side == "home":
                r["home_games"] += 1
                r["home_pf"] += own_score
                r["home_pa"] += opp_score
            else:
                r["away_games"] += 1
                r["away_pf"] += own_score
                r["away_pa"] += opp_score

    out: dict[str, dict] = {}
    for t, r in rows.items():
        n = max(1, r["games"])
        recent = r["margins"][-3:]
        out[t] = {
            "games": r["games"],
            "record": f'{r["wins"]}-{r["losses"]}' + (f'-{r["ties"]}' if r["ties"] else ""),
            "ppg": round(r["pf"] / n, 1),
            "papg": round(r["pa"] / n, 1),
            "margin": round((r["pf"] - r["pa"]) / n, 1),
            "pf": r["pf"], "pa": r["pa"],
            "home_ppg": round(r["home_pf"] / r["home_games"], 1) if r["home_games"] else None,
            "home_papg": round(r["home_pa"] / r["home_games"], 1) if r["home_games"] else None,
            "away_ppg": round(r["away_pf"] / r["away_games"], 1) if r["away_games"] else None,
            "away_papg": round(r["away_pa"] / r["away_games"], 1) if r["away_games"] else None,
            "avg_game_total": round(sum(r["totals"]) / n, 1) if r["totals"] else None,
            "last5": "".join(r["last5"][-5:]),
            "last3_margin": round(sum(recent) / len(recent), 1) if recent else None,
        }
    return out


# --------------------------------------------------------------------------- #
# ESPN enrichment
# --------------------------------------------------------------------------- #

_WANTED = {
    "passing.netYardsPerGame": "Pass yds/g",
    "rushing.rushingYardsPerGame": "Rush yds/g",
    "scoring.totalPointsPerGame": "Points/g",
    "general.yardsPerGame": "Total yds/g",
    "general.totalYards": "Total yards",
    "miscellaneous.thirdDownConvPct": "3rd down %",
    "miscellaneous.totalGiveaways": "Giveaways",
    "miscellaneous.totalTakeaways": "Takeaways",
    "miscellaneous.turnOverDifferential": "Turnover diff",
    "miscellaneous.redzoneScoringPct": "Red zone %",
    "defensive.sacks": "Sacks",
    "defensive.totalTackles": "Tackles",
    "passing.completionPct": "Completion %",
    "passing.yardsPerPassAttempt": "Yards/attempt",
    "rushing.yardsPerRushAttempt": "Yards/carry",
}


def tidy_espn(stats_by_team: dict[str, dict]) -> dict[str, dict]:
    """Reduce ESPN's very wide statistics payload to the handful worth showing."""
    out: dict[str, dict] = {}
    for team, raw in (stats_by_team or {}).items():
        row: dict[str, dict] = {}
        for key, label in _WANTED.items():
            if key in raw:
                row[label] = {"value": raw.get(key), "rank": raw.get(f"{key}.rank")}
        if row:
            out[team] = row
    return out


def league_context(derived_rows: dict[str, dict]) -> dict:
    """League averages, so a team's numbers can be stated as better or worse than typical."""
    vals = [r for r in derived_rows.values() if r.get("games")]
    if not vals:
        return {"ppg": None, "papg": None, "total": None, "n": 0}
    return {
        "ppg": round(sum(r["ppg"] for r in vals) / len(vals), 1),
        "papg": round(sum(r["papg"] for r in vals) / len(vals), 1),
        "total": round(sum(r["avg_game_total"] or 0 for r in vals) / len(vals), 1),
        "n": len(vals),
    }


def rank_table(ratings: dict[str, float], score_ratings: dict[str, dict],
               derived_rows: dict[str, dict], form: dict[str, dict],
               previous: dict[str, int] | None = None,
               market: dict[str, float] | None = None,
               team_hfa: dict[str, float] | None = None) -> list[dict]:
    """
    The power rankings the site publishes.

    Ranked on the solved net rating, with offence and defence shown separately
    and last run's position carried alongside so movement is visible. The
    movement column is the one people actually read, and it is only meaningful
    because the ratings are re-solved from results rather than retyped by hand.

    The market column is the same team priced out of the posted point spreads,
    so the last column is the model's standing disagreement with the market
    about that team -- the thing that quietly powers most of the week's edges.
    """
    rows = []
    for team, rating in ratings.items():
        sr = score_ratings.get(team) or {}
        d = derived_rows.get(team) or {}
        rows.append({
            "team": team,
            "rating": round(rating, 2),
            "off": round(sr.get("off", 0.0), 2),
            "def": round(sr.get("def", 0.0), 2),
            "games": d.get("games", 0),
            "record": d.get("record"),
            "ppg": d.get("ppg"),
            "papg": d.get("papg"),
            "margin": d.get("margin"),
            "last5": d.get("last5"),
            "ats": form.get(team, {}).get("season_ats"),
            "ou": form.get(team, {}).get("season_ou"),
            # What the betting market makes this team worth, and where the model
            # disagrees. A team-level gap is far more informative than sixteen
            # game-level ones: if every play on the card traces back to the same
            # disagreement, that is one opinion, not six independent bets.
            "market": (round(market[team], 2) if market and team in market else None),
            "vs_market": (round(rating - market[team], 2)
                          if market and team in market else None),
            "home_field": (team_hfa or {}).get(team),
        })
    rows.sort(key=lambda r: -r["rating"])
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
        prev = (previous or {}).get(r["team"])
        r["prev_rank"] = prev
        r["move"] = (prev - i) if prev else None
    return rows
