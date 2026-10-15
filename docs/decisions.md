# Decisions log

Every methodological choice in this project, in plain English, with the reason and the evidence. The numbers in brackets refer to the sections below.

## Summary

| # | Decision | Why, in one line |
|---|---|---|
| 1 | Use April 2017 data | First month with clock times, needed to time the gap between games |
| 2 | Regex parser instead of python-chess | Same results on 1.3M checked values, 34x faster |
| 3 | Parse every game, filter later | A player's "previous game" must be their real previous game |
| 4 | Classify speed from the time control | Lichess had no "Rapid" category in 2017 |
| 5 | A game ends at its last move; use each player's real starting clock | Fixes impossible overlaps (5.1% -> 0.07%) from berserk and walk-aways |
| 6 | Sessions break after 30 min; streaks count within a session | Tilt is short-lived; last night's losses shouldn't count today |
| 7 | Rated blitz/rapid, finished normally, both players moved | Only games that were actually played |
| 8 | Record the opponent's state | My result is the mirror of my opponent's |
| 9 | Standard Elo formula for expected score | Close to Glicko-2; fixed effects absorb constant bias |
| 10 | Lobby games for the headline, tournaments separately | Tournament pairing is automatic, so a fast restart isn't a choice |
| 11 | Cool-down comparison by time before the next game | Separates short-lived effects from rating mechanics |
| 12 | Controls in every model | Colour, session position (fatigue), opponent's state |
| 13 | Measure who keeps playing after a loss | We only see performance for those who continue |
| 14 | Control for rating vs. the player's own average | Without it, rating mechanics flip the sign |
| 15 | Placebo test with the game two back | Shows the effect builds up over a session |

---

## 1. Use April 2017 data instead of a 2016 month

**Decision:** Start with the April 2017 Lichess dump (3.48 GB compressed, 11.35M games) instead of a 2016 month.

**Why:** The research question needs the gap between the end of one game and the start of the next (did the player requeue within 1 minute?). PGN headers only record when a game *started*. Clock times (`[%clk]`) in the move text let us calculate how long a game actually lasted, so we can compute the end time instead of guessing it. Lichess started recording clock times in April 2017. You can see this in the file sizes: March and April 2017 have nearly the same number of games, but April's file is 60% bigger because every move now carries a timestamp. Without clocks we would have to estimate game length from the move count, which is too rough for a "<1 minute" bucket.

**Trade-off:** About 3× larger than a 2016 month, but still small enough for a laptop, and the file is streamed rather than loaded into memory.

---

## 2. Parse with a simple regex parser, not python-chess

**Decision:** `src/parse.py` reads PGN headers and `[%clk]` clock times with regular expressions instead of using python-chess.

**Why:** We never need the chess moves themselves, only who played, the result, the ratings and the clock times. python-chess replays every move to rebuild the board, which is slow. Benchmark on the first 100,000 games of April 2017 (`python src/benchmark_parse.py`):

| Method | Games/s | Clock times? | Est. time for 11.35M games |
|---|---:|---|---:|
| Regex parser (ours) | 38,318 | yes | ~5 min |
| python-chess `read_headers` | 30,675 | no | — |
| python-chess `read_game` | 1,114 | yes | ~170 min |

**Correctness check:** The benchmark compares our output with python-chess on all 100,000 games (IDs, players, result, ratings, time control, termination, number of clocked moves, first and final clocks). All 1,300,000 field values matched, with 0 mismatches. So we get the same answer 34× faster.

---

## 3. Parse every game; filter later

**Decision:** `parse.py` keeps every game in the dump (bullet, classical, abandoned, ...). Filtering to rated blitz/rapid happens in `features.py`, *after* each player's game history is built.

**Why:** If we dropped bullet games before building sequences, a player who goes blitz → bullet → blitz would look as if their two blitz games were back to back. Their "previous game" and "minutes since last game" would both be wrong. So we build each player's timeline from all their games and only then choose which games to analyze as outcomes.

**Limitation:** This dump only contains *rated standard* games. Casual games and variants (Chess960, etc.) aren't in it, so a player's true previous game may sometimes be missing.

---

## 4. Classify game speed from the time control, not the Event label

**Decision:** Speed = Lichess's own rule on *estimated game length* = base time + 40 × increment (seconds): bullet < 180, **blitz 180–479**, **rapid 480–1499**, classical ≥ 1500.

**Why:** Lichess had no "Rapid" category in April 2017. A 10+0 game was labelled "Classical". Using the time control gives the same blitz/rapid split Lichess uses today, so our results can be compared with recent months. (In 2017 these "rapid" games were rated in the Classical rating pool. That's fine: both players share the same pool, so the expected score is still a fair comparison.)

---

## 5. A game "ends" at its last move, and we use each player's real starting clock

**Decision:** We estimate when a game ended from the clock times: game length = thinking time used by both players, where time used = starting clock + increments earned − clock left after the player's last move. Two details:
- **Starting clock = the clock after the player's first move**, not the nominal time control. In tournaments a player can "berserk" (halve their clock and give up the increment). In the raw PGN, berserked players' clocks start at half and almost never go up (4 of 322 cases), while normal players' clocks go up 92% of the time thanks to the increment.
- **We stop at the last move, not the official end.** When someone stops moving, the game only officially ends when their clock runs out, which can be minutes later.

**Why:** The first version used the nominal time control and the official end. Then **5.1% of next games appeared to start before the previous game had ended**, which is impossible. Digging in: tournaments had 18.6% of these (berserk), and in normal games the overlaps were people who had already walked away. In 75% of cases the opponent had stopped moving, and in 24% it was the player themselves, with the overlap matching the time left on their own clock (correlation 0.89). In both cases the player had *mentally* finished at the last move and started a new game. After the fix only **0.07%** of gaps are negative (they fall into the "<1 min" bucket).

**Validation:** Rematches must start after the previous game ends, so they show how accurate the estimate is. After the fix, only 0.02% of rematch gaps are negative and the fastest 1% start 12 seconds after the estimated last move. That's a realistic time to click "rematch".

**Known error:** First moves are untimed, and the time a player spends before resigning isn't recorded, so the true gap is usually a few seconds *shorter* than our estimate. That's far smaller than the bucket widths (1, 5, 30 minutes).

---

## 6. Player timelines, sessions and losing streaks

**Decision:**
- A player's timeline = all their rated games sorted by start time, **except correspondence** (multi-day games overlap everything else).
- **New session** = 30+ minutes since the previous game's last move.
- **Losing streak** = losses in a row going into this game, **counted within the current session**. A win, a draw, or an unfinished game ends the streak, and so does a 30-minute break.

**Why:** Tilt is a short-lived emotional state. Three losses last night shouldn't count as a 3-game streak this morning. Bullet games in the middle of a session *do* count toward the streak and as the "previous game" (see #3), because they're part of what the player just experienced.

---

## 7. Which games we analyze

**Decision:** Outcome rows are player-games that are rated, blitz or rapid, finished normally (checkmate/resignation/draw/time-out, not abandoned, rules-violation or unfinished), and where **both players made at least one move**.

| Step | Player-game rows |
|---|---:|
| All games × 2 players | 22,697,012 |
| Not correspondence | 22,614,030 |
| Rated | 22,614,030 |
| Blitz or rapid | 15,745,326 |
| Finished normally | 15,664,950 |
| Both players moved | 15,650,552 |
| Both ratings present | 15,650,552 |
| No BOT on either side | 15,650,552 |

**Why "both players moved":** A game where one side never moved wasn't really played, so it says nothing about decision quality. (The BOT and missing-rating rules remove nothing in 2017, because BOT accounts arrived on Lichess in 2018. They stay in the code for when we scale to recent months.)

---

## 8. Record the opponent's state too

**Decision:** Each row also records the opponent's previous result and losing streak going into the same game.

**Why:** In every game my residual is exactly minus my opponent's. If my opponent is tilted, my residual goes up, which has nothing to do with my own state. Every model controls for this (decision #12).

---

## 9. Expected score uses the Elo formula, although Lichess uses Glicko-2

**Decision:** Expected score = `1 / (1 + 10^((opp − player) / 400))`, the standard Elo formula.

**Why it's OK:** Glicko-2 also tracks how *uncertain* each rating is, which isn't in the PGN. For established players the Elo formula gives almost the same expected score. Any constant over- or under-prediction for a player is absorbed by player fixed effects, because we compare each player with themselves. **Limitation:** for brand-new players with uncertain ratings, the expected score is noisier.

---

## 10. Main analysis uses lobby games; tournaments are a robustness check

**Decision:** Headline results use normal "lobby" games only (13.4M of 15.65M rows). Tournament games are analyzed separately.

**Why:** In Lichess arena tournaments you're paired automatically as soon as your game ends: the median gap before the next game is 68 seconds, against 224 seconds in the lobby. A fast restart in a tournament isn't the player choosing to rage-queue. Arena scoring also gives double points after two wins in a row, which changes how players react to wins and losses.

---

## 11. The "cool-down" comparison separates tilt from rating mechanics and bad form

**Decision:** Besides comparing "after a loss" with "after a win", we estimate the loss effect separately by how long the player waited before the next game (<1, 1–5, 5–30, 30+ minutes). The key number is the loss effect at **<1 min minus** the loss effect at **30+ min**.

**Why:** Two things other than tilt can make "after a loss" look different:
1. **Rating mechanics.** A loss lowers your rating, typically by 10 points (middle half of losses: 8 to 12, in the analysis sample). If the loss was partly bad luck, you're now slightly *underrated*, so you'll tend to beat the new prediction. This pushes toward "players do *better* after a loss".
2. **Bad form.** If you're having an off day, you lose *and* you keep playing badly. This pushes toward "worse after a loss" without any emotional reaction.

Rating mechanics don't depend on how many minutes you waited before the next game, so they cancel out when we subtract the 30+ minute loss effect from the <1 minute one. So does any *long-lasting* change in form. An emotional reaction should fade with time, so if the loss effect is worse for an immediate requeue than after a break, the effect is short-lived.

**Honest caveat:** a 30+ minute gap often means the next day. So this comparison separates *short-lived* effects (tilt, fatigue, a bad hour) from *long-lasting* ones (ratings, skill). It **cannot** tell emotional tilt apart from a bad day that ends when you stop playing. The practical takeaway, "take a break after a loss", holds either way.

---

## 12. Controls in every model

**Decision:** All models include player fixed effects plus: playing White, rapid vs blitz, how many games into the session (3rd, 4th, 5th, 6–10th, 11th+; baseline 1st–2nd), and the **opponent's** state (opponent just lost / drew / is starting a new session).

**Why:**
- **Colour:** White scores about 3.5 percentage points above prediction (ratings ignore colour), and colours alternate in rematches.
- **Position in session:** a 5-loss streak needs at least 6 games, so without this control a streak effect could just be fatigue.
- **Opponent's state:** my residual is exactly minus my opponent's, so their tilt shows up in my number (decision #8).

**Standard errors** are clustered by player. As a robustness check we also cluster by opponent (two-way), because both players' rows of a game share the same result.

---

## 13. Who keeps playing after a loss (selection)

**Decision:** Separately estimate, within player, whether a loss changes the chance of (a) playing again within 30 minutes and (b) requeuing within 1 minute. Games from the last day of the month are excluded, because their next game may be in next month's file.

**Why:** We can only measure performance after a loss for people who *keep playing*. If players who feel tilted tend to quit, the ones we observe are the calmer ones, and we would underestimate tilt. This also gives the engagement numbers for the business section.

---

## 14. Control for the player's rating relative to their own average

**Decision:** Every main model also controls for the player's current rating minus **their own average rating this month** (same speed, in units of 100 Elo). The model without it (the original specification) is saved alongside it (`rating_control = "none"` in `results/models.csv`).

**Why:** Without this control the model said players do **better** after a loss (+0.63 points, about +4 Elo) and better still after longer losing streaks. That's an artifact of how ratings work. Each loss typically knocks about 10 points off your rating, but your actual skill hasn't changed that fast, so in the next game you're slightly *underrated* and beat the prediction. The control asks the question we actually care about: **compared with how this player usually plays, how do they play after a loss?** With it, the sign flips: −0.99 points (about −7 Elo) after a loss, −3.5 points (about −24 Elo) after 5+ losses in a row.

**Evidence the control does what we think:** its coefficient is −0.13 per 100 Elo above your own average. A 100-point rating gap changes the expected score by about 0.14, so ~90% of short-term rating swings within a month are noise, not real changes in skill.

**Look-ahead check:** the monthly average includes games played *later*. As a check we use the average of the player's *previous* 20 games instead (no future information). The loss effect shrinks to −3.7 Elo, and the cool-down contrast grows to −9.1 Elo. Both are still clearly negative, so we describe the loss effect as **"about 4–7 Elo"**.

---

## 15. Placebo test: the effect builds up over a session

**Test:** For games 3+ in a session, put the results of *both* the previous game (N−1) and the one before it (N−2) in the model.

**Result:** A loss in game N−2 matters about as much (−5.0 Elo) as a loss in game N−1 (−4.5 Elo).

**What it means:** The effect isn't a reaction to the single most recent game. It **builds up across a session**, which matches the streak result (−6 Elo after 1 loss, −24 Elo after 5+). Two explanations fit, and this data can't fully separate them: (a) frustration accumulating with each loss, or (b) a bad day that causes the losses and continues until the player stops. Either way the effect **is gone after a 30+ minute break** (loss effect after a 30+ min gap: −0.1 Elo, not significant). We report this honestly instead of calling everything "tilt".
