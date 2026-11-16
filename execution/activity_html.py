"""Offline activity dashboard for saved forecast and paper-execution events."""
from __future__ import annotations

import html
import json
import math
from pathlib import Path


def _text(value):
    if value is None:
        return 'Not provided'
    if isinstance(value,(dict,list,tuple)):
        return json.dumps(value,ensure_ascii=False,default=str)
    return str(value)


def _probability(value):
    if value is None:
        return 'Unavailable'
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=1:
        return 'Invalid'
    return f'{value*100:.1f}%'


def render_activity_html(events, *, title='Forecast and paper-execution activity', synthetic=False):
    """Render event values without substituting a probability or executing code."""
    if not isinstance(events,list):
        raise ValueError('Activity events must be a list')
    def esc(value):return html.escape(_text(value),quote=True)
    rows=[]
    for event in events:
        if not isinstance(event,dict):raise ValueError('Each activity event must be an object')
        note=[]
        for name in ('scenario','reason','explanation','evidence','limitations','target_time','price_source','reference_price_definition','tie_definition','request_timestamp','influence_cap_feasible','details'):
            if event.get(name) is not None:
                note.append(f'{name}: {_text(event[name])}')
        cells=(event.get('timestamp'),event.get('kind'),event.get('agent'),event.get('status'),
               _probability(event.get('probability_up')),'\n'.join(note))
        rows.append('<tr>'+''.join('<td>'+esc(cell)+'</td>' for cell in cells)+'</tr>')
    banner=('<p class="banner">SYNTHETIC OFFLINE DEMO — fabricated inputs and paper execution only.</p>'
            if synthetic else '')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>'+esc(title)+'</title><style>body{font:16px system-ui;margin:2rem;color:#17202a}'
            'table{border-collapse:collapse;width:100%}th,td{border:1px solid #cbd5e1;padding:.55rem;'
            'text-align:left;vertical-align:top}td{white-space:pre-wrap}th{background:#e8eef5}'
            '.banner{background:#fff0c2;padding:.8rem;font-weight:600}</style></head><body><h1>'+esc(title)+'</h1>'
            +banner+'<p>Missing forecasts are shown as unavailable; no 50% value is substituted.</p>'
            '<table><thead><tr><th>Timestamp</th><th>Stage</th><th>Agent</th><th>Status</th>'
            '<th>Forecast</th><th>Explanation and details</th></tr></thead><tbody>'
            +''.join(rows)+'</tbody></table></body></html>')


def write_activity_html(events, path, *, synthetic=False):
    """Create a new standalone report without overwriting an existing file."""
    path=Path(path)
    with path.open('x',encoding='utf-8') as output:
        output.write(render_activity_html(events,synthetic=synthetic))
    return path
