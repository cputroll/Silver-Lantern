# OFFLINE TEST PROTOTYPE ONLY: not a publisher recovery mechanism.
# One supervisor per fresh private test directory; no concurrent admission protocol.
# Tests use a stand-in client and do not prove real handoff, complete crash recovery,
# durable state, or absence of escaped descendants; production holds remain required.
param([Parameter(Mandatory=$true)][string]$Directory)
$ErrorActionPreference = 'Stop'
try {
    . (Join-Path $PSScriptRoot '..\OwnedProcessGuard.ps1')
    Add-Type -Path (Join-Path $PSScriptRoot '..\OwnedProcess.cs')
    $marker = Join-Path $Directory 'poison'
    $child = Join-Path $Directory 'child.ps1'
    $started = Join-Path $Directory 'started'
    $finished = Join-Path $Directory 'finished'
    $shell = (Get-Command powershell.exe).Source
    [IO.File]::WriteAllText((Join-Path $Directory 'supervisor.pid'), [string]$PID)
    # Do not launch any job until the harness has captured our process handle.
    $clock = [Diagnostics.Stopwatch]::StartNew()
    while (!(Test-Path -LiteralPath (Join-Path $Directory 'admit'))) {
        if ($clock.Elapsed.TotalSeconds -ge 20) { throw 'Admission timed out' }
        Start-Sleep -Milliseconds 50
    }
    $code = Invoke-GuardedOwnedProcess $marker {
        [OwnedProcess]::Run($shell, @('-NoProfile', '-File', $child, $started, $finished), 45000)
    }
    if ($code -ne 0) { exit 1 }
    [IO.File]::WriteAllText((Join-Path $Directory 'supervisor.complete'), 'verified-empty')
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.ToString())
    exit 1
}
