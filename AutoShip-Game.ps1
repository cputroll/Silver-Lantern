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
$shipLock = $null
function Enter-ShipLock([string]$Path) {
    try { return [IO.File]::Open($Path, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None) }
    catch { throw "Cannot acquire local ship lock: $($_.Exception.Message)" }
}
function Get-RevisionBaseline([int]$MainRevision, [int]$ChannelRevision) {
    return [Math]::Max($MainRevision, $ChannelRevision)
}
function Log([string]$Message) {
    Add-Content -LiteralPath $logPath -Value (('{0} {1}' -f [DateTime]::UtcNow.ToString('o'), $Message)) -Encoding UTF8
}
function Run([string]$Program, [string[]]$Arguments) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Program @Arguments 2>&1 | ForEach-Object { Log ([string]$_) } }
    finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0) { throw "$Program failed exit $LASTEXITCODE" }
}
function Get-AvailableRevision([int]$PreviousRevision, [string[]]$AssetNames) {
    $revision = $PreviousRevision + 1
    while ($true) {
        $occupied = $false
        foreach ($name in $AssetNames) {
            if ($name -match "^r$revision-" -or $name -eq "game-manifest-$revision.json") {
                $occupied = $true
                break
            }
        }
        if (-not $occupied) { return $revision }
        if ($revision -eq [int]::MaxValue) { throw 'No available revision number' }
        $revision++
    }
}
function Version([string]$Tag) {
    if ($Tag -notmatch '^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$') { throw 'invalid version' }
    return [version]::new([int]$Matches[1], [int]$Matches[2], [int]$Matches[3])
}
try {
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
    $shipLock = Enter-ShipLock (Join-Path $env:PUBLIC 'SilverLantern-AutoShip.lock')
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
        Log 'Plan: rebuild, pack, upload immutable assets, commit manifest, then advance channel.'
        Write-Output 'GAMESHIP plan=rebuild,pack,upload-additive,commit-manifest,advance-channel'
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

        Run 'git' @('fetch', 'origin', '+refs/heads/*:refs/remotes/origin/*', '--prune')
        $channelTip = (git rev-parse --verify --quiet 'refs/remotes/origin/game-channel')
        if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 1) { throw 'Could not inspect fetched channel' }
        $hasChannel = -not [string]::IsNullOrWhiteSpace($channelTip)
        $previous = $null
        if ($hasChannel) {
            $previousText = (git show "${channelTip}:channel.json") -join "`n"
            if ($LASTEXITCODE -ne 0 -or -not $previousText) { throw 'Could not read fetched channel pointer' }
            $previous = $previousText | ConvertFrom-Json
            if (-not $previous.revision -or [int]$previous.revision -lt 1) { throw 'Invalid channel revision' }
        }
        Run 'git' @('pull', '--rebase', 'origin', 'main')
        $repoManifest = Join-Path $repo 'game-manifest.json'
        $previousRevision = 0
        if (Test-Path -LiteralPath $repoManifest) {
            $old = Get-Content -LiteralPath $repoManifest -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($old.revision) { $previousRevision = [int]$old.revision }
        }
        if ($previous) { $previousRevision = Get-RevisionBaseline $previousRevision ([int]$previous.revision) }
        $releaseJson = gh release view game-v1 --repo cputroll/Silver-Lantern --json assets
        if ($LASTEXITCODE -ne 0) { throw 'Could not list existing release assets' }
        $releaseAssets = $releaseJson | ConvertFrom-Json
        $existing = @{}
        foreach ($asset in $releaseAssets.assets) { $existing[$asset.name] = $true }
        $revision = Get-AvailableRevision -PreviousRevision $previousRevision -AssetNames @($existing.Keys)
        $prefix = "r$revision-"
        Log "Rebuilding game for revision $revision"
        $updatePreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo 'Update-Game.ps1') -GameFolder $GameFolder 2>&1 | ForEach-Object { Log ([string]$_) } }
        finally { $ErrorActionPreference = $updatePreference }
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

        Log "Uploading $($manifest.assets.Count) immutable bundle assets with prefix $prefix"
        foreach ($asset in $manifest.assets) {
            if ($existing.ContainsKey($asset.asset)) { throw "Bundle asset name collision: $($asset.asset)" }
            Run 'gh' @('release', 'upload', 'game-v1', (Join-Path $work $asset.asset), '--repo', 'cputroll/Silver-Lantern')
        }
        if ($existing.ContainsKey($manifestAsset)) { throw "Manifest asset already exists: $manifestAsset" }
        Run 'gh' @('release', 'upload', 'game-v1', $manifestFile, '--repo', 'cputroll/Silver-Lantern')

        $verify = Join-Path $work 'verify'; New-Item -ItemType Directory -Path $verify | Out-Null
        foreach ($asset in @($manifest.assets) + @([pscustomobject]@{ asset = $manifestAsset; size = (Get-Item -LiteralPath $manifestFile).Length; sha256 = $manifestDigest })) {
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
        if ($previous) {
            if ([int]$previous.revision -ge $revision) { throw "channel already at revision $($previous.revision)" }
            $pointer.previous = [ordered]@{ version = $previous.version; revision = $previous.revision; manifest = $previous.manifest; sha256 = $previous.sha256 }
        }
        $pointerJson = $pointer | ConvertTo-Json -Depth 5 -Compress
        $channelWork = Join-Path $work 'channel'
        if ($hasChannel) {
            Run 'git' @('worktree', 'add', '--detach', $channelWork, $channelTip)
        } else {
            Run 'git' @('worktree', 'add', '--detach', $channelWork, 'HEAD')
        }
        try {
            [IO.File]::WriteAllText((Join-Path $channelWork 'channel.json'), $pointerJson + "`n", [Text.UTF8Encoding]::new($false))
            Run 'git' @('-C', $channelWork, 'add', '--', 'channel.json')
            Run 'git' @('-C', $channelWork, 'commit', '-m', "Channel revision $revision")
            # The lease pins the exact fetched tip (an absent ref on first publication) and the
            # ancestry check stops a rewritten remote from producing a non-fast-forward update.
            if ($hasChannel) { Run 'git' @('-C', $channelWork, 'merge-base', '--is-ancestor', $channelTip, 'HEAD') }
            $expectedTip = if ($hasChannel) { $channelTip } else { '' }
            Run 'git' @('-C', $channelWork, 'push', "--force-with-lease=refs/heads/game-channel:$expectedTip", 'origin', 'HEAD:refs/heads/game-channel')
        } finally {
            Run 'git' @('worktree', 'remove', '--force', $channelWork)
        }

        # Legacy clients read the mutable fixed asset. A concurrent publisher on another host
        # could overwrite it with an older revision, so re-upload and confirm the remote digest
        # a few times; finitely many publishers must converge.
        $legacy = Join-Path $work 'game-manifest.json'
        Copy-Item -LiteralPath $manifestFile -Destination $legacy -Force
        $legacySha = (Get-FileHash -Algorithm SHA256 -LiteralPath $legacy).Hash.ToLowerInvariant()
        $legacyOk = $false
        for ($attempt = 1; $attempt -le 3; $attempt++) {
            Run 'gh' @('release', 'upload', 'game-v1', $legacy, '--clobber', '--repo', 'cputroll/Silver-Lantern')
            $remote = (gh release view game-v1 --repo cputroll/Silver-Lantern --json assets) | ConvertFrom-Json
            $digest = [string](@($remote.assets) | Where-Object { $_.name -eq 'game-manifest.json' } | Select-Object -First 1).digest
            if (($digest -replace '^sha256:', '').ToLowerInvariant() -eq $legacySha) { $legacyOk = $true; break }
            Log "Legacy manifest mismatch (attempt $attempt); retrying"
        }
        if (-not $legacyOk) { throw 'legacy game-manifest.json could not be confirmed' }
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
    if ($shipLock) { $shipLock.Dispose() }
}
