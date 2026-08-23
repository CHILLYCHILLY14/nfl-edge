"""
ESPN public data client for the NFL.

ESPN exposes an undocumented but stable, keyless, free JSON API. Everything this
project needs comes from it -- schedule, live and final scores, venue and roof
flags, pregame odds, the official week calendar, the league injury report, news,
depth charts and team statistics. No key, no account, no quota, nothing to
expire in the middle of December.

Two endpoint habits are worth knowing because they shape the code below.

  scoreboard  takes a DATE RANGE (`dates=20260910-20260916`), so a whole NFL week
              arrives in one request. The college version of this project had to
              walk day by day because a Saturday holds sixty games; an NFL week
              is sixteen and fits comfortably.

  summary     keeps a `pickcenter` odds block AFTER a game goes final, which the
              scoreboard drops the moment the clock hits zero. That is the only
              way to recover a closing line for a bet placed on Friday, so every
              line we ever see is also snapshotted to disk as it happens.

Every auxiliary feed (injuries, news, stats, depth charts) is wrapped so that a
failure degrades that one panel to empty rather than taking down the run. The
board is the product; the news column is not worth a broken build.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Any, Iterable

import requests

SITE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
WEB = "https://site.web.api.espn.com/apis"
CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; nfl-edge/1.0; +https://github.com/)",
    "Accept": "application/json",
}

# ESPN season types. 1 = preseason, 2 = regular, 3 = postseason, 4 = pro bowl.
SEASON_TYPE_NAMES = {1: "Preseason", 2: "Regular Season", 3: "Postseason", 4: "Off Season"}


class EspnError(RuntimeError):
    pass


def _get(url: str, params: dict | None = None, tries: int = 4, timeout: int = 30) -> dict:
    """GET with polite backoff. ESPN rate-limits softly; a few retries clears it."""
    last = None
    for attempt in range(tries):
        try:
            r = requests.get(url, params=params, headers=_HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}"
        except requests.RequestException as exc:  # network blip
            last = str(exc)
        except ValueError as exc:                 # not JSON
            last = f"bad JSON: {exc}"
        time.sleep(1.5 * (attempt + 1))
    raise EspnError(f"GET {url} failed after {tries} tries: {last}")


def _try(fn, default, label: str):
    """Run an auxiliary fetch; on any failure log it and carry on with `default`."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - deliberately broad, this is the guard
        print(f"  ! {label}: {type(exc).__name__}: {exc}")
        return default


# --------------------------------------------------------------------------- #
# Core endpoints
# --------------------------------------------------------------------------- #

def scoreboard(start: dt.date, end: dt.date | None = None, limit: int = 200) -> dict:
    """Games in a date range, inclusive. One request covers a full NFL week."""
    span = start.strftime("%Y%m%d") if end is None else f'{start:%Y%m%d}-{end:%Y%m%d}'
    return _get(f"{SITE}/scoreboard", {"dates": span, "limit": limit})


def summary(event_id: str) -> dict:
    return _get(f"{SITE}/summary", {"event": event_id})


def calendar(season: int) -> list[dict]:
    """
    The league's own definition of what a week is and when it runs.

    This is what makes date switching honest. Rather than guessing that week N
    starts on some Thursday, the site reads ESPN's calendar: every entry carries
    a label, a season type, and hard start/end timestamps, including the ones
    that move -- Thanksgiving, the Christmas games, the international windows and
    the whole postseason.
    """
    data = _get(f"{SITE}/scoreboard", {"dates": season})
    leagues = data.get("leagues") or []
    cal = (leagues[0].get("calendar") if leagues else None) or []
    out: list[dict] = []
    for block in cal:
        if not isinstance(block, dict):
            continue
        stype = int(block.get("value") or 0)
        for entry in block.get("entries") or []:
            out.append({
                "season_type": stype,
                "season_type_label": block.get("label") or SEASON_TYPE_NAMES.get(stype, ""),
                "week": int(entry.get("value") or 0),
                "label": entry.get("label") or "",
                "alternate_label": (entry.get("alternateLabel") or entry.get("label") or ""),
                "start": entry.get("startDate"),
                "end": entry.get("endDate"),
            })
    return out


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def _num(v: Any) -> float | None:
    if v is None or v == "" or v == "OFF":
        return None
    if v == "EVEN":
        return 100.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pick_odds_block(odds_list: Iterable[dict], priority: list[str]) -> dict | None:
    """
    ESPN can return several providers. Prefer the ones you actually bet into, in
    the order given in settings; otherwise take whatever came first.
    """
    blocks = list(odds_list or [])
    if not blocks:
        return None
    by_name: dict[str, dict] = {}
    for b in blocks:
        name = ((b.get("provider") or {}).get("name") or "").strip()
        by_name.setdefault(name.lower(), b)
    for want in priority:
        hit = by_name.get(want.lower())
        if hit:
            return hit
    return blocks[0]


def parse_odds(block: dict | None) -> dict:
    """
    Normalise one ESPN odds block into the fields the model needs.

    ESPN states `spread` from the HOME team's perspective (negative = home
    favoured), the same convention the workbook used, so it carries across
    unchanged and nothing needs its sign flipped downstream.
    """
    if not block:
        return {}
    away = block.get("awayTeamOdds") or {}
    home = block.get("homeTeamOdds") or {}

    def _ml(side: dict) -> float | None:
        for key in ("moneyLine", "moneyline"):
            v = _num(side.get(key))
            if v is not None:
                return v
        cur = side.get("current") or {}
        v = _num((cur.get("moneyLine") or {}).get("american"))
        if v is not None:
            return v
        opn = side.get("open") or {}
        return _num((opn.get("moneyLine") or {}).get("american"))

    def _spread_price(side: dict) -> float | None:
        cur = side.get("current") or {}
        v = _num((cur.get("pointSpread") or {}).get("american"))
        return v if v is not None else -110.0

    cur = block.get("current") or {}
    over_price = _num((cur.get("over") or {}).get("american"))
    under_price = _num((cur.get("under") or {}).get("american"))

    return {
        "book": ((block.get("provider") or {}).get("name") or "ESPN").strip(),
        "spread_home": _num(block.get("spread")),
        "spread_price_home": _spread_price(home),
        "spread_price_away": _spread_price(away),
        "total": _num(block.get("overUnder")),
        "over_price": over_price if over_price is not None else -110.0,
        "under_price": under_price if under_price is not None else -110.0,
        "ml_home": _ml(home),
        "ml_away": _ml(away),
        "details": block.get("details"),
    }


def parse_event(ev: dict, odds_priority: list[str]) -> dict | None:
    """Flatten one ESPN event into our internal game record."""
    comps = ev.get("competitions") or []
    if not comps:
        return None
    c = comps[0]
    competitors = c.get("competitors") or []
    home = next((x for x in competitors if x.get("homeAway") == "home"), None)
    away = next((x for x in competitors if x.get("homeAway") == "away"), None)
    if not home or not away:
        return None

    status = ((c.get("status") or ev.get("status") or {}).get("type") or {})
    venue = c.get("venue") or {}
    addr = venue.get("address") or {}
    season = ev.get("season") or {}

    def team(side: dict) -> dict:
        t = side.get("team") or {}
        return {
            "id": str(t.get("id") or ""),
            "abbr": (t.get("abbreviation") or t.get("shortDisplayName") or "").strip().upper(),
            "name": (t.get("displayName") or t.get("name") or "").strip(),
            "short": (t.get("shortDisplayName") or "").strip(),
            "logo": t.get("logo"),
            "color": t.get("color"),
            "record": next(
                (r.get("summary") for r in (side.get("records") or [])
                 if r.get("type") in ("total", "overall")),
                None,
            ),
        }

    def score(side: dict) -> int | None:
        try:
            return int(side.get("score"))
        except (TypeError, ValueError):
            return None

    return {
        "game_id": str(ev.get("id")),
        "date_utc": ev.get("date"),
        "name": ev.get("shortName") or ev.get("name"),
        "season": season.get("year"),
        "season_type": int(season.get("type") or 2),
        "week": ((ev.get("week") or {}).get("number")),
        "neutral": bool(c.get("neutralSite")),
        "indoor": bool(venue.get("indoor")),
        "venue": venue.get("fullName"),
        "venue_city": addr.get("city"),
        "venue_state": addr.get("state") or addr.get("country"),
        "state": status.get("state"),          # pre | in | post
        "completed": bool(status.get("completed")),
        "status_detail": status.get("shortDetail"),
        "home": team(home),
        "away": team(away),
        "home_score": score(home),
        "away_score": score(away),
        "odds": parse_odds(_pick_odds_block(c.get("odds"), odds_priority)),
        "broadcast": (c.get("broadcast")
                      or next((b.get("names", [None])[0] for b in (c.get("broadcasts") or [])
                               if b.get("names")), None)),
    }


# --------------------------------------------------------------------------- #
# Bulk fetches
# --------------------------------------------------------------------------- #

def fetch_range(start: dt.date, end: dt.date, odds_priority: list[str],
                chunk_days: int = 21) -> list[dict]:
    """
    Every NFL game between two dates, inclusive.

    Requested in chunks rather than one giant span because ESPN silently caps how
    much a single scoreboard call will return, and a quiet truncation that drops
    the back half of December is the kind of bug that only shows up in December.
    """
    out: list[dict] = []
    seen: set[str] = set()
    day = start
    while day <= end:
        hi = min(day + dt.timedelta(days=chunk_days - 1), end)
        try:
            data = scoreboard(day, hi)
        except EspnError as exc:
            print(f"  ! scoreboard {day}..{hi}: {exc}")
            day = hi + dt.timedelta(days=1)
            continue
        for ev in data.get("events") or []:
            g = parse_event(ev, odds_priority)
            if g and g["game_id"] not in seen:
                seen.add(g["game_id"])
                out.append(g)
        day = hi + dt.timedelta(days=1)
    return sorted(out, key=lambda g: (g.get("date_utc") or "", g["game_id"]))


def fetch_season(year: int, odds_priority: list[str]) -> list[dict]:
    """A whole season: preseason in late July through the Super Bowl in February."""
    return fetch_range(dt.date(year, 7, 25), dt.date(year + 1, 2, 20), odds_priority)


def odds_from_summary(event_id: str, odds_priority: list[str]) -> dict:
    """Recover odds for a game the scoreboard has already dropped (i.e. it's final)."""
    try:
        s = summary(event_id)
    except EspnError:
        return {}
    return parse_odds(_pick_odds_block(s.get("pickcenter") or [], odds_priority))


# --------------------------------------------------------------------------- #
# Injuries
# --------------------------------------------------------------------------- #

_STARTER_HINT_ORDER = ("starter", "1", "first")


def injuries() -> dict[str, list[dict]]:
    """
    The whole league's injury report in one request, keyed by team abbreviation.

    ESPN groups by team and gives, per player, a status ("Out", "Questionable",
    "Injured Reserve", ...), a position, and usually a short comment. That is
    exactly the shape the adjustment model needs.

    Team abbreviations are not on the injury payload's team block in every
    response, so this returns rows keyed by ESPN team ID as well and the caller
    resolves whichever it has.
    """
    data = _get(f"{SITE}/injuries")
    out: dict[str, list[dict]] = {}
    for team_block in data.get("injuries") or []:
        tid = str(team_block.get("id") or "")
        rows: list[dict] = []
        for inj in team_block.get("injuries") or []:
            ath = inj.get("athlete") or {}
            pos = ((ath.get("position") or {}).get("abbreviation")
                   or (ath.get("position") or {}).get("name") or "").upper()
            details = inj.get("details") or {}
            rows.append({
                "athlete_id": str(ath.get("id") or ""),
                "name": ath.get("displayName") or ath.get("shortName") or "",
                "position": pos,
                "status": (inj.get("status") or "").strip(),
                "type": (inj.get("type") or {}).get("description") if isinstance(inj.get("type"), dict) else inj.get("type"),
                "detail": details.get("detail") or details.get("type"),
                "location": details.get("location"),
                "return_date": details.get("returnDate"),
                "comment": (inj.get("shortComment") or inj.get("longComment") or "")[:400],
                "date": inj.get("date"),
                "headshot": (ath.get("headshot") or {}).get("href") if isinstance(ath.get("headshot"), dict) else None,
            })
        if tid:
            out[tid] = rows
        name = (team_block.get("displayName") or "").strip()
        if name:
            out.setdefault(f"name:{name.lower()}", rows)
    return out


def depth_chart(team_id: str, season: int) -> dict[str, list[str]]:
    """
    Position -> athlete IDs in depth order, so the model can tell a starting
    quarterback from a third-stringer.

    This matters more than it sounds. "QB is Out" is a four-point line move if
    it is the starter and a rounding error if it is the emergency third QB, and
    the injury feed alone cannot tell you which. When this call fails the caller
    falls back to a much more cautious rule rather than guessing.
    """
    data = _get(f"{CORE}/seasons/{season}/teams/{team_id}/depthcharts", timeout=20)
    out: dict[str, list[str]] = {}
    for item in data.get("items") or []:
        positions = item.get("positions") or {}
        if isinstance(positions, dict):
            iterable = positions.values()
        else:
            iterable = positions
        for pos in iterable:
            if not isinstance(pos, dict):
                continue
            abbr = ((pos.get("position") or {}).get("abbreviation") or "").upper()
            if not abbr:
                continue
            ranked = []
            for a in pos.get("athletes") or []:
                ref = (a.get("athlete") or {}).get("$ref") or ""
                aid = ref.rstrip("/").split("/")[-1].split("?")[0] if ref else str(a.get("id") or "")
                if aid:
                    ranked.append((int(a.get("rank") or 99), aid))
            ranked.sort()
            if ranked:
                out.setdefault(abbr, []).extend(aid for _, aid in ranked)
    return out


# --------------------------------------------------------------------------- #
# News and stats
# --------------------------------------------------------------------------- #

def news(limit: int = 40) -> list[dict]:
    """League news feed, trimmed to the fields the site actually renders."""
    data = _get(f"{SITE}/news", {"limit": limit})
    out = []
    for a in data.get("articles") or []:
        links = a.get("links") or {}
        web = (links.get("web") or {}).get("href")
        images = a.get("images") or []
        teams = []
        for cat in a.get("categories") or []:
            t = cat.get("team") or {}
            abbr = (t.get("abbreviation") or "").upper()
            if abbr:
                teams.append(abbr)
            elif cat.get("teamId"):
                teams.append(f"id:{cat['teamId']}")
        out.append({
            "headline": a.get("headline") or "",
            "description": a.get("description") or "",
            "published": a.get("published") or a.get("lastModified"),
            "type": a.get("type"),
            "url": web,
            "image": (images[0].get("url") if images else None),
            "teams": sorted(set(teams)),
        })
    return out


def team_stats(season: int, season_type: int = 2) -> dict[str, dict]:
    """
    Season team statistics, keyed by team abbreviation.

    Used for the Team Stats panel only -- deliberately NOT fed into the ratings.
    The ridge solve already learns team strength from margins; bolting yards per
    play on top of it double-counts the same evidence and makes the model look
    more informed than it is.
    """
    data = _get(f"{WEB}/common/v3/sports/football/nfl/statistics/byteam",
                {"region": "us", "lang": "en", "contentorigin": "espn",
                 "season": season, "seasontype": season_type, "limit": 32})
    out: dict[str, dict] = {}
    for row in data.get("teams") or []:
        team = row.get("team") or {}
        abbr = (team.get("abbreviation") or "").upper()
        if not abbr:
            continue
        stats: dict[str, Any] = {}
        for cat in row.get("categories") or []:
            cname = (cat.get("name") or "").lower()
            names = cat.get("names") or []
            values = cat.get("displayValues") or cat.get("values") or []
            ranks = cat.get("ranks") or []
            if names and values:
                for i, n in enumerate(names):
                    if i < len(values):
                        stats[f"{cname}.{n}"] = values[i]
                        if i < len(ranks):
                            stats[f"{cname}.{n}.rank"] = ranks[i]
            for s in cat.get("stats") or []:
                key = f"{cname}.{s.get('name')}"
                stats[key] = s.get("displayValue")
                if s.get("rank"):
                    stats[f"{key}.rank"] = s.get("rank")
        out[abbr] = stats
    return out


def teams() -> list[dict]:
    """The 32 clubs, with logos and colours for the site."""
    data = _get(f"{SITE}/teams", {"limit": 32})
    out = []
    for sp in data.get("sports") or []:
        for lg in sp.get("leagues") or []:
            for entry in lg.get("teams") or []:
                t = entry.get("team") or {}
                logos = t.get("logos") or []
                out.append({
                    "id": str(t.get("id") or ""),
                    "abbr": (t.get("abbreviation") or "").upper(),
                    "name": t.get("displayName"),
                    "short": t.get("shortDisplayName"),
                    "location": t.get("location"),
                    "color": t.get("color"),
                    "alt_color": t.get("alternateColor"),
                    "logo": (logos[0].get("href") if logos else None),
                })
    return out
