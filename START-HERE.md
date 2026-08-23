# Start here

Read this if you have never used a terminal, GitHub, or Python. It assumes nothing. Every step says exactly what to type and what you should see afterwards.

**Total time:** about 30 minutes, most of it waiting. **Cost:** nothing. **Ongoing work:** none — once it is running it updates itself five times a day forever.

If something does not match what this page says, jump to **When something goes wrong** at the bottom. Nothing here can break your computer, and nothing costs money at any point.

---

## What you are actually building

Three things that talk to each other:

1. **A program** (the folder you were given). It goes and gets NFL schedules, betting lines, injury reports and weather, does the maths, and writes the answers into a set of files.
2. **A free robot on GitHub** that runs that program five times a day, on their computers, not yours. Your laptop can be closed. It can be off.
3. **A web page** that shows the answers, which you drop into your Wix site.

You do not need to understand the program. You need to get it onto GitHub and flip three switches.

---

## Step 1 — Make a GitHub account

GitHub is a free website that stores code and will run it for you on a schedule.

1. Go to **https://github.com/signup**
2. Enter an email, pick a password, pick a username. Write the username down — you will type it a few times. This guide calls it `<you>`.
3. Verify the email they send.

If you already have an account, sign in and skip ahead.

---

## Step 2 — Make an empty home for the project

1. Go to **https://github.com/new**
2. **Repository name:** type `nfl-edge` — exactly that, lowercase, with the hyphen.
3. Leave it on **Public**. (Private repos cannot use the free web hosting. Nobody will find this page unless you send them the link, and it tells search engines to ignore it.)
4. Do **not** tick "Add a README file", and leave the other two dropdowns on "None". The folder you have already contains those.
5. Click **Create repository**.

You will land on a page with some instructions on it. Ignore them; the next step is easier.

---

## Step 3 — Put the files on GitHub

You have `nfl-edge.zip`. Unzip it — double-click on Windows, double-click on a Mac. You get a folder called `nfl-edge` with things like `README.md`, `pipeline`, `site`, `config` inside it.

**Now the important bit: you upload the *contents* of that folder, not the folder itself.**

1. On your new empty repository page, click the link that says **uploading an existing file**. (If you cannot see it: go to `https://github.com/<you>/nfl-edge/upload/main`.)
2. Open the `nfl-edge` folder on your computer.
3. Select everything inside it — `Ctrl+A` on Windows, `Cmd+A` on a Mac — and drag it all into the browser window.
4. Wait for the uploads to finish. It is a few hundred files; give it a minute or two.
5. Scroll to the bottom, and in the box under **Commit changes** type `first upload`.
6. Click **Commit changes**.

**Check it worked:** the repository page should now list folders named `config`, `pipeline`, `site`, `tests`, `tools` and files including `README.md`. If instead you see a single folder called `nfl-edge`, you dragged the folder rather than its contents — delete it and repeat, opening the folder first.

> **The hidden folder problem.** The project contains a folder called `.github` that GitHub's uploader sometimes skips, because folders starting with a dot are hidden. Without it, nothing runs on a schedule. Check now: is `.github` listed on your repository page? If not, see **When something goes wrong → the robot never runs**.

---

## Step 4 — Give the robot permission to save its work

The program needs to write down what it learns — every line it saw, every bet it graded. Without this it starts from scratch every time and can never track its own accuracy.

1. On your repository, click **Settings** (top right of the repo, not your account settings).
2. In the left sidebar, click **Actions**, then **General**.
3. Scroll to **Workflow permissions**.
4. Select **Read and write permissions**.
5. Click **Save**.

Skip this and everything else still appears to work, which is what makes it worth double-checking.

---

## Step 5 — Turn on the web page

1. Still in **Settings**, click **Pages** in the left sidebar.
2. Under **Build and deployment**, find **Source** and change the dropdown to **GitHub Actions**.

That is all. Do not choose "Deploy from a branch".

---

## Step 6 — Start it

1. Click the **Actions** tab along the top of your repository.
2. If it asks you to enable workflows, say yes.
3. In the left sidebar click **Refresh model**.
4. On the right, click the **Run workflow** button. A small panel opens.
5. **Tick the "full" checkbox.** This first run downloads last season so the model has history to learn from.
6. Click the green **Run workflow**.

A yellow dot appears. Refresh the page after a minute. Yellow means running, green tick means done, red X means something went wrong (see the bottom of this page). The first run takes **three to five minutes** because of the backfill; every run after it takes under a minute.

When it goes green, your board is live at:

```
https://<you>.github.io/nfl-edge/
```

Open it. Bookmark it. That address never changes.

From now on it runs by itself at 8am, noon, 4pm, 8pm and 1am Eastern. You never have to come back to this page.

---

## Step 7 — Put it on your Wix site

1. Open your Wix site in the editor.
2. Click the **+** (Add) button, choose **Embed Code**, then **Embed a Site**.
3. A box appears on your page. Click it, then click **Enter Website Address**.
4. Paste `https://<you>.github.io/nfl-edge/` and apply.
5. Drag the box wide, and drag it **tall** — at least 1200 pixels. This is a dense page and a short box scrolls badly, especially on a phone.
6. Click **Publish**.

**Handy trick:** you can point different Wix pages at different views by adding to the end of the address:

| Add this | You get |
|---|---|
| `?tab=best` | just the best bets |
| `?tab=sim` | just the simulator |
| `?tab=accuracy` | just the accuracy tracking |
| `?tab=power` | just the power rankings |
| `?week=7` | jump straight to Week 7 |

**One thing to know about privacy.** Wix can password-protect a page, but that protects the Wix page, not the GitHub address underneath it. Anyone you send that address to can open it directly. Treat it as semi-private: unlisted, not secret.

---

## What you are looking at

| Tab | What it is |
|---|---|
| **Best Bets** | The plays that qualified this week, each with the full reasoning. Often this list is short or empty. That is the system working, not failing. |
| **Full Board** | Every game it priced, including the ones it rejected and why. |
| **Games** | The schedule and scores. Use the Week dropdown at the top to go anywhere in the season. |
| **Simulator** | Pick any two teams and see what the model makes the game. Type in a spread to see the cover probability. |
| **Injuries / Weather / News** | The live feeds, refreshed every run. |
| **Power Rankings** | Every team rated in points, with what the market thinks beside it. |
| **Accuracy** | Whether the labels mean anything. Check this one every few weeks. |
| **Ledger** | The bet log and bankroll. |

**The single most important number on the whole site** is on the Accuracy tab: whether BEST BET is beating GOOD, beating LEAN, beating PASS. If it is not, the labels are not describing anything real yet. Give it a couple of hundred graded calls before you judge.

---

## Changing things

Everything adjustable lives in one file: `config/settings.json`. To change it:

1. On your repository, click into `config`, then click `settings.json`.
2. Click the **pencil icon** at the top right.
3. Change a number. Only the numbers — keep every comma and quotation mark exactly where it is.
4. Scroll down, click **Commit changes**.

The next scheduled run uses your new number. The most likely ones to want:

| Line | Means |
|---|---|
| `"starting": 500.0` | Your bankroll in dollars. |
| `"kelly_fraction": 0.25` | How aggressively it stakes. Lower is safer. Do not raise this. |
| `"max_plays_per_week": 6` | Most bets it will ever log in one week. |
| `"best_bet": 0.045` | How big an edge earns the top label. |

If you break the file, the next run turns red and nothing else is harmed — click the pencil again and put it back.

---

## Optional — running it on your own computer

You never need to do this. It is useful only if you want to see the page before uploading anything, or try out settings changes instantly.

**You need Python**, a free program that runs the code:

- **Windows:** open the Microsoft Store, search "Python 3.12", click Get.
- **Mac:** go to https://www.python.org/downloads/, download, run the installer.

**Then open a terminal:**

- **Windows:** press the Start button, type `powershell`, press Enter.
- **Mac:** press `Cmd+Space`, type `terminal`, press Enter.

A window with text appears. Type these lines one at a time, pressing Enter after each. Replace the path in the first line with wherever your `nfl-edge` folder actually is — on both systems you can type `cd ` (with the space) and then drag the folder onto the terminal window to fill in the path for you.

```
cd /path/to/nfl-edge
pip install -r requirements.txt
python -m tools.make_demo
python -m pipeline.build --offline
cd site
python -m http.server 8000
```

Now open **http://localhost:8000** in your browser. You will see the whole site running on made-up games — there is an orange banner saying so. Press `Ctrl+C` in the terminal to stop it.

To use real data instead of the made-up games, run `python -m pipeline.build` (no `--offline`). It clears out the fake data automatically first.

> On a Mac, if `python` says "command not found", type `python3` and `pip3` instead everywhere.

---

## When something goes wrong

**The workflow shows a red X.**
Click the failed run, then click the step with the red mark to see the message.
- "Permission denied" or an error on the push step → Step 4 was missed. Go back and set workflow permissions to read and write.
- Anything mentioning `settings.json` → a typo in a setting you edited. Open the file, look for a missing comma or quote mark.

**Actions only shows "Tests" — no "Refresh model", no "Deploy to GitHub Pages".**
One or more workflow files did not upload. Files in the hidden `.github` folder are the ones browsers most often skip when you drag a folder in, and they upload independently, so getting one and not the others is common.

First, check which ones actually made it. Open this address (with your username in it):

```
https://github.com/<you>/nfl-edge/tree/main/.github/workflows
```

You should see three files: `refresh.yml`, `deploy.yml` and `tests.yml`. A 404, or a shorter list, tells you exactly what is missing.

To add a missing one — this method never skips anything:

1. Go to `https://github.com/<you>/nfl-edge/new/main`
2. In the filename box type exactly `.github/workflows/refresh.yml`. As you type each `/` it turns into a folder — that is what you want.
3. Open the same file from your unzipped folder in Notepad (Windows) or TextEdit (Mac), select all, copy, and paste it into the big box on GitHub.
4. Scroll down, click **Commit changes**.
5. Repeat for any other missing file.

Then go back to the Actions tab. All three should now be listed in the left sidebar.

> **On a Mac, TextEdit may add formatting.** Before pasting, use **Format → Make Plain Text**, or open the file in the free TextEdit alternative of your choice. A file with smart quotes in it will not run.

**The page says "404" or "There isn't a GitHub Pages site here".**
Either Step 5 was missed, or the first run has not finished. Check the Actions tab is green, then wait two minutes and refresh.

**The page loads but every tab is empty.**
The build has not finished yet. Wait for the green tick and reload.

**The board shows no bets at all.**
Usually correct. The NFL market is very hard to beat and most weeks the honest answer is that nothing qualifies. The Best Bets tab will show you the three closest calls so you can see it is working.

**Everything is greyed out mid-week.**
The board only prices games within about two weeks. Mid-week, the current week's games have already been played — use the Week dropdown to look ahead.

**I want to start completely fresh.**
Delete the repository (Settings → scroll to the bottom → Delete this repository) and do Steps 2 to 6 again. You lose the bet history; nothing else.

---

## The honest part

This is a real model, built carefully, and it will still lose money plenty of weeks. The NFL betting market is the sharpest in North American sport — thousands of professionals and enormous amounts of money price the same sixteen games all week. Any genuine edge is small, and the model is built to report small edges rather than flatter you with big ones.

Use the Accuracy tab. If the tiers are not separating after a few hundred graded calls, believe that rather than the labels. Bet only what you can afford to lose.
