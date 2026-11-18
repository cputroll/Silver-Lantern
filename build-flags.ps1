function Write-BuildFlags {
  param([Parameter(Mandatory)][string]$Directory, [switch]$Qa)
  New-Item -ItemType Directory -Force -Path $Directory | Out-Null
  $value = if ($Qa) { 'True' } else { 'False' }
  [System.IO.File]::WriteAllText((Join-Path $Directory 'build_flags.py'), "QA_SEAMS = $value`n", [System.Text.UTF8Encoding]::new($false))
}

function Get-BuildArtifactName {
  param([switch]$Qa)
  if ($Qa) { 'HeyListen-Get-qa.zip' } else { 'HeyListen-Get.zip' }
}
