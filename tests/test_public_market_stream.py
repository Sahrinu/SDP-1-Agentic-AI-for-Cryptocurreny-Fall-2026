"""Public protocol, freshness and cleanup; injected sockets make no network calls."""
from datetime import timedelta
import json
import ssl
import pytest
from market_data.prediction_markets import public_stream as stream
from market_data.prediction_markets.limitless_live import BookTracker,read_book_event
from tests.test_limitless_live_reader import frame,market,NOW


class Clock:
    @staticmethod
    def now(tz):return NOW


class Socket:
    def __init__(self,packets):self.packets=iter(packets);self.sent=[];self.closed=False
    def recv(self):return next(self.packets,'')
    def send(self,packet):self.sent.append(packet)
    def settimeout(self,value):assert 0<value<=20
    def close(self):self.closed=True


def packets(*events):
    return ['0'+json.dumps({'sid':'public-engine-session'}),'40/markets,'+json.dumps({'sid':'public-market-session'}),
            '2',*[('42/markets,'+json.dumps(['orderbookUpdate',event])) for event in events]]


def test_public_handshake_ping_two_book_versions_and_verified_tls(monkeypatch):
    monkeypatch.setattr(stream,'datetime',Clock)
    first=frame();second=frame();second['version']=first['version']+1
    socket=Socket(packets(first,second));options={}
    def connect(url,**kwargs):options.update(url=url,**kwargs);return socket
    quote,event=stream.capture_public_book(market(),BookTracker(market(),NOW-timedelta(seconds=1)),connection_factory=connect)
    assert quote['version']==second['version'] and event==second
    assert socket.sent[0]=='40/markets,' and socket.sent[2]=='3'
    assert json.loads(socket.sent[1][len('42/markets,'):])[0]=='subscribe_market_prices'
    assert options['sslopt']['context'].verify_mode==ssl.CERT_REQUIRED
    assert socket.closed


@pytest.mark.parametrize('events',[[],['1'],['44/markets,{"message":"rejected"}'],
    ['42/markets,'+json.dumps(['error',{'message':'untrusted provider error'}])]])
def test_rejected_or_closed_stream_never_returns_quote_and_always_closes(monkeypatch,events):
    monkeypatch.setattr(stream,'datetime',Clock)
    socket=Socket(['0{"sid":"a"}','40/markets,{"sid":"b"}',*events])
    with pytest.raises((ValueError,RuntimeError)):
        stream.capture_public_book(market(),BookTracker(market(),NOW),connection_factory=lambda *a,**k:socket)
    assert socket.closed


def test_initial_snapshot_alone_is_not_a_live_change(monkeypatch):
    monkeypatch.setattr(stream,'datetime',Clock)
    socket=Socket(packets(frame()))
    with pytest.raises(ValueError):stream.capture_public_book(market(),BookTracker(market(),NOW),connection_factory=lambda *a,**k:socket)
    assert socket.closed


def test_small_provider_clock_skew_retains_both_original_times():
    event=frame();event['timestamp']=(NOW+timedelta(milliseconds=200)).isoformat()
    quote=read_book_event(event,market(),NOW)
    assert quote['received_at']==NOW.isoformat()
    assert quote['provider_event_time']==event['timestamp']
    assert quote['provider_clock_ahead_seconds']==pytest.approx(.2)
    event['timestamp']=(NOW+timedelta(milliseconds=501)).isoformat()
    with pytest.raises(ValueError):read_book_event(event,market(),NOW)
