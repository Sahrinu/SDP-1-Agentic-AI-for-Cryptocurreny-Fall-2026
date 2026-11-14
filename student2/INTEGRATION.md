# Student 2 forecast adapter

`Student2Adapter` exposes `forecast(day, data)` for News, Mood, or Betting
Odds. Its response uses the team's `agent`, `day`, `p_up`, `evidence`, and
`data_missing` fields, and includes `data_status` and `limitations`. An
insufficient result has `p_up: null` and `data_missing: true`; it is never
converted to 0.5 by the adapter.

The patched root runner preserves abstentions, skips their settlement and scoring,
and reports missing days and attack comparisons with changed participants. Student 2
registration is opt-in. Legacy agents retain the existing date-based CSV settlement.
The runner preserves its existing valid-probability clipping to [0.05, 0.95].

## Registering adapters

The Python `run(...)` interface accepts keyword-only `student2_adapters` (name to
adapter instance), `student2_inputs` (ISO date to snapshot mapping), and
`settlement_contract`. News/Mood model clients must still be explicitly injected
into `Student2Adapter`; the runner never constructs one.

`settlement_contract` must contain `price_source`, `reference_price_definition`,
`tie_definition: "down"`, and `close_times`, mapping each CSV date to its exact
aware closing timestamp. These are declarations about the actual CSV prices,
not permission to relabel existing data. Their truth must be established by the
data provider/caller. For each day, the runner compares both endpoints to the
next available CSV close, including date, source, convention and 24-hour duration.
A mismatch abstains before invoking the adapter. Missing dates are not treated
as a 24-hour step.

Only `student2_` snapshot fields are overlaid. The runner never passes future CSV
rows to agents. Evidence observation-time checks remain in the existing adapter.

`--student2-inputs path.json` loads an explicit bundle with `days` and `settlement`
keys and registers all three adapters without model clients or implicit fetches.
Thus this CLI alone will NOT generate News/Mood forecasts: missing clients cause
abstention. It is not a live demo. Supply clients explicitly using the Python
interface when model calls are authorized and target definitions are agreed.

The Student 2 runner guard permits the repaired `Student2Adapter("betting_odds")`
through the existing settlement-window check. Other or legacy Betting Odds
adapters remain blocked because they may return a contract price directly.
Missing evidence, a model callable or matching settlement metadata still causes
an abstention. No independent claim of live forecast accuracy is made.

A plain runner fake headline remains unsupported by the News adapter. If it
causes an abstention, the attack comparison is marked non-comparable, with no
numeric shift. This is not evidence of fake-news resistance. Proper timestamped
article-injection experiments remain separate work.

All-abstain days have blank forecasts/scores, zero active weights, unchanged coins,
and explicit missing-day counts. A real 0.5 remains a valid forecast. The existing
engine uses equal weights when too few agents can satisfy its influence cap;
`coins.csv` now exposes `cap_feasible` rather than claiming the cap was met.

## Explicit configuration and input snapshots

The team runner settles the next available date-indexed CSV close using a
strictly greater comparison; it does not define an exact 24-hour UTC window,
the source or reference-price convention. Supply the following explicitly in
`data["student2_forecast"]` rather than deriving them from `day`:

- `prediction_time` and `target_time`: timezone-aware timestamps exactly 24
  hours apart, with the prediction's UTC date equal to `day`.
- `price_source` and `reference_price_definition`: the source and comparison
  convention for both endpoints.
- `tie_definition`: `down`, meaning an exact tie is not Up.

Supply timestamped records and a timezone-aware snapshot observation time at
or before prediction using `student2_news_articles` and
`student2_news_observed_at`, `student2_mood_readings` and
`student2_mood_observed_at`, or `student2_market_observation` and
`student2_markets_observed_at`. A publication or reading time alone does not
prove when its contents were obtained or whether they were revised; the
caller is responsible for the snapshot's provenance. Naive evidence
timestamps are rejected instead of silently assigned UTC.

News and Mood require an explicitly injected model client; no model client is
created automatically. For Betting Odds, provide an explicit callable
`model_client(prompt)` and a saved validated `student2_market_observation`
with its timestamped raw book and summary. Optionally supply
`student2_price_context` for an explicit Binance BTC/USDT spot target; source,
price and time checks still apply. The adapter does not fetch or create a model.
Different valid contracts are evidence, with differences disclosed, not direct
forecasts; invalid or stale evidence and unavailable model calls abstain. A nonempty
team `fake_headline` is rejected for News until it is supplied as properly
timestamped article evidence.

For example, this performs no network or model calls and returns an explicit
insufficient-data result:

```python
from student2.integration_adapter import Student2Adapter

result = Student2Adapter("betting_odds").forecast("2026-11-07", {})
assert result["p_up"] is None and result["data_missing"] is True
```

These are adapter requirements, not an assertion that the team has agreed to
the missing market conventions. The saved Limitless daily market has different
timestamps and tie handling; a provider-timestamped book is evidence, not a direct
probability of the team event. See [MARKET_EVIDENCE.md](MARKET_EVIDENCE.md).
