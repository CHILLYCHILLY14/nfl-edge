# Prediction accuracy

The Accuracy view records model predictions independently of the wager ledger.
No wager is created by this tracker. Refreshes preserve `state/model_accuracy.json`
and publish the model-only report in `data/accuracy.json`.

* Forecasts and quoted selections are frozen before the event starts. Every
  original price, line, probability and tier remains available for review.
* Preferred-pick statistics use one first-selected side per event and market,
  including PASS/AVOID. The full audit includes the opposite sides and, for
  props, alternative lines; do not interpret its combined win rate as pick quality.
* Unpriced team/player forecasts are tracked. They report final-score or player
  statistic error even if there was never a wager or a qualifying betting edge.
* Final results grade wins, losses and pushes. A missing player, missing score,
  incomplete box score or failed feed remains pending; missing data is never zero.
* ROI is hypothetical flat-one-unit return. Push stakes are included in the ROI
  denominator; voids are excluded. Win rate and Brier exclude pushes and voids.
* Legacy records are imported only with a documented pre-event timestamp. Games
  with no saved pre-event forecast cannot be backfilled honestly.
* Model/tier versions remain separate. Changing a threshold never relabels old
  predictions or proves that a revised model will be profitable.

Run `python -m unittest tests.test_accuracy` to verify freezing, grading,
missing results, pushes, voids, calibration and history persistence.

## Expected-return tiers (2026-09-06-ev2)

A probability advantage and an expected return have different units. Qualification
now uses the smaller of compressed no-vig expected return and compressed expected
return at the offered price, then subtracts the selection haircut and a bounded
confidence reserve. The board publishes the underlying probability advantage,
raw offered-price EV, adjusted return and the required GOOD/BEST thresholds.
Unrealistic model/market disagreements, incomplete prices and existing eligibility
rules still prevent qualification. Tier labels are estimates, not observed skill.

The old NFL confidence multiplier made GOOD unreachable in opening-week data;
its BEST threshold also equalled the compression asymptote after the haircut.
The bounded reserve and reachable BEST threshold repair those contradictions.
