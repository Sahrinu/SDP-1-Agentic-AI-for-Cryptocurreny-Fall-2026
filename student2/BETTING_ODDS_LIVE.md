# Live market observation and strict forecasting are different

The reader records real transport observations; the older fixture CLI remains
available for strict quote inspection. The Student 2 Betting Odds adapter uses
validated books as evidence for an experimental model estimate.

## What changed

- New `student2/agents/limitless_live.py` retrieves daily market metadata and
  opens one public Socket.IO market-book connection. It does not retry or reconnect.
- It skips the first snapshot and requires a subsequent increasing-version frame
  with a recent provider event timestamp, valid outcome mapping and nonempty
  two-sided depth. Version or timestamp reversal fails the session. The midpoint,
  depth and timestamp all come from that SAME full-book message; the REST listing's
  prices are not used as if they had the stream's timestamp.
- The displayed value is a YES **payout midpoint**. It is not automatically P(BTC
  rises over the next 24 hours). The market start/end and tie rule are displayed.
- It saves the raw market, accepted raw book, quote summary and statuses together
  in a uniquely named `runs/betting_odds_live/.../observation.json` directory.
- Original specialist fixes: malformed quote-time mappings and numeric options
  return insufficient data, invalid reported liquidity is rejected, and arbitrary
  network exception text is no longer copied into saved forecasts.

## Install and run from the team repository

Use the Python environment that already runs Student 2.

```powershell
python -m pip install -r student2/requirements-betting-live.txt
python -m pytest tests/test_limitless_live_reader.py tests/test_betting_odds_specialist.py -q
python -m student2.agents.limitless_live
```

No model, wallet, credential or trading operation is involved. The command may
return a network/stream error or no supported market; that is not a live success.
It records the failing stage and sanitized error type. An observed book is
`observation_status: book_observed`. `team_probability_up` remains null even
when a matching contract is supplied: a book price is not itself a forecast.

The discovery phase has a 30-second budget, up to 20 pages, with at most 10 seconds
per request. One Socket.IO connection/wait attempt uses a 20-second configured
budget; the underlying connect operation has its own request and namespace timeout.
No automatic retries. Quiet markets may time out because an initial snapshot
alone is insufficient for this reader's live-change check.

Use `--slug EXACT_SLUG` to select a known supported active daily market rather
than the soonest-ending supported market. There is no interval substitution.

### PowerShell listing collection

If PowerShell can access the public listing, the following two commands save a
complete paginated listing and use it for discovery before opening the existing
read-only WebSocket stream. Run from the repository root:

```powershell
powershell -NoProfile -File .\student2\verification\collect_limitless_markets.ps1 -OutputPath .\limitless-listing.json
python -m student2.agents.limitless_live --listing-file .\limitless-listing.json
```

Choose a new output filename each time; the collector refuses to overwrite one.
It records the actual UTC completion time of each page, checks pagination and
does not retry. It uses the usual PowerShell TLS and network configuration. Its
page count and elapsed-time limits can reject an incomplete or changing listing.
Listing retrieval times do not establish the freshness of a quote: prices still
come from a separately validated, timestamped WebSocket book. A listing file's
provenance and timestamps are not independently authenticated. This collection
path does not by itself make live forecasting operational.

### Bounded discovery for the price-context demo

`student2/verification/run_market_price_demo.py` uses a separate targeted
search of at most three active-market pages, followed by a request for the
selected market's current details by slug. The search is not exhaustive;
changing totals across unrelated listing pages do not invalidate it. Candidate
IDs and slugs must not be duplicated in the searched pages, and the detail's
identity, rules, times and token mapping must agree with the selected listing.
The selected detail is checked again before the existing WebSocket reader runs.
Only a validated stream frame supplies a quote or its timestamp. The demo saves
the bounded search and detail in `targeted_discovery.json` under its unique run
directory. The complete-listing collector and its stricter pagination checks
remain available separately.

The optional flags `--prediction-time`, `--target-time`, `--price-source`,
`--reference-price-definition` and `--tie-definition` must be supplied together.
They request an additional strict quote inspection. They do not change the
market's actual rules. A currently underway split-payout daily market will still
fail the strict next-24-hour event. Future times are not guessed.

## What can be presented

After a successful run, show the actual retrieved market question, rule window,
bid/ask and midpoint, streamed event time and visible order-book depth. Explain:
"This is the observed price of this particular market contract; it is not yet a
validated probability for our different team forecast."

For a split payout, the idealized expected payout is P(up) + 0.5 P(tie), not
P(up). Real trading prices also reflect spread and other market effects.
No unknown tie probability or interval conversion is invented by this code.

## What is still required for a full team forecast

An evidence-based forecast needs an explicit team window, source and endpoint
convention, a validated timely book and an explicitly supplied model callable.
Different valid market contracts can supply evidence, with their differences
disclosed. The runner still checks the team settlement window. Provider event
timestamps do not prove the creation time of each resting order or guarantee
executability or statistical calibration.

## Verification actually performed

24 new offline tests passed using the uploaded real Pydantic schema and specialist
plus synthetic books and a mocked Socket.IO transport. Tests cover successful
same-frame assessment for a fabricated compatible contract, rejection of ties,
wrong windows, wrong tokens, bad depths, stale/future/out-of-order frames,
non-use of the initial snapshot, sanitization and disconnect cleanup.

The full existing suite was not rerun here: the original saved and synthetic
fixtures and the rest of the repository were not uploaded, and pytest is not
installed in this environment. New tests also run directly with
`python tests/test_limitless_live_reader.py` in a configured Student 2 environment.

One direct REST attempt from the authoring environment returned HTTP 403. It was
not retried. No live book or live probability was obtained here. This does not
establish a UAE restriction; the user's earlier local REST requests succeeded.

## Official implementation references (checked November 14, 2026)

- https://docs.limitless.exchange/developers/websocket/overview
  Public market subscription uses Socket.IO, `/markets`, WebSocket transport;
  no authenticated channel is used.
- https://docs.limitless.exchange/developers/websocket/market-data
  Full-book messages bind slug, levels, sequence and event timestamp. Sizes are
  converted from raw six-decimal units to shares; empty-book midpoint is not evidence.
- https://python-socketio.readthedocs.io/en/stable/client.html
  Standard event-driven client and `python-socketio[client]` dependency.
