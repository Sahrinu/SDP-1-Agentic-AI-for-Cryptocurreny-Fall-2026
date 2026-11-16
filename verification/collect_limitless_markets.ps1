[CmdletBinding()]
param(
    [string] $OutputPath,
    [ValidateRange(1, 100)] [int] $MaxPages = 100,
    [ValidateRange(1, 15)] [int] $PageTimeoutSeconds = 15,
    [ValidateRange(1, 300)] [int] $TotalTimeoutSeconds = 30
)
$ErrorActionPreference = 'Stop'
$endpoint = 'https://api.limitless.exchange/markets/active'
$startedAt = [DateTimeOffset]::UtcNow.ToString('o')
$clock = [System.Diagnostics.Stopwatch]::StartNew()
$pages = [System.Collections.Generic.List[object]]::new()
$seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
$expectedTotal = $null
$marketCount = 0
$complete = $false
for ($page = 1; $page -le $MaxPages; $page++) {
    $remaining = $TotalTimeoutSeconds - $clock.Elapsed.TotalSeconds
    if ($remaining -lt 1) { throw 'Market listing exceeded the collection time limit.' }
    $pageTimeout = [int] [Math]::Min($PageTimeoutSeconds, [Math]::Floor($remaining))
    $uri = '{0}?page={1}&limit=25' -f $endpoint, $page
    try {
        $response = Invoke-RestMethod -Method Get -Uri $uri -TimeoutSec $pageTimeout -ErrorAction Stop
        $retrievedAt = [DateTimeOffset]::UtcNow.ToString('o')
    } catch {
        $status = $null
        if ($null -ne $_.Exception.Response -and $null -ne $_.Exception.Response.StatusCode) {
            $status = [int] $_.Exception.Response.StatusCode
        }
        if ($null -ne $status) { throw ('Market listing failed: HTTP {0}, page {1}, /markets/active.' -f $status, $page) }
        throw ('Market listing failed: {0}, page {1}, /markets/active.' -f $_.Exception.GetType().Name, $page)
    }
    if ($null -eq $response -or $response -isnot [pscustomobject] -or $response.data -isnot [array]) {
        throw 'Market response lacks a data array.'
    }
    $total = $response.totalMarketsCount
    if (($total -isnot [int] -and $total -isnot [long]) -or $total -lt 0) { throw 'Market response lacks a valid totalMarketsCount.' }
    if ($null -ne $expectedTotal -and $total -ne $expectedTotal) { throw 'Market pagination total changed between pages.' }
    $expectedTotal = $total
    foreach ($market in $response.data) {
        if ($market -isnot [pscustomobject]) { throw 'Market response contains a non-object market.' }
        $identity = if ($market.id) { $market.id } else { $market.slug }
        if ($null -eq $identity -or -not $seen.Add([string] $identity)) { throw 'Market pagination contains a missing or duplicate market ID.' }
    }
    $marketCount += $response.data.Count
    if ($marketCount -gt $total) { throw 'Market pagination returned more than totalMarketsCount.' }
    $pages.Add([ordered]@{ page = $page; retrieved_at_utc = $retrievedAt; response = $response })
    if ($marketCount -eq $total) { $complete = $true; break }
    if ($response.data.Count -eq 0) { throw 'Market pagination ended before totalMarketsCount.' }
}
if (-not $complete) { throw ('Market pagination exceeded the {0}-page limit.' -f $MaxPages) }
$listing = [ordered]@{
    source = $endpoint
    started_at_utc = $startedAt
    finished_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
    pages = @($pages.ToArray())
}
if (-not $OutputPath) {
    $directory = Join-Path (Get-Location).Path 'runs/betting_odds_live/listings'
    [System.IO.Directory]::CreateDirectory($directory) | Out-Null
    $name = '{0}-{1}.json' -f [DateTimeOffset]::UtcNow.ToString('yyyyMMddTHHmmssZ'), [Guid]::NewGuid().ToString('N')
    $OutputPath = Join-Path $directory $name
}
$fullPath = [System.IO.Path]::GetFullPath($OutputPath)
$json = ConvertTo-Json -InputObject $listing -Depth 100
$stream = [System.IO.File]::Open($fullPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
try {
    $writer = [System.IO.StreamWriter]::new($stream, [System.Text.UTF8Encoding]::new($false))
    try { $writer.Write($json) } finally { $writer.Dispose() }
} finally { $stream.Dispose() }
Write-Output ('Saved complete listing: {0} markets, {1} pages, {2}' -f $marketCount, $pages.Count, $fullPath)
