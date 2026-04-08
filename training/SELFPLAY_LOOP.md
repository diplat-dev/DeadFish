# DeadFish Continual Self-Play Loop

This is a practical setup for improving DeadFish over time without manually rethinking the whole process every run.

The idea is:

1. keep one accepted baseline engine/net
2. generate fresh self-play from that baseline
3. label those positions with the same engine's depth or node-limited search
4. train a new candidate net
5. benchmark the candidate against the current baseline
6. only promote the candidate if it passes the gates

This keeps the project moving forward in small safe steps instead of replacing the active net with every experiment.

## One-Command Batch Helper

From the repo root, you can run the whole candidate cycle with:

```powershell
.\train_selfplay_hybrid.bat
```

Default behavior:

- `500` self-play games
- `20` total engine workers
- self-play concurrency derived from that budget as `10` games at a time
- `20` annotation workers
- classical teacher at `50000` nodes
- `8` training epochs
- self-play time control `1+0.01`
- use `training/output/deadfish_current.nnue` as the self-play baseline if it exists
- warm-start from `training/checkpoints/deadfish_current.pt` if it exists
- promote the candidate into those champion slots only if it beats the current champion NNUE in the fixed 25-game gate
- run an informational 25-game classical audit every 10 accepted promotions

Override any of those with:

```powershell
.\train_selfplay_hybrid.bat [games] [workers] [teacher_nodes] [epochs] [selfplay_tc] [gate_mode]
```

Example:

```powershell
.\train_selfplay_hybrid.bat 1000 20 75000 8 2+0.02
```

Skip the benchmark gate entirely for a pure training batch:

```powershell
.\train_selfplay_hybrid.bat 500 20 50000 8 1+0.01 none
```

## Recommended Layout

Keep these as the stable "current champion" files:

- `training/checkpoints/deadfish_current.pt`
- `training/output/deadfish_current.nnue`

For each run, write candidate artifacts under a dated run folder:

- `training/runs/YYYYMMDD-HHMM/selfplay.pgn`
- `training/runs/YYYYMMDD-HHMM/positions.jsonl`
- `training/runs/YYYYMMDD-HHMM/positions_annotated.jsonl`
- `training/runs/YYYYMMDD-HHMM/deadfish_candidate.pt`
- `training/runs/YYYYMMDD-HHMM/deadfish_candidate.nnue`

Do not overwrite the current champion until the candidate passes the benchmark gates. The batch helper now enforces that automatically when benchmarking is enabled.

## Baseline Choice

Use one of these as the self-play engine:

- classical DeadFish if there is no accepted champion net yet
- the last accepted hybrid DeadFish build if `training/output/deadfish_current.nnue` exists

For the current branch, the safe bootstrap baseline is still classical mode. Once a candidate passes the gate and is promoted, later runs automatically use that promoted net as the self-play baseline and warm-start point.

## One Full Candidate Cycle

Run from the repo root:

```powershell
cd C:\Users\Taylor\Documents\ProgrammingProjects\DeadFish
```

### 1. Build and verify the engine

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_native.ps1 -Target All
.\build\deadfish_tests.exe
.\build\deadfish_tests_native.exe
python .\scripts\uci_smoke.py
python .\scripts\tactical_suite.py
```

### 2. Generate fresh self-play from the current baseline

Use openings so the dataset is less narrow:

```powershell
python .\training\generate_selfplay_pgn.py --games 5000 --concurrency 10 --opening-file .\data\nnue_openings.pgn --opening-format pgn --opening-order random --opening-plies 8 --output-pgn .\training\runs\RUN_ID\selfplay.pgn --name-a DeadFish-Classical --name-b DeadFish-Champion --option-a UseNNUE=false --option-b UseNNUE=true --option-b EvalFile=.\training\output\deadfish_current.nnue
```

The batch helper rotates evenly across three pairing modes once a champion exists:

- classical vs classical
- classical vs champion
- champion vs champion

### 3. Extract positions

```powershell
python .\training\extract_positions.py --input-pgn .\training\runs\RUN_ID\selfplay.pgn --output .\training\runs\RUN_ID\positions.jsonl
```

### 4. Annotate with the classical teacher

The teacher always stays classical and uses a fixed node budget:

```powershell
python .\training\annotate_positions.py --engine .\build\deadfish_native.exe --input .\training\runs\RUN_ID\positions.jsonl --output .\training\runs\RUN_ID\positions_annotated.jsonl --nodes 50000 --workers 20 --option UseNNUE=false
```

### 5. Train a candidate hybrid residual net

This branch adds `classical-residual` mode:

```powershell
python .\training\train_nnue.py --input .\training\runs\RUN_ID\positions_annotated.jsonl --target-mode classical-residual --epochs 8 --output-checkpoint .\training\runs\RUN_ID\deadfish_candidate.pt
```

If the current champion checkpoint exists, warm-start from it:

```powershell
python .\training\train_nnue.py --input .\training\runs\RUN_ID\positions_annotated.jsonl --target-mode classical-residual --epochs 8 --initialize-from .\training\checkpoints\deadfish_current.pt --output-checkpoint .\training\runs\RUN_ID\deadfish_candidate.pt
```

### 6. Export the candidate

```powershell
python .\training\export_nnue.py --checkpoint .\training\runs\RUN_ID\deadfish_candidate.pt --output .\training\runs\RUN_ID\deadfish_candidate.nnue --write-metadata --inspect
```

### 7. Verify parity

```powershell
python .\scripts\nnue_parity.py --checkpoint .\training\runs\RUN_ID\deadfish_candidate.pt --eval-file .\training\runs\RUN_ID\deadfish_candidate.nnue --sample-jsonl .\training\runs\RUN_ID\positions_annotated.jsonl
```

### 8. Run the promotion gate

```powershell
python .\scripts\nnue_benchmark.py --cutechess cutechess-cli --engine .\build\deadfish_native.exe --eval-file .\training\runs\RUN_ID\deadfish_candidate.nnue --baseline-eval-file .\training\output\deadfish_current.nnue --mode quick --games 25 --tc 1+0.01 --concurrency 2 --require-positive
```

### 9. Periodic classical audit

```powershell
python .\scripts\nnue_benchmark.py --cutechess cutechess-cli --engine .\build\deadfish_native.exe --eval-file .\training\output\deadfish_current.nnue --mode quick --games 25 --tc 1+0.01 --concurrency 2
```

Run that audit every 10 accepted promotions. It is informational only and does not veto a promotion.

## Promotion Rule

Only promote the candidate if:

- parity passes
- the 25-game candidate-vs-champion gate is positive

If it passes, copy it into the "current champion" slot:

```powershell
Copy-Item .\training\runs\RUN_ID\deadfish_candidate.pt .\training\checkpoints\deadfish_current.pt -Force
Copy-Item .\training\runs\RUN_ID\deadfish_candidate.nnue .\training\output\deadfish_current.nnue -Force
```

Then use that new champion for the next round of self-play and warm-started training.

## Good Operating Rules

- Never replace the champion because a net "looks promising."
- Always keep one last-known-good checkpoint and export.
- Keep self-play generation, annotation, training, and benchmarking as separate steps.
- Save each run under its own folder so you can compare runs later.
- Treat failed candidates as data points, not as the new baseline.

## Current Branch Note

The current hybrid branch uses:

`final_eval = classical_backbone + nnue_residual`

That means the net is learning only the positional remainder, not the full score.

For now, this is the right branch to keep experimenting on, but it should still be treated as a bounded distillation loop. Because the teacher remains classical forever, the NNUE ladder can improve incrementally while still eventually plateauing near the strength of that teacher and architecture.
