"""
The shadow book: grade EVERY call the model makes, including the ones it passed.

The bet ledger answers "did I make money". This answers a different and more
important question: "do the labels mean anything".

A tier is a claim. BEST BET claims it will win more often than GOOD, which
claims it beats LEAN, which claims it beats the plays the model threw away. That
claim is testable, but only if the passes are recorded too -- and a ledger that
only holds the bets you placed structurally cannot test it. If PASS plays win at
the same rate as BEST BETs, the tiering is decoration and the whole staking plan
is built on sand. You cannot find that out from a P/L column.

So every candidate the model prices is written here once, at the moment it is
first seen, frozen with the line, price, probability, confidence and tier it had
at that moment. When the game goes final, all of them are graded at a flat one
unit, whether or not a dollar was ever risked. That gives:

  * win rate and flat-stake ROI by tier -- the direct test of the labels
  * the same by market, by side, by favourite/underdog, by week, by confidence
  * calibration: when the model says 58%, does 58% happen
  * tier separation: the single number that says whether the ordering holds

Frozen at first sighting, never re-priced. A record that quietly updates itself
to whatever the model thinks now is how every home-made model ends up with a
beautiful fake history.
"""

from __future__ import annotations

from . import model as M
from . import store

TIER_ORDER = ["BEST BET", "GOOD", "LEAN", "PASS"]


def key(game_id: str, market: str, side: str) -> str:
    return f"{game_id}:{market}:{side}"


def drop_pending_for_games(shadow: dict, game_ids: set[str]) -> int:
    """Discard only ungraded calls built from known fabricated price feeds."""
    doomed = [k for k, row in shadow.items()
              if str(row.get("game_id")) in game_ids
              and row.get("result") in (None, "Pending")]
    for k in doomed:
        del shadow[k]
    return len(doomed)


def record(shadow: dict, cands: list[dict]) -> int:
    """Write each candidate once, the first time the model priced it."""
    added = 0
    for c in cands:
        k = key(c["game_id"], c["market"], c["side"])
        if k in shadow:
            # Keep the ORIGINAL call. Only fill in fields that did not exist yet.
            row = shadow[k]
            row.setdefault("closing_tier", None)
            row["closing_tier"] = c["tier"]      # what the model would say now
            continue
        shadow[k] = {
            "id": k,
            "game_id": c["game_id"],
            "first_seen": store.now_iso(),
            "game_date": c.get("game_date"),
            "week": c.get("week"),
            "season_type": c.get("season_type"),
            "matchup": c.get("matchup"),
            "market": c["market"],
            "side": c["side"],
            "pick": c.get("pick"),
            "line": c.get("line"),
            "price": c.get("price"),
            "model_prob": round(float(c.get("model_prob") or 0), 4),
            "edge": round(float(c.get("edge") or 0), 4),
            "edge_raw": round(float(c.get("edge_raw") or 0), 4),
            "confidence": c.get("confidence"),
            "line_gap": c.get("line_gap"),
            "tier": c["tier"],
            "closing_tier": c["tier"],
            "filtered": c.get("filtered"),
            "result": "Pending",
            "units": None,
            "graded_at": None,
        }
        added += 1
    return added


def grade(shadow: dict, games_by_id: dict) -> int:
    """Grade every pending shadow row whose game is final. Flat one unit each."""
    n = 0
    for row in shadow.values():
        if row.get("result") not in (None, "Pending"):
            continue
        g = games_by_id.get(row["game_id"])
        if not g or not g.get("completed"):
            continue
        hs, as_ = g.get("home_score"), g.get("away_score")
        if hs is None or as_ is None:
            continue
        result = _settle(row, hs, as_)
        if result is None:
            continue
        row["result"] = result
        row["final_score"] = f'{g["away"]["abbr"]} {as_} — {g["home"]["abbr"]} {hs}'
        row["actual_margin"] = hs - as_
        row["actual_total"] = hs + as_
        if result == "Win":
            row["units"] = round(M.american_to_decimal(float(row["price"])) - 1.0, 3)
        elif result == "Loss":
            row["units"] = -1.0
        else:
            row["units"] = 0.0
        row["graded_at"] = store.now_iso()
        n += 1
    return n


def _settle(row: dict, hs: int, as_: int) -> str | None:
    margin, total = hs - as_, hs + as_
    market, side, line = row["market"], row["side"], row.get("line")
    if market == "ML":
        if margin == 0:
            return "Push"
        won = (margin > 0) if side == "home" else (margin < 0)
        return "Win" if won else "Loss"
    if market == "ATS":
        if line is None:
            return None
        adj = margin + float(line)
        if abs(adj) < 1e-9:
            return "Push"
        return ("Win" if adj > 0 else "Loss") if side == "home" else ("Win" if adj < 0 else "Loss")
    if market == "TOTAL":
        if line is None:
            return None
        if abs(total - float(line)) < 1e-9:
            return "Push"
        over = total > float(line)
        return "Win" if (over == (side == "over")) else "Loss"
    return None


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def _bucket(rows: list[dict]) -> dict:
    settled = [r for r in rows if r.get("result") in ("Win", "Loss", "Push")]
    w = sum(1 for r in settled if r["result"] == "Win")
    l = sum(1 for r in settled if r["result"] == "Loss")
    p = sum(1 for r in settled if r["result"] == "Push")
    units = sum(float(r["units"] or 0) for r in settled)
    decided = w + l
    edges = [float(r["edge"]) for r in settled if r.get("edge") is not None]
    probs = [float(r["model_prob"]) for r in settled if r.get("model_prob") is not None]
    return {
        "n": len(rows),
        "settled": len(settled),
        "pending": len(rows) - len(settled),
        "wins": w, "losses": l, "pushes": p,
        "record": f"{w}-{l}" + (f"-{p}" if p else ""),
        "win_pct": round(w / decided, 4) if decided else None,
        "units": round(units, 2),
        "roi": round(units / len(settled), 4) if settled else None,
        "avg_edge": round(sum(edges) / len(edges), 4) if edges else None,
        "avg_model_prob": round(sum(probs) / len(probs), 4) if probs else None,
        # Break-even is what the prices actually demanded, not a flat 52.4%.
        "breakeven": round(
            sum(M.american_to_prob(float(r["price"])) for r in settled) / len(settled), 4
        ) if settled else None,
    }


def _group(rows: list[dict], keyfn, order: list | None = None) -> dict:
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        k = keyfn(r)
        if k is None:
            continue
        buckets.setdefault(str(k), []).append(r)
    keys = order if order else sorted(buckets.keys())
    return {k: _bucket(buckets[k]) for k in keys if k in buckets}


def _favdog(r: dict) -> str | None:
    if r["market"] == "ML":
        try:
            return "Favourite" if float(r["price"]) < 0 else "Underdog"
        except (TypeError, ValueError):
            return None
    if r["market"] == "ATS" and r.get("line") is not None:
        home_favoured = float(r["line"]) < 0
        return ("Favourite" if home_favoured else "Underdog") if r["side"] == "home" \
            else ("Underdog" if home_favoured else "Favourite")
    return None


def _confidence_bucket(r: dict) -> str | None:
    c = r.get("confidence")
    if c is None:
        return None
    c = float(c)
    if c < 0.5:
        return "Low (<0.50)"
    if c < 0.7:
        return "Medium (0.50-0.70)"
    if c < 0.85:
        return "High (0.70-0.85)"
    return "Very high (0.85+)"


def calibration(rows: list[dict]) -> list[dict]:
    """
    Does a 58% call actually win 58% of the time?

    The single most important diagnostic a betting model can publish about
    itself, and the one almost nobody publishes. Every graded call, bucketed by
    the probability the model claimed, against what actually happened.
    """
    edges = [(0.40, 0.475), (0.475, 0.50), (0.50, 0.525), (0.525, 0.55),
             (0.55, 0.58), (0.58, 0.62), (0.62, 0.70), (0.70, 1.01)]
    settled = [r for r in rows if r.get("result") in ("Win", "Loss")]
    out = []
    for lo, hi in edges:
        sel = [r for r in settled if lo <= float(r["model_prob"]) < hi]
        if not sel:
            continue
        w = sum(1 for r in sel if r["result"] == "Win")
        out.append({
            "bucket": f"{lo*100:.1f}–{hi*100:.1f}%",
            "n": len(sel),
            "predicted": round(sum(float(r["model_prob"]) for r in sel) / len(sel), 4),
            "actual": round(w / len(sel), 4),
            "gap": round(w / len(sel) - sum(float(r["model_prob"]) for r in sel) / len(sel), 4),
        })
    return out


def separation(by_tier: dict) -> dict:
    """
    Does the tier ordering hold?

    Reports the win rate of each tier in order and whether each step down is
    actually a step down. If BEST BET is not beating PASS, the labels are not
    describing anything real -- which is worth knowing early and is invisible in
    a normal bet log.
    """
    seq = [(t, by_tier[t]["win_pct"]) for t in TIER_ORDER
           if t in by_tier and by_tier[t]["win_pct"] is not None and by_tier[t]["settled"] >= 10]
    monotone = all(seq[i][1] >= seq[i + 1][1] - 1e-9 for i in range(len(seq) - 1)) if len(seq) > 1 else None
    best = next((v for t, v in seq if t == "BEST BET"), None)
    worst = next((v for t, v in seq if t == "PASS"), None)
    return {
        "sequence": [{"tier": t, "win_pct": v} for t, v in seq],
        "ordering_holds": monotone,
        "best_minus_pass": round(best - worst, 4) if (best is not None and worst is not None) else None,
        "note": ("Not enough settled calls yet — needs about 10 per tier before this means anything."
                 if len(seq) < 2 else
                 "Each tier should win more often than the one below it. If it does not, the "
                 "thresholds are labelling noise."),
    }


def report(shadow: dict) -> dict:
    """The whole tier-accuracy picture, ready to serialise."""
    rows = list(shadow.values())
    settled = [r for r in rows if r.get("result") in ("Win", "Loss", "Push")]
    played = [r for r in rows if r["tier"] != "PASS"]

    by_tier = _group(rows, lambda r: r["tier"], order=TIER_ORDER)
    tier_market = {}
    for t in TIER_ORDER:
        sub = [r for r in rows if r["tier"] == t]
        if sub:
            tier_market[t] = _group(sub, lambda r: r["market"], order=["ML", "ATS", "TOTAL"])

    return {
        "generated_at": store.now_iso(),
        "total_calls": len(rows),
        "settled_calls": len(settled),
        "overall": _bucket(rows),
        "actionable_only": _bucket(played),
        "by_tier": by_tier,
        "by_market": _group(rows, lambda r: r["market"], order=["ML", "ATS", "TOTAL"]),
        "by_side": _group(rows, lambda r: r["side"]),
        "by_fav_dog": _group(rows, _favdog),
        "by_confidence": _group(rows, _confidence_bucket,
                                order=["Low (<0.50)", "Medium (0.50-0.70)",
                                       "High (0.70-0.85)", "Very high (0.85+)"]),
        "by_week": _group(rows, lambda r: r.get("week")),
        "by_tier_market": tier_market,
        "calibration": calibration(rows),
        "separation": separation(by_tier),
        "brier": _brier(rows),
    }


def _brier(rows: list[dict]) -> float | None:
    """Mean squared error of the probabilities. Lower is better; 0.25 is a coin flip."""
    settled = [r for r in rows if r.get("result") in ("Win", "Loss")]
    if not settled:
        return None
    s = sum((float(r["model_prob"]) - (1.0 if r["result"] == "Win" else 0.0)) ** 2 for r in settled)
    return round(s / len(settled), 4)
