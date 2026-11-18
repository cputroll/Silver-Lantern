$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'AutoShip-Game.ps1'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$errors)
foreach ($name in @('Enter-ShipLock', 'Get-RevisionBaseline')) {
    $fn = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    if (-not $fn) { throw "Missing $name" }
    Invoke-Expression $fn.Extent.Text
}
if ((Get-RevisionBaseline 4 9) -ne 9 -or (Get-RevisionBaseline 12 9) -ne 12) { throw 'Channel/main baseline is stale' }
$path = Join-Path $env:TEMP ('ship-lock-test-' + [guid]::NewGuid().ToString('N'))
$lock = Enter-ShipLock $path
try {
    $child = Start-Process powershell.exe -ArgumentList @('-NoProfile', '-Command', "try { `$f = [IO.File]::Open('$path', 'OpenOrCreate', 'ReadWrite', 'None'); `$f.Dispose(); exit 0 } catch { exit 17 }") -PassThru -WindowStyle Hidden
    if (-not $child.WaitForExit(15000)) { $child.Kill(); throw 'Lock test timed out' }
    if ($child.ExitCode -ne 17) { throw "Concurrent process acquired lock: $($child.ExitCode)" }
} finally { $lock.Dispose(); Remove-Item $path -Force -ErrorAction SilentlyContinue }
$source = Get-Content $scriptPath -Raw
if ($source -notmatch "force-with-lease=refs/heads/game-channel") { throw 'Channel push must use an expected-tip lease' }
if ($source.IndexOf('could not be confirmed') -lt $source.IndexOf("'HEAD:refs/heads/game-channel'")) { throw 'Legacy manifest must be confirmed after the channel advance' }
if (-not $source.Contains("Run 'git' @('fetch', 'origin', '+refs/heads/*:refs/remotes/origin/*', '--prune')")) { throw 'Checked channel fetch missing' }
Write-Output 'Concurrency and stale-channel tests passed'
