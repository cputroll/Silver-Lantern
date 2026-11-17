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

  # Discover every fast test module; test_release invokes this script to package.
  $testModules = @(Get-ChildItem -Path $here -Filter 'test*.py' -File |
    Where-Object { $_.BaseName -ne 'test_release' } |
    Sort-Object Name | ForEach-Object { $_.BaseName })
  if ($testModules.Count -eq 0) { throw 'No fast test modules were found.' }
  python -m unittest @testModules
  if ($LASTEXITCODE -ne 0) { throw "Tests failed ($LASTEXITCODE)." }

  $out = Join-Path $here 'dist-release'
  $stage = Join-Path $out 'stage'
  $build = Join-Path $out 'build'
  $spec = Join-Path $out 'spec'
  New-Item -ItemType Directory -Force -Path $out, $stage, $build, $spec | Out-Null
  Remove-Item (Join-Path $stage '*') -Recurse -Force -ErrorAction SilentlyContinue
  $sdlZip = Join-Path $out 'SDL2-devel-2.30.10-VC.zip'
  $sdlDir = Join-Path $out 'SDL2-2.30.10'
  $sdlDll = Join-Path $sdlDir 'SDL2-2.30.10\lib\x64\SDL2.dll'
  if (-not (Test-Path $sdlDll -PathType Leaf)) {
    if (-not (Test-Path $sdlZip -PathType Leaf)) {
      Invoke-WebRequest -Uri 'https://github.com/libsdl-org/SDL/releases/download/release-2.30.10/SDL2-devel-2.30.10-VC.zip' -OutFile $sdlZip
    }
    Expand-Archive -Path $sdlZip -DestinationPath $sdlDir -Force
  }
  $iconPath = (Resolve-Path (Join-Path $here 'assets\silver-lantern.ico')).Path
  $sdlDll = (Resolve-Path $sdlDll).Path
  $ttfDll = (python -c "import pathlib,sdl2dll; print(pathlib.Path(sdl2dll.__file__).parent / 'dll' / 'SDL2_ttf.dll')").Trim()
  if (-not (Test-Path $ttfDll -PathType Leaf)) { throw 'SDL2_ttf.dll not found; install launcher-requirements.txt.' }
  python -m PyInstaller --noconfirm --clean --onefile --name SilverLantern --icon $iconPath --hidden-import launcher --hidden-import curl_cffi --collect-all curl_cffi --add-binary "${sdlDll};." --add-binary "${ttfDll};." --distpath $stage --workpath $build --specpath $spec Get-HeyListen.py
  if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)." }
  $exe = Join-Path $stage 'SilverLantern.exe'
  if (-not (Test-Path $exe -PathType Leaf)) { throw 'PyInstaller did not produce SilverLantern.exe.' }
  $bakedVersion = (& $exe --version --manifest (Join-Path $here 'sources.json') | Out-String).Trim()
  if ($LASTEXITCODE -ne 0) { throw "Executable version check failed ($LASTEXITCODE)." }
  if ($bakedVersion -cne $version) { throw "Executable version $bakedVersion does not match $version in sources.json and tag $Tag." }
  $support = Join-Path $stage 'support'
  New-Item -ItemType Directory -Force -Path $support | Out-Null
  Copy-Item $exe (Join-Path $stage 'Run Silver Lantern.exe')
  Move-Item $exe (Join-Path $support 'SilverLantern.exe')
  Copy-Item (Join-Path $support 'SilverLantern.exe') (Join-Path $support 'HeyListen-Get.exe')
  Copy-Item $sdlDll $support
  Copy-Item sources.json, README.md, Apply-HeyListen-Preset.ps1, SilverLantern-Get.cmd, SilverLantern-Preset.cmd, Update-Game.ps1, Watch-Game.ps1, SilverLantern-AutoUpdate.cmd $support
  [System.IO.File]::WriteAllText((Join-Path $stage 'Read me.txt'), "Double-click Run Silver Lantern.exe to start.`r`nMore information is in the support folder.`r`n", [System.Text.UTF8Encoding]::new($false))
  $zip = Join-Path $out 'HeyListen-Get.zip'
  Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -Force
  $sha = [System.Security.Cryptography.SHA256]::Create()
  $fs = [System.IO.File]::OpenRead($zip)
  try { $hash = ([System.BitConverter]::ToString($sha.ComputeHash($fs)) -replace '-', '').ToLowerInvariant() }
  finally { $fs.Dispose(); $sha.Dispose() }
  [System.IO.File]::WriteAllText((Join-Path $out 'SHA256SUMS.txt'), "$hash  HeyListen-Get.zip`n", [System.Text.UTF8Encoding]::new($false))
  Write-Host "Packaged $zip ($hash)"
  if (-not $NoPublish) {
    gh release create $Tag $zip (Join-Path $out 'SHA256SUMS.txt') --title "HeyListen Get $Tag" --generate-notes
    if ($LASTEXITCODE -ne 0) { throw "gh release create failed ($LASTEXITCODE)." }
  }
} finally {
  Pop-Location
}
