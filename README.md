# Bitcoin trading agents

This repository holds the team's Bitcoin analysis and paper-trading prototype. All application code lives under one Python package, `crypto_trading`. The modules can be tested independently; the final multi-agent decision and risk pipeline is not connected yet, and no live exchange orders are placed.

## Layout

```text
crypto_trading/
  agents/            Market, fundamentals, long-term, news, mood, and result schemas
    prompts/         Market and fundamentals LLM instructions
  data/              Binance Spot candles and Coin Metrics daily metrics
  features/          Technical indicator calculations
  stablecoin/        CoinGecko data, peg rules, signal, offline fixture
  ensemble/          Forecast weighting, majority vote, simple average
  execution/         BTC/USD paper trader
  replay.py          Daily experiment runner
  __main__.py        One command entry point
examples/             Example forecast(day, data) adapter
tests/                Unit tests and clearly labelled synthetic fixtures
licenses/             Retained third-party licence
```

The old `student2`, `student4`, `tradingagents`, `exchange`, `experiments`, and root `agents` directories have been consolidated. Test fixtures remain under `tests/`; generated results go to ignored `outputs/` folders.

## Install and run

Python 3.11 or newer:

```powershell
python -m pip install -e ".[test]"
python -m crypto_trading --help
python -m crypto_trading replay --demo
python -m crypto_trading stablecoin demo
python -m pytest -q
```

The replay demo uses synthetic prices and agents. Stablecoin demo snapshots and BTC/USD trades are also synthetic. Neither is a performance claim.

For current public-data reports:

```powershell
python -m crypto_trading market --symbol BTCUSDT --interval 15m
python -m crypto_trading fundamentals
python -m crypto_trading long-term --prices path/to/btc_prices.csv --as-of 2026-11-11
```

Market analysis fetches completed Binance Spot candles. Fundamentals fetches Coin Metrics Community daily Bitcoin metrics with a conservative two-day lag. Both produce evidence reports; neither places an order or currently returns the replay's probability format. The LLM interpretation functions accept an injected model callable, so no LLM provider has been chosen for those two agents.

The Long-term agent reads historical `date,close` BTC prices, refits `log10(price) = a + b × log10(days since 2009-01-03)` using only completed prices through `--as-of`, and reports price relative to the fitted trend plus the trend one year ahead. It accepts BTC/USDT by default; use `--quote USD` for BTC/USD. USDT prices are only a proxy for the USD prices used in the cited study. The implementation follows the model family in [Santostasi and Perrenod (2026)](https://www.sciencedirect.com/science/article/pii/S3050517826000675), while estimating its own coefficients at every cutoff to avoid historical lookahead. It is descriptive context, not a probability that Bitcoin rises tomorrow; [separate walk-forward research](https://arxiv.org/abs/2605.21316) also finds that a power-law trend is more useful at long horizons than short ones. At least 365 observations spanning two years are required. The default cutoff is yesterday's completed UTC day.

For a historical replay, provide `btc_prices.csv` with `date,close` and a folder of modules implementing `forecast(day, data)` as shown in `examples/forecast_agent.py`:

```powershell
python -m crypto_trading replay --start 2025-01-01 --end 2025-03-31 --data-dir path/to/data --agents-dir path/to/agents
```

The runner gives each agent only CSV rows dated on or before the forecast day and stops on a malformed forecast. It does not expose a raw news directory, which could contain later stories. The three forecast combinations are exchange-weighted average, majority vote, and simple average. Exchange coins use Brier-score improvement over a neutral forecast; coins are experimental weights, not trading returns. Real historical, timestamped data is still needed before these comparisons are meaningful.

## News, mood, and stablecoins

The only current live credentials are `GOOGLE_API_KEY` (or `GEMINI_API_KEY`) for the News Specialist and `COINGECKO_DEMO_API_KEY` for CoinGecko. See `.env.example` for the exact names. The application reads operating-system environment variables and does **not** automatically load a `.env` file. For example, in PowerShell: `$env:GOOGLE_API_KEY = "your-key"`. Keep actual keys out of Git. Binance Spot candles, Coin Metrics Community metrics, and Alternative.me Fear & Greed use public endpoints in the present code. Market and fundamentals LLM providers have not been selected, so there are no provider keys for those steps yet.

The News Specialist filters articles by publication time and validates cited evidence IDs. Its live Gemini call requires `GOOGLE_API_KEY` or `GEMINI_API_KEY` and installation of `.[news-live]`; offline tests inject a model response. The Mood Specialist filters Alternative.me Fear & Greed readings by forecast time. With no calibrated forecast model, it returns a labelled neutral placeholder rather than converting the index score into a price probability. Historical replay must use readings available at each decision time.

The stablecoin collector uses CoinGecko and requires `COINGECKO_DEMO_API_KEY` for live collection. `python -m crypto_trading stablecoin collect --help` shows its options. Its BUY/SELL/HOLD signal is separate from the probability-based replay, and the paper trader is a simulator. A risk gateway and final decision layer must approve any eventual trade.

## Remaining integration

- Choose a common forecast horizon and adapter for the descriptive market/fundamentals/long-term reports and the stablecoin action signal.
- Provide archived point-in-time prices, news, sentiment, and on-chain data for replay; compare against simple baselines.
- Connect the debate and decision stages, position sizing, risk limits, circuit breaker, and persistent paper-trading state.
- Run a forward paper-trading trial before considering live execution.

The peg helper adapts the MIT-licensed [Pharos project](https://github.com/TokenBrice/pharos-watch); its licence remains in `licenses/pharos-MIT.txt`. See `LICENSE` for this repository's licence.
