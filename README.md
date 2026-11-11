# Student 2 — News and Mood Specialists
This repo contains the contribution package for the Bitcoin News Specialist and Mood Specialist, prepared in a self-contained form without depending on the original TradingAgents project.

## Included

- `student2/agents/news_specialist.py` — filtered evidence-based Bitcoin forecast logic.
- `student2/agents/mood_specialist.py` — Alternative.me Fear & Greed sentiment signal.
- `student2/agents/schemas.py` — shared structured result schema used by both specialists.
- `student2/compat/yahoo_compat.py` — minimal compatibility helpers for article extraction.
- `tests/test_news_specialist.py` — mock validation for filtering and prompt neutrality.
- `tests/test_mood_specialist.py` — mock validation for sentiment output.
- `tests/fixtures/news_specialist_fake_headline/` — synthetic-article experiment fixtures and manifest.

## Run tests

```powershell
cd "C:\Users\Fatema Tariq\Downloads\SDP-1-Agentic-AI-for-Cryptocurreny-Fall-2026"
python -m pytest tests/test_news_specialist.py tests/test_mood_specialist.py -q
```

## Notes

- Synthetic provenance stays in the experiment manifest only.
- The neutral experimental prompt wording preserves the filtered article set without exposing the synthetic label.
- No `.env`, API keys, or secret material are included in this package.

## License

This contribution retains the Apache 2.0 license notice used by the reused project codebase.
# Agentic AI for Cryptocurrency — Student 4

Student 4's weekly deliverables: **stablecoin data, Stablecoin agent, and paper trading**.
This branch provides independent Python components for the team's Bitcoin market engine.
Requires Python 3.10 or newer; no runtime dependencies or installation needed.

## Run the offline demo

From the repository root:

```bash
python3 -m student4 demo
```

On Windows, use `py -m student4 demo`. All stablecoin data and BTC prices in this
demo are **synthetic**, replayed against a fixed historical clock. It starts with
$10,000 of simulated USD cash, charges 0.1% per trade and 5 basis points of adverse
slippage, and shows:

1. HOLD: stable pegs, no earlier snapshot.
2. BUY: stablecoin market capitalisation increases 2% over 24 hours.
3. HOLD: USDC reaches $0.99, triggering the peg warning.
4. SELL: USDC reaches $0.97, triggering the peg stress rule.

Outputs go to `outputs/demo/`: four saved snapshots, `signals.json`, `trades.csv`,
and `portfolio.json`. Use `--output PATH` to select another directory. Repeating
a demo overwrites its output files and starts a new account.

## Live stablecoin data

The collector uses CoinGecko's
[`/simple/price` endpoint](https://docs.coingecko.com/demo/reference/simple-price)
for USDT (`tether`), USDC (`usd-coin`), and DAI (`dai`). It collects USD prices,
market capitalisation, 24-hour volume, and each quote's provider timestamp in
one request. Set a CoinGecko **Demo API key** in your environment:

```bash
export COINGECKO_DEMO_API_KEY='your-demo-key'
python3 -m student4 collect --output outputs/baseline.json
```

PowerShell equivalent: `$env:COINGECKO_DEMO_API_KEY = 'your-demo-key'`.
Later, collect a second snapshot and compare it with the first:

```bash
python3 -m student4 collect --previous outputs/baseline.json --output outputs/current.json
```

`collect` saves the snapshot and prints the agent decision as JSON. It does not
execute trades. Keys are sent in the request header. The client has a 15-second
timeout and reports API, authentication, rate-limit, incomplete-data, and stale-data
errors. It never substitutes demo data for a live failure. No automatic retry loop
is used; retry later if rate limited. The demo and tests need no API key.

## Stablecoin agent rules

The agent produces a **BTC market signal**, rather than buying or selling stablecoins.
Its rules run in priority order:

| Condition | Action | Score |
| --- | --- | --- |
| Unusable, incomplete, or stale current data | HOLD | 0 |
| Any tracked coin deviates at least 2% above or below $1 | SELL | -1 |
| Any tracked coin deviates at least 0.5%, but less than 2% | HOLD | 0 |
| Stable pegs and aggregate market cap grows at least 1% from the baseline | BUY | +1 |
| Stable pegs and aggregate market cap falls at least 1% from the baseline | SELL | -1 |
| Smaller change, absent baseline, or invalid baseline | HOLD | 0 |

Thresholds are configurable through `StablecoinAgent(...)`. Quotes and snapshot
timestamps may be at most 15 minutes old and at most 60 seconds ahead of the
decision clock. The baseline must contain the same coins, have usable quotes at
its own observation time, come from the same source, and precede the current
snapshot. Each current provider quote must also be newer than its baseline quote.
The reported comparison interval is the actual elapsed time; it is not assumed
to be 24 hours. Do not compare intervals interchangeably when evaluating the rules.

Stablecoin market cap is a **liquidity proxy**. It does not directly measure exchange
inflows, circulating issuance, or Bitcoin demand; price changes can also move market
cap. These are transparent classroom heuristics without a trained model, LLM,
calibrated confidence, or validated predictive performance. A single-provider peg
stress signal is not a confirmed multi-source depeg incident.

## Integration for Student 3

```python
from student4 import CoinGeckoClient, StablecoinAgent, StablecoinSnapshot, PaperTrader

current = CoinGeckoClient().fetch()
previous = StablecoinSnapshot.load("outputs/baseline.json")  # optional historical baseline
decision = StablecoinAgent().analyze(current, previous)

# Pass decision.action to majority vote, or decision.score to simple average.
print(decision.to_dict())
```

Every decision contains `agent`, `action`, `score`, `reason`, and `metrics`.
Scores use **-1 = SELL, 0 = HOLD, +1 = BUY**. The market engine should aggregate
this signal with the other agents before paper execution. Catch `DataError` if
collection fails; record that agent as unavailable or HOLD according to the
team's policy. API failures must not result in stale signals being reused.

The paper trader accepts the final ensemble action and the BTC/USD price from
Student 1's data feed:

```python
trader = PaperTrader(initial_cash="10000", fee_rate="0.001", slippage_bps="5")

# Example inputs from the team's engine and price feed:
final_action = "BUY"
btc_price_usd = "60000"
approved_cash_fraction = "0.10"

trader.execute(final_action, btc_price_usd, buy_fraction=approved_cash_fraction)
print(trader.summary(btc_price_usd))
trader.export("outputs/paper", btc_price_usd)
```

Keep the same `trader` instance across market cycles. BUY spends the supplied
fraction of available USD cash **including fees**, rounded down to whole satoshis;
its default is all available cash. Repeated BUY calls may add to a position.
SELL closes the whole BTC position; SELL with no holdings and HOLD create no
trade. No borrowing, short selling, leverage, or exchange orders are supported.
Cash is simulated fiat USD, so stablecoin peg changes do not revalue the cash balance.

Balances and accounting use `Decimal`. Buy fees enter cost basis; sell fees
reduce realised profit. Equity marks BTC to the supplied price and excludes
hypothetical future liquidation costs. CSV trade records include execution
prices, fees, quantities, balances, and realised profit or loss. The JSON report
includes equity, realised/unrealised/total P&L, return, and total fees.
Reports are exports; automatic account restoration after a process restart is
not implemented. Supply UTC-aware chronological timestamps for historical replay;
live calls default to the current UTC time.

Student 5's **25% risk rule** should approve position sizing before calling this
component. `buy_fraction` is a cash allocation, not a maximum-loss or total-portfolio
exposure rule. The risk engine remains responsible for enforcing the team's
precise policy. The component demo feeds its own agent directly into the simulator
only to demonstrate Student 4's work independently.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Alternatively, run `python3 -m pytest -q` if pytest is installed. Tests exercise
data validation, mocked API success/failures, exact peg boundaries, stale data,
baseline comparisons, order validation, fee/slippage accounting, P&L reconciliation,
exports, and the complete offline CLI demo. Live API authentication is not part
of the offline test suite.

## Files and open-source attribution

| File | Responsibility |
| --- | --- |
| `student4/data.py` | Stablecoin collection, validation, and snapshot JSON |
| `student4/peg.py` | Signed, unrounded peg deviation in basis points |
| `student4/agent.py` | Explained BUY/SELL/HOLD signal |
| `student4/paper_trading.py` | Simulated BTC/USD account and trade/report exports |
| `student4/__main__.py` | Offline demo and live collection commands |
| `student4/fixtures/demo.json` | Clearly labelled synthetic demo scenario |
| `tests/test_student4.py` | Offline verification |

The peg helper adapts `relativeBps` / `deriveDepegSignal` from the MIT-licensed
[Pharos stablecoin risk project](https://github.com/TokenBrice/pharos-watch), pinned
to [revision 8d554949ff7498e44d0f1a1e275d677fdbffd7b5](https://github.com/TokenBrice/pharos-watch/blob/8d554949ff7498e44d0f1a1e275d677fdbffd7b5/shared/lib/depeg-signals.ts).
The Python adaptation uses decimal arithmetic and retains raw deviation and
direction. Its licence is preserved in [licenses/pharos-MIT.txt](licenses/pharos-MIT.txt).
Pharos's larger monitoring pipeline, thresholds, and confirmation logic are not
included; our agent's thresholds are project-specific.

[Curvemetrics](https://github.com/xenophonlabs/curve-lp-metrics) was also reviewed.
Its Curve-pool metrics, database, and Bayesian change-point models would require
additional infrastructure beyond this week's module, so no code was imported.
CoinGecko supplies live data through its documented API; no CoinGecko code is vendored.

