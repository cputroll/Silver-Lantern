# Caller must hold the shared ship lock while checking and using this marker.
function Assert-OwnedProcessClear([string]$MarkerPath) {
    if ([IO.File]::Exists($MarkerPath) -or [IO.Directory]::Exists($MarkerPath)) {
        throw "Owned updater recovery required; marker present: $MarkerPath"
    }
}
function Invoke-GuardedOwnedProcess([string]$MarkerPath, [scriptblock]$Run) {
    Assert-OwnedProcessClear $MarkerPath
    # Persist before any launch; a crash or an uncertain teardown leaves the marker behind.
    $file = [IO.File]::Open($MarkerPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes("Updater may still be running. Verify cleanup before removing this marker.`r`n")
        $file.Write($bytes, 0, $bytes.Length)
        $file.Flush($true)
    } finally { $file.Dispose() }
    $result = & $Run
    # OwnedProcess returns only after job emptiness is verified.
    [IO.File]::Delete($MarkerPath)
    return $result
}
