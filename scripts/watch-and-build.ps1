<#
.SYNOPSIS
    Watches BL-HAOS Bridge source files and triggers a local build+push on
    any change, without waiting for a commit.

.DESCRIPTION
    Complements the post-commit hook for a tighter local dev loop: watches
    Dockerfile, build.yaml, config.yaml, backend/, rootfs/, and web_ui/dist
    recursively, debounces rapid successive changes, then runs
    build-and-push.ps1. Stop with Ctrl+C.
#>

param(
    [int]$DebounceSeconds = 5
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$watchPaths = @("Dockerfile", "build.yaml", "config.yaml", "backend", "rootfs", "web_ui/dist")

$watchers = @()
$script:pending = $false

foreach ($relPath in $watchPaths) {
    $fullPath = Join-Path $repoRoot $relPath
    if (-not (Test-Path $fullPath)) { continue }

    $isDir = (Get-Item $fullPath).PSIsContainer
    $watcher = New-Object System.IO.FileSystemWatcher
    $watcher.Path = if ($isDir) { $fullPath } else { Split-Path $fullPath }
    $watcher.Filter = if ($isDir) { "*.*" } else { Split-Path $fullPath -Leaf }
    $watcher.IncludeSubdirectories = $isDir
    $watcher.EnableRaisingEvents = $true

    Register-ObjectEvent -InputObject $watcher -EventName Changed -Action { $script:pending = $true } | Out-Null
    Register-ObjectEvent -InputObject $watcher -EventName Created -Action { $script:pending = $true } | Out-Null
    Register-ObjectEvent -InputObject $watcher -EventName Deleted -Action { $script:pending = $true } | Out-Null
    Register-ObjectEvent -InputObject $watcher -EventName Renamed -Action { $script:pending = $true } | Out-Null

    $watchers += $watcher
}

Write-Output "Watching for changes under: $($watchPaths -join ', ')"
Write-Output "Press Ctrl+C to stop."

try {
    while ($true) {
        Start-Sleep -Seconds 1
        if ($script:pending) {
            Start-Sleep -Seconds $DebounceSeconds
            $script:pending = $false
            Write-Output "[watch] Change detected; building and pushing images..."
            & (Join-Path $PSScriptRoot "build-and-push.ps1")
        }
    }
} finally {
    foreach ($watcher in $watchers) { $watcher.Dispose() }
}
