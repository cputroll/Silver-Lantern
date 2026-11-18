param(
    [Parameter(Mandatory=$true)][string]$Zip,
    [string]$Artifacts = (Join-Path $env:LOCALAPPDATA ("Temp\opencode\silver-friend-" + [guid]::NewGuid().ToString('N')))
)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $Artifacts | Out-Null
$script = Join-Path $PSScriptRoot 'simulate_friend.py'
& python $script --zip $Zip --artifacts $Artifacts
$code = $LASTEXITCODE
if ($code -ne 0) { exit $code }
exit 0
