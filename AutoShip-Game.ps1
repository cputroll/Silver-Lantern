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
. (Join-Path $repo 'OwnedProcessGuard.ps1')
$ownedProcessMarker = Join-Path $env:PUBLIC 'SilverLantern-AutoShip-updater-recovery.lock'
$work = $null
$preservePublicationEvidence = $false
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
function Assert-PublishingHost([string]$Designated, [string]$Actual) {
    if ([string]::IsNullOrWhiteSpace($Designated) -or [string]::IsNullOrWhiteSpace($Actual) -or $Designated.Trim() -ine $Actual.Trim()) {
        throw 'Publishing disabled: SILVERLANTERN_PUBLISH_HOST must match this computer name'
    }
}
function Assert-ChannelPointer($Pointer) {
    if ([int]$Pointer.revision -lt 1 -or [string]$Pointer.manifest -cne "game-manifest-$($Pointer.revision).json" -or [string]$Pointer.sha256 -notmatch '^[a-fA-F0-9]{64}$') { throw 'Invalid channel pointer' }
}
function Version([string]$Tag) {
    if ($Tag -notmatch '^v?(\d+)\.(\d+)\.(\d+)$') { throw 'invalid or ambiguous version' }
    return [version]::new([int]$Matches[1], [int]$Matches[2], [int]$Matches[3])
}
function Read-GameVersion([string]$Path) {
    $value = (Get-Content -LiteralPath $Path -Raw).Trim()
    if ($value -match '(?m)^version\s*=\s*([^\r\n]+)') { $value = $Matches[1].Trim() }
    $null = Version $value
    return $value
}
function Get-VerifiedChannelVersion($Pointer, [string]$Path, [string]$LocalVersionFile) {
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash
    if ($actual -ine [string]$Pointer.sha256) { throw 'Channel manifest digest mismatch' }
    $manifest = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([int]$manifest.revision -ne [int]$Pointer.revision) { throw 'Channel manifest revision mismatch' }
    if ($manifest.game_version) {
        $value = [string]$manifest.game_version
    } else {
        $entries = @($manifest.files | Where-Object { $_.path -ceq 'hookshot_version.txt' })
        if ($entries.Count -ne 1 -or -not (Test-Path -LiteralPath $LocalVersionFile)) { throw 'Channel game version unknown' }
        $localHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $LocalVersionFile).Hash
        if ($localHash -ieq [string]$entries[0].sha256) {
            $value = Read-GameVersion $LocalVersionFile
        } else {
            $value = & python (Join-Path $repo 'channel_version_recovery.py') $Path
            if ($LASTEXITCODE -ne 0 -or @($value).Count -ne 1) { throw 'Channel game version unknown' }
            $value = [string]$value
        }
    }
    $null = Version $value
    return $value
}
function Assert-NoChannelRegression([string]$Candidate, [string]$Channel) {
    if ((Version $Candidate) -lt (Version $Channel)) { throw 'Candidate game version would regress authoritative channel' }
}
function Test-ChannelCurrent([string]$Installed, [string]$Latest, [string]$Channel) {
    return ((Version $Channel) -ge (Version $Installed)) -and ((Version $Channel) -ge (Version $Latest))
}
try {
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
    if (-not $DryRun) {
        Assert-PublishingHost $env:SILVERLANTERN_PUBLISH_HOST $env:COMPUTERNAME
        throw 'Publication hold: cross-host single-publisher enforcement and crash recovery require operational approval'
    }
    $shipLock = Enter-ShipLock (Join-Path $env:PUBLIC 'SilverLantern-AutoShip.lock')
    Assert-OwnedProcessClear $ownedProcessMarker
    $installed = Read-GameVersion (Join-Path $GameFolder 'hookshot_version.txt')
    $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/Sacrappy/HeyListen/releases/latest' -Headers @{ 'User-Agent' = 'Silver-Lantern-AutoShip'; 'Accept' = 'application/vnd.github+json' }
    $latest = [string]$release.tag_name
    $due = $Force -or ((Version $latest) -gt (Version $installed))
    if (-not $due) {
        if (-not $DryRun) {
            Push-Location $repo
            try {
                $tokenFile = Join-Path $env:USERPROFILE '.heylisten-get\github-token.txt'
                if (-not $env:GH_TOKEN -and (Test-Path -LiteralPath $tokenFile)) { $env:GH_TOKEN = (Get-Content -LiteralPath $tokenFile -Raw).Trim() }
                if (-not $env:GH_TOKEN) { throw 'No GitHub token for reconciliation' }
                $work = Join-Path $stateDir ('silvership-' + [guid]::NewGuid().ToString('N'))
                New-Item -ItemType Directory -Path $work | Out-Null
                Run 'git' @('fetch', 'origin', '+refs/heads/game-channel:refs/remotes/origin/game-channel')
                $previousText = (git show 'refs/remotes/origin/game-channel:channel.json') -join "`n"
                if ($LASTEXITCODE -ne 0 -or -not $previousText) { throw 'Could not read authoritative channel pointer' }
                $previous = $previousText | ConvertFrom-Json
                Assert-ChannelPointer $previous
                $channelDir = Join-Path $work 'channel-check'
                New-Item -ItemType Directory -Path $channelDir | Out-Null
                Run 'gh' @('release', 'download', 'game-v1', '--repo', 'cputroll/Silver-Lantern', '--pattern', [string]$previous.manifest, '--dir', $channelDir)
                $channelVersion = Get-VerifiedChannelVersion $previous (Join-Path $channelDir ([string]$previous.manifest)) (Join-Path $GameFolder 'hookshot_version.txt')
                if (-not (Test-ChannelCurrent $installed $latest $channelVersion)) {
                    $due = $true
                    Log "Channel behind installed=$installed latest=$latest channel=$channelVersion"
                }
            } finally { Pop-Location }
        } else {
            throw 'Dry run cannot establish channel version; no current status is asserted'
        }
        if (-not $due) {
            Log "Current installed=$installed latest=$latest"
            Write-Output "GAMESHIP status=current installed=$installed latest=$latest"
            exit 0
        }
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
        $work = Join-Path $stateDir ('silvership-' + [guid]::NewGuid().ToString('N'))
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
            Assert-ChannelPointer $previous
            $channelDir = Join-Path $work 'publication-channel-check'
            New-Item -ItemType Directory -Path $channelDir | Out-Null
            Run 'gh' @('release', 'download', 'game-v1', '--repo', 'cputroll/Silver-Lantern', '--pattern', [string]$previous.manifest, '--dir', $channelDir)
            $channelVersion = Get-VerifiedChannelVersion $previous (Join-Path $channelDir ([string]$previous.manifest)) (Join-Path $GameFolder 'hookshot_version.txt')
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
        # Fail closed until job emptiness and teardown are independently verified.
        throw 'Updater process-tree ownership unavailable; publication blocked'
        Add-Type -Path (Join-Path $repo 'OwnedProcess.cs')
        $shell = (Get-Command powershell.exe -CommandType Application).Source | Select-Object -First 1
        $updateExit = Invoke-GuardedOwnedProcess $ownedProcessMarker {
            [OwnedProcess]::Run($shell, @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $repo 'Update-Game.ps1'), '-GameFolder', $GameFolder, '-RequireBuild'), 3590000)
        }
        if ($updateExit -ne 10) { throw "update did not complete a build (exit $updateExit)" }
        Run 'python' @((Join-Path $repo 'pack-game.py'), '--source', $GameFolder, '--out', $work, '--prefix', $prefix)
        $manifestPath = Join-Path $work 'game-manifest.json'
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if (@($manifest.assets).Count -lt 2) { throw 'fewer than two assets' }
        $versionFile = Join-Path $GameFolder 'hookshot_version.txt'
        $packedVersion = Read-GameVersion $versionFile
        $versionEntries = @($manifest.files | Where-Object { $_.path -ceq 'hookshot_version.txt' })
        if ($versionEntries.Count -ne 1 -or (Get-FileHash -Algorithm SHA256 -LiteralPath $versionFile).Hash -ine [string]$versionEntries[0].sha256) { throw 'Packed version file changed or is missing' }
        if ((Version $packedVersion) -lt (Version $latest)) { throw 'Updated local game version is older than latest release' }
        if ($hasChannel) { Assert-NoChannelRegression $packedVersion $channelVersion }
        $manifest | Add-Member -NotePropertyName game_version -NotePropertyValue $packedVersion
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
            $candidateTip = (git -C $channelWork rev-parse --verify HEAD)
            if ($LASTEXITCODE -ne 0 -or -not $candidateTip) { throw 'Could not resolve candidate channel commit' }
            # channel_publisher uses --force-with-lease=refs/heads/game-channel:<expected>
            # and reconciles ambiguous results only against the exact remote commit.
            $leaseArgument = if ($expectedTip) { $expectedTip } else { '-' }
            # Persist before the push: a process crash must not erase the candidate identity.
            $evidence = [ordered]@{ candidate = $candidateTip; expected = $expectedTip; worktree = $channelWork; createdUtc = [DateTime]::UtcNow.ToString('o') }
            [IO.File]::WriteAllText((Join-Path $work 'channel-push-evidence.json'), (($evidence | ConvertTo-Json -Compress) + "`n"), [Text.UTF8Encoding]::new($false))
            $preservePublicationEvidence = $true
            Run 'python' @((Join-Path $repo 'channel_publisher.py'), $channelWork, 'origin', $leaseArgument, $candidateTip)
            $preservePublicationEvidence = $false
        } finally {
            if (-not $preservePublicationEvidence) { Run 'git' @('worktree', 'remove', '--force', $channelWork) }
            else { Log "Unresolved channel push; evidence retained at $work" }
        }

        # Legacy clients stay on their existing manifest; only the leased channel is updated.
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
    if (-not $preservePublicationEvidence -and $work -and (Test-Path -LiteralPath $work)) { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
    if ($shipLock) { $shipLock.Dispose() }
}
