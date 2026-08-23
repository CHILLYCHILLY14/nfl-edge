"""
Why the model thinks what it thinks.

A projection that arrives as a bare number is unusable. You cannot tell whether
"Seattle by 2.5" came from a real read on the game or from a rating that has
been broken since Week 3, and by the time the results tell you, the season is
over. So every projection on this board ships with the arithmetic that produced
it, in the units it was produced in.

The output is a list of FACTORS. Each one is a real term in the projection --
the ratings difference, home field, rest, travel, injuries, weather, your own
overrides -- with its value in points and a note saying where the number came
from. They sum, exactly, to the model's raw line. Then the market anchor is
applied and shown as its own step, so the gap between what the model thinks and
what it is willing to bet is visible rather than buried.

Alongside that sits the EVIDENCE: the season stats behind each team's rating.
Points for and against, margin, recent form, ATS and over/under records, the
home and road splits. None of it feeds the model -- the ridge solve already
learned team strength from margins, and adding these on top would double-count
the same games -- but it is what lets a person sanity-check a rating. If the
model has a team three points above average and their evidence line reads 1-4
with a minus-nine margin, something is wrong, and you can see it in one glance
instead of finding out in November.
"""

from __future__ import annotations


def _fmt_pts(v: float) -> str:
    return f"{v:+.1f}"


def factors(g: dict, parts: dict) -> list[dict]:
    """
    The projection, itemised. Every entry is points on the home team's margin.

    `parts` is what project() recorded on the way through, so this reports what
    actually happened rather than recomputing it and hoping the two agree.
    """
    home, away = g["home"]["abbr"], g["away"]["abbr"]
    out: list[dict] = []

    rd = parts.get("rating_diff", 0.0)
    out.append({
        "label": "Power rating",
        "points": round(rd, 2),
        "note": (f'{home} {parts.get("home_rating", 0):+.1f} vs {away} '
                 f'{parts.get("away_rating", 0):+.1f} — ridge-solved from margins, '
                 f'strength of schedule already netted out'),
    })

    if parts.get("hfa"):
        out.append({
            "label": "Home field" if not g.get("neutral") else "Neutral site",
            "points": round(parts["hfa"], 2),
            "note": parts.get("hfa_source") or "Solved from this season's home results",
        })

    if parts.get("rest_adj"):
        out.append({
            "label": "Rest",
            "points": round(parts["rest_adj"], 2),
            "note": (f'{home} on {parts.get("home_rest", "?")} days, {away} on '
                     f'{parts.get("away_rest", "?")} — short weeks and byes only'),
        })

    if parts.get("travel_adj"):
        out.append({
            "label": "Travel",
            "points": round(parts["travel_adj"], 2),
            "note": parts.get("travel_note") or "Distance and time-zone shift for the road team",
        })

    inj = parts.get("injury") or {}
    if inj.get("margin_adj"):
        h, a = inj.get("home") or {}, inj.get("away") or {}
        bits = []
        for side, imp, abbr in (("home", h, home), ("away", a, away)):
            if imp.get("points"):
                names = ", ".join(f'{i["name"]} ({i["position"]}, {i["status"]})'
                                  for i in (imp.get("items") or [])[:3])
                bits.append(f'{abbr} −{imp["points"]:.1f}: {names}' if names
                            else f'{abbr} −{imp["points"]:.1f}')
        out.append({
            "label": "Injuries",
            "points": round(inj["margin_adj"], 2),
            "note": " · ".join(bits) or "No material injuries on either side",
            "detail": inj,
        })

    if parts.get("manual_margin"):
        out.append({
            "label": "Your override",
            "points": round(parts["manual_margin"], 2),
            "note": parts.get("manual_note") or "From config/overrides.json",
        })

    return out


def total_factors(g: dict, parts: dict) -> list[dict]:
    """The same treatment for the projected combined score."""
    home, away = g["home"]["abbr"], g["away"]["abbr"]
    out = [{
        "label": "Scoring ratings",
        "points": round(parts.get("base_total", 0.0), 1),
        "note": (f'{home} projects {parts.get("proj_home_pts", 0):.1f}, '
                 f'{away} {parts.get("proj_away_pts", 0):.1f} — each side solved as '
                 f'league average plus that offence minus that defence'),
    }]
    wx = parts.get("weather") or {}
    if wx.get("total_adj"):
        out.append({
            "label": "Weather",
            "points": round(wx["total_adj"], 2),
            "note": " · ".join(wx.get("reasons") or []) or "Kickoff forecast",
            "detail": wx,
        })
    if parts.get("divisional"):
        out.append({
            "label": "Division game",
            "points": round(parts["divisional"], 2),
            "note": "Teams that play twice a year and know each other cold run tighter "
                    "and lower-scoring than the same matchup between strangers",
        })
    inj = parts.get("injury") or {}
    if inj.get("total_adj"):
        out.append({
            "label": "Injuries",
            "points": round(inj["total_adj"], 2),
            "note": "Offensive personnel out on both sides",
        })
    if parts.get("manual_total"):
        out.append({
            "label": "Your override",
            "points": round(parts["manual_total"], 2),
            "note": parts.get("manual_note") or "From config/overrides.json",
        })
    return out


def evidence(g: dict, derived: dict, form: dict, league: dict,
             ratings: dict, score_ratings: dict) -> dict:
    """
    The season record behind each rating, so a projection can be checked rather
    than believed.
    """
    def side(abbr: str) -> dict:
        d = derived.get(abbr) or {}
        f = form.get(abbr) or {}
        sr = score_ratings.get(abbr) or {}
        ppg, papg = d.get("ppg"), d.get("papg")
        return {
            "team": abbr,
            "record": d.get("record") or "0-0",
            "games": d.get("games", 0),
            "ppg": ppg,
            "papg": papg,
            "ppg_vs_league": (round(ppg - league["ppg"], 1)
                              if ppg is not None and league.get("ppg") else None),
            "papg_vs_league": (round(papg - league["papg"], 1)
                               if papg is not None and league.get("papg") else None),
            "margin": d.get("margin"),
            "last5": d.get("last5"),
            "last3_margin": d.get("last3_margin"),
            "home_ppg": d.get("home_ppg"),
            "away_ppg": d.get("away_ppg"),
            "ats": f.get("season_ats"),
            "ou": f.get("season_ou"),
            "rating": round(ratings.get(abbr, 0.0), 2),
            "off": round(sr.get("off", 0.0), 2),
            "def": round(sr.get("def", 0.0), 2),
        }
    return {"home": side(g["home"]["abbr"]), "away": side(g["away"]["abbr"]),
            "league": league}


def narrative(g: dict, proj: dict, ev: dict, cand: dict | None) -> str:
    """
    One paragraph a person can argue with.

    Deliberately written in the language of disagreement with the market rather
    than prediction, because that is what a betting model actually produces. It
    does not know who wins. It has an opinion on a price.
    """
    home, away = g["home"]["abbr"], g["away"]["abbr"]
    h, a = ev["home"], ev["away"]
    bits: list[str] = []

    line = proj.get("mu", 0.0) + 0.0          # kills negative zero
    fav, dog, by = (home, away, line) if line >= 0 else (away, home, -line)
    hs = proj.get("score_home", round(proj.get("proj_home_pts", 0)))
    as_ = proj.get("score_away", round(proj.get("proj_away_pts", 0)))
    bits.append(f'Model makes it {fav} by {by:.1f} ({h["team"]} {hs} – {a["team"]} {as_}).')

    if h["games"] or a["games"]:
        bits.append(
            f'{home} are {h["record"]}, scoring {h["ppg"]} and allowing {h["papg"]} '
            f'({_signed(h["margin"])} margin); {away} are {a["record"]}, '
            f'{a["ppg"]} for and {a["papg"]} against ({_signed(a["margin"])}).'
        )
    else:
        bits.append('No games played yet — ratings are still the market\'s preseason '
                    'win totals blended with last season, so treat every number here '
                    'as a starting point rather than a read.')

    mkt = proj.get("market_mu")
    if mkt is not None:
        mkt = mkt + 0.0
        gap = proj.get("gap_raw")
        if gap is not None and abs(gap) >= 0.5:
            mfav, mby = (home, mkt) if mkt >= 0 else (away, -mkt)
            bits.append(f'The market has {mfav} by {mby:.1f}, so the model is '
                        f'{abs(gap):.1f} points away from it before anchoring — '
                        f'{"and it kept" if abs(proj.get("gap") or 0) >= 0.4 else "and it kept almost none of"} '
                        f'{abs(proj.get("gap") or 0):.1f} of that disagreement.')
        else:
            bits.append('The model and the market land on essentially the same number here, '
                        'which is the normal outcome and the reason most games are a pass.')

    if cand:
        bits.append(f'Best angle: {cand["pick"]} at {cand["price"]:+.0f} — '
                    f'{cand["model_prob"]*100:.1f}% to win against a break-even of '
                    f'{cand["breakeven"]*100:.1f}%, an edge of {cand["edge"]*100:.1f}%.')
    return " ".join(bits)


def _signed(v) -> str:
    if v is None:
        return "—"
    return f"{v:+.1f}"
