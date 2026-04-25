# DeadFish NNUE Notes

DeadFish includes an experimental `DFNNUE1` neural-evaluation path. Classical evaluation remains the default and recommended strength baseline.

Current trained networks have underperformed the classical evaluator in match play even when parity and loader checks pass, so use this path for experimentation, debugging, and future training work rather than as the default engine configuration.

## Runtime Support

The native engine can load exported `DFNNUE1` networks through `EvalFile` and switch them on or off through `UseNNUE`.

Related UCI options:

- `UseNNUE`
- `EvalFile`

Defaults:

- `UseNNUE` starts as `false`
- `EvalFile` starts empty
- invalid, missing, unreadable, or empty network files fall back cleanly to classical evaluation

Example UCI setup:

```text
setoption name EvalFile value C:\path\to\deadfish.nnue
setoption name UseNNUE value true
isready
```

To force classical mode:

```text
setoption name UseNNUE value false
setoption name EvalFile value
isready
```

The CLI `eval` command is useful for loader and parity debugging:

```powershell
.\build\deadfish.exe eval --fen "startpos" --json --use-nnue true --eval-file .\training\output\deadfish.nnue
```

It returns the side-to-move-relative static evaluation without running search.

## Training Toolkit

The data and training flow lives under [`training/`](./training/README.md). It currently includes:

- `generate_selfplay_pgn.py`
  Generates DeadFish self-play PGNs through `cutechess-cli`.
- `extract_positions.py`
  Samples JSONL training positions from PGN games.
- `annotate_positions.py`
  Annotates JSONL positions with scores from a generic UCI teacher using depth, movetime, or fixed nodes.
- `train_nnue.py`
  Trains a first-pass HalfKP-style checkpoint in PyTorch.
- `export_nnue.py`
  Exports the checkpoint to a custom DeadFish `.nnue` binary.
- `train_selfplay_hybrid.bat`
  Runs a full hybrid-residual self-play batch from build to quick benchmark.

Optional training dependencies are listed in [`training/requirements.txt`](./training/requirements.txt) and include PyTorch, NumPy, and `python-chess`.

## Recommended Workflow

1. Collect imported PGNs.
2. Extract sampled positions to JSONL.
3. Annotate them with Stockfish using a fixed node budget.
4. Train a checkpoint in PyTorch with `--target-mode teacher-cp`.
5. Export a `.nnue` blob.
6. Run parity checks.
7. Run sanity and holdout reports.
8. Benchmark classical vs neural eval with the fixed opening suite.
9. Only after the internal gate is positive, run an external ladder.

See [`training/README.md`](./training/README.md) for exact commands and file flow.

## Parity And Reports

Before trusting a network in match play, run:

```powershell
python .\scripts\nnue_parity.py --checkpoint .\training\checkpoints\deadfish_nnue.pt --eval-file .\training\output\deadfish.nnue
```

This compares Python checkpoint inference, exported `.nnue` inference, and `deadfish eval` on the same FENs.

After parity, run:

```powershell
python .\scripts\nnue_eval_report.py --eval-file .\training\output\deadfish.nnue --teacher-engine .\.tmp_stockfish\stockfish-windows-x86-64-avx2\stockfish\stockfish-windows-x86-64-avx2.exe --teacher-nodes 50000 --input .\training\output\positions_stockfish.jsonl
```

This compares teacher, classical, and neural static eval on a fixed sanity suite plus a sampled teacher-labeled holdout set. It also inspects one-ply child positions from the sanity suite.

For teacher-label holdout checks:

```powershell
python .\scripts\teacher_holdout.py --input .\training\output\positions_annotated.jsonl --eval-file .\training\output\deadfish.nnue --mode both
```

## Match Gate

Use the standardized benchmark gate with `data/nnue_openings.pgn`:

```powershell
python .\scripts\nnue_benchmark.py --eval-file .\training\output\deadfish.nnue --mode quick
python .\scripts\nnue_benchmark.py --eval-file .\training\output\deadfish.nnue --mode strength --require-positive
```

`quick` is a fast smoke gate. `strength` is the first real acceptance gate. Both use the fixed balanced opening suite instead of training self-play games.

## One-Command Hybrid Batch

For the current hybrid branch, the simplest loop is:

```powershell
.\train_selfplay_hybrid.bat
```

Defaults:

- `500` self-play games
- `20`-worker budget
- classical teacher at `50000` nodes
- `8` epochs
- `1+0.01` self-play time control

The batch helper turns that worker budget into `10` concurrent self-play games so it does not oversubscribe the machine with `40` engine processes. It trains a `classical_backbone + nnue_residual` candidate, exports it, runs parity, and then runs the fixed 25-game promotion gate against the current champion.

If an accepted champion already exists at `training/output/deadfish_current.nnue`, later runs automatically:

- use that champion as the self-play baseline
- keep the annotation teacher classical
- warm-start training from `training/checkpoints/deadfish_current.pt`
- promote the new candidate into champion slots only if it beats the current baseline in the 25-game gate

You can pass `none` as the sixth argument to skip benchmarking for a pure training batch:

```powershell
.\train_selfplay_hybrid.bat 500 20 8 8 1+0.01 none
```

## Validation Coverage

The native tests include deterministic loader, fallback, and fixed-score regression checks for the neural path. The UCI smoke test verifies load/unload behavior, fallback, and option reporting.
