"""Bounded public Engine.IO v4 / Socket.IO market subscription over WebSocket.

Only public text packets are supported: no auth, upgrades, binary attachments,
orders, reconnects or retry loops. Both transports use the same book validator.
"""
from datetime import datetime, timezone
import json
import ssl
import time

URL = 'wss://ws.limitless.exchange/socket.io/?transport=websocket&EIO=4'


def capture_public_book(market, tracker, *, timeout_seconds=20, connection_factory=None):
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 1 <= timeout_seconds <= 30:
        raise ValueError('Stream timeout must be between 1 and 30 seconds')
    if connection_factory is None:
        import websocket
        connection_factory = websocket.create_connection
    try:
        import truststore
        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        context = ssl.create_default_context()
    deadline = time.monotonic() + timeout_seconds
    socket = connection_factory(URL, timeout=timeout_seconds,
        sslopt={'context': context}, enable_multithread=True)
    phase = 'engine_open'
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('No qualifying fresh book within observation window')
            socket.settimeout(remaining)
            packet = socket.recv()
            if not isinstance(packet, str) or not packet or len(packet) > 1_000_000:
                raise ValueError('Closed, binary or oversized public stream packet')
            if packet == '2':
                socket.send('3')  # Engine.IO server heartbeat.
                continue
            if packet == '1' or packet.startswith('41/markets'):
                raise RuntimeError('Public stream disconnected before a fresh book')
            if phase == 'engine_open':
                if not packet.startswith('0'):
                    raise ValueError('Missing Engine.IO open packet')
                hello = json.loads(packet[1:])
                if not isinstance(hello, dict) or not isinstance(hello.get('sid'), str) or not hello['sid']:
                    raise ValueError('Invalid Engine.IO handshake')
                socket.send('40/markets,')
                phase = 'namespace_open'
                continue
            if phase == 'namespace_open':
                if not packet.startswith('40/markets,'):
                    raise ValueError('Public namespace handshake rejected')
                hello = json.loads(packet[len('40/markets,'):])
                if not isinstance(hello, dict) or not hello.get('sid'):
                    raise ValueError('Invalid public namespace handshake')
                socket.send('42/markets,' + json.dumps([
                    'subscribe_market_prices', {'marketSlugs': [market['slug']]}]))
                phase = 'subscribed'
                continue
            if not packet.startswith('42/markets,'):
                raise ValueError('Unsupported public stream packet')
            event = json.loads(packet[len('42/markets,'):])
            if not isinstance(event, list) or len(event) != 2 or not isinstance(event[0], str):
                raise ValueError('Malformed public stream event')
            name, payload = event
            if name in ('error', 'exception', 'connect_error'):
                raise RuntimeError('Public subscription rejected')
            if name == 'orderbookUpdate':
                received = datetime.now(timezone.utc)
                quote = tracker.accept(payload, received)
                if quote is not None:
                    return quote, payload
            # System notifications and other markets' channels are not quotes.
    finally:
        socket.close()
