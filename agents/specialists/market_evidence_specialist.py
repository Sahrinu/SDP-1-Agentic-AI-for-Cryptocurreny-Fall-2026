"""Experimental LLM forecast from market evidence, NOT a direct odds conversion.

The strict Betting Odds specialist and team runner are deliberately unchanged.
Only --live explicitly permits a paid Gemini call; --check makes no model call.
"""
from __future__ import annotations
import argparse
import html
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from agents.schemas import NewsSpecialistResult
from model_clients.structured import parse_model_object
from agents.specialists.betting_odds_specialist import _datetime, _rules
from market_data.prediction_markets.limitless_live import read_book_event

NAME = 'Market Evidence Specialist'
LIMITATIONS = [
    'Uncalibrated model estimate using prediction-market evidence; not the market contract probability.',
    'Different event windows and tie payouts cannot be converted into the requested probability by a known formula.',
    'Forecast accuracy and added value over a neutral baseline have not been established.',
]


class ModelEstimate(BaseModel):
    probability_up: float | None = Field(default=None, ge=0, le=1)
    data_status: Literal['sufficient_data', 'insufficient_data']
    explanation: str = Field(min_length=1)
    target_analysis: str = Field(min_length=1)
    contract_differences: str = Field(min_length=1)
    assumptions: str = Field(min_length=1)
    evidence_ids: list[str]
    limitations: list[str]

    @field_validator('probability_up', mode='before')
    @classmethod
    def numeric_probability(cls, value):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ValueError('Probability must be a JSON number or null')
        return value

    @field_validator('explanation','target_analysis','contract_differences','assumptions')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Explanation sections must not be blank')
        return value.strip()

    @model_validator(mode='after')
    def consistent(self):
        if (self.probability_up is None) != (self.data_status == 'insufficient_data'):
            raise ValueError('Probability and data status disagree')
        return self


def parse_model_estimate(payload):
    """Validate either structured output or the JSON text returned by a bridge."""
    if isinstance(payload, ModelEstimate):
        return payload
    return ModelEstimate.model_validate(parse_model_object(payload))


def prepare_evidence(observation, prediction, target, *, price_source, reference_price_definition):
    """Validate an actual saved frame and expose differences as features, not exclusions."""
    prediction, target = _datetime(prediction), _datetime(target)
    if target-prediction != timedelta(hours=24):
        raise ValueError('Forecast must span exactly 24 hours')
    if not all(isinstance(v,str) and v.strip() for v in (price_source,reference_price_definition)):
        raise ValueError('Explicit target price source and reference convention are required')
    if isinstance(observation, dict) and observation.get('provider') == 'kalshi':
        return _kalshi_features(observation, prediction, target, price_source, reference_price_definition)
    if not isinstance(observation,dict) or observation.get('observation_status') != 'book_observed':
        raise ValueError('A successful saved book observation is required')
    market, event, quote = observation['market'], observation['raw_book_event'], observation['quote']
    if not isinstance(market,dict) or not isinstance(event,dict) or not isinstance(quote,dict):
        raise ValueError('Market, book event and quote summary must be objects')
    if market.get('createdAt') is not None and _datetime(market['createdAt']) > prediction:
        raise ValueError('Market was created after the prediction time')
    if market.get('synthetic') is True or observation.get('synthetic') is True:
        raise ValueError('Synthetic observations are not permitted as model evidence')
    if observation.get('mode') != 'live_read_only_observation':
        raise ValueError('Observation must be explicitly marked as a live read-only capture')
    oracle = market.get('priceOracleMetadata')
    chainlink = isinstance(oracle,dict) and oracle.get('chartSource') == 'chainlink'
    if not chainlink and isinstance(oracle,dict) and (oracle.get('ticker') not in (None,'BTCUSDT') or oracle.get('chartSource') not in (None,'binance')):
        raise ValueError('Market oracle metadata conflicts with Binance BTC/USDT')
    metadata = market.get('metadata')
    chart = metadata.get('chart') if isinstance(metadata,dict) else None
    if not chainlink and isinstance(chart,dict) and (chart.get('symbol') not in (None,'BTCUSDT') or chart.get('source') not in (None,'binance')):
        raise ValueError('Market chart metadata conflicts with Binance BTC/USDT')
    received = _datetime(quote['received_at'])
    # Availability must precede prediction. Old saved results cannot silently become new forecasts.
    if not 0 <= (prediction-received).total_seconds() <= 300:
        raise ValueError('Observation is after prediction or older than five minutes')
    threshold = None
    try:
        if chainlink:
            from market_data.prediction_markets.limitless_targeted_discovery import validate_selected
            start,end,tie = validate_selected(market, prediction)
        else:
            start,end,tie = _rules(market)
    except ValueError:
        if chainlink:
            raise
        from market_data.prediction_markets.threshold_market_evidence import validate_threshold, read_threshold_book
        parent=observation.get('parent_market')
        if isinstance(parent,dict) and parent.get('synthetic') is True:
            raise ValueError('Synthetic observations are not permitted as model evidence')
        start,end,threshold=validate_threshold(market,parent,prediction)
        tie='threshold equality resolves Yes'
        checked=read_threshold_book(event,market,received)
    else:
        checked=read_book_event(event,market,received)
        if (market.get('status') != 'FUNDED' or market.get('expired') is not False
                or market.get('hidden') is True or end <= prediction):
            raise ValueError('Market must have been active and must not end before prediction')
        if (_datetime(market['startAt']) != start or _datetime(market['expirationTimestamp']) != end):
            raise ValueError('Market metadata conflicts with resolution rules')
    if quote != checked:
        raise ValueError('Quote summary does not match the exact saved book frame')
    market_source = 'chainlink:BTC/USD' if chainlink else 'binance:BTC/USDT'
    market_convention = '60s_twap' if chainlink else '1m_candle_high' if threshold is not None else '1m_candle_close'
    market_event = ('endpoint TWAP rises or ties' if chainlink else
                    'any candle high reaches threshold' if threshold is not None else 'endpoint close rises')
    features = {
        'target': {'prediction_time':prediction.isoformat(),'target_time':target.isoformat(),
                   'price_source':price_source,'reference_price_definition':reference_price_definition,
                   'tie_definition':'down','event':'target price strictly greater than prediction price'},
        'evidence': [{'evidence_id':'market-book-001','market_slug':market['slug'],
                      'market_price_source':market_source,
                      'market_price_convention':market_convention,
                      'market_event':market_event,
                      'market_threshold':threshold,
                      'market_start':start.isoformat(),'market_end':end.isoformat(),'market_tie_rule':tie,
                      'elapsed_market_hours':(prediction-start).total_seconds()/3600,
                      'remaining_market_hours':(end-prediction).total_seconds()/3600,
                      'window_matches_target':start==prediction and end==target,
                      'source_matches_target':price_source==market_source,
                      'reference_matches_target':reference_price_definition==market_convention and threshold is None,
                      'tie_matches_target':tie=='down',
                      **{k:checked[k] for k in ('best_bid','best_ask','spread','yes_payout_midpoint',
                         'displayed_bid_shares','displayed_ask_shares','provider_event_time','received_at')}}],
        'limitations':LIMITATIONS+[
            'Current spot price, independently verified market opening price and volatility history are not supplied.',
            'Visible liquidity may disappear; provider timestamp is not the creation time of each resting order.',
        ]}
    evidence = features['evidence'][0]
    evidence['provider_clock_ahead_seconds'] = checked.get('provider_clock_ahead_seconds', 0)
    if evidence['provider_clock_ahead_seconds']:
        features['limitations'].append(
            'Provider clock was ahead of local receipt by less than 0.5 seconds; '
            'both original timestamps are retained, without assuming exact clock synchronization.')
    if threshold is not None:
        features['limitations'].append('The threshold-hit market is a path-dependent event, not the forecast endpoint rise.')
    if not evidence['window_matches_target']:
        features['limitations'].append('Market and forecast windows differ; market odds are evidence for a different event.')
    if not evidence['source_matches_target']:
        features['limitations'].append('Market and forecast price sources differ.')
    if not evidence['reference_matches_target']:
        features['limitations'].append('Market and forecast endpoint price conventions differ.')
    if not evidence['tie_matches_target']:
        features['limitations'].append('Market and forecast tie handling differ.')
    return features


def _kalshi_features(observation, prediction, target, price_source, reference_price_definition):
    from market_data.prediction_markets.kalshi_live import market_terms, trade_quote

    market = observation['market']
    quote = observation['quote']
    if (observation.get('observation_status') != 'trade_observed'
            or observation.get('mode') != 'live_read_only_observation'
            or observation.get('synthetic') or market.get('synthetic')):
        raise ValueError('A live Kalshi trade observation is required')
    ticker, end, threshold = market_terms(market)
    received = _datetime(quote['received_at'])
    if not timedelta(0) <= prediction - received <= timedelta(minutes=5) or prediction >= end:
        raise ValueError('Kalshi trade observation is stale, future-dated, or expired')
    if quote != trade_quote(market, observation['raw_trade'], quote['received_at']):
        raise ValueError('Kalshi trade summary conflicts with its source trade')
    return {
        'target': {'prediction_time': prediction.isoformat(), 'target_time': target.isoformat(),
                   'price_source': price_source, 'reference_price_definition': reference_price_definition,
                   'tie_definition': 'down', 'event': 'target price strictly greater than prediction price'},
        'evidence': [{
            'evidence_id': 'kalshi-trade-001', 'market_provider': 'Kalshi', 'market_ticker': ticker,
            'market_price_source': 'CF Benchmarks Bitcoin Real-Time Index (BRTI)',
            'market_price_convention': 'average of the final 60 seconds',
            'market_event': 'BTC index fixing strictly exceeds a fixed USD threshold',
            'market_threshold': float(threshold), 'market_start': None, 'market_end': end.isoformat(),
            'market_tie_rule': 'threshold equality resolves No', **quote,
        }],
        'limitations': [
            'The Kalshi trade is for a fixed threshold at a daily fixing, while the target compares two Binance prices 24 hours apart.',
            'The YES trade price is a historical execution price. No bid, ask or midpoint was observed.',
            'Any estimate derived from this evidence is uncalibrated.',
        ],
    }


def forecast_kalshi_with_context(observation, prediction, target, *, price_source,
                                 reference_price_definition, reference_price, hourly_bars):
    """Estimate directional probability from a fresh threshold trade and realized volatility."""
    import math
    from statistics import NormalDist, stdev
    import pandas as pd

    features = prepare_evidence(observation, prediction, target, price_source=price_source,
                                reference_price_definition=reference_price_definition)
    trade = features['evidence'][0]
    if not isinstance(hourly_bars, pd.DataFrame) or 'close' not in hourly_bars:
        raise ValueError('Completed Binance hourly candles are unavailable')
    closes = hourly_bars['close'].tail(25)
    if (len(closes) != 25 or not isinstance(closes.index, pd.DatetimeIndex)
            or closes.index.tz is None
            or not timedelta(0) <= prediction - closes.index[-1] <= timedelta(minutes=62)
            or any(b - a != timedelta(hours=1) for a, b in zip(closes.index, closes.index[1:]))):
        raise ValueError('Need 25 consecutive, recent completed Binance hourly closes')
    spot = float(reference_price)
    values = [float(price) for price in closes]
    if not all(math.isfinite(price) and price > 0 for price in [spot, *values]):
        raise ValueError('Invalid Binance closing price')
    volatility = stdev(math.log(b) - math.log(a) for a, b in zip(values, values[1:]))
    if volatility <= 0:
        raise ValueError('Binance hourly volatility is zero')
    fixing_hours = (_datetime(trade['market_end']) - prediction).total_seconds() / 3600
    normal = NormalDist()
    # Infer the cumulative return to the fixing from the threshold and trade price.
    # Carry that cumulative shift into the 24h distribution without scaling up its drift rate.
    market_shift = (math.log(trade['market_threshold']) - math.log(spot)
                    + volatility * math.sqrt(fixing_hours) * normal.inv_cdf(trade['yes_trade_price']))
    probability = normal.cdf(market_shift / (volatility * math.sqrt(24)))
    return NewsSpecialistResult(
        agent=NAME, prediction_time=prediction, target_time=target,
        probability_up=probability, data_status='sufficient_data',
        explanation=(f"Kalshi trade price {trade['yes_trade_price']:.3f} for BTC above "
                     f"${trade['market_threshold']:,.2f} at {trade['market_end']}. "
                     f"Binance reference ${spot:,.2f}, hourly realized volatility {volatility:.3%}. "
                     "The estimate assumes normal log returns and carries the market-implied cumulative "
                     "shift to the Kalshi fixing into the Binance 24-hour distribution."),
        evidence_ids=[trade['evidence_id'], 'binance-hourly-returns', 'binance-reference-close'],
        limitations=[*features['limitations'],
                     'Assumes the Kalshi execution price approximates its threshold event probability, '
                     'the CF Benchmarks and Binance prices have negligible basis, and hourly volatility persists.'],
    )


def build_prompt(features):
    return (
        'You are an experimental Bitcoin market-evidence forecasting specialist. '
        'Estimate the probability of the EXACT target event in target, using only the supplied evidence. '
        'The market contract is a FEATURE, not an equivalent target. The YES payout midpoint is the '
        'average of the best bid and ask: a payout price for the stated contract, not an observed '
        'conditional probability and not necessarily a calibrated probability. If the evidence is a completed trade, '
        'use its execution price and timestamp and do not infer a bid or ask. Explicitly explain how its window and tie '
        'rule differ. Never copy or mechanically rescale that price to a different interval. '
        'A difference in window, threshold or tie treatment is a limitation, not by itself a reason '
        'to abstain. You may use clearly stated forecasting assumptions or hypothetical models; '
        'identify them as assumptions, distinguish them from observed data, and explain their uncertainty. '
        'Both realized_volatility_24h and realized_volatility_7d_scaled_to_24h are dimensionless '
        '24-hour-scaled values: sample standard deviations of hourly log returns times sqrt(24). '
        'The 7d denotes the historical lookback, not a 7-day forecast horizon. These values are '
        'not annualized; do not describe them as annualized. '
        'Do not invent observed prices, volatility, news or claim calibration. '
        'Do not treat liquidity as accuracy. If this evidence cannot support a defensible estimate even '
        'with clearly stated assumptions, '
        'return insufficient_data with null probability; a numeric answer is not compulsory. '
        'Return nonblank explanation, target_analysis, contract_differences and assumptions fields. '
        'Address the requested target, explain the relevant contract differences, and state assumptions '
        'or explicitly state that no assumptions were used. Use only evidence IDs supplied below. '
        'Any numeric output is an UNCALIBRATED MODEL ESTIMATE, '
        'not a market-implied probability of the target.\n'
        +json.dumps(features,ensure_ascii=False)
    )


def format_explanation(value, features):
    """Bind model sections to the actual target and observed contract."""
    target=features['target']; market=features['evidence'][0]
    threshold=market.get('market_threshold')
    threshold_note='' if threshold is None else f'; threshold={threshold}'
    market_time = (f"{market['market_start']} to {market['market_end']}" if market['market_start']
                   else f"fixing at {market['market_end']}")
    return ('Experimental uncalibrated model estimate. '+value.explanation
            +f"\nRequested target: {target['event']}; {target['prediction_time']} to {target['target_time']}; "
             f"source={target['price_source']}; convention={target['reference_price_definition']}; tie={target['tie_definition']}."
            +f"\nObserved market: {market['market_event']}{threshold_note}; {market_time}; "
             f"source={market['market_price_source']}; convention={market['market_price_convention']}; tie={market['market_tie_rule']}."
            +'\nTarget: '+value.target_analysis
            +'\nContract differences: '+value.contract_differences
            +'\nAssumptions: '+value.assumptions)


def forecast_from_market_evidence(observation, *, prediction_time, target_time,
        price_source, reference_price_definition, model_call=None):
    """model_call(prompt) is explicit and receives only validated, bounded features."""
    prediction,target=_datetime(prediction_time),_datetime(target_time)
    def missing(reason):
        return NewsSpecialistResult(agent=NAME,prediction_time=prediction,target_time=target,
            probability_up=None,data_status='insufficient_data',explanation=reason,evidence_ids=[],
            limitations=list(LIMITATIONS))
    try:
        features=prepare_evidence(observation,prediction,target,
            price_source=price_source,reference_price_definition=reference_price_definition)
    except (ValueError,TypeError,KeyError,OverflowError,OSError) as exc:
        return missing('Invalid or insufficient market evidence: '+type(exc).__name__)
    if model_call is None:
        return missing('Evidence validated, but no model was requested; no forecast was generated.')
    try:
        payload=model_call(build_prompt(features))
        estimate=parse_model_estimate(payload)
        allowed={item['evidence_id'] for item in features['evidence']}
        if not set(estimate.evidence_ids).issubset(allowed):
            raise ValueError('Unknown evidence ID')
        if estimate.data_status=='sufficient_data' and not estimate.evidence_ids:
            raise ValueError('A numeric forecast must cite supplied evidence')
    except Exception as exc:
        return missing('Model call or result validation failed: '+type(exc).__name__)
    return NewsSpecialistResult(agent=NAME,prediction_time=prediction,target_time=target,
        probability_up=estimate.probability_up,data_status=estimate.data_status,
        explanation=format_explanation(estimate,features),
        evidence_ids=estimate.evidence_ids,
        limitations=list(dict.fromkeys(features['limitations']+estimate.limitations)))


class MarketEvidenceAdapter:
    """Explicit alternative registration; does not remove the strict-odds guard.

    Use with the runner's specialist_adapters, specialist_inputs and caller-attested
    CSV settlement metadata. A model_call must be explicitly supplied.
    """
    AGENT_NAME = NAME
    kind = "market_evidence"

    def __init__(self, model_call=None):
        self.model_call = model_call

    def forecast(self, day, data):
        def missing(reason):
            return dict(agent=NAME,day=str(day),p_up=None,data_missing=True,
                        data_status='insufficient_data',evidence=[reason],limitations=list(LIMITATIONS))
        try:
            c=data['student2_forecast']
            prediction,target=_datetime(c['prediction_time']),_datetime(c['target_time'])
            if prediction.date().isoformat()!=str(day) or c['tie_definition']!='down':
                return missing('Forecast day/tie definition does not match target')
            result=forecast_from_market_evidence(data['student2_market_observation'],
                prediction_time=prediction,target_time=target,price_source=c['price_source'],
                reference_price_definition=c['reference_price_definition'],model_call=self.model_call)
        except (KeyError,TypeError,ValueError,OverflowError,OSError):
            return missing('Explicit forecast configuration and market observation are required')
        return dict(agent=NAME,day=str(day),p_up=result.probability_up,
            data_missing=result.data_status!='sufficient_data',data_status=result.data_status,
            evidence=[result.explanation,*result.evidence_ids],limitations=result.limitations,
            prediction_time=prediction.isoformat(),target_time=target.isoformat(),
            price_source=c['price_source'],reference_price_definition=c['reference_price_definition'],
            tie_definition='down')


def gemini_call(model):
    """Construct a one-attempt callable, not an implicit default model connection."""
    def call(prompt):
        from google import genai
        from google.genai import types
        key=os.environ.get('GOOGLE_API_KEY') or os.environ.get('GEMINI_API_KEY')
        if not key:
            raise RuntimeError('Gemini key is not configured')
        with genai.Client(api_key=key,http_options=types.HttpOptions(
                timeout=45000,retry_options=types.HttpRetryOptions(attempts=1))) as client:
            response=client.models.generate_content(model=model,contents=prompt,
                config=types.GenerateContentConfig(response_mime_type='application/json',
                    response_schema=ModelEstimate,temperature=0))
            return response.parsed if response.parsed is not None else json.loads(response.text)
    return call


def main():
    parser=argparse.ArgumentParser(description='Experimental model forecast from actual market evidence')
    parser.add_argument('--observation',type=Path,required=True)
    timing=parser.add_mutually_exclusive_group(required=True)
    timing.add_argument('--forecast-now',action='store_true',help='Start a NEW rolling 24-hour forecast at execution time')
    timing.add_argument('--prediction-time',help='Explicit aware start; intended for retrospective check/replay')
    parser.add_argument('--price-source',required=True)
    parser.add_argument('--reference-price-definition',required=True)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check',action='store_true',help='Validate and save features without calling Gemini')
    mode.add_argument('--live',action='store_true',help='Explicitly permit one potentially paid Gemini call')
    parser.add_argument('--model')
    parser.add_argument('--out',type=Path,default=Path('runs/market_evidence'))
    args=parser.parse_args()
    if args.live and (not args.model or not args.forecast_now):
        parser.error('--live requires --model and --forecast-now; old observations cannot be passed off as live')
    prediction=datetime.now(timezone.utc) if args.forecast_now else _datetime(args.prediction_time)
    target=prediction+timedelta(hours=24)
    observation=json.loads(args.observation.read_text(encoding='utf-8-sig'))
    try:
        features=prepare_evidence(observation,prediction,target,
            price_source=args.price_source,reference_price_definition=args.reference_price_definition)
    except (ValueError,TypeError,KeyError,OverflowError,OSError) as exc:
        print('EVIDENCE REJECTED:', str(exc))
        return 1
    folder=args.out/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:10])
    folder.mkdir(parents=True,exist_ok=False)
    (folder/'observation.json').write_text(json.dumps(observation,indent=2),encoding='utf-8')
    (folder/'features.json').write_text(json.dumps(features,indent=2),encoding='utf-8')
    (folder/'prompt.txt').write_text(build_prompt(features),encoding='utf-8')
    (folder/'metadata.json').write_text(json.dumps({'mode':'live_model' if args.live else 'offline_check',
        'retrospective':not args.forecast_now,'model':args.model if args.live else None,
        'prediction_time':prediction.isoformat(),'target_time':target.isoformat(),
        'forecast_kind':'experimental_uncalibrated_model_estimate','calibration_verified':False},indent=2),encoding='utf-8')
    if args.check:
        print('EVIDENCE CHECK PASSED — no model call or probability produced.')
    else:
        result=forecast_from_market_evidence(observation,prediction_time=prediction,target_time=target,
            price_source=args.price_source,reference_price_definition=args.reference_price_definition,
            model_call=gemini_call(args.model))
        (folder/'result.json').write_text(result.model_dump_json(indent=2),encoding='utf-8')
        print(result.model_dump_json(indent=2))
    print('Saved:',folder.resolve())
    return 0


if __name__=='__main__':raise SystemExit(main())
