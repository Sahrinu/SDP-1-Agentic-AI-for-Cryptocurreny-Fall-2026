"""One explicit experimental model attempt with verified market and price evidence."""
import argparse,json,uuid
from datetime import datetime,timezone,timedelta
from pathlib import Path
from agents.specialists import market_evidence_specialist as model
from market_data.prediction_markets import market_price_context as prices
from agents.schemas import NewsSpecialistResult

def prepare(observation,context,prediction):
    features=model.prepare_evidence(observation,prediction,prediction+timedelta(hours=24),
        price_source='binance:BTC/USDT',reference_price_definition='spot_price')
    features['evidence'].append(prices.validate(context,observation,prediction))
    features['limitations']=[s for s in features['limitations'] if not s.startswith('Current spot price, independently')]
    features['limitations']+=['Latest trade is a timestamped spot proxy, not an exact prediction-time fixing.',
        'Historical returns and volatility cannot guarantee future outcomes or calibrate a language model.']
    return features

def evaluate(features,call):
    payload=call(model.build_prompt(features))
    value=model.ModelEstimate.model_validate(payload)
    ids={e['evidence_id'] for e in features['evidence']}
    if not set(value.evidence_ids)<=ids:raise ValueError('Unknown evidence citation')
    if value.probability_up is not None and not value.evidence_ids:raise ValueError('Missing evidence citations')
    return NewsSpecialistResult(agent=model.NAME,prediction_time=features['target']['prediction_time'],
        target_time=features['target']['target_time'],probability_up=value.probability_up,
        data_status=value.data_status,explanation=model.format_explanation(value,features),
        evidence_ids=value.evidence_ids,limitations=features['limitations']+value.limitations)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--observation',type=Path,required=True)
    p.add_argument('--context',type=Path,help='Saved Binance capture; otherwise collect public data once')
    p.add_argument('--live',action='store_true',help='Permit one paid Gemini attempt; otherwise only validate evidence')
    p.add_argument('--model');p.add_argument('--out',type=Path,default=Path('runs/market_evidence_with_prices'))
    a=p.parse_args()
    if a.live and not a.model:p.error('--live requires --model')
    folder=a.out/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:10]);folder.mkdir(parents=True)
    def save(name,data):(folder/name).write_text(json.dumps(data,indent=2),encoding='utf-8')
    stage='price_collection'
    try:
        observation=json.loads(a.observation.read_text(encoding='utf-8-sig'));save('observation.json',observation)
        context=json.loads(a.context.read_text(encoding='utf-8-sig')) if a.context else prices.collect(observation)
        save('price_context.json',context)
        prediction=datetime.now(timezone.utc);stage='evidence_validation'
        features=prepare(observation,context,prediction);save('features.json',features)
        (folder/'prompt.txt').write_text(model.build_prompt(features),encoding='utf-8')
        save('metadata.json',dict(prediction_time=prediction.isoformat(),target_time=(prediction+timedelta(hours=24)).isoformat(),
            mode='live_model' if a.live else 'evidence_check',model=a.model,forecast_kind='experimental_uncalibrated_model_estimate'))
        if a.live:
            stage='model_call_or_validation';result=evaluate(features,model.gemini_call(a.model))
            save('result.json',result.model_dump(mode='json'));print(result.model_dump_json(indent=2))
        else:print('Price and market evidence validated. No model call made.')
    except Exception as e:
        # Do not expose provider response text, URLs with secrets, or credentials.
        save('failure.json',dict(stage=stage,error_type=type(e).__name__))
        print('Stopped:',stage,type(e).__name__);print('Saved:',folder.resolve());return 1
    print('Saved:',folder.resolve());return 0

if __name__=='__main__':raise SystemExit(main())
