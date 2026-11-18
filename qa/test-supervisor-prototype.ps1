# Offline-only; no publisher or network. All waits and worker lifetimes are bounded.
$ErrorActionPreference = 'Stop'
$dir = Join-Path ([IO.Path]::GetTempPath()) ('supervisor-prototype-' + [guid]::NewGuid())
[IO.Directory]::CreateDirectory($dir) | Out-Null
$supervisor = Join-Path $PSScriptRoot 'supervisor-prototype.ps1'
$shell = (Get-Command powershell.exe).Source
$tracked = New-Object 'System.Collections.Generic.List[System.Diagnostics.Process]'
function Wait-File([string]$path, [int]$seconds = 15) {
    $clock = [Diagnostics.Stopwatch]::StartNew()
    while (!(Test-Path -LiteralPath $path) -or (Get-Item -LiteralPath $path).Length -eq 0) {
        if ($clock.Elapsed.TotalSeconds -ge $seconds) { throw "Timed out waiting for $path" }
        Start-Sleep -Milliseconds 50
    }
}
function Track-Worker([string]$identityFile, $parent, [string]$scriptPath) {
    $parts = ([IO.File]::ReadAllText($identityFile)).Split(',')
    if ($parts.Count -ne 2) { throw 'Invalid worker identity' }
    $id = [int]$parts[0]
    $ticks = [long]$parts[1]
    $p = [Diagnostics.Process]::GetProcessById($id)
    try {
        $null = $p.Handle
        $info = Get-CimInstance Win32_Process -Filter "ProcessId = $id"
        if ($p.HasExited -or $parent.HasExited -or $p.StartTime.ToUniversalTime().Ticks -ne $ticks -or
            !$info -or $info.ParentProcessId -ne $parent.Id -or !$info.CommandLine -or
            !$info.CommandLine.Contains($scriptPath)) { throw 'Worker identity mismatch' }
        $tracked.Add($p)
        return $p
    } catch { $p.Dispose(); throw }
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
    $identity = Join-Path $case 'child.pid'
    $parts = ([IO.File]::ReadAllText($identity)).Split(',')
    $badIdentity = Join-Path $case 'invalid.pid'
    [IO.File]::WriteAllText($badIdentity, "$($parts[0]),$([long]$parts[1] + 1)")
    $count = $tracked.Count
    $rejected = $false
    try { $null = Track-Worker $badIdentity $sp (Join-Path $case 'child.ps1') } catch { $rejected = $true }
    if (!$rejected -or $tracked.Count -ne $count) { throw 'Wrong creation time was tracked' }
    $child = Track-Worker $identity $sp (Join-Path $case 'child.ps1')
    Wait-File (Join-Path $case 'descendant.pid')
    $descendant = Track-Worker (Join-Path $case 'descendant.pid') $child (Join-Path $case 'descendant.ps1')
    if ($child.HasExited -or $descendant.HasExited -or $sp.HasExited) { throw 'Worker exited before ready' }
    [IO.File]::WriteAllText((Join-Path $case 'ready-ack'), 'go')
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
[IO.File]::WriteAllText((Join-Path $d 'child.pid.tmp'), "$PID,$([Diagnostics.Process]::GetCurrentProcess().StartTime.ToUniversalTime().Ticks)")
[IO.File]::Move((Join-Path $d 'child.pid.tmp'), (Join-Path $d 'child.pid'))
$p = Start-Process powershell.exe -ArgumentList @('-NoProfile','-File',('"'+(Join-Path $d 'descendant.ps1')+'"'),('"'+$d+'"')) -PassThru
$clock = [Diagnostics.Stopwatch]::StartNew()
while (!(Test-Path (Join-Path $d 'ready-ack'))) { if ($clock.Elapsed.TotalSeconds -ge 15) { exit 7 }; Start-Sleep -Milliseconds 50 }
while (!(Test-Path (Join-Path $d 'release'))) { if ($clock.Elapsed.TotalSeconds -ge 25) { exit 8 }; Start-Sleep -Milliseconds 50 }
if (!$p.WaitForExit(5000)) { exit 9 }
[IO.File]::WriteAllText($finished, 'finished')
'@
    Set-Content (Join-Path $dir 'descendant.ps1') @'
param($d)
[IO.File]::WriteAllText((Join-Path $d 'descendant.pid.tmp'), "$PID,$([Diagnostics.Process]::GetCurrentProcess().StartTime.ToUniversalTime().Ticks)")
[IO.File]::Move((Join-Path $d 'descendant.pid.tmp'), (Join-Path $d 'descendant.pid'))
$clock = [Diagnostics.Stopwatch]::StartNew()
while (!(Test-Path (Join-Path $d 'ready-ack'))) { if ($clock.Elapsed.TotalSeconds -ge 15) { exit 7 }; Start-Sleep -Milliseconds 50 }
while (!(Test-Path (Join-Path $d 'release'))) { if ($clock.Elapsed.TotalSeconds -ge 25) { exit 8 }; Start-Sleep -Milliseconds 50 }
'@
    $early = Join-Path $dir 'early'
    [IO.Directory]::CreateDirectory($early) | Out-Null
    $earlySupervisor = Start-Process $shell -ArgumentList @('-NoProfile','-File',('"'+$supervisor+'"'),'-Directory',('"'+$early+'"')) -PassThru
    $tracked.Add($earlySupervisor)
    $null = $earlySupervisor.Handle
    Wait-File (Join-Path $early 'supervisor.pid')
    if (Test-Path (Join-Path $early 'child.pid')) { throw 'Worker started before admission' }
    $earlySupervisor.Kill()
    Wait-Exited $earlySupervisor
    if (Test-Path (Join-Path $early 'child.pid')) { throw 'Worker started during pre-admission teardown' }
    $a = Start-Case 'client-death'
    $a.Client.Kill()
    Wait-Exited $a.Client
    if ($a.Supervisor.HasExited -or $a.Child.HasExited -or $a.Descendant.HasExited) { throw 'Workers did not survive client death' }
    if (!(Test-Path (Join-Path $a.Dir 'poison'))) { throw 'Poison lost while workers active' }
    [IO.File]::WriteAllText((Join-Path $a.Dir 'release'), 'go')
    Wait-Exited $a.Child
    Wait-Exited $a.Descendant
    Wait-Exited $a.Supervisor
    if (!(Test-Path (Join-Path $a.Dir 'finished')) -or !(Test-Path (Join-Path $a.Dir 'supervisor.complete')) -or (Test-Path (Join-Path $a.Dir 'poison'))) { throw 'Successful teardown was not verified' }
    $b = Start-Case 'supervisor-crash'
    $b.Supervisor.Kill()
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
    # Only terminate processes whose retained handles were validated or directly launched.
    foreach ($p in $tracked) {
        try { if (!$p.HasExited) { $p.Kill() }; if (!$p.WaitForExit(5000)) { $cleanupFailed += $p.Id } } catch { $cleanupFailed += $p.Id }
    }
    # A scan is diagnostic only: never kill an unverified PID from a snapshot.
    $remaining = @(Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($dir) -and $_.ProcessId -ne $PID })
    if ($cleanupFailed.Count -or $remaining.Count) { throw "Cleanup could not verify termination: $cleanupFailed; remaining: $($remaining.ProcessId)" }
    Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
}
