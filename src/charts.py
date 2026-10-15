"""Phase 4: charts for the README and LinkedIn post.

Usage:  python src/charts.py
Input:  results/*.csv (from analysis.py). Every number on a chart is read from there.
Output: figures/*.png

Style: one hue per series (blue, then orange), gray for context, thin marks, hairline
gridlines, values labelled in ink (never in the series colour). The two series colours
were checked for colour-blind separation with a palette validator (worst-case ΔE 24.7).
"""
import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")  # render to files, no window
import matplotlib.pyplot as plt
from matplotlib.patches import PathPatch, Rectangle
from matplotlib.path import Path

from config import RESULTS_DIR, ROOT

FIGURES_DIR = ROOT / "figures"
ELO_PER_SCORE_POINT = 400 / (np.log(10) * 0.25)  # same constant as analysis.py
MAIN = "own monthly average"                       # rating_control value of the main models
MINUS = "−"                                   # typographic minus sign

# Colours (light theme)
SURFACE, INK, INK_2, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#898781"
GRID, BASELINE = "#e1e0d9", "#c3c2b7"
BLUE, ORANGE, GRAY = "#2a78d6", "#eb6834", "#b5b3ab"

plt.rcParams.update({
    "font.family": ["Segoe UI", "DejaVu Sans"],
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "text.color": INK, "axes.labelcolor": INK_2, "xtick.color": INK_2, "ytick.color": MUTED,
    "axes.edgecolor": BASELINE, "axes.linewidth": 0.8,
    "xtick.major.size": 0, "ytick.major.size": 0,
})


# ---------------------------------------------------------------- helpers

def load(name):
    return pd.read_csv(RESULTS_DIR / name)


def to_elo(df):
    """Add Elo-equivalent columns for the coefficient and its 95% CI."""
    return df.assign(elo=df.coef * ELO_PER_SCORE_POINT,
                     elo_low=df.ci_low * ELO_PER_SCORE_POINT,
                     elo_high=df.ci_high * ELO_PER_SCORE_POINT)


def fmt_elo(v):
    """-6.3 -> '−6', values that round to zero -> '≈ 0'."""
    r = round(v)
    return "≈ 0" if r == 0 else f"{MINUS if r < 0 else '+'}{abs(r)}"


def style_axes(ax, ygrid=True):
    """Recessive chrome: no box, hairline horizontal gridlines, no ticks."""
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    if ygrid:
        ax.yaxis.grid(True, color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)


def titles(fig, title, subtitle, size=20):
    """Left-aligned title that states the finding, with a smaller subtitle under it."""
    fig.text(0.04, 0.965, title, fontsize=size, fontweight="bold", va="top", ha="left")
    fig.text(0.04, 0.965 - 0.012 * size / 10 - 0.05 * title.count("\n") - 0.035, subtitle,
             fontsize=size * 0.58, color=INK_2, va="top", ha="left", linespacing=1.4)


def footnote(fig, text, size=9):
    fig.text(0.04, 0.02, text, fontsize=size, color=MUTED, va="bottom", ha="left", linespacing=1.4)


def rounded_bar(ax, x, value, width, color, radius_px=8):
    """A bar from 0 to `value`, rounded at the data end and square at the baseline.

    Drawn as one outline (so no seams): straight up the left side, a curved corner,
    across the end, another curved corner, back down to the baseline. The radius is
    given in pixels and converted to data units on each axis, so call this after the
    axis limits are final.
    """
    (x0, y0), (x1, y1) = ax.transData.inverted().transform([(0, 0), (radius_px, radius_px)])
    rx, ry = abs(x1 - x0), min(abs(y1 - y0), abs(value) / 2)
    left, right, s = x - width / 2, x + width / 2, np.sign(value) or 1
    end = value - s * ry  # where the straight sides stop and the corners start
    verts = [(left, 0), (left, end), (left, value), (left + rx, value),
             (right - rx, value), (right, value), (right, end), (right, 0), (left, 0)]
    codes = [Path.MOVETO, Path.LINETO, Path.CURVE3, Path.CURVE3,
             Path.LINETO, Path.CURVE3, Path.CURVE3, Path.LINETO, Path.CLOSEPOLY]
    ax.add_patch(PathPatch(Path(verts, codes), fc=color, ec="none", zorder=2.5))


def ci_whisker(ax, x, low, high):
    ax.plot([x, x], [low, high], color=INK_2, linewidth=1.3, solid_capstyle="round", zorder=3)


def save(fig, name):
    FIGURES_DIR.mkdir(exist_ok=True)
    fig.savefig(FIGURES_DIR / name, dpi=150)
    plt.close(fig)
    print(f"  wrote figures/{name}")


# ---------------------------------------------------------------- charts

def hero_losing_streaks():
    """Hero chart for LinkedIn: the streak 'staircase' plus the after-a-break bar. Square for mobile."""
    m = to_elo(load("models.csv").query("rating_control == @MAIN"))
    streak = m[(m.model == "streak") & m.term.str.startswith("streak_")].set_index("term")
    order = ["streak_1", "streak_2", "streak_3", "streak_4", "streak_5plus"]
    brk = m[(m.model == "cooldown") & (m.term == "loss_x_gap_30plus")].iloc[0]
    n_obs = streak.n_obs.iloc[0]

    fig = plt.figure(figsize=(8, 8))
    ax = fig.add_axes([0.10, 0.21, 0.86, 0.53])
    style_axes(ax)
    xs = list(range(5)) + [5.9]
    values = [streak.loc[t, "elo"] for t in order] + [brk.elo]
    lows = [streak.loc[t, "elo_low"] for t in order] + [brk.elo_low]
    highs = [streak.loc[t, "elo_high"] for t in order] + [brk.elo_high]

    ax.set_xlim(-0.6, 6.5)
    ax.set_ylim(min(lows) * 1.18, 4)
    ax.axhline(0, color=BASELINE, linewidth=1.2, zorder=2)
    for x, v, lo, hi in zip(xs, values, lows, highs):
        rounded_bar(ax, x, v, 0.62, BLUE if x < 5 else GRAY)
        ci_whisker(ax, x, lo, hi)
        ax.text(x, min(v, lo) - 1.0, fmt_elo(v), ha="center", va="top", fontsize=19, fontweight="bold")

    ax.set_xticks(xs, ["1", "2", "3", "4", "5+", "Lost, then\n30+ min\nbreak"], fontsize=14)
    ax.tick_params(axis="x", pad=8)
    ax.set_yticks([-30, -20, -10, 0], [f"{MINUS}30", f"{MINUS}20", f"{MINUS}10", "0"], fontsize=11)
    # x-axis title centred under the five streak bars only (data x = 2), not the break bar
    label_x = fig.transFigure.inverted().transform(ax.transData.transform((2, 0)))[0]
    fig.text(label_x, 0.105, "Losses in a row before the game", ha="center", fontsize=13, color=INK_2)

    fig.text(0.05, 0.955, "Every loss in a row makes\nchess players play worse", fontsize=27,
             fontweight="bold", va="top", linespacing=1.15)
    fig.text(0.05, 0.835, "…and a 30-minute break resets it.", fontsize=19, color=INK_2, va="top")
    fig.text(0.05, 0.785, "Strength in the next game vs. the same player’s usual level, in Elo points",
             fontsize=12.5, color=INK_2, va="top")
    footnote(fig, f"{n_obs / 1e6:.1f}M player-games within a session · Lichess blitz & rapid, April 2017 "
                  "· 95% confidence intervals\nPlayer fixed effects; compared with the same player after a win. "
                  "Break bar: separate model, same controls.", size=8.5)
    save(fig, "1_hero_losing_streaks.png")


def cooldown_by_gap():
    m = to_elo(load("models.csv").query("rating_control == @MAIN and model == 'cooldown'"))
    terms = ["loss_x_gap_lt1", "loss_x_gap_1_5", "loss_x_gap_5_30", "loss_x_gap_30plus"]
    d = m.set_index("term").loc[terms]
    contrast = m[m.term == "loss_x_gap_lt1 - loss_x_gap_30plus"].iloc[0]

    fig = plt.figure(figsize=(10, 6.2))
    ax = fig.add_axes([0.08, 0.20, 0.88, 0.58])
    style_axes(ax)
    ax.set_xlim(-0.6, 3.6)
    ax.set_ylim(d.elo_low.min() * 1.3, 1)
    ax.axhline(0, color=BASELINE, linewidth=1.2, zorder=2)
    for x, (_, r) in enumerate(d.iterrows()):
        rounded_bar(ax, x, r.elo, 0.34, BLUE)
        ci_whisker(ax, x, r.elo_low, r.elo_high)
        ax.text(x, min(r.elo, r.elo_low) - 0.4, fmt_elo(r.elo), ha="center", va="top", fontsize=15, fontweight="bold")
    ax.set_xticks(range(4), ["Under 1 min", "1–5 min", "5–30 min", "30+ min\n(new session)"], fontsize=12)
    ax.set_yticks([-8, -6, -4, -2, 0], [f"{MINUS}8", f"{MINUS}6", f"{MINUS}4", f"{MINUS}2", "0"], fontsize=10)
    ax.set_xlabel("Time between the loss and the next game", fontsize=11, labelpad=10)

    titles(fig, "The hit lasts all session — and is gone after a 30-minute break",
           "Strength after a loss vs. after a win, by how long the player waited before the next game (Elo points, 95% CI)",
           size=19)
    est, lo, hi = (f"{v:.1f}".replace("-", MINUS) for v in (contrast.elo, contrast.elo_low, contrast.elo_high))
    footnote(fig, f"Requeuing within 1 minute vs. after a 30+ min break: {est} Elo "
                  f"(95% CI {lo} to {hi})."
                  f"  {contrast.n_obs / 1e6:.1f}M player-games, Lichess blitz & rapid lobby games, April 2017.")
    save(fig, "2_cooldown_by_gap.png")


def by_rating_band():
    g = to_elo(load("effects_by_group.csv").query("speed == 'All' and rating_band != 'All'"))
    bands = ["<1200", "1200-1600", "1600-2000", "2000+"]
    one = g[g.term == "prev_loss"].set_index("rating_band").loc[bands]
    five = g[g.term == "streak_5plus"].set_index("rating_band").loc[bands]

    fig = plt.figure(figsize=(10, 6.2))
    ax = fig.add_axes([0.08, 0.20, 0.88, 0.58])
    style_axes(ax)
    ax.set_xlim(-0.6, 3.6)
    ax.set_ylim(min(five.elo_low.min(), one.elo_low.min()) * 1.2, 2)
    ax.axhline(0, color=BASELINE, linewidth=1.2, zorder=2)
    w, off = 0.24, 0.135  # two bars per band, with a small surface gap between them
    for i, band in enumerate(bands):
        for dx, r, color in [(-off, one.loc[band], BLUE), (off, five.loc[band], ORANGE)]:
            rounded_bar(ax, i + dx, r.elo, w, color, radius_px=6)
            ci_whisker(ax, i + dx, r.elo_low, r.elo_high)
            label = fmt_elo(r.elo) + ("\nn.s." if r.pvalue >= 0.05 else "")
            ax.text(i + dx, min(r.elo, r.elo_low) - 0.8, label, ha="center", va="top", fontsize=12, fontweight="bold")
    ax.set_xticks(range(4), ["Under 1200", "1200–1600", "1600–2000", "2000+"], fontsize=12)
    ax.set_yticks([-40, -30, -20, -10, 0], [f"{MINUS}40", f"{MINUS}30", f"{MINUS}20", f"{MINUS}10", "0"], fontsize=10)
    ax.set_xlabel("Player rating", fontsize=11, labelpad=10)
    ax.legend(handles=[Rectangle((0, 0), 1, 1, fc=BLUE), Rectangle((0, 0), 1, 1, fc=ORANGE)],
              labels=["After 1 loss", "After 5+ losses in a row"], loc="lower right",
              frameon=False, fontsize=11, handlelength=1.0, handleheight=1.0)

    titles(fig, "Lower-rated players are hit hardest",
           "Strength in the next game vs. the same player’s usual level, compared with after a win (Elo points, 95% CI)",
           size=19)
    footnote(fig, "n.s. = not statistically significant (p ≥ 0.05).  Lichess blitz & rapid lobby games within a session, April 2017.")
    save(fig, "3_by_rating_band.png")


def rating_control_flips_sign():
    m = to_elo(load("models.csv").query("model == 'streak' and term.str.startswith('streak_')", engine="python"))
    order = ["streak_1", "streak_2", "streak_3", "streak_4", "streak_5plus"]
    xs = np.arange(5)

    fig = plt.figure(figsize=(10, 6.2))
    ax = fig.add_axes([0.08, 0.18, 0.72, 0.57])
    style_axes(ax)
    ax.axhline(0, color=BASELINE, linewidth=1.2, zorder=2)
    series =[("none", GRAY, "Naive model:\ncompared with\ntheir current rating"),
              (MAIN, BLUE, "Main model:\ncompared with\ntheir usual level")]
    for control, color, label in series:
        d = m[m.rating_control == control].set_index("term").loc[order]
        ax.fill_between(xs, d.elo_low, d.elo_high, color=color, alpha=0.15, linewidth=0)
        ax.plot(xs, d.elo, color=color, linewidth=2.4, solid_capstyle="round", zorder=4,
                marker="o", markersize=8, markerfacecolor=color, markeredgecolor=SURFACE, markeredgewidth=2)
        ax.annotate(label, (xs[-1], d.elo.iloc[-1]), xytext=(18, 0), textcoords="offset points",
                    va="center", fontsize=11, color=INK, linespacing=1.3)
        ax.text(xs[-1], d.elo.iloc[-1] + (2.2 if d.elo.iloc[-1] > 0 else -2.2), fmt_elo(d.elo.iloc[-1]),
                ha="center", va="bottom" if d.elo.iloc[-1] > 0 else "top", fontsize=12, fontweight="bold")
    ax.set_xlim(-0.3, 4.3)
    ax.set_xticks(xs, ["1", "2", "3", "4", "5+"], fontsize=12)
    ax.set_yticks([-30, -20, -10, 0, 10, 20], [f"{MINUS}30", f"{MINUS}20", f"{MINUS}10", "0", "+10", "+20"], fontsize=10)
    ax.set_xlabel("Losses in a row before the game", fontsize=11, labelpad=10)

    titles(fig, "Why the naive model says losing makes you play better",
           "A loss lowers your rating, so in the next game you’re briefly underrated and “beat” it.\n"
           "Comparing players with their own usual level removes that effect and flips the sign (Elo points, 95% CI).",
           size=19)
    footnote(fig, "Same data and controls in both models; the main model adds the player’s rating minus their "
                  "own average rating that month.")
    save(fig, "4_rating_control_flips_sign.png")


def requeue_after_losses():
    s = load("selection.csv")
    order = ["win", "loss (1 in a row)", "loss (2 in a row)", "loss (3 in a row)",
             "loss (4 in a row)", "loss (5+ in a row)"]
    xs = np.arange(len(order))

    fig = plt.figure(figsize=(10, 6.2))
    ax = fig.add_axes([0.08, 0.24, 0.78, 0.55])
    style_axes(ax)
    for speed, color in [("blitz", BLUE), ("rapid", ORANGE)]:
        d = s[s.speed == speed].set_index("this_game").loc[order]
        y = 100 * d.share_next_within_1min
        ax.plot(xs, y, color=color, linewidth=2.4, marker="o", markersize=8, markerfacecolor=color,
                markeredgecolor=SURFACE, markeredgewidth=2, zorder=4)
        ax.annotate(f"{speed.capitalize()}  {y.iloc[-1]:.0f}%", (xs[-1], y.iloc[-1]), xytext=(14, 0),
                    textcoords="offset points", va="center", fontsize=12, fontweight="bold")
        ax.annotate(f"{y.iloc[0]:.0f}%", (xs[0], y.iloc[0]), xytext=(-14, 0), textcoords="offset points",
                    va="center", ha="right", fontsize=11, color=INK_2)
    ax.set_xlim(-0.5, 5.3)
    ax.set_ylim(0, 65)
    ax.set_xticks(xs, ["Won", "1", "2", "3", "4", "5+"], fontsize=12)
    ax.set_yticks([0, 20, 40, 60], ["0%", "20%", "40%", "60%"], fontsize=10)
    ax.set_xlabel("Result of the game just played (number of losses in a row)", fontsize=11, labelpad=10)
    ax.legend(handles=[plt.Line2D([], [], color=BLUE, lw=2.4), plt.Line2D([], [], color=ORANGE, lw=2.4)],
              labels=["Blitz", "Rapid"], loc="lower left", frameon=False, fontsize=11)

    titles(fig, "The more they lose, the faster they jump back in",
           "Share of games followed by a new game within 1 minute",
           size=19)
    footnote(fig, "Raw rates, Lichess lobby games, April 2017 (last day excluded). Within the same player, a loss raises "
                  "the chance of requeuing within a minute\nand lowers the chance of playing again at all (results/models.csv).")
    save(fig, "5_requeue_after_losses.png")


def raw_vs_expected():
    d = load("descriptive_prev_result.csv").query("speed == 'All' and rating_band == 'All'").set_index("prev_result")
    raw_gap = d.loc["win", "mean_score"] - d.loc["loss", "mean_score"]
    exp_gap = d.loc["win", "mean_expected"] - d.loc["loss", "mean_expected"]

    fig = plt.figure(figsize=(10, 5.6))
    ax = fig.add_axes([0.17, 0.20, 0.70, 0.42])
    style_axes(ax, ygrid=False)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    rows = [("win", "After a win"), ("loss", "After a loss")]
    # Expected = hollow ring, actual = filled dot inside it, so both stay visible when they coincide.
    expected_style = dict(marker="o", ls="", ms=19, mfc=SURFACE, mec=ORANGE, mew=2.5)
    actual_style = dict(marker="o", ls="", ms=10, color=BLUE, mec=SURFACE, mew=1.5)
    for y, (key, _) in enumerate(rows):
        actual, expected = 100 * d.loc[key, "mean_score"], 100 * d.loc[key, "mean_expected"]
        ax.plot(expected, y, zorder=4, **expected_style)
        ax.plot(actual, y, zorder=5, **actual_style)
        ax.annotate(f"{actual:.1f}% actual", (max(actual, expected), y), xytext=(16, 0), textcoords="offset points",
                    va="center", fontsize=12, fontweight="bold")
        ax.annotate(f"{expected:.1f}% expected", (min(actual, expected), y), xytext=(-16, 0),
                    textcoords="offset points", va="center", ha="right", fontsize=11, color=INK_2)
    ax.set_yticks([0, 1], [label for _, label in rows], fontsize=12)
    ax.tick_params(axis="y", colors=INK_2, pad=10)
    ax.set_ylim(1.6, -0.6)
    ax.set_xlim(42, 58)
    ticks = [42, 46, 50, 54, 58]
    ax.set_xticks(ticks, [f"{t}%" for t in ticks], fontsize=10)
    ax.set_xlabel("Score in the next game (win = 100%, draw = 50%)", fontsize=11, labelpad=10)
    ax.legend(handles=[plt.Line2D([], [], **actual_style), plt.Line2D([], [], **{**expected_style, "ms": 14})],
              labels=["Actual score", "Expected from both players’ ratings"], loc="upper center", ncol=2,
              bbox_to_anchor=(0.5, 1.30), frameon=False, fontsize=11)

    titles(fig, "After a loss, players win less — mostly because of who they play",
           f"Raw score drops {100 * raw_gap:.1f} points after a loss, but {exp_gap / raw_gap:.0%} of that drop "
           "is already predicted by the two players’ ratings.\n"
           "That’s why this project measures performance against the rating prediction, not raw win rate.",
           size=18)
    footnote(fig, "Next game in the same session, Lichess blitz & rapid lobby games, April 2017.")
    save(fig, "6_raw_vs_expected_score.png")


def main():
    print("Writing charts...")
    hero_losing_streaks()
    cooldown_by_gap()
    by_rating_band()
    rating_control_flips_sign()
    requeue_after_losses()
    raw_vs_expected()


if __name__ == "__main__":
    main()
