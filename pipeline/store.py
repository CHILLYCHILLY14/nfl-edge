"""
Persistent state, committed back to the repo by the scheduled job.

Two things have to survive between runs because they cannot be re-derived later:

  lines.json   Every odds snapshot we have ever seen for a game. ESPN drops odds
               from the scoreboard the moment a game goes final, so if we don't
               record the number we bet into, we can never grade the bet or
               measure closing line value. The first snapshot is the opener, the
               last one before kickoff is the close.

  ledger.json  The bet log. A bet is written once, at the moment the model
               qualified it, and then only ever graded -- never re-priced. This
               is what stops the record from being quietly rewritten every time
               the ratings move, which is the classic way a "model" ends up with
               a fake winning history.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")


def _path(name: str) -> str:
    os.makedirs(STATE_DIR, exist_ok=True)
    return os.path.join(STATE_DIR, name)


def load(name: str, default: Any) -> Any:
    p = _path(name)
    if not os.path.exists(p):
        return default
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return default


def save(name: str, data: Any) -> None:
    p = _path(name)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True, default=str)
    os.replace(tmp, p)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------- #
# Line history
# --------------------------------------------------------------------------- #

_PRICE_KEYS = ("spread_price_home", "spread_price_away", "over_price", "under_price")


def repair_fabricated_default_prices(lines: dict) -> set[str]:
    """Remove the old parser's invented ``-110`` prices in place.

    Before August 2026 the parser filled every missing spread and total price
    with -110. A single game can genuinely be -110 on all four selections, so
    this migration only activates when that exact no-moneyline shape appears
    across at least three games and six snapshots — a feed-wide signature, not
    one ordinary market. Lines and timestamps are preserved; only invented
    prices are cleared so the next live build can append the real prices.
    """
    def is_minus_110(value) -> bool:
        try:
            return float(value) == -110.0
        except (TypeError, ValueError):
            return False

    candidates: list[tuple[str, dict]] = []
    for game_id, history in (lines or {}).items():
        for snap in history or []:
            prices = [snap.get(k) for k in _PRICE_KEYS]
            if (snap.get("ml_home") is None and snap.get("ml_away") is None
                    and all(is_minus_110(v) for v in prices)):
                candidates.append((str(game_id), snap))
    game_ids = {game_id for game_id, _ in candidates}
    if len(candidates) < 6 or len(game_ids) < 3:
        return set()
    for _, snap in candidates:
        for key in _PRICE_KEYS:
            snap[key] = None
        snap["repaired"] = "removed fabricated -110 defaults"
    print(f"   odds repair: cleared {len(candidates)} fabricated price snapshot(s) "
          f"across {len(game_ids)} games")
    return game_ids

def record_lines(lines: dict, games: list[dict]) -> dict:
    """Append a snapshot for any game whose number has moved since last run."""
    ts = now_iso()
    for g in games:
        o = g.get("odds") or {}
        if not o or o.get("spread_home") is None and o.get("total") is None and o.get("ml_home") is None:
            continue
        gid = g["game_id"]
        hist = lines.setdefault(gid, [])
        snap = {
            "ts": ts,
            "book": o.get("book"),
            "spread_home": o.get("spread_home"),
            "spread_price_home": o.get("spread_price_home"),
            "spread_price_away": o.get("spread_price_away"),
            "total": o.get("total"),
            "over_price": o.get("over_price"),
            "under_price": o.get("under_price"),
            "ml_home": o.get("ml_home"),
            "ml_away": o.get("ml_away"),
        }
        if hist:
            prev = {k: v for k, v in hist[-1].items() if k != "ts"}
            if prev == {k: v for k, v in snap.items() if k != "ts"}:
                continue
        hist.append(snap)
    return lines


def opener(lines: dict, game_id: str) -> dict | None:
    h = lines.get(str(game_id)) or []
    return h[0] if h else None


def closer(lines: dict, game_id: str) -> dict | None:
    h = lines.get(str(game_id)) or []
    return h[-1] if h else None


def line_move(lines: dict, game_id: str) -> dict:
    """How far the number has travelled since it opened."""
    o, c = opener(lines, game_id), closer(lines, game_id)
    if not o or not c:
        return {}
    def diff(k):
        a, b = o.get(k), c.get(k)
        return None if (a is None or b is None) else round(b - a, 2)
    return {
        "spread": diff("spread_home"),
        "total": diff("total"),
        "ml_home": diff("ml_home"),
        "snapshots": len(lines.get(str(game_id)) or []),
        "opened_spread": o.get("spread_home"),
        "opened_total": o.get("total"),
    }
