"""
Orchestrator. Run this and the whole board rebuilds.

    python -m pipeline.build              # normal scheduled run (rolling window)
    python -m pipeline.build --full       # full-season backfill, rebuilds the cache
    python -m pipeline.build --no-bet     # price everything, log nothing
    python -m pipeline.build --no-extras  # skip injuries/weather/news/stats

Sequence: refresh games -> snapshot odds -> pull injuries, weather, news and
stats -> re-solve ratings from results -> project every upcoming game -> anchor
to the market -> price -> tier -> record every call in the shadow book -> log
the qualified ones -> grade finals -> write the JSON the site reads.

Everything the workbook made you do by hand on a Sunday morning happens here, on
a schedule, whether or not anyone is awake.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys

from . import calibrate, espn, explain, injuries as INJ, ledger, market as MKT, model as M
from . import ratings as R, stats as ST, store, tracker, weather as WX

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE_DATA = os.path.join(ROOT, "site", "data")


def load_cfg() -> dict:
    with open(os.path.join(ROOT, "config", "settings.json"), encoding="utf-8") as fh:
        return json.load(fh)


def load_divisions() -> dict:
    p = os.path.join(ROOT, "config", "divisions.json")
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as fh:
        return (json.load(fh) or {}).get("teams") or {}


def same_division(a: str, b: str, divisions: dict) -> bool:
    da, db = divisions.get(a), divisions.get(b)
    return bool(da and db and da[0] == db[0] and da[1] == db[1])


def load_overrides() -> dict:
    p = os.path.join(ROOT, "config", "overrides.json")
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# Schedule helpers
# --------------------------------------------------------------------------- #

def merge_games(cache: list[dict], fresh: list[dict]) -> list[dict]:
    """
    Fresh data wins, except never let a blank overwrite something we already had.

    Odds specifically: once a game is final ESPN stops returning a line, so a
    naive merge would erase the closing number needed for grading and CLV.
    """
    by_id = {g["game_id"]: g for g in cache}
    for g in fresh:
        old = by_id.get(g["game_id"])
        if old:
            if not (g.get("odds") or {}).get("spread_home") and (old.get("odds") or {}).get("spread_home"):
                g["odds"] = old["odds"]
            if g.get("home_score") is None and old.get("home_score") is not None:
                g["home_score"] = old["home_score"]
                g["away_score"] = old["away_score"]
                g["completed"] = old.get("completed", g.get("completed"))
        by_id[g["game_id"]] = g
    return sorted(by_id.values(), key=lambda x: (x.get("date_utc") or "", x["game_id"]))


def rest_days(games: list[dict]) -> dict[str, int]:
    """
    Days of rest each team brings into its next game.

    Derived from the schedule itself -- another column the workbook expected you
    to type in, which the calendar already knows. This is what prices a Thursday
    game off a Sunday, and a team coming off a bye.
    """
    last: dict[str, str] = {}
    out: dict[str, int] = {}
    for g in sorted(games, key=lambda x: x.get("date_utc") or ""):
        d = (g.get("date_utc") or "")[:10]
        if not d:
            continue
        for side in ("home", "away"):
            t = g[side]["abbr"]
            prev = last.get(t)
            if prev:
                try:
                    delta = (dt.date.fromisoformat(d) - dt.date.fromisoformat(prev)).days
                    out[f'{g["game_id"]}:{side}'] = min(delta, 21)
                except ValueError:
                    pass
        if g.get("completed"):
            for side in ("home", "away"):
                last[g[side]["abbr"]] = d
    return out


def _haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Miles between two coordinates."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 7917.5 * math.asin(math.sqrt(h))


def travel_adjustment(g: dict, venues: dict, cfg: dict) -> tuple[float, str]:
    """
    Distance and body-clock cost for the road team.

    Small by design. Travel effects are real but modest and heavily overstated in
    public analysis; the one with genuine evidence behind it is a West Coast team
    playing an early Eastern kickoff, so that gets its own term.
    """
    m = cfg["model"]
    coords = venues.get("team_coords") or {}
    offs = venues.get("team_utc_offset") or {}
    home, away = g["home"]["abbr"], g["away"]["abbr"]
    if home not in coords or away not in coords:
        return 0.0, ""
    miles = _haversine(tuple(coords[away]), tuple(coords[home]))
    adj = (miles / 1000.0) * float(m.get("travel_weight_per_1000mi", 0.35))
    notes = [f"{away} travelling {miles:,.0f} mi"]

    # Body-clock penalty: a west-coast team at a 1pm ET kickoff is playing at
    # 10am on its own clock.
    try:
        ko = dt.datetime.fromisoformat((g.get("date_utc") or "").replace("Z", "+00:00"))
        local_hour = ko.hour + float(offs.get(home, -5))
        shift = float(offs.get(away, -5)) - float(offs.get(home, -5))
        if shift <= -2 and local_hour <= 14:
            adj += float(m.get("timezone_penalty", 0.4))
            notes.append(f"{away} on a {abs(shift):.0f}-hour body-clock deficit for an early kickoff")
    except (ValueError, AttributeError):
        pass
    return round(adj, 2), " · ".join(notes)


# --------------------------------------------------------------------------- #
# Projection
# --------------------------------------------------------------------------- #

def project(g: dict, rat: dict, hfa: float, score_rat: dict, league: float,
            home_bump: float, rests: dict, ovr: dict, cfg: dict,
            inj_by_game: dict, wx_by_game: dict, venues: dict,
            team_hfa: dict | None = None, divisions: dict | None = None) -> dict:
    """
    Projected margin (home - away) and projected combined total, itemised.

    Every term is kept so explain.factors() can report what actually happened
    instead of recomputing it and hoping the two agree.
    """
    h, a = g["home"]["abbr"], g["away"]["abbr"]
    rh, ra = rat.get(h), rat.get(a)
    known = rh is not None and ra is not None
    rh = rh if rh is not None else 0.0
    ra = ra if ra is not None else 0.0

    parts: dict = {"home_rating": rh, "away_rating": ra, "rating_diff": rh - ra}
    mu = rh - ra

    own_hfa = (team_hfa or {}).get(h)
    parts["hfa"] = 0.0 if g.get("neutral") else (own_hfa if own_hfa is not None else hfa)
    parts["hfa_source"] = ("neutral site" if g.get("neutral")
                           else (f"{h}'s own home field, shrunk toward the league's {hfa:.2f}"
                                 if own_hfa is not None else "league average, solved from results"))
    mu += parts["hfa"]

    rh_rest = rests.get(f'{g["game_id"]}:home')
    ra_rest = rests.get(f'{g["game_id"]}:away')
    rest_adj = 0.0
    if rh_rest is not None and ra_rest is not None:
        rest_adj = (rh_rest - ra_rest) * float(cfg["model"]["rest_day_weight"])
        bye = float(cfg["model"].get("bye_week_bonus", 0.0))
        if rh_rest >= 12 and ra_rest < 12:
            rest_adj += bye
        elif ra_rest >= 12 and rh_rest < 12:
            rest_adj -= bye
    parts["rest_adj"] = round(rest_adj, 2)
    parts["home_rest"], parts["away_rest"] = rh_rest, ra_rest
    mu += rest_adj

    travel, travel_note = travel_adjustment(g, venues, cfg)
    parts["travel_adj"], parts["travel_note"] = travel, travel_note
    mu += travel

    inj = inj_by_game.get(g["game_id"]) or {}
    parts["injury"] = inj
    mu += float(inj.get("margin_adj") or 0.0)

    o = ovr.get(g["game_id"], {}) or {}
    team_adj = (ovr.get("team_adjust") or {})
    manual = float(o.get("margin_adj", 0.0)) + float(team_adj.get(h, 0) or 0) - float(team_adj.get(a, 0) or 0)
    parts["manual_margin"] = manual
    parts["manual_note"] = o.get("note")
    mu += manual

    # ---- total ---- #
    so_h = score_rat.get(h) or {"off": 0.0, "def": 0.0}
    so_a = score_rat.get(a) or {"off": 0.0, "def": 0.0}
    pts_home = league + so_h["off"] - so_a["def"] + (0.0 if g.get("neutral") else home_bump)
    pts_away = league + so_a["off"] - so_h["def"]
    base_total = pts_home + pts_away

    wx = wx_by_game.get(g["game_id"]) or {}
    parts["weather"] = wx
    div_adj = 0.0
    if divisions and same_division(h, a, divisions):
        div_adj = float(cfg["model"].get("divisional_total_adj", 0.0))
    parts["divisional"] = div_adj
    total = base_total + float(wx.get("total_adj") or 0.0) \
        + float(inj.get("total_adj") or 0.0) + float(o.get("total_adj", 0.0)) + div_adj
    parts["base_total"] = round(base_total, 1)
    parts["manual_total"] = float(o.get("total_adj", 0.0))
    parts["proj_home_pts"] = round(pts_home, 1)
    parts["proj_away_pts"] = round(pts_away, 1)

    # ---- anchor to the market ---- #
    odds = g.get("odds") or {}
    anchored = M.blend_to_market(mu, odds.get("spread_home"), cfg)
    anchored_total = M.blend_total_to_market(total, odds.get("total"), cfg)

    # Redistribute the anchored total across the two sides, keeping the
    # projected margin consistent with the projected score.
    final_mu, final_total = anchored["mu"], anchored_total["total"]
    home_pts = (final_total + final_mu) / 2.0
    away_pts = (final_total - final_mu) / 2.0

    return {
        **anchored,
        "proj_total": final_total,
        "proj_total_raw": anchored_total["total_raw"],
        "market_total": anchored_total["market_total"],
        "total_gap": anchored_total["gap"],
        "total_gap_raw": anchored_total["gap_raw"],
        "proj_home_pts": round(home_pts, 1),
        "proj_away_pts": round(away_pts, 1),
        # Integers rounded half-up, computed once here so the score on the card,
        # the score in the written explanation and the score in the workbook are
        # always the same number. Python and JavaScript round .5 in opposite
        # directions, which is exactly how a card ends up saying "DAL 23" above a
        # sentence that says "DAL 22".
        "score_home": int(math.floor(home_pts + 0.5)),
        "score_away": int(math.floor(away_pts + 0.5)),
        "ratings_known": known,
        "parts": parts,
    }


def adverse_move(move: dict, market: str, side: str) -> float | None:
    """
    How far the market has moved AGAINST our side since we first saw the line.

    Positive means the number got worse for us. The market drifting away from an
    opinion after you form it is the single clearest signal available that the
    opinion is the stale one -- it is the same information as closing line value,
    only early enough to act on.
    """
    if not move:
        return None
    if market == "ATS" and move.get("spread") is not None:
        # move["spread"] is closing minus opening on the HOME line. Opening -3
        # drifting to -1 means a home bettor laid 3 when 1 would now do: that is
        # value lost, so it is adverse for home and favourable for away. Same
        # convention as ledger._attach_clv, deliberately -- this is that number,
        # only early enough to act on.
        d = float(move["spread"])
        return d if side == "home" else -d
    if market == "TOTAL" and move.get("total") is not None:
        d = float(move["total"])
        return -d if side == "over" else d
    if market == "ML" and move.get("ml_home") is not None:
        d = float(move["ml_home"]) / 25.0    # crude price-to-points conversion
        return d if side == "home" else -d
    return None


def price_game(g: dict, proj: dict, cfg: dict, conf: float, stale: bool,
               calib: dict | None = None, move: dict | None = None) -> list[dict]:
    """Every market on one game, priced against the book."""
    o = g.get("odds") or {}
    blend = float(cfg["model"]["market_blend"])
    sd_m = float(cfg["model"]["margin_sd"])
    sd_t = float(cfg["model"]["total_sd"])
    keys = bool(cfg["model"]["use_key_numbers"])
    ties_push = bool(cfg["model"].get("ties_are_push", True))
    mu = proj["mu"]
    out: list[dict] = []

    base = {
        "game_id": g["game_id"],
        "game_date": g.get("date_utc"),
        "week": g.get("week"),
        "season_type": g.get("season_type"),
        "matchup": f'{g["away"]["abbr"]} @ {g["home"]["abbr"]}',
        "book": o.get("book"),
        "confidence": conf,
    }

    def finish(row: dict, raw_edge: float, line_gap: float | None) -> dict:
        # Self-calibration, fitted on the model's own graded history. Applied to
        # the probability BEFORE the edge is measured, because an edge computed
        # from an uncalibrated probability is just the miscalibration wearing a
        # percentage sign.
        if calib and calib.get("enabled"):
            before = row["model_prob"]
            row["model_prob_uncalibrated"] = before
            row["model_prob"] = round(calibrate.apply(before, calib), 4)
            raw_edge = row["model_prob"] - row["breakeven"]
        row["edge_raw"] = round(raw_edge, 4)
        row["edge"] = round(M.compress_edge(raw_edge, cfg), 4)
        row["line_gap"] = line_gap
        row["adverse_move"] = adverse_move(move or {}, row["market"], row["side"])
        tier, why = M.tier_for(row["edge"], cfg, conf, line_gap=line_gap,
                               price=row["price"], stale=stale,
                               adverse=row["adverse_move"])
        row["tier"] = tier
        if why:
            row["tier_note"] = why
        return row

    # ---- Moneyline -------------------------------------------------------- #
    if cfg["markets"]["moneyline"] and o.get("ml_home") is not None and o.get("ml_away") is not None:
        raw_h, p_tie = M.moneyline_probability(mu, sd_m, keys, ties_push)
        raw_a = 1.0 - raw_h - p_tie
        be_h = M.american_to_prob(float(o["ml_home"]))
        be_a = M.american_to_prob(float(o["ml_away"]))
        fair_h, fair_a = M.devig(be_h, be_a)
        # Renormalise the model onto the non-tie space so it is comparable to a
        # de-vigged two-way market, then put the tie back as push probability.
        denom = raw_h + raw_a
        nh = raw_h / denom if denom else 0.5
        p_h = (1 - blend) * nh + blend * fair_h
        gap = proj.get("gap")
        for side, p, be, fair, price, label in (
            ("home", p_h, be_h, fair_h, float(o["ml_home"]), f'{g["home"]["abbr"]} ML'),
            ("away", 1 - p_h, be_a, fair_a, float(o["ml_away"]), f'{g["away"]["abbr"]} ML'),
        ):
            p_eff = p * (1 - p_tie)
            out.append(finish({**base, "market": "ML", "side": side, "pick": label, "line": None,
                               "price": price, "model_prob": round(p, 4),
                               "raw_model_prob": round(nh if side == "home" else 1 - nh, 4),
                               "market_fair_prob": round(fair, 4), "breakeven": round(be, 4),
                               "push_prob": round(p_tie, 4),
                               "ev": M.expected_value(p_eff, price, p_tie)},
                              p - be, gap))

    # ---- Spread ----------------------------------------------------------- #
    if cfg["markets"]["spread"] and o.get("spread_home") is not None:
        sp = float(o["spread_home"])
        pw, pp, pl = M.cover_probability(mu, sd_m, sp, keys)
        denom = pw + pl
        raw_h = pw / denom if denom else 0.5
        ph_price = float(o.get("spread_price_home") or -110)
        pa_price = float(o.get("spread_price_away") or -110)
        be_h, be_a = M.american_to_prob(ph_price), M.american_to_prob(pa_price)
        fair_h, fair_a = M.devig(be_h, be_a)
        p_h = (1 - blend) * raw_h + blend * fair_h
        gap = proj.get("gap")
        fmt = lambda x: f"{x:+g}"
        for side, p, be, fair, price, label in (
            ("home", p_h, be_h, fair_h, ph_price, f'{g["home"]["abbr"]} {fmt(sp)}'),
            ("away", 1 - p_h, be_a, fair_a, pa_price, f'{g["away"]["abbr"]} {fmt(-sp)}'),
        ):
            out.append(finish({**base, "market": "ATS", "side": side, "pick": label, "line": sp,
                               "price": price, "model_prob": round(p, 4),
                               "raw_model_prob": round(raw_h if side == "home" else 1 - raw_h, 4),
                               "market_fair_prob": round(fair, 4), "breakeven": round(be, 4),
                               "push_prob": round(pp, 4),
                               "ev": M.expected_value(p * (1 - pp), price, pp)},
                              p - be, gap))

    # ---- Total ------------------------------------------------------------ #
    if cfg["markets"]["total"] and o.get("total") is not None:
        tot = float(o["total"])
        po, pp, pu = M.over_probability(proj["proj_total"], tot, sd_t)
        denom = po + pu
        raw_o = po / denom if denom else 0.5
        op = float(o.get("over_price") or -110)
        up = float(o.get("under_price") or -110)
        be_o, be_u = M.american_to_prob(op), M.american_to_prob(up)
        fair_o, fair_u = M.devig(be_o, be_u)
        p_o = (1 - blend) * raw_o + blend * fair_o
        tgap = proj.get("total_gap")
        for side, p, be, fair, price, label in (
            ("over", p_o, be_o, fair_o, op, f"Over {tot:g}"),
            ("under", 1 - p_o, be_u, fair_u, up, f"Under {tot:g}"),
        ):
            out.append(finish({**base, "market": "TOTAL", "side": side, "pick": label, "line": tot,
                               "price": price, "model_prob": round(p, 4),
                               "raw_model_prob": round(raw_o if side == "over" else 1 - raw_o, 4),
                               "market_fair_prob": round(fair, 4), "breakeven": round(be, 4),
                               "push_prob": round(pp, 4),
                               "ev": M.expected_value(p * (1 - pp), price, pp)},
                              p - be, tgap))

    return out


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #

def apply_filters(cands: list[dict], cfg: dict, g: dict) -> list[dict]:
    f = cfg["filters"]
    stype = int(g.get("season_type") or 2)
    for c in cands:
        if stype == 1 and not f.get("bet_preseason", False):
            c["tier"] = "PASS"
            c["filtered"] = ("Preseason — priced for reference only. Preseason results are "
                             "decided by how long the starters played, which no rating model sees.")
            continue
        if stype == 3 and not f.get("bet_postseason", True):
            c["tier"] = "PASS"
            c["filtered"] = "postseason betting disabled in settings"
            continue
        if not (float(f["min_price"]) <= c["price"] <= float(f["max_price"])):
            c["tier"] = "PASS"
            c["filtered"] = f'price {c["price"]:+.0f} outside the allowed range'
        elif c["ev"] <= 0 and c["tier"] != "PASS":
            c["tier"] = "PASS"
            c["filtered"] = "negative expected value at this price"
    return cands


def correlation_guard(cands: list[dict], cfg: dict) -> list[dict]:
    """
    One angle per game.

    A team's moneyline and its spread are close to the same bet -- the outcomes
    are heavily correlated, so the pair carries roughly double the variance the
    Kelly sizing assumed. Keep the best edge, demote the rest to a note.
    """
    limit = int(cfg["filters"].get("max_bets_per_game") or 1)
    by_game: dict[str, list[dict]] = {}
    for c in cands:
        by_game.setdefault(c["game_id"], []).append(c)
    for rows in by_game.values():
        playable = [r for r in rows if r["tier"] != "PASS"]
        playable.sort(key=lambda r: (M.TIER_RANK[r["tier"]], -r["edge"]))
        for i, r in enumerate(playable):
            if i >= limit:
                r["tier"] = "PASS"
                r["filtered"] = "correlated with a stronger play on the same game"
    return cands


def weekly_cap(cands: list[dict], cfg: dict) -> list[dict]:
    """
    Cap how many bets one week can produce, best edges first.

    A low threshold against a full Sunday slate will qualify twenty plays.
    Betting all of them is not more edge, it is more variance and more exposure
    to the one thing every bet shares -- the model being wrong in the same
    direction all afternoon.
    """
    limit = int(cfg["filters"].get("max_plays_per_week") or 0)
    if limit <= 0:
        return cands
    by_week: dict[str, list[dict]] = {}
    for c in cands:
        if c["tier"] == "PASS":
            continue
        by_week.setdefault(f'{c.get("season_type")}:{c.get("week")}', []).append(c)
    for rows in by_week.values():
        rows.sort(key=lambda r: (M.TIER_RANK[r["tier"]], -r["edge"]))
        for r in rows[limit:]:
            r["tier"] = "PASS"
            r["filtered"] = f"outside the top {limit} plays for this week"
    return cands


DEMO_MARKER = "DEMO_STATE"


def purge_demo_state() -> None:
    """
    Wipe simulated state before the first real run.

    The repo ships with a generated season so the page has something to show on
    day one. That data must never survive into a live run: the fake games carry
    invented scores and invented game ids, and merging real ESPN games into them
    would leave the ratings solving over a league that is half real and half
    fiction -- a corruption that produces plausible-looking numbers and is
    almost impossible to notice from the output. So the moment a build runs for
    real, the simulated state is deleted outright.
    """
    marker = os.path.join(store.STATE_DIR, DEMO_MARKER)
    if not os.path.exists(marker):
        return
    print("-- simulated state found; clearing it before the first live run")
    for name in os.listdir(store.STATE_DIR):
        try:
            os.remove(os.path.join(store.STATE_DIR, name))
        except OSError:
            pass
    print("   state cleared. This run backfills from ESPN, which takes a few minutes.")


def current_week(cal: list[dict], today: dt.date) -> dict | None:
    """Which week we are in right now, per ESPN's own calendar."""
    now = today.isoformat()
    for entry in cal:
        s, e = (entry.get("start") or "")[:10], (entry.get("end") or "")[:10]
        if s and e and s <= now <= e:
            return entry
    upcoming = [c for c in cal if (c.get("start") or "")[:10] >= now]
    return upcoming[0] if upcoming else (cal[-1] if cal else None)


# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="full-season backfill")
    ap.add_argument("--no-bet", action="store_true", help="price only, do not log bets")
    ap.add_argument("--no-extras", action="store_true", help="skip injuries/weather/news/stats")
    ap.add_argument("--offline", action="store_true",
                    help="never touch the network; rebuild from cached state only")
    args = ap.parse_args()

    # Offline mode rebuilds the entire site from state/ without a single HTTP
    # call. It exists for three reasons: you can re-run the model after editing
    # settings without waiting on ESPN, the test suite can exercise the real
    # pipeline end to end, and a demo build can be generated before the repo has
    # ever seen live data.
    feeds = store.load("offline_feeds.json", {}) if args.offline else {}
    if not args.offline:
        purge_demo_state()

    cfg = load_cfg()
    ovr = load_overrides()
    season = int(cfg["season"])
    prior_season = int(cfg["prior_season"])
    prio = cfg["data"]["odds_provider_priority"]
    today = dt.date.today()
    print(f"== nfl-edge build {store.now_iso()} (season {season}) ==")

    # 1. Prior season -- fetched once, then cached forever.
    prior_games = store.load(f"history_{prior_season}.json", [])
    if not prior_games and not args.offline:
        print(f"-- backfilling {prior_season} season (one time, a couple of minutes)")
        prior_games = espn.fetch_season(prior_season, prio)
        store.save(f"history_{prior_season}.json", prior_games)
    print(f"   prior season games: {len(prior_games)}")

    # 2. Current season.
    cache = store.load(f"games_{season}.json", [])
    if args.offline:
        print("-- offline: using cached games only")
        fresh = []
    elif args.full or not cache:
        print("-- full season fetch")
        fresh = espn.fetch_range(dt.date(season, 7, 25), dt.date(season + 1, 2, 20), prio)
    else:
        lo = today - dt.timedelta(days=int(cfg["data"]["lookback_days"]))
        hi = today + dt.timedelta(days=int(cfg["data"]["lookahead_days"]))
        print(f"-- rolling fetch {lo} .. {hi}")
        fresh = espn.fetch_range(lo, hi, prio)
    games = merge_games(cache, fresh)
    store.save(f"games_{season}.json", games)
    finals = sum(1 for g in games if g.get("completed"))
    print(f"   current season games: {len(games)} ({finals} final)")

    cal = (feeds.get("calendar") or []) if args.offline \
        else espn._try(lambda: espn.calendar(season), [], "calendar")
    week_now = current_week(cal, today)

    # 3. Odds snapshots -- this is what makes grading and CLV possible at all.
    lines = store.record_lines(store.load("lines.json", {}), games)
    ledg = store.load("ledger.json", {})
    for bet in ledg.values():
        if args.offline:
            break
        if bet.get("result") == "Pending" and not store.closer(lines, bet["game_id"]):
            rec = espn.odds_from_summary(bet["game_id"], prio)
            if rec:
                lines.setdefault(bet["game_id"], []).append({"ts": store.now_iso(), **rec})
    store.save("lines.json", lines)

    # 4. Ratings, solved from results.
    played_counts = R.games_played(games)
    preseason, prior_note = R.preseason_prior(prior_games, cfg)
    market_rat, market_hfa = ({}, 0.0)
    if cfg["ratings"].get("use_market_spread_ratings", True):
        market_rat, market_hfa = MKT.solve(games, cfg)
        if market_rat:
            preseason, mnote = MKT.blend_prior(preseason, market_rat, played_counts, cfg)
            prior_note = f"{prior_note}, plus {mnote}"
            print(f"   market ratings solved from {len(market_rat)} teams' spreads "
                  f"(market home field {market_hfa:.2f})")
    rat, hfa = R.solve_margin_ratings(games, cfg, prior=preseason)
    if not any(g.get("completed") and int(g.get("season_type") or 2) != 1 for g in games):
        rat, hfa = preseason, float(cfg["model"]["home_field_fallback"])
        print(f"   no regular-season results yet — ratings are the preseason prior ({prior_note})")
    team_hfa = R.per_team_home_field(games, hfa, cfg)
    divisions = load_divisions()
    score_rat, league_pts, home_bump = R.solve_scoring_ratings(games + prior_games, cfg)
    played = played_counts
    form = R.ats_form(games)
    rests = rest_days(games)
    venues = WX.load_venues()
    derived = ST.derived(games)
    league_ctx = ST.league_context(derived)
    print(f"   ratings: {len(rat)} teams | home field {hfa:.2f} pts | league avg {league_pts:.1f} pts"
          + (f" | per-team home field for {len(team_hfa)}" if team_hfa else ""))

    # 5. The board: which games get priced this run.
    horizon = today + dt.timedelta(days=int(cfg["data"]["lookahead_days"]))
    upcoming = [g for g in games
                if not g.get("completed")
                and g.get("date_utc")
                and (today - dt.timedelta(days=1)).isoformat() <= g["date_utc"][:10] <= horizon.isoformat()]

    # 6. Extras: injuries, weather, news, stats.
    inj_feed, inj_by_game, wx_by_game, news_rows, team_stat_rows = {}, {}, {}, [], {}
    if not args.no_extras:
        if (cfg.get("injuries") or {}).get("enabled", True):
            inj_feed = (feeds.get("injuries") or {}) if args.offline \
                else espn._try(espn.injuries, {}, "injuries")
            depth_ok = bool((cfg.get("injuries") or {}).get("depth_chart_enabled", True)) \
                and not args.offline
            qb_cache: dict[str, tuple[str | None, bool]] = {}
            offline_qb = (feeds.get("starting_qb") or {}) if args.offline else {}

            def qb_for(team_id: str) -> tuple[str | None, bool]:
                if args.offline:
                    return offline_qb.get(team_id), bool(offline_qb)
                if team_id in qb_cache:
                    return qb_cache[team_id]
                if not depth_ok or not team_id:
                    qb_cache[team_id] = (None, False)
                    return qb_cache[team_id]
                depth = espn._try(lambda: espn.depth_chart(team_id, season), None,
                                  f"depth chart {team_id}")
                qb_cache[team_id] = (INJ.starting_qb(depth), depth is not None)
                return qb_cache[team_id]

            for g in upcoming:
                per_side = {}
                for side in ("home", "away"):
                    t = g[side]
                    rows = INJ.resolve_team_rows(inj_feed, t["id"], t["name"])
                    qb_id, known = qb_for(t["id"])
                    per_side[side] = INJ.team_impact(rows, cfg, qb_id, qb_known=known)
                inj_by_game[g["game_id"]] = INJ.game_adjustment(per_side["home"], per_side["away"], cfg)
            print(f"   injuries: {sum(len(v) for k, v in inj_feed.items() if not k.startswith('name:'))} "
                  f"listed league-wide, applied to {len(inj_by_game)} games")

        if (cfg.get("weather") or {}).get("enabled", True):
            if args.offline:
                wx_by_game = feeds.get("weather") or {}
            else:
                geo = store.load("geo.json", {})
                wx_by_game = WX.build_for_games(upcoming, cfg, geo)
                store.save("geo.json", geo)
            moved = sum(1 for v in wx_by_game.values() if v.get("applied"))
            print(f"   weather: {len(wx_by_game)} venues located, {moved} totals adjusted")

        if cfg["data"].get("fetch_news", True):
            news_rows = (feeds.get("news") or []) if args.offline else espn._try(
                lambda: espn.news(int(cfg["data"].get("news_limit", 40))), [], "news")
            print(f"   news: {len(news_rows)} articles")

        if cfg["data"].get("fetch_stats", True):
            if args.offline:
                team_stat_rows = feeds.get("team_stats") or {}
            else:
                raw_stats = espn._try(lambda: espn.team_stats(season, 2), {}, "team stats")
                if not raw_stats or all(not v for v in raw_stats.values()):
                    raw_stats = espn._try(lambda: espn.team_stats(prior_season, 2), {},
                                          "team stats (prior)")
                team_stat_rows = ST.tidy_espn(raw_stats)
            print(f"   team stats: {len(team_stat_rows)} teams")

    # 7. Self-calibration, fitted on the model's own graded history.
    shadow_now = store.load("shadow.json", {})
    calib = calibrate.fit(shadow_now, cfg)
    if calib.get("enabled"):
        print(f"   calibration: a={calib['a']} b={calib['b']} on {calib['n']} graded calls "
              f"— {calib.get('interpretation','')}")
    else:
        print(f"   calibration: off ({calib.get('reason')})")

    # 8. Price everything.
    board: list[dict] = []
    game_cards: list[dict] = []
    for g in upcoming:
        odds = g.get("odds") or {}
        has_odds = odds.get("spread_home") is not None or odds.get("ml_home") is not None
        conf = M.confidence_score(played.get(g["home"]["abbr"], 0),
                                  played.get(g["away"]["abbr"], 0),
                                  has_odds, cfg, int(g.get("season_type") or 2))
        proj = project(g, rat, hfa, score_rat, league_pts, home_bump, rests, ovr, cfg,
                       inj_by_game, wx_by_game, venues, team_hfa, divisions)
        if not proj["ratings_known"]:
            conf = min(conf, 0.4)
        snaps = len(lines.get(g["game_id"]) or [])
        stale = snaps == 0
        move = store.line_move(lines, g["game_id"])
        cands = apply_filters(price_game(g, proj, cfg, conf, stale, calib, move), cfg, g)
        for c in cands:
            c["projection"] = {k: v for k, v in proj.items() if k != "parts"}
        board.extend(cands)

        ev = explain.evidence(g, derived, form, league_ctx, rat, score_rat)
        best = min((c for c in cands if c["tier"] != "PASS"),
                   key=lambda c: (M.TIER_RANK[c["tier"]], -c["edge"]), default=None)
        game_cards.append({
            "game_id": g["game_id"],
            "date": g.get("date_utc"),
            "week": g.get("week"),
            "season_type": g.get("season_type"),
            "matchup": f'{g["away"]["abbr"]} @ {g["home"]["abbr"]}',
            "away": g["away"], "home": g["home"],
            "venue": g.get("venue"), "neutral": g.get("neutral"),
            "broadcast": g.get("broadcast"),
            "odds": odds,
            "confidence": conf,
            "projection": {k: v for k, v in proj.items() if k != "parts"},
            "factors": explain.factors(g, proj["parts"]),
            "total_factors": explain.total_factors(g, proj["parts"]),
            "evidence": ev,
            "weather": wx_by_game.get(g["game_id"]),
            "injuries": inj_by_game.get(g["game_id"]),
            "narrative": explain.narrative(g, proj, ev, best),
            "line_move": store.line_move(lines, g["game_id"]),
        })

    board = weekly_cap(correlation_guard(board, cfg), cfg)
    board.sort(key=lambda c: (M.TIER_RANK[c["tier"]], -c["edge"]))
    plays = sum(1 for c in board if c["tier"] != "PASS")
    print(f"   priced {len(upcoming)} games -> {len(board)} market lines, {plays} actionable")

    # 9. Shadow book: record EVERY call, including the passes, then grade finals.
    shadow = shadow_now
    added = tracker.record(shadow, board)
    games_by_id = {g["game_id"]: g for g in games}
    shadow_graded = tracker.grade(shadow, games_by_id)
    store.save("shadow.json", shadow)
    print(f"   shadow book: +{added} new calls, {shadow_graded} graded, {len(shadow)} tracked")

    # 10. Log qualified bets, then grade finals.
    starting = float(cfg["bankroll"]["starting"])
    opened = 0
    if not args.no_bet:
        for c in board:
            if c["tier"] == "PASS":
                continue
            bankroll = (starting if cfg["bankroll"]["size_off"] == "starting"
                        else ledger.bankroll_from(ledg, starting))
            if ledger.open_bet(ledg, c, bankroll, cfg):
                opened += 1
    graded = ledger.grade_all(ledg, games_by_id, lines)
    store.save("ledger.json", ledg)
    print(f"   ledger: +{opened} new, {graded} graded, {len(ledg)} total")

    # 11. Power rankings, with movement since the last run.
    prev_ranks = store.load("rank_history.json", {})
    rank_rows = ST.rank_table(rat, score_rat, derived, form,
                              previous=prev_ranks.get("latest"),
                              market=market_rat, team_hfa=team_hfa)
    store.save("rank_history.json", {
        "latest": {r["team"]: r["rank"] for r in rank_rows},
        "updated": store.now_iso(),
        "previous": prev_ranks.get("latest") or {},
    })

    # 12. Emit the site payload.
    os.makedirs(SITE_DATA, exist_ok=True)

    def write(name: str, payload) -> None:
        with open(os.path.join(SITE_DATA, name), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"), default=str)

    summary = ledger.summarise(ledg, starting)
    perf = tracker.report(shadow)

    write("meta.json", {
        "generated_at": store.now_iso(),
        "season": season,
        "league": "NFL",
        "demo": bool(feeds.get("demo")),
        "home_field_advantage": round(hfa, 2),
        "market_home_field": market_hfa or None,
        "calibration": calib,
        "per_team_home_field": team_hfa,
        "divisions": divisions,
        "league_avg_points": round(league_pts, 1),
        "prior_note": prior_note,
        "games_final": finals,
        "games_upcoming": len(upcoming),
        "current_week": week_now,
        "calendar": cal,
        "settings": cfg,
        "league_context": league_ctx,
        "counts": {"board": len(board), "actionable": plays, "tracked_calls": len(shadow)},
    })
    # Everything the in-browser simulator needs to price an arbitrary matchup
    # with exactly the same maths the board uses.
    write("simulator.json", {
        "generated_at": store.now_iso(),
        "home_field": round(hfa, 2),
        "market_home_field": market_hfa or round(hfa, 2),
        "per_team_home_field": team_hfa,
        "league_avg_points": round(league_pts, 1),
        "home_scoring_bump": round(home_bump, 2),
        "margin_sd": float(cfg["model"]["margin_sd"]),
        "total_sd": float(cfg["model"]["total_sd"]),
        "use_key_numbers": bool(cfg["model"]["use_key_numbers"]),
        "key_numbers": M.KEY_NUMBER_BUMPS,
        "divisional_total_adj": float(cfg["model"].get("divisional_total_adj", 0.0)),
        "divisions": divisions,
        "calibration": calib,
        "teams": {t: {"rating": round(rat[t], 2),
                      "market": market_rat.get(t),
                      "off": round((score_rat.get(t) or {}).get("off", 0.0), 2),
                      "def": round((score_rat.get(t) or {}).get("def", 0.0), 2),
                      "games": played.get(t, 0),
                      "name": next((g["home"]["name"] for g in games
                                    if g["home"]["abbr"] == t), t),
                      "logo": next((g["home"].get("logo") for g in games
                                    if g["home"]["abbr"] == t), None)}
                  for t in sorted(rat)},
    })
    write("board.json", [{**c, "line_move": store.line_move(lines, c["game_id"])} for c in board])
    write("games_detail.json", game_cards)
    write("ledger.json", sorted(ledg.values(), key=lambda b: (b.get("game_date") or ""), reverse=True))
    write("summary.json", {**summary, "calibration": ledger.calibration(ledg)})
    write("performance.json", perf)
    write("ratings.json", rank_rows)
    write("injuries.json", {k: v for k, v in inj_feed.items() if not k.startswith("name:")})
    write("news.json", news_rows)
    write("team_stats.json", {"espn": team_stat_rows, "derived": derived, "league": league_ctx})
    write("weather.json", wx_by_game)
    write("games.json", [{
        "game_id": g["game_id"], "date": g.get("date_utc"), "week": g.get("week"),
        "season_type": g.get("season_type"),
        "away": g["away"]["abbr"], "home": g["home"]["abbr"],
        "away_name": g["away"]["name"], "home_name": g["home"]["name"],
        "away_logo": g["away"].get("logo"), "home_logo": g["home"].get("logo"),
        "away_score": g.get("away_score"), "home_score": g.get("home_score"),
        "completed": g.get("completed"), "neutral": g.get("neutral"),
        "venue": g.get("venue"), "broadcast": g.get("broadcast"),
        "status": g.get("status_detail"), "odds": g.get("odds"),
    } for g in games])

    print(f"   wrote {SITE_DATA}")
    roi_txt = "n/a" if summary["roi"] is None else f"{summary['roi'] * 100:.1f}%"
    tier_line = " | ".join(
        f'{t} {perf["by_tier"][t]["record"]}'
        for t in tracker.TIER_ORDER if t in perf["by_tier"] and perf["by_tier"][t]["settled"]
    ) or "no graded calls yet"
    print(f"== bankroll {cfg['currency_symbol']}{summary['current_bankroll']} "
          f"| {summary['settled']} settled | ROI {roi_txt} ==")
    print(f"== tier accuracy: {tier_line} ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
