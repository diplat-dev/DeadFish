from __future__ import annotations

import json
from dataclasses import dataclass
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


def _normalized_target(record: dict[str, object], clip_cp: float) -> float:
    if "score_cp" in record and record["score_cp"] is not None:
        score_cp = float(record["score_cp"])
        score_cp = max(-clip_cp, min(clip_cp, score_cp))
        return score_cp / clip_cp
    if "wdl" in record and record["wdl"] is not None:
        value = float(record["wdl"])
        return max(-1.0, min(1.0, value * 2.0 - 1.0))
    if "outcome" in record and record["outcome"] is not None:
        return max(-1.0, min(1.0, float(record["outcome"])))
    raise ValueError("Record must include score_cp, wdl, or outcome.")


def load_jsonl_records(path: Path, max_positions: int = 0, clip_cp: float = 1200.0) -> list[PositionRecord]:
    records: list[PositionRecord] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            raw = json.loads(line)
            fen = str(raw["fen"])
            target = _normalized_target(raw, clip_cp)
            weight = float(raw.get("weight", 1.0))
            records.append(PositionRecord(fen=fen, target=target, weight=weight, raw=raw))
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
