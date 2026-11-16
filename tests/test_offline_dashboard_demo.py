"""The offline dashboard uses isolated synthetic fixtures and rejects actions."""
import json
import socket
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from examples.offline_dashboard_demo import forecast_fixture, make_server, paper_fixtures


def test_fixtures_are_offline_and_paper_outcomes_reconcile(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError('Synthetic fixture attempted a network connection')
    monkeypatch.setattr(socket.socket, 'connect', no_network)
    forecast, activity = forecast_fixture()
    empty, empty_activity = forecast_fixture(True)
    paper = paper_fixtures(tmp_path)
    assert forecast['mode'] == 'synthetic offline fixture'
    assert forecast['active_count'] == 4 and activity['abstained_count'] == 9
    assert empty['methods']['exchange'] is None and empty_activity['abstained_count'] == 13
    assert [paper[k]['risk']['status'] for k in ('executed', 'blocked', 'halted', 'hold')] == [
        'executed', 'blocked', 'halted', 'no_trade']
    assert '25%' in paper['blocked']['risk']['reason']
    assert paper['executed']['portfolio'] == paper['blocked']['portfolio']
    assert not paper['unconfigured']['breaker']['configured']


def test_offline_http_displays_banner_and_rejects_actions():
    server, _ = make_server('mixed', 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f'http://127.0.0.1:{server.server_port}'
        with urlopen(base + '/') as response:
            html = response.read().decode()
        assert 'SYNTHETIC OFFLINE FIXTURE' in html
        assert 'forecast and paper cases are separate examples' in html
        with urlopen(base + '/api/state') as response:
            state = json.load(response)
        assert state['forecast']['active_count'] == 4
        for action in ('collect', 'forecast', 'paper', 'replay', 'debate', 'gemini'):
            with pytest.raises(HTTPError) as error:
                urlopen(Request(base + '/api/' + action, data=b'', method='POST'))
            assert error.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
