param([Parameter(Mandatory=$true)][string]$Observation,[Parameter(Mandatory=$true)][string]$OutputPath)
$ErrorActionPreference='Stop'
$full=[IO.Path]::GetFullPath($OutputPath)
if (Test-Path -LiteralPath $full) { throw 'Output exists; use a new filename' }
$o=Get-Content -LiteralPath $Observation -Raw | ConvertFrom-Json
$start=([DateTimeOffset]::Parse($o.market.startAt)).ToUnixTimeMilliseconds()
$now=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$directory=[IO.Path]::GetDirectoryName($full)
[IO.Directory]::CreateDirectory($directory) | Out-Null
function Write-NewBytes([string]$Path, [byte[]]$Bytes) {
    $stream=[IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write)
    try { $stream.Write($Bytes,0,$Bytes.Length) } finally { $stream.Dispose() }
}
function Read-Public([string]$Path, [string]$Kind) {
    $url='https://data-api.binance.vision'+$Path
    $response=Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 15 -ErrorAction Stop
    $received=[DateTimeOffset]::UtcNow.ToString('o')
    $raw=$response.Content
    if ($raw -is [byte[]]) { $bytes=$raw }
    elseif ($raw -is [string]) { $bytes=[Text.Encoding]::UTF8.GetBytes($raw) }
    else { throw 'Unsupported public response content type' }
    $name=[IO.Path]::GetFileName($full)+'.'+$Kind+'.raw.json'
    Write-NewBytes (Join-Path $directory $name) $bytes
    return @{source_url=$url;received_at=$received;raw_file=$name}
}
$context=@{source='binance:BTC/USDT'}
$context.hourly=Read-Public "/api/v3/klines?symbol=BTCUSDT&interval=1h&endTime=$now&limit=170" 'hourly'
$context.opening=Read-Public "/api/v3/klines?symbol=BTCUSDT&interval=1m&startTime=$start&limit=1" 'opening'
$context.trade=Read-Public '/api/v3/aggTrades?symbol=BTCUSDT&limit=1' 'trade'
$json=$context | ConvertTo-Json -Depth 10
Write-NewBytes $full ([Text.Encoding]::UTF8.GetBytes($json))
Write-Output "Saved raw Binance response manifest: $full"
