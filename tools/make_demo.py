"""
Generate a simulated season so the site can be seen before it has live data.

    python -m tools.make_demo && python -m pipeline.build --offline

Every team is real. Every game, line, score, injury and forecast is invented by
this file. It exists so the page has something to render on the day you clone
the repo, and so the test suite can drive the real pipeline end to end without a
network connection -- the ratings solve, the market anchoring, the key-number
distribution, the tiering, the shadow book and the ledger are all the production
code paths, fed synthetic input.

Anything built from this data is stamped `demo: true` in meta.json, and the site
shows a banner saying so. A betting model quietly displaying simulated results
as if they were real is the single most dishonest thing this project could do.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import random

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state")

TEAMS = [
    ("BUF", "Buffalo Bills", "2", 1.9), ("MIA", "Miami Dolphins", "15", -3.4),
    ("NE", "New England Patriots", "17", 1.2), ("NYJ", "New York Jets", "20", -2.6),
    ("BAL", "Baltimore Ravens", "33", 3.6), ("CIN", "Cincinnati Bengals", "4", 0.9),
    ("CLE", "Cleveland Browns", "5", -2.1), ("PIT", "Pittsburgh Steelers", "23", -0.2),
    ("HOU", "Houston Texans", "34", 1.1), ("IND", "Indianapolis Colts", "11", -1.0),
    ("JAX", "Jacksonville Jaguars", "30", 1.0), ("TEN", "Tennessee Titans", "10", -2.2),
    ("DEN", "Denver Broncos", "7", 1.1), ("KC", "Kansas City Chiefs", "12", 2.4),
    ("LV", "Las Vegas Raiders", "13", -2.7), ("LAC", "Los Angeles Chargers", "24", 2.3),
    ("DAL", "Dallas Cowboys", "6", 1.0), ("NYG", "New York Giants", "19", -1.2),
    ("PHI", "Philadelphia Eagles", "21", 2.5), ("WSH", "Washington Commanders", "28", -1.1),
    ("CHI", "Chicago Bears", "3", 1.2), ("DET", "Detroit Lions", "8", 2.6),
    ("GB", "Green Bay Packers", "9", 2.3), ("MIN", "Minnesota Vikings", "16", 0.1),
    ("ATL", "Atlanta Falcons", "1", -1.3), ("CAR", "Carolina Panthers", "29", -1.4),
    ("NO", "New Orleans Saints", "18", -1.5), ("TB", "Tampa Bay Buccaneers", "27", 0.2),
    ("ARI", "Arizona Cardinals", "22", -3.5), ("LAR", "Los Angeles Rams", "14", 3.5),
    ("SF", "San Francisco 49ers", "25", 2.4), ("SEA", "Seattle Seahawks", "26", 2.3),
]

VENUES = {
    "BUF": ("Highmark Stadium", "Orchard Park", False), "MIA": ("Hard Rock Stadium", "Miami Gardens", False),
    "NE": ("Gillette Stadium", "Foxborough", False), "NYJ": ("MetLife Stadium", "East Rutherford", False),
    "BAL": ("M&T Bank Stadium", "Baltimore", False), "CIN": ("Paycor Stadium", "Cincinnati", False),
    "CLE": ("Huntington Bank Field", "Cleveland", False), "PIT": ("Acrisure Stadium", "Pittsburgh", False),
    "HOU": ("NRG Stadium", "Houston", True), "IND": ("Lucas Oil Stadium", "Indianapolis", True),
    "JAX": ("EverBank Stadium", "Jacksonville", False), "TEN": ("Nissan Stadium", "Nashville", False),
    "DEN": ("Empower Field at Mile High", "Denver", False),
    "KC": ("GEHA Field at Arrowhead Stadium", "Kansas City", False),
    "LV": ("Allegiant Stadium", "Las Vegas", True), "LAC": ("SoFi Stadium", "Inglewood", False),
    "DAL": ("AT&T Stadium", "Arlington", True), "NYG": ("MetLife Stadium", "East Rutherford", False),
    "PHI": ("Lincoln Financial Field", "Philadelphia", False),
    "WSH": ("Commanders Field", "Landover", False), "CHI": ("Soldier Field", "Chicago", False),
    "DET": ("Ford Field", "Detroit", True), "GB": ("Lambeau Field", "Green Bay", False),
    "MIN": ("U.S. Bank Stadium", "Minneapolis", True), "ATL": ("Mercedes-Benz Stadium", "Atlanta", True),
    "CAR": ("Bank of America Stadium", "Charlotte", False), "NO": ("Caesars Superdome", "New Orleans", True),
    "TB": ("Raymond James Stadium", "Tampa", False), "ARI": ("State Farm Stadium", "Glendale", True),
    "LAR": ("SoFi Stadium", "Inglewood", False), "SF": ("Levi's Stadium", "Santa Clara", False),
    "SEA": ("Lumen Field", "Seattle", False),
}

BY_ABBR = {t[0]: t for t in TEAMS}
rng = random.Random(2026)


def _team_block(abbr: str) -> dict:
    name, tid = BY_ABBR[abbr][1], BY_ABBR[abbr][2]
    return {"id": tid, "abbr": abbr, "name": name, "short": name.split()[-1],
            "logo": f"https://a.espncdn.com/i/teamlogos/nfl/500/{abbr.lower()}.png",
            "color": None, "record": None}


def _price(p: float) -> int:
    """A fair-ish American price for a probability, with a bit of vig."""
    p = min(max(p * 1.025, 0.03), 0.96)
    v = -100 * p / (1 - p) if p >= 0.5 else 100 * (1 - p) / p
    return int(round(v / 5) * 5)


def _make_game(gid: int, home: str, away: str, date: dt.datetime, week: int,
               season_type: int, played: bool) -> dict:
    hr, ar = BY_ABBR[home][3], BY_ABBR[away][3]
    true_margin = hr - ar + 1.9
    venue, city, indoor = VENUES[home]

    spread = round((-true_margin + rng.gauss(0, 0.9)) * 2) / 2
    total = round((44.0 + rng.gauss(0, 3.4)) * 2) / 2
    p_home = 0.5 + (-spread) * 0.028
    g = {
        "game_id": str(gid),
        "date_utc": date.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "name": f"{away} @ {home}",
        "season": 2026 if season_type != 0 else 2025,
        "season_type": season_type,
        "week": week,
        "neutral": False,
        "indoor": indoor,
        "venue": venue, "venue_city": city, "venue_state": "",
        "state": "post" if played else "pre",
        "completed": played,
        "status_detail": "Final" if played else date.strftime("%a %-d %b, %H:%M UTC"),
        "home": _team_block(home), "away": _team_block(away),
        "home_score": None, "away_score": None,
        "broadcast": rng.choice(["CBS", "FOX", "NBC", "ESPN", "Prime Video", "NFL Net"]),
        "odds": {
            "book": "DraftKings",
            "spread_home": spread,
            "spread_price_home": -110, "spread_price_away": -110,
            "total": total, "over_price": -110, "under_price": -110,
            "ml_home": _price(p_home), "ml_away": _price(1 - p_home),
            "details": f'{home} {spread:+g}' if spread < 0 else f'{away} {-spread:+g}',
        },
    }
    if played:
        margin = true_margin + rng.gauss(0, 12.5)
        combined = max(20, total + rng.gauss(0, 10.0))
        hs = max(0, int(round((combined + margin) / 2)))
        as_ = max(0, int(round((combined - margin) / 2)))
        if hs == as_ and rng.random() > 0.02:
            hs += 3
        g["home_score"], g["away_score"] = hs, as_
    return g


def _season(year: int, season_type: int, weeks: int, start: dt.date,
            gid_base: int, played_through: int) -> list[dict]:
    games: list[dict] = []
    gid = gid_base
    abbrs = [t[0] for t in TEAMS]
    for wk in range(1, weeks + 1):
        pool = abbrs[:]
        rng.shuffle(pool)
        kickoff_day = start + dt.timedelta(days=7 * (wk - 1) + 3)
        for i in range(0, len(pool) - 1, 2):
            home, away = pool[i], pool[i + 1]
            when = dt.datetime.combine(kickoff_day + dt.timedelta(days=3),
                                       dt.time(17 + (i % 3) * 3, 0))
            games.append(_make_game(gid, home, away, when, wk, season_type,
                                    played=wk <= played_through))
            gid += 1
    return games


def build() -> None:
    os.makedirs(STATE, exist_ok=True)
    today = dt.date.today()

    # Prior season: fully played, so the preseason prior has something to solve.
    history = _season(2025, 2, 18, dt.date(2025, 9, 4), 400_100_000, played_through=18)

    # Current season: a handful of weeks in the books, the next few ahead.
    season_start = today - dt.timedelta(days=35)
    current = _season(2026, 2, 12, season_start, 400_200_000, played_through=5)

    # Preseason, so the "display only, never bet" path is exercised too.
    pre = _season(2026, 1, 2, today - dt.timedelta(days=6), 400_190_000, played_through=1)

    games = sorted(pre + current, key=lambda g: g["date_utc"])

    # Injuries: a plausible league-wide report, with a few starting QBs out.
    positions = ["QB", "RB", "WR", "TE", "OT", "G", "C", "DE", "DT", "LB", "CB", "S", "K"]
    statuses = ["Out", "Questionable", "Questionable", "Doubtful", "Injured Reserve", "Questionable"]
    ailments = ["Hamstring", "Ankle", "Knee", "Concussion", "Shoulder", "Groin", "Illness"]
    inj: dict[str, list[dict]] = {}
    starting_qb: dict[str, str] = {}
    for abbr, name, tid, _ in TEAMS:
        rows = []
        n = rng.randint(2, 7)
        qb_id = f"{tid}001"
        starting_qb[tid] = qb_id
        qb_hurt = rng.random() < 0.12
        for j in range(n):
            pos = "QB" if (j == 0 and qb_hurt) else rng.choice(positions[1:])
            rows.append({
                "athlete_id": qb_id if pos == "QB" and j == 0 else f"{tid}{100+j}",
                "name": f"{rng.choice(['J.','T.','D.','M.','C.','A.'])} "
                        f"{rng.choice(['Carter','Reed','Bennett','Okafor','Silva','Novak','Hayes','Duncan'])}",
                "position": pos,
                "status": "Out" if (pos == "QB" and j == 0) else rng.choice(statuses),
                "type": "Injury", "detail": rng.choice(ailments), "location": None,
                "return_date": None,
                "comment": "Did not practise Wednesday or Thursday.",
                "date": (today - dt.timedelta(days=rng.randint(0, 4))).isoformat(),
                "headshot": None,
            })
        inj[tid] = rows

    # Weather: already in the shape build.py expects when offline.
    wx: dict[str, dict] = {}
    for g in games:
        if g["completed"]:
            continue
        indoor = g["indoor"]
        wind = round(rng.uniform(2, 24), 1)
        temp = round(rng.uniform(24, 82), 1)
        precip = rng.randint(0, 90)
        reasons, adj = [], 0.0
        if not indoor:
            if wind >= 20:
                adj, _ = -3.0, reasons.append(f"Wind {wind:.0f} mph (gusts {wind+7:.0f})")
            elif wind >= 16:
                adj, _ = -1.8, reasons.append(f"Wind {wind:.0f} mph (gusts {wind+6:.0f})")
            elif wind >= 12:
                adj, _ = -0.8, reasons.append(f"Wind {wind:.0f} mph")
            if precip >= 55:
                adj -= 1.0
                reasons.append(f"{precip}% chance of rain")
        wx[g["game_id"]] = {
            "venue": g["venue"], "city": g["venue_city"],
            "roof": "dome" if indoor else "open",
            "forecast": None if indoor else {
                "time_utc": g["date_utc"], "temp_f": temp, "feels_f": temp - 3,
                "wind_mph": wind, "gust_mph": round(wind + 6, 1),
                "precip_prob": precip, "precip_in": 0.0, "snow_in": 0.0,
                "cloud_pct": rng.randint(0, 100), "code": 3, "condition": "Overcast",
            },
            "total_adj": round(adj, 2), "margin_adj": 0.0,
            "reasons": reasons or (["Indoors — weather ignored"] if indoor else []),
            "applied": adj != 0.0,
        }

    news = [{
        "headline": f"[SIMULATED] {BY_ABBR[a][1]} injury report ahead of Week {rng.randint(1,12)}",
        "description": "Placeholder article generated by tools/make_demo.py. "
                       "Real runs pull the live ESPN news feed.",
        "published": (dt.datetime.utcnow() - dt.timedelta(hours=i * 3)).isoformat() + "Z",
        "type": "Story", "url": "https://www.espn.com/nfl/", "image": None, "teams": [a],
    } for i, a in enumerate([t[0] for t in TEAMS[:14]])]

    stats = {t[0]: {
        "Points/g": {"value": round(rng.uniform(15, 30), 1), "rank": rng.randint(1, 32)},
        "Total yds/g": {"value": round(rng.uniform(270, 410), 1), "rank": rng.randint(1, 32)},
        "Yards/attempt": {"value": round(rng.uniform(5.6, 8.4), 1), "rank": rng.randint(1, 32)},
        "Yards/carry": {"value": round(rng.uniform(3.5, 5.3), 1), "rank": rng.randint(1, 32)},
        "3rd down %": {"value": round(rng.uniform(31, 49), 1), "rank": rng.randint(1, 32)},
        "Turnover diff": {"value": rng.randint(-9, 9), "rank": rng.randint(1, 32)},
        "Sacks": {"value": rng.randint(6, 34), "rank": rng.randint(1, 32)},
    } for t in TEAMS}

    calendar = []
    for wk in range(1, 19):
        s = season_start + dt.timedelta(days=7 * (wk - 1))
        calendar.append({
            "season_type": 2, "season_type_label": "Regular Season", "week": wk,
            "label": f"Week {wk}", "alternate_label": f"Week {wk}",
            "start": s.isoformat() + "T07:00Z",
            "end": (s + dt.timedelta(days=6)).isoformat() + "T06:59Z",
        })

    def save(name, payload):
        with open(os.path.join(STATE, name), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1, sort_keys=True, default=str)

    save("history_2025.json", history)
    save("games_2026.json", games)
    save("offline_feeds.json", {
        "demo": True, "injuries": inj, "starting_qb": starting_qb,
        "weather": wx, "news": news, "team_stats": stats, "calendar": calendar,
    })
    # A fresh demo should not inherit a previous run's bets.
    for f in ("ledger.json", "shadow.json", "lines.json", "rank_history.json"):
        p = os.path.join(STATE, f)
        if os.path.exists(p):
            os.remove(p)

    # The tripwire. build.py sees this and deletes every scrap of simulated state
    # before it ever runs live, so invented games can never end up in the same
    # ratings solve as real ones.
    with open(os.path.join(STATE, "DEMO_STATE"), "w", encoding="utf-8") as fh:
        fh.write("Simulated state written by tools/make_demo.py.\n"
                 "`python -m pipeline.build` (without --offline) deletes this whole "
                 "directory before its first live run.\n")

    print(f"demo state written to {STATE}")
    print(f"  {len(history)} prior-season games, {len(games)} current-season games")
    print("  next: python -m pipeline.build --offline")


if __name__ == "__main__":
    build()
