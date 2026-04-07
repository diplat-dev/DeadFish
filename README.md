# DeadFish

DeadFish is a native-first C++ chess engine with a CLI harness, a minimal UCI frontend, and a growing strength-development toolchain around profiling, gauntlets, and NNUE training.

The browser/WebAssembly path has been intentionally removed so the active codebase can focus on native engine strength work.

## Features

- Custom bitboard engine core with legal move generation, compact delta make/unmake, repetition tracking, and full Zobrist hashing
- Iterative deepening with alpha-beta, aspiration windows, PVS, quiescence, null-move pruning, LMR, killer/history heuristics, SEE-based move handling, and a clustered fixed-size TT
- Classical tapered evaluation with material, piece-square terms, mobility, king safety, pawn structure, passed pawns, bishop pair, rook file bonuses, simplification, and tempo
- Minimal UCI support for `uci`, `isready`, `ucinewgame`, `position`, `go depth`, `go movetime`, `go wtime/btime/winc/binc/movestogo`, `go infinite`, `stop`, `quit`, and engine options
- Bundled Polyglot opening-book support through `data/book.bin`
- Syzygy probing through vendored [Fathom](./third_party/fathom/README.md) with external tablebase files
- Native CLI commands for `play`, `search`, `perft`, `legal`, `status`, `fen`, and `bench`
- Python-based strength tooling for tactical regression, generic-vs-native profiling, cutechess gauntlets, self-play PGN generation, and first-pass NNUE data/training/export flow

## Repository Layout

- `engine/`: core chess engine, search, and evaluation
- `cli/`: native CLI and UCI entrypoint
- `data/`: bundled runtime assets and example gauntlet configuration
- `tests/`: native correctness and search regression tests
- `third_party/fathom/`: vendored Syzygy probing backend
- `scripts/`: build helpers, UCI smoke tests, bench tools, and gauntlet wrappers
- `training/`: NNUE data extraction, score annotation, training, and export pipeline

## Requirements

- Native build: `clang++` with C++20 support
- Scripted smoke and workflow helpers: Python 3
- Optional match tooling: `cutechess-cli`
- Optional NNUE training pipeline: PyTorch, NumPy, and `python-chess` via `training/requirements.txt`

No environment variables or `.env` file are required for normal development or use.

## Native Build And Test

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_native.ps1
.\build\deadfish_tests.exe
.\build\deadfish.exe
.\build\deadfish.exe search --depth 5
python .\scripts\uci_smoke.py
python .\scripts\tactical_suite.py
```

The native build script looks for `clang++` on `PATH` first, falls back to the default LLVM install path on Windows, and automatically enables Syzygy probing when `third_party/fathom/` is present.

It also supports a generic release build, a native-tuned build, or both:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_native.ps1 -Target Generic
powershell -ExecutionPolicy Bypass -File .\scripts\build_native.ps1 -Target Native
powershell -ExecutionPolicy Bypass -File .\scripts\build_native.ps1 -Target All
```

The native target emits `build/deadfish_native.exe` and `build/deadfish_tests_native.exe` with CPU-targeted compiler flags for local profiling and strength work.

## Recommended Workflows

### 1. Edit-build-verify loop

Use this after engine or search changes:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_native.ps1 -Target All
.\build\deadfish_tests.exe
.\build\deadfish_tests_native.exe
python .\scripts\uci_smoke.py
python .\scripts\tactical_suite.py
```

This is the default "did I break anything?" workflow. If these pass, the native engine is still in a good baseline state.

### 2. Lightweight speed check

Use this after hot-path or compiler-flag changes:

```powershell
python .\scripts\profile_bench.py --repeat 5
```

This compares `build/deadfish.exe` and `build/deadfish_native.exe` on the fixed bench suite. For one-off executable comparisons against another build, use:

```powershell
python .\scripts\bench_compare.py --engine-b path\to\other\engine.exe
```

### 3. Small internal engine match

Use this when comparing two DeadFish builds:

```powershell
python .\scripts\selfplay_gauntlet.py --engine-b path\to\other\deadfish.exe --movetime 75
```

This is a quick native regression check, not a replacement for a serious Elo run.

### 4. External engine ladder

Use this when comparing DeadFish against a small known ladder:

1. Edit [`data/external_gauntlet.example.json`](./data/external_gauntlet.example.json) so the engine paths match your local installs.
2. Run:

```powershell
python .\scripts\external_gauntlet.py --config .\data\external_gauntlet.example.json
```

For a single direct cutechess match instead of the whole ladder:

```powershell
python .\scripts\cutechess_match.py --engine-b path\to\other\engine.exe --games 20 --tc 8+0.08
```

These scripts require `cutechess-cli` plus the target engine binaries.

## UCI Usage

Run `.\build\deadfish.exe` without arguments to enter the UCI loop. The engine also supports `.\build\deadfish.exe uci` explicitly.

Implemented UCI options:

- `Hash`
- `Clear Hash`
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
- `python .\scripts\profile_bench.py --repeat 5`
  Compares the generic and native-tuned DeadFish builds on the fixed bench suite and reports elapsed-time and NPS speedups.
- `python .\scripts\tactical_suite.py`
  Runs the fixed tactical regression suite in [`data/tactical_suite.txt`](./data/tactical_suite.txt).
- `python .\scripts\selfplay_gauntlet.py --engine-b path\to\other\engine.exe`
  Runs a small opening-suite gauntlet with both color assignments using UCI.
- `python .\scripts\cutechess_match.py --engine-b path\to\other\engine.exe`
  Runs a configurable UCI-vs-UCI match through `cutechess-cli`, with optional openings and SPRT parameters.
- `python .\scripts\external_gauntlet.py --config .\data\external_gauntlet.example.json`
  Runs DeadFish against a configured engine ladder through `cutechess-cli`.

The `cutechess`-based scripts require a local `cutechess-cli` install and external engine binaries; they are included as measurement tooling and are not required for normal builds.

## Training Toolkit

The NNUE data and training flow lives under [`training/`](./training/README.md). It currently includes:

- `generate_selfplay_pgn.py`
  Generates DeadFish self-play PGNs through `cutechess-cli`.
- `extract_positions.py`
  Samples JSONL training positions from PGN games.
- `annotate_positions.py`
  Annotates JSONL positions with DeadFish UCI search scores.
- `train_nnue.py`
  Trains a first-pass HalfKP-style NNUE checkpoint in PyTorch.
- `export_nnue.py`
  Exports the checkpoint to a custom DeadFish `.nnue` binary.

This training/export pipeline is ready to use independently, but the engine does not consume NNUE weights yet.

The recommended NNUE workflow is:

1. Generate or collect PGNs.
2. Extract sampled positions to JSONL.
3. Annotate them with DeadFish scores if you want score-driven targets.
4. Train a checkpoint in PyTorch.
5. Export a `.nnue` blob for future engine integration.

See [`training/README.md`](./training/README.md) for the exact commands and file flow.

## Validation

The native test suite covers:

- FEN parse and serialize round trips
- make/unmake and null-move state restoration
- castling, en passant, promotion, mate, stalemate, repetition, fifty-move, and insufficient-material cases
- perft on the starting position, Kiwipete, and additional tactical reference positions
- SEE regression checks for winning, equal, and losing exchanges
- search smoke tests for mate finding, movetime limits, and clock-based limits
- bundled-book usage and clean fallback when book or Syzygy paths are missing

## Current Roadmap Status

The current tree includes:

- the first native speed refactor: compact undo, staged move picking, and clustered TT replacement
- native generic-vs-tuned profiling and repeatable regression tooling
- cutechess-based gauntlet scaffolding and a small example engine ladder
- the first NNUE data/training/export pipeline under `training/`

Still planned for the engine itself:

- NNUE inference and accumulator integration
- `UseNNUE` / `EvalFile` runtime support
- multi-threaded search with `Threads`
