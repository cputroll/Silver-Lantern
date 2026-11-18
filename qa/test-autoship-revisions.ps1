$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'AutoShip-Game.ps1'
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'AutoShip script has syntax errors' }
$function = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-AvailableRevision' }, $true)
if (-not $function) { throw 'Missing revision allocation function' }
Invoke-Expression $function.Extent.Text
function Assert-Revision($expected, $base, $names) {
    $actual = Get-AvailableRevision -PreviousRevision $base -AssetNames $names
    if ($actual -ne $expected) { throw "Expected revision $expected, got $actual" }
}
Assert-Revision 1 0 @()
Assert-Revision 5 2 @('r3-part.zip', 'game-manifest-4.json')
Assert-Revision 8 7 @('r70-part.zip', 'game-manifest-80.json', 'unrelated-r8-file')
Assert-Revision 3 2 @('r3x-file.zip', 'game-manifest-3.json.bak')
$source = Get-Content -LiteralPath $scriptPath -Raw
if ($source -notmatch 'if \(\$existing\.ContainsKey\(\$asset\.asset\)\) \{ throw') { throw 'Existing bundle must be rejected, not reused by size' }
if ($source -notmatch 'foreach \(\$asset in @\(\$manifest\.assets\) \+') { throw 'All bundles must be verified before channel publication' }
if ($source.IndexOf('asset verification failed') -gt $source.IndexOf("'HEAD:refs/heads/game-channel'")) { throw 'Channel publication precedes asset verification' }
if ($source -notmatch 'force-with-lease=refs/heads/game-channel') { throw 'Channel push must use an expected-tip lease' }
if ($source -notmatch 'could not be confirmed') { throw 'Legacy manifest must be confirmed after upload' }
if ($source.IndexOf('could not be confirmed') -lt $source.IndexOf("'HEAD:refs/heads/game-channel'")) { throw 'Legacy update must follow the channel advance' }
Write-Output 'Revision allocation and publication safety tests passed'
