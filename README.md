# DeadFish

DeadFish is a custom C++ chess engine with a native CLI, a minimal UCI frontend for GUI play, and a lightweight browser demo compiled through WebAssembly.

The current codebase is organized around the active engine under `engine/`, `cli/`, `tests/`, and `web/`.

## Features

- Custom bitboard-based engine core with legal move generation, make/unmake state tracking, repetition history, full Zobrist hashing, and standard chess rules
- Iterative-deepening negamax with alpha-beta, aspiration windows, principal-variation search, quiescence, fixed-size transposition table, null-move pruning, late-move reductions, killer moves, history heuristic, and SEE-based capture handling
- Tapered evaluation with material, piece-square terms, mobility, king safety, pawn structure, passed pawns, bishop pair, rook file bonuses, simplification, and tempo
- Minimal UCI support for `uci`, `isready`, `ucinewgame`, `position`, `go depth`, `go movetime`, `go wtime/btime/winc/binc/movestogo`, `go infinite`, `stop`, `quit`, and engine options
- Bundled Polyglot opening-book support through `data/book.bin`
- Syzygy probing through vendored [Fathom](./third_party/fathom/README.md) with external tablebase files
- Native CLI commands for `play`, `search`, `perft`, `legal`, `status`, `fen`, and `bench`
- Plain HTML/JavaScript browser UI backed by the same engine compiled to WebAssembly

## Repository Layout

- `engine/`: core chess engine, search, evaluation, and WASM bridge
- `cli/`: native CLI and UCI entrypoint
- `data/`: bundled runtime assets such as the default Polyglot book
- `tests/`: native correctness and search regression tests
- `third_party/fathom/`: vendored Syzygy probing backend
- `web/`: browser demo UI, worker, and generated WASM output
- `scripts/`: build helpers, protocol smoke tests, parity checks, and comparison workflows

## Requirements

- Native build: `clang++` with C++20 support
- Browser build: Emscripten `em++`
- Scripted smoke and workflow helpers: Python 3
- Parity check: Node.js

No environment variables or `.env` file are required for normal development or use.

## Native Build And Test

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_native.ps1
.\build\deadfish_tests.exe
.\build\deadfish.exe
.\build\deadfish.exe search --depth 5
python .\scripts\uci_smoke.py
```

The native build script looks for `clang++` on `PATH` first, falls back to the default LLVM install path on Windows, and automatically enables Syzygy probing when `third_party/fathom/` is present.

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

## UCI Usage

Run `.\build\deadfish.exe` without arguments to enter the UCI loop. The engine also supports `.\build\deadfish.exe uci` explicitly.

Implemented UCI options:

- `Hash`
- `OwnBook`
- `BookPath`
- `SyzygyPath`
- `SyzygyProbeLimit`
- `MoveOverhead`

## CLI Commands

- `search [--fen FEN] [--depth N] [--movetime MS] [--json]`
- `perft [--fen FEN] --depth N [--divide]`
- `legal [--fen FEN]`
- `status [--fen FEN] [--moves uci,uci,...] [--json]`
- `fen [--fen FEN] [--moves uci,uci,...]`
- `play [--fen FEN] [--depth N] [--movetime MS]`
- `bench [--depth N] [--movetime MS]`

The `status` command is useful for automation and returns JSON with side-to-move, check, mate, stalemate, draw, and legal-move counts when used with `--json`.

## Workflow Scripts

- `python .\scripts\uci_smoke.py`
  Verifies the UCI handshake, options, depth search, movetime search, and `go infinite` / `stop`.
- `python .\scripts\bench_compare.py --engine-b path\to\other\engine.exe`
  Runs the fixed native bench suite on two executables and compares time and NPS.
- `python .\scripts\selfplay_gauntlet.py --engine-b path\to\other\engine.exe`
  Runs a small opening-suite gauntlet with both color assignments using UCI.

## Validation

The native test suite covers:

- FEN parse and serialize round trips
- make/unmake state restoration
- null-move make/unmake restoration
- castling, en passant, promotion, mate, stalemate, repetition, fifty-move, and insufficient-material cases
- perft on the starting position, Kiwipete, and additional tactical reference positions
- SEE regression checks for winning, equal, and losing exchanges
- search smoke tests for mate finding, movetime limits, and clock-based limits
- bundled-book usage and clean fallback when book or Syzygy paths are missing
