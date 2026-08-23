"""
Edge model: projection -> probability -> price comparison -> tier -> stake.

Five things here are meaningfully better than the spreadsheet this replaced.

1. KEY NUMBERS. The workbook turned a projected margin into a cover probability
   with NORM.S.DIST -- a smooth bell curve. NFL margins are the least smooth
   distribution in sport. Roughly one game in seven lands exactly on 3, and one
   in fourteen on 7, because of how scoring works. That is why -2.5 and -3.5 are
   different bets and -5.5 and -6.5 are nearly the same one. A smooth curve
   systematically overpays to buy off 3 and misprices every number beside it.
   This builds a discrete margin distribution with the real spikes in it, which
   also produces honest push probabilities on whole numbers.

2. TIES ARE PUSHES. The workbook priced an NFL tie as impossible and graded it
   as a loss. Ties are rare (~0.2% of games) but they are real, and a moneyline
   tie is refunded, not lost. Pricing that correctly is worth a fraction of a
   percent -- which is the same order as the edges being hunted, so it matters.

3. PROPER DE-VIGGING. Comparing a model probability to a raw, vig-inclusive
   implied probability confuses "I disagree with the market" with "the book
   charges juice". Those get separated here: the market's *fair* opinion is the
   de-vigged number, and the price you must beat is the break-even number. Edge
   is measured against the second, disagreement against the first.

4. EDGE COMPRESSION. The single biggest flaw in home-made betting models is that
   they report enormous edges. A model that says it has found 15% on an NFL
   side against a market with billions of dollars and every sharp in the country
   pricing it has not found 15%; it has found a bug in itself. Raw edge is
   squeezed through a tanh so small edges pass through almost untouched and
   large ones asymptote toward a ceiling. Both numbers are kept, so the
   compression is visible rather than hidden.

5. LOCK RULES. "BEST BET" now has to earn the label on more than one axis: a
   real edge, enough data to trust the rating, a line the model actually
   disagrees with, and a price worth taking. A big probability edge on a line
   the model agrees with is an artefact of the price, not a read on the game.
"""

from __future__ import annotations

import math

# Relative frequency bumps at the NFL's key numbers, from the long-run
# distribution of final margins. 3 and 7 are the great spikes; 10, 14, 6 and 4
# are meaningfully elevated; 2, 5, 9, 11, 12 and 15 sit in the troughs between
# them. These are the numbers the whole spread market is built around.
KEY_NUMBER_BUMPS = {
    0: 0.35, 1: 1.05, 2: 0.80, 3: 2.80, 4: 1.25, 5: 0.85, 6: 1.40,
    7: 2.10, 8: 1.05, 9: 0.80, 10: 1.70, 11: 0.85, 12: 0.75, 13: 0.95,
    14: 1.60, 15: 0.80, 16: 0.90, 17: 1.35, 18: 0.85, 19: 0.80,
    20: 1.00, 21: 1.20, 22: 0.80, 23: 0.85, 24: 1.15, 25: 0.80,
    26: 0.85, 27: 0.95, 28: 1.10, 29: 0.80, 30: 0.90, 31: 1.00,
    34: 0.95, 35: 0.95, 38: 0.85, 41: 0.85,
}

_MAX_MARGIN = 60


# --------------------------------------------------------------------------- #
# Odds conversions
# --------------------------------------------------------------------------- #

def american_to_decimal(american: float) -> float:
    return 1.0 + (american / 100.0 if american > 0 else 100.0 / -american)


def american_to_prob(american: float) -> float:
    """Break-even (vig-inclusive) probability for an American price."""
    return (-american / (-american + 100.0)) if american < 0 else (100.0 / (american + 100.0))


def prob_to_american(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -100.0 * p / (1 - p) if p >= 0.5 else 100.0 * (1 - p) / p


def prob_to_spread(p_home: float, sd: float) -> float:
    """Rough inverse: the spread at which a home win probability would be fair."""
    p = min(max(p_home, 0.001), 0.999)
    # inverse standard normal, Acklam-style approximation is overkill here
    z = math.sqrt(2) * _erfinv(2 * p - 1)
    return -round(z * sd, 1)


def _erfinv(x: float) -> float:
    a = 0.147
    ln = math.log(1 - x * x) if abs(x) < 1 else -30.0
    t = 2 / (math.pi * a) + ln / 2
    return math.copysign(math.sqrt(max(0.0, math.sqrt(t * t - ln / a) - t)), x)


def devig(p_a: float, p_b: float) -> tuple[float, float]:
    """
    Strip the vig from a two-way market (proportional method).

    Proportional rather than additive or Shin because on the roughly -110/-110
    two-way markets this project bets, all three agree to within a fraction of a
    point, and proportional is the one that cannot produce a negative
    probability on a lopsided line.
    """
    tot = p_a + p_b
    if tot <= 0:
        return 0.5, 0.5
    return p_a / tot, p_b / tot


# --------------------------------------------------------------------------- #
# Anchoring the projection to the market
# --------------------------------------------------------------------------- #

def blend_to_market(mu_model: float, market_spread_home: float | None, cfg: dict) -> dict:
    """
    Pull the model's line toward the market's, and cap how far apart they may be.

    This is where most of the model's humility lives, and it lives here rather
    than buried in a probability blend because it is legible: "we make it
    Seattle by 2.5, the market makes it Seattle by 3.5" is a sentence a person
    can argue with.

    The disagreement is squeezed with tanh instead of clipped, so a 2-point
    difference of opinion survives nearly intact while a 15-point one -- which is
    always a data problem, never an insight -- compresses toward the ceiling.
    """
    m = cfg["model"]
    if market_spread_home is None:
        return {"mu": round(mu_model, 2), "mu_raw": round(mu_model, 2),
                "market_mu": None, "gap": None, "gap_raw": None, "anchored": False}

    market_mu = -float(market_spread_home)          # market's projected home margin
    raw_gap = mu_model - market_mu
    ceiling = float(m.get("max_spread_disagreement", 6.0))
    squeezed = ceiling * math.tanh(raw_gap / ceiling) if ceiling > 0 else raw_gap
    kept = float(m.get("projection_blend", 0.55))
    mu = market_mu + kept * squeezed
    return {
        "mu": round(mu, 2),
        "mu_raw": round(mu_model, 2),
        "market_mu": round(market_mu, 2),
        "gap": round(mu - market_mu, 2),
        "gap_raw": round(raw_gap, 2),
        "anchored": True,
    }


def blend_total_to_market(proj_total: float, market_total: float | None, cfg: dict) -> dict:
    """Same idea for the total. Totals markets are slightly softer, so slightly more is kept."""
    m = cfg["model"]
    if market_total is None:
        return {"total": round(proj_total, 1), "total_raw": round(proj_total, 1),
                "market_total": None, "gap": None, "gap_raw": None, "anchored": False}
    raw_gap = proj_total - float(market_total)
    ceiling = float(m.get("max_spread_disagreement", 6.0)) * 1.4
    squeezed = ceiling * math.tanh(raw_gap / ceiling) if ceiling > 0 else raw_gap
    kept = min(1.0, float(m.get("projection_blend", 0.55)) + 0.05)
    tot = float(market_total) + kept * squeezed
    return {
        "total": round(tot, 1),
        "total_raw": round(proj_total, 1),
        "market_total": float(market_total),
        "gap": round(tot - float(market_total), 2),
        "gap_raw": round(raw_gap, 2),
        "anchored": True,
    }


# --------------------------------------------------------------------------- #
# Discrete margin distribution
# --------------------------------------------------------------------------- #

def _normal_pdf(x: float, mu: float, sd: float) -> float:
    z = (x - mu) / sd
    return math.exp(-0.5 * z * z) / (sd * math.sqrt(2 * math.pi))


def margin_distribution(mu: float, sd: float, use_key_numbers: bool = True) -> dict[int, float]:
    """P(final margin == k) for integer k, centred on the projected margin."""
    dist: dict[int, float] = {}
    for k in range(-_MAX_MARGIN, _MAX_MARGIN + 1):
        p = _normal_pdf(k, mu, sd)
        if use_key_numbers:
            p *= KEY_NUMBER_BUMPS.get(abs(k), 1.0)
        dist[k] = p
    tot = sum(dist.values())
    return {k: v / tot for k, v in dist.items()}


def cover_probability(mu: float, sd: float, spread_home: float,
                      use_key_numbers: bool = True) -> tuple[float, float, float]:
    """
    Home cover / push / away cover at `spread_home`.

    ESPN's sign convention: a negative spread means the home team lays points.
    """
    dist = margin_distribution(mu, sd, use_key_numbers)
    win = push = loss = 0.0
    for k, p in dist.items():
        adj = k + spread_home
        if adj > 1e-9:
            win += p
        elif adj < -1e-9:
            loss += p
        else:
            push += p
    return win, push, loss


def moneyline_probability(mu: float, sd: float, use_key_numbers: bool = True,
                          ties_are_push: bool = True) -> tuple[float, float]:
    """
    Straight-up home win probability, and the probability of a tie.

    NFL regular-season games can end level. A moneyline tie is a push -- stake
    returned -- so it is carved out of the win/loss space rather than split
    between the two sides the way a college model (where overtime always
    resolves) can get away with.
    """
    dist = margin_distribution(mu, sd, use_key_numbers)
    win = sum(p for k, p in dist.items() if k > 0)
    tie = dist.get(0, 0.0)
    if not ties_are_push:
        return win + tie / 2.0, 0.0
    return win, tie


def over_probability(proj_total: float, market_total: float, sd: float) -> tuple[float, float, float]:
    """Over / push / under for a projected combined score."""
    over = push = under = 0.0
    for k in range(0, 120):
        p = _normal_pdf(k, proj_total, sd)
        if k > market_total + 1e-9:
            over += p
        elif k < market_total - 1e-9:
            under += p
        else:
            push += p
    tot = over + push + under
    if tot <= 0:
        return 0.5, 0.0, 0.5
    return over / tot, push / tot, under / tot


# --------------------------------------------------------------------------- #
# Edge
# --------------------------------------------------------------------------- #

def compress_edge(raw: float, cfg: dict) -> float:
    """
    Squeeze a raw edge toward a believable ceiling.

    max * tanh(raw / max). At the default ceiling of 5.5%:

        raw  1%  ->  1.0%     (untouched -- small edges are the plausible ones)
        raw  3%  ->  2.8%
        raw  5%  ->  4.2%
        raw  8%  ->  4.9%
        raw 20%  ->  5.5%     (asymptote -- the model has not found 20%)

    Negative edges compress symmetrically, which keeps the sign and ordering of
    every comparison intact.
    """
    ceiling = float(cfg["model"].get("edge_compression") or 0)
    if ceiling <= 0:
        return raw
    return ceiling * math.tanh(raw / ceiling)


def kelly_fraction(p: float, american: float) -> float:
    """Full-Kelly fraction of bankroll. Zero means no bet."""
    b = american_to_decimal(american) - 1.0
    if b <= 0:
        return 0.0
    q = 1.0 - p
    return max(0.0, (p * b - q) / b)


def expected_value(p: float, american: float, p_push: float = 0.0) -> float:
    """EV per unit staked, with push probability removing stake from risk."""
    b = american_to_decimal(american) - 1.0
    p_lose = max(0.0, 1.0 - p - p_push)
    return p * b - p_lose


def stake_for(p: float, american: float, bankroll: float, cfg: dict,
              edge: float | None = None) -> float:
    """
    Fractional Kelly, capped, and sized off the COMPRESSED edge.

    Sizing off the compressed number is the point of compressing it. Kelly is
    famously unforgiving of an overstated probability -- a model that thinks it
    has 12% when it has 3% does not lose 4x more slowly, it eventually goes
    broke. Feeding the honest number in is what keeps the staking plan honest.
    """
    bk = cfg["bankroll"]
    if edge is not None:
        # Rebuild the probability implied by the compressed edge at this price.
        p = min(american_to_prob(american) + edge, float(cfg["model"]["max_model_prob"]))
    f = kelly_fraction(min(p, float(cfg["model"]["max_model_prob"])), american) * float(bk["kelly_fraction"])
    f = min(f, float(bk["max_stake_pct"]))
    raw = f * bankroll
    step = float(bk.get("round_stake_to") or 0.5)
    stake = round(raw / step) * step if step > 0 else raw
    return 0.0 if stake < float(bk.get("min_stake") or 0) else round(stake, 2)


# --------------------------------------------------------------------------- #
# Tiering
# --------------------------------------------------------------------------- #

TIER_RANK = {"BEST BET": 0, "GOOD": 1, "LEAN": 2, "PASS": 3}


def tier_for(edge: float, cfg: dict, confidence: float,
             line_gap: float | None = None, price: float | None = None,
             stale: bool = False, adverse: float | None = None) -> tuple[str, str | None]:
    """
    Map a compressed edge to BEST BET / GOOD / LEAN / PASS, and say why.

    Three adjustments before the thresholds apply.

    The winner's-curse haircut. You only bet where the model disagrees with the
    market -- which is precisely where the model's own error is largest. So the
    edges you select are overstated even when the model is well calibrated
    across all games. A flat haircut is the blunt, honest correction.

    Confidence scales the thresholds rather than the edge, so a thin-data game
    has to clear a higher bar for the same label instead of having its number
    quietly rewritten.

    The lock rules gate BEST BET specifically. That label should mean the model,
    the market and the data agree, and it should be rare. Anything that clears
    the edge bar but fails a lock rule is capped at GOOD, with the failed rule
    recorded so the card can show it.
    """
    t = cfg["tiers"]
    if confidence <= 0:
        return "PASS", "no usable price"
    edge = edge - float(cfg["model"].get("selection_haircut", 0.0))
    scale = 1.0 / max(0.35, confidence)

    if edge >= float(t["best_bet"]) * scale:
        candidate = "BEST BET"
    elif edge >= float(t["good"]) * scale:
        candidate = "GOOD"
    elif edge >= float(t["lean"]) * scale:
        candidate = "LEAN"
    else:
        return "PASS", None

    if candidate != "BEST BET":
        return candidate, None

    rules = t.get("lock_rules") or {}
    if confidence < float(rules.get("min_confidence", 0)):
        return "GOOD", f"not enough data yet (confidence {confidence:.2f})"
    if price is not None and price < float(rules.get("min_price", -10000)):
        return "GOOD", f"price {price:+.0f} is too short to lock"
    if rules.get("require_no_stale_odds") and stale:
        return "GOOD", "line is stale"
    limit = rules.get("max_adverse_line_move")
    if limit is not None and adverse is not None and adverse >= float(limit):
        return "GOOD", (f"the market has moved {adverse:.1f} pts against this side since we "
                        f"first saw it — that usually means our number is the stale one")
    if line_gap is not None:
        gap = abs(line_gap)
        if gap < float(rules.get("min_line_gap", 0)):
            return "GOOD", f"model only disagrees by {gap:.1f} pts — edge is coming from the price, not the game"
        if gap > float(rules.get("max_line_gap", 99)):
            return "GOOD", f"model is {gap:.1f} pts from the market — that is usually the model being wrong"
    return "BEST BET", None


def confidence_score(n_home: int, n_away: int, has_odds: bool, cfg: dict,
                     season_type: int = 2) -> float:
    """
    How much the model trusts itself on this game, 0-1.

    Driven mostly by sample size. In Week 1 nobody has played, every rating is
    the market's preseason win total, and the honest answer is "not much".
    Preseason is floored near zero because preseason results are decided by which
    starters played, not by which team is better.
    """
    if not has_odds:
        return 0.0
    if season_type == 1:
        return 0.15
    need = float(cfg["model"]["min_games_for_full_confidence"])
    n = min(n_home, n_away)
    sample = min(1.0, (n / need) ** 0.5) if need > 0 else 1.0
    floor = 1.0 - float(cfg["model"]["early_season_shrink"])
    return round(floor + (1.0 - floor) * sample, 3)
