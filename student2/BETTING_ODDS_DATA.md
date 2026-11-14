# Current status after the live-reader patch

A public Socket.IO book reader is now implemented separately in
`student2.agents.limitless_live`. See [BETTING_ODDS_LIVE.md](BETTING_ODDS_LIVE.md)
for installation, tests and the live observation command. It has offline tests;
a successful live run has NOT been verified by the patch author.

The strict REST quote-inspection path below retains its freshness policy. The
reader binds its midpoint to the same streamed event timestamp; it never assigns
that timestamp to a separate REST quote. The repaired Betting Odds adapter uses
validated market evidence for an experimental model estimate, disclosing rule
differences instead of copying a contract price into the forecast.

## Original REST investigation (historical context)

# Betting Odds: Limitless live-data blocker

The specialist cannot currently verify that a live Limitless REST quote is fresh.
It therefore returns `insufficient_data` unless a trusted adapter supplies a
verified quote timestamp for the exact quote being evaluated. The default
Limitless market fetch does not provide such an adapter. A market update time,
request time, response time, or timestamp from a different quote must not be
substituted for the quote's own verified time.

## Findings from the official documentation

- [Market detail](https://docs.limitless.exchange/api-reference/markets/get-market)
  provides outcome prices and an `updatedAt` field, but describes that field
  only as an updated timestamp; it does not establish that the timestamp
  belongs to those outcome prices. A trade event's timestamp likewise does not
  timestamp the current quote or book.
- [REST order book](https://docs.limitless.exchange/api-reference/trading/orderbook)
  provides a YES-token book with bid and ask price levels and sizes. The book
  incorporates complementary NO orders. Level sizes use six-decimal position
  token units: divide by 1,000,000 for shares. This is displayed depth, not
  trading volume. The documented response has no quote timestamp, and the
  provider explicitly guarantees no bound on response freshness. Its
  `lastTradePrice` may represent a different time from the book levels.
- The [market-data WebSocket](https://docs.limitless.exchange/developers/websocket/market-data)
  documents full-book `orderbookUpdate` events with an event timestamp and
  version. That timestamp is not documented as belonging to a separate REST
  market-price response. The docs do not establish the maximum age of the
  initial snapshot, and versions can restart after failover. A future stream
  adapter would need to establish timestamp meaning, book identity, ordering,
  and freshness before using its price or depth; at the time of this investigation no such reader was implemented; see the current-status note above.

## Implications for the specialist

`run_betting_odds_specialist` is a legacy exact-contract quote inspector, not
the team forecasting adapter. It retains its strict event, tie, mapping, timing,
and quote-freshness checks. `verified_quote_times` is an explicit input for a
trusted adapter; the caller must establish that each time corresponds to the
same market and price observation, not merely copy a response or retrieval
timestamp. The current offline tests use this input with synthetic data only.
For compatibility its schema uses the field name `probability_up` for the
inspected payout price; that field is not an evidence-based forecast. The team
adapter does not consume it as a prediction.

The saved [market 463042 response](examples/limitless_discovery/market-463042-response.json)
is historical discovery evidence with an unknown retrieval time. Its 50–50 tie
rule and fixed forecast window also differ from the team's saved strict-up
forecast. It is not a fresh quote or evidence of usable depth.

No REST order-book retrieval path has been added: depth without a verified
quote timestamp is not sufficient to produce a live market-implied forecast.

## Forecast contract and integration

The Student 2 news and mood specialists describe a provisional forecast:
BTC at the target timestamp is strictly higher than BTC at the prediction
timestamp, using the same price source. They do not specify a provider or
price convention. The root experiment runner instead accepts dates and settles
against the next available CSV close. It excludes missing forecasts from
aggregation and scoring and reports abstentions. It neither defines an exact
24-hour interval nor automatically loads Student 2 specialists. Do not treat
those interfaces as equivalent.

The Betting Odds code depends on the Student 2 schema and import repairs in
this contribution. The earlier branch version of the schema was a wrapper
that could import a sibling checkout, and the package initializer imports
News and Mood. Integrate these repairs and Betting Odds together rather than
depending on that wrapper or a local sibling checkout.

The Betting Odds callable and fixture CLI require the prediction and target
timestamps, price source, reference-price definition and tie rule explicitly.
The current matcher recognizes only Binance BTC/USDT one-minute candle closes
and the strict-higher rule where an exact tie is down. Unsupported or
unverified definitions return `insufficient_data`; these supported values are
not a declaration that the team has agreed to them. No live market has been
verified to match the team's contract. The runner permits the repaired Student 2
evidence adapter only with valid explicit settlement metadata; legacy Betting Odds
adapters remain blocked from supplying raw prices as forecasts. Historical inputs
must be demonstrably available by their prediction times.

## Dependencies and offline commands

See [README.md](README.md) for a fresh-environment installation and required
and optional dependencies. Run the commands below from the repository root
with Python 3.11 or later and the `student2` and `student2-test` extras installed.

The following PowerShell commands run all Student 2 offline tests and then the
historical fixture demo. The temporary test directory is outside the repository.
The timestamps and source below are explicit example inputs, not team defaults.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; $student2TestTemp = Join-Path $env:TEMP ('student2-offline-' + [guid]::NewGuid().ToString('N')); python -m pytest -p no:cacheprovider --basetemp $student2TestTemp tests/test_betting_odds_specialist.py tests/test_news_specialist.py tests/test_mood_specialist.py tests/test_news_specialist_fake_headline.py tests/test_student2_adapter.py tests/test_runner_abstentions.py tests/test_student2_import_provenance.py tests/test_student2_yahoo_compat.py
python -m student2.agents.betting_odds_specialist --fixture student2/examples/limitless_discovery/market-463042-response.json --prediction-time 2026-11-07T21:51:27.932434Z --target-time 2026-11-08T21:51:27.932434Z --price-source binance:BTC/USDT --reference-price-definition 1m_candle_close --tie-definition down
```

The demo reads the saved historical response and should return a null
probability with `insufficient_data`; it makes no API or model calls.
