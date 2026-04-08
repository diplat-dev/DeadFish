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

Do not overwrite the current champion until the candidate passes the benchmark gates.

## Baseline Choice

Use one of these as the self-play engine:

- classical DeadFish if NNUE is still unstable
- the last accepted hybrid DeadFish build if it has already beaten classical

For the current branch, the safe baseline is still classical mode.

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
python .\training\generate_selfplay_pgn.py --games 5000 --concurrency 20 --opening-file .\data\nnue_openings.pgn --opening-format pgn --opening-order random --opening-plies 8 --output-pgn .\training\runs\RUN_ID\selfplay.pgn
```

If the active baseline should stay classical during self-play, use the GUI/UCI default configuration or generate games from the classical executable/settings you trust.

### 3. Extract positions

```powershell
python .\training\extract_positions.py --input-pgn .\training\runs\RUN_ID\selfplay.pgn --output .\training\runs\RUN_ID\positions.jsonl
```

### 4. Annotate with the baseline teacher

For the current branch, this means your existing depth-limited DeadFish labels:

```powershell
python .\training\annotate_positions.py --input .\training\runs\RUN_ID\positions.jsonl --output .\training\runs\RUN_ID\positions_annotated.jsonl --depth 8 --workers 20
```

If you later move to node-based labeling, prefer `--nodes` over `--depth`.

### 5. Train a candidate hybrid residual net

This branch adds `classical-residual` mode:

```powershell
python .\training\train_nnue.py --input .\training\runs\RUN_ID\positions_annotated.jsonl --target-mode classical-residual --epochs 8 --output-checkpoint .\training\runs\RUN_ID\deadfish_candidate.pt
```

### 6. Export the candidate

```powershell
python .\training\export_nnue.py --checkpoint .\training\runs\RUN_ID\deadfish_candidate.pt --output .\training\runs\RUN_ID\deadfish_candidate.nnue --write-metadata --inspect
```

### 7. Verify parity

```powershell
python .\scripts\nnue_parity.py --checkpoint .\training\runs\RUN_ID\deadfish_candidate.pt --eval-file .\training\runs\RUN_ID\deadfish_candidate.nnue --sample-jsonl .\training\runs\RUN_ID\positions_annotated.jsonl
```

### 8. Run the quick gate

```powershell
python .\scripts\nnue_benchmark.py --cutechess cutechess-cli --engine .\build\deadfish_native.exe --eval-file .\training\runs\RUN_ID\deadfish_candidate.nnue --mode quick
```

### 9. Run the strength gate only if quick passes

```powershell
python .\scripts\nnue_benchmark.py --cutechess cutechess-cli --engine .\build\deadfish_native.exe --eval-file .\training\runs\RUN_ID\deadfish_candidate.nnue --mode strength --require-positive
```

## Promotion Rule

Only promote the candidate if:

- parity passes
- quick gate is not a collapse
- strength gate is positive

If it passes, copy it into the "current champion" slot:

```powershell
Copy-Item .\training\runs\RUN_ID\deadfish_candidate.pt .\training\checkpoints\deadfish_current.pt -Force
Copy-Item .\training\runs\RUN_ID\deadfish_candidate.nnue .\training\output\deadfish_current.nnue -Force
```

Then use that new champion for the next round of self-play.

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

For now, this is the right branch to keep experimenting on, but the first quick gate on the existing 1000-game depth-8 self-play labels still failed badly. So this loop should be treated as a safe experimentation framework, not as proof that the current self-play labels are already sufficient.
