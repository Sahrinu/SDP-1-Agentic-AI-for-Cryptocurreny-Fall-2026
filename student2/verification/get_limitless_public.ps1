[CmdletBinding(DefaultParameterSetName='Page')]
param(
    [Parameter(ParameterSetName='Page', Mandatory=$true)] [ValidateRange(1, 5)] [int] $Page,
    [Parameter(ParameterSetName='Detail', Mandatory=$true)] [ValidatePattern('^[A-Za-z0-9-]+$')] [string] $Slug,
    [ValidateRange(1, 15)] [int] $TimeoutSeconds = 15
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
if ($PSCmdlet.ParameterSetName -eq 'Page') {
    $url = 'https://api.limitless.exchange/markets/active?page={0}&limit=25' -f $Page
} else {
    $url = 'https://api.limitless.exchange/markets/{0}' -f $Slug
}
try {
    $response = Invoke-RestMethod -Uri $url -TimeoutSec $TimeoutSeconds -ErrorAction Stop
    $json = ConvertTo-Json -InputObject $response -Depth 100 -Compress
    [Console]::Out.Write($json)
} catch {
    $status = $null
    if ($null -ne $_.Exception.Response -and $null -ne $_.Exception.Response.StatusCode) {
        $status = [int] $_.Exception.Response.StatusCode
    }
    if ($null -ne $status) { [Console]::Error.WriteLine(('Public market request failed: HTTP {0}' -f $status)) }
    else { [Console]::Error.WriteLine('Public market request failed') }
    exit 1
}
