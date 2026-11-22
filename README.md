# Bitcoin Agent Exchange

A Python research prototype that combines 13 Bitcoin specialist forecasts using
an Agent Exchange, majority voting, and a simple average. A local dashboard shows
market data, agent evidence, forecast probabilities, and saved results.

This repository contains the implementation. Research methodology, evaluation,
and conclusions belong in the project report. No component places real exchange orders.

## Quick start

Requires **Python 3.11+**. Run these commands from the repository root:

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1

python -m pip install -e ".[specialists,live,macro,test]"
```

Copy [`.env.example`](.env.example) to `.env`, then fill in the services you use:

```bash
# macOS / Linux
cp .env.example .env
# Windows PowerShell: Copy-Item .env.example .env
```

Start the dashboard:

```bash
python -m dashboard
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). The server is local-only.
Use `python -m dashboard --port 8766` if the default port is already in use.

## API configuration

| Variable | Used for |
| --- | --- |
| `GOOGLE_API_KEY` | Gemini-backed News and Mood agents, plus the debate comparison |
| `COINGECKO_DEMO_API_KEY` | Live stablecoin data and price history |
| `SOSOVALUE_API_KEY` | Refreshing US spot Bitcoin ETF net flows |
| `ANTHROPIC_API_KEY` | Optional Claude model-comparison experiments |
| `OPENAI_API_KEY` | Optional OpenAI model-comparison experiments |

`GEMINI_API_KEY` is an alternative to `GOOGLE_API_KEY` for the dashboard and demo
client. Optional model overrides are `GEMINI_MODEL`, `CLAUDE_MODEL`, and
`OPENAI_MODEL`. The dashboard uses Gemini; adding Claude/OpenAI keys enables the
separate experiments, not a dashboard provider switch.

Keep real credentials in your local `.env` or environment variables. `.env` is
ignored by Git. Restart the dashboard after changing keys. Model calls can incur
provider charges when you explicitly generate a forecast or run an experiment.

## Using the dashboard

1. **Refresh market data** to retrieve the current inputs.
2. Inspect **Connected sources** for unavailable or stale data.
3. Select **Generate 24h forecast** to run the specialists and combine their results.
4. Inspect each agent's evidence and the decision-room execution timeline.
5. Optionally use **Compare Bull / Bear / Judge** for a separate model-based comparison.

An agent abstains when its required data or model response is unusable. A working
API key does not guarantee that all 13 agents can contribute on every run.

Forecasts target whether Binance BTCUSDT will be strictly higher 24 hours after
the decision; ties count as down. Due forecasts settle on the next data refresh,
forecast run, or explicit settlement action. Agent coins track forecast influence
and are separate from cash or BTC balances.

## Data connections

| Source | Inputs |
| --- | --- |
| Binance | BTC candles, reference prices, funding, and open interest |
| Coin Metrics community API | Network and miner metrics |
| Yahoo | Bitcoin news, dollar index, and Treasury-yield proxies |
| Alternative.me | Fear & Greed readings |
| CoinGecko, DefiLlama, Coinbase | Stablecoin and liquidity inputs |
| SoSoValue + reviewed CSV | US spot Bitcoin ETF flows |
| Public Kalshi trades | Fresh prediction-market evidence |

ETF refresh falls back to `data/etf_flows.csv` if the API is unavailable. Some
stablecoin comparisons need observations approximately 24 hours apart. Prediction
market participation requires a recent qualifying trade. Collection is triggered
by the dashboard or CLI; there is no n8n integration.

## Model-comparison experiments

Re-run the News agent on the same saved forecast with different providers:

```bash
python -m experiments.live_attack outputs/dashboard/forecasts/<id>.json --attack all --repeats 2 --provider gemini
python -m experiments.live_attack outputs/dashboard/forecasts/<id>.json --attack all --repeats 2 --provider claude
python -m experiments.live_attack outputs/dashboard/forecasts/<id>.json --attack all --repeats 2 --provider openai
python -m experiments.model_comparison
```

These commands make model calls. Attack runs are saved in `outputs/live_attacks/`;
the comparison table is written to `outputs/model_comparison/`. Use matching
forecast and attack sets when comparing providers.

## Code layout

| Directory | Purpose |
| --- | --- |
| `dashboard/` | Local HTTP server and browser interface |
| `agents/` | Specialist catalog, adapters, and forecast logic |
| `market_data/` | Data collection, validation, and source adapters |
| `model_clients/` | Provider clients and structured-response parsing |
| `exchange/` | Forecast aggregation, agent coins, and settlement ledger |
| `risk/` | Forecast auditing and risk controls |
| `experiments/` | Replays, attack tests, metrics, and result generation |
| `tests/` | Offline tests and labeled fixtures |
| `data/` | Reviewed inputs and cached datasets |
| `compat/` | Forwarding modules for older import paths |

Dashboard forecasts and captured inputs are saved under `outputs/dashboard/`.
Generated `outputs/` files are ignored by Git. Historical examples remain in
[`examples/reference_runs/`](examples/reference_runs/); they are saved evidence,
not current forecasts. Source-specific caveats are in
[`docs/student2-evidence-notes.txt`](docs/student2-evidence-notes.txt).

## Tests

```bash
python -m pytest -q
# List the registered specialists without calling provider APIs:
python -m agents
```

Offline tests validate code behavior, not live credentials or forecast accuracy.
Synthetic fixtures and replays must remain labeled as synthetic.

## Attribution

Keep [`LICENSE`](LICENSE) and [`licenses/pharos-MIT.txt`](licenses/pharos-MIT.txt).
The stablecoin peg helper adapts calculations from
[Pharos](https://github.com/TokenBrice/pharos-watch); its license is retained.
