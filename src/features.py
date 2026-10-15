"""Build one row per (player, game) with the features the analysis needs.

Usage:  python src/features.py

Input:  data/processed/games.parquet         (one row per game, from parse.py)
Output: data/processed/player_games.parquet  (one row per player per game, analysis sample only)

Steps (all in DuckDB SQL, so 22M rows never have to fit in pandas):
  1. Score each game, classify its speed, and estimate how long it lasted from the clock times.
  2. Split each game into two rows: one from White's point of view, one from Black's.
  3. Sort each player's games by start time (their "timeline") and look back at the previous
     game: its result, when it ended, and how long the player waited before starting this one.
  4. Group games into sessions (a break of 30+ minutes starts a new session) and count
     the losing streak going into each game.
  5. Keep only the games we analyze (rated blitz/rapid, finished normally, ...), attach the
     opponent's state, and compute expected score and the performance residual.

Every non-obvious choice here is explained in docs/decisions.md.
"""
import duckdb
import pandas as pd

from config import GAMES_FILE, PLAYER_GAMES_FILE, RESULTS_DIR

SESSION_BREAK_MINUTES = 30

# Steps 1-4. Reads a table/view called `games` with the schema written by parse.py.
TIMELINE_SQL = f"""
WITH
scored AS (
    SELECT
        *,
        CASE result WHEN '1-0' THEN 1.0 WHEN '0-1' THEN 0.0 WHEN '1/2-1/2' THEN 0.5 END AS white_score,

        -- Lichess's own speed rule: estimated length = base time + 40 * increment (seconds).
        -- We can't use the Event label, because Lichess had no "Rapid" category in 2017.
        CASE
            WHEN base_seconds IS NULL                         THEN 'correspondence'
            WHEN base_seconds + 40 * increment_seconds < 30   THEN 'ultrabullet'
            WHEN base_seconds + 40 * increment_seconds < 180  THEN 'bullet'
            WHEN base_seconds + 40 * increment_seconds < 480  THEN 'blitz'
            WHEN base_seconds + 40 * increment_seconds < 1500 THEN 'rapid'
            ELSE 'classical'
        END AS speed,

        (n_clock_plies + 1) // 2 AS white_moves,  -- White moves on plies 1, 3, 5, ...
        n_clock_plies // 2       AS black_moves,

        -- "Berserk" (tournaments only): a player halves their own clock and gives up the increment.
        -- It shows up as a starting clock below the time control.
        CASE WHEN white_first_clock < base_seconds THEN 0 ELSE increment_seconds END AS white_increment,
        CASE WHEN black_first_clock < base_seconds THEN 0 ELSE increment_seconds END AS black_increment
    FROM games
),

timed AS (
    -- Time from the start of the game to its LAST MOVE = thinking time used by both players.
    -- Time used by one side = its starting clock + increments earned - clock left after its last move.
    --   * starting clock = the clock after its first move (first moves are untimed on Lichess)
    --   * increments are earned on every move except that untimed first one, hence (moves - 1)
    -- We stop at the last move, not the official end: see docs/decisions.md #6.
    SELECT
        *,
          coalesce(greatest(white_first_clock + white_increment * (white_moves - 1) - white_final_clock, 0), 0)
        + coalesce(greatest(black_first_clock + black_increment * (black_moves - 1) - black_final_clock, 0), 0)
        AS duration_seconds
    FROM scored
    WHERE speed <> 'correspondence'  -- multi-day games overlap everything else; not part of a timeline
),

player_rows AS (
    SELECT game_id, event, speed, time_control, termination, n_clock_plies, start_time, duration_seconds,
           white AS player, black AS opponent, 'white' AS color,
           white_elo AS player_elo, black_elo AS opp_elo,
           white_title AS player_title, black_title AS opp_title,
           white_rating_diff AS rating_diff, white_score AS score
    FROM timed
    UNION ALL
    SELECT game_id, event, speed, time_control, termination, n_clock_plies, start_time, duration_seconds,
           black, white, 'black',
           black_elo, white_elo,
           black_title, white_title,
           black_rating_diff, 1 - white_score
    FROM timed
),

with_end AS (
    SELECT
        *,
        start_time + to_seconds(duration_seconds::BIGINT) AS last_move_time,
        -- a "clean" game was actually played and finished normally
        (termination IN ('Normal', 'Time forfeit') AND score IS NOT NULL AND n_clock_plies >= 2) AS is_clean
    FROM player_rows
),

ordered AS (
    SELECT
        *,
        row_number()         OVER w AS game_idx,
        lag(game_id)         OVER w AS prev_game_id,
        lag(score)           OVER w AS prev_score,
        lag(score, 2)        OVER w AS prev2_score,     -- for the placebo test
        lag(last_move_time)  OVER w AS prev_last_move_time,
        lag(speed)           OVER w AS prev_speed,
        lag(opponent)        OVER w AS prev_opponent,
        lag(is_clean)        OVER w AS prev_is_clean,
        lead(start_time)     OVER w AS next_start_time  -- to measure who keeps playing (selection)
    FROM with_end
    WINDOW w AS (PARTITION BY player ORDER BY start_time, game_id)
),

gaps AS (
    SELECT
        *,
        -- minutes between the previous game's last move and the start of this game
        date_diff('second', prev_last_move_time, start_time) / 60.0 AS gap_minutes,
        (prev_last_move_time IS NULL
         OR date_diff('second', prev_last_move_time, start_time) >= {SESSION_BREAK_MINUTES * 60}) AS new_session
    FROM ordered
),

sessions AS (
    SELECT *, sum(new_session::INT) OVER (PARTITION BY player ORDER BY game_idx) AS session_id
    FROM gaps
),

streaks AS (
    -- Losses in a row ending at this game, within the session:
    --   this game's position minus the position of the last non-loss (win, draw, or unfinished).
    SELECT
        *,
        game_idx - min(game_idx) OVER s + 1 AS game_in_session,
        game_idx - coalesce(
            max(CASE WHEN score IS DISTINCT FROM 0 THEN game_idx END) OVER s_so_far,
            min(game_idx) OVER s - 1
        ) AS losses_in_row_through_this_game
    FROM sessions
    WINDOW s        AS (PARTITION BY player, session_id),
           s_so_far AS (PARTITION BY player, session_id ORDER BY game_idx
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
)

SELECT
    *,
    -- streak going INTO this game = the streak through the previous game in the same session
    coalesce(lag(losses_in_row_through_this_game)
             OVER (PARTITION BY player, session_id ORDER BY game_idx), 0) AS loss_streak_before
FROM streaks
"""

# Rules for which games we analyze as outcomes (methodology step 1). Applied cumulatively,
# in this order, so the funnel printed by main() shows how many rows each rule removes.
SAMPLE_RULES = [
    ("rated",                       "event LIKE 'Rated%'"),
    ("blitz or rapid",              "speed IN ('blitz', 'rapid')"),
    ("finished normally",           "termination IN ('Normal', 'Time forfeit') AND score IS NOT NULL"),
    ("both players moved",          "n_clock_plies >= 2"),
    ("both ratings present",        "player_elo IS NOT NULL AND opp_elo IS NOT NULL"),
    ("no BOT on either side",       "coalesce(player_title, '') <> 'BOT' AND coalesce(opp_title, '') <> 'BOT'"),
]
SAMPLE_WHERE = " AND ".join(f"({cond})" for _, cond in SAMPLE_RULES)

# Step 5. Reads the `timeline` table built from TIMELINE_SQL.
SAMPLE_SQL = f"""
SELECT
    t.game_id, t.player, t.opponent, t.color, t.speed, t.time_control,
    t.event LIKE '%tournament%' AS is_tournament,
    t.start_time, t.last_move_time, t.duration_seconds,
    t.player_elo, t.opp_elo, t.rating_diff,
    CASE WHEN t.player_elo < 1200 THEN '<1200'
         WHEN t.player_elo < 1600 THEN '1200-1600'
         WHEN t.player_elo < 2000 THEN '1600-2000'
         ELSE '2000+' END AS rating_band,

    -- outcome: actual score minus the score the Elo formula expects given both ratings
    t.score,
    1 / (1 + pow(10, (t.opp_elo - t.player_elo) / 400.0)) AS expected_score,
    t.score - 1 / (1 + pow(10, (t.opp_elo - t.player_elo) / 400.0)) AS residual,

    -- the player's previous game
    t.game_idx, t.prev_game_id, t.prev_score, t.prev2_score, t.prev_speed, t.prev_is_clean,
    t.opponent = t.prev_opponent AS is_rematch,
    t.gap_minutes,
    CASE WHEN t.prev_last_move_time IS NULL THEN NULL
         WHEN t.gap_minutes < 1  THEN '<1 min'
         WHEN t.gap_minutes < 5  THEN '1-5 min'
         WHEN t.gap_minutes < {SESSION_BREAK_MINUTES} THEN '5-30 min'
         ELSE '30+ min' END AS gap_bucket,
    t.new_session, t.session_id, t.game_in_session, t.loss_streak_before,
    -- minutes from this game's last move to the player's next game (NULL = no more games this month)
    date_diff('second', t.last_move_time, t.next_start_time) / 60.0 AS next_gap_minutes,

    -- the opponent's state going into the same game (their tilt affects our residual too)
    o.prev_score AS opp_prev_score,
    o.new_session AS opp_new_session,
    o.loss_streak_before AS opp_loss_streak_before
FROM (SELECT * FROM timeline WHERE {SAMPLE_WHERE}) t
JOIN timeline o ON o.game_id = t.game_id AND o.color <> t.color
"""


def build_timeline(con):
    """Create the `timeline` table (all non-correspondence player-games) from the `games` view."""
    con.execute(f"CREATE OR REPLACE TABLE timeline AS {TIMELINE_SQL}")


def build_sample(con):
    """Return the analysis sample as a DuckDB relation (requires build_timeline first)."""
    return con.sql(SAMPLE_SQL)


def sample_funnel(con):
    """How many games, players and player-game rows there are, and how many survive each sample rule.

    Printed, and saved to results/sample_funnel.csv so the README and dashboard can cite these counts.
    """
    count = lambda sql: con.sql(sql).fetchone()[0]
    rows = [
        ("games in the dump", count("SELECT count(*) FROM games")),
        ("players in the dump", count("SELECT count(DISTINCT p) FROM (SELECT white AS p FROM games "
                                      "UNION ALL SELECT black FROM games)")),
        ("all player-games", count("SELECT 2 * count(*) FROM games")),
        ("not correspondence", count("SELECT count(*) FROM timeline")),
    ]
    conditions = []
    for label, cond in SAMPLE_RULES:
        conditions.append(f"({cond})")
        rows.append((label, count(f"SELECT count(*) FROM timeline WHERE {' AND '.join(conditions)}")))
    funnel = pd.DataFrame(rows, columns=["step", "count"])

    print("\n=== Sample funnel ===")
    for step, n in rows:
        print(f"  {step:<28}{n:>14,}")
    return funnel


def print_checks(con):
    def show(title, sql):
        print(f"\n=== {title} ===")
        print(con.sql(sql).df().to_string(index=False))

    show("Estimated game length by time control (top 6, seconds)", """
        SELECT time_control, count(*) AS player_games,
               quantile_cont(duration_seconds, 0.1)::INT AS p10, median(duration_seconds)::INT AS median,
               quantile_cont(duration_seconds, 0.9)::INT AS p90, max(duration_seconds) AS max
        FROM s GROUP BY 1 ORDER BY 2 DESC LIMIT 6""")
    show("Gap between end of previous game and start of this one (seconds)", """
        SELECT CASE WHEN is_rematch THEN 'rematch (same opponent)' ELSE 'new opponent' END AS kind,
               count(*) AS player_games,
               round(100 * avg((gap_minutes < 0)::INT), 2) AS pct_negative,
               (quantile_cont(gap_minutes, 0.01) * 60)::INT AS p1,
               (quantile_cont(gap_minutes, 0.05) * 60)::INT AS p5,
               (quantile_cont(gap_minutes, 0.25) * 60)::INT AS p25,
               (median(gap_minutes) * 60)::INT AS median
        FROM s WHERE gap_minutes IS NOT NULL GROUP BY 1""")
    show("Gap buckets", """
        SELECT gap_bucket, count(*) AS player_games, round(100 * count(*) / sum(count(*)) OVER (), 1) AS pct
        FROM s GROUP BY 1 ORDER BY min(coalesce(gap_minutes, -1e9))""")
    show("Losing streak going into the game (same session)", """
        SELECT least(loss_streak_before, 5) AS streak_5plus, count(*) AS player_games,
               round(100 * count(*) / sum(count(*)) OVER (), 2) AS pct
        FROM s GROUP BY 1 ORDER BY 1""")
    show("Sample by speed, rating band, tournament", """
        SELECT speed, rating_band, is_tournament, count(*) AS player_games FROM s GROUP BY ALL ORDER BY ALL""")
    show("Residual check (should be ~0: each game's two residuals cancel)", """
        SELECT count(*) AS rows, count(DISTINCT player) AS players,
               round(avg(residual), 6) AS mean_residual, round(avg(expected_score), 4) AS mean_expected
        FROM s""")


def main():
    con = duckdb.connect()
    con.execute(f"CREATE VIEW games AS SELECT * FROM read_parquet('{GAMES_FILE.as_posix()}')")

    print("Building player timelines...")
    build_timeline(con)
    RESULTS_DIR.mkdir(exist_ok=True)
    sample_funnel(con).to_csv(RESULTS_DIR / "sample_funnel.csv", index=False)

    build_sample(con).write_parquet(PLAYER_GAMES_FILE.as_posix())
    con.execute(f"CREATE VIEW s AS SELECT * FROM read_parquet('{PLAYER_GAMES_FILE.as_posix()}')")
    print(f"\nWrote {PLAYER_GAMES_FILE}")
    print_checks(con)


if __name__ == "__main__":
    main()
