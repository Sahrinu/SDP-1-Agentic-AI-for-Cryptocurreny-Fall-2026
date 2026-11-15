"""Evidence-bound debate and reproducible baselines, without real model calls."""
import json
import math
import pandas as pd
import pytest
from experiments.debate import debate
from experiments.evaluation import paired_brier,trading_baselines
from dashboard.service import json_safe


def snapshot():
    return {'contract':{'target_time':'2026-11-16T10:00:00Z'},'agents':[
        {'role':r,'p_up':.6,'evidence':['synthetic test'],'audit':{'passed':True}} for r in ('Chart','Mood','News')]}


def test_debate_three_calls_and_contract_is_shared():
    calls=[]
    def model(prompt):
        obj=json.loads(prompt[prompt.index('{'):]);calls.append(obj)
        return json.dumps({'argument':'Uses only supplied evidence','evidence_ids':['0'],'p_up':.6})
    result=debate(snapshot(),model)
    assert result['available'] and len(calls)==3
    assert [c['role'] for c in calls]==['Bull','Bear','Judge']
    assert calls[-1]['snapshot']['contract']==snapshot()['contract']
    assert len(calls[-1]['prior_arguments'])==2

@pytest.mark.parametrize('bad',[{'argument':'claim','evidence_ids':['unknown'],'p_up':.5},
    {'argument':'claim','evidence_ids':['0'],'p_up':True},
    {'argument':'claim','evidence_ids':['0'],'p_up':float('nan')}])
def test_debate_rejects_invalid_judge_or_references(bad):
    with pytest.raises(ValueError):debate(snapshot(),lambda p:bad)


def test_insufficient_reports_never_calls_model():
    assert not debate({'agents':[],'contract':{}},lambda p:pytest.fail())['available']


def test_bootstrap_reproducible_and_missing_not_neutral():
    first=paired_brier([1,0,None,.5],[1,0,1,1],repeats=100)
    assert first==paired_brier([1,0,None,.5],[1,0,1,1],repeats=100)
    assert first['n']==3 and first['brier']==pytest.approx(.25/3)
    assert first['ci95'][0]<=first['brier']<=first['ci95'][1]
    assert paired_brier([None],[1])['brier'] is None


def test_baselines_next_day_costs_and_future_invariance(tmp_path):
    index=pd.date_range('2026-01-01',periods=80,tz='UTC')
    frame=pd.DataFrame({'open':[100+i for i in range(80)],'close':[100.5+i for i in range(80)]},index=index)
    start,end=str(index[50].date()),str(index[60].date())
    first=trading_baselines(frame,start=start,end=end,output=tmp_path/'a')
    assert all(float(x['total_fees_usd'])>0 for x in first)
    trade=pd.read_csv(tmp_path/'a/capped_allocation/trades.csv').iloc[0]
    assert pd.Timestamp(trade.timestamp)==index[51]
    assert float(trade.market_price_usd)==151
    frame.loc[index[70]:,'close']=1e9
    assert trading_baselines(frame,start=start,end=end,output=tmp_path/'b')==first
    with pytest.raises(FileExistsError):trading_baselines(frame,start=start,end=end,output=tmp_path/'a')


def test_json_missing_metrics_are_null():
    assert json.dumps(json_safe({'a':[float('nan'),float('inf'),.5]}),allow_nan=False)=='{"a": [null, null, 0.5]}'
