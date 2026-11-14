"""Assemble saved raw Binance HTTP responses without case-insensitive decoding.

The PowerShell transport records response bytes, URL and receipt time only.
This module parses JSON, preserving distinct keys such as ``m`` and ``M``.
Price, source and timestamp checks remain in market_price_context.validate.
"""
from __future__ import annotations

import json
from pathlib import Path

MAX_RAW_BYTES = 2_000_000
KINDS = ('hourly', 'opening', 'trade')


def assemble(manifest_path):
    """Read one saved manifest and its sibling raw responses into a context.

    The input files are never modified. Raw file names must be those generated
    by the collector, and must resolve to the manifest directory.
    """
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    if not isinstance(manifest, dict) or manifest.get('source') != 'binance:BTC/USDT':
        raise ValueError('Invalid raw response manifest source')
    base = manifest_path.parent.resolve()
    result = {'source': manifest['source']}
    for kind in KINDS:
        entry = manifest.get(kind)
        if not isinstance(entry, dict):
            raise ValueError('Invalid raw response manifest entry')
        name = entry.get('raw_file')
        expected = manifest_path.name + '.' + kind + '.raw.json'
        if not isinstance(name, str) or name != expected:
            raise ValueError('Invalid raw response file name')
        path = base / name
        if path.resolve().parent != base:
            raise ValueError('Raw response must remain in the manifest directory')
        if path.stat().st_size > MAX_RAW_BYTES:
            raise ValueError('Raw response too large')
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data, list):
            raise ValueError('Expected a raw JSON array')
        if not isinstance(entry.get('source_url'), str) or not isinstance(entry.get('received_at'), str):
            raise ValueError('Missing raw response URL or receipt time')
        result[kind] = {'source_url': entry['source_url'],
                        'received_at': entry['received_at'], 'data': data}
    return result
