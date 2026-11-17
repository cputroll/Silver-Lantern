<#
  Apply-HeyListen-Preset.ps1
  Writes a performance preset into a built HeyListen game folder's hookshot_options.ini:
  NVIDIA DLSS on, DLSS Performance (50% render), Sharpness 80%, Reflex on, frame pacing on.

  Keys are the engine's own (engine/gpu/vk/vk_menu.cpp ini_key):
    dlss       0 Game's own | 1 NVIDIA DLSS | 2 DLAA | 3 FSR 3.1 | 4 FSR 4 | 5 FSR 4 Native AA
    resolution 0 Auto | 1 100% | 2 75% | 3 67% (Quality) | 4 50% (Performance) | 5 58% (Balanced) | 6 33% (Ultra)
    sharpness  0-100
    reflex     0 Off | 1 On | 2 On+Boost
    pace       0/1 frame pacing
    framegen   0 Off | 1 Auto | 2 2x | 3 3x ...

  DLSS itself must be compiled in: the Builder's "NVIDIA DLSS" box has to be ticked for that build.
#>
[CmdletBinding()]
param(
  [string]$GameFolder = (Join-Path $env:USERPROFILE 'Games\HeyListen'),
  [ValidateSet('Performance','Quality','Balanced','UltraPerformance','Auto')]
  [string]$RenderMode = 'Performance',
  [int]$Sharpness = 80,
  [ValidateSet('On','Off','Boost')] [string]$Reflex = 'On',
  [ValidateSet('Off','Auto','2x')] [string]$FrameGeneration = 'Auto',
  [switch]$NoFramePacing
)

$ini = Join-Path $GameFolder 'hookshot_options.ini'
if (-not (Test-Path $GameFolder)) {
  Write-Error "Game folder not found: $GameFolder  (build the game first, or pass -GameFolder)"
  exit 1
}

$res = @{ Auto = 0; Performance = 4; Quality = 3; Balanced = 5; UltraPerformance = 6 }[$RenderMode]
$ref = @{ Off = 0; On = 1; Boost = 2 }[$Reflex]
$fg  = @{ Off = 0; Auto = 1; '2x' = 2 }[$FrameGeneration]

$wanted = [ordered]@{
  dlss       = 1          # NVIDIA DLSS
  resolution = $res       # DLSS Performance = 50% = value 4
  sharpness  = $Sharpness # DLSS is softer than the game's own: 80% is the documented pairing
  reflex     = $ref
  pace       = if ($NoFramePacing) { 0 } else { 1 }
  framegen   = $fg
}

# Merge: keep every line we do not own (controls, pad_/key_, unknown keys).
$lines = @()
if (Test-Path $ini) { $lines = Get-Content $ini }
$out  = New-Object System.Collections.Generic.List[string]
$seen = @{}
foreach ($line in $lines) {
  $eq = $line.IndexOf('=')
  if ($eq -le 0) { $out.Add($line); continue }
  $key = $line.Substring(0, $eq).Trim()
  if ($wanted.Contains($key)) {
    if (-not $seen.ContainsKey($key)) { $out.Add("$key=$($wanted[$key])"); $seen[$key] = $true }
  } else {
    $out.Add($line)
  }
}
foreach ($k in $wanted.Keys) {
  if (-not $seen.ContainsKey($k)) { $out.Add("$k=$($wanted[$k])") }
}

Set-Content -Path $ini -Value $out -Encoding ASCII
Write-Host "Preset written to $ini"
$wanted.GetEnumerator() | ForEach-Object { "  {0} = {1}" -f $_.Key, $_.Value }
Write-Host "Reminder: DLSS only works if this game was built with the Builder's 'NVIDIA DLSS' box ticked."
