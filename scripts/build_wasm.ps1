param()

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$webDir = Join-Path $root "web"
$includeDir = Join-Path $root "engine\include"
$localEmsdk = Join-Path $root "tools\emsdk"
$localEmpp = Join-Path $localEmsdk "upstream\emscripten\em++.bat"
$localConfig = Join-Path $localEmsdk ".emscripten"

$empp = if (Test-Path $localEmpp) {
    if (Test-Path $localConfig) {
        $env:EMSDK = $localEmsdk
        $env:EM_CONFIG = $localConfig

        $nodeDir = Get-ChildItem -Path (Join-Path $localEmsdk "node") -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
        $pythonDir = Get-ChildItem -Path (Join-Path $localEmsdk "python") -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($nodeDir) {
            $env:EMSDK_NODE = Join-Path $nodeDir.FullName "bin\node.exe"
        }
        if ($pythonDir) {
            $env:EMSDK_PYTHON = Join-Path $pythonDir.FullName "python.exe"
        }

        $pathEntries = @(
            $localEmsdk,
            (Join-Path $localEmsdk "upstream\emscripten"),
            (Join-Path $localEmsdk "upstream\bin")
        )
        if ($nodeDir) {
            $pathEntries += (Join-Path $nodeDir.FullName "bin")
        }
        if ($pythonDir) {
            $pathEntries += $pythonDir.FullName
        }
        $env:PATH = (($pathEntries | Where-Object { $_ }) -join ";") + ";" + $env:PATH
    }
    $localEmpp
} else {
    $command = Get-Command em++ -ErrorAction SilentlyContinue
    if ($command) { $command.Source } else { $null }
}

if (-not $empp) {
    throw "em++ is required to build the WebAssembly target. Install Emscripten or place emsdk in .\\tools\\emsdk."
}

$exports = @(
    "_df_reset",
    "_df_set_fen",
    "_df_get_fen",
    "_df_legal_moves_csv",
    "_df_apply_move",
    "_df_status_json",
    "_df_search_json",
    "_df_last_error"
) -join ","

$runtimeExports = @("cwrap") -join ","

& $empp `
    "-std=c++20" `
    "-O3" `
    "-I" $includeDir `
    (Join-Path $root "engine\src\engine.cpp") `
    (Join-Path $root "engine\src\wasm_api.cpp") `
    "-s" "MODULARIZE=1" `
    "-s" "EXPORT_NAME=DeadFishModule" `
    "-s" "ENVIRONMENT=web,worker,node" `
    "-s" "ALLOW_MEMORY_GROWTH=1" `
    "-s" "EXPORTED_FUNCTIONS=[$exports]" `
    "-s" "EXPORTED_RUNTIME_METHODS=[$runtimeExports]" `
    "-o" (Join-Path $webDir "deadfish_wasm.js")

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Built:"
Write-Host "  $webDir\deadfish_wasm.js"
Write-Host "  $webDir\deadfish_wasm.wasm"
