$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'AutoShip-Game.ps1'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Syntax errors' }
foreach ($name in @('Assert-PublishingHost', 'Assert-ChannelPointer')) {
    $fn = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    if (-not $fn) { throw "Missing $name" }
    Invoke-Expression $fn.Extent.Text
}
function Must-Fail([scriptblock]$action) { $failed = $false; try { & $action } catch { $failed = $true }; if (-not $failed) { throw 'Expected failure' } }
Must-Fail { Assert-PublishingHost '' 'HOST1' }
Must-Fail { Assert-PublishingHost 'HOST2' 'HOST1' }
Assert-PublishingHost 'host1' 'HOST1'
Must-Fail { Assert-ChannelPointer ([pscustomobject]@{ revision=1; manifest='../bad'; sha256=('a' * 64) }) }
Must-Fail { Assert-ChannelPointer ([pscustomobject]@{ revision=1; manifest='game-manifest-1.json'; sha256='bad' }) }
Assert-ChannelPointer ([pscustomobject]@{ revision=1; manifest='game-manifest-1.json'; sha256=('a' * 64) })
$source = Get-Content -LiteralPath $scriptPath -Raw
if ($source -match 'Repair-LegacyManifest|release'', ''upload'', ''game-v1'', \$legacy') { throw 'Publisher must never write the mutable legacy release asset' }
if ($source.IndexOf('Get-VerifiedChannelVersion $previous') -gt $source.IndexOf('GAMESHIP status=current')) { throw 'Current exit must follow channel reconciliation' }
if ($source -match 'finitely many publishers must converge') { throw 'Cross-host convergence is not guaranteed' }
Write-Output 'Publisher safety tests passed'
