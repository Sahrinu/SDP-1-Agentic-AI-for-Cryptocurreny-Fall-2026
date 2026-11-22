"""Frozen-input attack comparisons with injected models, not live inference."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
from types import SimpleNamespace
import pytest
from experiments.news_robustness import ATTACKS,make_attack,run_comparisons
from agents.specialists.news_specialist import build_gemini_prompt

NOW=datetime(2026,11,18,9,tzinfo=timezone.utc)


def inputs():
    return {'student2_news_articles':[{'evidence_id':'clean-news','title':'Bitcoin trading demand changes',
        'summary':'BTC spot demand rose today according to the supplied report.',
        'publication_timestamp':(NOW-timedelta(hours=1)).isoformat(),'publisher':'Test fixture'}],
        'student2_news_observed_at':NOW.isoformat(),
        'student2_mood_readings':[{'evidence_id':'fng-test','timestamp':NOW.isoformat(),
            'timestamp_unix':int(NOW.timestamp()),'score':40,'classification':'Fear','source':'Test fixture'}],
        'student2_mood_observed_at':NOW.isoformat()}


class Model:
    model='injected-test-only'
    def __init__(self,bad=False):self.responses=[];self.usage=[];self.bad=bad
    def invoke(self,messages):
        prompt=messages[0].content
        mood='fng_readings:' in prompt
        attacked='local-attack-' in prompt
        obj={'agent':'mood_specialist' if mood else 'news_specialist',
            'prediction_time':NOW.isoformat(),'target_time':(NOW+timedelta(hours=24)).isoformat(),
            'probability_up':True if self.bad else .45 if mood else .2 if attacked else .6,
            'explanation':'Injected unit-test estimate only.', 'data_status':'sufficient_data',
            'evidence_ids':['fng-test' if mood else 'clean-news'],'limitations':[]}
        self.responses.append({'prompt':prompt,'parsed':obj});self.usage.append({'model':self.model})
        return SimpleNamespace(content=[{'type':'text','text':json.dumps(obj)}])


def test_repeated_pairs_freeze_inputs_keep_mood_control_and_save_provenance(tmp_path):
    data=inputs();before=deepcopy(data);model=Model()
    output=tmp_path/'experiment'
    summary=run_comparisons(data,model,output,prediction=NOW,repeats=2)
    assert data==before and len(model.responses)==8
    by_kind={r['kind']:r for r in summary}
    assert by_kind['news']['mean_absolute_shift']==pytest.approx(.4)
    assert by_kind['mood']['mean_absolute_shift']==0
    assert all(r['comparable_pairs']==2 for r in summary)
    truth=json.loads((output/'bearish_claim_truth.json').read_text())
    assert truth['synthetic'] and truth['publication_time_basis']=='fabricated scenario timestamp'
    attacked=json.loads((output/'bearish_claim_inputs.json').read_text())
    assert attacked['student2_mood_readings']==before['student2_mood_readings']
    assert len(attacked['student2_news_articles'])==2
    manifest=json.loads((output/'manifest.json').read_text())
    assert manifest['mode']=='injected_test' and manifest['planned_model_calls']==8
    assert len(list(output.glob('*-news-*.json')))==4
    # Second repeat reverses arm order, while clean baselines are re-run.
    assert 'local-attack-' not in model.responses[0]['prompt']
    assert 'local-attack-' in model.responses[1]['prompt']
    assert 'local-attack-' in model.responses[4]['prompt']


def test_failures_are_retained_not_reported_as_zero_shift(tmp_path):
    summary=run_comparisons(inputs(),Model(bad=True),tmp_path/'bad',prediction=NOW,repeats=2)
    assert all(r['comparable_pairs']==0 and r['incomplete_pairs']==2 for r in summary)
    assert all(r['mean_absolute_shift'] is None for r in summary)


@pytest.mark.parametrize('name',[name for name in ATTACKS if name != 'hidden_text'])
def test_attacks_have_cutoff_timestamps_and_are_not_labelled_live_yahoo(name):
    attacked,truth=make_attack(inputs(),NOW,name)
    article=attacked['student2_news_articles'][-1]
    assert datetime.fromisoformat(article['publication_timestamp'])<=NOW
    expected_source = 'simulated_prior_session_note' if name == 'poisoned_memory' else 'unverified_bulletin'
    assert article['source_type'] == expected_source
    assert truth['synthetic']
    prompt=build_gemini_prompt([article],NOW,NOW+timedelta(hours=24))['content']
    assert '0 live_yahoo record(s)' in prompt
    assert '1 other or unverified record(s)' in prompt


def test_hidden_text_marker_changes_only_raw_text():
    clean = inputs()
    attacked, truth = make_attack(clean, NOW, 'hidden_text')
    before = clean['student2_news_articles'][0]
    after = attacked['student2_news_articles'][0]
    assert {key: value for key, value in after.items() if key != 'text'} == {
        key: value for key, value in before.items() if key != 'text'}
    assert after['text'].endswith(truth['fixture_text'])
    prompt = build_gemini_prompt([after], NOW, NOW + timedelta(hours=24))['content']
    assert 'REPLAY-HIDDEN-TEXT-MARKER' in prompt


def test_future_observation_rejected_before_any_model_call(tmp_path):
    data=inputs();data['student2_news_observed_at']=(NOW+timedelta(seconds=1)).isoformat()
    model=Model()
    with pytest.raises(ValueError):run_comparisons(data,model,tmp_path/'future',prediction=NOW,repeats=2)
    assert not model.responses
