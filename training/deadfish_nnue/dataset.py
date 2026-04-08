from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    import torch
    from torch.utils.data import Dataset
except ImportError as exc:  # pragma: no cover - runtime dependency guard
    raise SystemExit(
        "PyTorch is required for the DeadFish NNUE training tools. "
        "Install training/requirements.txt first."
    ) from exc

from .features import encode_fen


@dataclass(frozen=True, slots=True)
class PositionRecord:
    fen: str
    target: float
    weight: float
    raw: dict[str, object]
    game_index: int | None = None


@dataclass(slots=True)
class LoadStats:
    raw_records: int = 0
    loaded_records: int = 0
    score_cp_records: int = 0
    outcome_records: int = 0
    mate_records: int = 0
    skipped_mate_records: int = 0
    non_cp_records: int = 0
    skipped_non_cp_records: int = 0
    clipped_records: int = 0
    abs_le_25: int = 0
    abs_le_50: int = 0
    abs_le_100: int = 0
    abs_le_200: int = 0
    abs_le_400: int = 0
    abs_le_800: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def _bucket_score(stats: LoadStats, score_cp: float) -> None:
    absolute = abs(score_cp)
    if absolute <= 25:
        stats.abs_le_25 += 1
    if absolute <= 50:
        stats.abs_le_50 += 1
    if absolute <= 100:
        stats.abs_le_100 += 1
    if absolute <= 200:
        stats.abs_le_200 += 1
    if absolute <= 400:
        stats.abs_le_400 += 1
    if absolute <= 800:
        stats.abs_le_800 += 1


def _normalized_target(
    record: dict[str, object],
    clip_cp: float,
    *,
    target_mode: str = "score-or-outcome",
    stats: LoadStats | None = None,
) -> float:
    score_kind = record.get("score_kind")
    if score_kind == "mate":
        if stats is not None:
            stats.mate_records += 1
        raise ValueError("mate-score records are excluded from score-supervised training by default")
    if "score_cp" in record and record["score_cp"] is not None:
        score_cp = float(record["score_cp"])
        if stats is not None:
            stats.score_cp_records += 1
            _bucket_score(stats, score_cp)
            if abs(score_cp) > clip_cp:
                stats.clipped_records += 1
        score_cp = max(-clip_cp, min(clip_cp, score_cp))
        return score_cp / clip_cp
    if target_mode == "teacher-cp":
        if stats is not None:
            stats.non_cp_records += 1
            if record.get("outcome") is not None:
                stats.outcome_records += 1
        raise ValueError("teacher-cp mode requires score_cp")
    if "wdl" in record and record["wdl"] is not None:
        value = float(record["wdl"])
        return max(-1.0, min(1.0, value * 2.0 - 1.0))
    if "outcome" in record and record["outcome"] is not None:
        if stats is not None:
            stats.outcome_records += 1
        return max(-1.0, min(1.0, float(record["outcome"])))
    raise ValueError("Record must include score_cp, wdl, or outcome.")


def load_jsonl_records(
    path: Path,
    max_positions: int = 0,
    clip_cp: float = 1200.0,
    target_mode: str = "score-or-outcome",
    stats: LoadStats | None = None,
) -> list[PositionRecord]:
    records: list[PositionRecord] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            if stats is not None:
                stats.raw_records += 1
            raw = json.loads(line)
            fen = str(raw["fen"])
            try:
                target = _normalized_target(raw, clip_cp, target_mode=target_mode, stats=stats)
            except ValueError as exc:
                message = str(exc)
                if "mate-score records" in message:
                    if stats is not None:
                        stats.skipped_mate_records += 1
                    continue
                if "teacher-cp mode requires score_cp" in message:
                    if stats is not None:
                        stats.skipped_non_cp_records += 1
                    continue
                raise
            weight = float(raw.get("weight", 1.0))
            game_index_raw = raw.get("game_index")
            game_index: int | None = None
            if isinstance(game_index_raw, int):
                game_index = game_index_raw
            elif isinstance(game_index_raw, float) and game_index_raw.is_integer():
                game_index = int(game_index_raw)
            records.append(PositionRecord(fen=fen, target=target, weight=weight, raw=raw, game_index=game_index))
            if stats is not None:
                stats.loaded_records += 1
            if max_positions > 0 and len(records) >= max_positions:
                break
    if not records:
        raise ValueError(f"No training positions were loaded from {path}.")
    return records


class JsonlPositionDataset(Dataset[PositionRecord]):
    def __init__(self, records: list[PositionRecord]) -> None:
        self._records = records

    def __len__(self) -> int:
        return len(self._records)

    def __getitem__(self, index: int) -> PositionRecord:
        return self._records[index]


def collate_records(records: list[PositionRecord]) -> dict[str, torch.Tensor]:
    white_indices_flat: list[int] = []
    black_indices_flat: list[int] = []
    white_offsets: list[int] = []
    black_offsets: list[int] = []
    stm_flags: list[bool] = []
    targets: list[float] = []
    weights: list[float] = []

    for record in records:
        encoded = encode_fen(record.fen)
        white_offsets.append(len(white_indices_flat))
        black_offsets.append(len(black_indices_flat))
        white_indices_flat.extend(encoded.white_indices)
        black_indices_flat.extend(encoded.black_indices)
        stm_flags.append(encoded.stm_is_white)
        targets.append(record.target)
        weights.append(record.weight)

    return {
        "white_indices": torch.tensor(white_indices_flat, dtype=torch.long),
        "white_offsets": torch.tensor(white_offsets, dtype=torch.long),
        "black_indices": torch.tensor(black_indices_flat, dtype=torch.long),
        "black_offsets": torch.tensor(black_offsets, dtype=torch.long),
        "stm_is_white": torch.tensor(stm_flags, dtype=torch.bool),
        "targets": torch.tensor(targets, dtype=torch.float32),
        "weights": torch.tensor(weights, dtype=torch.float32),
    }
