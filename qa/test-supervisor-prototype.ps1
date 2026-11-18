# Offline-only; no publisher or network. All waits and worker lifetimes are bounded.
$ErrorActionPreference = 'Stop'
$dir = Join-Path ([IO.Path]::GetTempPath()) ('supervisor-prototype-' + [guid]::NewGuid())
[IO.Directory]::CreateDirectory($dir) | Out-Null
$supervisor = Join-Path $PSScriptRoot 'supervisor-prototype.ps1'
$shell = (Get-Command powershell.exe).Source
$tracked = New-Object 'System.Collections.Generic.List[System.Diagnostics.Process]'
function Wait-File([string]$path, [int]$seconds = 15) {
    $clock = [Diagnostics.Stopwatch]::StartNew()
    while (!(Test-Path -LiteralPath $path)) {
        if ($clock.Elapsed.TotalSeconds -ge $seconds) { throw "Timed out waiting for $path" }
        Start-Sleep -Milliseconds 50
    }
}
function Track([int]$id) {
    $p = [Diagnostics.Process]::GetProcessById($id)
    # Opening the handle now prevents PID reuse from being mistaken for this process.
    $null = $p.Handle
    $tracked.Add($p)
    return $p
}
function Wait-Exited($p, [int]$milliseconds = 10000) {
    if (!$p.WaitForExit($milliseconds)) { throw "Process $($p.Id) did not terminate" }
}
function Start-Case([string]$name) {
    $case = Join-Path $dir $name
    [IO.Directory]::CreateDirectory($case) | Out-Null
    Copy-Item (Join-Path $dir 'child.ps1') $case
    Copy-Item (Join-Path $dir 'descendant.ps1') $case
    # Harness owns the launch and retains the supervisor handle before admission.
    $sp = Start-Process $shell -ArgumentList @('-NoProfile','-File',('"'+$supervisor+'"'),'-Directory',('"'+$case+'"')) -PassThru
    $tracked.Add($sp)
    $null = $sp.Handle
    $client = Start-Process $shell -ArgumentList @('-NoProfile','-File',('"'+(Join-Path $dir 'client.ps1')+'"'),('"'+$case+'"')) -PassThru
    $tracked.Add($client)
    $null = $client.Handle
    Wait-File (Join-Path $case 'supervisor.pid')
    if ([int](Get-Content (Join-Path $case 'supervisor.pid')) -ne $sp.Id) { throw 'Supervisor identity mismatch' }
    [IO.File]::WriteAllText((Join-Path $case 'admit'), 'go')
    Wait-File (Join-Path $case 'child.pid')
    $child = Track ([int](Get-Content (Join-Path $case 'child.pid')))
    Wait-File (Join-Path $case 'descendant.pid')
    $descendant = Track ([int](Get-Content (Join-Path $case 'descendant.pid')))
    if ($child.HasExited -or $descendant.HasExited -or $sp.HasExited) { throw 'Worker exited before ready' }
    if (!(Test-Path (Join-Path $case 'poison'))) { throw 'Missing poison while workers run' }
    return @{ Dir=$case; Client=$client; Supervisor=$sp; Child=$child; Descendant=$descendant }
}
try {
    Set-Content (Join-Path $dir 'client.ps1') @'
param($dir)
# Stand-in publisher client: its death must not stop the independent supervisor.
Start-Sleep -Seconds 25
'@
    Set-Content (Join-Path $dir 'child.ps1') @'
param($started,$finished)
$d = Split-Path $started
[IO.File]::WriteAllText((Join-Path $d 'child.pid'), [string]$PID)
$p = Start-Process powershell.exe -ArgumentList @('-NoProfile','-File',('"'+(Join-Path $d 'descendant.ps1')+'"'),('"'+$d+'"')) -PassThru
$clock = [Diagnostics.Stopwatch]::StartNew()
while (!(Test-Path (Join-Path $d 'release'))) { if ($clock.Elapsed.TotalSeconds -ge 25) { exit 8 }; Start-Sleep -Milliseconds 50 }
if (!$p.WaitForExit(5000)) { exit 9 }
[IO.File]::WriteAllText($finished, 'finished')
'@
    Set-Content (Join-Path $dir 'descendant.ps1') @'
param($d)
[IO.File]::WriteAllText((Join-Path $d 'descendant.pid'), [string]$PID)
$clock = [Diagnostics.Stopwatch]::StartNew()
while (!(Test-Path (Join-Path $d 'release'))) { if ($clock.Elapsed.TotalSeconds -ge 25) { exit 8 }; Start-Sleep -Milliseconds 50 }
'@
    $early = Join-Path $dir 'early'
    [IO.Directory]::CreateDirectory($early) | Out-Null
    $earlySupervisor = Start-Process $shell -ArgumentList @('-NoProfile','-File',('"'+$supervisor+'"'),'-Directory',('"'+$early+'"')) -PassThru
    $tracked.Add($earlySupervisor)
    $null = $earlySupervisor.Handle
    Wait-File (Join-Path $early 'supervisor.pid')
    if (Test-Path (Join-Path $early 'child.pid')) { throw 'Worker started before admission' }
    Stop-Process -Id $earlySupervisor.Id -Force
    Wait-Exited $earlySupervisor
    if (Test-Path (Join-Path $early 'child.pid')) { throw 'Worker started during pre-admission teardown' }
    $a = Start-Case 'client-death'
    Stop-Process -Id $a.Client.Id -Force
    Wait-Exited $a.Client
    if ($a.Supervisor.HasExited -or $a.Child.HasExited -or $a.Descendant.HasExited) { throw 'Workers did not survive client death' }
    if (!(Test-Path (Join-Path $a.Dir 'poison'))) { throw 'Poison lost while workers active' }
    [IO.File]::WriteAllText((Join-Path $a.Dir 'release'), 'go')
    Wait-Exited $a.Child
    Wait-Exited $a.Descendant
    Wait-Exited $a.Supervisor
    if (!(Test-Path (Join-Path $a.Dir 'finished')) -or !(Test-Path (Join-Path $a.Dir 'supervisor.complete')) -or (Test-Path (Join-Path $a.Dir 'poison'))) { throw 'Successful teardown was not verified' }
    $b = Start-Case 'supervisor-crash'
    Stop-Process -Id $b.Supervisor.Id -Force
    Wait-Exited $b.Supervisor
    Wait-Exited $b.Child
    Wait-Exited $b.Descendant
    if (!(Test-Path (Join-Path $b.Dir 'poison'))) { throw 'Poison lost after supervisor crash' }
    # Fresh output names ensure a retry cannot silently start another child.
    Remove-Item (Join-Path $b.Dir 'child.pid'), (Join-Path $b.Dir 'descendant.pid')
    $retry = Start-Process $shell -ArgumentList @('-NoProfile','-File',('"'+$supervisor+'"'),'-Directory',('"'+$b.Dir+'"')) -PassThru
    $tracked.Add($retry)
    $null = $retry.Handle
    Wait-Exited $retry 15000
    if ($retry.ExitCode -eq 0 -or !(Test-Path (Join-Path $b.Dir 'poison')) -or (Test-Path (Join-Path $b.Dir 'child.pid')) -or (Test-Path (Join-Path $b.Dir 'descendant.pid'))) { throw 'Poisoned retry launched work or did not fail closed' }
    Write-Output 'Supervisor client-death and supervisor-crash tests passed'
} finally {
    $cleanupFailed = @()
    # Stop tracked launchers first, then discover anything launched before tracking.
    foreach ($p in $tracked) {
        try { if (!$p.HasExited) { $p.Kill() }; if (!$p.WaitForExit(5000)) { $cleanupFailed += $p.Id } } catch { $cleanupFailed += $p.Id }
    }
    $escaped = @(Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($dir) -and $_.ProcessId -ne $PID })
    foreach ($item in $escaped) {
        try { $p = Track ([int]$item.ProcessId); if (!$p.HasExited) { $p.Kill() }; if (!$p.WaitForExit(5000)) { $cleanupFailed += $p.Id } } catch { $cleanupFailed += $item.ProcessId }
    }
    $remaining = @(Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($dir) -and $_.ProcessId -ne $PID })
    if ($cleanupFailed.Count -or $remaining.Count) { throw "Cleanup could not verify termination: $cleanupFailed; remaining: $($remaining.ProcessId)" }
    Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
}
