"""
Self-calibration: feed the tracked accuracy back into the probabilities.

The Accuracy tab measures whether a 58% call wins 58% of the time. This module
does the obvious next thing with that measurement, which almost no home-made
model bothers to do: it corrects the probabilities.

The method is Platt scaling — a one-dimensional logistic regression fitted on
the model's own graded history:

    calibrated = sigmoid( a * logit(raw) + b )

`a` is sharpness. Below 1 the model is overconfident and gets flattened toward
the middle; above 1 it is underconfident and gets stretched. `b` is bias — a
persistent lean toward favourites, or overs, or home teams.

Three guards, because a recalibration fitted on thin data is worse than none.

  * It needs `min_samples` graded calls before it does anything at all, and the
    default is deliberately high. Fitting a correction to forty results is
    fitting to noise.
  * `a` and `b` are clamped. A wild fit means something upstream is broken; the
    right response is to nudge the probabilities and leave a message in the log,
    not to let one strange month invert the whole model.
  * The fit uses ALL graded calls including the passes, not just the bets. The
    bets are a selected sample — the ones the model liked — and calibrating on
    them alone would bake the selection bias straight into the correction.

The fitted parameters are published in `meta.json` and shown on the site, so a
correction being applied is never invisible.
"""

from __future__ import annotations

import math


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def fit(shadow: dict, cfg: dict) -> dict:
    """
    Fit (a, b) by Newton-Raphson on the log-likelihood. Returns a status dict
    that is always safe to hand to `apply`.
    """
    conf = (cfg.get("model") or {}).get("calibration") or {}
    off = {"enabled": False, "a": 1.0, "b": 0.0, "n": 0,
           "reason": "calibration disabled in settings"}
    if not conf.get("enabled", False):
        return off

    rows = [(float(r["model_prob"]), 1.0 if r["result"] == "Win" else 0.0)
            for r in shadow.values()
            if r.get("result") in ("Win", "Loss") and r.get("model_prob") is not None]
    need = int(conf.get("min_samples", 400))
    if len(rows) < need:
        return {"enabled": False, "a": 1.0, "b": 0.0, "n": len(rows),
                "reason": f"needs {need} graded calls, has {len(rows)}"}

    xs = [_logit(p) for p, _ in rows]
    ys = [y for _, y in rows]

    a, b = 1.0, 0.0
    for _ in range(60):
        g_a = g_b = h_aa = h_ab = h_bb = 0.0
        for x, y in zip(xs, ys):
            p = _sigmoid(a * x + b)
            d = p - y
            w = p * (1 - p)
            g_a += d * x
            g_b += d
            h_aa += w * x * x
            h_ab += w * x
            h_bb += w
        det = h_aa * h_bb - h_ab * h_ab
        if abs(det) < 1e-12:
            break
        da = (g_a * h_bb - g_b * h_ab) / det
        db = (g_b * h_aa - g_a * h_ab) / det
        a -= da
        b -= db
        if abs(da) < 1e-9 and abs(db) < 1e-9:
            break

    lo_a, hi_a = float(conf.get("min_slope", 0.5)), float(conf.get("max_slope", 1.6))
    max_b = float(conf.get("max_bias", 0.35))
    clamped = not (lo_a <= a <= hi_a and abs(b) <= max_b)
    a = min(max(a, lo_a), hi_a)
    b = min(max(b, -max_b), max_b)

    return {
        "enabled": True, "a": round(a, 4), "b": round(b, 4), "n": len(rows),
        "clamped": clamped,
        "reason": ("fit hit the safety clamps — check the Accuracy tab, something "
                   "upstream may be wrong" if clamped else
                   "fitted on every graded call, passes included"),
        "interpretation": _describe(a, b),
    }


def _describe(a: float, b: float) -> str:
    bits = []
    if a < 0.95:
        bits.append("the model has been overconfident, so probabilities are being "
                    "pulled toward the middle")
    elif a > 1.05:
        bits.append("the model has been underconfident, so probabilities are being "
                    "pushed outward")
    else:
        bits.append("sharpness is about right")
    if b > 0.05:
        bits.append("and it has been leaning slightly too far against its own picks")
    elif b < -0.05:
        bits.append("and it has been leaning slightly too far toward its own picks")
    return ", ".join(bits) + "."


def apply(p: float, params: dict) -> float:
    """Calibrate one probability. A disabled or missing fit returns it unchanged."""
    if not params or not params.get("enabled"):
        return p
    return _sigmoid(float(params.get("a", 1.0)) * _logit(p) + float(params.get("b", 0.0)))
