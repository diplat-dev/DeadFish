# DeadFish

DeadFish is a native-first C++ chess engine with a CLI harness, UCI support, a Windows-focused Tkinter GUI, and repeatable tooling for profiling and match testing.

The browser/WebAssembly path has been intentionally removed so the active codebase can stay focused on native engine strength work.

## Features

- Custom bitboard engine core with fixed-capacity search move lists, pseudo-legal hot-path move generation, compact make/unmake, repetition tracking, and full Zobrist hashing
- Iterative deepening search with alpha-beta, aspiration windows, PVS, quiescence, null-move pruning, probcut, razoring, adaptive LMR, singular-style TT move extension, killer/history/countermove/continuation heuristics, SEE-aware move handling, a clustered TT, and thread-aware Lazy SMP
- Classical tapered evaluation with material, piece-square terms, mobility, king safety, pawn structure, passed pawns, threats, space, activity, outposts, rook file/7th-rank bonuses, endgame scaling, simplification, and tempo
- UCI support for standard GUI play, fixed-depth search, node limits, movetime, clock-managed games, infinite analysis, stop, and engine options
- Windows-first Tkinter GUI with live analysis, White/Black player slots, clock games, FEN tools, PGN notation, and dynamic UCI option editing
- Bundled Polyglot opening-book support through `data/book.bin`
- Syzygy probing through vendored [Fathom](./third_party/fathom/README.md) with external tablebase files
- Native CLI commands for `play`, `search`, `perft`, `legal`, `status`, `fen`, `eval`, and `bench`
- Python tooling for tactical regression, generic-vs-native profiling, thread scaling, self-play, and cutechess gauntlets

Neural-eval and training details live in [NNUE.md](./NNUE.md).

## Repository Layout

- `engine/`: core chess engine, search, evaluation, UCI-facing engine state, and optional tablebase integration
- `cli/`: native CLI and UCI entrypoint
- `data/`: bundled runtime assets, tactical suites, opening suites, and example gauntlet configuration
- `gui/`: Tkinter UCI GUI, controller, and protocol client
- `scripts/`: build helpers, smoke tests, profiling tools, match runners, and gauntlet wrappers
- `tests/`: native correctness and search regression tests
- `third_party/fathom/`: vendored Syzygy probing backend
- `training/`: neural-eval data and training pipeline; see [NNUE.md](./NNUE.md)

## Requirements

- Native build: `clang++` with C++20 support
- Scripted smoke and workflow helpers: Python 3
- Optional GUI runtime: `python-chess` via `gui/requirements.txt`
- Optional match tooling: `cutechess-cli`

No environment variables or `.env` file are required for normal development or use.

## Build And Test

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_native.ps1
.\build\deadfish_tests.exe
python .\scripts\uci_smoke.py
python .\scripts\tactical_suite.py
```

The native build script looks for `clang++` on `PATH` first, falls back to the default LLVM install path on Windows, and automatically enables Syzygy probing when `third_party/fathom/` is present.

It can build the generic release target, the local native-tuned target, or both:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_native.ps1 -Target Generic
powershell -ExecutionPolicy Bypass -File .\scripts\build_native.ps1 -Target Native
powershell -ExecutionPolicy Bypass -File .\scripts\build_native.ps1 -Target All
```

The native target emits `build/deadfish_native.exe` and `build/deadfish_tests_native.exe` with CPU-targeted compiler flags for local profiling and strength work.

## GUI

Install the lightweight GUI dependency set:

```powershell
python -m pip install -r .\gui\requirements.txt
```

Launch the desktop GUI:

```powershell
python -m gui
python -m gui --engine path\to\other-uci-engine.exe
.\quickstart.bat
```

The GUI defaults to `build/deadfish_native.exe` when present, falls back to `build/deadfish.exe`, and can browse to any other UCI engine executable. It supports:

- click or drag piece movement with promotion prompts
- live analysis through `go infinite`
- White/Black player slots where each side can be human or any loaded UCI engine
- clock games using `go wtime/btime/winc/binc`, plus fixed movetime and node-limited move modes
- optional background thinking on human turns
- standard PGN/SAN notation in the side panel
- FEN load/copy, board flip, and reset/new-game controls
- grouped core and advanced UCI settings
- engine log output

For a controller/protocol smoke check:

```powershell
python .\scripts\gui_smoke.py
```

For a one-command Windows workflow that clears generated files, rebuilds DeadFish, runs verification, installs the GUI dependency into `.gui_pydeps`, and launches the GUI:

```powershell
.\quickstart.bat
.\quickstart.bat --no-launch
```

## Recommended Workflows

### Edit-build-verify loop

Use this after engine, search, GUI, or protocol changes:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_native.ps1 -Target All
.\build\deadfish_tests.exe
.\build\deadfish_tests_native.exe
python .\scripts\uci_smoke.py
python .\scripts\tactical_suite.py
python .\scripts\gui_smoke.py
```

### Lightweight speed check

Use this after hot-path, search, or compiler-flag changes:

```powershell
python .\scripts\profile_bench.py --repeat 5
python .\scripts\thread_scaling.py --movetime 1000 --threads 1,2,4,8,12,16,20
```

`profile_bench.py` compares `build/deadfish.exe` and `build/deadfish_native.exe` on the fixed bench suite. `thread_scaling.py` drives UCI directly and reports depth, nodes, NPS, and best move across thread counts.

For one-off executable comparisons against another build:

```powershell
python .\scripts\bench_compare.py --engine-b path\to\other\engine.exe
```

### Internal engine match

Use this when comparing two DeadFish builds quickly:

```powershell
python .\scripts\selfplay_gauntlet.py --engine-b path\to\other\deadfish.exe --movetime 75 --threads-a 20 --threads-b 20
```

This is a quick regression check, not a replacement for a serious Elo run.

### External engine ladder

Use cutechess for objective match testing:

1. Edit [`data/external_gauntlet.example.json`](./data/external_gauntlet.example.json) so engine paths match your local installs.
2. Run:

```powershell
python .\scripts\external_gauntlet.py --config .\data\external_gauntlet.example.json
```

The gauntlet runner auto-detects the repo-local `.tmp_cutechess` install when present, writes PGNs/logs/JSON summaries under `.tmp_matches`, and prints an anchored rating estimate when opponents include a `rating` field. For Stockfish limited-strength anchors, start from:

```powershell
python .\scripts\external_gauntlet.py --config .\data\stockfish_limited_gauntlet.example.json
```

For a single direct cutechess match:

```powershell
python .\scripts\cutechess_match.py --engine-b path\to\other\engine.exe --games 20 --tc 8+0.08
```

These scripts require `cutechess-cli` plus the target engine binaries. Local external engines belong under `.tmp_engines` unless they are intentionally tracked.

## UCI Usage

Run `.\build\deadfish.exe` without arguments to enter the UCI loop. The engine also supports `.\build\deadfish.exe uci` explicitly.

Common UCI options:

- `Hash`
- `Threads`
- `Clear Hash`
- `OwnBook`
- `BookPath`
- `SyzygyPath`
- `SyzygyProbeLimit`
- `MoveOverhead`

`Threads` defaults to `1`. When it is raised, DeadFish chooses an internal thread plan based on root move count, clock/node budget, and requested thread count. High-count runs such as `Threads=20` mix full-depth Lazy SMP workers with lagged helper searches instead of blindly duplicating every worker.

## CLI Commands

- `search [--fen FEN] [--depth N] [--movetime MS] [--threads N] [--json]`
- `eval [--fen FEN] [--moves uci,uci,...] [--json]`
- `perft [--fen FEN] --depth N [--divide]`
- `legal [--fen FEN]`
- `status [--fen FEN] [--moves uci,uci,...] [--json]`
- `fen [--fen FEN] [--moves uci,uci,...]`
- `play [--fen FEN] [--depth N] [--movetime MS] [--threads N]`
- `bench [--depth N] [--movetime MS] [--threads N]`

The `status` command is useful for automation and returns JSON with side-to-move, check, mate, stalemate, draw, and legal-move counts when used with `--json`.

The `eval` command returns a side-to-move-relative static evaluation without running search.

## Workflow Scripts

- `python .\scripts\uci_smoke.py`
  Verifies the UCI handshake, runtime options, depth search, movetime search, and `go infinite` / `stop`.
- `python .\scripts\gui_smoke.py`
  Verifies GUI controller flows against both a fake UCI engine and DeadFish, including multi-engine slots, clock games, node-limited moves, PGN notation, dynamic options, analysis, promotions, FEN reset, and invalid-option fallback logging.
- `python .\scripts\bench_compare.py --engine-b path\to\other\engine.exe`
  Runs the fixed native bench suite on two executables and compares time and NPS.
- `python .\scripts\profile_bench.py --repeat 5`
  Compares generic and native-tuned DeadFish builds on the fixed bench suite.
- `python .\scripts\thread_scaling.py --movetime 1000 --threads 1,2,4,8,12,16,20`
  Measures UCI thread scaling for one engine with fixed Hash across selected thread counts.
- `python .\scripts\tactical_suite.py`
  Runs the fixed tactical regression suite in [`data/tactical_suite.txt`](./data/tactical_suite.txt).
- `python .\scripts\selfplay_gauntlet.py --engine-b path\to\other\engine.exe`
  Runs a small opening-suite gauntlet with both color assignments using UCI.
- `python .\scripts\cutechess_match.py --engine-b path\to\other\engine.exe`
  Runs a configurable UCI-vs-UCI match through `cutechess-cli`, with optional openings, SPRT parameters, and ignored match artifacts.
- `python .\scripts\external_gauntlet.py --config .\data\external_gauntlet.example.json`
  Runs DeadFish against a configured engine ladder through `cutechess-cli` and estimates rating from configured anchors.

The cutechess-based scripts require a local `cutechess-cli` install and external engine binaries; they auto-detect `.tmp_cutechess` but are not required for normal builds.

## Validation Coverage

The native test suite covers:

- FEN parse and serialize round trips
- make/unmake and null-move state restoration
- castling, en passant, promotion, mate, stalemate, repetition, fifty-move, and insufficient-material cases
- perft on the starting position, Kiwipete, tactical reference positions, promotion/check cases, and pin/evasion cases
- SEE regression checks for winning, equal, and losing exchanges
- search smoke tests for mate finding, movetime limits, clock-based limits, node limits, and threaded stop behavior
- bundled-book usage and clean fallback when book or Syzygy paths are missing

## Roadmap

Current focus:

- stronger classical search selectivity and move ordering
- faster legal/evasion generation in search hot paths
- stronger Lazy SMP scaling and thread-aware tuning
- larger external gauntlets against fixed-version rating-list engines
