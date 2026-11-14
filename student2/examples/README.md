# Saved demo examples

These files are previously saved historical run artifacts copied from the source repository for reference and review. They are not current forecasts, live agent outputs, or newly generated predictions.

## Included examples

- Mood example: `mood_run_20261109T130837Z`
  - Source: `runs/mood_run_20261109T130837Z`
  - Date: 2026-11-09T13:08:36.530362Z prediction time; target 2026-11-10T13:08:36.530362Z
  - Data source: Alternative.me Fear & Greed Index summaries and readings
  - Limitation: the index reflects sentiment and volatility context rather than direct order-flow or a live market forecast.

- News example: `probe_run_20261111T210601Z`
  - Source: `runs/probe_run_20261111T210601Z`
  - Date: 2026-11-11T21:06:01.324032Z prediction time; target 2026-11-12T21:06:01.324032Z
  - Data source: saved accepted evidence and article URLs captured in the run output; these are historical references, not a live news feed.
  - Limitation: the saved run reflects only the evidence present at that moment and cannot be treated as a current forecast.

- Fake-headline comparison: `news_specialist_fake_headline_20261110`
  - Source: `tests/fixtures/news_specialist_fake_headline`
  - Date: 2026-11-07T21:51:27.932434Z prediction time; target 2026-11-08T21:51:27.932434Z
  - Data source: clean baseline input, attacked article set, and a synthetic injected headline recorded in the manifest
  - Limitation: this attack comparison is one paired run using a synthetic headline. It is not proof of general robustness, resilience, or forecast accuracy across other inputs or market conditions.

## Important note

These artifacts are retained as archived examples of earlier saved runs. They are intentionally not meant to be treated as fresh model outputs or as a guarantee of future performance.
