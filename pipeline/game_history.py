"""Adapters for team-game model accuracy, independent of wager ledgers."""
from . import model_accuracy as A


def call(row, league, historical=False):
    line = row.get("line")
    if row.get("market") == "ATS" and row.get("side") == "away" and league != "WNBA" and line is not None:
        line = -float(line)
    captured = next((row.get(k) for k in ("first_seen", "logged_at", "tracked_at") if row.get(k)), None)
    return {"kind": "call", "league": league, "event_id": str(row.get("game_id") or ""),
            "start": row.get("game_date") or row.get("tipoff"), "matchup": row.get("matchup"),
            "market": row.get("market"), "side": row.get("side"), "pick": row.get("pick"),
            "line": line, "price": row.get("price"), "book": row.get("book"),
            "probability": row.get("model_prob"), "tier": row.get("tier"),
            "edge": row.get("action_edge", row.get("edge")), "reason": row.get("filtered"),
            **({"captured_at": captured, "version": "legacy", "prior_result": row.get("result"),
                "graded_at": row.get("graded_at")} if historical else {})}


def game(row, league):
    p = row.get("projection") or {}
    o = row.get("odds") or {}
    home, away = row.get("home") or {}, row.get("away") or {}
    return {"kind": "game", "league": league, "event_id": str(row.get("game_id") or ""),
            "start": row.get("date_utc") or row.get("tipoff") or row.get("date"),
            "completed": row.get("completed"),
            "matchup": row.get("matchup") or f'{away.get("abbr")} @ {home.get("abbr")}',
            "margin": p.get("mu", p.get("margin")), "total": p.get("proj_total", p.get("total")),
            "probability": row.get("p_home"),
            "market_spread": o.get("spread_home", -p["market_margin"] if p.get("market_margin") is not None else None),
            "market_total": o.get("total", p.get("market_total"))}


def results(games):
    return {str(g["game_id"]): {"completed": g.get("completed"), "canceled": g.get("canceled"),
                              "home_score": g.get("home_score", (g.get("home") or {}).get("score")),
                              "away_score": g.get("away_score", (g.get("away") or {}).get("score"))} for g in games}


def update(state_path, output_path, candidates, forecast_games, all_games, league, historical=(), old_forecasts=None):
    log = A.load(state_path)
    # Import only a documented pregame timestamp, never reconstruct past picks.
    A.record(log, [call(r, league, True) for r in (historical if not log.get("legacy_imported") else ())
                   if any(r.get(k) for k in ("first_seen", "logged_at", "tracked_at"))])
    if old_forecasts and not log.get("legacy_imported"):
        for r in old_forecasts.values():
            snap = r.get("first") or {}
            if snap.get("ts"):
                A.record(log, [{"kind": "game", "league": league, "event_id": r["game_id"],
                                "start": r.get("date"), "matchup": r.get("matchup"),
                                "margin": snap.get("model_margin"), "total": snap.get("model_total"),
                                "probability": snap.get("p_home"), "market_spread": snap.get("market_spread"),
                                "market_total": snap.get("market_total"), "captured_at": snap["ts"], "version": "legacy"}])
    log["legacy_imported"] = True
    A.record(log, [call(r, league) for r in candidates])
    A.record(log, [game(r, league) for r in forecast_games if r.get("projection")])
    A.settle(log, results(all_games))
    A.save(state_path, log)
    output = A.report(log, f"{league} prediction accuracy")
    A.save(output_path, output)
    return output
