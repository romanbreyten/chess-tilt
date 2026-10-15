# Decisions log

Every methodological choice, in plain English, with the reason. Newest at the bottom.

---

## 1. Use April 2017 data instead of a 2016 month

**Decision:** Start with the April 2017 Lichess dump (3.48 GB compressed, 11.35M games) instead of a 2016 month.

**Why:** The research question needs the gap between the end of one game and the start of the next (did the player requeue within 1 minute?). PGN headers only record when a game *started*. Clock times (`[%clk]`) in the move text let us calculate how long a game actually lasted, so we can compute the end time instead of guessing it. Lichess started recording clock times in April 2017. You can see this in the file sizes: March and April 2017 have nearly the same number of games, but April's file is 60% bigger because every move now carries a timestamp. Without clocks we would have to estimate game length from the move count, which is too rough for a "<1 minute" bucket.

**Trade-off:** About 3× larger than a 2016 month, but still well under the 5 GB limit, and we stream it instead of loading it into memory.

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
- **New session** = 30+ minutes since the previous game's last move (from the project spec).
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

**Why:** In every game my residual is exactly minus my opponent's. If my opponent is tilted, my residual goes up, which has nothing to do with my own state. The model in Phase 3 can control for this.

---

## 9. Expected score uses the Elo formula, although Lichess uses Glicko-2

**Decision:** Expected score = `1 / (1 + 10^((opp − player) / 400))`, as specified.

**Why it's OK:** Glicko-2 also tracks how *uncertain* each rating is, which isn't in the PGN. For established players the Elo formula gives almost the same expected score. Any constant over- or under-prediction for a player is absorbed by player fixed effects, because we compare each player with themselves. **Limitation:** for brand-new players with uncertain ratings, the expected score is noisier.
