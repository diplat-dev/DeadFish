from __future__ import annotations

import argparse
import pickle
from pathlib import Path

try:
    import torch
except ImportError as exc:  # pragma: no cover - runtime dependency guard
    raise SystemExit(
        "PyTorch is required for the DeadFish NNUE training tools. "
        "Install training/requirements.txt first."
    ) from exc

from deadfish_nnue import export_model
from deadfish_nnue.export import checkpoint_to_model, read_export, write_metadata_json


def load_checkpoint(path: Path) -> dict[str, object]:
    try:
        return torch.load(path, map_location="cpu")
    except pickle.UnpicklingError as exc:
        message = str(exc)
        if "Weights only load failed" not in message:
            raise
        return torch.load(path, map_location="cpu", weights_only=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a DeadFish NNUE checkpoint to a custom binary format.")
    parser.add_argument("--checkpoint", type=Path, required=True, help="Input PyTorch checkpoint.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "output" / "deadfish.nnue",
        help="Output NNUE path.",
    )
    parser.add_argument(
        "--metadata-json",
        type=Path,
        help="Optional metadata JSON output path. Defaults to <output>.json when --write-metadata is used.",
    )
    parser.add_argument("--write-metadata", action="store_true", help="Write a JSON metadata sidecar.")
    parser.add_argument("--inspect", action="store_true", help="Read the export back and validate its tensor shapes.")
    args = parser.parse_args()

    checkpoint_path = args.checkpoint.resolve()
    if not checkpoint_path.exists():
        raise FileNotFoundError(f"Checkpoint not found: {checkpoint_path}")

    checkpoint = load_checkpoint(checkpoint_path)
    model, config = checkpoint_to_model(checkpoint)
    output_path = args.output.resolve()
    metadata = export_model(output_path, model, config)
    print(f"Exported NNUE weights to {output_path}")

    if args.write_metadata:
        metadata_path = args.metadata_json.resolve() if args.metadata_json else output_path.with_suffix(output_path.suffix + ".json")
        write_metadata_json(metadata_path, metadata)
        print(f"Wrote metadata to {metadata_path}")

    if args.inspect:
        loaded_metadata, tensors = read_export(output_path)
        print(
            f"Inspected {output_path}: "
            f"feature_weights={tensors['feature_weights'].shape} "
            f"hidden_weight={tensors['hidden_weight'].shape} "
            f"output_scale={loaded_metadata.output_scale}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
