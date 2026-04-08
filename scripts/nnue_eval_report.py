from __future__ import annotations

import argparse
import json
import math
import random
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRAINING_DIR = ROOT / "training"
if str(TRAINING_DIR) not in sys.path:
    sys.path.insert(0, str(TRAINING_DIR))

from _uci import UciEngine, apply_move, evaluate as engine_evaluate, legal_moves, preferred_engine_path  # noqa: E402
from annotate_positions import annotate_position, configure_engine  # noqa: E402


@dataclass(frozen=True, slots=True)
class HoldoutRecord:
    fen: str
    teacher_cp: int


@dataclass(frozen=True, slots=True)
class SanityRecord:
    label: str
    category: str
    fen: str


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


class TeacherSession:
    def __init__(self, engine_path: Path, hash_mb: int, extra_options: list[str]) -> None:
        self._engine = UciEngine(engine_path)
        configure_engine(self._engine, hash_mb, extra_options)

    def evaluate(self, fen: str, nodes: int) -> dict[str, object]:
        annotation = annotate_position(self._engine, fen, None, None, nodes)
        return {
            "score_kind": annotation.score_kind,
            "score_value": annotation.score_value,
            "score_cp": annotation.score_value if annotation.score_kind == "cp" else None,
            "annotated_depth": annotation.depth,
            "annotated_nodes": annotation.nodes if annotation.nodes > 0 else nodes,
            "best_move": annotation.best_move,
            "pv": annotation.pv,
        }

    def close(self) -> None:
        self._engine.quit()


def load_sanity_records(path: Path) -> list[SanityRecord]:
    records: list[SanityRecord] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            raw = json.loads(line)
            records.append(
                SanityRecord(
                    label=str(raw["label"]),
                    category=str(raw["category"]),
                    fen=str(raw["fen"]),
                )
            )
    if not records:
        raise ValueError(f"No sanity-suite records found in {path}.")
    return records


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


def distribution(values: list[int]) -> dict[str, float]:
    if not values:
        return {
            "count": 0,
            "mean": 0.0,
            "stdev": 0.0,
            "min": 0.0,
            "max": 0.0,
            "abs_le_25": 0.0,
            "abs_le_50": 0.0,
            "abs_le_100": 0.0,
            "abs_le_200": 0.0,
            "abs_le_400": 0.0,
            "abs_le_800": 0.0,
        }
    count = len(values)
    mean = sum(values) / count
    variance = sum((value - mean) ** 2 for value in values) / count
    absolute = [abs(value) for value in values]
    return {
        "count": count,
        "mean": mean,
        "stdev": math.sqrt(variance),
        "min": min(values),
        "max": max(values),
        "abs_le_25": sum(1 for value in absolute if value <= 25) / count,
        "abs_le_50": sum(1 for value in absolute if value <= 50) / count,
        "abs_le_100": sum(1 for value in absolute if value <= 100) / count,
        "abs_le_200": sum(1 for value in absolute if value <= 200) / count,
        "abs_le_400": sum(1 for value in absolute if value <= 400) / count,
        "abs_le_800": sum(1 for value in absolute if value <= 800) / count,
    }


def evaluate_modes(engine_path: Path, fen: str, eval_file: Path) -> tuple[int, int]:
    classical = int(engine_evaluate(engine_path, fen, use_nnue=False)["score"])
    nnue = int(engine_evaluate(engine_path, fen, use_nnue=True, eval_file=eval_file)["score"])
    return classical, nnue


def build_sanity_suite_report(
    engine_path: Path,
    eval_file: Path,
    sanity_file: Path,
    teacher_engine: Path | None,
    teacher_nodes: int,
    teacher_hash: int,
    teacher_options: list[str],
    leaf_limit: int,
) -> dict[str, object]:
    teacher_session = TeacherSession(teacher_engine, teacher_hash, teacher_options) if teacher_engine else None
    try:
        records = load_sanity_records(sanity_file)
        suite_rows: list[dict[str, object]] = []
        failing_positions: list[dict[str, object]] = []
        for record in records:
            root_teacher = teacher_session.evaluate(record.fen, teacher_nodes) if teacher_session else None
            root_classical, root_nnue = evaluate_modes(engine_path, record.fen, eval_file)
            leaves: list[dict[str, object]] = []
            for move in sorted(legal_moves(engine_path, record.fen))[:leaf_limit]:
                child_fen = apply_move(engine_path, record.fen, move)
                child_teacher = teacher_session.evaluate(child_fen, teacher_nodes) if teacher_session else None
                child_classical, child_nnue = evaluate_modes(engine_path, child_fen, eval_file)
                leaf_entry = {
                    "move": move,
                    "fen": child_fen,
                    "teacher": child_teacher,
                    "classical_cp": child_classical,
                    "nnue_cp": child_nnue,
                }
                if child_teacher and child_teacher["score_cp"] is not None:
                    leaf_entry["nnue_error"] = child_nnue - int(child_teacher["score_cp"])
                    leaf_entry["classical_error"] = child_classical - int(child_teacher["score_cp"])
                    if abs(int(leaf_entry["nnue_error"])) > abs(int(leaf_entry["classical_error"])):
                        failing_positions.append(
                            {
                                "source": "sanity-leaf",
                                "label": record.label,
                                "move": move,
                                "fen": child_fen,
                                "teacher_cp": child_teacher["score_cp"],
                                "classical_cp": child_classical,
                                "nnue_cp": child_nnue,
                            }
                        )
                leaves.append(leaf_entry)

            row = {
                "label": record.label,
                "category": record.category,
                "fen": record.fen,
                "teacher": root_teacher,
                "classical_cp": root_classical,
                "nnue_cp": root_nnue,
                "leaves": leaves,
            }
            if root_teacher and root_teacher["score_cp"] is not None:
                teacher_cp = int(root_teacher["score_cp"])
                row["classical_error"] = root_classical - teacher_cp
                row["nnue_error"] = root_nnue - teacher_cp
                row["nnue_better_or_equal"] = abs(int(row["nnue_error"])) <= abs(int(row["classical_error"]))
                if not row["nnue_better_or_equal"]:
                    failing_positions.append(
                        {
                            "source": "sanity-root",
                            "label": record.label,
                            "fen": record.fen,
                            "teacher_cp": teacher_cp,
                            "classical_cp": root_classical,
                            "nnue_cp": root_nnue,
                        }
                    )
            suite_rows.append(row)

        return {
            "sanity_file": str(sanity_file),
            "teacher_nodes": teacher_nodes if teacher_engine else 0,
            "records": suite_rows,
            "failing_positions": failing_positions,
        }
    finally:
        if teacher_session is not None:
            teacher_session.close()


def build_holdout_report(
    engine_path: Path,
    eval_file: Path,
    input_path: Path,
    sample_count: int,
    seed: int,
    sign_threshold: int,
) -> dict[str, object]:
    records = reservoir_sample_cp_records(input_path, sample_count, seed)
    classical_metrics = Metrics()
    nnue_metrics = Metrics()
    teacher_scores: list[int] = []
    classical_scores: list[int] = []
    nnue_scores: list[int] = []
    failing_positions: list[dict[str, object]] = []

    for record in records:
        classical_cp, nnue_cp = evaluate_modes(engine_path, record.fen, eval_file)
        teacher_scores.append(record.teacher_cp)
        classical_scores.append(classical_cp)
        nnue_scores.append(nnue_cp)
        classical_metrics.add(record.teacher_cp, classical_cp, sign_threshold)
        nnue_metrics.add(record.teacher_cp, nnue_cp, sign_threshold)
        if abs(nnue_cp - record.teacher_cp) > abs(classical_cp - record.teacher_cp):
            failing_positions.append(
                {
                    "source": "holdout",
                    "fen": record.fen,
                    "teacher_cp": record.teacher_cp,
                    "classical_cp": classical_cp,
                    "nnue_cp": nnue_cp,
                }
            )

    return {
        "input": str(input_path),
        "sample_count": len(records),
        "sign_threshold": sign_threshold,
        "metrics": {
            "classical": classical_metrics.summary(),
            "nnue": nnue_metrics.summary(),
        },
        "distributions": {
            "teacher": distribution(teacher_scores),
            "classical": distribution(classical_scores),
            "nnue": distribution(nnue_scores),
        },
        "failing_positions": failing_positions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare teacher, classical, and NNUE eval on a sanity suite and holdout sample.")
    parser.add_argument("--engine", type=Path, default=preferred_engine_path(), help="Path to the DeadFish executable.")
    parser.add_argument("--eval-file", type=Path, required=True, help="Exported DeadFish NNUE file.")
    parser.add_argument("--teacher-engine", type=Path, help="Optional teacher UCI engine for on-the-fly sanity-suite scores.")
    parser.add_argument("--teacher-nodes", type=int, default=50000, help="Fixed node budget for the teacher engine.")
    parser.add_argument("--teacher-hash", type=int, default=64, help="Hash size in MB for the teacher engine.")
    parser.add_argument("--teacher-option", action="append", default=[], help="Extra teacher UCI option in NAME=VALUE format.")
    parser.add_argument(
        "--sanity-file",
        type=Path,
        default=ROOT / "data" / "nnue_sanity_suite.jsonl",
        help="Fixed sanity-suite file.",
    )
    parser.add_argument("--leaf-limit", type=int, default=4, help="How many legal child positions to inspect per sanity FEN.")
    parser.add_argument("--input", type=Path, help="Optional annotated JSONL file with teacher score_cp labels for holdout reporting.")
    parser.add_argument("--sample-count", type=int, default=200, help="Holdout sample size. Use 0 to inspect all cp-labeled records.")
    parser.add_argument("--seed", type=int, default=1337, help="Sampling seed.")
    parser.add_argument("--sign-threshold", type=int, default=50, help="Minimum |teacher_cp| counted for sign agreement.")
    args = parser.parse_args()

    engine_path = args.engine.resolve()
    eval_file = args.eval_file.resolve()
    sanity_file = args.sanity_file.resolve()
    teacher_engine = args.teacher_engine.resolve() if args.teacher_engine else None
    input_path = args.input.resolve() if args.input else None

    for path, label in ((engine_path, "engine"), (eval_file, "eval file"), (sanity_file, "sanity suite")):
        if not path.exists():
            raise FileNotFoundError(f"{label.capitalize()} not found: {path}")
    if teacher_engine is not None and not teacher_engine.exists():
        raise FileNotFoundError(f"Teacher engine not found: {teacher_engine}")
    if input_path is not None and not input_path.exists():
        raise FileNotFoundError(f"Holdout input not found: {input_path}")

    report = {
        "sanity_suite": build_sanity_suite_report(
            engine_path,
            eval_file,
            sanity_file,
            teacher_engine,
            args.teacher_nodes,
            args.teacher_hash,
            args.teacher_option,
            args.leaf_limit,
        )
    }
    if input_path is not None:
        report["holdout"] = build_holdout_report(
            engine_path,
            eval_file,
            input_path,
            args.sample_count,
            args.seed,
            args.sign_threshold,
        )

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
