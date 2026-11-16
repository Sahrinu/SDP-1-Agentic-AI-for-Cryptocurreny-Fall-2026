"""Orchestrate one fresh observation and price-backed demo on Windows."""
import argparse,json,os,subprocess,sys,uuid
from datetime import datetime,timedelta,timezone
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--env-file',type=Path)
    p.add_argument('--model');p.add_argument('--live',action='store_true')
    a=p.parse_args();root=Path(__file__).resolve().parents[1]
    if a.live and not a.model:p.error('--live requires --model')
    if a.live:
        if a.env_file:
            from dotenv import dotenv_values
            values=dotenv_values(a.env_file)
            for key in ('GOOGLE_API_KEY','GEMINI_API_KEY'):
                if values.get(key):os.environ[key]=values[key]
        if not (os.environ.get('GOOGLE_API_KEY') or os.environ.get('GEMINI_API_KEY')):
            p.error('No Gemini key loaded; no collection or model call made')
    sys.path.insert(0, str(root))
    from market_data.prediction_markets.limitless_targeted_discovery import DiscoveryRequestError, discover
    from market_data.prediction_markets.limitless_live import observe_market
    from market_data.prediction_markets.binance_raw_context import assemble
    from agents.specialists import market_evidence_specialist as model
    from agents.specialists import market_evidence_with_prices as enriched
    from agents.adapters import Student2Adapter
    folder=root/'runs'/'price_context_demo'/uuid.uuid4().hex;folder.mkdir(parents=True)
    def ps(script,*args):subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(root/script),*map(str,args)],cwd=root,check=True)
    try:
        market, discovery = discover()
        (folder/'targeted_discovery.json').write_text(json.dumps(discovery,indent=2),encoding='utf-8')
        print('Bounded, non-exhaustive discovery selected:',market['slug'],flush=True)
        report=observe_market(targeted_market=market)
        observation_dir=folder/'observation';observation_dir.mkdir()
        observation=observation_dir/'observation.json'
        observation.write_text(json.dumps(report,indent=2),encoding='utf-8')
        print('Observation status:',report['observation_status'],'stage:',report.get('stage'),flush=True)
        if report.get('observation_status')!='book_observed':
            raise RuntimeError('No validated live market book')
        raw_manifest=folder/'binance_raw_manifest.json'
        ps('verification/collect_binance_context.ps1','-Observation',observation,'-OutputPath',raw_manifest)
        context=folder/'price_context.json'
        assembled=assemble(raw_manifest)
        with context.open('x',encoding='utf-8') as output:
            json.dump(assembled,output,indent=2)
        prediction=datetime.now(timezone.utc);target=prediction+timedelta(hours=24)
        forecast=folder/'forecast';forecast.mkdir()
        metadata={'prediction_time':prediction.isoformat(),'target_time':target.isoformat(),
                  'price_source':'binance:BTC/USDT','reference_price_definition':'spot_price',
                  'tie_definition':'down','mode':'live_model' if a.live else 'evidence_check',
                  'model':a.model if a.live else None,'forecast_kind':'experimental_uncalibrated_model_estimate',
                  'team_settlement_contract_established':False}
        (forecast/'metadata.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
        features=enriched.prepare(report,assembled,prediction)
        (forecast/'features.json').write_text(json.dumps(features,indent=2),encoding='utf-8')
        (forecast/'prompt.txt').write_text(model.build_prompt(features),encoding='utf-8')
        if a.live:
            attempts=0
            def one_call(prompt):
                nonlocal attempts
                if attempts:raise RuntimeError('More than one model attempt is not permitted')
                attempts+=1
                return model.gemini_call(a.model)(prompt)
            adapter=Student2Adapter('betting_odds',model_client=one_call)
            data={'student2_forecast':{k:metadata[k] for k in ('prediction_time','target_time','price_source','reference_price_definition','tie_definition')},
                  'student2_market_observation':report,'student2_markets_observed_at':report['finished_at_utc'],
                  'student2_price_context':assembled}
            result=adapter.forecast(prediction.date().isoformat(),data)
            (forecast/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            print(json.dumps(result,indent=2));print('Model attempts:',attempts)
        else:
            print('Price and market evidence validated. No model call made.')
        print('Forecast artifacts:',forecast)
    except (OSError,ValueError,RuntimeError,subprocess.CalledProcessError) as e:
        print('Stopped without retry:',type(e).__name__)
        if isinstance(e,DiscoveryRequestError):print('Discovery:',str(e))
        print('Evidence folder:',folder);return 1
    print('Completed. Evidence folder:',folder);return 0
if __name__=='__main__':raise SystemExit(main())
