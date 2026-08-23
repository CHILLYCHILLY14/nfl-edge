"""
Injuries, converted into points.

The workbook had a Manual Adj column and a note telling you to type a number in
when a quarterback got hurt. That is the step that never happens on a Sunday
morning, which is exactly when it matters. This module does it automatically and
then shows its working.

The method is deliberately simple and deliberately conservative.

  * Each injury has a STATUS weight. Out and IR count fully; Doubtful counts most
    of the way; Questionable counts barely at all. NFL injury designations are
    strategically vague by design -- a Questionable player suits up more often
    than not, so treating Questionable as a real absence would have the model
    fading healthy teams every week.

  * Each position has a POINTS value: how much team strength walks out of the
    building when that player is fully out. One number is large and the rest are
    small, which is the honest shape of the effect. A starting quarterback is
    worth several points. Everyone else, individually, is worth a fraction of
    one, and the market has usually priced the big ones before the model sees
    them.

  * A starting quarterback is identified from the DEPTH CHART, not from the
    injury report. "QB Out" is a four-point swing for the starter and noise for
    the third-stringer, and the injury feed alone cannot tell those apart. When
    the depth chart is unavailable the fallback is cautious rather than clever:
    the QB deduction is applied at reduced weight and the game card says so.

  * The total per team is CAPPED. Without a cap, a long Week 14 report stacks a
    dozen half-point deductions into a fake double-digit swing, and the model
    goes hunting for a bet that only exists because it added up a hamstring, a
    thumb and two healthy scratches.

Everything applied is written into the output, player by player, so any number
on the board can be traced back to the names that produced it.
"""

from __future__ import annotations

# Position groups, so the config does not need an entry for every ESPN label.
_GROUP = {
    "QB": "QB",
    "RB": "RB", "FB": "RB", "HB": "RB",
    "WR": "WR", "TE": "TE",
    "OL": "OL", "OT": "T", "T": "T", "LT": "T", "RT": "T",
    "OG": "G", "G": "G", "LG": "G", "RG": "G", "C": "C",
    "DL": "DL", "DE": "DE", "DT": "DT", "NT": "DT", "EDGE": "DE",
    "LB": "LB", "ILB": "LB", "OLB": "LB", "MLB": "LB",
    "CB": "CB", "DB": "CB", "S": "S", "FS": "S", "SS": "S",
    "K": "K", "PK": "K", "P": "P", "LS": "LS",
}

OFFENSE = {"QB", "RB", "WR", "TE", "OL", "T", "G", "C"}

# Statuses that mean the player will not be on the field, spelled the various
# ways ESPN spells them.
_OUT_LIKE = ("out", "injured reserve", "ir", "suspension", "suspended",
             "physically unable to perform", "pup", "non football injury",
             "non-football injury", "did not participate")


def status_weight(status: str, cfg: dict) -> float:
    s = (status or "").strip().lower()
    table = {k.lower(): float(v) for k, v in
             ((cfg.get("injuries") or {}).get("status_weight") or {}).items()}
    if s in table:
        return table[s]
    for key, val in table.items():
        if key and key in s:
            return val
    for token in _OUT_LIKE:
        if token in s:
            return 1.0
    return 0.0


def position_points(pos: str, cfg: dict, is_starting_qb: bool = False,
                    qb_known: bool = True) -> float:
    pts = (cfg.get("injuries") or {}).get("position_points") or {}
    group = _GROUP.get((pos or "").upper(), (pos or "").upper())
    if group == "QB":
        if is_starting_qb:
            return float(pts.get("QB_starter", 4.5))
        if not qb_known:
            # No depth chart. Split the difference rather than guessing: a QB on
            # the report is more likely the starter than not, but not by enough
            # to bet the full number on it.
            return float(pts.get("QB_starter", 4.5)) * 0.45
        return float(pts.get("QB_backup", 0.2))
    return float(pts.get(group, pts.get("default", 0.25)))


def team_impact(rows: list[dict], cfg: dict, starting_qb_id: str | None,
                qb_known: bool = True) -> dict:
    """
    Points of team strength lost, plus the itemised list behind the number.

    `rows` is one team's slice of ESPN's injury feed.
    """
    inj_cfg = cfg.get("injuries") or {}
    cap = float(inj_cfg.get("max_team_points", 7.0))
    items: list[dict] = []
    total = 0.0
    offense_total = 0.0

    for r in rows or []:
        w = status_weight(r.get("status", ""), cfg)
        if w <= 0:
            continue
        pos = (r.get("position") or "").upper()
        group = _GROUP.get(pos, pos)
        is_qb1 = bool(group == "QB" and starting_qb_id and r.get("athlete_id") == starting_qb_id)
        base = position_points(pos, cfg, is_starting_qb=is_qb1, qb_known=qb_known)
        pts = round(base * w, 2)
        if pts <= 0:
            continue
        total += pts
        if group in OFFENSE:
            offense_total += pts
        items.append({
            "name": r.get("name"),
            "position": pos,
            "status": r.get("status"),
            "detail": r.get("detail") or r.get("type"),
            "points": pts,
            "starter_qb": is_qb1,
            "comment": r.get("comment"),
        })

    items.sort(key=lambda x: -x["points"])
    raw = round(total, 2)
    capped = min(raw, cap)
    scale = (capped / raw) if raw > 0 else 1.0
    return {
        "points": round(capped, 2),
        "raw_points": raw,
        "capped": raw > cap,
        "offense_points": round(offense_total * scale, 2),
        "count_out": sum(1 for i in items if status_weight(i["status"], cfg) >= 1.0),
        "count_listed": len(rows or []),
        "qb_out": any(i["starter_qb"] for i in items),
        "qb_confident": qb_known,
        "items": items[:12],
    }


def starting_qb(depth: dict[str, list[str]] | None) -> str | None:
    qbs = (depth or {}).get("QB") or []
    return qbs[0] if qbs else None


def game_adjustment(home_imp: dict, away_imp: dict, cfg: dict) -> dict:
    """
    Combine two teams' injury loads into a line adjustment.

    Sign convention matches everything else in this project: margin is stated
    from the home team's perspective, so the HOME team's injuries push it down
    and the AWAY team's injuries push it up.
    """
    inj_cfg = cfg.get("injuries") or {}
    if not inj_cfg.get("enabled", True):
        return {"margin_adj": 0.0, "total_adj": 0.0, "home": home_imp, "away": away_imp,
                "reasons": []}

    margin = round(away_imp["points"] - home_imp["points"], 2)
    per_pt = float(inj_cfg.get("total_points_per_offensive_point", 0.55))
    total = round(-(home_imp["offense_points"] + away_imp["offense_points"]) * per_pt, 2)

    reasons: list[str] = []
    for side, imp in (("home", home_imp), ("away", away_imp)):
        if imp["qb_out"]:
            reasons.append(f"{side} starting QB out")
        elif imp["points"] >= 1.5:
            reasons.append(f"{side} down {imp['points']:.1f} pts of personnel")
    return {"margin_adj": margin, "total_adj": total,
            "home": home_imp, "away": away_imp, "reasons": reasons}


def resolve_team_rows(feed: dict, team_id: str, team_name: str) -> list[dict]:
    """The injury feed is keyed by ESPN team id, with a name key as a backstop."""
    if team_id and team_id in feed:
        return feed[team_id]
    key = f"name:{(team_name or '').lower()}"
    return feed.get(key, [])
