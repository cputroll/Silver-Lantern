$ErrorActionPreference = 'Stop'
$guard = (Resolve-Path (Join-Path $PSScriptRoot '..\OwnedProcessGuard.ps1')).Path
. $guard
$dir = Join-Path ([IO.Path]::GetTempPath()) ('owned-guard-crash-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $dir | Out-Null
$process = $null
try {
    $marker = Join-Path $dir 'updater.marker'
    $child = Join-Path $dir 'crash.ps1'
    @'
param($guard, $marker)
$ErrorActionPreference = 'Stop'
. $guard
Invoke-GuardedOwnedProcess $marker { [Environment]::Exit(23) }
'@ | Set-Content -LiteralPath $child
    $shell = (Get-Command powershell.exe).Source
    # This simulates abrupt parent exit only; it does not launch an updater or test process-tree recovery.
    $process = Start-Process -FilePath $shell -ArgumentList @('-NoProfile', '-NonInteractive', '-File', ('"' + $child + '"'), ('"' + $guard + '"'), ('"' + $marker + '"')) -PassThru
    if (!$process.WaitForExit(10000)) { throw 'Abrupt-exit subprocess timed out' }
    if ($process.ExitCode -ne 23) { throw "Abrupt-exit subprocess exited unexpectedly: $($process.ExitCode)" }
    if (!(Test-Path -LiteralPath $marker -PathType Leaf)) { throw 'Abrupt exit failed to retain marker' }
    $ran = $false
    $blocked = $false
    try { Invoke-GuardedOwnedProcess $marker { $script:ran = $true } }
    catch { if ($_.Exception.Message -match 'recovery required') { $blocked = $true } else { throw } }
    if (!$blocked -or $ran) { throw 'Restart was not blocked before running updater' }
    Remove-Item -LiteralPath $marker
    $result = Invoke-GuardedOwnedProcess $marker { 'finished' }
    if ($result -ne 'finished' -or (Test-Path -LiteralPath $marker)) { throw 'Successful run did not clear marker' }
    $failed = $false
    try { Invoke-GuardedOwnedProcess $marker { throw 'simulated uncertain teardown' } }
    catch { if ($_.Exception.Message -match 'simulated uncertain teardown') { $failed = $true } else { throw } }
    if (!$failed -or !(Test-Path -LiteralPath $marker -PathType Leaf)) { throw 'Failure did not retain marker' }
    Write-Output 'Owned process abrupt-exit guard tests passed'
} finally {
    if ($null -ne $process) {
        try {
            if (!$process.HasExited) {
                $process.Kill()
                if (!$process.WaitForExit(5000)) { throw 'Subprocess cleanup timed out' }
            }
            if (!$process.HasExited) { throw 'Subprocess cleanup could not be verified' }
        } finally { $process.Dispose() }
    }
    Remove-Item -LiteralPath $dir -Recurse -Force
}
