# NFL Edge

An automatic NFL betting model. It fetches its own schedule, odds, injuries, weather, news and statistics, solves its own power ratings from results, prices every market against the book, tracks every call it makes, and publishes the whole thing as a web page you can drop into a Wix site.

Nothing needs a paid API key. Nothing needs to be typed in each week.

This replaces the `nfl betting.xlsx` workbook. What that file did well — Kelly staking, a transparent power-rating spread, a bet ledger, edge tiers — is all here. What it did badly is fixed, and the parts that only worked if you sat down every Sunday morning and pasted numbers in now happen on a schedule whether or not you are awake.

---

## What it does on its own

| Every run | Source | Key needed |
|---|---|---|
| Full schedule, kickoff times, venues, scores | ESPN public API | No |
| Market spread, total and moneyline | ESPN public API | No |
| League-wide injury report, with depth charts | ESPN public API | No |
| Kickoff weather for every venue | Open-Meteo | No |
| League news | ESPN public API | No |
| Team statistics | ESPN public API | No |
| Power ratings | Solved here, from results | — |

Runs five times a day on GitHub Actions, free. Deploys itself to GitHub Pages, free.

---

## What changed from the workbook

The workbook was a good spreadsheet. These are the things a spreadsheet could not do, plus the errors it shipped with.

**Eleven of the 32 win totals were wrong.** New England, Cincinnati, Cleveland, Pittsburgh, Jacksonville, the Chargers, Green Bay, Atlanta, New Orleans, Arizona and San Francisco were all off by a win, which is 1.6 points of power rating each — a Patriots–Bengals game was mispriced by over three points before anything else happened. More importantly, the workbook froze those numbers for the whole season. Here they are only the August prior, and from Week 1 the ratings are re-solved from actual results.

**Ratings are solved, not typed.** Ridge-regularised least squares on margin of victory across every game, so strength of schedule falls out of the maths instead of being ignored. Blowouts are capped, recent games count more, and each rating is anchored to its preseason prior in proportion to how little evidence exists yet.

**Ties are priced and graded correctly.** The workbook treated an NFL tie as impossible and would have graded one as a loss. A moneyline tie is a push.

**Key numbers.** `NORM.S.DIST` says the margin distribution is a smooth bell curve. It is not — roughly one NFL game in seven lands exactly on 3. That is why −2.5 and −3.5 are different bets and −5.5 and −6.5 are nearly the same one. The model builds a discrete margin distribution with the real spikes in it, which also gives honest push probabilities.

**Home field is 1.9 points, and it moves.** The workbook used 1.5 as a fixed constant. The real number is solved from this season's home results and shrunk toward the league baseline early on.

**Rest, travel and body clock are computed from the calendar**, which already knows who played Thursday and who flew three time zones for a 1 p.m. kickoff.

**De-vigging.** Comparing a model probability against a raw vig-inclusive implied probability confuses "I disagree with the market" with "the book charges juice". Those are now separate numbers.

**The edges are believable.** See below — this is the big one.

**Every call is tracked, including the passes.** See below — this is the other big one.

**The market's own ratings are solved and shown.** Every posted point spread is a statement about two teams at once, so a season of spreads can be solved for a rating per team the same way results can. That gives a far better early-season prior than win totals alone, and it puts a `Gap` column on the power rankings showing where the model disagrees with the market about a *team* — which is how you spot that four plays on a card are really one opinion showing up four times.

**It calibrates itself.** Once there are enough graded calls, the model fits a Platt correction on its own history and applies it to every probability it produces. If it has been running 3 points hot, it stops. The fitted parameters are published on the site rather than applied invisibly.

**Home field is per stadium**, shrunk hard toward the league number, so Denver's altitude survives and thirty stadiums' worth of noise does not.

**Division games get their own adjustment**, and the board withdraws a BEST BET when the market has moved against that side since the model first saw it.

**There is a simulator.** Any two teams, any adjustments, the full margin distribution.

---

## Realistic edges

A home-made model that reports a 15% edge on an NFL side has not found 15%. It has found a bug in itself. The NFL market is the sharpest in North American sport: 32 teams, one slate a week, enormous handle, every book in the world pricing the same sixteen games for five days. Real edges live between 1% and 4%.

Three mechanisms keep the numbers honest, and all three are visible on the page rather than hidden in the code.

**1. The line is anchored to the market.** The model builds its own spread from ratings, home field, rest, travel and injuries — then compares it to the market's spread and keeps only part of the disagreement. The gap is squeezed with a `tanh` so a 2-point difference of opinion survives nearly intact while a 15-point one (always a data problem, never an insight) compresses toward a ceiling of 6. Every game card shows the raw model line, the market line, and how much of the disagreement was kept.

**2. Edges are compressed.** After pricing, the raw edge goes through `ceiling × tanh(raw / ceiling)`:

| Raw edge | Reported |
|---|---|
| 1% | 1.0% |
| 3% | 2.8% |
| 5% | 4.2% |
| 8% | 4.9% |
| 20% | 5.5% |

Small edges pass through untouched; large ones asymptote. Both numbers are kept and displayed, so you can always see what was squeezed. **Kelly sizes off the compressed number**, which is the whole point — Kelly is unforgiving of an overstated probability, and a model that thinks it has 12% when it has 3% does not lose slowly, it goes broke.

**3. BEST BET has to earn the label on five axes.** An edge over the threshold is necessary and not sufficient. It also needs:

- confidence ≥ 0.72 (enough games played to trust the ratings)
- the model's line at least 1.0 point away from the market's — a big probability edge on a line the model agrees with is a pricing artefact, not a read on the game
- and no more than 6.0 points away — past that, the model is the one that is wrong
- a price no shorter than −250, and a line that is not stale
- and the market must not have moved a full point *against* that side since the model first saw it — a line drifting away from your opinion is the clearest early signal that yours is the stale one

Anything that clears the edge bar but fails a lock rule is capped at GOOD, and the card tells you which rule stopped it. Expect BEST BET to be rare. Most weeks the honest answer is that there is nothing worth betting, and the board says so.

---

## Projections you can check

Every game card carries the arithmetic, not just the answer.

- **The projected score and both lines** — "SEA 24 – NE 21, model line SEA −2.5 against a market of SEA −3.5."
- **The factor table.** Power rating, home field, rest, travel, injuries, weather, your own overrides — each with its value in points and a note saying where it came from. They sum exactly to the model's raw line, then the market anchor is applied as its own visible step.
- **The season evidence.** Both teams' record, points for and against, point margin, last five, last three margins, home and road splits, ATS and over/under records, and their solved offence and defence ratings, against the league average. None of this feeds the model — the ridge solve already learned team strength from these same games, and adding them on top would double-count the evidence. It is there so you can sanity-check a rating. If the model has a team three points above average and the evidence line reads 1-4 with a −9 margin, something is wrong, and you can see it at a glance instead of in November.
- **A paragraph in plain language**, written in the language of disagreement with the market rather than prediction. The model does not know who wins. It has an opinion on a price.
- **The injury list that moved the number**, player by player, with the points each one cost.
- **The forecast**, with the reason any weather adjustment was applied.

---

## Tracking accuracy — including the passes

The bet ledger answers *did I make money*. The **Accuracy** tab answers a more important question: *do the labels mean anything?*

A tier is a claim. BEST BET claims it beats GOOD, which claims it beats LEAN, which claims it beats the plays the model threw away. That claim is testable — but only if the passes are recorded too, and a ledger that only holds the bets you placed structurally cannot test it.

So every candidate the model prices is written to a **shadow book**, frozen at the moment it was first seen, and graded at a flat one unit when the game goes final — whether or not a dollar was ever risked. That produces:

- **Win rate, record, units and ROI for BEST BET, GOOD, LEAN and PASS** — the direct test of the labels
- **"Needed"** alongside each — the win rate the prices actually demanded, which is what you have to beat, not 50%
- **Tier separation** — a single verdict on whether the ordering holds, flagged loudly when a lower tier is outperforming a higher one
- The same breakdown **by market** (ML / ATS / total), **by side**, **by favourite vs underdog**, **by confidence bucket**, and **by week**
- **Calibration** — when the model says 58%, does 58% happen? With the gap in each bucket.
- **Brier score** — 0.25 is a coin flip; lower is better.

If PASS plays win as often as GOOD plays, the tiering is decoration and the staking plan is built on sand. This is how you find that out in October rather than in February.

Bets and shadow calls are both **written once and only ever graded, never re-priced**. A model that retroactively re-grades itself against the closing line always looks brilliant and is always lying.

---

> **Never used GitHub or a terminal before?** Read **[START-HERE.md](START-HERE.md)** instead of this file. It assumes nothing and walks through every click.

## Quick start

```bash
git clone https://github.com/<you>/nfl-edge && cd nfl-edge
pip install -r requirements.txt

# See the layout immediately, with simulated data
python -m tools.make_demo && python -m pipeline.build --offline
cd site && python -m http.server 8000     # then open http://localhost:8000

# The real thing (first run backfills last season, a couple of minutes)
python -m pipeline.build
```

Full setup, including the Wix embed and the GitHub Actions schedule, is in [SETUP.md](SETUP.md).

---

## Commands

| Command | What it does |
|---|---|
| `python -m pipeline.build` | Normal run. Rolling window, updates everything. |
| `python -m pipeline.build --full` | Full-season backfill. Slow; rebuilds the cache. |
| `python -m pipeline.build --no-bet` | Price everything, log nothing. |
| `python -m pipeline.build --offline` | Rebuild from cached state with no network at all. |
| `python -m pipeline.backtest` | Walk-forward backtest — ratings solved only from games that had already finished. |
| `python -m pipeline.to_excel` | Export the current state to `NFL_Edge_Model.xlsx`. |
| `python -m tools.make_demo` | Generate a simulated season. |
| `python -m tools.advance_demo` | Play the simulated season forward a week, so the accuracy panels fill in. |
| `python -m unittest discover -s tests` | 62 offline tests. No network needed. |

---

## Configuration

Everything lives in `config/`, and the next scheduled run picks up any change.

- **`settings.json`** — every knob: bankroll, Kelly fraction, tier thresholds, lock rules, edge compression, market anchoring, injury weights, weather thresholds, filters. Heavily commented; the `_note` fields explain why each default is what it is.
- **`overrides.json`** — your own adjustments for what no feed knows: a coach resting starters in Week 18, a locker-room story. Per game or permanent per team. Never overwritten.
- **`win_totals.json`** — the market's season win totals, used only as the August prior. Refresh once a year.
- **`venues.json`** — stadium coordinates and roof types, plus international sites. Anything unlisted is geocoded once and cached.

---

## Honest limitations

- **The market is very good.** This model's realistic ceiling is finding a small number of small edges. Treat a week with no plays as the system working.
- **Injury adjustments are blunt.** A starting quarterback is worth 4.5 points here regardless of who backs him up, because the model has no way to know how good the backup is. That is deliberately conservative and sometimes wrong in both directions.
- **One line per game.** ESPN gives a consensus number, not five books side by side. Shop your own price before betting — a half-point on a key number is worth more than most of the edges on this board.
- **Preseason is displayed, never bet.** Preseason results are decided by how long the starters played, which no rating model sees.
- **GitHub's scheduler runs late sometimes.** Fine for a model that reads a market every few hours. Not a live feed.
- **Demo numbers prove nothing.** The simulated season draws outcomes around the market's own line, so the model cannot beat it by construction. That demonstrates the machinery, not an edge.

Nothing here is a guarantee. Bet only what you can afford to lose.
