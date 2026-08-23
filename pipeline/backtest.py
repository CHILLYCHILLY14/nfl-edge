"""
Walk-forward backtest: how would this model have done, honestly?

    python -m pipeline.backtest
    python -m pipeline.backtest --season 2025

The word "backtest" is usually a lie. Fit a model on a whole season, then score
it against that same season, and you get a beautiful curve that means nothing --
the model already saw every result it is being graded on. This does the harder
version:

  For each week W, the ratings are solved using ONLY games that finished before
  week W began. The board for week W is then priced with those ratings and the
  market lines that existed at the time, and graded against what happened.

That is the same information the model would genuinely have had on the Friday.
It is slower and the numbers come out much worse, which is the point.

What to look at:

  * Calibration first, profit second. If the model says 57% and 57% happens,
    everything else is fixable. If it says 57% and 51% happens, no threshold
    tuning will save it.
  * The selection haircut. Compare calibration across ALL priced games against
    calibration on the selected plays only. The gap between them is the
    winner's-curse penalty, and it is what `model.selection_haircut` should be
    set to. Measure it here rather than trusting the shipped default.
  * Tier separation. BEST BET should beat GOOD should beat LEAN. If it does not
    over a full season, the thresholds are labelling noise.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections import defaultdict

from . import build as B, model as M, ratings as R, store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def week_key(g: dict) -> tuple:
    return (int(g.get("season_type") or 2), int(g.get("week") or 0))


def run(season: int, cfg: dict) -> dict:
    games = store.load(f"games_{season}.json", [])
    prior = store.load(f"history_{int(cfg['prior_season'])}.json", [])
    if not games:
        print(f"no cached games for {season}. Run `python -m pipeline.build --full` first.")
        return {}

    played = [g for g in games if g.get("completed") and g.get("home_score") is not None
              and int(g.get("season_type") or 2) == 2]
    if not played:
        print("no completed regular-season games in the cache yet.")
        return {}

    weeks = sorted({week_key(g) for g in played})
    preseason, _ = R.preseason_prior(prior, cfg)
    venues = json.load(open(os.path.join(ROOT, "config", "venues.json"), encoding="utf-8"))

    calls: list[dict] = []
    for wk in weeks:
        # Only games that had FINISHED before this week started.
        history = [g for g in played if week_key(g) < wk]
        rat, hfa = R.solve_margin_ratings(history, cfg, prior=preseason) if history \
            else (preseason, float(cfg["model"]["home_field_fallback"]))
        score_rat, league, bump = R.solve_scoring_ratings(history + prior, cfg)
        n_played = R.games_played(history)
        rests = B.rest_days(history + [g for g in played if week_key(g) == wk])

        for g in [x for x in played if week_key(x) == wk]:
            odds = g.get("odds") or {}
            if odds.get("spread_home") is None and odds.get("ml_home") is None:
                continue
            conf = M.confidence_score(n_played.get(g["home"]["abbr"], 0),
                                      n_played.get(g["away"]["abbr"], 0), True, cfg,
                                      int(g.get("season_type") or 2))
            proj = B.project(g, rat, hfa, score_rat, league, bump, rests, {}, cfg, {}, {}, venues)
            for c in B.price_game(g, proj, cfg, conf, stale=False):
                res = _settle(c, g)
                if res is None:
                    continue
                c["result"] = res
                c["units"] = (M.american_to_decimal(c["price"]) - 1.0) if res == "Win" \
                    else (-1.0 if res == "Loss" else 0.0)
                c["week"] = wk[1]
                calls.append(c)

    return summarise(calls, cfg)


def _settle(c: dict, g: dict) -> str | None:
    hs, as_ = g["home_score"], g["away_score"]
    margin, total = hs - as_, hs + as_
    if c["market"] == "ML":
        if margin == 0:
            return "Push"
        return "Win" if ((margin > 0) == (c["side"] == "home")) else "Loss"
    if c["market"] == "ATS":
        adj = margin + float(c["line"])
        if abs(adj) < 1e-9:
            return "Push"
        return "Win" if ((adj > 0) == (c["side"] == "home")) else "Loss"
    if c["market"] == "TOTAL":
        if abs(total - float(c["line"])) < 1e-9:
            return "Push"
        return "Win" if ((total > float(c["line"])) == (c["side"] == "over")) else "Loss"
    return None


def _calib(rows: list[dict]) -> tuple[float, int]:
    """Mean absolute gap between claimed probability and realised frequency."""
    buckets = defaultdict(list)
    for r in rows:
        if r["result"] not in ("Win", "Loss"):
            continue
        buckets[round(r["model_prob"] * 20) / 20].append(1.0 if r["result"] == "Win" else 0.0)
    gaps, n = [], 0
    for p, outcomes in buckets.items():
        if len(outcomes) < 8:
            continue
        gaps.append(abs(p - sum(outcomes) / len(outcomes)))
        n += len(outcomes)
    return (sum(gaps) / len(gaps) if gaps else float("nan")), n


def summarise(calls: list[dict], cfg: dict) -> dict:
    def block(rows):
        settled = [r for r in rows if r["result"] in ("Win", "Loss", "Push")]
        w = sum(1 for r in settled if r["result"] == "Win")
        l = sum(1 for r in settled if r["result"] == "Loss")
        units = sum(r["units"] for r in settled)
        cal, cal_n = _calib(settled)
        return {"n": len(settled), "record": f"{w}-{l}",
                "win_pct": (w / (w + l)) if (w + l) else None,
                "units": round(units, 2),
                "roi": round(units / len(settled), 4) if settled else None,
                "calibration_gap": round(cal, 4) if cal == cal else None,
                "calibration_n": cal_n}

    by_tier = {t: block([c for c in calls if c["tier"] == t])
               for t in ("BEST BET", "GOOD", "LEAN", "PASS")}
    all_block = block(calls)
    selected = block([c for c in calls if c["tier"] != "PASS"])
    haircut = None
    if all_block["calibration_gap"] is not None and selected["calibration_gap"] is not None:
        haircut = round(selected["calibration_gap"] - all_block["calibration_gap"], 4)

    out = {"all": all_block, "selected": selected, "by_tier": by_tier,
           "by_market": {m: block([c for c in calls if c["market"] == m])
                         for m in ("ML", "ATS", "TOTAL")},
           "implied_selection_haircut": haircut,
           "configured_selection_haircut": cfg["model"].get("selection_haircut")}
    return out


def report(res: dict) -> None:
    if not res:
        return
    print()
    print("  WALK-FORWARD BACKTEST — ratings solved only from games that had finished")
    print("  " + "-" * 74)
    header = ("{:12s} {:>6s} {:>10s} {:>8s} {:>9s} {:>8s} {:>10s}"
              .format("", "n", "record", "win%", "units", "ROI", "calib gap"))
    print("  " + header)
    for label, b in [("ALL priced", res["all"]), ("SELECTED", res["selected"])] + \
                    [(t, res["by_tier"][t]) for t in ("BEST BET", "GOOD", "LEAN", "PASS")] + \
                    [(m, res["by_market"][m]) for m in ("ML", "ATS", "TOTAL")]:
        if not b["n"]:
            continue
        win = "—" if b["win_pct"] is None else "{:.1f}%".format(b["win_pct"] * 100)
        roi = "—" if b["roi"] is None else "{:.1f}%".format(b["roi"] * 100)
        cal = "—" if b["calibration_gap"] is None else "{:.4f}".format(b["calibration_gap"])
        print("  {:12s} {:6d} {:>10s} {:>8s} {:9.2f} {:>8s} {:>10s}"
              .format(label, b["n"], b["record"], win, b["units"], roi, cal))
    print()
    h, c = res["implied_selection_haircut"], res["configured_selection_haircut"]
    if h is not None:
        print(f"  Implied selection haircut: {h:+.4f}   (configured: {c})")
        print("  That is how much worse the model's calibration is on the bets it CHOSE than")
        print("  across every game it priced -- the winner's curse, measured. If it is much")
        print("  larger than the configured value, raise model.selection_haircut in settings.")
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=None)
    args = ap.parse_args()
    cfg = B.load_cfg()
    season = args.season or int(cfg["season"])
    print(f"== backtest {season} ==")
    report(run(season, cfg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
