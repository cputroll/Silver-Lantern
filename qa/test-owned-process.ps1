$ErrorActionPreference = 'Stop'
Add-Type -Path (Join-Path $PSScriptRoot '..\OwnedProcess.cs')
$dir = Join-Path ([IO.Path]::GetTempPath()) ('owned-process-test-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $dir | Out-Null
try {
    $child = Join-Path $dir 'child.ps1'
    $root = Join-Path $dir 'root.ps1'
    $marker = Join-Path $dir 'done.txt'
    Set-Content $child 'param($marker, $delay) Start-Sleep -Milliseconds $delay; Set-Content $marker done'
    Set-Content $root 'param($child, $marker, $delay) Start-Process -FilePath powershell.exe -ArgumentList @("-NoProfile", "-File", ("`"" + $child + "`""), ("`"" + $marker + "`""), $delay); exit 0'
    $shell = (Get-Command powershell.exe).Source
    $streams = Join-Path $dir 'streams.ps1'
    Set-Content $streams '[Console]::Out.WriteLine("output"); [Console]::Error.WriteLine("error"); if ([Console]::In.Read() -ne -1) { exit 7 }; exit 0'
    if ([OwnedProcess]::Run($shell, @('-NoProfile', '-File', $streams), 10000) -ne 0) { throw 'Standard streams were not usable' }
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $code = [OwnedProcess]::Run($shell, @('-NoProfile', '-File', $root, $child, $marker, '1200'), 10000)
    if ($code -ne 0 -or !(Test-Path $marker) -or $clock.ElapsedMilliseconds -lt 1000) { throw 'Returned before descendant completed' }
    Remove-Item $marker
    $timedOut = $false
    try { [OwnedProcess]::Run($shell, @('-NoProfile', '-File', $root, $child, $marker, '4000'), 1000) | Out-Null }
    catch { if ($_.Exception.ToString() -match 'timed out') { $timedOut = $true } else { throw } }
    if (!$timedOut) { throw 'Expected timeout while descendant remained active' }
    Start-Sleep -Milliseconds 4500
    if (Test-Path $marker) { throw 'Timed-out descendant survived teardown' }
    Write-Output 'OwnedProcess integration tests passed'
} finally { Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue }
