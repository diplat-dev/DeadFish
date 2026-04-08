# DeadFish NNUE Training

This folder contains the native training and export pipeline for DeadFish's NNUE evaluation path.

The native engine can now load exported `DFNNUE1` networks through the UCI `EvalFile` option. The goal of this package is to make data generation, position extraction, score annotation, training, and export repeatable so you can generate better networks as the engine improves.

## Requirements

Install the training dependencies in a dedicated environment:

```powershell
python -m pip install -r .\training\requirements.txt
```

If you want a minimal dependency check before running a long job:

```powershell
python .\training\smoke_test.py
```

## Workflow Overview

1. Generate self-play PGNs with `generate_selfplay_pgn.py`, or use external PGNs from cutechess.
2. Extract sampled training positions with `extract_positions.py`.
3. Optionally annotate those positions with DeadFish search scores using `annotate_positions.py`.
4. Train a HalfKP-style NNUE with `train_nnue.py`.
5. Export a `.nnue` blob with `export_nnue.py`.
6. Load that `.nnue` file in the engine with `EvalFile` when you want to test it.

## End-To-End Workflow

### 1. Generate self-play PGNs

```powershell
python .\training\generate_selfplay_pgn.py --games 5000 --concurrency 0
```

Default output:

- `training/output/selfplay.pgn`

By default this overwrites `training/output/selfplay.pgn` so each run starts fresh. Use `--append` only when you explicitly want to add to an existing PGN, and `--recover` only when you intentionally want cutechess resume behavior.

Use `--opening-file`, `--tc`, `--games`, and `--concurrency` to control the run. `--concurrency 0` means auto based on CPU count. This step requires `cutechess-cli`.

### 2. Extract sampled positions

```powershell
python .\training\extract_positions.py --input-pgn .\training\output\selfplay.pgn
```

Default output:

- `training/output/positions.jsonl`

Useful knobs:

- `--skip-opening-plies`
- `--sample-every`
- `--max-games`
- `--max-positions`

### 3. Annotate positions with DeadFish scores

If you want search-score targets instead of outcome-only targets:

```powershell
python .\training\annotate_positions.py --input .\training\output\positions.jsonl --depth 8 --workers 0
```

Default output:

- `training/output/positions_annotated.jsonl`

This step drives the current native engine through UCI, disables the opening book, and records:

- `score_kind`
- `score_value`
- `score_cp`
- `annotated_depth`
- `best_move`
- `pv`

Mate scores are preserved as `score_kind = "mate"` and `score_value = ...`. They are excluded from score-supervised NNUE training by default instead of being flattened into fake centipawn labels.

If you want a lighter run on battery or limited time, use `--movetime` or `--limit`. `--workers 0` means auto based on CPU count.

### 4. Train a checkpoint

```powershell
python .\training\train_nnue.py --input .\training\output\positions_annotated.jsonl
```

Default output:

- `training/checkpoints/deadfish_nnue.pt`

Useful knobs:

- `--max-positions`
- `--epochs`
- `--batch-size`
- `--learning-rate`
- `--device cpu|cuda|auto`
- `--accumulator-size`
- `--hidden-size`

If you skip the annotation step, `train_nnue.py` can still train from raw extracted positions by falling back to `outcome`.

The trainer now:

- uses `clip_cp = 1200` by default
- uses `output_scale = clip_cp` by default unless you override it
- prints JSON summary data including train/validation loss and load statistics
- skips mate-labeled search targets by default

### 5. Export a `.nnue` file

```powershell
python .\training\export_nnue.py --checkpoint .\training\checkpoints\deadfish_nnue.pt --write-metadata --inspect
```

Default output:

- `training/output/deadfish.nnue`
- `training/output/deadfish.nnue.json` when `--write-metadata` is used

`--inspect` reads the export back immediately and validates its tensor shapes.

You can then test the exported network in DeadFish:

```text
setoption name EvalFile value C:\path\to\training\output\deadfish.nnue
setoption name UseNNUE value true
isready
```

### 6. Run the parity check

Before benchmarking, make sure Python inference, the exported `.nnue`, and the engine runtime agree:

```powershell
python .\scripts\nnue_parity.py --checkpoint .\training\checkpoints\deadfish_nnue.pt --eval-file .\training\output\deadfish.nnue
```

### 7. Run the fixed benchmark gate

Benchmark NNUE against the classical evaluator with the fixed balanced opening suite:

```powershell
python .\scripts\nnue_benchmark.py --eval-file .\training\output\deadfish.nnue --mode quick
python .\scripts\nnue_benchmark.py --eval-file .\training\output\deadfish.nnue --mode strength --require-positive
```

### 8. Compare against teacher labels before match play

If your JSONL was annotated by a stronger teacher such as Stockfish, compare DeadFish eval directly against those labels:

```powershell
python .\scripts\teacher_holdout.py --input .\training\output\positions_annotated.jsonl --eval-file .\training\output\deadfish.nnue --mode both
```

This gives you a quick sanity check for whether NNUE is actually moving closer to the teacher than the classical evaluator is.

## Imported PGN + Stockfish Teacher

If you already have PGNs and want a stronger initial teacher than DeadFish:

```powershell
python .\training\extract_positions.py --input-pgn C:\path\to\imported_games.pgn
python .\training\annotate_positions.py --engine C:\engines\stockfish\stockfish.exe --input .\training\output\positions.jsonl --output .\training\output\positions_stockfish.jsonl --depth 10 --workers 0 --option Threads=1
python .\training\train_nnue.py --input .\training\output\positions_stockfish.jsonl --epochs 8
python .\training\export_nnue.py --checkpoint .\training\checkpoints\deadfish_nnue.pt --write-metadata --inspect
python .\scripts\nnue_parity.py --checkpoint .\training\checkpoints\deadfish_nnue.pt --eval-file .\training\output\deadfish.nnue
python .\scripts\teacher_holdout.py --input .\training\output\positions_stockfish.jsonl --eval-file .\training\output\deadfish.nnue --mode both
python .\scripts\nnue_benchmark.py --eval-file .\training\output\deadfish.nnue --mode quick
```

`annotate_positions.py` now detects advertised UCI options and only sends supported defaults. Extra `--option NAME=VALUE` settings are applied when the teacher engine supports them.

## Short Command Chain

For the current recovery recipe:

```powershell
python .\training\generate_selfplay_pgn.py --games 5000 --concurrency 0
python .\training\extract_positions.py --input-pgn .\training\output\selfplay.pgn
python .\training\annotate_positions.py --input .\training\output\positions.jsonl --depth 8 --workers 0
python .\training\train_nnue.py --input .\training\output\positions_annotated.jsonl
python .\training\export_nnue.py --checkpoint .\training\checkpoints\deadfish_nnue.pt --write-metadata --inspect
python .\scripts\nnue_parity.py --checkpoint .\training\checkpoints\deadfish_nnue.pt --eval-file .\training\output\deadfish.nnue
python .\scripts\nnue_benchmark.py --eval-file .\training\output\deadfish.nnue --mode quick
```

## Data Format

The extracted and annotated datasets use JSON Lines. Each record includes:

- `fen`: position FEN
- `outcome`: game result from White's perspective in `[-1, 0, 1]`
- `ply`: ply number in the source game
- `score_kind`: optional annotated score type (`cp` or `mate`)
- `score_value`: raw annotated score value
- `score_cp`: optional annotated centipawn score, present only for `score_kind = "cp"`
- `best_move`: optional annotated best move
- `pv`: optional principal variation

If `score_cp` is present it is used as the primary training target. Mate-labeled search scores are skipped by default. If no score is present, the trainer falls back to the game outcome.

## Notes

- The engine consumes the exported float32 `DFNNUE1` weights directly through `EvalFile`, with safe fallback to classical eval if the file is invalid or not set.
- `scripts/nnue_parity.py` is the first thing to run if a network behaves strangely. It tells you whether the disagreement is in training, export, or runtime inference.
- Long self-play generation, annotation runs, and real training loops are intentionally separated so you can run heavy steps later when plugged in or on a larger machine.
- Generated datasets, checkpoints, and exports are ignored by git through the repo-level `.gitignore`.

## Export Format

`export_nnue.py` writes a custom DeadFish binary format with:

- a fixed header and version tag
- network dimensions
- `EmbeddingBag` feature weights
- accumulator bias
- hidden-layer weights and bias
- output-layer weights and bias

The exporter also supports a JSON metadata sidecar for inspection.
