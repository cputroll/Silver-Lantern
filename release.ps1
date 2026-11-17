<# Build HeyListen-Get.zip and SHA256SUMS.txt; use -NoPublish to build only. #>
[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string]$Tag,
  [switch]$NoPublish
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $here
try {
  $version = (Get-Content sources.json -Raw | ConvertFrom-Json).tool.version
  if ($Tag -cne "v$version") { throw "Tag $Tag does not match v$version in sources.json." }

  # The packaging test invokes this script, so run only the non-packaging suite here.
  python -m unittest test_updater
  if ($LASTEXITCODE -ne 0) { throw "Updater tests failed ($LASTEXITCODE)." }

  $out = Join-Path $here 'dist-release'
  $stage = Join-Path $out 'stage'
  $build = Join-Path $out 'build'
  $spec = Join-Path $out 'spec'
  New-Item -ItemType Directory -Force -Path $out, $stage, $build, $spec | Out-Null
  Remove-Item (Join-Path $stage '*') -Recurse -Force -ErrorAction SilentlyContinue
  python -m PyInstaller --noconfirm --clean --onefile --name HeyListen-Get --hidden-import curl_cffi --collect-all curl_cffi --distpath $stage --workpath $build --specpath $spec Get-HeyListen.py
  if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)." }
  $exe = Join-Path $stage 'HeyListen-Get.exe'
  if (-not (Test-Path $exe -PathType Leaf)) { throw 'PyInstaller did not produce HeyListen-Get.exe.' }
  $bakedVersion = (& $exe --version --manifest (Join-Path $here 'sources.json') | Out-String).Trim()
  if ($LASTEXITCODE -ne 0) { throw "Executable version check failed ($LASTEXITCODE)." }
  if ($bakedVersion -cne $version) { throw "Executable version $bakedVersion does not match $version in sources.json and tag $Tag." }
  Copy-Item sources.json, README.md, Apply-HeyListen-Preset.ps1, SilverLantern-Get.cmd, SilverLantern-Preset.cmd $stage
  $zip = Join-Path $out 'HeyListen-Get.zip'
  Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -Force
  $hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLowerInvariant()
  [System.IO.File]::WriteAllText((Join-Path $out 'SHA256SUMS.txt'), "$hash  HeyListen-Get.zip`n", [System.Text.UTF8Encoding]::new($false))
  Write-Host "Packaged $zip ($hash)"
  if (-not $NoPublish) {
    gh release create $Tag $zip (Join-Path $out 'SHA256SUMS.txt') --title "HeyListen Get $Tag" --generate-notes
    if ($LASTEXITCODE -ne 0) { throw "gh release create failed ($LASTEXITCODE)." }
  }
} finally {
  Pop-Location
}
