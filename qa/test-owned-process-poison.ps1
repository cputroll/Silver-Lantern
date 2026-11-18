$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\OwnedProcessGuard.ps1')
$dir = Join-Path ([IO.Path]::GetTempPath()) ('owned-guard-' + [guid]::NewGuid())
New-Item -ItemType Directory $dir | Out-Null
try {
    $marker = Join-Path $dir 'poison'
    foreach ($message in @('TerminateJobObject failed', 'QueryInformationJobObject failed', 'CreateProcess with JOB_LIST failed')) {
        try { Invoke-GuardedOwnedProcess $marker { throw $message }; throw 'Expected failure' } catch { if ($_.Exception.Message -eq 'Expected failure') { throw } }
        if (!(Test-Path -LiteralPath $marker)) { throw "Missing poison after $message" }
        try { Assert-OwnedProcessClear $marker; throw 'Expected poison rejection' } catch { if ($_.Exception.Message -eq 'Expected poison rejection') { throw } }
        Remove-Item -LiteralPath $marker
    }
    $value = Invoke-GuardedOwnedProcess $marker { 10 }
    if ($value -ne 10 -or (Test-Path -LiteralPath $marker)) { throw 'Verified completion did not clear marker' }
    Write-Output 'Owned process poison tests passed'
} finally { Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue }
