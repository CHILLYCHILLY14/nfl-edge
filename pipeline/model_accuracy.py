"""Read-only model history. Frozen pre-event predictions, never actual wagers.

One preferred side per event/market is scored separately from the complete
priced-side audit. Forecasts without a sportsbook quote are tracked as well.
Missing scores/statistics stay pending; historical forecasts are never invented.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

VERSION = "2026-09-06-ev2"


def instant(value):
    try:
        if "T" not in str(value):
            return None
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d.astimezone(timezone.utc) if d.tzinfo else None
    except (TypeError, ValueError):
        return None


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def load(path):
    path = Path(path)
    # Fail a refresh on corrupt history instead of silently erasing it.
    return json.loads(path.read_text()) if path.exists() else {"records": {}}


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=1, allow_nan=False) + "\n")
    tmp.replace(path)


def key(row):
    base = ":".join(str(row.get(k) or "") for k in
                    ("kind", "league", "event_id", "player", "market", "side"))
    return base + (":" + str(row.get("line")) if row.get("kind") == "call" and row.get("player") else "")


def group(row):
    return ":".join(str(row.get(k) or "") for k in
                    ("league", "event_id", "player", "market"))


def record(log, rows, now=None):
    now = instant(now) if now is not None else datetime.now(timezone.utc)
    records = log.setdefault("records", {})
    chosen = {group(r) for r in records.values() if r.get("selected")}
    # The first selected side is immutable, including its line, tier and price.
    for source in sorted(rows, key=lambda r: -(number(r.get("edge")) or 0)):
        r = dict(source)
        k = key(r)
        if k in records:
            continue
        start = instant(r.get("start"))
        captured = instant(r.get("captured_at")) if r.get("captured_at") else now
        if not now or not start or not captured or captured >= start or captured > now:
            continue
        if r.get("completed") and not r.get("captured_at"):
            continue
        if not r.get("event_id") or r.get("kind") not in {"call", "game", "prop"}:
            continue
        if r["kind"] == "call":
            probability = number(r.get("probability"))
            if number(r.get("price")) in (None, 0) or probability is None or not 0 <= probability <= 1:
                continue
            r["probability"] = probability
            r["selected"] = group(r) not in chosen
            chosen.add(group(r))
        r.update(id=k, captured_at=captured.isoformat(), result="Pending",
                 version=r.get("version") or VERSION)
        previous = r.pop("prior_result", None)
        if previous in {"Win", "Loss", "Push", "Void"} and r["kind"] == "call":
            price = float(r["price"])
            units = (price/100 if price > 0 else 100/-price) if previous == "Win" else -1 if previous == "Loss" else 0
            r.update(result=previous, units=round(units, 6))
        r.pop("completed", None)
        records[k] = r
    return log


def settle(log, results, now=None):
    stamp = now or datetime.now(timezone.utc).isoformat()
    for r in log.get("records", {}).values():
        if r.get("result") != "Pending":
            continue
        g = results.get(str(r["event_id"])) or {}
        if g.get("canceled"):
            r.update(result="Void", graded_at=stamp, units=0)
            continue
        if not g.get("completed"):
            continue
        h, a = number(g.get("home_score")), number(g.get("away_score"))
        if r["kind"] == "game":
            if h is None or a is None:
                continue
            r.update(result="Graded", actual_margin=h-a, actual_total=h+a,
                     final_home=h, final_away=a, graded_at=stamp)
            continue
        actual = None
        if r.get("player"):
            actual = number((g.get("stats") or {}).get(r.get("stat_key")))
            if actual is None:
                # A missing player/stat is not evidence of a zero or a loss.
                continue
            if r["kind"] == "prop":
                r.update(result="Graded", actual=actual, graded_at=stamp)
                continue
            line = number(r.get("line"))
            if r.get("side") in {"yes", "no"}:
                delta = 1 if actual > 0 else -1
                if r["side"] == "no":
                    delta = -delta
            elif line is not None and r.get("side") in {"over", "under"}:
                delta = (actual-line) * (1 if r["side"] == "over" else -1)
            else:
                continue
        elif h is not None and a is not None:
            if r["market"] == "ML":
                if r.get("three_way"):
                    winner = "home" if h > a else "away" if a > h else "draw"
                    delta = 1 if r["side"] == winner else -1
                else:
                    delta = (h-a) * (1 if r["side"] == "home" else -1)
            elif r["market"] == "ATS" and number(r.get("line")) is not None:
                # Normalized line is always the selected team's handicap.
                delta = (h-a if r["side"] == "home" else a-h) + float(r["line"])
            elif r["market"] == "TOTAL" and number(r.get("line")) is not None:
                delta = (h+a-float(r["line"])) * (1 if r["side"] == "over" else -1)
            else:
                continue
        else:
            continue
        outcome = "Win" if delta > 1e-9 else "Loss" if delta < -1e-9 else "Push"
        price = float(r["price"])
        units = (price/100 if price > 0 else 100/-price) if outcome == "Win" else -1 if outcome == "Loss" else 0
        r.update(result=outcome, units=round(units, 6), actual=actual,
                 final_home=h, final_away=a, graded_at=stamp)
    return log


def mean(values):
    v = [n for x in values if (n := number(x)) is not None]
    return round(sum(v)/len(v), 5) if v else None


def call_stats(rows):
    wins = sum(r["result"] == "Win" for r in rows)
    losses = sum(r["result"] == "Loss" for r in rows)
    pushes = sum(r["result"] == "Push" for r in rows)
    voids = sum(r["result"] == "Void" for r in rows)
    settled = wins+losses+pushes
    decided = [r for r in rows if r["result"] in {"Win", "Loss"}]
    units = sum(r.get("units") or 0 for r in rows if r["result"] != "Pending")
    return {"logged": len(rows), "wins": wins, "losses": losses, "pushes": pushes,
            "voids": voids, "settled": settled, "pending": len(rows)-settled-voids,
            "win_rate": wins/(wins+losses) if wins+losses else None,
            "units": round(units, 3), "roi": units/settled if settled else None,
            "brier": mean([(r["probability"]-(r["result"] == "Win"))**2 for r in decided
                           if number(r.get("probability")) is not None])}


def buckets(rows, field):
    groups = defaultdict(list)
    for r in rows:
        groups[str(r.get(field) or "Unknown")].append(r)
    return {k: call_stats(v) for k, v in sorted(groups.items())}


def report(log, label="Model accuracy"):
    rows = list(log.get("records", {}).values())
    calls = [r for r in rows if r["kind"] == "call"]
    picks = [r for r in calls if r.get("selected")]
    games = [r for r in rows if r["kind"] == "game"]
    finals = [r for r in games if r["result"] == "Graded"]
    winner = [r for r in finals if number(r.get("margin")) not in (None, 0) and r["actual_margin"] != 0]
    ats = [r for r in finals if number(r.get("margin")) is not None and number(r.get("market_spread")) is not None
           and abs(r["margin"]+r["market_spread"]) > .25 and abs(r["actual_margin"]+r["market_spread"]) > 1e-9]
    totals = [r for r in finals if number(r.get("total")) is not None and number(r.get("market_total")) is not None
              and abs(r["total"]-r["market_total"]) > .25 and abs(r["actual_total"]-r["market_total"]) > 1e-9]
    matched_margin = [r for r in finals if number(r.get("margin")) is not None and number(r.get("market_spread")) is not None]
    matched_total = [r for r in finals if number(r.get("total")) is not None and number(r.get("market_total")) is not None]
    props = [r for r in rows if r["kind"] == "prop"]
    prop_groups = defaultdict(list)
    for r in props:
        prop_groups[r["market"]].append(r)
    calibration = []
    for i in range(10):
        rs = [r for r in picks if r["result"] in {"Win", "Loss"} and
              number(r.get("probability")) is not None and min(9, int(r["probability"]*10)) == i]
        if rs:
            calibration.append({"bucket": f"{i*10}–{(i+1)*10}%", "n": len(rs),
                                "predicted": mean([r["probability"] for r in rs]),
                                "actual": mean([r["result"] == "Win" for r in rs])})
    def rate(rs, fn):
        return {"n": len(rs), "correct": sum(bool(fn(r)) for r in rs),
                "accuracy": mean([bool(fn(r)) for r in rs])}
    return {"schema": 1, "generated_at": datetime.now(timezone.utc).isoformat(), "label": label,
            "method": "First pre-event snapshot, frozen line and price. All model picks, including PASS/AVOID and unstaked picks. One preferred side per event/market in pick statistics; the complete priced-side audit is separate. One hypothetical unit per pick; no actual wagers are created. Missing results remain pending.",
            "overall": call_stats(picks), "all_calls": call_stats(calls),
            "by_tier": buckets(picks, "tier"), "by_market": buckets(picks, "market"),
            "by_version": buckets(picks, "version"), "calibration": calibration,
            "games": {"logged": len(games), "graded": len(finals),
                      "pending": sum(r["result"] == "Pending" for r in games),
                      "winner": rate(winner, lambda r: (r["margin"] > 0) == (r["actual_margin"] > 0)),
                      "ats": rate(ats, lambda r: (r["margin"]+r["market_spread"] > 0) == (r["actual_margin"]+r["market_spread"] > 0)),
                      "totals": rate(totals, lambda r: (r["total"]-r["market_total"] > 0) == (r["actual_total"]-r["market_total"] > 0)),
                      "margin_comparison": {"n": len(matched_margin),
                          "model_mae": mean([abs(r["margin"]-r["actual_margin"]) for r in matched_margin]),
                          "market_mae": mean([abs(-r["market_spread"]-r["actual_margin"]) for r in matched_margin])},
                      "total_comparison": {"n": len(matched_total),
                          "model_mae": mean([abs(r["total"]-r["actual_total"]) for r in matched_total]),
                          "market_mae": mean([abs(r["market_total"]-r["actual_total"]) for r in matched_total])},
                      "margin_mae": mean([abs(r["margin"]-r["actual_margin"]) for r in finals if number(r.get("margin")) is not None]),
                      "total_mae": mean([abs(r["total"]-r["actual_total"]) for r in finals if number(r.get("total")) is not None]),
                      "brier": mean([(r["probability"]-(r["actual_margin"] > 0))**2 for r in finals
                                     if number(r.get("probability")) is not None and r["actual_margin"] != 0])},
            "props": {k: {"logged": len(v), "graded": sum(r["result"] == "Graded" for r in v),
                          "mae": mean([abs(r["projection"]-r["actual"]) for r in v if r["result"] == "Graded"]),
                          "bias": mean([r["projection"]-r["actual"] for r in v if r["result"] == "Graded"])} for k,v in sorted(prop_groups.items())},
            "records": sorted(rows, key=lambda r: (r.get("start") or "", r["id"]), reverse=True)}
