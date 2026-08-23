"""
Offline test suite. No network, no live season required.

These check the things that would be expensive to discover in November: a sign
flipped somewhere in the spread convention, a tie graded as a loss, an injury
adjustment applied to the wrong team, a rating solve that cannot recover
strengths it was handed. The ratings test in particular is the one that matters
most -- it feeds the solver a synthetic league whose true ratings are known and
checks that it gets them back.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import datetime as dt
import json
import os
import random
import unittest

from pipeline import explain, injuries as INJ, ledger, model as M
from pipeline import ratings as R, stats as ST, tracker, weather as WX
from pipeline import build as B

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(ROOT, "config", "settings.json"), encoding="utf-8"))


# --------------------------------------------------------------------------- #

class TestOdds(unittest.TestCase):
    def test_american_prob_round_trip(self):
        for a in (-350, -180, -110, 100, 145, 400):
            p = M.american_to_prob(a)
            self.assertAlmostEqual(M.american_to_prob(M.prob_to_american(p)), p, places=6)

    def test_breakeven_of_minus_110(self):
        self.assertAlmostEqual(M.american_to_prob(-110), 0.5238, places=3)

    def test_devig_sums_to_one(self):
        a, b = M.devig(M.american_to_prob(-110), M.american_to_prob(-110))
        self.assertAlmostEqual(a + b, 1.0, places=9)
        self.assertAlmostEqual(a, 0.5, places=9)

    def test_devig_keeps_ordering(self):
        fav, dog = M.devig(M.american_to_prob(-250), M.american_to_prob(+200))
        self.assertGreater(fav, dog)
        self.assertAlmostEqual(fav + dog, 1.0, places=9)


class TestKeyNumbers(unittest.TestCase):
    def test_three_and_seven_are_spikes(self):
        d = M.margin_distribution(0.0, 13.2, use_key_numbers=True)
        self.assertGreater(d[3], d[2])
        self.assertGreater(d[3], d[4])
        self.assertGreater(d[7], d[6])
        self.assertGreater(d[7], d[8])

    def test_distribution_normalised(self):
        d = M.margin_distribution(2.5, 13.2)
        self.assertAlmostEqual(sum(d.values()), 1.0, places=9)

    def test_push_probability_only_on_whole_numbers(self):
        _, push_whole, _ = M.cover_probability(3.0, 13.2, -3.0)
        _, push_half, _ = M.cover_probability(3.0, 13.2, -3.5)
        self.assertGreater(push_whole, 0.05)
        self.assertEqual(push_half, 0.0)

    def test_spread_sign_convention(self):
        """Negative spread_home means the home team lays points."""
        # Home projected to win by 10, laying 3 -> should cover comfortably.
        win, _, loss = M.cover_probability(10.0, 13.2, -3.0)
        self.assertGreater(win, loss)
        # Same projection, laying 17 -> should not.
        win2, _, loss2 = M.cover_probability(10.0, 13.2, -17.0)
        self.assertLess(win2, loss2)


class TestTies(unittest.TestCase):
    def test_tie_is_carved_out_not_split(self):
        p_home, p_tie = M.moneyline_probability(0.0, 13.2, True, ties_are_push=True)
        self.assertGreater(p_tie, 0.0)
        self.assertLess(p_home, 0.5)          # tie mass removed from both sides
        p_split, tie2 = M.moneyline_probability(0.0, 13.2, True, ties_are_push=False)
        self.assertEqual(tie2, 0.0)
        self.assertAlmostEqual(p_split, 0.5, places=6)

    def test_tie_graded_as_push(self):
        bet = {"market": "ML", "side": "home", "line": None, "stake": 10.0, "price": -110}
        result, pnl = ledger._grade_one(bet, {"home_score": 20, "away_score": 20})
        self.assertEqual(result, "Push")
        self.assertEqual(pnl, 0.0)


class TestEdgeCompression(unittest.TestCase):
    def test_small_edges_survive(self):
        self.assertAlmostEqual(M.compress_edge(0.01, CFG), 0.01, places=3)

    def test_large_edges_are_squeezed(self):
        ceiling = CFG["model"]["edge_compression"]
        self.assertLess(M.compress_edge(0.20, CFG), ceiling + 1e-9)
        self.assertLess(M.compress_edge(0.20, CFG), 0.30 * 0.25)

    def test_monotone_and_sign_preserving(self):
        vals = [M.compress_edge(x / 100, CFG) for x in range(-20, 21)]
        self.assertEqual(vals, sorted(vals))
        self.assertLess(M.compress_edge(-0.10, CFG), 0)
        self.assertGreater(M.compress_edge(0.10, CFG), 0)

    def test_stake_uses_compressed_edge(self):
        """A wildly overstated probability must not size a wildly large bet."""
        big = M.stake_for(0.95, -110, 500, CFG, edge=None)
        honest = M.stake_for(0.95, -110, 500, CFG, edge=0.03)
        self.assertLess(honest, big)


class TestMarketAnchor(unittest.TestCase):
    def test_disagreement_is_capped(self):
        # Model says home by 25; market says home by 3.
        out = M.blend_to_market(25.0, -3.0, CFG)
        self.assertLess(abs(out["gap"]), CFG["model"]["max_spread_disagreement"])
        self.assertGreater(out["mu"], 3.0)          # still leans the model's way
        self.assertLess(out["mu"], 25.0)            # but nowhere near all the way

    def test_small_disagreement_mostly_survives(self):
        out = M.blend_to_market(4.0, -3.0, CFG)     # 1 point apart
        kept = CFG["model"]["projection_blend"]
        # tanh barely bites this close in, so almost all of the 1-point
        # disagreement should survive the squeeze before the blend is applied.
        self.assertGreater(out["gap"], kept * 0.95)
        self.assertLessEqual(out["gap"], kept)

    def test_no_market_means_no_anchor(self):
        out = M.blend_to_market(7.0, None, CFG)
        self.assertFalse(out["anchored"])
        self.assertEqual(out["mu"], 7.0)


class TestTiering(unittest.TestCase):
    def test_lock_needs_confidence(self):
        # Confidence 0.6 clears the edge threshold but not the lock rule's 0.72.
        tier, why = M.tier_for(0.09, CFG, confidence=0.6, line_gap=3.0, price=-110)
        self.assertEqual(tier, "GOOD")
        self.assertIn("confidence", why)
        # Thin data on top of that should not sneak through either.
        self.assertNotEqual(M.tier_for(0.09, CFG, 0.4, 3.0, -110)[0], "BEST BET")

    def test_lock_needs_real_line_disagreement(self):
        tier, why = M.tier_for(0.09, CFG, confidence=0.95, line_gap=0.2, price=-110)
        self.assertEqual(tier, "GOOD")
        self.assertIn("disagrees", why)

    def test_lock_rejects_absurd_disagreement(self):
        tier, why = M.tier_for(0.09, CFG, confidence=0.95, line_gap=12.0, price=-110)
        self.assertEqual(tier, "GOOD")
        self.assertIn("wrong", why)

    def test_lock_passes_when_everything_lines_up(self):
        tier, why = M.tier_for(0.09, CFG, confidence=0.95, line_gap=2.5, price=-110)
        self.assertEqual(tier, "BEST BET")
        self.assertIsNone(why)

    def test_thin_data_raises_the_bar(self):
        # The haircut comes off before the thresholds, so add it back to sit
        # exactly on the GOOD line at full confidence.
        edge = CFG["tiers"]["good"] + CFG["model"]["selection_haircut"] + 0.001
        self.assertEqual(M.tier_for(edge, CFG, 1.0, 2.0, -110)[0], "GOOD")
        self.assertEqual(M.tier_for(edge, CFG, 0.4, 2.0, -110)[0], "PASS")

    def test_preseason_confidence_is_floored(self):
        self.assertLessEqual(M.confidence_score(8, 8, True, CFG, season_type=1), 0.2)


class TestInjuries(unittest.TestCase):
    def _rows(self, *specs):
        return [{"athlete_id": a, "name": n, "position": p, "status": s, "detail": "Knee"}
                for a, n, p, s in specs]

    def test_starting_qb_dominates(self):
        starter = INJ.team_impact(self._rows(("1", "QB1", "QB", "Out")), CFG, "1")
        backup = INJ.team_impact(self._rows(("9", "QB3", "QB", "Out")), CFG, "1")
        self.assertGreater(starter["points"], 4.0)
        self.assertLess(backup["points"], 0.5)
        self.assertTrue(starter["qb_out"])

    def test_questionable_barely_counts(self):
        out = INJ.team_impact(self._rows(("1", "QB1", "QB", "Out")), CFG, "1")["points"]
        q = INJ.team_impact(self._rows(("1", "QB1", "QB", "Questionable")), CFG, "1")["points"]
        self.assertLess(q, out * 0.35)

    def test_unknown_depth_chart_reduces_qb_weight(self):
        known = INJ.team_impact(self._rows(("1", "QB1", "QB", "Out")), CFG, "1", qb_known=True)
        unknown = INJ.team_impact(self._rows(("1", "QB1", "QB", "Out")), CFG, None, qb_known=False)
        self.assertLess(unknown["points"], known["points"])
        self.assertGreater(unknown["points"], 1.0)

    def test_team_cap_applies(self):
        many = self._rows(*[(str(i), f"P{i}", "WR", "Out") for i in range(40)])
        imp = INJ.team_impact(many, CFG, None)
        self.assertTrue(imp["capped"])
        self.assertLessEqual(imp["points"], CFG["injuries"]["max_team_points"] + 1e-9)

    def test_sign_convention_home_injuries_hurt_home(self):
        hurt = INJ.team_impact(self._rows(("1", "QB1", "QB", "Out")), CFG, "1")
        healthy = INJ.team_impact([], CFG, None)
        adj = INJ.game_adjustment(hurt, healthy, CFG)
        self.assertLess(adj["margin_adj"], 0)         # home hurt -> margin down
        adj2 = INJ.game_adjustment(healthy, hurt, CFG)
        self.assertGreater(adj2["margin_adj"], 0)     # away hurt -> margin up

    def test_ir_and_suspension_count_as_out(self):
        for status in ("Injured Reserve", "Suspension", "Physically Unable to Perform"):
            self.assertEqual(INJ.status_weight(status, CFG), 1.0, status)


class TestWeather(unittest.TestCase):
    def _fc(self, **kw):
        base = {"temp_f": 55, "wind_mph": 5, "gust_mph": 8, "precip_prob": 10,
                "snow_in": 0, "precip_in": 0}
        base.update(kw)
        return base

    def test_dome_ignores_everything(self):
        adj = WX.adjustment(self._fc(wind_mph=30, precip_prob=100), "dome", CFG)
        self.assertEqual(adj["total_adj"], 0.0)

    def test_wind_lowers_the_total(self):
        calm = WX.adjustment(self._fc(wind_mph=4), "open", CFG)["total_adj"]
        windy = WX.adjustment(self._fc(wind_mph=22), "open", CFG)["total_adj"]
        self.assertEqual(calm, 0.0)
        self.assertLess(windy, -2.0)

    def test_retractable_roof_halves_the_effect(self):
        openair = WX.adjustment(self._fc(wind_mph=22), "open", CFG)["total_adj"]
        retract = WX.adjustment(self._fc(wind_mph=22), "retractable", CFG)["total_adj"]
        self.assertAlmostEqual(retract, openair * 0.5, places=2)

    def test_snow_is_worse_than_rain(self):
        rain = WX.adjustment(self._fc(precip_prob=90), "open", CFG)["total_adj"]
        snow = WX.adjustment(self._fc(precip_prob=90, snow_in=0.5), "open", CFG)["total_adj"]
        self.assertLess(snow, rain)

    def test_reasons_are_reported(self):
        adj = WX.adjustment(self._fc(wind_mph=22), "open", CFG)
        self.assertTrue(adj["reasons"])


class TestRatings(unittest.TestCase):
    """The important one: can the solver recover strengths it was never told?"""

    def _league(self, true_ratings, hfa, n_rounds=8, seed=7):
        rng = random.Random(seed)
        teams = list(true_ratings)
        games, gid = [], 0
        day = dt.date(2025, 9, 7)
        for rnd in range(n_rounds):
            rng.shuffle(teams)
            for i in range(0, len(teams) - 1, 2):
                h, a = teams[i], teams[i + 1]
                margin = true_ratings[h] - true_ratings[a] + hfa + rng.gauss(0, 9)
                total = 44 + rng.gauss(0, 8)
                hs = max(0, int(round((total + margin) / 2)))
                as_ = max(0, int(round((total - margin) / 2)))
                games.append({
                    "game_id": str(gid), "completed": True, "season_type": 2,
                    "date_utc": (day + dt.timedelta(days=7 * rnd)).isoformat() + "T17:00:00Z",
                    "neutral": False, "home_score": hs, "away_score": as_,
                    "home": {"abbr": h, "id": "", "name": h},
                    "away": {"abbr": a, "id": "", "name": a},
                    "odds": {"spread_home": -round((true_ratings[h] - true_ratings[a] + hfa) * 2) / 2,
                             "total": 44.0},
                })
                gid += 1
        return games

    def test_recovers_known_ratings(self):
        true = {f"T{i:02d}": v for i, v in enumerate(
            [7.5, 6.0, 4.5, 3.5, 2.5, 2.0, 1.0, 0.5, 0.0, -0.5, -1.0, -2.0,
             -2.5, -3.5, -4.5, -6.0])}
        games = self._league(true, hfa=2.0, n_rounds=14)
        cfg = json.loads(json.dumps(CFG))
        cfg["ratings"]["recency_halflife_games"] = 999   # no decay for this test
        solved, hfa = R.solve_margin_ratings(games, cfg)
        # Ridge deliberately shrinks every rating toward its prior, so the solved
        # numbers are smaller than the true ones by construction. What must hold
        # is that they line up: correlation, not equality. Exact ordering is too
        # strict a test on 14 rounds of noisy results -- neighbouring teams half a
        # point apart will swap, and should.
        keys = sorted(true)
        xs = [true[k] for k in keys]
        ys = [solved[k] for k in keys]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
        var = (sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys)) ** 0.5
        corr = cov / var
        self.assertGreater(corr, 0.90, f"ratings correlation only {corr:.3f}")
        order_true = sorted(true, key=lambda t: -true[t])
        self.assertGreater(solved[order_true[0]], solved[order_true[-1]])
        self.assertGreater(hfa, 0.5)
        self.assertLess(hfa, 4.0)

    def test_ratings_centre_on_zero(self):
        true = {f"T{i}": i - 4 for i in range(8)}
        solved, _ = R.solve_margin_ratings(self._league(true, 2.0), CFG)
        self.assertAlmostEqual(sum(solved.values()) / len(solved), 0.0, places=6)

    def test_preseason_games_excluded(self):
        games = self._league({"A": 5, "B": -5}, 2.0, n_rounds=2)
        for g in games:
            g["season_type"] = 1
        solved, _ = R.solve_margin_ratings(games, CFG)
        self.assertEqual(solved, {})

    def test_alias_does_not_invent_a_team(self):
        prior = {"WSH": 1.0, "KC": 2.0}
        aligned = R.align_to_league(prior, {"WAS", "KC"})
        self.assertIn("WAS", aligned)
        self.assertNotIn("WSH", aligned)
        self.assertEqual(len(aligned), 2)

    def test_market_prior_matches_the_workbook_formula(self):
        prior = R.market_prior()
        self.assertTrue(prior)
        # (win_total - 8.5) * 1.6 -- an 11.5-win team should be +4.8.
        self.assertAlmostEqual(max(prior.values()), (11.5 - 8.5) * 1.6, places=6)


class TestGrading(unittest.TestCase):
    def test_ats_home_cover(self):
        bet = {"market": "ATS", "side": "home", "line": -3.0, "stake": 10.0, "price": -110}
        self.assertEqual(ledger._grade_one(bet, {"home_score": 24, "away_score": 17})[0], "Win")
        self.assertEqual(ledger._grade_one(bet, {"home_score": 20, "away_score": 17})[0], "Push")
        self.assertEqual(ledger._grade_one(bet, {"home_score": 20, "away_score": 19})[0], "Loss")

    def test_ats_away_cover(self):
        bet = {"market": "ATS", "side": "away", "line": -3.0, "stake": 10.0, "price": -110}
        self.assertEqual(ledger._grade_one(bet, {"home_score": 20, "away_score": 19})[0], "Win")

    def test_total(self):
        bet = {"market": "TOTAL", "side": "over", "line": 44.5, "stake": 10.0, "price": -110}
        self.assertEqual(ledger._grade_one(bet, {"home_score": 24, "away_score": 21})[0], "Win")
        self.assertEqual(ledger._grade_one(bet, {"home_score": 20, "away_score": 21})[0], "Loss")

    def test_payout_matches_price(self):
        bet = {"market": "ML", "side": "home", "line": None, "stake": 100.0, "price": +150}
        result, pnl = ledger._grade_one(bet, {"home_score": 21, "away_score": 10})
        self.assertEqual(result, "Win")
        self.assertAlmostEqual(pnl, 150.0, places=2)

    def test_clv_sign_for_spread(self):
        bet = {"market": "ATS", "side": "home", "line": -3.0, "price": -110}
        lines = {"1": [{"spread_home": -6.5, "spread_price_home": -110, "ts": "x"}]}
        bet["game_id"] = "1"
        ledger._attach_clv(bet, lines)
        # We took -3 and it closed -6.5: we got the better number, so CLV positive.
        self.assertGreater(bet["clv_prob"], 0)


class TestTracker(unittest.TestCase):
    def _cand(self, gid, tier, market="ATS", side="home", price=-110, line=-3.0):
        return {"game_id": gid, "market": market, "side": side, "pick": "X", "line": line,
                "price": price, "model_prob": 0.55, "edge": 0.03, "edge_raw": 0.05,
                "confidence": 0.9, "tier": tier, "matchup": "A @ B", "week": 1,
                "game_date": "2026-09-13T17:00:00Z", "line_gap": 2.0, "season_type": 2}

    def test_records_once_and_never_reprices(self):
        shadow = {}
        tracker.record(shadow, [self._cand("1", "GOOD")])
        tracker.record(shadow, [self._cand("1", "PASS")])
        self.assertEqual(len(shadow), 1)
        row = list(shadow.values())[0]
        self.assertEqual(row["tier"], "GOOD")        # original call preserved
        self.assertEqual(row["closing_tier"], "PASS")  # later opinion recorded separately

    def test_passes_are_tracked_too(self):
        shadow = {}
        tracker.record(shadow, [self._cand("1", "PASS"), self._cand("2", "LEAN")])
        self.assertEqual(len(shadow), 2)

    def test_grades_and_reports_by_tier(self):
        shadow = {}
        tracker.record(shadow, [self._cand("1", "GOOD"), self._cand("2", "PASS")])
        games = {
            "1": {"completed": True, "home_score": 24, "away_score": 17,
                  "home": {"abbr": "B"}, "away": {"abbr": "A"}},
            "2": {"completed": True, "home_score": 17, "away_score": 24,
                  "home": {"abbr": "B"}, "away": {"abbr": "A"}},
        }
        self.assertEqual(tracker.grade(shadow, games), 2)
        rep = tracker.report(shadow)
        self.assertEqual(rep["by_tier"]["GOOD"]["record"], "1-0")
        self.assertEqual(rep["by_tier"]["PASS"]["record"], "0-1")
        self.assertEqual(rep["settled_calls"], 2)
        self.assertIsNotNone(rep["brier"])

    def test_units_reflect_the_price(self):
        shadow = {}
        tracker.record(shadow, [self._cand("1", "GOOD", price=+200)])
        tracker.grade(shadow, {"1": {"completed": True, "home_score": 30, "away_score": 10,
                                     "home": {"abbr": "B"}, "away": {"abbr": "A"}}})
        self.assertAlmostEqual(list(shadow.values())[0]["units"], 2.0, places=3)

    def test_favdog_classification(self):
        self.assertEqual(tracker._favdog({"market": "ML", "price": -200, "side": "home"}), "Favourite")
        self.assertEqual(tracker._favdog({"market": "ATS", "line": -6.0, "side": "away"}), "Underdog")
        self.assertEqual(tracker._favdog({"market": "ATS", "line": -6.0, "side": "home"}), "Favourite")


class TestStatsAndExplain(unittest.TestCase):
    def _games(self):
        return [{"completed": True, "season_type": 2, "date_utc": "2026-09-13T17:00:00Z",
                 "home": {"abbr": "AAA"}, "away": {"abbr": "BBB"},
                 "home_score": 27, "away_score": 17,
                 "odds": {"spread_home": -3.0, "total": 44.0}}]

    def test_derived_stats(self):
        d = ST.derived(self._games())
        self.assertEqual(d["AAA"]["record"], "1-0")
        self.assertEqual(d["AAA"]["ppg"], 27.0)
        self.assertEqual(d["BBB"]["margin"], -10.0)

    def test_ats_and_ou_records(self):
        f = R.ats_form(self._games())
        self.assertEqual(f["AAA"]["season_ats"], "1-0-0")   # won by 10, laying 3
        self.assertEqual(f["AAA"]["season_ou"], "0-0-1")    # 44 scored on a 44 total -> push

    def test_factors_sum_to_the_model_line(self):
        parts = {"home_rating": 3.0, "away_rating": -1.0, "rating_diff": 4.0, "hfa": 1.9,
                 "rest_adj": 0.3, "travel_adj": 0.2, "manual_margin": 0.0,
                 "injury": {"margin_adj": -1.5, "home": {"points": 4.5, "items": [], "qb_out": True},
                            "away": {"points": 3.0, "items": [], "qb_out": False}}}
        g = {"home": {"abbr": "AAA"}, "away": {"abbr": "BBB"}, "neutral": False}
        rows = explain.factors(g, parts)
        self.assertAlmostEqual(sum(r["points"] for r in rows), 4.0 + 1.9 + 0.3 + 0.2 - 1.5, places=6)
        self.assertTrue(all(r.get("note") for r in rows))

    def test_narrative_mentions_both_teams(self):
        g = {"home": {"abbr": "AAA"}, "away": {"abbr": "BBB"}, "neutral": False}
        ev = explain.evidence(g, ST.derived(self._games()), R.ats_form(self._games()),
                              ST.league_context(ST.derived(self._games())),
                              {"AAA": 2.0, "BBB": -1.0}, {})
        text = explain.narrative(g, {"mu": 3.0, "proj_home_pts": 24, "proj_away_pts": 21,
                                     "market_mu": 2.0, "gap": 0.5, "gap_raw": 1.0}, ev, None)
        self.assertIn("AAA", text)
        self.assertIn("BBB", text)


class TestPipelineWiring(unittest.TestCase):
    def test_travel_penalty_favours_the_home_team(self):
        venues = WX.load_venues()
        g = {"home": {"abbr": "NYJ"}, "away": {"abbr": "SEA"}, "neutral": False,
             "date_utc": "2026-10-11T17:00:00Z", "game_id": "1"}
        adj, note = B.travel_adjustment(g, venues, CFG)
        self.assertGreater(adj, 0)
        self.assertIn("SEA", note)

    def test_short_trip_is_small(self):
        venues = WX.load_venues()
        g = {"home": {"abbr": "NYJ"}, "away": {"abbr": "PHI"}, "neutral": False,
             "date_utc": "2026-10-11T17:00:00Z", "game_id": "1"}
        adj, _ = B.travel_adjustment(g, venues, CFG)
        self.assertLess(adj, 0.2)

    def test_rest_days_from_schedule(self):
        games = [
            {"game_id": "1", "date_utc": "2026-09-13T17:00:00Z", "completed": True,
             "home": {"abbr": "AAA"}, "away": {"abbr": "BBB"}},
            {"game_id": "2", "date_utc": "2026-09-17T17:00:00Z", "completed": False,
             "home": {"abbr": "AAA"}, "away": {"abbr": "CCC"}},
        ]
        rests = B.rest_days(games)
        self.assertEqual(rests["2:home"], 4)   # Thursday off a Sunday

    def test_preseason_never_qualifies(self):
        cands = [{"tier": "BEST BET", "price": -110, "ev": 0.05}]
        out = B.apply_filters(cands, CFG, {"season_type": 1})
        self.assertEqual(out[0]["tier"], "PASS")
        self.assertIn("Preseason", out[0]["filtered"])

    def test_correlation_guard_keeps_one_angle(self):
        cands = [
            {"game_id": "1", "tier": "BEST BET", "edge": 0.05, "market": "ML", "side": "home"},
            {"game_id": "1", "tier": "GOOD", "edge": 0.04, "market": "ATS", "side": "home"},
        ]
        out = B.correlation_guard(cands, CFG)
        self.assertEqual(sum(1 for c in out if c["tier"] != "PASS"), 1)

    def test_weekly_cap(self):
        cfg = json.loads(json.dumps(CFG))
        cfg["filters"]["max_plays_per_week"] = 2
        cands = [{"game_id": str(i), "tier": "GOOD", "edge": 0.05 - i / 1000,
                  "week": 3, "season_type": 2} for i in range(6)]
        out = B.weekly_cap(cands, cfg)
        self.assertEqual(sum(1 for c in out if c["tier"] != "PASS"), 2)

    def test_merge_never_erases_a_closing_line(self):
        cached = [{"game_id": "1", "odds": {"spread_home": -3.0}, "home_score": 24,
                   "away_score": 17, "completed": True, "date_utc": "2026-09-13T17:00:00Z"}]
        fresh = [{"game_id": "1", "odds": {}, "home_score": None, "away_score": None,
                  "completed": False, "date_utc": "2026-09-13T17:00:00Z"}]
        merged = B.merge_games(cached, fresh)
        self.assertEqual(merged[0]["odds"]["spread_home"], -3.0)
        self.assertEqual(merged[0]["home_score"], 24)


class TestSiteData(unittest.TestCase):
    """The build must emit every file the page fetches, or a tab renders empty."""

    REQUIRED = ["meta", "summary", "board", "games_detail", "games", "ledger",
                "performance", "ratings", "injuries", "news", "team_stats", "weather"]

    def test_all_site_files_present(self):
        data = os.path.join(ROOT, "site", "data")
        if not os.path.isdir(data) or not os.listdir(data):
            self.skipTest("no build output yet — run `python -m pipeline.build --offline`")
        for name in self.REQUIRED:
            path = os.path.join(data, name + ".json")
            self.assertTrue(os.path.exists(path), f"missing site/data/{name}.json")
            with open(path, encoding="utf-8") as fh:
                json.load(fh)

    def test_board_edges_are_compressed(self):
        path = os.path.join(ROOT, "site", "data", "board.json")
        if not os.path.exists(path):
            self.skipTest("no build output yet")
        board = json.load(open(path, encoding="utf-8"))
        if not board:
            self.skipTest("empty board")
        ceiling = CFG["model"]["edge_compression"]
        self.assertLessEqual(max(abs(c["edge"]) for c in board), ceiling + 1e-6)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestDemoGuard(unittest.TestCase):
    """Simulated state must never survive into a live run."""

    def test_marker_triggers_a_purge(self):
        import shutil, tempfile
        from pipeline import store as ST_
        original = ST_.STATE_DIR
        tmp = tempfile.mkdtemp()
        try:
            ST_.STATE_DIR = tmp
            with open(os.path.join(tmp, B.DEMO_MARKER), "w") as fh:
                fh.write("x")
            with open(os.path.join(tmp, "games_2026.json"), "w") as fh:
                fh.write("[]")
            B.purge_demo_state()
            self.assertEqual(os.listdir(tmp), [])
        finally:
            ST_.STATE_DIR = original
            shutil.rmtree(tmp, ignore_errors=True)

    def test_no_marker_leaves_state_alone(self):
        import shutil, tempfile
        from pipeline import store as ST_
        original = ST_.STATE_DIR
        tmp = tempfile.mkdtemp()
        try:
            ST_.STATE_DIR = tmp
            with open(os.path.join(tmp, "ledger.json"), "w") as fh:
                fh.write("{}")
            B.purge_demo_state()
            self.assertEqual(os.listdir(tmp), ["ledger.json"])
        finally:
            ST_.STATE_DIR = original
            shutil.rmtree(tmp, ignore_errors=True)


class TestMarketRatings(unittest.TestCase):
    """Ratings solved from the spreads themselves."""

    def _games(self, strengths, hfa=1.9):
        out, gid = [], 0
        teams = list(strengths)
        for h in teams:
            for a in teams:
                if a == h:
                    continue
                out.append({
                    "game_id": str(gid), "season_type": 2, "completed": False,
                    "date_utc": "2026-10-11T17:00:00Z", "neutral": False,
                    "home": {"abbr": h}, "away": {"abbr": a},
                    "odds": {"spread_home": -(strengths[h] - strengths[a] + hfa)},
                })
                gid += 1
        return out

    def test_recovers_the_spreads_it_was_given(self):
        from pipeline import market as MKT
        true = {"AAA": 4.0, "BBB": 1.0, "CCC": -1.0, "DDD": -4.0}
        solved, hfa = MKT.solve(self._games(true), CFG)
        self.assertEqual(sorted(true, key=lambda t: -true[t]),
                         sorted(solved, key=lambda t: -solved[t]))
        self.assertAlmostEqual(hfa, 1.9, places=1)

    def test_prior_fades_as_teams_play(self):
        from pipeline import market as MKT
        model_prior = {"AAA": 0.0, "BBB": 0.0}
        market = {"AAA": 4.0, "BBB": -4.0}
        fresh, _ = MKT.blend_prior(model_prior, market, {}, CFG)
        late, _ = MKT.blend_prior(model_prior, market, {"AAA": 12, "BBB": 12}, CFG)
        self.assertGreater(fresh["AAA"], late["AAA"])
        self.assertAlmostEqual(late["AAA"], 0.0, places=6)

    def test_preseason_spreads_ignored(self):
        from pipeline import market as MKT
        games = self._games({"AAA": 4.0, "BBB": -4.0})
        for g in games:
            g["season_type"] = 1
        self.assertEqual(MKT.solve(games, CFG)[0], {})


class TestCalibration(unittest.TestCase):
    def _shadow(self, n, squash):
        rng = random.Random(11)
        out = {}
        for i in range(n):
            p = rng.uniform(0.35, 0.75)
            true = 0.5 + (p - 0.5) * squash
            out[str(i)] = {"model_prob": p,
                           "result": "Win" if rng.random() < true else "Loss"}
        return out

    def test_needs_enough_data(self):
        from pipeline import calibrate
        fit = calibrate.fit(self._shadow(50, 0.5), CFG)
        self.assertFalse(fit["enabled"])
        self.assertIn("needs", fit["reason"])

    def test_detects_overconfidence(self):
        from pipeline import calibrate
        fit = calibrate.fit(self._shadow(1500, 0.55), CFG)
        self.assertTrue(fit["enabled"])
        self.assertLess(fit["a"], 0.9)
        self.assertLess(calibrate.apply(0.70, fit), 0.70)
        self.assertGreater(calibrate.apply(0.40, fit), 0.40)

    def test_leaves_a_calibrated_model_alone(self):
        from pipeline import calibrate
        fit = calibrate.fit(self._shadow(2000, 1.0), CFG)
        self.assertAlmostEqual(fit["a"], 1.0, delta=0.25)
        self.assertAlmostEqual(calibrate.apply(0.62, fit), 0.62, delta=0.04)

    def test_clamps_are_respected(self):
        from pipeline import calibrate
        fit = calibrate.fit(self._shadow(1200, 0.02), CFG)
        self.assertGreaterEqual(fit["a"], CFG["model"]["calibration"]["min_slope"] - 1e-9)

    def test_disabled_fit_is_identity(self):
        from pipeline import calibrate
        self.assertEqual(calibrate.apply(0.61, {"enabled": False, "a": 0.5, "b": 1.0}), 0.61)


class TestPerTeamHomeField(unittest.TestCase):
    def test_shrinks_toward_the_league_number(self):
        games = [{
            "game_id": str(i), "completed": True, "season_type": 2,
            "date_utc": f"2026-10-{11+i:02d}T17:00:00Z", "neutral": False,
            "home": {"abbr": "AAA"}, "away": {"abbr": "BBB"},
            "home_score": 30, "away_score": 10,
            "odds": {"spread_home": -3.0, "total": 44.0},
        } for i in range(6)]
        out = R.per_team_home_field(games, 1.9, CFG)
        # Six huge home wins is not enough evidence to move far from the league
        # number. That restraint is the entire job of the shrinkage constant.
        self.assertIn("AAA", out)
        self.assertLess(abs(out["AAA"] - 1.9), 2.5)

    def test_disabled_returns_nothing(self):
        cfg = json.loads(json.dumps(CFG))
        cfg["model"]["per_team_home_field"] = False
        self.assertEqual(R.per_team_home_field([], 1.9, cfg), {})


class TestAdverseMove(unittest.TestCase):
    def test_spread_move_against_home(self):
        self.assertGreater(B.adverse_move({"spread": 2.0}, "ATS", "home"), 0)
        self.assertLess(B.adverse_move({"spread": 2.0}, "ATS", "away"), 0)

    def test_total_move_against_over(self):
        self.assertGreater(B.adverse_move({"total": -2.0}, "TOTAL", "over"), 0)
        self.assertLess(B.adverse_move({"total": -2.0}, "TOTAL", "under"), 0)

    def test_lock_withdrawn_on_adverse_move(self):
        tier, why = M.tier_for(0.09, CFG, 0.95, line_gap=2.5, price=-110, adverse=1.5)
        self.assertEqual(tier, "GOOD")
        self.assertIn("moved", why)

    def test_favourable_move_keeps_the_lock(self):
        self.assertEqual(M.tier_for(0.09, CFG, 0.95, 2.5, -110, adverse=-1.5)[0], "BEST BET")


class TestDivisions(unittest.TestCase):
    def test_division_detection(self):
        d = B.load_divisions()
        self.assertTrue(B.same_division("BUF", "MIA", d))
        self.assertFalse(B.same_division("BUF", "KC", d))
        self.assertFalse(B.same_division("BUF", "PHI", d))

    def test_every_team_has_a_division(self):
        d = B.load_divisions()
        for t in R.market_prior():
            self.assertIn(t, d, f"{t} missing from config/divisions.json")


class TestSimulatorPayload(unittest.TestCase):
    """The in-browser simulator needs every one of these keys or it renders blank."""

    REQUIRED = ["home_field", "market_home_field", "per_team_home_field",
                "league_avg_points", "home_scoring_bump", "margin_sd", "total_sd",
                "use_key_numbers", "key_numbers", "divisional_total_adj",
                "divisions", "calibration", "teams"]

    def _load(self):
        path = os.path.join(ROOT, "site", "data", "simulator.json")
        if not os.path.exists(path):
            self.skipTest("no build output yet")
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_payload_is_complete(self):
        d = self._load()
        for k in self.REQUIRED:
            self.assertIn(k, d)
        self.assertTrue(d["teams"])
        for team, row in d["teams"].items():
            for k in ("rating", "off", "def", "games", "name"):
                self.assertIn(k, row, f"{team} missing {k}")

    def test_key_numbers_match_the_python_model(self):
        # The browser must bump 3 and 7 exactly as the pipeline does, or the
        # simulator and the board would quietly disagree about the same game.
        d = self._load()
        self.assertEqual({int(k): v for k, v in d["key_numbers"].items()},
                         M.KEY_NUMBER_BUMPS)
