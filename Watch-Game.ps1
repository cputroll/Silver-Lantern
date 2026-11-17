[CmdletBinding()]
param(
    [string]$GameFolder = (Join-Path $env:USERPROFILE 'Games\HeyListen'),
    [switch]$Once,
    [switch]$Force,
    [ValidateRange(1, 8760)][int]$IntervalHours = 24,
    [switch]$Loop,
    [switch]$InstallTask,
    [switch]$UninstallTask,
    [switch]$DryRun,
    [string]$UpstreamRepo = 'Sacrappy/HeyListen'
)

$ErrorActionPreference = 'Stop'
$stateDir = Join-Path $env:LOCALAPPDATA 'SilverLantern'
$statePath = Join-Path $stateDir 'last-heylisten.json'
$logPath = Join-Path $stateDir 'autoupdate.log'
$taskName = 'Silver Lantern Auto Update'

function Get-VersionValue([string]$Tag) {
    if ($Tag -notmatch '^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$') { throw "Invalid version tag: $Tag" }
    return [version]::new([int]$Matches[1], [int]$Matches[2], [int]$Matches[3])
}

function Invoke-Check {
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $from = '-'; $to = '-'; $result = 'error'; $code = 1
    try {
        New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
        $previous = $null
        if (Test-Path -LiteralPath $statePath) {
            try { $previous = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json } catch { $previous = $null }
        }
        $installedPath = Join-Path $GameFolder 'hookshot_version.txt'
        if (Test-Path -LiteralPath $installedPath) {
            $installedContent = (Get-Content -LiteralPath $installedPath -Raw).Trim()
            if ($installedContent -match '(?m)^version\s*=\s*([^\r\n]+)') { $from = $Matches[1].Trim() } else { $from = $installedContent }
        } elseif ($previous -and $previous.tag -and $previous.lastResult -in @('updated', 'up to date')) {
            $from = [string]$previous.tag
        }
        Write-Host 'Checking for updates...'
        $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$UpstreamRepo/releases/latest" -Headers @{ 'User-Agent' = 'Silver-Lantern-AutoUpdate'; 'Accept' = 'application/vnd.github+json' }
        $to = [string]$release.tag_name
        $latestVersion = Get-VersionValue $to
        $null = $release.assets
        $needsUpdate = $Force -or $from -eq '-' -or ($latestVersion -gt (Get-VersionValue $from))
        if (-not $needsUpdate) {
            $result = 'up to date'; $code = 0
            Write-Host "Up to date ($from)."
        } elseif ($DryRun) {
            $result = 'dry run'; $code = 0
            Write-Host "New version $to found. Dry run: would rebuild."
        } else {
            $updater = Join-Path $PSScriptRoot 'Update-Game.ps1'
            if (-not (Test-Path -LiteralPath $updater -PathType Leaf)) { throw 'Update-Game.ps1 not found beside Watch-Game.ps1' }
            Write-Host "New version $to found. Rebuilding (usually a few minutes)..."
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $updater -GameFolder $GameFolder
            $updateCode = $LASTEXITCODE
            if ($updateCode -eq 10) {
                $result = 'updated'; $code = 10
                Write-Host "Updated to $to."
            } elseif ($updateCode -eq 0) {
                $result = 'up to date'; $code = 0
                Write-Host "Up to date ($to)."
            } else { throw "Update failed (exit $updateCode)." }
        }
    } catch {
        $result = 'error'; $code = 1
        $errorMessage = $_.Exception.Message -replace '[\r\n]+', ' '
        Write-Host "Update check failed: $errorMessage"
    } finally {
        $watch.Stop()
        try {
            New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
            $stateTag = if ($to -ne '-') { $to } elseif ($previous -and $previous.tag) { [string]$previous.tag } else { '' }
            @{ tag = $stateTag; checkedAt = [DateTime]::UtcNow.ToString('o'); lastResult = $result } | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
            $event = '{0} from={1} to={2} result={3} duration={4:F2}s' -f [DateTime]::UtcNow.ToString('o'), $from, $to, $result, $watch.Elapsed.TotalSeconds
            if ($result -eq 'error' -and $errorMessage) { $event += ' error=' + $errorMessage }
            Add-Content -LiteralPath $logPath -Value $event -Encoding UTF8
        } catch { Write-Host "Could not write update state or log: $($_.Exception.Message)"; $code = 1 }
    }
    return $code
}

if ($InstallTask -or $UninstallTask) {
    if ($InstallTask -and $UninstallTask) { Write-Host 'Choose either install or uninstall.'; exit 1 }
    if ($InstallTask) {
        $launcher = Join-Path $PSScriptRoot 'SilverLantern-AutoUpdate.cmd'
        if ($DryRun) { Write-Host "Would install daily task: $taskName at 04:00."; exit 0 }
        & schtasks.exe /Create /TN $taskName /SC DAILY /ST 04:00 /TR ('"' + $launcher + '"') /F
    } else {
        if ($DryRun) { Write-Host "Would remove task: $taskName."; exit 0 }
        & schtasks.exe /Delete /TN $taskName /F
    }
    if ($LASTEXITCODE -ne 0) { exit 1 }
    exit 0
}

do {
    $exitCode = Invoke-Check
    if ($Loop -and -not $Once) { Start-Sleep -Seconds ($IntervalHours * 3600) }
} while ($Loop -and -not $Once)
exit $exitCode
