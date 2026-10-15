# Tilt: Do Chess Players Make Worse Decisions After Losing?

> Work in progress. Findings will appear here once the analysis is done.

Using millions of rated games from the [Lichess open database](https://database.lichess.org), this project tests whether players perform worse than their rating predicts right after a loss, and whether it gets worse when they requeue quickly or are on a losing streak.

## Setup

Requires Python 3.11+ (developed on 3.14).

```bash
py -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
```
