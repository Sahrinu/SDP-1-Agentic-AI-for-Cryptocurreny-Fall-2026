"""Offline collector tests: Invoke-RestMethod is replaced by an in-process mock."""
import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / 'student2' / 'verification' / 'collect_limitless_markets.ps1'
POWERSHELL = shutil.which('powershell') or shutil.which('pwsh')

HARNESS = r'''param([string]$Collector, [string]$OutputPath, [string]$Scenario)
$global:requests = [System.Collections.Generic.List[object]]::new()
$global:scenario = $Scenario
function Invoke-RestMethod {
    param($Method, $Uri, $TimeoutSec, $ErrorAction)
    $global:requests.Add([pscustomobject]@{method=$Method; uri=$Uri; timeout=$TimeoutSec})
    if ($Uri -match 'page=2&') {
        if ($global:scenario -eq 'failure') { throw 'provider-secret-body' }
        $id = if ($global:scenario -eq 'duplicate') { 1 } else { 26 }
        $total = if ($global:scenario -eq 'changed') { 27 } else { 26 }
        return [pscustomobject]@{data=@([pscustomobject]@{id=$id});totalMarketsCount=$total}
    }
    $items = @(1..25 | ForEach-Object { [pscustomobject]@{id=$_} })
    return [pscustomobject]@{data=$items;totalMarketsCount=26}
}
$errorMessage = $null
try { & $Collector -OutputPath $OutputPath -MaxPages 3 -PageTimeoutSeconds 5 -TotalTimeoutSeconds 20 | Out-Null }
catch { $errorMessage = $_.Exception.Message }
[pscustomobject]@{requests=@($global:requests.ToArray());error=$errorMessage} | ConvertTo-Json -Depth 10 -Compress
'''


@unittest.skipUnless(POWERSHELL, 'PowerShell is unavailable')
class PowerShellCollectorTests(unittest.TestCase):
    def run_collector(self, tmp, scenario):
        script=Path(tmp)/'harness.ps1'; script.write_text(HARNESS,encoding='utf-8')
        output=Path(tmp)/'listing.json'
        proc=subprocess.run([POWERSHELL,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
                             '-File',str(script),str(COLLECTOR),str(output),scenario],
                            text=True,capture_output=True,timeout=20,check=True)
        return json.loads(proc.stdout),output

    def test_successful_pagination_saves_complete_responses_and_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            result,output=self.run_collector(tmp,'success')
            listing=json.loads(output.read_text(encoding='utf-8-sig'))
        self.assertIsNone(result['error'])
        self.assertEqual([r['uri'] for r in result['requests']],
                         [f'https://api.limitless.exchange/markets/active?page={n}&limit=25' for n in (1,2)])
        self.assertTrue(all(r['method']=='Get' and 1 <= r['timeout'] <= 5 for r in result['requests']))
        self.assertEqual([p['page'] for p in listing['pages']],[1,2])
        self.assertEqual(sum(len(p['response']['data']) for p in listing['pages']),26)
        stamps=[datetime.fromisoformat(listing['started_at_utc'].replace('Z','+00:00'))]
        stamps += [datetime.fromisoformat(p['retrieved_at_utc'].replace('Z','+00:00')) for p in listing['pages']]
        stamps.append(datetime.fromisoformat(listing['finished_at_utc'].replace('Z','+00:00')))
        self.assertEqual(stamps,sorted(stamps))
        self.assertTrue(all(stamp.utcoffset().total_seconds()==0 for stamp in stamps))

    def test_failed_or_inconsistent_page_is_not_retried_or_saved(self):
        for scenario in ('failure','duplicate','changed'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as tmp:
                result,output=self.run_collector(tmp,scenario)
                self.assertEqual(len(result['requests']),2)
                self.assertIsNotNone(result['error'])
                self.assertNotIn('provider-secret-body',result['error'])
                self.assertFalse(output.exists())

    def test_existing_file_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'listing.json'; output.write_text('preserve me',encoding='utf-8')
            result,_=self.run_collector(tmp,'success')
            self.assertIsNotNone(result['error'])
            self.assertEqual(output.read_text(encoding='utf-8'),'preserve me')
