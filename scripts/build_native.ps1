param(
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$buildDir = Join-Path $root "build"
$includeDir = Join-Path $root "engine\include"
$fathomDir = Join-Path $root "third_party\fathom\src"
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

if (Test-Path $fathomDir) {
    $common += @(
        "-DDEADFISH_WITH_SYZYGY=1",
        "-D_CRT_SECURE_NO_WARNINGS",
        "-D_SILENCE_CXX20_ATOMIC_INIT_DEPRECATION_WARNING",
        "-DTB_NO_HW_POP_COUNT=1",
        "-I", $fathomDir
    )
}

if ($Configuration -eq "Debug") {
    $common += @("-O0", "-g")
} else {
    $common += @("-O2")
}

$nativeSources = @(
    (Join-Path $root "cli\main.cpp"),
    (Join-Path $root "engine\src\engine.cpp")
)
if (Test-Path $fathomDir) {
    $nativeSources += @("-x", "c++", (Join-Path $fathomDir "tbprobe.c"))
}

& $clang @common @nativeSources "-o" (Join-Path $buildDir "deadfish.exe")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$testSources = @(
    (Join-Path $root "tests\main.cpp"),
    (Join-Path $root "engine\src\engine.cpp")
)
if (Test-Path $fathomDir) {
    $testSources += @("-x", "c++", (Join-Path $fathomDir "tbprobe.c"))
}

& $clang @common @testSources "-o" (Join-Path $buildDir "deadfish_tests.exe")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Built:"
Write-Host "  $buildDir\deadfish.exe"
Write-Host "  $buildDir\deadfish_tests.exe"
