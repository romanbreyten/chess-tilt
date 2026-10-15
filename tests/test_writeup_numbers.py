"""Every headline number in the README and LinkedIn draft must match results/*.csv.

The expected text is rebuilt from the results files, so if the analysis changes and the
write-up doesn't, this test fails.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
ELO = 400 / (np.log(10) * 0.25)  # same constant as src/analysis.py
MAIN = "own monthly average"
MINUS = "−"


def signed(v, decimals=1):
    return f"{v:+.{decimals}f}".replace("-", MINUS)


def neg(v, decimals=0):
    """-6.9 -> '−7' (decimals=0) or '−6.9' (decimals=1)."""
    return f"{v:.{decimals}f}".replace("-", MINUS)


def one(df, **where):
    for col, val in where.items():
        df = df[df[col] == val]
    assert len(df) == 1, where
    return df.iloc[0]


@pytest.fixture(scope="module")
def r():
    return {name: pd.read_csv(RESULTS / f"{name}.csv") for name in
            ["models", "effects_by_group", "robustness", "selection", "sample_funnel", "descriptive_prev_result"]}


def readme_expected(r):
    m, g, rb = r["models"], r["effects_by_group"], r["robustness"]
    funnel = r["sample_funnel"].set_index("step")["count"]
    main = lambda model, term: one(m, model=model, rating_control=MAIN, term=term)
    loss = main("after_loss", "prev_loss")
    streak = [main("streak", t) for t in ["streak_1", "streak_2", "streak_3", "streak_4", "streak_5plus"]]
    brk, contrast = main("cooldown", "loss_x_gap_30plus"), main("cooldown", "loss_x_gap_lt1 - loss_x_gap_30plus")
    band = lambda b, t: one(g, speed="All", rating_band=b, term=t).coef * ELO
    sel = r["selection"].set_index(["speed", "this_game"])
    d = r["descriptive_prev_result"].query("speed == 'All' and rating_band == 'All'").set_index("prev_result")
    raw_gap = d.loc["win", "mean_score"] - d.loc["loss", "mean_score"]
    exp_gap = d.loc["win", "mean_expected"] - d.loc["loss", "mean_expected"]
    check = lambda label, est: one(rb, check=label, estimate=est).coef * ELO
    LOSS, COOL = "loss effect (same session)", "cool-down: loss effect <1 min minus 30+ min"

    expected = [
        f"{funnel['games in the dump'] / 1e6:.1f} million rated games",
        f"{funnel['games in the dump']:,} games by {funnel['players in the dump']:,} players",
        f"{loss.n_obs / 1e6:.1f} million player-games by {loss.n_players:,.0f} players",
        f"**{neg(loss.coef * ELO)} Elo** | {neg(loss.ci_low * ELO, 1)} to {neg(loss.ci_high * ELO, 1)}",
        " / ".join(neg(s.coef * ELO) for s in streak[:4]) + " Elo",
        f"**{neg(streak[4].coef * ELO)} Elo** | {neg(streak[4].ci_low * ELO, 1)} to {neg(streak[4].ci_high * ELO, 1)}",
        f"**{neg(brk.coef * ELO, 1)} Elo** (not significant) | {neg(brk.ci_low * ELO, 1)} to {signed(brk.ci_high * ELO)}",
        f"{-contrast.coef * ELO:.1f} Elo (95% CI {-contrast.ci_high * ELO:.1f} to {-contrast.ci_low * ELO:.1f})",
        f"{neg(band('<1200', 'prev_loss'))} Elo for players under 1200, {neg(band('1200-1600', 'prev_loss'))} for "
        f"1200–1600, {neg(band('1600-2000', 'prev_loss'))} for 1600–2000",
        f"lose {-band('2000+', 'streak_5plus'):.0f} points after five straight losses",
        f"({neg(one(g, speed='rapid', rating_band='All', term='prev_loss').coef * ELO, 1)} vs "
        f"{neg(one(g, speed='blitz', rating_band='All', term='prev_loss').coef * ELO, 1)} Elo)",
        f"{-100 * one(m, model='keeps_playing', term='this_loss').coef:.1f} percentage points *less*",
        f"{100 * one(m, model='requeues_1min', term='this_loss').coef:.1f} points *more*",
        f"{100 * sel.loc[('blitz', 'loss (5+ in a row)'), 'share_next_within_1min']:.0f}% of games after five straight "
        f"losses are followed by a new game within a minute, against "
        f"{100 * sel.loc[('blitz', 'win'), 'share_next_within_1min']:.0f}% after a win",
        f"{100 * raw_gap:.1f} percentage points less after a loss",
        f"{exp_gap / raw_gap:.0%} of that gap",
        f"({signed(one(m, model='after_loss', rating_control='none', term='prev_loss').coef * ELO)} Elo)",
        f"| Main result | {neg(check('main (lobby games)', LOSS), 1)} | {neg(check('main (lobby games)', COOL), 1)} |",
        f"| {neg(check('rating vs previous 20 games (no look-ahead)', LOSS), 1)} | "
        f"{neg(check('rating vs previous 20 games (no look-ahead)', COOL), 1)} |",
        f"| {neg(check('players with 20+ games', LOSS), 1)} | {neg(check('players with 20+ games', COOL), 1)} |",
        f"| {neg(check('blitz only', LOSS), 1)} / {neg(check('rapid only', LOSS), 1)} | "
        f"{neg(check('blitz only', COOL), 1)} / {neg(check('rapid only', COOL), 1)} |",
        f"| {neg(check('tournament games only', LOSS), 1)} | {neg(check('tournament games only', COOL), 1)} |",
        f"| {neg(check('previous game same speed', LOSS), 1)} | {neg(check('previous game same speed', COOL), 1)} |",
        f"| {neg(check('excluding rematches', LOSS), 1)} | {neg(check('excluding rematches', COOL), 1)} |",
        f"| {neg(check('two-way clustered SE (player + opponent)', LOSS), 1)} | "
        f"{neg(check('two-way clustered SE (player + opponent)', COOL), 1)} |",
        f"({neg(one(rb, check='placebo: games 3+ in a session', term='prev2_loss').coef * ELO, 1)} Elo) as the most "
        f"recent loss ({neg(one(rb, check='placebo: games 3+ in a session', term='prev_loss').coef * ELO, 1)} Elo)",
    ]
    return expected


def test_linkedin_numbers_match_results(r):
    m, sel = r["models"], r["selection"].set_index(["speed", "this_game"])
    funnel = r["sample_funnel"].set_index("step")["count"]
    streak = lambda t: one(m, model="streak", rating_control=MAIN, term=t).coef * ELO
    brk = one(m, model="cooldown", rating_control=MAIN, term="loss_x_gap_30plus")
    expected = [
        f"play like they're {-streak('streak_5plus'):.0f} rating points weaker",
        f"I analyzed {funnel['games in the dump'] / 1e6:.1f} million games",
        f"about {-streak('streak_1'):.0f} rating points below their usual level. After five in a row, "
        f"{-streak('streak_5plus'):.0f}.",
        f"{100 * sel.loc[('blitz', 'loss (5+ in a row)'), 'share_next_within_1min']:.0f}% of games after five straight "
        f"losses are followed by a new game within a minute, compared with "
        f"{100 * sel.loc[('blitz', 'win'), 'share_next_within_1min']:.0f}% after a win",
    ]
    assert brk.pvalue >= 0.05, "post says the after-a-break effect is statistically zero"
    text = (ROOT / "docs" / "linkedin_post.md").read_text(encoding="utf-8")
    missing = [s for s in expected if s not in text]
    assert not missing, "LinkedIn draft out of sync with results/:\n" + "\n".join(missing)


def test_readme_numbers_match_results(r):
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    missing = [s for s in readme_expected(r) if s not in text]
    assert not missing, "README out of sync with results/:\n" + "\n".join(missing)
