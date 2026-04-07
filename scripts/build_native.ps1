param(
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Release",

    [ValidateSet("Generic", "Native", "All")]
    [string]$Target = "Generic"
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

$baseFlags = @(
    "-std=c++20",
    "-I", $includeDir,
    "-Wall",
    "-Wextra"
)

if (Test-Path $fathomDir) {
    $baseFlags += @(
        "-DDEADFISH_WITH_SYZYGY=1",
        "-D_CRT_SECURE_NO_WARNINGS",
        "-D_SILENCE_CXX20_ATOMIC_INIT_DEPRECATION_WARNING",
        "-DTB_NO_HW_POP_COUNT=1",
        "-I", $fathomDir
    )
}

if ($Configuration -eq "Debug") {
    $baseFlags += @("-O0", "-g")
} else {
    $baseFlags += @("-O2")
}

$sources = @(
    (Join-Path $root "engine\src\engine.cpp")
)
if (Test-Path $fathomDir) {
    $sources += @("-x", "c++", (Join-Path $fathomDir "tbprobe.c"))
}

function Build-DeadFishTarget {
    param(
        [string]$Name,
        [string[]]$ExtraFlags,
        [string]$Suffix
    )

    $flags = @($baseFlags + $ExtraFlags)
    $engineOutput = Join-Path $buildDir ("deadfish{0}.exe" -f $Suffix)
    $testsOutput = Join-Path $buildDir ("deadfish_tests{0}.exe" -f $Suffix)

    & $clang @flags (Join-Path $root "cli\main.cpp") @sources "-o" $engineOutput
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & $clang @flags (Join-Path $root "tests\main.cpp") @sources "-o" $testsOutput
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "Built ${Name}:"
    Write-Host "  $engineOutput"
    Write-Host "  $testsOutput"
}

switch ($Target) {
    "Generic" {
        Build-DeadFishTarget -Name "generic" -ExtraFlags @() -Suffix ""
    }
    "Native" {
        Build-DeadFishTarget -Name "native" -ExtraFlags @("-O3", "-march=native", "-mtune=native", "-mpopcnt") -Suffix "_native"
    }
    "All" {
        Build-DeadFishTarget -Name "generic" -ExtraFlags @() -Suffix ""
        Build-DeadFishTarget -Name "native" -ExtraFlags @("-O3", "-march=native", "-mtune=native", "-mpopcnt") -Suffix "_native"
    }
}
