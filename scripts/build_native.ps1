param(
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$buildDir = Join-Path $root "build"
$includeDir = Join-Path $root "engine\include"
$defaultClang = "C:\Program Files\LLVM\bin\clang++.exe"
$command = Get-Command clang++ -ErrorAction SilentlyContinue
$clang = if ($command) {
    $command.Source
} elseif (Test-Path $defaultClang) {
    $defaultClang
} else {
    $null
}

if (-not $clang) {
    throw "clang++ is required to build the native targets. Install LLVM or add clang++ to PATH."
}

New-Item -ItemType Directory -Force -Path $buildDir | Out-Null

$common = @(
    "-std=c++20",
    "-I", $includeDir,
    "-Wall",
    "-Wextra"
)

if ($Configuration -eq "Debug") {
    $common += @("-O0", "-g")
} else {
    $common += @("-O2")
}

& $clang @common (Join-Path $root "cli\main.cpp") (Join-Path $root "engine\src\engine.cpp") "-o" (Join-Path $buildDir "deadfish.exe")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $clang @common (Join-Path $root "tests\main.cpp") (Join-Path $root "engine\src\engine.cpp") "-o" (Join-Path $buildDir "deadfish_tests.exe")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Built:"
Write-Host "  $buildDir\deadfish.exe"
Write-Host "  $buildDir\deadfish_tests.exe"
