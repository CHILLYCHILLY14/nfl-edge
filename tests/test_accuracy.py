import copy
import json
import tempfile
import unittest
from pathlib import Path
from pipeline import model_accuracy as A

NOW = "2026-09-01T12:00:00+00:00"
START = "2026-09-10T18:00:00+00:00"


def call(**kwargs):
    return {"kind": "call", "league": "NFL", "event_id": "1", "start": START,
            "market": "ML", "side": "home", "price": -110, "probability": .6,
            "tier": "PASS", "edge": .04, **kwargs}


class AccuracyTests(unittest.TestCase):
    def test_freezes_pregame_line_price_probability_and_tier(self):
        log = A.record({}, [call(market="ATS", line=-3)], NOW)
        first = copy.deepcopy(log)
        A.record(log, [call(market="ATS", line=-6, price=200, probability=.9, tier="BEST")], "2026-09-02T12:00:00Z")
        self.assertEqual(log, first)
        A.settle(log, {"1": {"completed": True, "home_score": 27, "away_score": 23}})
        row = next(iter(log["records"].values()))
        self.assertEqual(row["result"], "Win")
        self.assertAlmostEqual(row["units"], 100/110, places=5)
        self.assertEqual(row["tier"], "PASS")

    def test_rejects_late_missing_and_naive_timestamps(self):
        for start in [None, "2026-09-10", "2026-09-10T18:00:00", "2026-08-31T12:00:00Z", NOW]:
            self.assertEqual(A.record({}, [call(start=start)], NOW)["records"], {})
        self.assertEqual(A.record({}, [call(completed=True)], NOW)["records"], {})
        self.assertEqual(A.record({}, [call(captured_at="2026-09-02T12:00:00Z")], NOW)["records"], {})

    def test_import_requires_real_pregame_snapshot_not_retroactive_forecast(self):
        log = A.record({}, [call(captured_at=NOW, version="legacy")], "2026-09-12T00:00:00Z")
        self.assertEqual(len(log["records"]), 1)
        self.assertEqual(A.record({}, [call()], "2026-09-12T00:00:00Z")["records"], {})

    def test_both_sides_audited_but_preferred_pick_not_diluted(self):
        log = A.record({}, [call(side="away", edge=-.04, probability=.4), call()], NOW)
        A.settle(log, {"1": {"completed": True, "home_score": 21, "away_score": 14}})
        r = A.report(log)
        self.assertEqual(r["all_calls"]["win_rate"], .5)
        self.assertEqual(r["overall"]["win_rate"], 1)
        self.assertEqual(r["by_tier"]["PASS"]["wins"], 1)
        self.assertAlmostEqual(r["overall"]["brier"], .16)
        self.assertEqual(r["calibration"][0]["predicted"], .6)

    def test_push_void_and_missing_result_denominators(self):
        rows = [call(event_id=str(i)) for i in range(4)]
        log = A.record({}, rows, NOW)
        A.settle(log, {"0": {"completed": True, "home_score": 21, "away_score": 21},
                       "1": {"canceled": True}, "2": {"completed": True, "home_score": 7, "away_score": 3},
                       "3": {"completed": True, "home_score": None, "away_score": 3}})
        o = A.report(log)["overall"]
        self.assertEqual((o["wins"],o["pushes"],o["voids"],o["pending"]),(1,1,1,1))
        self.assertEqual(o["win_rate"], 1)
        self.assertAlmostEqual(o["roi"], (100/110)/2, places=5)

    def test_soccer_draw_loses_two_sides_and_wins_draw(self):
        log=A.record({},[call(side=s,three_way=True) for s in ["home","away","draw"]],NOW)
        A.settle(log,{"1":{"completed":True,"home_score":1,"away_score":1}})
        self.assertEqual({r["side"]:r["result"] for r in log["records"].values()},
                         {"home":"Loss","away":"Loss","draw":"Win"})

    def test_unpriced_game_forecast_grades_without_any_bet(self):
        log=A.record({},[{"kind":"game","league":"NFL","event_id":"1","start":START,
                          "margin":4,"total":44,"probability":.6}],NOW)
        A.settle(log,{"1":{"completed":True,"home_score":24,"away_score":17}})
        r=A.report(log)
        self.assertEqual(r["overall"]["logged"],0)
        self.assertEqual(r["games"]["winner"]["accuracy"],1)
        self.assertEqual(r["games"]["margin_mae"],3)
        self.assertEqual(r["games"]["total_mae"],3)
        self.assertIsNone(r["games"]["ats"]["accuracy"])

    def test_own_side_spread_and_total_push(self):
        log=A.record({},[call(market="ATS",side="away",line=4),call(market="TOTAL",side="under",line=41)],NOW)
        A.settle(log,{"1":{"completed":True,"home_score":24,"away_score":17}})
        self.assertEqual({r["market"]:r["result"] for r in log["records"].values()},{"ATS":"Loss","TOTAL":"Push"})

    def test_prop_negative_stat_zero_and_missing_are_distinct(self):
        log=A.record({},[call(player="Player",market="Rushing yards",side="over",line=-1,stat_key="rush"),
                         call(player="Player",market="Passing yards",side="under",line=100,stat_key="pass"),
                         {"kind":"prop","league":"NFL","event_id":"1","start":START,"player":"Player",
                          "market":"Rushing yards","projection":3,"stat_key":"rush"}],NOW)
        A.settle(log,{"1":{"completed":True,"stats":{"rush":-2}}})
        rs=list(log["records"].values())
        self.assertEqual(next(r for r in rs if r.get("market")=="Passing yards")["result"],"Pending")
        self.assertEqual(next(r for r in rs if r["kind"]=="call" and r["market"]=="Rushing yards")["result"],"Loss")
        self.assertEqual(A.report(log)["props"]["Rushing yards"]["mae"],5)

    def test_history_survives_empty_refresh_and_corruption_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"history.json"
            log=A.record({},[call()],NOW);A.save(path,log)
            self.assertEqual(A.record(A.load(path),[],NOW),log)
            path.write_text("invalid")
            with self.assertRaises(json.JSONDecodeError):A.load(path)


if __name__ == "__main__":
    unittest.main()
