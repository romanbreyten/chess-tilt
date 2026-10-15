# Decisions log

Every methodological choice, in plain English, with the reason. Newest at the bottom.

---

## 1. Use April 2017 data instead of a 2016 month

**Decision:** Start with the April 2017 Lichess dump (3.48 GB compressed, 11.35M games) instead of a 2016 month.

**Why:** The research question needs the gap between the end of one game and the start of the next (did the player requeue within 1 minute?). PGN headers only record when a game *started*. Clock times (`[%clk]`) in the move text let us calculate how long a game actually lasted, so we can compute the end time instead of guessing it. Lichess started recording clock times in April 2017. You can see this in the file sizes: March and April 2017 have nearly the same number of games, but April's file is 60% bigger because every move now carries a timestamp. Without clocks we would have to estimate game length from the move count, which is too rough for a "<1 minute" bucket.

**Trade-off:** About 3× larger than a 2016 month, but still well under the 5 GB limit, and we stream it instead of loading it into memory.
