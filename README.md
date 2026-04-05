# DeadFish

DeadFish is a custom C++ chess engine with a native command-line interface and a lightweight browser demo compiled through WebAssembly.

This repository is organized around the active engine under `engine/`, `cli/`, `tests/`, and `web/`.

## Features

- Custom bitboard-based engine core with legal move generation and make/unmake state tracking
- Iterative-deepening negamax search with alpha-beta pruning, quiescence search, transposition table, killer moves, and history heuristic
- Tapered evaluation with material, piece-square terms, mobility, king safety, pawn structure, passed pawns, bishop pair, rook file bonuses, simplification, and tempo
- Native CLI commands for `play`, `search`, `perft`, `legal`, and `bench`
- Plain HTML/JavaScript browser UI backed by the same engine compiled to WebAssembly

## Repository Layout

- `engine/`: core chess engine, search, evaluation, and WASM bridge
- `cli/`: native command-line harness
- `tests/`: native correctness and search regression tests
- `web/`: browser demo UI, worker, and generated WASM output
- `scripts/`: PowerShell build helpers and the native/WASM parity checker

## Requirements

- Native build: `clang++` with C++20 support
- Browser build: Emscripten `em++`
- Parity check: Node.js

No environment variables or `.env` file are required for normal development or use.

## Native Build And Test

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_native.ps1
.\build\deadfish_tests.exe
.\build\deadfish.exe search --depth 5
```

The native build script looks for `clang++` on `PATH` first and falls back to the default LLVM install path on Windows.

## Browser Build

Install Emscripten so `em++` is available on `PATH`, or place a local `emsdk` checkout under `tools/emsdk` for personal use. The `tools/emsdk` path is ignored by git and is not meant to be committed.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_wasm.ps1
```

That generates:

- `web/deadfish_wasm.js`
- `web/deadfish_wasm.wasm`

Serve [`web/index.html`](./web/index.html) from a local static server after building the WASM bundle.

## WASM Parity Check

After both the native and WASM builds exist:

```powershell
node .\scripts\wasm_parity.mjs
```

The parity script compares legal move generation, FEN transitions, and search results between the native executable and the browser-targeted module.

## CLI Commands

- `search [--fen FEN] [--depth N] [--movetime MS] [--json]`
- `perft [--fen FEN] --depth N [--divide]`
- `legal [--fen FEN]`
- `play [--fen FEN] [--depth N] [--movetime MS]`
- `bench [--depth N] [--movetime MS]`

## Validation

The native test suite covers:

- FEN parse and serialize round trips
- make/unmake state restoration
- castling, en passant, promotion, mate, stalemate, repetition, fifty-move, and insufficient-material cases
- perft on the starting position, Kiwipete, and additional tactical reference positions
- search smoke tests for mate finding and time-limited search
