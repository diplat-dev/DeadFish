param()

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$webDir = Join-Path $root "web"
$includeDir = Join-Path $root "engine\include"
$localEmpp = Join-Path $root "tools\emsdk\upstream\emscripten\em++.bat"

$empp = if (Test-Path $localEmpp) {
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
