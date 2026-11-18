$ErrorActionPreference = 'Stop'
$path = Join-Path (Split-Path $PSScriptRoot -Parent) 'AutoShip-Game.ps1'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Syntax errors' }
$source = Get-Content -LiteralPath $path -Raw
$publication = $source.Substring($source.IndexOf("$" + "channelTip = (git rev-parse"))
if ($publication.IndexOf('Get-VerifiedChannelVersion') -lt 0 -or $publication.IndexOf('Assert-NoChannelRegression $packedVersion $channelVersion') -lt $publication.IndexOf('Get-VerifiedChannelVersion') -or $publication.IndexOf('Assert-NoChannelRegression $packedVersion $channelVersion') -gt $publication.IndexOf("Run 'gh' @('release', 'upload'")) { throw 'Publication must compare verified channel before upload, including forced runs' }
if ($source -notmatch 'Publication hold:') { throw 'Publication hold missing' }
foreach ($name in @('Version', 'Read-GameVersion', 'Get-VerifiedChannelVersion', 'Test-ChannelCurrent', 'Assert-NoChannelRegression')) {
    $fn = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true)
    if (-not $fn) { throw "Missing $name" }
    Invoke-Expression $fn.Extent.Text
}
function Must-Fail([scriptblock]$action) { $failed = $false; try { & $action } catch { $failed = $true }; if (-not $failed) { throw 'Expected failure' } }
$dir = Join-Path ([IO.Path]::GetTempPath()) ('channel-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $dir | Out-Null
try {
    $local = Join-Path $dir 'hookshot_version.txt'
    [IO.File]::WriteAllText($local, 'version=2.0.0')
    $file = Join-Path $dir 'manifest.json'
    [IO.File]::WriteAllText($file, '{"revision":1,"game_version":"1.0.0"}')
    $digest = (Get-FileHash $file -Algorithm SHA256).Hash
    $pointer = [pscustomobject]@{ revision=1; sha256=$digest }
    $version = Get-VerifiedChannelVersion $pointer $file $local
    if (Test-ChannelCurrent '2.0.0' '2.0.0' $version) { throw 'Interrupted upload incorrectly reported current' }
    if (-not (Test-ChannelCurrent '2.0.0' '2.0.0' '2.0.0')) { throw 'Matching channel is not current' }
    if (Test-ChannelCurrent '2.0.0' '2.1.0' '2.0.0') { throw 'New upstream must be due' }
    Must-Fail { Assert-NoChannelRegression '2.0.0' '3.0.0' }
    Assert-NoChannelRegression '3.0.0' '3.0.0'
    Assert-NoChannelRegression '4.0.0' '3.0.0'
    Must-Fail { Assert-NoChannelRegression '2.0.0' '' }
    [IO.File]::WriteAllText($file, '{"revision":1,"game_version":"2.0.0"}')
    Must-Fail { Get-VerifiedChannelVersion $pointer $file $local }
    $hash = (Get-FileHash $local -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText($file, "{`"revision`":1,`"files`":[{`"path`":`"hookshot_version.txt`",`"sha256`":`"$hash`"}]}")
    $pointer.sha256 = (Get-FileHash $file -Algorithm SHA256).Hash
    if ((Get-VerifiedChannelVersion $pointer $file $local) -ne '2.0.0') { throw 'Legacy version hash fallback failed' }
    [IO.File]::WriteAllText($local, 'version=3.0.0')
    $repo = Split-Path $PSScriptRoot -Parent
    function python { $global:LASTEXITCODE = 0; return '2.0.0' }
    $recovered = Get-VerifiedChannelVersion $pointer $file $local
    if (Test-ChannelCurrent '3.0.0' '3.0.0' $recovered) { throw 'Interrupted upload did not trigger rebuild' }
    function python { $global:LASTEXITCODE = 1 }
    Must-Fail { Get-VerifiedChannelVersion $pointer $file $local }
    [IO.File]::WriteAllText($file, '{"revision":1}')
    $pointer.sha256 = (Get-FileHash $file -Algorithm SHA256).Hash
    Must-Fail { Get-VerifiedChannelVersion $pointer $file $local }
} finally { Remove-Item -Recurse -Force $dir }
Write-Output 'Channel recovery tests passed'
