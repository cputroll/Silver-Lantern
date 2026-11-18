param(
    [Parameter(Mandatory=$true)][string]$Zip,
    [string]$OldZip,
    [string]$Artifacts = (Join-Path $env:LOCALAPPDATA ("Temp\opencode\silver-friend-" + [guid]::NewGuid().ToString('N')))
)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $Artifacts | Out-Null
$script = Join-Path $PSScriptRoot 'simulate_friend.py'
$arguments = @($script, '--zip', $Zip, '--artifacts', $Artifacts)
if ($OldZip) { $arguments += @('--old-zip', $OldZip) }
& python @arguments
$code = $LASTEXITCODE
if ($code -ne 0) { exit $code }
exit 0
