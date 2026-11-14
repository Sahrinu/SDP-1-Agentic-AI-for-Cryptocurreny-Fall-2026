# Student 5 implementation and integration

This contribution targets the current GitHub `main` layout and its
`agents/template.py` contract. It adds macro inputs, the World economy forecast,
a 25% BTC exposure gateway and an editable [Gantt template](gantt.md). It is a
research prototype with transparent assumptions, not validated trading advice.

## Files and commands

| File | Purpose |
| --- | --- |
| `agents/world_econompy.py` | Automatically discovered `forecast(day, data)` agent; requested filename retained |
| `macro/collect.py` | Yahoo download, source snapshot and daily alignment |
| `risk/gateway.py` | Exposure approval and guarded Student 4 execution |
| `examples/student5_demo.py` | Fully offline demonstration with synthetic inputs |
| `tests/test_student5_macro.py` | Availability, weekend, stale-data and runner checks |
| `tests/test_student5_gateway.py` | Cost-aware risk tests through the actual paper trader |

From the repository root:

```bash
python -m pip install -r requirements-student5.txt
python -m pytest tests/test_student5_macro.py tests/test_student5_gateway.py -q
python examples/student5_demo.py
python -m macro.collect --start 2026-08-01 --end 2026-11-13
```

The demo saves `outputs/student5_demo.json` and makes no network requests.
Its input is clearly synthetic. The live collector needs internet access and no
API key. Yahoo can reject/rate-limit requests; a failed download raises an error
and never creates synthetic replacement data. Do not mistake the offline demo
for a successful provider download.

## Macro source choice and timing

Use **Yahoo Finance daily `DX-Y.NYB`** (US Dollar Index, DXY) and **`^TNX`**
(10-year Treasury yield). DXY is the indicator; Yahoo is its data provider.
Treasury yield is a market-based interest-rate proxy, not the Federal Reserve's
policy rate. Values are used in Yahoo's quoted percent units (for example, 4.2
means 4.2%, not 42%); yield differences are percentage points.

The choice favors daily market observations over economic releases. The Federal
Reserve's H.10 dollar indexes publish the previous business week's observations
on Monday at 4:15 p.m., subject to holidays. They are not zero-lag inputs merely
because their observations have daily dates. FRED/ALFRED offers historical
vintages and would be a better choice for a publication-vintage study, but it
does not remove the underlying series' release delay.

**No zero-lag guarantee is made.** Yahoo quote delays and daily history availability
vary. The historical collector assumes a completed session dated D is available
at **00:00 UTC on D+1**. The forecast for `day=D` runs at that same completed-day
boundary, using BTC's D close to predict D+1 versus D, matching the existing
runner. Intraday/live decisions cannot use the same-day daily close. The
collector rejects today's still-incomplete UTC date.

The midnight convention is an explicit research approximation, not a Yahoo
publication SLA. Raw archives include retrieval time and an availability-basis
label. Historical data downloaded today may include later corrections; these
timestamps are not proof that the values were available at an old decision time.
For strict point-in-time evaluation, archive each response as it arrives in
forward collection or use independently verified vintage/publication timestamps.
The agent respects supplied later `*_available_at` timestamps when provided.

## Forward filling and forecast rule

The collector requests 30 warmup calendar days and writes `data/macro.csv` with:

- `date`, `dxy`, `treasury_10y`;
- each series' `*_observed_on`, `*_available_at`, and `*_forward_filled`;
- `source` and `availability_basis`.

It carries the last available observation through weekends and holidays, never
backfills from the future and retains the original source date. Carrying stops
after **four calendar days**; longer gaps remain missing. No Friday observation
is relabelled as a new Saturday measurement. The collector saves raw downloaded
closes under `data/macro_raw/`; generated/provider data is ignored by Git.

The agent uses the changes across **five distinct source sessions** (six
observations), not five forward-filled calendar rows. With dollar change in
percent and yield change in percentage points:

```text
pressure = 0.6 * (dollar_change / 1.0) + 0.4 * (yield_change / 0.25)
p_up = 0.5 - 0.1 * tanh(pressure)
```

This assumes a stronger dollar and rising yields are headwinds. Coefficients and
the 0.40–0.60 range are deliberately modest, **uncalibrated heuristic choices**;
their predictive value must be tested, not asserted. Missing, stale, invalid or
short-history input produces the template's neutral `p_up=0.5` and
`data_missing=True`. Evidence includes the source dates and changes.

The collector warmup helps filling, but a replay beginning on 1 September must
load macro rows starting earlier (for example, use `--start 2026-08-01`). The
agent needs six distinct sessions present in the CSV, not just in the raw archive.

The existing runner automatically discovers the file and loads `macro.csv`:

```bash
python experiments/run.py --start 2026-09-01 --end 2026-10-31 \
    --data-dir data --agents-dir agents --out outputs/student5_replay
```

Also supply Student 1's real `data/btc_prices.csv` and the other team's agents.
Only the Student 5 agent is added here. A one-agent run cannot demonstrate the
Exchange's 20% influence cap. The existing runner logs probabilities but discards
the evidence list; the Student 5 demo saves full evidence. The team runner still
needs a separate enhancement for full per-day evidence persistence.

## The 25% risk rule

The invariant at each checked quote is:

```text
BTC quantity * quote <= 0.25 * (cash + BTC quantity * quote)
```

The gateway sizes each new BUY using current cash, existing BTC, the execution
fee and adverse slippage. Costs reduce post-trade equity, so the calculation
includes their effect on the denominator. Quantity and the approved cash fraction
round down. Repeated BUY requests cannot bypass the limit.

Use the guarded wrapper in the final decision stage:

```python
from risk.gateway import execute_guarded
from student4.paper_trading import PaperTrader

trader = PaperTrader(initial_cash="10000", fee_rate="0.001", slippage_bps="5")
# Keep the SAME trader across cycles. final_action comes from the team's
# forecast-to-position policy; no probability threshold is imposed here.
final_action = "BUY"
decision, trade = execute_guarded(trader, final_action, "60000",
                                  requested_buy_fraction="1")
print(decision.to_dict())
```

The wrapper reads the simulator's actual costs and balances and executes at the
same supplied quote. A capped/dust BUY becomes HOLD. SELL closes the position.
If price appreciation has already pushed BTC above 25%, even a HOLD or BUY is
changed to a full SELL. Full liquidation is necessary with Student 4's current
all-or-nothing SELL API; partial rebalancing is not implemented in this change.

The cap is checked when this wrapper runs; price moves between checks can cause
temporary breaches. Direct calls to `trader.execute()` bypass this gateway.
The replay runner currently has no execution stage, so this change supplies and
tests the integration function rather than pretending the team pipeline is
already connected. No orders reach a real exchange.

## References

- [Yahoo data providers and delays](https://help.yahoo.com/kb/finance/article-exchanges-data-delays-sln2310.html)
- [Yahoo DXY daily history](https://finance.yahoo.com/quote/DX-Y.NYB/history/)
- [Yahoo Treasury yield](https://finance.yahoo.com/quote/%5ETNX/)
- [yfinance download parameters](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html)
- [Federal Reserve H.10 release schedule](https://www.federalreserve.gov/releases/h10/)
- [FRED real-time periods](https://fred.stlouisfed.org/docs/api/fred/realtime_period.html)

## Review and contribution record

Validation in the prepared workspace:

- Student 5, Exchange engine and Student 4 targeted tests: **64 passed,
  61 subtests passed**.
- Offline synthetic demo: completed, including weekend carry, a capped BUY,
  a blocked repeat BUY and overweight liquidation.
- Live Yahoo smoke check: both series returned **105 usable daily rows** for
  1 August–13 November 2026; source snapshots were saved locally. This tests
  download/format compatibility, not forecast accuracy or historical vintages.
- Full `pytest -q`: stops at three existing News/Mood test collection errors,
  all importing missing `tradingagents.agents.schemas`. That module is absent
  from the baseline `main` tree. Student 5 does not modify those modules.

These files are prepared locally against main commit
`c86071215c3803647b9977f9979ad974c9ae8954`; no commit/push is part of preparation.
Record your own commit URL, review date and test results once approved. The
Contrarian agent, circuit breaker and full security study remain separate work.
