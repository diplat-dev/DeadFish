from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRAINING_DIR = ROOT / "training"
if str(TRAINING_DIR) not in sys.path:
    sys.path.insert(0, str(TRAINING_DIR))

try:
    import numpy as np
    import torch
except ImportError as exc:  # pragma: no cover - runtime dependency guard
    raise SystemExit(
        "NumPy and PyTorch are required for NNUE parity checks. "
        "Install training/requirements.txt first."
    ) from exc

from _uci import evaluate as engine_evaluate  # noqa: E402
from _uci import preferred_engine_path  # noqa: E402
from deadfish_nnue import encode_fen, evaluate_backbone_fen, read_export  # noqa: E402
from deadfish_nnue.export import checkpoint_to_model  # noqa: E402
from export_nnue import load_checkpoint  # noqa: E402


def load_fens(path: Path) -> list[str]:
    fens: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fens.append(line)
    return fens


def sample_jsonl_fens(path: Path, sample_count: int, seed: int) -> list[str]:
    if sample_count <= 0 or not path.exists():
        return []
    fens: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            record = json.loads(line)
            fen = record.get("fen")
            if isinstance(fen, str) and fen:
                fens.append(fen)
    if not fens:
        return []
    rng = random.Random(seed)
    rng.shuffle(fens)
    seen: set[str] = set()
    sampled: list[str] = []
    for fen in fens:
        if fen in seen:
            continue
        seen.add(fen)
        sampled.append(fen)
        if len(sampled) >= sample_count:
            break
    return sampled


def checkpoint_score(model, fen: str) -> int:
    encoded = encode_fen(fen)
    white_indices = torch.tensor(encoded.white_indices, dtype=torch.long)
    white_offsets = torch.tensor([0], dtype=torch.long)
    black_indices = torch.tensor(encoded.black_indices, dtype=torch.long)
    black_offsets = torch.tensor([0], dtype=torch.long)
    stm_is_white = torch.tensor([encoded.stm_is_white], dtype=torch.bool)
    with torch.no_grad():
        cp = model.predict_centipawns(
            white_indices,
            white_offsets,
            black_indices,
            black_offsets,
            stm_is_white,
        ).item()
    return int(round(float(cp)))


def export_score(metadata, tensors: dict[str, np.ndarray], fen: str) -> int:
    encoded = encode_fen(fen)
    accumulator_size = metadata.accumulator_size
    feature_weights = tensors["feature_weights"]
    acc_bias = tensors["acc_bias"]

    white_acc = acc_bias.astype(np.float32, copy=True)
    black_acc = acc_bias.astype(np.float32, copy=True)
    if encoded.white_indices:
        white_acc += feature_weights[np.asarray(encoded.white_indices, dtype=np.int64)].sum(axis=0)
    if encoded.black_indices:
        black_acc += feature_weights[np.asarray(encoded.black_indices, dtype=np.int64)].sum(axis=0)

    white_activated = np.clip(white_acc, 0.0, 1.0)
    black_activated = np.clip(black_acc, 0.0, 1.0)
    if encoded.stm_is_white:
        stacked = np.concatenate([white_activated, black_activated], axis=0)
    else:
        stacked = np.concatenate([black_activated, white_activated], axis=0)

    hidden = np.clip(tensors["hidden_weight"] @ stacked + tensors["hidden_bias"], 0.0, 1.0)
    output = float(tensors["output_weight"][0] @ hidden + tensors["output_bias"][0])
    return int(round(output * float(metadata.output_scale)))


def default_sample_jsonl() -> Path:
    return ROOT / "training" / "output" / "positions_annotated.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare checkpoint, exported NNUE, and engine eval on the same FENs.")
    parser.add_argument("--engine", type=Path, default=preferred_engine_path(), help="Path to the DeadFish executable.")
    parser.add_argument("--checkpoint", type=Path, required=True, help="PyTorch checkpoint to validate.")
    parser.add_argument("--eval-file", type=Path, required=True, help="Exported .nnue file to validate.")
    parser.add_argument(
        "--fen-file",
        type=Path,
        default=ROOT / "data" / "nnue_parity_fens.txt",
        help="Fixed FEN suite for parity checks.",
    )
    parser.add_argument(
        "--sample-jsonl",
        type=Path,
        default=default_sample_jsonl(),
        help="Optional JSONL source for random sampled FENs. Missing files are skipped cleanly.",
    )
    parser.add_argument("--sample-count", type=int, default=16, help="Random sample size from --sample-jsonl.")
    parser.add_argument("--seed", type=int, default=1337, help="Sampling seed for JSONL positions.")
    parser.add_argument("--tolerance", type=int, default=1, help="Maximum allowed centipawn disagreement.")
    args = parser.parse_args()

    engine_path = args.engine.resolve()
    checkpoint_path = args.checkpoint.resolve()
    eval_file = args.eval_file.resolve()
    fen_file = args.fen_file.resolve()
    sample_jsonl = args.sample_jsonl.resolve()

    for path, label in (
        (engine_path, "engine"),
        (checkpoint_path, "checkpoint"),
        (eval_file, "eval file"),
        (fen_file, "fen file"),
    ):
        if not path.exists():
            raise FileNotFoundError(f"{label.capitalize()} not found: {path}")

    checkpoint = load_checkpoint(checkpoint_path)
    model, _ = checkpoint_to_model(checkpoint)
    model.eval()
    metadata, tensors = read_export(eval_file)

    fens = load_fens(fen_file)
    sampled_fens = sample_jsonl_fens(sample_jsonl, args.sample_count, args.seed)
    if sampled_fens:
        fens.extend(sampled_fens)

    deduped_fens: list[str] = []
    seen: set[str] = set()
    for fen in fens:
        if fen in seen:
            continue
        seen.add(fen)
        deduped_fens.append(fen)

    if not deduped_fens:
        raise ValueError("No FENs were available for parity testing.")

    failures = 0
    max_diff = 0
    for index, fen in enumerate(deduped_fens, start=1):
        backbone_cp = evaluate_backbone_fen(fen)
        checkpoint_cp = checkpoint_score(model, fen) + backbone_cp
        export_cp = export_score(metadata, tensors, fen) + backbone_cp
        engine_report = engine_evaluate(engine_path, fen, use_nnue=True, eval_file=eval_file)
        engine_cp = int(engine_report["score"])

        diffs = {
            "checkpoint_export": abs(checkpoint_cp - export_cp),
            "checkpoint_engine": abs(checkpoint_cp - engine_cp),
            "export_engine": abs(export_cp - engine_cp),
        }
        worst = max(diffs.values())
        max_diff = max(max_diff, worst)
        status = "OK" if worst <= args.tolerance else "FAIL"
        if status == "FAIL":
            failures += 1

        print(
            f"{index:02d} {status} "
            f"checkpoint={checkpoint_cp:>6} export={export_cp:>6} engine={engine_cp:>6} backbone={backbone_cp:>6} "
            f"maxDiff={worst:>2} fen={fen}"
        )

    print(
        json.dumps(
            {
                "checked_fens": len(deduped_fens),
                "sampled_fens": len(sampled_fens),
                "tolerance": args.tolerance,
                "max_diff": max_diff,
                "failures": failures,
                "sample_jsonl_used": str(sample_jsonl) if sample_jsonl.exists() else "",
            },
            indent=2,
        )
    )
    if failures:
        raise SystemExit(f"NNUE parity failed on {failures} FEN(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
