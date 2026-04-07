from __future__ import annotations

import json
import math
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    import numpy as np
    import torch
except ImportError as exc:  # pragma: no cover - runtime dependency guard
    raise SystemExit(
        "NumPy and PyTorch are required for the DeadFish NNUE training tools. "
        "Install training/requirements.txt first."
    ) from exc

from .model import DeadFishNNUE, NetworkConfig


MAGIC = b"DFNNUE1\x00"
HEADER = struct.Struct("<8sIIIf")


@dataclass(frozen=True, slots=True)
class ExportMetadata:
    feature_count: int
    accumulator_size: int
    hidden_size: int
    output_scale: float

    def tensor_shapes(self) -> dict[str, tuple[int, ...]]:
        return {
            "feature_weights": (self.feature_count, self.accumulator_size),
            "acc_bias": (self.accumulator_size,),
            "hidden_weight": (self.hidden_size, self.accumulator_size * 2),
            "hidden_bias": (self.hidden_size,),
            "output_weight": (1, self.hidden_size),
            "output_bias": (1,),
        }


def checkpoint_to_model(checkpoint: dict[str, object]) -> tuple[DeadFishNNUE, NetworkConfig]:
    config = NetworkConfig(**dict(checkpoint["config"]))
    model = DeadFishNNUE(config)
    model.load_state_dict(dict(checkpoint["state_dict"]))
    model.eval()
    return model, config


def _write_tensor(handle, tensor: torch.Tensor) -> None:
    array = tensor.detach().cpu().to(torch.float32).contiguous().numpy().astype("<f4", copy=False)
    handle.write(array.tobytes(order="C"))


def export_model(path: Path, model: DeadFishNNUE, config: NetworkConfig) -> ExportMetadata:
    metadata = ExportMetadata(
        feature_count=config.feature_count,
        accumulator_size=config.accumulator_size,
        hidden_size=config.hidden_size,
        output_scale=config.output_scale,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(
            HEADER.pack(
                MAGIC,
                metadata.feature_count,
                metadata.accumulator_size,
                metadata.hidden_size,
                metadata.output_scale,
            )
        )
        _write_tensor(handle, model.feature_weights.weight)
        _write_tensor(handle, model.acc_bias)
        _write_tensor(handle, model.hidden.weight)
        _write_tensor(handle, model.hidden.bias)
        _write_tensor(handle, model.output.weight)
        _write_tensor(handle, model.output.bias)
    return metadata


def write_metadata_json(path: Path, metadata: ExportMetadata) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(metadata), indent=2) + "\n", encoding="utf-8")


def read_export(path: Path) -> tuple[ExportMetadata, dict[str, np.ndarray]]:
    with path.open("rb") as handle:
        header = handle.read(HEADER.size)
        if len(header) != HEADER.size:
            raise ValueError(f"{path} is too small to be a DeadFish NNUE export.")
        magic, feature_count, accumulator_size, hidden_size, output_scale = HEADER.unpack(header)
        if magic != MAGIC:
            raise ValueError(f"{path} does not have the expected DeadFish NNUE header.")
        metadata = ExportMetadata(
            feature_count=feature_count,
            accumulator_size=accumulator_size,
            hidden_size=hidden_size,
            output_scale=output_scale,
        )
        tensors: dict[str, np.ndarray] = {}
        for name, shape in metadata.tensor_shapes().items():
            count = math.prod(shape)
            raw = handle.read(count * 4)
            if len(raw) != count * 4:
                raise ValueError(f"{path} ended unexpectedly while reading {name}.")
            tensors[name] = np.frombuffer(raw, dtype="<f4").reshape(shape)
        if handle.read(1):
            raise ValueError(f"{path} contains trailing bytes after the final tensor.")
        return metadata, tensors
