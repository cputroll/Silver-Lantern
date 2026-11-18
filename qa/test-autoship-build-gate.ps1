$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$ship = Get-Content (Join-Path $repo 'AutoShip-Game.ps1') -Raw
$update = Get-Content (Join-Path $repo 'Update-Game.ps1') -Raw
if ($ship -notmatch "'Update-Game\.ps1'\), '-GameFolder', \`$GameFolder, '-RequireBuild'") { throw 'Publisher must request a fresh build even when the version is current' }
if ($ship -notmatch '\$updateExit -ne 10\)') { throw 'Publisher must reject a skipped or unsuccessful build' }
if ($update -notmatch '\$latestV -le \$installedV -and -not \$Force -and -not \$RequireBuild') { throw 'RequireBuild must bypass the current-version shortcut' }
if ($update -notmatch '\$RequireBuild -and \$latestV -lt \$installedV') { throw 'Rebuild must refuse to downgrade a newer installation' }
if ($update -notmatch '\$freeRam -lt \$MinFreeGb -and -not \$Force') { throw 'RequireBuild must not bypass memory safety' }
$guard = $ship.IndexOf("throw 'Updater process-tree ownership unavailable")
$launch = $ship.IndexOf('[OwnedProcess]::Run(')
if ($guard -lt 0 -or $launch -lt 0 -or $guard -gt $launch) { throw 'Unverified owned updater must remain blocked' }
if ($ship -notmatch "throw 'Publication hold:") { throw 'Publication hold must remain in place' }
Write-Output 'Build completion gate tests passed'
