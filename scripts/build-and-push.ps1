<#
.SYNOPSIS
    Builds and pushes the BL-HAOS Bridge multi-arch images locally to GHCR,
    replacing the GitHub Actions build for faster iteration.

.DESCRIPTION
    Reads BUILD_FROM base images from build.yaml and the version from
    config.yaml, then uses `docker buildx` to build + push one image per
    architecture (aarch64, amd64, armv7) tagged with the current version
    and `latest`.

    Requires a one-time `docker login ghcr.io` with a PAT that has
    `write:packages` scope. This script never reads or stores credentials.

.PARAMETER Owner
    GHCR namespace/owner. Defaults to "mahmoud-eltabakh".

.PARAMETER Archs
    Architectures to build. Defaults to all three add-on architectures.
#>

param(
    [string]$Owner = "mahmoud-eltabakh",
    [string[]]$Archs = @("aarch64", "amd64", "armv7"),
    [string]$Builder = "bl-haos-builder"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

$archPlatformMap = @{
    "aarch64" = "linux/arm64"
    "amd64"   = "linux/amd64"
    "armv7"   = "linux/arm/v7"
}

function Get-YamlScalar([string]$Path, [string]$Key) {
    $line = Select-String -Path $Path -Pattern "^\s*${Key}:\s*(.+)$" | Select-Object -First 1
    if (-not $line) { throw "Could not find key '$Key' in $Path" }
    return $line.Matches[0].Groups[1].Value.Trim('"', "'", ' ')
}

function Get-BuildFrom([string]$Path, [string]$Arch) {
    $line = Select-String -Path $Path -Pattern "^\s*${Arch}:\s*(.+)$" | Select-Object -First 1
    if (-not $line) { throw "Could not find build_from entry for '$Arch' in $Path" }
    return $line.Matches[0].Groups[1].Value.Trim('"', "'", ' ')
}

$version = Get-YamlScalar (Join-Path $repoRoot "config.yaml") "version"
Write-Output "Building BL-HAOS Bridge version $version for: $($Archs -join ', ')"

if (-not (docker buildx ls | Select-String -SimpleMatch $Builder)) {
    throw "Buildx builder '$Builder' not found. Create it first (docker buildx create --name $Builder --driver docker-container --use)."
}
docker buildx use $Builder

foreach ($arch in $Archs) {
    $platform = $archPlatformMap[$arch]
    if (-not $platform) { throw "Unknown architecture '$arch'" }
    $buildFrom = Get-BuildFrom (Join-Path $repoRoot "build.yaml") $arch
    $image = "ghcr.io/$Owner/$arch-bl-haos-bridge"

    Write-Output ""
    Write-Output "=== Building $image ($platform) ==="
    docker buildx build `
        --platform $platform `
        --build-arg "BUILD_FROM=$buildFrom" `
        --tag "${image}:${version}" `
        --tag "${image}:latest" `
        --push `
        $repoRoot

    if ($LASTEXITCODE -ne 0) {
        throw "Build failed for $arch (exit code $LASTEXITCODE)"
    }
}

Write-Output ""
Write-Output "All architectures built and pushed for version $version."
