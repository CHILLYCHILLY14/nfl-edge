"""
Play the simulated season forward one week, so the accuracy panels have data.

    python -m tools.advance_demo && python -m pipeline.build --offline

This exists to demonstrate -- and to test -- the one thing a betting model can
only prove over time: that the calls it made BEFORE a game are graded honestly
AFTER it. Running make_demo, then build, then this, then build again reproduces
the real lifecycle exactly: the shadow book records every call while the games
are still in the future, the scores land, and the same rows are graded without
ever being re-priced.

If grading worked by re-running the model on finished games, every tier would
look brilliant. This is the loop that proves it does not.
"""

from __future__ import annotations

import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state")

rng = random.Random()


def advance(weeks: int = 1) -> None:
    path = os.path.join(STATE, "games_2026.json")
    if not os.path.exists(path):
        print("no demo state — run `python -m tools.make_demo` first")
        return
    with open(path, encoding="utf-8") as fh:
        games = json.load(fh)

    pending = sorted({g["week"] for g in games
                      if not g["completed"] and int(g.get("season_type") or 2) == 2})
    if not pending:
        print("simulated season is already complete")
        return

    target = pending[:weeks]
    n = 0
    for g in games:
        if g["completed"] or g["week"] not in target or int(g.get("season_type") or 2) != 2:
            continue
        odds = g.get("odds") or {}
        spread = float(odds.get("spread_home") or 0)
        total = float(odds.get("total") or 44)
        # Outcomes drawn around the market's own number, so the simulated season
        # behaves like a market that is roughly efficient -- which is the honest
        # test. A demo where the model beats a market that was designed to lose
        # would prove nothing at all.
        margin = -spread + rng.gauss(0, 12.8)
        combined = max(13, total + rng.gauss(0, 10.2))
        hs = max(0, int(round((combined + margin) / 2)))
        as_ = max(0, int(round((combined - margin) / 2)))
        g["home_score"], g["away_score"] = hs, as_
        g["completed"] = True
        g["state"] = "post"
        g["status_detail"] = "Final"
        n += 1

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(games, fh, indent=1, sort_keys=True, default=str)
    print(f"played {n} simulated games in week(s) {', '.join(map(str, target))}")
    print("  next: python -m pipeline.build --offline")


if __name__ == "__main__":
    advance(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
