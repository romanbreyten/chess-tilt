# LinkedIn post draft

**Attach:** `figures/1_hero_losing_streaks.png` (square, sized for the mobile feed)

---

After 5 losses in a row, online chess players play like they're 24 rating points weaker.

I analyzed 11.3 million games from Lichess to answer a question every chess player has asked after a bad loss: am I actually playing worse now, or does it just feel that way?

I compared each player's games after a loss with their own games after a win, measuring results against what both players' ratings predicted. Three things stood out:

1. Losses stack up. After one loss, players perform about 6 rating points below their usual level. After five in a row, 24.

2. A break makes it disappear. Jumping straight into the next game carries the full effect. After a break of 30+ minutes, it's statistically zero.

3. People rush back in at the worst moment. In blitz, 56% of games after five straight losses are followed by a new game within a minute, compared with 43% after a win.

The most interesting part to work through: my first model said players do *better* after losing. That turned out to be an artifact of the rating system. A loss lowers your rating right away, before your actual skill has changed, so in the next game you're briefly underrated. Comparing players with their own usual level fixed it and flipped the result.

Business takeaway: a platform could test a light "take a short break" nudge after three losses in a row, and A/B test whether it improves next-game performance and long-term retention.

Code, full method and limitations: github.com/romanbreyten/chess-tilt

#DataScience #Python #BehavioralEconomics #Chess

---

**Notes before posting**
- Add the dashboard link next to the GitHub link once it's deployed.
- The data can't tell emotional tilt apart from simply having a bad day; if someone asks, that's in the README's limitations, and the "take a break" takeaway holds either way.
