<#
  Update-Game.ps1
  Updates a built HeyListen game folder to the latest upstream builder release without a full rebuild
  (contract: update-spec.md section 3; speed findings: rebuild-speed.md).

    1. Read <GameFolder>\hookshot_version.txt (version=, builder=) and GitHub releases/latest; compare as semver.
    2. Up to date (and no -Force): exit 0.
    3. Preflight: game files present, game/builder not running, >= 15 GB free, enough free RAM (-MinFreeGb).
    4. -DryRun: print the plan and exit 10. No downloads, no processes, no writes.
    5. If versions\<latest>\installed.txt is missing: install that builder with the official bootstrapper
       (HeyListenBuilder.exe from the release, checked against SHA256SUMS.txt), wait for installed.txt, close its GUI.
    6. Back up <GameFolder>\user\ (read only) to %LOCALAPPDATA%\HeyListenBuilder\save-backups\.
    7. Run tools\nx2\make_game.py --update <GameFolder> --work %LOCALAPPDATA%\HeyListenBuilder.
    8. Check version=, the Vulkan renderer and DLSS; re-apply the preset (Apply-HeyListen-Preset.ps1); exit 10.

  The compiled objects under %LOCALAPPDATA%\HeyListenBuilder\objcache (about 639 MB) are what make rebuilds
  incremental. This script never deletes them, never "frees up space", and never removes build files.
  HOOKSHOT_KEEP_BUILD_FILES=1 is set for the make_game.py process only (never with setx) so the next rebuild can
  skip the ~3 min translation. HOOKSHOT_JOBS is not set (automatic = one compiler per CPU thread, memory-gated).

  Stdout carries only machine lines that start with "HLUPDATE " (key=value; spaces in values are written as %20).
  Narration and make_game.py output go to stderr and to %LOCALAPPDATA%\SilverLantern\update.log.

  Exit codes: 0 up to date | 10 updated (or, with -DryRun, update available) | 1 error.
#>
[CmdletBinding()]
param(
    [string]$GameFolder = (Join-Path $env:USERPROFILE 'Games\HeyListen'),
    [switch]$DryRun,
    [switch]$Force,
    [switch]$RequireBuild,
    [ValidateRange(0, 1024)][int]$Jobs = 32,
    [string]$UpstreamRepo = 'Sacrappy/HeyListen',
    [switch]$NoBuilderUpdate,
    [switch]$PrepareShadersLater,
    [string]$LogDir,
    [ValidateRange(0, 1024)][double]$MinFreeGb = 6,
    [ValidateRange(1, 240)][int]$BuilderTimeoutMinutes = 30
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
$ProgressPreference = 'SilentlyContinue'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }

$Inv = [Globalization.CultureInfo]::InvariantCulture
$BuilderRoot = Join-Path $env:LOCALAPPDATA 'HeyListenBuilder'
$StateDir = Join-Path $env:LOCALAPPDATA 'SilverLantern'
$UpdateLog = Join-Path $StateDir 'update.log'
if (-not $LogDir) { $LogDir = $BuilderRoot }
$script:LogWriter = $null
$script:Reason = 'unexpected'

function Out-Record {
    # One machine line on stdout: Out-Record key1 value1 [key2 value2 ...]. ASCII, key=value, no spaces in values.
    $parts = New-Object System.Collections.Generic.List[string]
    for ($i = 0; $i + 1 -lt $args.Count; $i += 2) {
        $v = ([string]$args[$i + 1]).Trim() -replace '\s', '%20'
        $v = [regex]::Replace($v, '[^\x21-\x7E]', '?')
        if (-not $v) { $v = '-' }
        $parts.Add([string]$args[$i] + '=' + $v)
    }
    $line = 'HLUPDATE ' + ($parts -join ' ')
    [Console]::Out.WriteLine($line)
    Write-Log $line
}

function Write-Log([string]$Message) {
    if ($script:LogWriter) {
        $script:LogWriter.WriteLine(([DateTime]::Now.ToString('yyyy-MM-dd HH:mm:ss', $Inv)) + ' ' + $Message)
        $script:LogWriter.Flush()
    }
}

function Say([string]$Message) {
    # Human narration: stderr and the log, never stdout.
    [Console]::Error.WriteLine($Message)
    Write-Log $Message
}

function Fail([string]$Reason, [string]$Message) {
    $script:Reason = $Reason
    throw $Message
}

function Get-VersionValue([string]$Tag) {
    if ($Tag -notmatch '^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$') { return $null }
    return New-Object Version ([int]$Matches[1]), ([int]$Matches[2]), ([int]$Matches[3])
}

function Read-KeyValueFile([string]$Path) {
    $map = @{}
    foreach ($line in [IO.File]::ReadAllLines($Path)) {
        $eq = $line.IndexOf('=')
        if ($eq -gt 0) { $map[$line.Substring(0, $eq).Trim().ToLowerInvariant()] = $line.Substring($eq + 1).Trim() }
    }
    return $map
}

function Get-FreeRamGb {
    try {
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        return [Math]::Round([double]$os.FreePhysicalMemory / 1MB, 1)
    } catch { return -1 }
}

function Get-FreeDiskGb([string]$Path) {
    $root = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Path))
    $d = New-Object IO.DriveInfo $root
    return [Math]::Round($d.AvailableFreeSpace / 1GB, 1)
}

function Get-ReleaseJson([string]$Repo) {
    $uri = "https://api.github.com/repos/$Repo/releases/latest"
    $headers = @{ 'User-Agent' = 'Silver-Lantern-Update-Game'; 'Accept' = 'application/vnd.github+json' }
    try { return Invoke-RestMethod -Uri $uri -Headers $headers -UseBasicParsing -TimeoutSec 60 }
    catch { Fail 'network' ("releases/latest failed: " + $_.Exception.Message) }
}

function Get-UnderPath([string]$Path, [string]$Root) {
    if (-not $Path -or -not $Root) { return $false }
    $r = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    return [IO.Path]::GetFullPath($Path).StartsWith($r, [StringComparison]::OrdinalIgnoreCase)
}

function Get-BuilderProcesses {
    @(Get-Process -Name 'HeyListenBuilder' -ErrorAction SilentlyContinue)
}

function Close-Processes($Procs) {
    foreach ($p in @($Procs)) {
        try {
            if ($p.HasExited) { continue }
            Say ("closing HeyListenBuilder (pid " + $p.Id + ")")
            $null = $p.CloseMainWindow()
            if (-not $p.WaitForExit(15000)) { $p.Kill(); $null = $p.WaitForExit(10000) }
        } catch { Say ("could not close pid " + $p.Id + ": " + $_.Exception.Message) }
    }
}

function Install-Builder([string]$Tag, $Release) {
    # Phase A1: the official bootstrapper downloads only the missing parts into versions\<tag>.
    $ua = @{ 'User-Agent' = 'Silver-Lantern-Update-Game' }
    $assets = @($Release.assets)
    $exeAsset = @($assets | Where-Object { $_.name -eq 'HeyListenBuilder.exe' }) | Select-Object -First 1
    $sumAsset = @($assets | Where-Object { $_.name -eq 'SHA256SUMS.txt' }) | Select-Object -First 1
    if (-not $exeAsset) { Fail 'no-bootstrapper' "release $Tag has no HeyListenBuilder.exe" }
    if (-not $sumAsset) { Fail 'no-checksums' "release $Tag has no SHA256SUMS.txt" }
    Out-Record stage builder-download
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('SilverLantern-builder-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    $exe = Join-Path $tmp 'HeyListenBuilder.exe'
    $sums = Join-Path $tmp 'SHA256SUMS.txt'
    try {
        Invoke-WebRequest -Uri $exeAsset.browser_download_url -OutFile $exe -Headers $ua -UseBasicParsing -TimeoutSec 300
        Invoke-WebRequest -Uri $sumAsset.browser_download_url -OutFile $sums -Headers $ua -UseBasicParsing -TimeoutSec 60
    } catch { Fail 'download' ("bootstrapper download failed: " + $_.Exception.Message) }
    $want = $null
    foreach ($l in [IO.File]::ReadAllLines($sums)) {
        if ($l -match '^\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*$' -and ([IO.Path]::GetFileName($Matches[2]) -eq 'HeyListenBuilder.exe')) { $want = $Matches[1].ToLowerInvariant() }
    }
    if (-not $want) { Fail 'checksum' 'HeyListenBuilder.exe is not listed in SHA256SUMS.txt' }
    $have = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($have -ne $want) { Fail 'checksum' "HeyListenBuilder.exe SHA-256 mismatch ($have)" }
    Say "bootstrapper verified ($have); starting it"
    $marker = Join-Path $BuilderRoot "versions\$Tag\installed.txt"
    $verDir = Join-Path $BuilderRoot "versions\$Tag"
    $boot = Start-Process -FilePath $exe -WorkingDirectory $tmp -PassThru
    $deadline = [DateTime]::UtcNow.AddMinutes($BuilderTimeoutMinutes)
    $ready = $false
    while ([DateTime]::UtcNow -lt $deadline) {
        if ((Test-Path -LiteralPath $marker) -and (([IO.File]::ReadAllText($marker)).Trim() -eq $Tag)) { $ready = $true; break }
        Start-Sleep -Seconds 5
    }
    # Close the GUI the bootstrapper's run line starts (give it up to 60 s to appear), then the bootstrapper.
    $guiDeadline = [DateTime]::UtcNow.AddSeconds(60)
    do {
        $gui = @(Get-BuilderProcesses | Where-Object { try { Get-UnderPath $_.Path $verDir } catch { $false } })
        if ($gui.Count -gt 0 -or -not $ready) { break }
        Start-Sleep -Seconds 3
    } while ([DateTime]::UtcNow -lt $guiDeadline)
    Close-Processes $gui
    Close-Processes @(Get-BuilderProcesses | Where-Object { try { Get-UnderPath $_.Path $tmp } catch { $false } })
    if (-not $boot.HasExited) { Close-Processes @($boot) }
    try { Remove-Item -LiteralPath $tmp -Recurse -Force } catch { Say "could not remove $tmp" }
    if (-not $ready) { Fail 'builder-timeout' "builder $Tag was not installed within $BuilderTimeoutMinutes min" }
    Out-Record stage builder-ready
}

function Send-BuildLine([string]$Line) {
    # make_game.py output: stderr + log; its @@stage / @@times protocol lines also become HLUPDATE records.
    Say $Line
    if ($Line -match '^@@stage\s+(\S+)\s*(.*)$') { Out-Record stage $Matches[1] text $Matches[2] }
    elseif ($Line -match '^@@times\s+(\S+)\s+(\S+)\s+(\S+)') { Out-Record times_total $Matches[1] times_compile $Matches[2] times_previous $Matches[3] }
}

function Test-VulkanRenderer([string]$Folder) {
    (Test-Path -LiteralPath (Join-Path $Folder 'bin\hookshot_vk.dll')) -or (Test-Path -LiteralPath (Join-Path $Folder 'hookshot_vk.dll'))
}

function Find-NgxSdk {
    $direct = Join-Path $BuilderRoot 'ngx-sdk'
    if (Test-Path -LiteralPath $direct -PathType Container) { return $direct }
    $hit = Get-ChildItem -LiteralPath $BuilderRoot -Directory -Recurse -Depth 4 -Filter 'ngx-sdk' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($hit) { return $hit.FullName }
    return $null
}

$exitCode = 1
$watch = [Diagnostics.Stopwatch]::StartNew()
try {
    try {
        if (-not $DryRun) {
            New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
            $script:LogWriter = New-Object IO.StreamWriter($UpdateLog, $true, (New-Object Text.UTF8Encoding($false)))
        }
    } catch { $script:LogWriter = $null }
    Write-Log ('---- Update-Game.ps1 start: GameFolder=' + $GameFolder + ' DryRun=' + [bool]$DryRun + ' Force=' + [bool]$Force)

    # 1. Installed and latest versions.
    $versionFile = Join-Path $GameFolder 'hookshot_version.txt'
    if (-not (Test-Path -LiteralPath $versionFile -PathType Leaf)) {
        Out-Record installed unknown
        Fail 'no-version-file' "missing $versionFile"
    }
    $kv = Read-KeyValueFile $versionFile
    $installed = if ($kv.ContainsKey('version')) { $kv['version'] } else { '' }
    $installedBuilder = if ($kv.ContainsKey('builder')) { $kv['builder'] } else { '' }
    $installedV = Get-VersionValue $installed
    if (-not $installedV) { Out-Record installed unknown; Fail 'bad-installed-version' "no valid version= in $versionFile" }
    $release = Get-ReleaseJson $UpstreamRepo
    $latest = [string]$release.tag_name
    $latestV = Get-VersionValue $latest
    if (-not $latestV) { Fail 'bad-latest-version' "latest release tag '$latest' is not vX.Y.Z" }
    Out-Record installed $installed
    Out-Record latest $latest
    $freeRam = Get-FreeRamGb
    Out-Record freeram_gb ($freeRam.ToString('0.0', $Inv))

    if ($RequireBuild -and $latestV -lt $installedV) { Fail 'newer-installed' "refusing to rebuild newer installed $installed with older release $latest" }
    if ($latestV -le $installedV -and -not $Force -and -not $RequireBuild) {
        if ($latestV -lt $installedV) { Say "installed $installed is newer than latest $latest; nothing to do" }
        Out-Record status current installed $installed latest $latest
        $exitCode = 0
        return
    }

    # 2. Preflight.
    $game = @(Get-Process -Name 'HeyListen' -ErrorAction SilentlyContinue | Where-Object { try { Get-UnderPath $_.Path $GameFolder } catch { $false } })
    if ($game.Count -gt 0) { Fail 'game-running' 'the game is running; close it first' }
    if (@(Get-BuilderProcesses).Count -gt 0) { Fail 'builder-running' 'HeyListenBuilder is running; close it first' }
    foreach ($need in '.hookshot_exefs', 'romfs') {
        if (-not (Test-Path -LiteralPath (Join-Path $GameFolder $need) -PathType Container)) { Fail 'missing-game-files' "no $need in $GameFolder" }
    }
    foreach ($p in $BuilderRoot, $GameFolder) {
        $free = Get-FreeDiskGb $p
        if ($free -lt 15) { Fail 'low-disk' "only $free GB free on the drive of $p (15 GB needed)" }
    }
    if ($freeRam -ge 0 -and $freeRam -lt 8) {
        Say ("Warning: only " + $freeRam.ToString('0.0', $Inv) + " GB of RAM is free. Closing memory-heavy apps (browsers, games, AI models) can cut the compile from ~22 min to ~8-12 min.")
    }
    if ($freeRam -ge 0 -and $freeRam -lt $MinFreeGb -and -not $Force) {
        Fail 'low-memory' ("free RAM " + $freeRam.ToString('0.0', $Inv) + " GB is below -MinFreeGb $MinFreeGb; close apps or pass -Force")
    }

    # 3. Plan.
    $currentTxt = Join-Path $BuilderRoot 'versions\current.txt'
    $builderInstalled = if (Test-Path -LiteralPath $currentTxt) { ([IO.File]::ReadAllText($currentTxt)).Trim() } else { 'none' }
    Out-Record builder_installed $builderInstalled
    $objDir = Join-Path $BuilderRoot 'objcache'
    $objMb = 0
    if (Test-Path -LiteralPath $objDir) { $objMb = [Math]::Round(((Get-ChildItem -LiteralPath $objDir -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum) / 1MB) }
    Out-Record objcache_mb $objMb
    $latestMarker = Join-Path $BuilderRoot "versions\$latest\installed.txt"
    $needBuilder = -not ((Test-Path -LiteralPath $latestMarker) -and (([IO.File]::ReadAllText($latestMarker)).Trim() -eq $latest))
    if ($needBuilder -and $NoBuilderUpdate) { Say "-NoBuilderUpdate: building with installed builder $builderInstalled"; $needBuilder = $false; $buildTag = $builderInstalled }
    else { $buildTag = $latest }
    if ($needBuilder) { Out-Record plan 'download-builder,rebuild' } else { Out-Record plan rebuild }
    $T = Join-Path $BuilderRoot "versions\$buildTag\HookshotBuilder\tools"
    $makeGame = Join-Path $T 'nx2\make_game.py'
    $buildLog = Join-Path $LogDir ('update-' + $latest + '-' + [DateTime]::Now.ToString('yyyyMMdd-HHmmss', $Inv) + '.log')
    $pyArgs = @('-I', '-u', '-X', 'utf8', $makeGame, '--update', $GameFolder, '--work', $BuilderRoot, '--log', $buildLog)
    if ($PSBoundParameters.ContainsKey('Jobs') -and $Jobs -gt 0) { $pyArgs += @('--jobs', [string]$Jobs) }
    if ($PrepareShadersLater) { $pyArgs += '--no-prepare-shaders' }
    if ($DryRun) {
        Out-Record command ('python ' + ($pyArgs -join ' '))
        Out-Record status available installed $installed latest $latest
        $exitCode = 10
        return
    }

    # 4. Phase A: the builder for the latest tag.
    if ($needBuilder) { Install-Builder $latest $release }
    if (-not (Test-Path -LiteralPath $makeGame -PathType Leaf)) { Fail 'no-make-game' "missing $makeGame" }

    # 5. Back up the saves (read only on the source).
    $userDir = Join-Path $GameFolder 'user'
    if (Test-Path -LiteralPath $userDir -PathType Container) {
        $bk = Join-Path $BuilderRoot ('save-backups\update-' + $installed + '-to-' + $latest + '-' + [DateTime]::Now.ToString('yyyyMMdd-HHmmss', $Inv))
        New-Item -ItemType Directory -Path $bk -Force | Out-Null
        try { Copy-Item -LiteralPath $userDir -Destination $bk -Recurse -Force } catch { Fail 'backup' ("save backup failed: " + $_.Exception.Message) }
        Out-Record backup $bk
    }

    # 6. Phase B: make_game.py --update, environment for this process tree only.
    $py = Join-Path $T 'python\python.exe'
    if (-not (Test-Path -LiteralPath $py -PathType Leaf)) {
        $cmd = Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $cmd) { Fail 'no-python' 'no python.exe in the builder tools or on PATH' }
        $py = $cmd.Source
    }
    $hadDlss = Test-Path -LiteralPath (Join-Path $GameFolder 'ngx\nvngx_dlss.dll') -PathType Leaf
    $env:HOOKSHOT_BUILDER_EXE = Join-Path $BuilderRoot "versions\$buildTag\HookshotBuilder\HeyListenBuilder.exe"
    $env:HOOKSHOT_KEEP_BUILD_FILES = '1'
    if ($hadDlss) {
        $ngx = Find-NgxSdk
        if (-not $ngx) { Fail 'no-ngx-sdk' 'the folder has DLSS but no ngx-sdk was found under HeyListenBuilder' }
        $env:HOOKSHOT_NGX_SDK = $ngx
    }
    if (Test-Path -LiteralPath (Join-Path $T 'cpm')) { $env:RX_CPM = Join-Path $T 'cpm' }
    if (Test-Path -LiteralPath (Join-Path $T 'wgpu-native')) { $env:WGPU_NATIVE = Join-Path $T 'wgpu-native' }
    if (Test-Path -LiteralPath (Join-Path $T 'llvm-mingw')) { $env:HOOKSHOT_MINGW = Join-Path $T 'llvm-mingw' }
    Out-Record log $buildLog
    Out-Record stage build
    Say ("running: " + $py + ' ' + ($pyArgs -join ' '))
    Push-Location -LiteralPath $T
    $oldEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $py @pyArgs 2>&1 | ForEach-Object { Send-BuildLine ([string]$_) }
        $pyCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $oldEap
        Pop-Location
    }
    if ($pyCode -ne 0) { Fail 'build-failed' "make_game.py exited with $pyCode" }

    # 7. Verify.
    $after = Read-KeyValueFile $versionFile
    $newVersion = if ($after.ContainsKey('version')) { $after['version'] } else { '' }
    if ($newVersion -ne $buildTag) { Fail 'version-unchanged' "hookshot_version.txt shows '$newVersion', expected $buildTag" }
    if (-not (Test-VulkanRenderer $GameFolder)) { Fail 'no-vulkan' 'hookshot_vk.dll missing after the build' }
    if ($hadDlss -and -not (Test-Path -LiteralPath (Join-Path $GameFolder 'ngx\nvngx_dlss.dll'))) { Fail 'dlss-lost' 'ngx\nvngx_dlss.dll missing after the build' }

    # 8. Preset (auto-detects DLSS/FSR; only touches hookshot_options.ini display keys).
    $preset = Join-Path $PSScriptRoot 'Apply-HeyListen-Preset.ps1'
    if (Test-Path -LiteralPath $preset -PathType Leaf) {
        $oldEap = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $preset -GameFolder $GameFolder 2>&1 | ForEach-Object { Say ([string]$_) }
            if ($LASTEXITCODE -ne 0) { Say "preset step exited with $LASTEXITCODE (build is fine)" }
        } finally { $ErrorActionPreference = $oldEap }
    }
    Out-Record status updated from $installed to $newVersion seconds ([int]$watch.Elapsed.TotalSeconds)
    $exitCode = 10
} catch {
    $msg = $_.Exception.Message
    Write-Log ('ERROR ' + $script:Reason + ': ' + $msg)
    try { [Console]::Error.WriteLine('Update failed: ' + $msg) } catch { }
    Out-Record status error reason $script:Reason
    $exitCode = 1
} finally {
    Write-Log ('---- end exit=' + $exitCode)
    if ($script:LogWriter) { $script:LogWriter.Dispose() }
    exit $exitCode
}
