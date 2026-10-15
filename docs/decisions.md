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
| Regex parser (ours) | 39,034 | yes | ~5 min |
| python-chess `read_headers` | 30,763 | no | — |
| python-chess `read_game` | 1,095 | yes | ~173 min |

**Correctness check:** The benchmark compares our output with python-chess on all 100,000 games (IDs, players, result, ratings, time control, termination, number of clocked moves, final clocks). All 1,100,000 field values matched, with 0 mismatches. So we get the same answer 36× faster.

---

## 3. Parse every game; filter later

**Decision:** `parse.py` keeps every game in the dump (bullet, classical, abandoned, ...). Filtering to rated blitz/rapid happens in `features.py`, *after* each player's game history is built.

**Why:** If we dropped bullet games before building sequences, a player who goes blitz → bullet → blitz would look as if their two blitz games were back to back. Their "previous game" and "minutes since last game" would both be wrong. So we build each player's timeline from all their games and only then choose which games to analyze as outcomes.

**Limitation:** This dump only contains *rated standard* games. Casual games and variants (Chess960, etc.) aren't in it, so a player's true previous game may sometimes be missing.
