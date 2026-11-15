"""Small read-only HTTP transport with verified native certificate trust."""

import json
import ssl
from urllib.request import Request, urlopen


def get_bytes(url, *, timeout=15, max_bytes=8_000_000, headers=None):
    try:
        import truststore
        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        context = ssl.create_default_context()
    request = Request(url, headers={"User-Agent": "BitcoinAgentExchange-research/0.1", **(headers or {})})
    with urlopen(request, timeout=timeout, context=context) as response:
        payload = response.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError("provider response exceeded the size limit")
    return payload


def get_json(url, **kwargs):
    return json.loads(get_bytes(url, **kwargs))
