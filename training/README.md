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
python .\training\generate_selfplay_pgn.py --games 50
```

Default output:

- `training/output/selfplay.pgn`

Use `--opening-file`, `--tc`, `--games`, and `--concurrency` to control the run. This step requires `cutechess-cli`.

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
python .\training\annotate_positions.py --input .\training\output\positions.jsonl --depth 6
```

Default output:

- `training/output/positions_annotated.jsonl`

This step drives the current native engine through UCI, disables the opening book, and records:

- `score_cp`
- `annotated_depth`
- `best_move`
- `pv`

If you want a lighter run on battery or limited time, use `--movetime` or `--limit`.

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

## Short Command Chain

For a straightforward full pass:

```powershell
python .\training\generate_selfplay_pgn.py --games 50
python .\training\extract_positions.py --input-pgn .\training\output\selfplay.pgn
python .\training\annotate_positions.py --input .\training\output\positions.jsonl
python .\training\train_nnue.py --input .\training\output\positions_annotated.jsonl
python .\training\export_nnue.py --checkpoint .\training\checkpoints\deadfish_nnue.pt
```

## Data Format

The extracted and annotated datasets use JSON Lines. Each record includes:

- `fen`: position FEN
- `outcome`: game result from White's perspective in `[-1, 0, 1]`
- `ply`: ply number in the source game
- `score_cp`: optional annotated centipawn score
- `best_move`: optional annotated best move
- `pv`: optional principal variation

If `score_cp` is present it is used as the primary training target. Otherwise the trainer falls back to the game outcome.

## Notes

- The engine consumes the exported float32 `DFNNUE1` weights directly through `EvalFile`, with safe fallback to classical eval if the file is invalid or not set.
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
