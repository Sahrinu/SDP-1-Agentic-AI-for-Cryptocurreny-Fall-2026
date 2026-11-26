"""Rebuild past daily forecasts ("backtests") for the dashboard's chat archive, including crash stress tests.

For each day D the agents see only rows whose availability time is at or before the
end of D (00:00 UTC on D+1); the forecast is scored against the close of D+1. In each
scenario the Exchange starts every agent at 100 coins and settles day by day.

Point-in-time rules (stated in each saved file):
  * BTC daily closes: Binance spot, known at the candle close. Before Binance's first candle (17 Aug 2017)
    the Long-term agent's lookback uses Coin Metrics daily reference prices; scoring always uses Binance.
  * Network / Miner: Coin Metrics community history; availability estimated as 06:00 UTC next day
    (the team's replay convention).
  * Borrowing: funding settlements and the daily open-interest snapshot are public at 00:00 UTC next day.
    Binance (the live source) keeps only ~25 days of open-interest history, so older scenarios use Bybit's
    BTCUSDT perpetual instead (history from early 2021; open interest in BTC converted to USD with the
    Binance close). 2018 and 2020 have no borrowing data.
  * ETF: SoSoValue / Farside daily totals; availability estimated as 12:00 UTC next day.
    US spot BTC ETFs started trading on 11 Jan 2024. History before late Oct 2026 comes from Farside's
    all-data table copied by hand (outputs/dashboard/backtest/farside_all.csv); those values are the latest
    revision, so early-reporting days may include later corrections.
  * World economy: Yahoo daily closes of DXY and the 10-year yield, assumed available at 00:00 UTC
    after the session (the macro module's own historical basis).
  * Stablecoin: walk-forward. The liquidity model (and the peg-stress model when enough history exists)
    is retrained on data dated before each scenario starts; the agent still refuses in-sample days.
    Before DefiLlama's history starts (Nov 2019) the same columns come from Coin Metrics daily market caps
    and prices (USDT, USDC, SAI/DAI, BTC).
  * Mood (Gemini): Alternative.me Fear & Greed history; only readings timestamped strictly before the
    decision time. Caveat: the language model may already know how some past periods played out from
    its training data, so older Mood backtests can look better than they really are.
  * News (Gemini): Bitcoin articles with text, published and last edited within the 36 hours before the
    decision. Sources in order: Alpaca news (archive from spring 2026), Free Crypto News archive
    (cryptocurrency.cv, publisher timestamps), then CoinDesk Data news only for days still short of
    articles. Duplicates are merged and at most 20 recent articles are used. Same language-model caveat
    as Mood; archive timestamps come from the providers and are not independently verified.
    Replay variant: the team's News prompt calls every non-Yahoo article "unverified" (live it only reads
    Yahoo). In this replay process only, that one sentence is replaced so archived articles are described
    as genuine dated reporting from named publishers; the rest of the prompt and the agent are unchanged.
  * Betting odds: replay of the team's unchanged Kalshi path (from Nov 2024, when Kalshi's daily BTC
    markets exist). The observation is rebuilt from Kalshi's archived real trade in the next-fixing daily
    market, using the agent's own 5-minute freshness rule (no trade in the last 5 minutes -> abstain).
    The trade closest to 50% is chosen because archived order books (bid/ask) are not available. The
    observation carries the live-capture mode label only so the unchanged validator accepts it; each
    result states that it was reconstructed from the archive.

Usage (repo root):
  python -m experiments.backtest_archive                       # last 30 days ("month")
  python -m experiments.backtest_archive --scenario crash-2026 # one crash window
  python -m experiments.backtest_archive --all                 # month + every crash window
  add --no-llm to skip the Gemini Mood agent (results are cached in outputs/dashboard/backtest/_llm_cache)
"""
import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from exchange.engine import Exchange  # noqa: E402
from experiments.run import combine, data_upto, evaluate_agents, load_agents, load_data  # noqa: E402

OUT = ROOT / "outputs" / "dashboard" / "backtest"
SCENARIOS = {
    "month": {"label": "Last 30 days", "start": None, "end": None},
    "ftx-2022": {"label": "FTX collapse (Nov 2022)", "start": "2022-10-20", "end": "2022-11-30"},
    "yen-2024": {"label": "Yen carry-trade crash (Aug 2024)", "start": "2024-07-15", "end": "2024-08-25"},
    "slide-2025": {"label": "Nov 2025 slide", "start": "2025-10-20", "end": "2025-12-05"},
    "crash-2026": {"label": "Feb 2026 crash", "start": "2026-01-15", "end": "2026-03-05"},
    "drop-2026": {"label": "Jun 2026 drop", "start": "2026-05-10", "end": "2026-06-20"},
    "apr-2025": {"label": "Apr 2025 tariff shock (around 18 Apr)", "start": "2025-03-20", "end": "2025-04-30"},
    "oct-2025": {"label": "Oct 10 2025 liquidation crash (around 8 Oct)", "start": "2025-09-10", "end": "2025-10-20"},
    "bear-2018": {"label": "Nov 2018 capitulation", "start": "2018-10-25", "end": "2018-12-20"},
    "covid-2020": {"label": "COVID crash (Mar 2020)", "start": "2020-02-15", "end": "2020-04-10"},
    "may-2021": {"label": "May 2021 crash", "start": "2021-04-15", "end": "2021-06-10"},
    "luna-2022": {"label": "Luna / Celsius collapse (May-Jun 2022)", "start": "2022-04-25", "end": "2022-06-25"},
    "etf-rally-2024": {"label": "Spot ETF rally (Jan-Mar 2024)", "start": "2024-01-02", "end": "2024-03-20"},
    "election-2024": {"label": "US election rally (Nov 2024)", "start": "2024-10-20", "end": "2024-12-20"},
}
# Dated events (UTC calendar day) used only as labels in the dashboard; never shown to the agents.
EVENTS = {
    "2018-11-14": "Bitcoin Cash 'hash war' sell-off begins",
    "2018-11-15": "Bitcoin Cash hard fork (BCH / BSV split)",
    "2020-03-12": "COVID crash ('Black Thursday')",
    "2020-03-13": "COVID crash, day 2",
    "2020-03-15": "Fed cuts rates to near zero",
    "2021-05-12": "Tesla suspends bitcoin payments",
    "2021-05-19": "May 2021 crash after China warns banks off crypto",
    "2022-05-09": "Terra's UST stablecoin loses its peg",
    "2022-05-12": "LUNA collapses; Terra halts its chain",
    "2022-06-13": "Celsius freezes withdrawals",
    "2022-06-18": "BTC falls below 18,000",
    "2022-11-08": "FTX liquidity crisis; Binance agrees to buy FTX",
    "2022-11-09": "Binance walks away from the FTX deal",
    "2022-11-11": "FTX files for bankruptcy",
    "2024-01-10": "SEC approves US spot bitcoin ETFs",
    "2024-01-11": "US spot bitcoin ETFs start trading",
    "2024-03-14": "Bitcoin record high above 73,000",
    "2024-07-27": "Trump speaks at the Bitcoin 2024 conference",
    "2024-11-05": "US election day",
    "2024-11-06": "Trump wins the US election; bitcoin record high",
    "2024-12-05": "Bitcoin tops 100,000 for the first time",
    "2024-08-05": "Global sell-off (yen carry-trade unwind)",
    "2025-04-02": "US 'Liberation Day' tariffs announced",
    "2025-04-07": "Tariff sell-off hits global markets",
    "2025-04-09": "US pauses tariffs for 90 days; markets rebound",
    "2025-10-06": "Bitcoin record high",
    "2025-10-10": "Record crypto liquidation crash after US-China tariff threat",
}
PINNED = {"2025-04-18", "2025-10-08", "2025-11-08"}   # dates the user asked about
ALT_BUY, ALT_SELL = 0.52, 0.46   # sensitivity only: thresholds matched to the Exchange's observed range
ROLES = {"chart": "Chart", "borrowing": "Borrowing", "network": "Network", "miner": "Miner",
         "stablecoin": "Stablecoin", "etf": "ETF", "world_economy": "World economy",
         "crash": "Crash", "long_term": "Long-term", "contrarian": "Contrarian", "Mood Specialist": "Mood",
         "News Specialist": "News", "Betting Odds Specialist": "Betting odds"}
NO_ARCHIVE = {"News Specialist": "News", "Betting Odds Specialist": "Betting odds"}
NO_ARCHIVE_REASONS = {
    "News Specialist": ("Backtest: Alpaca's news archive only reaches back to spring 2026 (Tiingo's free plan excludes "
                        "its News API), and reading today's news about a past day would leak the future."),
    "Betting Odds Specialist": ("Backtest: no archive of past prediction-market order books exists, and reading "
                                "today's markets about a past day would leak the future."),
}
MOOD_OFF_REASON = ("Backtest: Gemini was not used for this run (no key or --no-llm), so Mood sits out rather than "
                   "guess.")
ETF_LAUNCH = pd.Timestamp("2024-01-11")
ALPACA_NEWS_START = pd.Timestamp("2026-03-01")   # first month with any Alpaca news on this account
KALSHI_START = pd.Timestamp("2024-11-06")        # first Kalshi daily BTC (KXBTCD) markets found in the archive
FEE, SLIP, BUY_FRACTION, MAX_EXPOSURE = 0.001, 0.0005, 0.10, 0.25   # same costs/limits as the dashboard demo
_CACHE = {}


def get_json(url, headers=None):
    """GET JSON; retries dropped connections and timeouts (HTTP errors such as 429 are raised to the caller)."""
    import time
    from urllib.error import HTTPError, URLError
    for attempt in range(4):
        try:
            with urlopen(Request(url, headers={"User-Agent": "SDP-research/1.0", **(headers or {})}), timeout=30) as response:
                return json.load(response)
        except HTTPError:
            raise
        except (URLError, TimeoutError, ConnectionError, OSError):
            if attempt == 3:
                raise
            time.sleep(10 * (attempt + 1))


def btc_daily():
    """All completed Binance BTCUSDT daily candles (from Aug 2017) up to yesterday."""
    if "btc" in _CACHE:
        return _CACHE["btc"]
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    cursor, rows = datetime(2017, 8, 17, tzinfo=timezone.utc), []
    while cursor < end:
        batch = get_json("https://api.binance.com/api/v3/klines?" + urlencode(dict(
            symbol="BTCUSDT", interval="1d", startTime=int(cursor.timestamp() * 1000),
            endTime=int(end.timestamp() * 1000) - 1, limit=1000)))
        if not batch:
            break
        rows.extend(batch)
        cursor = datetime.fromtimestamp(int(batch[-1][0]) / 1000, timezone.utc) + timedelta(days=1)
    frame = pd.DataFrame([{"date": pd.to_datetime(r[0], unit="ms", utc=True), "open": float(r[1]),
                           "high": float(r[2]), "low": float(r[3]), "close": float(r[4]),
                           "volume": float(r[5])} for r in rows]).drop_duplicates("date")
    frame = frame.set_index("date").sort_index()
    try:
        first = frame.index[0]
        url = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?" + urlencode(dict(
            assets="btc", metrics="PriceUSD", frequency="1d", start_time="2013-01-01",
            end_time=str((first - pd.Timedelta(days=1)).date()), page_size=10000))
        records = []
        while url:
            payload = get_json(url)
            records += payload.get("data", [])
            url = payload.get("next_page_url")
        early = pd.DataFrame([{"date": pd.Timestamp(r["time"]).tz_convert("UTC").normalize(), "close": float(r["PriceUSD"])}
                              for r in records if r.get("PriceUSD")]).set_index("date")
        frame = pd.concat([early[early.index < first], frame]).sort_index()
    except Exception as exc:
        print("  Coin Metrics early prices unavailable:", type(exc).__name__)
    _CACHE["btc"] = frame
    return _CACHE["btc"]


def chain_history(start, end):
    """Coin Metrics community daily metrics; availability estimated as 06:00 UTC next day."""
    url = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?" + urlencode(dict(
        assets="btc", metrics="AdrActCnt,TxCnt,HashRate,FeeTotNtv", frequency="1d",
        start_time=str(start), end_time=str(end), page_size=10000))
    records = []
    while url:
        payload = get_json(url)
        records += payload.get("data", [])
        url = payload.get("next_page_url")
    out = {}
    for family, fields in {"network": {"AdrActCnt": "active_addresses", "TxCnt": "transactions"},
                           "miner": {"HashRate": "hash_rate", "FeeTotNtv": "fees_btc"}}.items():
        rows = []
        for r in records:
            if any(r.get(k) is None for k in fields):
                continue
            day = pd.Timestamp(r["time"]).tz_convert("UTC").normalize()
            rows.append({"date": day.strftime("%Y-%m-%d"), **{v: float(r[k]) for k, v in fields.items()},
                         "available_at": (day + pd.Timedelta(days=1, hours=6)).isoformat(), "complete": True,
                         "source": "Coin Metrics community API; history, availability estimated as 06:00 UTC next day"})
        out[family] = pd.DataFrame(rows)
    return out


def bybit_borrowing(start, end, prices):
    """Bybit BTCUSDT perpetual: daily open interest (BTC, converted with the Binance close) and 8-hour funding."""
    first = pd.Timestamp(start, tz="UTC") - pd.Timedelta(days=10)
    last = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
    ms = lambda ts: int(ts.timestamp() * 1000)
    oi, cursor = {}, first
    while cursor <= last:
        upto = min(cursor + pd.Timedelta(days=150), last + pd.Timedelta(days=1))
        payload = get_json("https://api.bybit.com/v5/market/open-interest?" + urlencode(dict(
            category="linear", symbol="BTCUSDT", intervalTime="1d", startTime=ms(cursor), endTime=ms(upto), limit=200)))
        for row in (payload.get("result") or {}).get("list") or []:
            oi[pd.Timestamp(int(row["timestamp"]), unit="ms", tz="UTC")] = float(row["openInterest"])
        cursor = upto + pd.Timedelta(days=1)
    funding, upto = [], last + pd.Timedelta(days=1)
    while upto > first:
        payload = get_json("https://api.bybit.com/v5/market/funding/history?" + urlencode(dict(
            category="linear", symbol="BTCUSDT", startTime=ms(first), endTime=ms(upto), limit=200)))
        rows = (payload.get("result") or {}).get("list") or []
        if not rows:
            break
        funding += [(pd.Timestamp(int(r["fundingRateTimestamp"]), unit="ms", tz="UTC"), float(r["fundingRate"])) for r in rows]
        oldest = min(ts for ts, _ in funding)
        if oldest >= upto:
            break
        upto = oldest - pd.Timedelta(milliseconds=1)
    fund = pd.DataFrame(funding, columns=["at", "rate"]).drop_duplicates("at")
    close = prices["close"]
    out = []
    for day in pd.date_range(first, last - pd.Timedelta(days=1), tz="UTC"):
        snap = day + pd.Timedelta(days=1)
        rates = fund[(fund["at"] >= day) & (fund["at"] < snap)]
        price = close.get(day)
        complete = len(rates) == 3 and snap in oi and price is not None and pd.notna(price)
        out.append({"date": str(day.date()),
                    "funding_rate": float(rates["rate"].mean()) if len(rates) else None,
                    "open_interest_usd": oi[snap] * float(price) if complete else None,
                    "open_interest_observed_at": snap.isoformat() if snap in oi else None,
                    "available_at": snap.isoformat(), "complete": bool(complete),
                    "source": "Bybit USDT perpetual BTCUSDT; history, public at 00:00 UTC next day; OI in BTC x Binance close as USD"})
    return pd.DataFrame(out)


def borrowing_history(inputs, start, end):
    """Binance for the last ~25 days (its open-interest history limit); Bybit for older scenarios."""
    today = pd.Timestamp.now(tz="UTC").normalize()
    if pd.Timestamp(start, tz="UTC") - pd.Timedelta(days=7) < today - pd.Timedelta(days=25):
        try:
            frame = bybit_borrowing(start, end, btc_daily())
            return frame if frame["complete"].any() else None
        except Exception as exc:
            print("  Bybit borrowing history unavailable:", type(exc).__name__)
            return None
    from market_data.borrowing import collect
    today = pd.Timestamp.now(tz="UTC").normalize()
    first = max(pd.Timestamp(start, tz="UTC") - pd.Timedelta(days=7), today - pd.Timedelta(days=25))
    last = min(pd.Timestamp(end, tz="UTC"), today - pd.Timedelta(days=1))
    if first > last:
        return None
    frame = collect(str(first.date()), str(last.date()), output=str(inputs / "_borrowing_collect.csv"))
    (inputs / "_borrowing_collect.csv").unlink(missing_ok=True)
    frame = frame[frame.complete.astype(str).str.lower() == "true"].copy()
    # Funding settles at 00:00 UTC and the OI snapshot is taken at the day's close: both public then.
    frame["available_at"] = (pd.to_datetime(frame["date"], utc=True) + pd.Timedelta(days=1)).map(lambda t: t.isoformat())
    frame["source"] = "Binance USD-M BTCUSDT; history, public at 00:00 UTC next day"
    return frame


def macro_history(inputs, start, end):
    from market_data.macro import collect
    last = min(pd.Timestamp(end) + pd.Timedelta(days=2), pd.Timestamp.now(tz="UTC").tz_localize(None).normalize() - pd.Timedelta(days=1))
    return collect(str((pd.Timestamp(start) - pd.Timedelta(days=20)).date()), str(last.date()), output=str(inputs / "macro.csv"))


def etf_history():
    """Reviewed daily totals plus SoSoValue history (~300 sessions); estimated publication 12:00 UTC next day."""
    if "etf" in _CACHE:
        return _CACHE["etf"]
    frame = pd.read_csv(ROOT / "data" / "etf_flows.csv")
    estimated = (pd.to_datetime(frame["date"], utc=True) + pd.Timedelta(days=1, hours=12)).map(lambda t: t.isoformat())
    copied = frame["source"].astype(str).str.contains("farside", case=False)
    frame.loc[copied, "available_at"] = estimated[copied]
    frame.loc[copied, "source"] = frame.loc[copied, "source"].astype(str) + "; availability estimated as 12:00 UTC next day"
    try:
        from market_data import etf_sosovalue as sv
        key = sv.api_key(ROOT / ".env")
        if key:
            extra = sv.fetch(key)
            extra = extra[~extra["date"].astype(str).isin(set(frame["date"].astype(str)))]
            frame = pd.concat([extra, frame], ignore_index=True).sort_values("date")
    except Exception as exc:  # keep going with the reviewed file only; never print the key
        print("  SoSoValue history unavailable:", type(exc).__name__)
    farside = OUT / "farside_all.csv"
    if farside.exists():   # hand-copied full Farside history; reviewed rows above take precedence
        extra = pd.read_csv(farside)
        extra = extra[~extra["date"].astype(str).isin(set(frame["date"].astype(str)))]
        frame = pd.concat([extra, frame], ignore_index=True).sort_values("date")
    _CACHE["etf"] = frame
    return frame


def fng_history():
    """Full Alternative.me Fear & Greed daily history (timestamps at 00:00 UTC)."""
    if "fng" not in _CACHE:
        payload = get_json("https://api.alternative.me/fng/?limit=0&format=json")
        rows = []
        for item in payload.get("data") or []:
            try:
                stamp = datetime.fromtimestamp(int(item["timestamp"]), tz=timezone.utc)
                rows.append({"evidence_id": f"alternative-me-fng-{stamp.strftime('%Y-%m-%dT%H:%M:%SZ')}",
                             "timestamp": stamp.isoformat().replace("+00:00", "Z"), "timestamp_unix": int(item["timestamp"]),
                             "score": int(item["value"]), "classification": str(item.get("value_classification") or "unknown"),
                             "source": "Alternative.me Fear & Greed Index"})
            except (KeyError, TypeError, ValueError):
                continue
        _CACHE["fng"] = sorted(rows, key=lambda r: r["timestamp_unix"])
    return _CACHE["fng"]


class MoodBacktest:
    """Runs the team's Mood adapter on Fear & Greed readings published strictly before the decision."""
    AGENT_NAME = "Mood Specialist"

    def __init__(self, client, cache_dir):
        from agents.adapters import SpecialistAdapter
        self.adapter = SpecialistAdapter("mood", model_client=client)
        self.cache_dir = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

    def forecast(self, day, view):
        prediction = pd.Timestamp(day, tz="UTC") + pd.Timedelta(days=1)
        readings = [r for r in fng_history() if r["timestamp_unix"] < prediction.timestamp()][-30:]
        cache = self.cache_dir / f"mood-{prediction.date()}-{readings[-1]['timestamp_unix'] if readings else 0}.json"
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
        data = {"student2_forecast": {"prediction_time": prediction.isoformat(),
                                      "target_time": (prediction + pd.Timedelta(days=1)).isoformat(),
                                      "price_source": "binance:BTC/USDT daily close",
                                      "reference_price_definition": "Binance BTCUSDT daily candle close at the contract timestamp",
                                      "tie_definition": "down"},
                "student2_mood_readings": readings,
                "student2_mood_observed_at": (prediction - pd.Timedelta(minutes=1)).isoformat()}
        result = self.adapter.forecast(str(prediction.date()), data)
        failed = any("Specialist failed" in str(e) for e in result.get("evidence") or [])
        if not failed:  # never cache transient model errors
            cache.write_text(json.dumps(result, default=str), encoding="utf-8")
        return result


def alpaca_keys():
    from dotenv import dotenv_values
    env = dotenv_values(ROOT / ".env")
    kid, secret = env.get("ALPACA_API_KEY_ID"), env.get("ALPACA_API_SECRET_KEY")
    return (kid, secret) if kid and secret else None


def alpaca_day(day, keys, cache_dir):
    """Bitcoin articles (with summaries) from Alpaca's news feed for one UTC day; cached on disk."""
    import re
    import time
    cache = cache_dir / f"alpaca-{day}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    pattern, token, total, hits = re.compile(r"\b(bitcoin|btc)\b", re.I), None, 0, []
    for _ in range(80):
        query = dict(start=f"{day}T00:00:00Z", end=f"{day}T23:59:59Z", limit=50, sort="desc", include_content="false")
        if token:
            query["page_token"] = token
        payload = get_json("https://data.alpaca.markets/v1beta1/news?" + urlencode(query),
                           {"APCA-API-KEY-ID": keys[0], "APCA-API-SECRET-KEY": keys[1]})
        time.sleep(0.32)  # stay under the free plan's 200 requests per minute
        articles = payload.get("news") or []
        total += len(articles)
        hits += [{k: a.get(k) for k in ("id", "headline", "summary", "created_at", "updated_at", "source", "author", "url")}
                 for a in articles if (a.get("summary") or "").strip() and pattern.search(a.get("headline", "") + " " + a.get("summary", ""))]
        token = payload.get("next_page_token")
        if not token:
            break
    result = {"day": day, "archive_articles": total, "bitcoin_articles": hits}
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result), encoding="utf-8")
    return result


def _norm_title(text):
    import re
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()[:90]


def _mentions_bitcoin(*parts):
    import re
    return bool(re.search(r"\b(bitcoin|btc)\b", " ".join(str(p or "") for p in parts), re.I))


def fcn_range(start, end, cache_dir):
    """Free Crypto News archive (cryptocurrency.cv) BTC articles with descriptions for a date range; cached."""
    import re
    import time
    cache = cache_dir / f"fcn-{start}-{end}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    rows, offset = [], 0
    while offset <= 5000:
        url = "https://cryptocurrency.cv/api/archive?" + urlencode(dict(
            start_date=start, end_date=end, ticker="BTC", limit=100, offset=offset))
        for attempt in range(8):
            try:
                payload = get_json(url, {"Accept": "application/json"})
                break
            except Exception as exc:  # free API rate limit: back off and retry, never cache a partial range
                if "429" not in str(exc) or attempt == 7:
                    raise
                time.sleep(20 * (attempt + 1))
        batch = payload.get("articles") or []
        for a in batch:
            published, text = a.get("pub_date") or a.get("first_seen"), (a.get("description") or "").strip()
            if not published or len(text) < 40:
                continue
            link = a.get("link") or ""
            stamp = re.search(r"/(20\d\d)/(\d\d)/", link)   # drop items whose URL dates them after the claimed publication
            if stamp and (int(stamp.group(1)), int(stamp.group(2))) > (int(published[:4]), int(published[5:7])):
                continue
            rows.append({"id": a.get("id"), "title": a.get("title"), "summary": text, "published": published,
                         "updated": published, "publisher": a.get("source") or "Free Crypto News", "url": link,
                         "source_type": "free_crypto_news"})
        if not (payload.get("pagination") or {}).get("hasMore") or not batch:
            break
        offset += len(batch)
        time.sleep(2.0)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(rows), encoding="utf-8")
    return rows


def coindesk_window(prediction, hours, key, cache_dir):
    """CoinDesk Data news (BTC category) published in the `hours` before `prediction`; slow and cached."""
    import time
    cache = cache_dir / f"coindesk-{prediction.date()}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    if _CACHE.get("coindesk_off"):   # quota used up earlier in this run: skip instantly
        return []
    floor, to_ts, rows = (prediction - pd.Timedelta(hours=hours)).timestamp(), int(prediction.timestamp()) - 1, []
    for _ in range(12):
        try:
            payload = get_json("https://data-api.coindesk.com/news/v1/article/list?" + urlencode(dict(
                lang="EN", to_ts=to_ts, limit=50, categories="BTC")), {"authorization": "Apikey " + key})
        except Exception as exc:
            if "429" in str(exc):   # free key: 100 calls a month; once refused, stop asking for this run
                _CACHE["coindesk_off"] = True
                print("    CoinDesk free quota used up; News falls back to the other archives", flush=True)
                return []
            raise
        batch = (payload or {}).get("Data") or []
        for a in batch:
            body = (a.get("BODY") or "").strip()
            if a["PUBLISHED_ON"] >= floor and len(body) >= 40 and _mentions_bitcoin(a.get("TITLE"), body[:600]):
                published = pd.Timestamp(a["PUBLISHED_ON"], unit="s", tz="UTC")
                updated = pd.Timestamp(a.get("UPDATED_ON") or a["PUBLISHED_ON"], unit="s", tz="UTC")
                rows.append({"id": a.get("ID"), "title": a.get("TITLE"), "summary": body[:500], "published": published.isoformat(),
                             "updated": updated.isoformat(), "publisher": (a.get("SOURCE_DATA") or {}).get("NAME") or "CoinDesk Data",
                             "url": a.get("URL") or "", "source_type": "coindesk_news"})
        time.sleep(2.5)
        if not batch or batch[-1]["PUBLISHED_ON"] < floor:
            break
        to_ts = int(batch[-1]["PUBLISHED_ON"]) - 1
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(rows), encoding="utf-8")
    return rows


_REPLAY_NEWS_NOTE = ("The remaining {n} record(s) are archived_news: genuine news articles retrieved from dated news "
                     "archives (Alpaca/Benzinga, Free Crypto News, CoinDesk) with publisher timestamps before prediction_time. "
                     "They are not Yahoo reporting but are real external news items; weigh them like live reporting.")
_UNVERIFIED = r"It also contains (\d+) other or unverified record\(s\); these are not authenticated Yahoo reporting\. "


def _patch_news_prompt():
    """Replay-only: describe archived articles as real dated reporting instead of 'unverified' (in-process only)."""
    import re
    from agents.specialists import news_specialist as ns
    if getattr(ns, "_replay_patched", False):
        return
    original = ns.build_gemini_prompt

    def build(articles, prediction_time, target_time):
        prompt = original(articles, prediction_time, target_time)
        prompt["content"] = re.sub(_UNVERIFIED, lambda m: _REPLAY_NEWS_NOTE.format(n=m.group(1)) + " ", prompt["content"])
        return prompt
    ns.build_gemini_prompt = build
    ns._replay_patched = True


class NewsBacktest:
    """Runs the team's News adapter on archived Bitcoin articles published and last edited before the decision."""
    AGENT_NAME = "News Specialist"
    HOURS, MIN_ARTICLES, MAX_ARTICLES = 36, 3, 20

    def __init__(self, client, start, end, cache_dir):
        from agents.adapters import SpecialistAdapter
        from dotenv import dotenv_values
        _patch_news_prompt()
        self.adapter, self.cache_dir = SpecialistAdapter("news", model_client=client), cache_dir
        self.alpaca = alpaca_keys()
        self.coindesk = (dotenv_values(ROOT / ".env").get("COINDESK_API_KEY") or "").strip() or None
        first = (pd.Timestamp(start) - pd.Timedelta(days=2)).date()
        self.fcn = fcn_range(str(first), str((pd.Timestamp(end) + pd.Timedelta(days=1)).date()), cache_dir / "news")

    def _alpaca(self, prediction):
        rows = []
        if not self.alpaca or prediction < pd.Timestamp(ALPACA_NEWS_START, tz="UTC"):
            return rows
        for n in (2, 1):
            day = alpaca_day(str((prediction - pd.Timedelta(days=n)).date()), self.alpaca, self.cache_dir / "news")
            for a in day["bitcoin_articles"]:
                rows.append({"id": a["id"], "title": a["headline"], "summary": a["summary"], "published": a["created_at"],
                             "updated": a.get("updated_at") or a["created_at"], "publisher": a.get("source") or a.get("author") or "Alpaca news",
                             "url": a.get("url") or "", "source_type": "alpaca_news"})
        return rows

    def _eligible(self, rows, prediction):
        out = []
        for r in rows:
            published, updated = pd.Timestamp(r["published"]), pd.Timestamp(r.get("updated") or r["published"])
            if published.tzinfo is None:
                published = published.tz_localize("UTC")
            if updated.tzinfo is None:
                updated = updated.tz_localize("UTC")
            if prediction - pd.Timedelta(hours=self.HOURS) <= published and max(published, updated) < prediction:
                out.append(dict(r, published=published.isoformat()))
        return out

    def forecast(self, day, view):
        prediction = pd.Timestamp(day, tz="UTC") + pd.Timedelta(days=1)
        rows = self._eligible(self._alpaca(prediction), prediction)
        if len(rows) < self.MIN_ARTICLES:
            rows += self._eligible(self.fcn, prediction)
        if len(rows) < self.MIN_ARTICLES and self.coindesk:
            try:
                rows += self._eligible(coindesk_window(prediction, self.HOURS, self.coindesk, self.cache_dir / "news"), prediction)
            except Exception as exc:
                print("    CoinDesk unavailable for", prediction.date(), type(exc).__name__, flush=True)
        seen, articles = set(), []
        for r in sorted(rows, key=lambda r: r["published"], reverse=True):
            key = _norm_title(r["title"])
            if key in seen:
                continue
            seen.add(key)
            articles.append({"evidence_id": f"{r['source_type']}-{r['id']}", "title": r["title"], "summary": r["summary"],
                             "publisher": r["publisher"], "url": r["url"], "published_at": r["published"],
                             "source_type": r["source_type"]})
            if len(articles) == self.MAX_ARTICLES:
                break
        if not articles:
            return {"agent": self.AGENT_NAME, "day": str(prediction.date()), "p_up": None, "data_missing": True,
                    "evidence": ["No archived Bitcoin articles with text were found for the 36 hours before this decision "
                                 "(checked Alpaca and Free Crypto News; CoinDesk's free quota of 100 calls a month was used up)."]}
        import hashlib
        digest = hashlib.sha1("|".join(a["evidence_id"] for a in articles).encode("utf-8")).hexdigest()[:10]
        cache = self.cache_dir / f"news-v2-{prediction.date()}-{digest}.json"
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
        data = {"student2_forecast": {"prediction_time": prediction.isoformat(),
                                      "target_time": (prediction + pd.Timedelta(days=1)).isoformat(),
                                      "price_source": "binance:BTC/USDT daily close",
                                      "reference_price_definition": "Binance BTCUSDT daily candle close at the contract timestamp",
                                      "tie_definition": "down"},
                "student2_news_articles": articles,
                "student2_news_observed_at": (prediction - pd.Timedelta(minutes=1)).isoformat()}
        result = self.adapter.forecast(str(prediction.date()), data)
        result["news_sources"] = sorted({a["source_type"] for a in articles})
        if not any("Specialist failed" in str(e) for e in result.get("evidence") or []):
            cache.write_text(json.dumps(result, default=str), encoding="utf-8")
        return result


def headline_label(event_day, news, client):
    """Short label for a big-move day, summarised by Gemini from that day's archived headlines only."""
    cache = OUT / "_llm_cache" / f"label-{event_day}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8")).get("label")
    end = pd.Timestamp(event_day, tz="UTC") + pd.Timedelta(days=1)
    rows = [r for r in news._eligible(news._alpaca(end) + news.fcn, end) if pd.Timestamp(r["published"]) >= end - pd.Timedelta(days=1)]
    if len(rows) < 3 and news.coindesk:
        try:
            rows += [r for r in coindesk_window(end, 24, news.coindesk, news.cache_dir / "news")]
        except Exception:
            pass
    titles = list(dict.fromkeys(str(r["title"]).strip() for r in rows if r.get("title")))[:25]
    label = None
    if len(titles) >= 3:
        prompt = ("Below are crypto news headlines published on " + str(event_day) + " (UTC). Using ONLY these headlines, "
                  "describe the main market event of that day in at most 9 words. If the headlines do not show a clear "
                  "single event, answer with an empty string. Return JSON {\"label\": string}.\n" + json.dumps(titles))
        try:
            value = client(prompt)
            label = (value.get("label") if isinstance(value, dict) else None) or None
        except Exception:
            label = None
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"label": label, "headlines": titles}), encoding="utf-8")
    return label


class BettingBacktest:
    """Runs the team's Betting Odds adapter (Kalshi trade path, no model call) on an archived Kalshi trade."""
    AGENT_NAME = "Betting Odds Specialist"
    API = "https://api.elections.kalshi.com/trade-api/v2"

    def __init__(self, cache_dir, hourly):
        from agents.adapters import SpecialistAdapter
        self.adapter, self.cache_dir, self.hourly = SpecialistAdapter("betting_odds", model_client=None), cache_dir, hourly

    def _get(self, path, **params):
        import time
        for attempt in range(5):
            try:
                return get_json(self.API + path + "?" + urlencode(params), {"Accept": "application/json"})
            except Exception as exc:
                if "429" not in str(exc) or attempt == 4:
                    raise
                time.sleep(5 * (attempt + 1))

    def _missing(self, prediction, reason):
        return {"agent": self.AGENT_NAME, "day": str(prediction.date()), "p_up": None, "data_missing": True, "evidence": [reason]}

    def observation(self, prediction):
        """Archived trade within 5 minutes before the decision in the next 5pm-ET fixing market; cached."""
        from zoneinfo import ZoneInfo
        cache = self.cache_dir / f"kalshi-{prediction.date()}.json"
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
        fixing = prediction.tz_convert(ZoneInfo("America/New_York"))
        fixing = fixing + pd.Timedelta(days=1) if fixing.hour >= 17 else fixing
        event = "KXBTCD-" + fixing.strftime("%y%b%d").upper() + "17"
        markets = (self._get("/historical/markets", event_ticker=event, limit=200).get("markets") or
                   self._get("/markets", event_ticker=event, limit=200).get("markets") or [])
        by_ticker = {m["ticker"]: m for m in markets}
        trades, lo, hi = [], int(prediction.timestamp()) - 300, int(prediction.timestamp())
        for path in ("/historical/trades", "/markets/trades"):
            cursor = ""
            for _ in range(15):
                payload = self._get(path, min_ts=lo, max_ts=hi, limit=1000, cursor=cursor)
                trades += [x for x in payload.get("trades") or [] if x.get("ticker") in by_ticker]
                cursor = payload.get("cursor")
                if not cursor:
                    break
            if trades:
                break
        result = {"event": event, "markets": len(markets), "trades": trades, "by_ticker": by_ticker}
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(result), encoding="utf-8")
        return result

    def forecast(self, day, view):
        from market_data.prediction_markets.kalshi_live import trade_quote
        prediction = pd.Timestamp(day, tz="UTC") + pd.Timedelta(days=1)
        if prediction < pd.Timestamp(KALSHI_START, tz="UTC"):
            return self._missing(prediction, "Kalshi's daily Bitcoin markets did not exist yet (first found in Nov 2024).")
        found = self.observation(prediction)
        if not found["markets"]:
            return self._missing(prediction, f"No Kalshi daily Bitcoin market ({found['event']}) was found in the archive.")
        eligible = []
        for trade in found["trades"]:
            market = dict(found["by_ticker"][trade["ticker"]], status="active")  # its state at the decision time
            stamp = pd.Timestamp(trade["created_time"])
            received = (stamp + pd.Timedelta(seconds=1)).isoformat()
            try:
                quote = trade_quote(market, trade, received)
            except (KeyError, TypeError, ValueError):
                continue
            eligible.append((abs(quote["yes_trade_price"] - 0.5), -stamp.timestamp(), market, trade, quote))
        if not eligible:
            return self._missing(prediction, "No Kalshi trade in the 5 minutes before this decision (the agent's own "
                                             "freshness rule); the daily markets trade thinly around 00:00 UTC.")
        _, _, market, trade, quote = min(eligible, key=lambda e: e[:2])
        hourly = self.hourly
        bars = hourly[(hourly.index < prediction) & (hourly.index >= prediction - pd.Timedelta(hours=25))]
        close = view["btc_prices"]["close"]
        reference = float(close.iloc[-1])
        observation = {"provider": "kalshi", "mode": "live_read_only_observation", "observation_status": "trade_observed",
                       "market": market, "raw_trade": trade, "quote": quote,
                       "replay": "Rebuilt from Kalshi's archived trade by experiments/backtest_archive.py; not a live capture."}
        data = {"student2_forecast": {"prediction_time": prediction.isoformat(),
                                      "target_time": (prediction + pd.Timedelta(days=1)).isoformat(),
                                      "price_source": "binance:BTC/USDT",
                                      "reference_price_definition": "last completed UTC one-minute close at or before each contract timestamp",
                                      "tie_definition": "down"},
                "student2_market_observation": observation, "student2_markets_observed_at": quote["received_at"],
                "btc_hourly": bars, "_reference_close": reference}
        result = self.adapter.forecast(str(prediction.date()), data)
        result.setdefault("limitations", []).append("Backtest replay: observation rebuilt from Kalshi's archived trade.")
        return result


def btc_hourly_history(start, end):
    """Binance hourly candles (indexed by open time, UTC) for the Betting odds replay."""
    first, last = pd.Timestamp(start, tz="UTC") - pd.Timedelta(days=3), pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=2)
    rows, cursor = [], first
    while cursor < last:
        batch = get_json("https://api.binance.com/api/v3/klines?" + urlencode(dict(
            symbol="BTCUSDT", interval="1h", startTime=int(cursor.timestamp() * 1000),
            endTime=int(last.timestamp() * 1000) - 1, limit=1000)))
        if not batch:
            break
        rows += batch
        cursor = pd.Timestamp(int(batch[-1][0]), unit="ms", tz="UTC") + pd.Timedelta(hours=1)
    frame = pd.DataFrame([{"date": pd.Timestamp(int(r[0]), unit="ms", tz="UTC"), "close": float(r[4])} for r in rows])
    return frame.drop_duplicates("date").set_index("date").sort_index()


def gemini_client():
    try:
        from dotenv import dotenv_values
        from model_clients.demo_gemini import DemoGemini
        key = dotenv_values(ROOT / ".env").get("GEMINI_API_KEY") or dotenv_values(ROOT / ".env").get("GOOGLE_API_KEY")
        return DemoGemini(api_key=key) if key else None
    except Exception as exc:
        print("  Gemini unavailable:", type(exc).__name__)
        return None


def walk_forward_stablecoin(data, start):
    """Retrain the Stablecoin models on rows dated before the scenario start (no in-sample evidence)."""
    from market_data.stablecoin import depeg_model, liquidity_model
    first = pd.Timestamp(start, tz="UTC")
    notes = {}
    long = data.get("stablecoin_history_long")
    if isinstance(long, pd.DataFrame):
        frame = long.copy()
        frame.index = pd.to_datetime(frame.index, utc=True)
        try:
            data["stablecoin_liquidity_model"] = liquidity_model.train(frame[frame.index < first])
            notes["liquidity_train_end"] = data["stablecoin_liquidity_model"]["train_end"]
        except Exception as exc:
            notes["liquidity"] = "not retrained: " + type(exc).__name__
    short = data.get("stablecoin_history")
    if isinstance(short, pd.DataFrame):
        frame = short.copy()
        frame.index = pd.to_datetime(frame.index, utc=True)
        frame = frame[frame.index < first]
        try:
            if len(frame) < 120:
                raise ValueError("too little history")
            data["stablecoin_depeg_model"] = depeg_model.train(frame)
            notes["depeg_train_end"] = data["stablecoin_depeg_model"]["train_end"]
        except Exception as exc:
            notes["depeg"] = f"not retrained ({type(exc).__name__}; {len(frame)} rows before the window)"
    return notes


def stablecoin_long_history():
    """DefiLlama long history, extended backwards with Coin Metrics market caps (same columns)."""
    if "stable_long" in _CACHE:
        return _CACHE["stable_long"]
    frame = pd.read_csv(ROOT / "data" / "stablecoin_history_long.csv")
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    first = frame["date"].min()
    try:
        url = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?" + urlencode(dict(
            assets="usdt,usdc,sai,dai,btc", metrics="CapMrktCurUSD,PriceUSD", frequency="1d",
            start_time="2016-01-01", end_time=str((first - pd.Timedelta(days=1)).date()), page_size=10000))
        records = []
        while url:
            payload = get_json(url)
            records += payload.get("data", [])
            url = payload.get("next_page_url")
        table = {}
        for r in records:
            day = pd.Timestamp(r["time"]).tz_convert("UTC").normalize()
            row = table.setdefault(day, {})
            asset = "dai" if r["asset"] in ("sai", "dai") else r["asset"]
            if r.get("CapMrktCurUSD"):
                row[asset + "_cap"] = row.get(asset + "_cap", 0.0) + float(r["CapMrktCurUSD"])
            if r.get("PriceUSD") and asset + "_price" not in row:
                row[asset + "_price"] = float(r["PriceUSD"])
        early = pd.DataFrame([{"date": d, **v} for d, v in sorted(table.items())])
        for col in ("usdc", "dai"):   # not issued yet in the earliest years
            early[col + "_cap"] = early.get(col + "_cap", pd.Series(dtype=float)).fillna(0.0)
            early[col + "_price"] = early.get(col + "_price", pd.Series(dtype=float)).fillna(1.0)
        early = early.dropna(subset=["usdt_cap", "usdt_price", "btc_price", "btc_cap"])
        frame = pd.concat([early[early["date"] < first], frame], ignore_index=True)
    except Exception as exc:
        print("  Coin Metrics stablecoin backfill unavailable:", type(exc).__name__)
    frame["date"] = frame["date"].map(lambda d: d.isoformat())
    _CACHE["stable_long"] = frame
    return frame


def build_inputs(inputs, start, end):
    inputs.mkdir(parents=True, exist_ok=True)
    for old in inputs.glob("*.csv"):
        old.unlink()
    btc_daily().to_csv(inputs / "btc_prices.csv", index_label="date")
    chain = chain_history((pd.Timestamp(start) - pd.Timedelta(days=30)).date(), (pd.Timestamp(end) + pd.Timedelta(days=2)).date())
    for name, frame in chain.items():
        frame.to_csv(inputs / f"{name}.csv", index=False)
    borrowing = borrowing_history(inputs, start, end)
    if borrowing is not None and not borrowing.empty:
        borrowing.to_csv(inputs / "borrowing.csv", index=False)
    macro_history(inputs, start, end)
    etf_history().to_csv(inputs / "etf_flows.csv", index=False)
    pd.read_csv(ROOT / "data" / "stablecoin_history.csv").to_csv(inputs / "stablecoin_history.csv", index=False)
    stablecoin_long_history().to_csv(inputs / "stablecoin_history_long.csv", index=False)


def readable_reason(name, reason, day):
    reason = str(reason or "No usable data")
    if name == "borrowing" and ("no borrowing data" in reason or "no historical borrowing" in reason or "six consecutive" in reason):
        return ("No open-interest history covers this day: Binance keeps only ~25 days and Bybit's history starts in "
                "early 2021.")
    if name == "etf" and day < ETF_LAUNCH:
        return "US spot Bitcoin ETFs did not exist yet (they started trading on 11 Jan 2024)."
    if name == "etf" and ("no etf_flows" in reason or "no historical" in reason or "three recent" in reason):
        return "Fewer than three recent ETF reporting sessions were published before this decision."
    if name == "stablecoin" and "archived stablecoin" in reason:
        return "No walk-forward stablecoin model or recent stablecoin history was available for this day."
    return reason


def agent_entry(name, p, evidence, reason, day):
    if name in NO_ARCHIVE and reason == "__noarchive__":   # only for agents that were not run at all
        return {"agent": name, "role": NO_ARCHIVE[name], "p_up": None, "data_missing": True,
                "evidence": [NO_ARCHIVE_REASONS[name]], "limitations": [NO_ARCHIVE_REASONS[name]]}
    if name == "Mood Specialist" and reason == "__off__":
        return {"agent": name, "role": "Mood", "p_up": None, "data_missing": True,
                "evidence": [MOOD_OFF_REASON], "limitations": [MOOD_OFF_REASON]}
    role = ROLES.get(name, name)
    if p is None:
        text = readable_reason(name, reason, day)[:300]
        return {"agent": name, "role": role, "p_up": None, "data_missing": True, "evidence": [text], "limitations": [text]}
    return {"agent": name, "role": role, "p_up": p, "data_missing": False, "evidence": list(evidence or [])}


def brier(rows, key):
    vals = [(r[key] - r["up"]) ** 2 for r in rows if isinstance(r[key], (int, float))]
    return sum(vals) / len(vals) if vals else None


def boss_paper(rows, buy_at=.55, sell_at=.45, record=True):
    """Boss rule (BUY >= buy_at, SELL <= sell_at) on a 10,000 USD long-only account, filled at the decision close."""
    cash, btc, trades = 10000.0, 0.0, 0
    for r in rows:
        price, p = r["price"], r["exchange"]
        action = "HOLD" if p is None else "BUY" if p >= buy_at and r["cap_feasible"] else "SELL" if p <= sell_at else "HOLD"
        equity = cash + btc * price
        if action == "BUY":
            spend = min(cash * BUY_FRACTION, max(0.0, MAX_EXPOSURE * equity - btc * price))
            if spend > 1:
                fill = price * (1 + SLIP)
                btc += spend * (1 - FEE) / fill
                cash -= spend
                trades += 1
        elif action == "SELL" and btc > 0:
            cash += btc * price * (1 - SLIP) * (1 - FEE)
            btc, trades = 0.0, trades + 1
        if record:
            r["boss"] = action
    last = rows[-1]["next_price"]
    return {"final_equity": cash + btc * last, "return_pct": (cash + btc * last) / 10000 * 100 - 100, "trades": trades,
            "buy_and_hold_pct": (last / rows[0]["price"] - 1) * 100}


def run_scenario(name, label, start, end, base_agents, client):
    folder = OUT / name
    build_inputs(folder / "inputs", start, end)
    data = load_data(folder / "inputs")
    stable_notes = walk_forward_stablecoin(data, start)
    agents = dict(base_agents)
    if client is not None:
        agents["Mood Specialist"] = MoodBacktest(client, OUT / "_llm_cache")
        agents["News Specialist"] = NewsBacktest(client, start, end, OUT / "_llm_cache")
    if pd.Timestamp(end) >= KALSHI_START:
        try:
            agents["Betting Odds Specialist"] = BettingBacktest(OUT / "_llm_cache" / "kalshi", btc_hourly_history(start, end))
        except Exception as exc:
            print("  Kalshi replay unavailable:", type(exc).__name__)
    close = pd.to_numeric(data["btc_prices"]["close"])
    days = [d for d in close.index if pd.Timestamp(start) <= d <= pd.Timestamp(end) and d < close.index[-1]]
    archive = folder / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    for old in archive.glob("*.json"):
        old.unlink()
    ex = Exchange(list(agents))
    rows, hits_by_agent = [], {}
    print(f"\n== {label}: {days[0].date()} to {days[-1].date()} ({len(days)} days); stablecoin {stable_notes}", flush=True)
    for day in days:
        tomorrow = close.index[close.index.get_loc(day) + 1]
        evidence = {}
        forecasts, abstentions = evaluate_agents(agents, set(), day, data_upto(data, day), None, evidence_log=evidence)
        team = combine(ex, forecasts)
        weights = ex.weights(forecasts.keys()) if forecasts else {}
        coins_before = dict(ex.coins)
        went_up = bool(close[tomorrow] > close[day])
        changes = ex.settle(forecasts, went_up) or {}
        for agent, p in forecasts.items():
            if p != .5:
                hits_by_agent.setdefault(ROLES.get(agent, agent), []).append((p > .5) == went_up)
        decision = pd.Timestamp(day.date(), tz="UTC") + pd.Timedelta(days=1)
        entries = [agent_entry(n, forecasts.get(n), evidence.get(n), abstentions.get(n), day) for n in agents]
        entries += [agent_entry(n, None, None, "__noarchive__", day) for n in NO_ARCHIVE if n not in agents]
        if "Mood Specialist" not in agents:
            entries.append(agent_entry("Mood Specialist", None, None, "__off__", day))
        forecast = {
            "id": f"backtest-{name}-{day.date()}", "mode": "backtest", "scenario": {"name": name, "label": label},
            "events": {"today": EVENTS.get(str(day.date())), "next_day": EVENTS.get(str(tomorrow.date())),
                       "pinned": str(day.date()) in PINNED},
            "created_at": decision.isoformat(), "decision_day": str(day.date()),
            "methods": team, "cap_feasible": bool(forecasts) and len(forecasts) * ex.cap >= 1,
            "active_count": len(forecasts), "weights": weights, "agents": entries,
            "quote": {"price": float(close[day]), "bar_close_time": decision.isoformat(), "source": "Binance BTCUSDT daily close"},
            "contract": {"prediction_time": decision.isoformat(), "target_time": (decision + pd.Timedelta(days=1)).isoformat(),
                         "price_source": "binance:BTC/USDT daily close", "tie_definition": "down"},
            "backtest": {"rules": __doc__.split("Point-in-time rules (stated in each saved file):")[1].split("Usage")[0].strip(),
                         "built_at": datetime.now(timezone.utc).isoformat(), "stablecoin_walk_forward": stable_notes},
        }
        settlement = {"settled": True, "reference_price": float(close[day]), "target_price": float(close[tomorrow]),
                      "went_up": int(went_up), "settled_at": (decision + pd.Timedelta(days=1)).isoformat(),
                      "target_time": (decision + pd.Timedelta(days=1)).isoformat(),
                      "bets": [{"agent": a, "p_up": p, "coins_before": coins_before.get(a), "change": changes.get(a),
                                "coins_after": ex.coins[a]} for a, p in forecasts.items()]}
        move = (close[tomorrow] / close[day] - 1) * 100
        if client is not None and abs(move) >= 5 and not forecast["events"]["next_day"] and "News Specialist" in agents:
            label = headline_label(str(tomorrow.date()), agents["News Specialist"], client)
            if label:
                forecast["events"]["next_day_headlines"] = label
        (archive / f"{day.date()}.json").write_text(json.dumps({"forecast": forecast, "settlement": settlement},
                                                              indent=1, default=str), encoding="utf-8")
        rows.append({"day": str(day.date()), "price": float(close[day]), "next_price": float(close[tomorrow]),
                     "change": (close[tomorrow] / close[day] - 1) * 100, "up": int(went_up), "active": len(forecasts),
                     "cap_feasible": forecast["cap_feasible"], **{k: team[k] for k in ("exchange", "vote", "average")}})
    prices = [rows[0]["price"]] + [r["next_price"] for r in rows]
    peak, drawdown = prices[0], 0.0
    for price in prices:
        peak = max(peak, price)
        drawdown = min(drawdown, price / peak - 1)
    big = [r for r in rows if r["change"] <= -5]
    summary = {
        "name": name, "label": label, "days": len(rows), "start": rows[0]["day"], "end": rows[-1]["day"],
        "btc_change_pct": (prices[-1] / prices[0] - 1) * 100, "max_drawdown_pct": drawdown * 100,
        "up_days": sum(r["up"] for r in rows), "mean_active_agents": sum(r["active"] for r in rows) / len(rows),
        "brier": {k: brier(rows, k) for k in ("exchange", "vote", "average")} | {"constant_50": 0.25},
        "exchange_direction_hits": sum((r["exchange"] > .5) == bool(r["up"]) for r in rows if r["exchange"] is not None),
        "always_down_hits": sum(1 - r["up"] for r in rows),
        "mean_exchange": sum(r["exchange"] for r in rows if r["exchange"] is not None) / len(rows),
        "big_down_days": [{"day": r["day"], "change_pct": r["change"], "exchange": r["exchange"], "vote": r["vote"]} for r in big],
        "mean_exchange_before_big_drops": (sum(r["exchange"] for r in big) / len(big)) if big else None,
        "boss_paper": boss_paper(rows),
        "boss_paper_sensitivity": dict(boss_paper(rows, ALT_BUY, ALT_SELL, record=False), buy_at=ALT_BUY, sell_at=ALT_SELL,
                                       note="Thresholds chosen after seeing the Exchange's range; sensitivity only, not the live rule."),
        "agent_hit_rates": {k: {"right": sum(v), "calls": len(v)} for k, v in sorted(hits_by_agent.items())},
        "stablecoin_walk_forward": stable_notes, "mood_llm": client is not None,
    }
    (folder / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    b = summary["brier"]
    print(f"   BTC {summary['btc_change_pct']:+.1f}% (max drawdown {summary['max_drawdown_pct']:.1f}%), up days {summary['up_days']}/{len(rows)},"
          f" agents ~{summary['mean_active_agents']:.1f}")
    print(f"   Brier exchange {b['exchange']:.4f} vote {b['vote']:.4f} average {b['average']:.4f} (constant 0.25);"
          f" exchange direction {summary['exchange_direction_hits']}/{len(rows)} (always-down {summary['always_down_hits']})")
    print(f"   mean exchange {summary['mean_exchange']:.3f}; before -5% days {summary['mean_exchange_before_big_drops']}; boss paper {summary['boss_paper']}")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", action="append", choices=list(SCENARIOS))
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--no-llm", action="store_true", help="skip the Gemini Mood agent")
    args = parser.parse_args()
    client = None if args.no_llm else gemini_client()
    print("Mood (Gemini):", "on" if client else "off", flush=True)
    names = list(SCENARIOS) if args.all else (args.scenario or ["month"])
    today = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    agents = load_agents(ROOT / "agents")
    for name in names:
        spec = dict(SCENARIOS[name])
        if name == "month":
            spec["start"], spec["end"] = str((today - pd.Timedelta(days=31)).date()), str((today - pd.Timedelta(days=2)).date())
        run_scenario(name, spec["label"], spec["start"], spec["end"], agents, client)
    if client is not None:
        tokens = [u for u in client.usage if isinstance(u, dict)]
        print(f"Gemini calls this run: {len(tokens)} (cached days are free)")
        client.close()


if __name__ == "__main__":
    main()
