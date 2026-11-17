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
        $tokenFile = Join-Path $env:USERPROFILE '.heylisten-get\github-token.txt'
        if (-not $env:GH_TOKEN -and (Test-Path -LiteralPath $tokenFile)) { $env:GH_TOKEN = (Get-Content -LiteralPath $tokenFile -Raw).Trim() }
        if (-not $env:GH_TOKEN) { throw 'No GitHub token for publishing' }
        $work = Join-Path ([IO.Path]::GetTempPath()) ('silvership-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $work | Out-Null

        git fetch origin main game-channel 2>&1 | ForEach-Object { Log ([string]$_) }
        Run 'git' @('pull', '--rebase', 'origin', 'main')
        $repoManifest = Join-Path $repo 'game-manifest.json'
        $revision = 1
        if (Test-Path -LiteralPath $repoManifest) {
            $old = Get-Content -LiteralPath $repoManifest -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($old.revision) { $revision = [int]$old.revision + 1 }
        }
        $prefix = "r$revision-"
        Log "Rebuilding game for revision $revision"
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo 'Update-Game.ps1') -GameFolder $GameFolder 2>&1 | ForEach-Object { Log ([string]$_) }
        if ($LASTEXITCODE -ne 10 -and $LASTEXITCODE -ne 0) { throw "update failed exit $LASTEXITCODE" }
        Run 'python' @((Join-Path $repo 'pack-game.py'), '--source', $GameFolder, '--out', $work, '--prefix', $prefix)
        $manifestPath = Join-Path $work 'game-manifest.json'
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if (@($manifest.assets).Count -lt 2) { throw 'fewer than two assets' }
        $manifest | Add-Member -NotePropertyName revision -NotePropertyValue $revision
        $manifestJson = $manifest | ConvertTo-Json -Depth 20 -Compress
        $manifestAsset = "game-manifest-$revision.json"
        $manifestFile = Join-Path $work $manifestAsset
        [IO.File]::WriteAllText($manifestFile, $manifestJson + "`n", [Text.UTF8Encoding]::new($false))
        $manifestDigest = (Get-FileHash -Algorithm SHA256 -LiteralPath $manifestFile).Hash.ToLowerInvariant()

        $releaseAssets = gh release view game-v1 --repo cputroll/Silver-Lantern --json assets | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0) { throw 'Could not list existing release assets' }
        $existing = @{}
        foreach ($asset in $releaseAssets.assets) { $existing[$asset.name] = $asset.size }

        Log "Uploading $($manifest.assets.Count) immutable bundle assets with prefix $prefix"
        foreach ($asset in $manifest.assets) {
            if ($existing.ContainsKey($asset.asset)) {
                if ([int64]$existing[$asset.asset] -ne [int64]$asset.size) { throw "Bundle asset name collision: $($asset.asset)" }
                continue
            }
            Run 'gh' @('release', 'upload', 'game-v1', (Join-Path $work $asset.asset), '--repo', 'cputroll/Silver-Lantern')
        }
        if ($existing.ContainsKey($manifestAsset)) { throw "Manifest asset already exists: $manifestAsset" }
        Run 'gh' @('release', 'upload', 'game-v1', $manifestFile, '--repo', 'cputroll/Silver-Lantern')

        $verify = Join-Path $work 'verify'; New-Item -ItemType Directory -Path $verify | Out-Null
        foreach ($asset in @($manifest.assets | Select-Object -First 2) + @([pscustomobject]@{ asset = $manifestAsset; size = (Get-Item -LiteralPath $manifestFile).Length; sha256 = $manifestDigest })) {
            Run 'gh' @('release', 'download', 'game-v1', '--repo', 'cputroll/Silver-Lantern', '--pattern', $asset.asset, '--dir', $verify, '--clobber')
            $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $verify $asset.asset)).Hash.ToLowerInvariant()
            if ($actual -ne $asset.sha256) { throw "asset verification failed: $($asset.asset)" }
        }

        # Record the new immutable manifest in main for history and rollback context.
        [IO.File]::WriteAllText($repoManifest, $manifestJson + "`n", [Text.UTF8Encoding]::new($false))
        Run 'git' @('add', '--', 'game-manifest.json')
        Run 'git' @('commit', '-m', "Publish game revision $revision")
        Run 'git' @('pull', '--rebase', 'origin', 'main')
        Run 'git' @('push', 'origin', 'HEAD:main')

        # Advance the channel branch by one fast-forward commit; the client sees either the old
        # complete pointer or the new complete pointer, never a partial file.
        $pointer = [ordered]@{ version = 1; revision = $revision; manifest = $manifestAsset; sha256 = $manifestDigest }
        $previous = $null
        $hasChannel = (git branch -r --list 'origin/game-channel') -ne $null
        if ($hasChannel) {
            $previousText = (git show 'origin/game-channel:channel.json') -join "`n"
            if ($previousText) { $previous = $previousText | ConvertFrom-Json }
        }
        if ($previous) {
            if ([int]$previous.revision -ge $revision) { throw "channel already at revision $($previous.revision)" }
            $pointer.previous = [ordered]@{ version = $previous.version; revision = $previous.revision; manifest = $previous.manifest; sha256 = $previous.sha256 }
        }
        $pointerJson = $pointer | ConvertTo-Json -Depth 5 -Compress
        $channelWork = Join-Path $work 'channel'
        if ($hasChannel) {
            Run 'git' @('worktree', 'add', '--detach', $channelWork, 'origin/game-channel')
        } else {
            Run 'git' @('worktree', 'add', '--detach', $channelWork, 'HEAD')
        }
        try {
            [IO.File]::WriteAllText((Join-Path $channelWork 'channel.json'), $pointerJson + "`n", [Text.UTF8Encoding]::new($false))
            Run 'git' @('-C', $channelWork, 'add', '--', 'channel.json')
            Run 'git' @('-C', $channelWork, 'commit', '-m', "Channel revision $revision")
            Run 'git' @('-C', $channelWork, 'push', 'origin', 'HEAD:game-channel')
        } finally {
            Run 'git' @('worktree', 'remove', '--force', $channelWork)
        }

        # Legacy clients keep the old fixed manifest until they migrate; update it last so the
        # atomic channel above is never waiting on this non-atomic step.
        $legacy = Join-Path $work 'game-manifest.json'
        Copy-Item -LiteralPath $manifestFile -Destination $legacy -Force
        Run 'gh' @('release', 'upload', 'game-v1', $legacy, '--clobber', '--repo', 'cputroll/Silver-Lantern')
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
