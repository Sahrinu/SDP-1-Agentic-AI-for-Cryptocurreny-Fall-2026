"""Offline PowerShell-to-Python transport tests; HTTP is mocked in PowerShell."""
import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from market_data.prediction_markets.binance_raw_context import assemble

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / 'verification' / 'collect_binance_context.ps1'
POWERSHELL = shutil.which('powershell') or shutil.which('pwsh')
HOURLY = '[[1000,"1","2","1","2","0",2000],[2001,"2","3","2","3","0",3001]]'
OPENING = '[[1000,"1","2","1","2","0",2000]]'
TRADE = '[{"a":123,"p":"1.000","T":123,"m":true,"M":true}]'
HARNESS = r'''param([string]$Collector, [string]$Observation, [string]$OutputPath)
function Invoke-WebRequest {
    param([switch]$UseBasicParsing, $Uri, $TimeoutSec, $ErrorAction)
    if ($Uri -match 'interval=1h') { $content = '__HOURLY__' }
    elseif ($Uri -match 'interval=1m') { $content = '__OPENING__' }
    elseif ($Uri -match '/aggTrades') { $content = '__TRADE__' }
    else { throw 'Unexpected URL' }
    if ($TimeoutSec -ne 15) { throw 'Unexpected timeout' }
    return [pscustomobject]@{Content=$content}
}
& $Collector -Observation $Observation -OutputPath $OutputPath
'''.replace('__HOURLY__',HOURLY).replace('__OPENING__',OPENING).replace('__TRADE__',TRADE)


@unittest.skipUnless(POWERSHELL, 'PowerShell unavailable')
class BinanceRawTransportTests(unittest.TestCase):
    def test_real_powershell_collector_to_python_preserves_case_and_arrays(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp); harness=folder/'harness.ps1'
            harness.write_text(HARNESS,encoding='utf-8')
            observation=folder/'observation.json'
            observation.write_text(json.dumps({'market':{'startAt':'2026-11-14T17:00:00Z'}}),encoding='utf-8')
            manifest=folder/'binance_raw_manifest.json'
            subprocess.run([POWERSHELL,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
                            '-File',str(harness),str(COLLECTOR),str(observation),str(manifest)],
                           capture_output=True,check=True,timeout=20)
            contents=json.loads(manifest.read_text(encoding='utf-8-sig'))
            before={}
            for kind,raw in (('hourly',HOURLY),('opening',OPENING),('trade',TRADE)):
                entry=contents[kind]
                path=folder/entry['raw_file']
                before[path]=path.read_bytes()
                self.assertEqual(before[path],raw.encode('utf-8'))
                self.assertTrue(entry['source_url'].startswith('https://data-api.binance.vision/api/v3/'))
                timestamp=datetime.fromisoformat(entry['received_at'].replace('Z','+00:00'))
                self.assertEqual(timestamp.utcoffset().total_seconds(),0)
            context=assemble(manifest)
            self.assertEqual(context['trade']['data'][0]['m'],True)
            self.assertEqual(context['trade']['data'][0]['M'],True)
            self.assertEqual(context['hourly']['data'],json.loads(HOURLY))
            self.assertEqual(context['opening']['data'],json.loads(OPENING))
            self.assertEqual(context['trade']['source_url'],contents['trade']['source_url'])
            self.assertEqual(context['trade']['received_at'],contents['trade']['received_at'])
            for path,raw in before.items():self.assertEqual(path.read_bytes(),raw)

    def test_malformed_raw_json_is_rejected_without_mutating_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp); manifest=folder/'raw.json'
            entries={'source':'binance:BTC/USDT'}
            for kind in ('hourly','opening','trade'):
                name=manifest.name+'.'+kind+'.raw.json'
                (folder/name).write_text('not JSON' if kind=='trade' else '[]',encoding='utf-8')
                entries[kind]={'source_url':'https://example.invalid','received_at':'2026-01-01T00:00:00Z','raw_file':name}
            manifest.write_text(json.dumps(entries),encoding='utf-8')
            with self.assertRaises(json.JSONDecodeError):assemble(manifest)
            self.assertEqual((folder/entries['trade']['raw_file']).read_text(encoding='utf-8'),'not JSON')
