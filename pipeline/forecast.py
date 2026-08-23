"""
The forecast log: what the model said about every game, and what happened.

The bet ledger asks "did I make money". The shadow book asks "do the labels
mean anything". This asks the question underneath both of them, and the only one
that is really about the model rather than about the staking plan:

    Does this thing predict football games?

Every game the model prices gets a forecast recorded before kickoff -- projected
margin, projected total, projected score, win probability -- alongside the
market's number for the same game. When the game finals, both are graded against
what actually happened.

That last part is the whole point. A model's absolute error is meaningless in
isolation: NFL margins have a standard deviation over thirteen points, so
*everyone* is wrong by a lot. The number that matters is the comparison. If the
market's closing spread predicts final margins more accurately than this model
does, then the model has no business disagreeing with it, and no amount of
threshold tuning or Kelly fraction will fix that. If the model is even slightly
closer, that difference is where an edge could live.

Two forecasts are kept for each game, not one:

  first   what the model said the moment the game came into view, days out
  latest  what it said on the last run before kickoff

Comparing those two answers a question that would otherwise be invisible: does
the model actually get better as the week goes on and information arrives, or is
it just moving with the market? A model whose late forecast is no better than
its early one is not learning from the injury report -- it is following the line.

Nothing here is a bet. Games are recorded whether or not anything was staked,
which is what makes this a measurement of the model rather than a measurement of
the bets that happened to clear a threshold.
"""

from __future__ import annotations

from . import store


def _snapshot(proj: dict, odds: dict, p_home: float | None) -> dict:
    return {
        "ts": store.now_iso(),
        "model_margin": proj.get("mu"),
        "model_margin_raw": proj.get("mu_raw"),
        "model_total": proj.get("proj_total"),
        "market_spread": odds.get("spread_home"),
        "market_total": odds.get("total"),
        "score_home": proj.get("score_home"),
        "score_away": proj.get("score_away"),
        "p_home": None if p_home is None else round(p_home, 4),
    }


def record(log: dict, game: dict, proj: dict, p_home: float | None) -> bool:
    """
    Log this run's forecast for one game.

    The first forecast is written once and never touched again. The latest is
    overwritten every run until the game kicks off. Neither is ever rewritten
    after grading -- a forecast log that updates itself once the result is known
    is not a forecast log.
    """
    odds = game.get("odds") or {}
    if odds.get("spread_home") is None and odds.get("total") is None:
        return False
    gid = game["game_id"]
    snap = _snapshot(proj, odds, p_home)
    row = log.get(gid)
    if row is None:
        log[gid] = {
            "game_id": gid,
            "date": game.get("date_utc"),
            "week": game.get("week"),
            "season_type": game.get("season_type"),
            "away": game["away"]["abbr"],
            "home": game["home"]["abbr"],
            "matchup": f'{game["away"]["abbr"]} @ {game["home"]["abbr"]}',
            "first": snap,
            "latest": snap,
            "runs": 1,
            "result": "Pending",
        }
        return True
    if row.get("result") == "Pending":
        row["latest"] = snap
        row["runs"] = int(row.get("runs") or 1) + 1
    return False


def grade(log: dict, games_by_id: dict) -> int:
    """Grade every pending forecast whose game has a final score."""
    n = 0
    for gid, row in log.items():
        if row.get("result") != "Pending":
            continue
        g = games_by_id.get(gid)
        if not g or not g.get("completed"):
            continue
        hs, as_ = g.get("home_score"), g.get("away_score")
        if hs is None or as_ is None:
            continue

        margin = hs - as_          # home minus away, same convention throughout
        total = hs + as_
        row["result"] = "Graded"
        row["graded_at"] = store.now_iso()
        row["actual_margin"] = margin
        row["actual_total"] = total
        row["final_score"] = f'{row["away"]} {as_} — {row["home"]} {hs}'

        for when in ("first", "latest"):
            snap = row.get(when) or {}
            row[when] = {**snap, **_errors(snap, margin, total)}
        n += 1
    return n


def _errors(snap: dict, margin: int, total: int) -> dict:
    """Absolute errors and outright calls, for the model and for the market."""
    out: dict = {}
    mm, ms = snap.get("model_margin"), snap.get("market_spread")
    mt, mkt_t = snap.get("model_total"), snap.get("market_total")

    if mm is not None:
        out["model_margin_ae"] = round(abs(mm - margin), 2)
        # Straight up: did the side the model favoured actually win? A projected
        # margin of exactly zero is not a call, and a tie is not a result.
        if abs(mm) > 1e-9 and margin != 0:
            out["su_correct"] = (mm > 0) == (margin > 0)
    if ms is not None:
        # The market's projected margin is the negative of the home spread.
        out["market_margin_ae"] = round(abs((-ms) - margin), 2)
        if mm is not None:
            out["beat_market_margin"] = out["model_margin_ae"] < out["market_margin_ae"]
            # Which side of the market's number the model was on, and whether
            # that side covered. This is the honest ATS test: it does not care
            # what price was available or whether anything was staked.
            lean = mm - (-ms)
            cover = margin + ms
            if abs(lean) > 0.25 and abs(cover) > 1e-9:
                out["ats_correct"] = (lean > 0) == (cover > 0)
    if mt is not None:
        out["model_total_ae"] = round(abs(mt - total), 2)
    if mkt_t is not None:
        out["market_total_ae"] = round(abs(mkt_t - total), 2)
        if mt is not None:
            out["beat_market_total"] = out["model_total_ae"] < out["market_total_ae"]
            lean = mt - mkt_t
            if abs(lean) > 0.25 and abs(total - mkt_t) > 1e-9:
                out["total_correct"] = (lean > 0) == (total > mkt_t)
    return out


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def _mean(vals: list) -> float | None:
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def _rate(vals: list) -> dict:
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0, "correct": 0, "pct": None}
    hits = sum(1 for v in vals if v)
    return {"n": len(vals), "correct": hits, "pct": round(hits / len(vals), 4)}


def _block(rows: list[dict], when: str) -> dict:
    snaps = [r.get(when) or {} for r in rows]
    model_mae = _mean([s.get("model_margin_ae") for s in snaps])
    market_mae = _mean([s.get("market_margin_ae") for s in snaps])
    model_tot = _mean([s.get("model_total_ae") for s in snaps])
    market_tot = _mean([s.get("market_total_ae") for s in snaps])
    return {
        "games": len(rows),
        "straight_up": _rate([s.get("su_correct") for s in snaps]),
        "against_the_spread": _rate([s.get("ats_correct") for s in snaps]),
        "totals": _rate([s.get("total_correct") for s in snaps]),
        "margin_mae": model_mae,
        "market_margin_mae": market_mae,
        "margin_vs_market": (None if (model_mae is None or market_mae is None)
                             else round(market_mae - model_mae, 3)),
        "beat_market_margin": _rate([s.get("beat_market_margin") for s in snaps]),
        "total_mae": model_tot,
        "market_total_mae": market_tot,
        "total_vs_market": (None if (model_tot is None or market_tot is None)
                            else round(market_tot - model_tot, 3)),
        "beat_market_total": _rate([s.get("beat_market_total") for s in snaps]),
    }


def report(log: dict) -> dict:
    """Everything the Accuracy tab needs to say whether the model predicts games."""
    rows = list(log.values())
    graded = [r for r in rows if r.get("result") == "Graded"]
    pending = [r for r in rows if r.get("result") == "Pending"]

    by_week: dict[str, dict] = {}
    for r in graded:
        key = f'{r.get("season_type")}:{r.get("week")}'
        by_week.setdefault(key, []).append(r)

    latest = _block(graded, "latest")
    first = _block(graded, "first")
    sharpening = None
    if latest["margin_mae"] is not None and first["margin_mae"] is not None:
        sharpening = round(first["margin_mae"] - latest["margin_mae"], 3)

    return {
        "generated_at": store.now_iso(),
        "total_games": len(rows),
        "graded": len(graded),
        "pending": len(pending),
        "latest_forecast": latest,
        "first_forecast": first,
        "sharpening": sharpening,
        "_sharpening_note": ("Points of margin error the model sheds between its first look at a "
                             "game and its last one before kickoff. Positive means it genuinely "
                             "improves as the week's information arrives. Near zero, or negative, "
                             "means the late forecast is no better than the early one -- the model "
                             "is drifting with the line rather than learning from the injury report."),
        "by_week": {k: _block(v, "latest") for k, v in sorted(by_week.items())},
        "verdict": _verdict(latest),
    }


def _verdict(block: dict) -> str:
    """One plain sentence, because a table of MAEs does not tell you what to do."""
    n = block.get("games") or 0
    if not n:
        return ("No games graded yet. This fills in on its own as results land — every game the "
                "model priced gets scored, whether or not anything was staked on it.")
    if n < 16:
        return (f"Only {n} graded game{'s' if n != 1 else ''} so far. Nothing here means anything "
                f"until about a full week's slate has been scored, and it does not mean much until "
                f"there are a few hundred.")
    diff = block.get("margin_vs_market")
    if diff is None:
        return f"{n} games graded, but no market line to compare against."
    if diff > 0.25:
        return (f"Across {n} games the model's projected margin has been {diff:.2f} points closer "
                f"to the final result than the market's spread. That is the case for it having a "
                f"real edge -- keep watching it, because this is the number that decides "
                f"everything else.")
    if diff < -0.25:
        return (f"Across {n} games the market's spread has been {abs(diff):.2f} points closer to "
                f"the final result than this model. That is the honest reading: the market is "
                f"predicting these games better than the model is, and betting into it on the "
                f"model's disagreement is unlikely to be profitable. Raise the thresholds, or "
                f"trust the market anchor more.")
    return (f"Across {n} games the model and the market are predicting final margins about equally "
            f"well ({diff:+.2f} points). That is a respectable place to be and not, on its own, an "
            f"edge -- the edge would have to come from the price, not the projection.")
