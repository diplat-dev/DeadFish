# DeadFish

DeadFish is a native-first C++ chess engine with a CLI harness, a minimal UCI frontend, and a growing strength-development toolchain around profiling, gauntlets, and NNUE training.

The browser/WebAssembly path has been intentionally removed so the active codebase can focus on native engine strength work.

## Features

- Custom bitboard engine core with legal move generation, compact delta make/unmake, repetition tracking, and full Zobrist hashing
- Iterative deepening with alpha-beta, aspiration windows, PVS, quiescence, null-move pruning, LMR, killer/history heuristics, SEE-based move handling, and a clustered fixed-size TT
- Classical tapered evaluation with material, piece-square terms, mobility, king safety, pawn structure, passed pawns, bishop pair, rook file bonuses, simplification, and tempo
- Engine-side float32 `DFNNUE1` inference with search-local accumulators, plus safe fallback to classical eval when no network is loaded
- Minimal UCI support for `uci`, `isready`, `ucinewgame`, `position`, `go depth`, `go movetime`, `go wtime/btime/winc/binc/movestogo`, `go infinite`, `stop`, `quit`, and engine options
- Windows-first Tkinter UCI GUI with play mode, live analysis, FEN tools, and dynamic UCI option editing
- Bundled Polyglot opening-book support through `data/book.bin`
- Syzygy probing through vendored [Fathom](./third_party/fathom/README.md) with external tablebase files
- Native CLI commands for `play`, `search`, `perft`, `legal`, `status`, `fen`, and `bench`
- Python-based strength tooling for tactical regression, generic-vs-native profiling, cutechess gauntlets, self-play PGN generation, and first-pass NNUE data/training/export flow

## Repository Layout

- `engine/`: core chess engine, search, and evaluation
- `cli/`: native CLI and UCI entrypoint
- `data/`: bundled runtime assets and example gauntlet configuration
- `gui/`: Tkinter UCI GUI, controller, and protocol client
- `tests/`: native correctness and search regression tests
- `third_party/fathom/`: vendored Syzygy probing backend
- `scripts/`: build helpers, UCI smoke tests, bench tools, and gauntlet wrappers
- `training/`: NNUE data extraction, score annotation, training, and export pipeline

## Requirements

- Native build: `clang++` with C++20 support
- Scripted smoke and workflow helpers: Python 3
- Optional GUI runtime: `python-chess` via `gui/requirements.txt`
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

The GUI defaults to `build/deadfish_native.exe` when present, falls back to `build/deadfish.exe`, and can also browse to any other UCI engine executable. It supports:

- click or drag piece movement with promotion prompts
- live analysis via `go infinite`
- human-vs-engine play with a configurable movetime
- FEN load/copy, board flip, and reset/new-game controls
- dynamic UCI settings for `check`, `spin`, `string`, `button`, and `combo` options
- engine log output, including DeadFish NNUE load and fallback status messages

For a controller/protocol smoke check that covers both a fake generic UCI engine and DeadFish itself:

```powershell
python .\scripts\gui_smoke.py
```

For a one-command Windows workflow that clears generated files, rebuilds DeadFish, runs verification, installs the GUI dependency into `.gui_pydeps`, and launches the GUI:

```powershell
.\quickstart.bat
.\quickstart.bat --no-launch
```

On Windows, you can also simply double-click [`quickstart.bat`](./quickstart.bat). It will rebuild and verify the engine, prepare the GUI runtime automatically, and then open the GUI once everything is ready.

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

### 3. NNUE parity check

Use this before benchmarking any exported net:

```powershell
python .\scripts\nnue_parity.py --checkpoint .\training\checkpoints\deadfish_nnue.pt --eval-file .\training\output\deadfish.nnue
```

This compares Python checkpoint inference, exported `.nnue` inference, and `deadfish eval` on the same FENs. It should pass before you trust match results.

### 4. NNUE sanity and holdout report

Use this after parity and before match play:

```powershell
python .\scripts\nnue_eval_report.py --eval-file .\training\output\deadfish.nnue --teacher-engine .\.tmp_stockfish\stockfish-windows-x86-64-avx2\stockfish\stockfish-windows-x86-64-avx2.exe --teacher-nodes 50000 --input .\training\output\positions_stockfish.jsonl
```

This compares teacher, classical, and NNUE static eval on a fixed sanity suite plus a sampled teacher-labeled holdout set. It also inspects one-ply child positions from the sanity suite so root-only parity is not the only signal.

### 5. Standard NNUE gate

Use this for classical-vs-NNUE checks with a fixed balanced opening suite:

```powershell
python .\scripts\nnue_benchmark.py --eval-file .\training\output\deadfish.nnue --mode quick
python .\scripts\nnue_benchmark.py --eval-file .\training\output\deadfish.nnue --mode strength --require-positive
```

`quick` is a fast smoke gate. `strength` is the first real acceptance gate. Both use `data/nnue_openings.pgn` instead of training self-play games.

### 6. Small internal engine match

Use this when comparing two DeadFish builds:

```powershell
python .\scripts\selfplay_gauntlet.py --engine-b path\to\other\deadfish.exe --movetime 75
```

This is a quick native regression check, not a replacement for a serious Elo run.

### 7. External engine ladder

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
- `UseNNUE`
- `EvalFile`
- `OwnBook`
- `BookPath`
- `SyzygyPath`
- `SyzygyProbeLimit`
- `MoveOverhead`

`UseNNUE` defaults to `false`, so DeadFish starts in classical mode unless you explicitly enable NNUE and load a valid network through `EvalFile`. If `EvalFile` is empty, missing, unreadable, or invalid, DeadFish stays fully usable and falls back to the classical evaluator.

Current note:
the NNUE pipeline is still experimental, and current trained nets have been underperforming the classical evaluator in match play even when parity and loader checks pass. For normal use, classical mode is the built-in default.

Example:

```text
setoption name EvalFile value C:\path\to\deadfish.nnue
setoption name UseNNUE value true
isready
```

To force classical mode and keep NNUE from interfering:

```text
setoption name UseNNUE value false
setoption name EvalFile value
isready
```

In a UCI GUI, the default setup is already the safe setup:

- `UseNNUE` starts as `false`
- leave `EvalFile` empty unless you explicitly want NNUE

## CLI Commands

- `search [--fen FEN] [--depth N] [--movetime MS] [--json]`
- `eval [--fen FEN] [--moves uci,uci,...] [--json] [--use-nnue BOOL] [--eval-file PATH]`
- `perft [--fen FEN] --depth N [--divide]`
- `legal [--fen FEN]`
- `status [--fen FEN] [--moves uci,uci,...] [--json]`
- `fen [--fen FEN] [--moves uci,uci,...]`
- `play [--fen FEN] [--depth N] [--movetime MS]`
- `bench [--depth N] [--movetime MS]`

The `status` command is useful for automation and returns JSON with side-to-move, check, mate, stalemate, draw, and legal-move counts when used with `--json`.

The `eval` command is useful for NNUE debugging and parity checks. It returns the side-to-move-relative static evaluation without running search.

## Workflow Scripts

- `python .\scripts\uci_smoke.py`
  Verifies the UCI handshake, runtime options, NNUE load/unload fallback, depth search, movetime search, and `go infinite` / `stop`.
- `python .\scripts\gui_smoke.py`
  Verifies GUI controller flows against both a fake UCI engine and DeadFish, including dynamic options, analysis, engine replies, promotions, FEN reset, and invalid-option fallback logging.
- `python .\scripts\bench_compare.py --engine-b path\to\other\engine.exe`
  Runs the fixed native bench suite on two executables and compares time and NPS.
- `python .\scripts\profile_bench.py --repeat 5`
  Compares the generic and native-tuned DeadFish builds on the fixed bench suite and reports elapsed-time and NPS speedups.
- `python .\scripts\nnue_parity.py --checkpoint .\training\checkpoints\deadfish_nnue.pt --eval-file .\training\output\deadfish.nnue`
  Verifies that the Python checkpoint, exported `.nnue`, and engine runtime agree within a small centipawn tolerance on a fixed FEN suite plus sampled JSONL positions.
- `python .\scripts\nnue_eval_report.py --eval-file .\training\output\deadfish.nnue --teacher-engine path\to\stockfish.exe --input .\training\output\positions_stockfish.jsonl`
  Compares teacher, classical, and NNUE eval on the fixed sanity suite and a sampled holdout, including one-ply child positions and score-distribution summaries.
- `python .\scripts\nnue_benchmark.py --eval-file .\training\output\deadfish.nnue --mode quick`
  Runs the standardized classical-vs-NNUE benchmark gate with `data/nnue_openings.pgn`.
- `python .\scripts\teacher_holdout.py --input .\training\output\positions_annotated.jsonl --eval-file .\training\output\deadfish.nnue --mode both`
  Compares DeadFish classical and NNUE static eval against teacher `score_cp` labels on a sampled holdout set before match play.
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
  Annotates JSONL positions with scores from a generic UCI teacher using depth, movetime, or fixed nodes.
- `train_nnue.py`
  Trains a first-pass HalfKP-style NNUE checkpoint in PyTorch.
- `export_nnue.py`
  Exports the checkpoint to a custom DeadFish `.nnue` binary.

The native engine can now load exported `DFNNUE1` networks directly through `EvalFile` and switch them on or off through `UseNNUE`.

The recommended NNUE workflow is:

1. Collect imported PGNs.
2. Extract sampled positions to JSONL.
3. Annotate them with Stockfish using a fixed node budget.
4. Train a checkpoint in PyTorch with `--target-mode teacher-cp`.
5. Export a `.nnue` blob.
6. Run the parity check.
7. Run the sanity and holdout report.
8. Benchmark classical vs NNUE with the fixed opening suite.
9. Only after the internal gate is positive, run the external ladder.

See [`training/README.md`](./training/README.md) for the exact commands and file flow.

## Validation

The native test suite covers:

- FEN parse and serialize round trips
- make/unmake and null-move state restoration
- castling, en passant, promotion, mate, stalemate, repetition, fifty-move, and insufficient-material cases
- perft on the starting position, Kiwipete, and additional tactical reference positions
- SEE regression checks for winning, equal, and losing exchanges
- search smoke tests for mate finding, movetime limits, and clock-based limits
- deterministic NNUE loader, fallback, and fixed-score regression checks
- bundled-book usage and clean fallback when book or Syzygy paths are missing

## Current Roadmap Status

The current tree includes:

- the first native speed refactor: compact undo, staged move picking, and clustered TT replacement
- native generic-vs-tuned profiling and repeatable regression tooling
- cutechess-based gauntlet scaffolding and a small example engine ladder
- the first NNUE data/training/export pipeline under `training/`
- engine-side `DFNNUE1` loading, accumulator-backed search eval, and `UseNNUE` / `EvalFile` runtime control

Still planned for the engine itself:

- heavier real-world NNUE training runs and tuning
- multi-threaded search with `Threads`
