# Setup

> **Never used GitHub or a terminal before?** Read **[START-HERE.md](START-HERE.md)** instead. It covers the same ground assuming no prior knowledge, with every click spelled out. This page is the condensed version.

From an empty GitHub account to a live, self-updating board embedded in your Wix site. About fifteen minutes, no cost, no API keys.

This is the same path the NCAAF board uses, so if that one is already running, everything here will look familiar.

---

## 1. Create the repository

1. Go to <https://github.com/new>.
2. Name it **`nfl-edge`**.
3. **Public** is easiest — GitHub Pages on a private repo needs a paid plan. The page is not linked from anywhere and carries `noindex, nofollow`, but treat the URL as semi-private rather than secret.
4. Do not add a README, `.gitignore` or licence — this repo already has them.

Then push these files. Use whichever remote style you already have set up — the only line that differs is `git remote add`.

**HTTPS** (asks for a username and a personal access token, not your password):

```bash
cd nfl-edge
git init
git add .
git commit -m "NFL Edge: automatic model"
git branch -M main
git remote add origin https://github.com/<you>/nfl-edge.git
git push -u origin main
```

**SSH** (no password prompt once the key is set up — see below):

```bash
cd nfl-edge
git init
git add .
git commit -m "NFL Edge: automatic model"
git branch -M main
git remote add origin git@github.com:<you>/nfl-edge.git
git push -u origin main
```

Note the shape of the SSH address: `git@github.com:` with a **colon** before your username, not a slash. That colon is the single most common reason an SSH push fails with "repository not found".

---

### 1a. Setting up SSH, if you have not already

Skip this if `ssh -T git@github.com` already greets you by name.

**Check for an existing key first** — most people already have one:

```bash
ls -al ~/.ssh
```

If you see `id_ed25519.pub` (or `id_rsa.pub`), you have a key already; jump to step 3 below.

**1. Create a key.**

```bash
ssh-keygen -t ed25519 -C "your_email@example.com"
```

Press Enter to accept the default location. A passphrase is optional — it protects the key if someone gets your laptop, and the agent in step 2 means you only type it once per session.

On an older system that rejects `ed25519`, use `ssh-keygen -t rsa -b 4096 -C "your_email@example.com"` instead.

**2. Load it into the agent.**

```bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519
```

On macOS, add it to the keychain so it survives a reboot:

```bash
ssh-add --apple-use-keychain ~/.ssh/id_ed25519
```

**3. Give the public half to GitHub.** Copy it:

```bash
cat ~/.ssh/id_ed25519.pub          # then copy the output
# macOS:    pbcopy < ~/.ssh/id_ed25519.pub
# Windows:  clip < ~/.ssh/id_ed25519.pub
# Linux:    xclip -sel clip < ~/.ssh/id_ed25519.pub
```

Copy the `.pub` file — the one ending in `.pub`. The other file is your private key and should never leave your machine or be pasted anywhere.

Then: **GitHub → your avatar → Settings → SSH and GPG keys → New SSH key**. Give it a title like "laptop", leave the type as "Authentication Key", paste, and save.

**4. Test it.**

```bash
ssh -T git@github.com
```

Say `yes` to the fingerprint prompt the first time. You want:

```
Hi <you>! You've successfully authenticated, but GitHub does not provide shell access.
```

That message is a success, despite how it reads.

**Already cloned over HTTPS and want to switch?**

```bash
git remote set-url origin git@github.com:<you>/nfl-edge.git
git remote -v          # confirm both lines now start with git@github.com:
```

**If your network blocks port 22** (common on office and some café Wi-Fi — the symptom is `ssh: connect to host github.com port 22: Connection timed out`), route SSH over port 443 by putting this in `~/.ssh/config`:

```
Host github.com
  Hostname ssh.github.com
  Port 443
  User git
```

Then `ssh -T git@github.com` again.

**One thing SSH does not change:** the GitHub Actions workflow pushes from inside GitHub's own runners using the built-in token, not your key. So step 2 below still matters exactly as much — SSH gets *your* laptop pushing, it does nothing for the bot.

---

## 2. Let the workflow write back

The model has to commit its own state — the odds snapshots that make grading and closing-line value possible, the bet ledger, and the shadow book that tracks tier accuracy. Without this it forgets everything between runs.

**Settings → Actions → General → Workflow permissions** → select **Read and write permissions** → **Save**.

---

## 3. Turn on Pages

**Settings → Pages → Build and deployment → Source → GitHub Actions.**

Do not pick "Deploy from a branch". The workflow uploads the `site/` folder itself.

---

## 4. First run

**Actions → Refresh model → Run workflow.** Tick **full** for this first one: it backfills last season so the preseason prior has something to solve from. Two or three minutes.

When it goes green, your board is at:

```
https://<you>.github.io/nfl-edge/
```

After that it runs on its own at 08:00, 12:00, 16:00 and 20:00 ET, plus 01:00 ET to grade the late games.

If you would rather see the layout before any of this, run it locally:

```bash
pip install -r requirements.txt
python -m tools.make_demo && python -m pipeline.build --offline
cd site && python -m http.server 8000
```

---

## 5. Embed it in Wix

1. In the Wix editor: **Add → Embed Code → Embed a Site** (the HTML iframe element).
2. Paste `https://<you>.github.io/nfl-edge/` as the address.
3. Stretch it wide and set the height to at least **1200 px**. The board is a dense table; a short iframe scrolls badly on phones.
4. Publish.

**Landing on a specific tab.** The page reads URL parameters, so you can point different Wix pages at different views:

```
https://<you>.github.io/nfl-edge/?tab=best        # best bets only
https://<you>.github.io/nfl-edge/?tab=accuracy    # tier accuracy
https://<you>.github.io/nfl-edge/?tab=power       # power rankings
https://<you>.github.io/nfl-edge/?tab=sim         # the game simulator
https://<you>.github.io/nfl-edge/?tab=injuries
https://<you>.github.io/nfl-edge/?week=7          # jump to a week
https://<you>.github.io/nfl-edge/?date=2026-11-26 # jump to a date
```

**A note on privacy.** A Wix page can be password-protected in **Pages → Settings → Permissions**, but that protects the Wix page, not the GitHub Pages URL behind it — anyone with that link can open it directly. If that matters, keep the repo private and pay for Pages, or self-host the `site/` folder behind your own auth.

---

## 6. Make it yours

Edit `config/settings.json`, commit, and the next run uses it. The values that matter most:

| Setting | Default | What it does |
|---|---|---|
| `bankroll.starting` | 500 | Your bankroll, in CAD. |
| `bankroll.kelly_fraction` | 0.25 | Quarter-Kelly. Raise at your peril. |
| `bankroll.max_stake_pct` | 0.05 | Hard cap on any single stake. |
| `tiers.best_bet` / `good` / `lean` | 0.045 / 0.028 / 0.015 | Edge thresholds. |
| `tiers.lock_rules` | — | The extra conditions BEST BET must also satisfy. |
| `model.edge_compression` | 0.055 | The ceiling edges are squeezed toward. Lower = more conservative. |
| `model.projection_blend` | 0.55 | How much of the model's disagreement with the market survives. Lower = more humble. |
| `filters.max_plays_per_week` | 6 | Hard cap on bets per week, best edges first. |
| `injuries.position_points.QB_starter` | 4.5 | Points lost when a starting QB is out. |
| `weather.wind_thresholds` | — | Points off the total at each wind speed. |

`config/overrides.json` is for what no feed knows — a coach resting starters, a story you trust. Keyed by ESPN game id, which is on every game card. Positive `margin_adj` favours the home team.

---

## 7. Keep an eye on it

**Every few weeks, open the Accuracy tab.** Two things to look for:

- **Does the ordering hold?** BEST BET should be beating GOOD, beating LEAN, beating PASS. The panel says so in a sentence and flags it loudly when it does not. Give it a few dozen graded calls per tier before drawing conclusions — early on, that verdict is mostly noise.
- **Calibration.** When the model says 58%, is it winning 58%? A persistent gap in one direction means the probabilities are off, and no amount of threshold tuning fixes that.

**Once a season is in the books**, run the walk-forward backtest:

```bash
python -m pipeline.backtest
```

It re-solves the ratings week by week using only games that had already finished, then reports how the calls would have graded. The line to read is **implied selection haircut**: how much worse the model's calibration is on the bets it *chose* than across every game it priced. That is the winner's curse, measured on your own data. If it comes out much larger than `model.selection_haircut` in settings, raise that number.

**In August each year**, refresh `config/win_totals.json` with the new market season win totals and bump `season` / `prior_season` in `settings.json`. That is the only annual maintenance.

---

## Troubleshooting

**The workflow fails on push.** Step 2 was skipped — workflow permissions are still read-only. This is unrelated to whether *you* use SSH or HTTPS; the runner uses its own token.

**`git@github.com: Permission denied (publickey).`** The key is not loaded or not on your GitHub account. Run `ssh-add -l` — if it says "no identities", run `ssh-add ~/.ssh/id_ed25519`. If the key is loaded, the public half was never added to GitHub, or you pasted the private one.

**`ERROR: Repository not found.` over SSH.** Two usual causes: the address used a slash instead of a colon (`git@github.com/you/...` rather than `git@github.com:you/...`), or the key belongs to a different GitHub account than the one that owns the repo. `ssh -T git@github.com` tells you which account you are authenticating as.

**`Connection timed out` on port 22.** Your network blocks SSH. Use the port-443 `~/.ssh/config` block in step 1a.

**The page loads but every tab is empty.** The build has not run yet, or `site/data/` was not committed. Check the Actions log.

**"Could not load the data files".** You opened `index.html` directly from disk. Browsers block `fetch` on `file://`. Serve the folder: `cd site && python -m http.server`.

**Every rating is the same.** No regular-season games have finished yet, so the ratings are still the preseason prior. Correct behaviour — the masthead says which prior is in use.

**ESPN returns nothing for a date range.** Their API occasionally goes quiet for a minute. The client retries with backoff and the next scheduled run picks it up. Auxiliary feeds — injuries, news, stats, weather — fail independently: if one is down, that panel is empty and the board is unaffected.

**A tab shows fewer games than expected.** The board only prices games inside `data.lookahead_days` (14). Games further out appear on the Games tab but are not priced yet.
