"""Phase 3: do players perform worse than their rating predicts right after a loss?

Usage:  python src/analysis.py
Input:  data/processed/player_games.parquet (from features.py)
Output: results/*.csv. Every number in the README and LinkedIn post comes from these files.

Outcome everywhere: residual = actual score - expected score from the two ratings.
A residual of -0.01 means scoring 1 percentage point less than the ratings predict, which is
about what you'd expect from a player ~7 Elo points weaker (see ELO_PER_SCORE_POINT).

Main sample: rated blitz/rapid *lobby* games (not tournaments, see docs/decisions.md #10)
whose previous game was a normally finished game.

Rating control (docs/decisions.md #14): a loss lowers your rating, so in the next game you are
briefly *underrated* compared with your usual strength, and you beat the prediction for purely
mechanical reasons. Every main model therefore controls for the player's current rating minus
their own average rating this month. The model without it is also saved (rating_control = "none").

Models (all with player fixed effects, i.e. each player is compared with themselves,
and standard errors clustered by player):
  after_loss   in-session games: after a loss / draw vs. after a win
  streak       in-session games: after 1, 2, 3, 4, 5+ losses in a row vs. after a win
  cooldown     all games: the loss effect separately by time since the previous game,
               including 30+ min (a new session). Rating mechanics and day-long "form" are the
               same however long you wait, so if the loss effect shrinks with a longer break,
               that points to a short-lived emotional reaction (tilt).
  full         in-session games: loss, streak, gap and loss x gap together
  keeps_playing / requeues_1min   who keeps playing after a loss (selection, engagement)
"""
import time

import duckdb
import numpy as np
import pandas as pd
from linearmodels.panel import PanelOLS
from scipy.stats import norm

from config import PLAYER_GAMES_FILE, RESULTS_DIR

# Slope of the Elo expected-score curve at 50%: 1 score point ~ 695 Elo points near an even match.
ELO_PER_SCORE_POINT = 400 / (np.log(10) * 0.25)

PREV_RESULT = "CASE prev_score WHEN 1 THEN 'win' WHEN 0.5 THEN 'draw' WHEN 0 THEN 'loss' END"
STREAK_GROUP = """CASE WHEN prev_score = 1   THEN '0 (after win)'
                       WHEN prev_score = 0.5 THEN '0 (after draw)'
                       WHEN loss_streak_before >= 5 THEN '5+'
                       ELSE loss_streak_before::VARCHAR END"""

# 0/1 indicator columns for the regressions, as SQL expressions.
INDICATORS = {
    # previous game
    "prev_loss": "prev_score = 0",
    "prev_draw": "prev_score = 0.5",
    "prev2_loss": "prev2_score = 0 AND game_in_session >= 3",   # placebo: game N-2, same session
    "prev2_draw": "prev2_score = 0.5 AND game_in_session >= 3",
    # losing streak going into the game (streak_1 = exactly one loss)
    "streak_1": "loss_streak_before = 1",
    "streak_2": "loss_streak_before = 2",
    "streak_3": "loss_streak_before = 3",
    "streak_4": "loss_streak_before = 4",
    "streak_5plus": "loss_streak_before >= 5",
    # time since the previous game's last move (baseline: under 1 minute)
    "gap_1_5": "gap_bucket = '1-5 min'",
    "gap_5_30": "gap_bucket = '5-30 min'",
    "gap_30plus": "gap_bucket = '30+ min'",
    # loss / draw effect at each gap
    "loss_x_gap_lt1": "prev_score = 0 AND gap_bucket = '<1 min'",
    "loss_x_gap_1_5": "prev_score = 0 AND gap_bucket = '1-5 min'",
    "loss_x_gap_5_30": "prev_score = 0 AND gap_bucket = '5-30 min'",
    "loss_x_gap_30plus": "prev_score = 0 AND gap_bucket = '30+ min'",
    "draw_x_gap_lt1": "prev_score = 0.5 AND gap_bucket = '<1 min'",
    "draw_x_gap_1_5": "prev_score = 0.5 AND gap_bucket = '1-5 min'",
    "draw_x_gap_5_30": "prev_score = 0.5 AND gap_bucket = '5-30 min'",
    "draw_x_gap_30plus": "prev_score = 0.5 AND gap_bucket = '30+ min'",
    # controls
    "is_white": "color = 'white'",                      # White scores more; ratings ignore colour
    "rapid": "speed = 'rapid'",
    "opp_after_loss": "NOT opp_new_session AND opp_prev_score = 0",   # opponent's own tilt
    "opp_after_draw": "NOT opp_new_session AND opp_prev_score = 0.5",
    "opp_new_session": "opp_new_session",
    "pos_3": "game_in_session = 3",                     # how deep into the session (fatigue);
    "pos_4": "game_in_session = 4",                     #   baseline = 1st or 2nd game
    "pos_5": "game_in_session = 5",
    "pos_6_10": "game_in_session BETWEEN 6 AND 10",
    "pos_11plus": "game_in_session >= 11",
    # selection outcomes (what the player does AFTER this game)
    "this_loss": "score = 0",
    "this_draw": "score = 0.5",
    "keeps_playing": "next_gap_minutes < 30",
    "requeues_1min": "next_gap_minutes < 1",
    # games in the last day of the month: their "next game" may be in next month's file
    "not_last_day": "start_time < (SELECT max(start_time) FROM s) - INTERVAL 1 DAY",
}

CONTROLS = ["is_white", "rapid", "opp_after_loss", "opp_after_draw", "opp_new_session",
            "pos_3", "pos_4", "pos_5", "pos_6_10", "pos_11plus"]

# Current rating minus the player's own average rating this month in the same rating pool,
# in units of 100 Elo. Robustness: the same against the average of their previous 20 games.
RATING_CONTROL = "rating_vs_own_avg"
RATING_CONTROL_NO_LOOKAHEAD = "rating_vs_recent20"

MODELS = {
    "after_loss": ["prev_loss", "prev_draw"],
    "streak": ["streak_1", "streak_2", "streak_3", "streak_4", "streak_5plus", "prev_draw"],
    "cooldown": ["gap_1_5", "gap_5_30", "gap_30plus",
                 "loss_x_gap_lt1", "loss_x_gap_1_5", "loss_x_gap_5_30", "loss_x_gap_30plus",
                 "draw_x_gap_lt1", "draw_x_gap_1_5", "draw_x_gap_5_30", "draw_x_gap_30plus"],
    "full": ["prev_loss", "prev_draw", "streak_2", "streak_3", "streak_4", "streak_5plus",
             "gap_1_5", "gap_5_30", "loss_x_gap_1_5", "loss_x_gap_5_30"],
}
# The cool-down test: loss effect when requeuing within 1 minute minus after a 30+ minute break.
COOLDOWN_CONTRAST = ("loss_x_gap_lt1", "loss_x_gap_30plus")


# ---------------------------------------------------------------- descriptive tables

def describe(con, by, where):
    """Mean residual per group with a 95% CI clustered by player.

    `by` maps output column -> SQL expression (a constant like "'All'" gives a roll-up row).
    Clustered SE of a mean = sqrt(G/(G-1) * sum over players of (sum of their deviations)^2) / n,
    so a player with 500 games can't make the estimate look more precise than it is.
    """
    select = ", ".join(f"{expr} AS {name}" for name, expr in by.items())
    keys = ", ".join(by)
    df = con.sql(f"""
        WITH base AS (
            SELECT {select}, player, residual, score, expected_score, opp_elo - player_elo AS opp_minus_player
            FROM s WHERE {where}),
        grp AS (
            SELECT {keys}, count(*) AS n, count(DISTINCT player) AS players,
                   avg(residual) AS mean_residual, avg(score) AS mean_score,
                   avg(expected_score) AS mean_expected, avg(opp_minus_player) AS mean_opp_minus_player
            FROM base GROUP BY ALL),
        per_player AS (  -- each player's summed deviation from the group mean
            SELECT {keys}, sum(base.residual - grp.mean_residual) AS d
            FROM base JOIN grp USING ({keys}) GROUP BY {keys}, base.player),
        ss AS (SELECT {keys}, sum(d * d) AS sum_sq FROM per_player GROUP BY ALL)
        SELECT grp.*, sqrt(sum_sq * players / nullif(players - 1, 0)) / n AS se
        FROM grp JOIN ss USING ({keys}) ORDER BY ALL""").df()
    df["ci_low"] = df.mean_residual - 1.96 * df.se
    df["ci_high"] = df.mean_residual + 1.96 * df.se
    df["elo_equiv"] = df.mean_residual * ELO_PER_SCORE_POINT
    return df


def describe_with_rollups(con, by, where):
    """describe() for every speed x rating band combination, plus 'All' roll-ups for each."""
    parts = []
    for speed in ("speed", "'All'"):
        for band in ("rating_band", "'All'"):
            parts.append(describe(con, {"speed": speed, "rating_band": band, **by}, where))
    return pd.concat(parts, ignore_index=True)


# ---------------------------------------------------------------- fixed-effects regressions

def load_model_frame(con):
    """All rows that have a usable previous game, with every indicator as a 0/1 column."""
    indicators = ",\n".join(f"coalesce(({expr}), FALSE)::TINYINT AS {name}" for name, expr in INDICATORS.items())
    df = con.sql(f"""
        SELECT player, game_idx, opponent, residual, speed, rating_band, is_tournament, new_session,
               game_in_session, is_rematch, speed = prev_speed AS prev_same_speed, n_player_games,
               {RATING_CONTROL}, {RATING_CONTROL_NO_LOOKAHEAD},
               {indicators}
        FROM (
            SELECT *,
                   count(*) OVER (PARTITION BY player, is_tournament) AS n_player_games,
                   -- blitz and rapid have separate ratings, so compare within the same speed
                   (player_elo - avg(player_elo) OVER (PARTITION BY player, speed)) / 100.0 AS {RATING_CONTROL},
                   CASE WHEN count(*) OVER prev20 >= 5
                        THEN (player_elo - avg(player_elo) OVER prev20) / 100.0 END AS {RATING_CONTROL_NO_LOOKAHEAD}
            FROM s
            WINDOW prev20 AS (PARTITION BY player, speed ORDER BY game_idx ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING))
        WHERE prev_score IS NOT NULL AND prev_is_clean""").df()
    df["player"] = df["player"].astype("category")
    return df


def fe_fit(df, xcols, y="residual", two_way=False):
    """OLS with player fixed effects, SEs clustered by player (and opponent if two_way).

    Returns the fitted result; tidy() turns it into a table.
    """
    data = df.dropna(subset=xcols).set_index(["player", "game_idx"])  # rows lacking a control are skipped
    xcols = [c for c in xcols if data[c].nunique() > 1]  # e.g. no 5+ streaks in a small subgroup
    X = data[xcols].astype(float).assign(const=1.0)
    model = PanelOLS(data[y].astype(float), X, entity_effects=True, drop_absorbed=True)
    if two_way:
        opp = pd.DataFrame({"opponent": data["opponent"].astype("category").cat.codes}, index=data.index)
        return model.fit(cov_type="clustered", cluster_entity=True, clusters=opp)
    return model.fit(cov_type="clustered", cluster_entity=True)


def tidy(res, terms=None):
    """Coefficient table: coef, SE, 95% CI, p-value, N, players, Elo-equivalent."""
    terms = [t for t in (terms or res.params.index) if t != "const" and t in res.params.index]
    out = pd.DataFrame({
        "term": terms,
        "coef": res.params[terms].values,
        "se": res.std_errors[terms].values,
        "pvalue": res.pvalues[terms].values,
    })
    out["ci_low"] = out.coef - 1.96 * out.se
    out["ci_high"] = out.coef + 1.96 * out.se
    out["elo_equiv"] = out.coef * ELO_PER_SCORE_POINT
    out["n_obs"] = res.nobs
    out["n_players"] = res.entity_info["total"]
    return out


def contrast(res, a, b):
    """Difference of two coefficients (a - b) with its SE, as a one-row tidy table."""
    cov = res.cov
    coef = res.params[a] - res.params[b]
    se = np.sqrt(cov.loc[a, a] + cov.loc[b, b] - 2 * cov.loc[a, b])
    return pd.DataFrame([{
        "term": f"{a} - {b}", "coef": coef, "se": se, "pvalue": 2 * norm.sf(abs(coef / se)),
        "ci_low": coef - 1.96 * se, "ci_high": coef + 1.96 * se, "elo_equiv": coef * ELO_PER_SCORE_POINT,
        "n_obs": res.nobs, "n_players": res.entity_info["total"],
    }])


def run_models(df):
    """The main models on the lobby sample. Returns (table, fitted results by name)."""
    lobby = df[~df.is_tournament]
    in_session = lobby[~lobby.new_session]
    samples = {"after_loss": in_session, "streak": in_session, "cooldown": lobby, "full": in_session}

    tables, fits = [], {}
    for name, xvars in MODELS.items():
        fits[name] = fe_fit(samples[name], xvars + CONTROLS + [RATING_CONTROL])
        tables.append(tidy(fits[name]).assign(model=name, rating_control="own monthly average"))
    tables.append(contrast(fits["cooldown"], *COOLDOWN_CONTRAST).assign(model="cooldown", rating_control="own monthly average"))

    # The specification as originally written (no rating control), to show what the control changes.
    for name in ("after_loss", "streak", "cooldown"):
        res = fe_fit(samples[name], MODELS[name] + CONTROLS)
        tables.append(tidy(res).assign(model=name, rating_control="none"))
        if name == "cooldown":
            tables.append(contrast(res, *COOLDOWN_CONTRAST).assign(model=name, rating_control="none"))

    # Selection: after this game's result, does the player keep playing / requeue within a minute?
    # Excludes the last day of the month, whose "next game" may fall in the next month's file.
    for outcome in ("keeps_playing", "requeues_1min"):
        res = fe_fit(lobby[lobby.not_last_day == 1], ["this_loss", "this_draw"] + CONTROLS, y=outcome)
        tables.append(tidy(res, ["this_loss", "this_draw"]).assign(model=outcome, rating_control="none"))

    out = pd.concat(tables, ignore_index=True)
    out["outcome"] = out.model.where(out.model.isin(["keeps_playing", "requeues_1min"]), "residual")
    out.loc[out.outcome != "residual", "elo_equiv"] = np.nan  # Elo only makes sense for score outcomes
    return out, fits


def run_robustness(df):
    """Re-estimate the two headline numbers under alternative samples / methods."""
    lobby = df[~df.is_tournament]
    # label: (sample, rating control, two-way clustering)
    checks = {
        "main (lobby games)": (lobby, RATING_CONTROL, False),
        "no rating control (spec as first written)": (lobby, None, False),
        "rating vs previous 20 games (no look-ahead)": (lobby, RATING_CONTROL_NO_LOOKAHEAD, False),
        "players with 20+ games": (lobby[lobby.n_player_games >= 20], RATING_CONTROL, False),
        "blitz only": (lobby[lobby.speed == "blitz"], RATING_CONTROL, False),
        "rapid only": (lobby[lobby.speed == "rapid"], RATING_CONTROL, False),
        "tournament games only": (df[df.is_tournament], RATING_CONTROL, False),
        "previous game same speed": (lobby[lobby.prev_same_speed], RATING_CONTROL, False),
        "excluding rematches": (lobby[~lobby.is_rematch], RATING_CONTROL, False),
        "two-way clustered SE (player + opponent)": (lobby, RATING_CONTROL, True),
    }
    rows = []
    for label, (sample, rating, two_way) in checks.items():
        t0 = time.time()
        extra = CONTROLS + ([rating] if rating else [])
        in_session = sample[~sample.new_session]
        loss = fe_fit(in_session, MODELS["after_loss"] + extra, two_way=two_way)
        rows.append(tidy(loss, ["prev_loss"]).assign(check=label, estimate="loss effect (same session)"))
        cool = fe_fit(sample, MODELS["cooldown"] + extra, two_way=two_way)
        rows.append(contrast(cool, *COOLDOWN_CONTRAST).assign(check=label, estimate="cool-down: loss effect <1 min minus 30+ min"))
        print(f"  {label:<45} {time.time() - t0:5.1f}s")

    # Placebo: does the game BEFORE the previous one matter once we know the previous game?
    # Only games 3+ in a session, so both earlier games are in it (game 3 is the position baseline).
    deep = lobby[lobby.game_in_session >= 3]
    controls = [c for c in CONTROLS if c != "pos_3"] + [RATING_CONTROL]
    placebo = fe_fit(deep, ["prev_loss", "prev_draw", "prev2_loss", "prev2_draw"] + controls)
    rows.append(tidy(placebo, ["prev_loss", "prev2_loss"]).assign(
        check="placebo: games 3+ in a session", estimate="loss effect of game N-1 vs game N-2"))
    return pd.concat(rows, ignore_index=True)


def run_by_group(df):
    """Loss, streak and cool-down effects for each speed x rating band (for the dashboard)."""
    lobby = df[~df.is_tournament]
    rows = []
    for speed in ["All", "blitz", "rapid"]:
        for band in ["All", "<1200", "1200-1600", "1600-2000", "2000+"]:
            g = lobby[((lobby.speed == speed) | (speed == "All")) & ((lobby.rating_band == band) | (band == "All"))]
            in_session = g[~g.new_session]
            for name, sample in [("after_loss", in_session), ("streak", in_session), ("cooldown", g)]:
                res = fe_fit(sample, MODELS[name] + CONTROLS + [RATING_CONTROL])
                terms = [t for t in MODELS[name] if t.startswith(("prev_loss", "streak", "loss_x"))]
                rows.append(tidy(res, terms).assign(speed=speed, rating_band=band, model=name))
                if name == "cooldown":
                    rows.append(contrast(res, *COOLDOWN_CONTRAST).assign(speed=speed, rating_band=band, model=name))
    return pd.concat(rows, ignore_index=True)


# ---------------------------------------------------------------- selection (raw rates)

def selection_table(con):
    """What players do after a win / draw / loss (and after k losses in a row): raw rates."""
    return con.sql("""
        SELECT speed,
               CASE WHEN score = 1 THEN 'win' WHEN score = 0.5 THEN 'draw'
                    WHEN loss_streak_before + 1 >= 5 THEN 'loss (5+ in a row)'
                    ELSE 'loss (' || (loss_streak_before + 1) || ' in a row)' END AS this_game,
               count(*) AS n,
               avg(coalesce(next_gap_minutes < 1, FALSE)::INT)  AS share_next_within_1min,
               avg(coalesce(next_gap_minutes < 5, FALSE)::INT)  AS share_next_within_5min,
               avg(coalesce(next_gap_minutes < 30, FALSE)::INT) AS share_keeps_playing_30min
        FROM s
        WHERE NOT is_tournament AND start_time < (SELECT max(start_time) - INTERVAL 1 DAY FROM s)
        GROUP BY ALL ORDER BY ALL""").df()


# ---------------------------------------------------------------- main

def main():
    RESULTS_DIR.mkdir(exist_ok=True)
    con = duckdb.connect()
    con.execute(f"CREATE VIEW s AS SELECT * FROM read_parquet('{PLAYER_GAMES_FILE.as_posix()}')")

    def save(df, name):
        df.to_csv(RESULTS_DIR / name, index=False)
        print(f"  wrote results/{name} ({len(df)} rows)")

    t0 = time.time()
    print("Descriptive tables...")
    lobby_in_session = "NOT is_tournament AND prev_is_clean AND NOT new_session"
    save(describe_with_rollups(con, {"prev_result": PREV_RESULT}, lobby_in_session), "descriptive_prev_result.csv")
    save(describe_with_rollups(con, {"streak": STREAK_GROUP}, lobby_in_session), "descriptive_streak.csv")
    save(describe_with_rollups(con, {"gap_bucket": "gap_bucket", "prev_result": PREV_RESULT},
                               "NOT is_tournament AND prev_is_clean"), "descriptive_gap.csv")
    save(selection_table(con), "selection.csv")

    print("Loading model data...")
    df = load_model_frame(con)
    print(f"  {len(df):,} rows, {df.player.nunique():,} players")

    print("Main models...")
    models, _ = run_models(df)
    save(models, "models.csv")
    print("Robustness checks...")
    save(run_robustness(df), "robustness.csv")
    print("Effects by speed x rating band...")
    save(run_by_group(df), "effects_by_group.csv")
    print(f"Done in {time.time() - t0:.0f}s")

    show = ["model", "rating_control", "term", "coef", "ci_low", "ci_high", "pvalue", "elo_equiv", "n_obs"]
    with pd.option_context("display.width", 220, "display.float_format", "{:.4f}".format):
        print("\n=== Main models (controls hidden) ===")
        print(models[~models.term.isin(CONTROLS)][show].to_string(index=False))


if __name__ == "__main__":
    main()
