[CmdletBinding()]
param(
    [string]$GameFolder = (Join-Path $env:USERPROFILE 'Games\HeyListen'),
    [switch]$DryRun,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
$env:GIT_TERMINAL_PROMPT = '0'
$stateDir = Join-Path $env:LOCALAPPDATA 'SilverLantern'
$logPath = Join-Path $stateDir 'autoship.log'
$repo = $PSScriptRoot
$work = $null
function Log([string]$Message) {
    Add-Content -LiteralPath $logPath -Value (('{0} {1}' -f [DateTime]::UtcNow.ToString('o'), $Message)) -Encoding UTF8
}
function Run([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments 2>&1 | ForEach-Object { Log ([string]$_) }
    if ($LASTEXITCODE -ne 0) { throw "$Program failed exit $LASTEXITCODE" }
}
function Version([string]$Tag) {
    if ($Tag -notmatch '^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$') { throw 'invalid version' }
    return [version]::new([int]$Matches[1], [int]$Matches[2], [int]$Matches[3])
}
try {
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
    $installed = (Get-Content -LiteralPath (Join-Path $GameFolder 'hookshot_version.txt') -Raw).Trim()
    if ($installed -match '(?m)^version\s*=\s*([^\r\n]+)') { $installed = $Matches[1].Trim() }
    $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/Sacrappy/HeyListen/releases/latest' -Headers @{ 'User-Agent' = 'Silver-Lantern-AutoShip'; 'Accept' = 'application/vnd.github+json' }
    $latest = [string]$release.tag_name
    $due = $Force -or ((Version $latest) -gt (Version $installed))
    if (-not $due) {
        Log "Current installed=$installed latest=$latest"
        Write-Output "GAMESHIP status=current installed=$installed latest=$latest"
        exit 0
    }
    if ($DryRun) {
        Log 'Plan: rebuild, pack, upload new bundles without overwriting, commit manifest, then replace its serving asset.'
        Write-Output 'GAMESHIP plan=rebuild,pack,upload-additive,commit-manifest,upload-manifest'
        Write-Output "GAMESHIP status=available installed=$installed latest=$latest"
        exit 10
    }
    Push-Location $repo
    try {
        $work = Join-Path ([IO.Path]::GetTempPath()) ('silvership-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $work | Out-Null
        Log 'Rebuilding game'
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo 'Update-Game.ps1') -GameFolder $GameFolder 2>&1 | ForEach-Object { Log ([string]$_) }
        if ($LASTEXITCODE -ne 10 -and $LASTEXITCODE -ne 0) { throw "update failed exit $LASTEXITCODE" }
        Run 'python' @((Join-Path $repo 'pack-game.py'), '--source', $GameFolder, '--out', $work)
        $manifestPath = Join-Path $work 'game-manifest.json'
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if (@($manifest.assets).Count -lt 2) { throw 'fewer than two assets' }
        $tokenFile = Join-Path $env:USERPROFILE '.heylisten-get\github-token.txt'
        if (-not $env:GH_TOKEN -and (Test-Path -LiteralPath $tokenFile)) { $env:GH_TOKEN = (Get-Content -LiteralPath $tokenFile -Raw).Trim() }
        $releaseAssets = gh release view game-v1 --repo cputroll/Silver-Lantern --json assets | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0) { throw 'Could not list existing release assets' }
        $existing = @($releaseAssets.assets | ForEach-Object { $_.name })
        foreach ($asset in $manifest.assets) {
            if ($existing -contains $asset.asset) { throw "Bundle asset already exists: $($asset.asset); choose unique names before publishing" }
        }
        foreach ($asset in $manifest.assets) {
            Run 'gh' @('release', 'upload', 'game-v1', (Join-Path $work $asset.asset), '--repo', 'cputroll/Silver-Lantern')
        }
        $verify = Join-Path $work 'verify'; New-Item -ItemType Directory -Path $verify | Out-Null
        foreach ($asset in @($manifest.assets | Select-Object -First 2)) {
            Run 'gh' @('release', 'download', 'game-v1', '--repo', 'cputroll/Silver-Lantern', '--pattern', $asset.asset, '--dir', $verify, '--clobber')
            if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $verify $asset.asset)).Hash.ToLowerInvariant() -ne $asset.sha256) { throw 'asset verification failed' }
        }
        Run 'git' @('pull', '--rebase', 'origin', 'main')
        $repoManifest = Join-Path $repo 'game-manifest.json'
        $old = Get-Content -LiteralPath $repoManifest -Raw -Encoding UTF8 | ConvertFrom-Json
        $revision = 1; if ($old.revision) { $revision = [int]$old.revision + 1 }
        $manifest | Add-Member -NotePropertyName revision -NotePropertyValue $revision
        $json = $manifest | ConvertTo-Json -Depth 20 -Compress
        [IO.File]::WriteAllText($repoManifest, $json + "`n", [Text.UTF8Encoding]::new($false))
        Run 'git' @('add', '--', 'game-manifest.json')
        Run 'git' @('commit', '-m', "Publish game revision $revision", '--', 'game-manifest.json')
        Run 'git' @('pull', '--rebase', 'origin', 'main')
        Run 'git' @('push', 'origin', 'HEAD:main')
        Run 'gh' @('release', 'upload', 'game-v1', $repoManifest, '--clobber', '--repo', 'cputroll/Silver-Lantern')
        $manifestVerify = Join-Path $work 'manifest-verify'; New-Item -ItemType Directory -Path $manifestVerify | Out-Null
        Run 'gh' @('release', 'download', 'game-v1', '--repo', 'cputroll/Silver-Lantern', '--pattern', 'game-manifest.json', '--dir', $manifestVerify)
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $manifestVerify 'game-manifest.json')).Hash -ne (Get-FileHash -Algorithm SHA256 -LiteralPath $repoManifest).Hash) { throw 'manifest asset verification failed' }
        Log "Shipped revision=$revision assets=$(@($manifest.assets).Count)"
        Write-Output "GAMESHIP status=shipped revision=$revision assets=$(@($manifest.assets).Count)"
        exit 10
    } finally { Pop-Location }
} catch {
    $reason = ($_.Exception.Message -replace '[^a-zA-Z0-9_.-]+', '_').Trim('_')
    try { Log "Error: $($_.Exception.Message)" } catch { }
    Write-Output "GAMESHIP status=error reason=$reason"
    exit 1
} finally {
    if ($work -and (Test-Path -LiteralPath $work)) { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
}
