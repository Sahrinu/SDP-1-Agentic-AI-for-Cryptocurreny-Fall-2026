# Student 2: News, Mood and Betting Odds

The Student 2 packages are distributed with Student 4. Their runtime
dependencies are optional so an existing Student 4 installation does not
acquire them. Install from the repository root with Python 3.11 or newer:

```powershell
python -m venv .venv-student2
.\.venv-student2\Scripts\python.exe -m pip install ".[student2,student2-test]"
```

The `student2` extra supplies Pydantic 2, Google GenAI, LangChain Core,
python-dotenv, yfinance and the Windows timezone data needed for News, Mood
and Betting Odds. Importing `student2.agents` currently imports News and Mood,
so even a Betting Odds import needs these runtime dependencies. The
`student2-test` extra supplies pytest. `student2-live` supplies the Socket.IO
client for read-only live book capture and is optional for offline specialists.
Other optional extras are `student2-google`
for the alternative Google LangChain client, `student2-anthropic` for
Anthropic, `student2-openai` for OpenAI and Azure, and `student2-aws` for
Bedrock. These optional clients are not needed by the Student 2 offline suite.
For example, add `student2-google` to the install extras only if using that
fallback. No API credentials are needed for the commands below.

The project metadata retains Python 3.10 compatibility for Student 4; Student 2
requires Python 3.11 or newer because its schema and Yahoo helper use newer
standard-library features.

For a fresh temporary environment, build and install the wheel, verify installed
imports and packaged helpers, and run the full offline suite from the repository
root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\student2\verification\verify_fresh_environment.ps1
```

The installed import checks use isolated Python. The test suite then runs from
the checkout, as its provenance checks and fixtures require repository paths.
This verification installs `student2`, `student2-test` and `student2-live`;
it makes no model or market API calls.

Alternatively, run all Student 2 offline tests in the environment above:

```powershell
$student2TestTemp = Join-Path $env:TEMP ('student2-offline-' + [guid]::NewGuid().ToString('N'))
.\.venv-student2\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp $student2TestTemp `
  tests/test_betting_odds_specialist.py tests/test_news_specialist.py tests/test_mood_specialist.py `
  tests/test_news_specialist_fake_headline.py tests/test_student2_adapter.py tests/test_runner_abstentions.py `
  tests/test_student2_import_provenance.py tests/test_student2_yahoo_compat.py `
  tests/test_betting_evidence_forecast.py tests/test_binance_json_transport.py tests/test_binance_raw_transport.py `
  tests/test_limitless_live_reader.py tests/test_limitless_powershell_collector.py tests/test_limitless_saved_listing.py `
  tests/test_limitless_targeted_discovery.py tests/test_market_evidence_specialist.py tests/test_market_price_context.py
```

Run the saved historical Betting Odds fixture without a network call:

```powershell
.\.venv-student2\Scripts\python.exe -m student2.agents.betting_odds_specialist --fixture student2/examples/limitless_discovery/market-463042-response.json --prediction-time 2026-11-07T21:51:27.932434Z --target-time 2026-11-08T21:51:27.932434Z --price-source binance:BTC/USDT --reference-price-definition 1m_candle_close --tie-definition down
```

The fixture command must report `insufficient_data` with a null probability.
The specified forecast inputs are examples, not agreed team defaults. The
Betting Odds adapter uses a timestamped book and explicitly supplied model to
produce an experimental evidence-based forecast or abstain. Team use still needs
verified forecast timestamps, a reference-price provider and convention; REST
retrieval time does not establish quote freshness. See
[BETTING_ODDS_DATA.md](BETTING_ODDS_DATA.md) for the precise limitations.
The team runner retains its date-based settlement contract. It now excludes
unavailable forecasts from aggregation and scoring rather than substituting
0.5; a genuine 0.5 forecast remains available. The repaired Betting Odds adapter
is admitted only after the runner's explicit window check; legacy Betting Odds
adapters remain blocked. See [INTEGRATION.md](INTEGRATION.md).
