# Student 1: Bitcoin price data, Chart agent, Long-term agent

This is a self-contained copy of the Student 1 work. It covers BTC/USDT Spot
data. Price collection is the shared data component; Chart and Long-term are
the two analysis agents. Neither places trades.

## Run

From this folder with Python 3.11 or later:

```powershell
$env:PYTHONPATH = "src"
python -m crypto_trading.price_data_main
python -m crypto_trading.chart_main
python -m crypto_trading.long_term_main
```

The price-data command downloads completed UTC daily BTC/USDT candles from
Binance's public Spot API into `outputs/btcusdt_daily.csv`. It paginates through
history and checks for gaps. No Binance account or API key is required. For a
smaller date range:

```powershell
python -m crypto_trading.price_data_main --start 2020-01-01 --end 2024-12-31
```

The Chart agent reads recent completed 15-minute candles and reports EMA,
MACD, RSI, ATR, volume, and taker-buy context as JSON. To analyze archived
daily candles instead:

```powershell
python -m crypto_trading.chart_main --interval 1d --history outputs/btcusdt_daily.csv
```

The Long-term agent fits an exploratory log-price versus log-time line to the
archived daily prices. It needs at least 365 observations across two years. It
reports distance from the fitted line, not a next-day trading prediction. A
historical cutoff excludes later prices from the fit:

```powershell
python -m crypto_trading.long_term_main --as-of 2024-12-31
```

The Chart agent reuses the existing market indicator calculations and has an
optional model-independent prompt/response validator. No LLM provider is
connected. The Long-term agent does not use an LLM.

## Test

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```
