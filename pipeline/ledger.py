"""
The bet ledger: log once, grade once, never re-price.

A bet enters the ledger the first time the model qualifies it, capturing the
number and price available *at that moment*. Later runs may see the ratings move
or the line move; neither is allowed to touch a bet already on the book. That
one rule is what makes the recorded ROI mean something -- a model that
retroactively re-grades itself against the closing line will always look
brilliant, and always be lying.

Closing line value is computed as a separate, honest scorecard: the probability
you locked in versus the probability the market settled on. Over a few hundred
bets CLV predicts long-run profit far better than win rate does, and it tells
you whether the model is finding real edges or just getting lucky.
"""

from __future__ import annotations

from . import model as M
from . import store


def bet_key(game_id: str, market: str, side: str) -> str:
    return f"{game_id}:{market}:{side}"


def open_bet(ledger: dict, cand: dict, bankroll: float, cfg: dict) -> bool:
    """Add a qualified candidate to the ledger if it isn't already there."""
    key = bet_key(cand["game_id"], cand["market"], cand["side"])
    if key in ledger:
        return False
    # Sized off the COMPRESSED edge, not the raw one. Kelly is unforgiving of an
    # overstated probability: a model that thinks it has 12% when it has 3% does
    # not lose slowly, it eventually goes broke.
    stake = M.stake_for(cand["model_prob"], cand["price"], bankroll, cfg, edge=cand.get("edge"))
    if stake <= 0:
        return False
    ledger[key] = {
        "bet_id": key,
        "game_id": cand["game_id"],
        "placed_at": store.now_iso(),
        "game_date": cand["game_date"],
        "week": cand.get("week"),
        "matchup": cand["matchup"],
        "market": cand["market"],
        "side": cand["side"],
        "pick": cand["pick"],
        "line": cand.get("line"),
        "price": cand["price"],
        "book": cand.get("book"),
        "model_prob": round(cand["model_prob"], 4),
        "market_fair_prob": round(cand.get("market_fair_prob", 0.0), 4),
        "breakeven": round(cand["breakeven"], 4),
        "edge": round(cand["edge"], 4),
        "edge_raw": round(cand.get("edge_raw", cand["edge"]), 4),
        "line_gap": cand.get("line_gap"),
        "season_type": cand.get("season_type"),
        "ev_pct": round(cand.get("ev", 0.0), 4),
        "tier": cand["tier"],
        "confidence": cand.get("confidence"),
        "stake": stake,
        "bankroll_at_placement": round(bankroll, 2),
        "result": "Pending",
        "pnl": None,
        "closing_line": None,
        "closing_price": None,
        "clv_prob": None,
        "graded_at": None,
    }
    return True


def _grade_one(bet: dict, game: dict) -> tuple[str, float]:
    """Return (result, pnl). Grading uses the line stored ON THE BET."""
    hs, as_ = game.get("home_score"), game.get("away_score")
    if hs is None or as_ is None:
        return "Pending", 0.0
    margin = hs - as_          # home - away
    total = hs + as_
    market, side = bet["market"], bet["side"]
    line = bet.get("line")

    if market == "ML":
        won = (margin > 0) if side == "home" else (margin < 0)
        result = "Win" if won else ("Push" if margin == 0 else "Loss")
    elif market == "ATS":
        if line is None:
            return "Void", 0.0
        adj = margin + float(line)      # line is the HOME spread
        if abs(adj) < 1e-9:
            result = "Push"
        elif side == "home":
            result = "Win" if adj > 0 else "Loss"
        else:
            result = "Win" if adj < 0 else "Loss"
    elif market == "TOTAL":
        if line is None:
            return "Void", 0.0
        if abs(total - float(line)) < 1e-9:
            result = "Push"
        elif side == "over":
            result = "Win" if total > float(line) else "Loss"
        else:
            result = "Win" if total < float(line) else "Loss"
    else:
        return "Void", 0.0

    stake = float(bet["stake"])
    price = float(bet["price"])
    if result == "Win":
        pnl = stake * (M.american_to_decimal(price) - 1.0)
    elif result == "Loss":
        pnl = -stake
    else:
        pnl = 0.0
    return result, round(pnl, 2)


def grade_all(ledger: dict, games_by_id: dict, lines: dict) -> int:
    """Grade every pending bet whose game has a final score. Returns count graded."""
    n = 0
    for key, bet in ledger.items():
        if bet.get("result") not in (None, "Pending"):
            continue
        g = games_by_id.get(bet["game_id"])
        if not g or not g.get("completed"):
            continue
        result, pnl = _grade_one(bet, g)
        if result == "Pending":
            continue
        bet["result"] = result
        bet["pnl"] = pnl
        bet["graded_at"] = store.now_iso()
        bet["final_score"] = f'{g["away"]["abbr"]} {g["away_score"]} - {g["home"]["abbr"]} {g["home_score"]}'
        _attach_clv(bet, lines)
        n += 1
    return n


def _attach_clv(bet: dict, lines: dict) -> None:
    """Compare the price we took to the last price the market showed."""
    close = store.closer(lines, bet["game_id"])
    if not close:
        return
    market, side = bet["market"], bet["side"]
    if market == "ML":
        cp = close.get("ml_home") if side == "home" else close.get("ml_away")
        cl = None
    elif market == "ATS":
        cl = close.get("spread_home")
        cp = close.get("spread_price_home") if side == "home" else close.get("spread_price_away")
    else:
        cl = close.get("total")
        cp = close.get("over_price") if side == "over" else close.get("under_price")

    bet["closing_line"] = cl
    bet["closing_price"] = cp
    if cp is None:
        return

    # Price CLV: how much cheaper was our number than the closing number?
    took = M.american_to_prob(float(bet["price"]))
    closed = M.american_to_prob(float(cp))
    clv = closed - took

    # For spreads and totals the line itself moved too, which usually dwarfs the
    # price move. Convert the points gained into probability.
    #
    # Sign convention, since this is easy to get backwards: a home spread of -3.5
    # closing at -6.5 means the home team got more expensive after we bet it, so
    # a home bettor gained 3 points and an away bettor lost 3. A total of 52
    # closing at 55 means the over got harder to hit after we took it, so the
    # over bettor gained 3 and the under bettor lost 3.
    if market == "ATS" and cl is not None and bet.get("line") is not None:
        pts = float(bet["line"]) - float(cl)
        gained = pts if side == "home" else -pts
        clv += gained * 0.030   # ~3 pts of win probability per point of NFL spread
    elif market == "TOTAL" and cl is not None and bet.get("line") is not None:
        pts = float(cl) - float(bet["line"])
        gained = pts if side == "over" else -pts
        clv += gained * 0.026

    bet["clv_prob"] = round(clv, 4)


def bankroll_from(ledger: dict, starting: float, before_date: str | None = None) -> float:
    """
    Bankroll from settled bets only.

    Sizing off settled results dodges the circularity the workbook called out --
    a stake can't depend on the outcome of the bet it is sizing -- without
    freezing the bankroll at its starting value forever the way the workbook did.
    """
    bk = float(starting)
    for bet in ledger.values():
        if bet.get("result") in ("Win", "Loss", "Push") and bet.get("pnl") is not None:
            if before_date and (bet.get("game_date") or "") >= before_date:
                continue
            bk += float(bet["pnl"])
    return round(bk, 2)


def summarise(ledger: dict, starting: float) -> dict:
    """Everything the Bankroll and ROI Analytics sheets used to compute."""
    bets = list(ledger.values())
    settled = [b for b in bets if b.get("result") in ("Win", "Loss", "Push")]
    pending = [b for b in bets if b.get("result") == "Pending"]

    staked = sum(float(b["stake"]) for b in settled)
    pnl = sum(float(b["pnl"] or 0) for b in settled)
    wins = sum(1 for b in settled if b["result"] == "Win")
    losses = sum(1 for b in settled if b["result"] == "Loss")
    pushes = sum(1 for b in settled if b["result"] == "Push")
    clvs = [float(b["clv_prob"]) for b in settled if b.get("clv_prob") is not None]

    def bucket(keyfn) -> dict:
        out: dict = {}
        for b in settled:
            k = keyfn(b)
            if k is None:
                continue
            row = out.setdefault(str(k), {"bets": 0, "wins": 0, "losses": 0, "pushes": 0,
                                          "staked": 0.0, "pnl": 0.0})
            row["bets"] += 1
            row["staked"] += float(b["stake"])
            row["pnl"] += float(b["pnl"] or 0)
            if b["result"] == "Win":
                row["wins"] += 1
            elif b["result"] == "Loss":
                row["losses"] += 1
            else:
                row["pushes"] += 1
        for row in out.values():
            row["roi"] = (row["pnl"] / row["staked"]) if row["staked"] else None
            row["staked"] = round(row["staked"], 2)
            row["pnl"] = round(row["pnl"], 2)
        return out

    curve = []
    running = float(starting)
    for b in sorted(settled, key=lambda x: (x.get("graded_at") or "", x.get("game_date") or "")):
        running += float(b["pnl"] or 0)
        curve.append({"date": (b.get("game_date") or "")[:10], "bankroll": round(running, 2)})

    return {
        "starting_bankroll": round(float(starting), 2),
        "current_bankroll": round(float(starting) + pnl, 2),
        "total_bets": len(bets),
        "settled": len(settled),
        "pending": len(pending),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "win_rate": (wins / (wins + losses)) if (wins + losses) else None,
        "staked": round(staked, 2),
        "pnl": round(pnl, 2),
        "roi": (pnl / staked) if staked else None,
        "avg_clv": (sum(clvs) / len(clvs)) if clvs else None,
        "clv_positive_rate": (sum(1 for c in clvs if c > 0) / len(clvs)) if clvs else None,
        "by_market": bucket(lambda b: b.get("market")),
        "by_tier": bucket(lambda b: b.get("tier")),
        "by_week": bucket(lambda b: b.get("week")),
        "curve": curve,
    }


def calibration(ledger: dict) -> list[dict]:
    """
    Does a 60% pick actually win 60% of the time?

    The single most important diagnostic a betting model can publish about
    itself, and the one almost nobody publishes. Buckets every settled bet by the
    probability the model claimed, and reports what actually happened.
    """
    buckets = [(0.0, 0.45), (0.45, 0.50), (0.50, 0.55), (0.55, 0.60),
               (0.60, 0.65), (0.65, 0.75), (0.75, 1.01)]
    rows = []
    settled = [b for b in ledger.values() if b.get("result") in ("Win", "Loss")]
    for lo, hi in buckets:
        sel = [b for b in settled if lo <= float(b["model_prob"]) < hi]
        if not sel:
            continue
        w = sum(1 for b in sel if b["result"] == "Win")
        rows.append({
            "bucket": f"{int(lo*100)}-{int(hi*100)}%",
            "n": len(sel),
            "predicted": round(sum(float(b["model_prob"]) for b in sel) / len(sel), 4),
            "actual": round(w / len(sel), 4),
        })
    return rows


def brier(ledger: dict) -> float | None:
    settled = [b for b in ledger.values() if b.get("result") in ("Win", "Loss")]
    if not settled:
        return None
    s = sum((float(b["model_prob"]) - (1.0 if b["result"] == "Win" else 0.0)) ** 2 for b in settled)
    return round(s / len(settled), 4)
