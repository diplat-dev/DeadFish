from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path

from _uci import evaluate as engine_evaluate
from _uci import preferred_engine_path


@dataclass(frozen=True, slots=True)
class HoldoutRecord:
    fen: str
    teacher_cp: int


@dataclass(slots=True)
class Metrics:
    count: int = 0
    mae_sum: float = 0.0
    mse_sum: float = 0.0
    signed_error_sum: float = 0.0
    sign_count: int = 0
    sign_match_count: int = 0

    def add(self, teacher_cp: int, predicted_cp: int, sign_threshold: int) -> None:
        error = predicted_cp - teacher_cp
        self.count += 1
        self.mae_sum += abs(error)
        self.mse_sum += error * error
        self.signed_error_sum += error
        if abs(teacher_cp) >= sign_threshold:
            self.sign_count += 1
            if (teacher_cp > 0 and predicted_cp > 0) or (teacher_cp < 0 and predicted_cp < 0):
                self.sign_match_count += 1

    def summary(self) -> dict[str, float]:
        count = max(1, self.count)
        return {
            "count": self.count,
            "mae": self.mae_sum / count,
            "rmse": math.sqrt(self.mse_sum / count),
            "mean_signed_error": self.signed_error_sum / count,
            "sign_agreement": (self.sign_match_count / self.sign_count) if self.sign_count else 0.0,
            "sign_positions": self.sign_count,
        }


def reservoir_sample_cp_records(path: Path, sample_count: int, seed: int) -> list[HoldoutRecord]:
    rng = random.Random(seed)
    reservoir: list[HoldoutRecord] = []
    seen = 0
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("score_kind") == "mate":
                continue
            score_cp = record.get("score_cp")
            fen = record.get("fen")
            if not isinstance(fen, str) or not fen or score_cp is None:
                continue
            item = HoldoutRecord(fen=fen, teacher_cp=int(score_cp))
            seen += 1
            if sample_count <= 0:
                reservoir.append(item)
                continue
            if len(reservoir) < sample_count:
                reservoir.append(item)
                continue
            replacement_index = rng.randrange(seen)
            if replacement_index < sample_count:
                reservoir[replacement_index] = item
    if not reservoir:
        raise ValueError(f"No centipawn-labeled records found in {path}.")
    return reservoir


def evaluate_mode(
    engine_path: Path,
    record: HoldoutRecord,
    mode: str,
    eval_file: Path | None,
) -> int:
    if mode == "classical":
        report = engine_evaluate(engine_path, record.fen, use_nnue=False)
        return int(report["score"])
    if eval_file is None:
        raise ValueError("NNUE evaluation requires --eval-file.")
    report = engine_evaluate(engine_path, record.fen, use_nnue=True, eval_file=eval_file)
    return int(report["score"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare DeadFish eval against teacher centipawn labels on a holdout sample.")
    parser.add_argument("--engine", type=Path, default=preferred_engine_path(), help="Path to the DeadFish executable.")
    parser.add_argument("--input", type=Path, required=True, help="Annotated JSONL file with teacher score_cp labels.")
    parser.add_argument("--eval-file", type=Path, help="Exported DeadFish NNUE file for NNUE comparisons.")
    parser.add_argument(
        "--mode",
        choices=("classical", "nnue", "both"),
        default="both",
        help="Which DeadFish eval path to compare against the teacher labels.",
    )
    parser.add_argument("--sample-count", type=int, default=200, help="Reservoir sample size. Use 0 to use all cp records.")
    parser.add_argument("--seed", type=int, default=1337, help="Sampling seed.")
    parser.add_argument("--sign-threshold", type=int, default=50, help="Only positions with |teacher_cp| >= threshold count for sign agreement.")
    args = parser.parse_args()

    engine_path = args.engine.resolve()
    input_path = args.input.resolve()
    eval_file = args.eval_file.resolve() if args.eval_file else None

    if not engine_path.exists():
        raise FileNotFoundError(f"Engine executable not found: {engine_path}")
    if not input_path.exists():
        raise FileNotFoundError(f"Input JSONL not found: {input_path}")
    if args.mode in ("nnue", "both") and eval_file is None:
        raise ValueError("--eval-file is required for nnue or both mode.")
    if eval_file is not None and not eval_file.exists():
        raise FileNotFoundError(f"Eval file not found: {eval_file}")

    records = reservoir_sample_cp_records(input_path, args.sample_count, args.seed)
    metrics_by_mode: dict[str, Metrics] = {}
    active_modes = ("classical", "nnue") if args.mode == "both" else (args.mode,)

    for mode in active_modes:
        metrics = Metrics()
        for record in records:
            predicted_cp = evaluate_mode(engine_path, record, mode, eval_file)
            metrics.add(record.teacher_cp, predicted_cp, args.sign_threshold)
        metrics_by_mode[mode] = metrics

    print(
        json.dumps(
            {
                "input": str(input_path),
                "sample_count": len(records),
                "sign_threshold": args.sign_threshold,
                "results": {mode: metrics.summary() for mode, metrics in metrics_by_mode.items()},
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
