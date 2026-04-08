from __future__ import annotations

import argparse
import json
import random
from dataclasses import asdict
from pathlib import Path

try:
    import torch
    from torch.utils.data import DataLoader
except ImportError as exc:  # pragma: no cover - runtime dependency guard
    raise SystemExit(
        "PyTorch is required for the DeadFish NNUE training tools. "
        "Install training/requirements.txt first."
    ) from exc

from deadfish_nnue import DeadFishNNUE, JsonlPositionDataset, LoadStats, NetworkConfig, collate_records, load_jsonl_records


def resolve_device(name: str) -> str:
    if name != "auto":
        return name
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def build_loader(records, batch_size: int, shuffle: bool) -> DataLoader:
    return DataLoader(
        JsonlPositionDataset(records),
        batch_size=batch_size,
        shuffle=shuffle,
        num_workers=0,
        collate_fn=collate_records,
    )


def move_batch_to_device(batch: dict[str, torch.Tensor], device: str) -> dict[str, torch.Tensor]:
    return {key: value.to(device) for key, value in batch.items()}


def batch_loss(model: DeadFishNNUE, batch: dict[str, torch.Tensor]) -> torch.Tensor:
    predictions = model(
        batch["white_indices"],
        batch["white_offsets"],
        batch["black_indices"],
        batch["black_offsets"],
        batch["stm_is_white"],
    )
    errors = (predictions - batch["targets"]) ** 2
    weighted = errors * batch["weights"]
    return weighted.mean()


def evaluate(model: DeadFishNNUE, loader: DataLoader, device: str) -> float:
    model.eval()
    total_loss = 0.0
    total_batches = 0
    with torch.no_grad():
        for batch in loader:
            loss = batch_loss(model, move_batch_to_device(batch, device))
            total_loss += float(loss.item())
            total_batches += 1
    return total_loss / max(1, total_batches)


def checkpoint_safe_value(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): checkpoint_safe_value(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [checkpoint_safe_value(item) for item in value]
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Train a first-pass DeadFish HalfKP NNUE checkpoint.")
    parser.add_argument("--input", type=Path, required=True, help="Input JSONL dataset.")
    parser.add_argument("--validation", type=Path, help="Optional validation JSONL dataset.")
    parser.add_argument("--max-positions", type=int, default=0, help="Optional cap on loaded positions.")
    parser.add_argument("--clip-cp", type=float, default=1200.0, help="Centipawn clip used for score normalization.")
    parser.add_argument("--validation-split", type=float, default=0.05, help="Validation split when no separate file is provided.")
    parser.add_argument("--batch-size", type=int, default=256, help="Training batch size.")
    parser.add_argument("--epochs", type=int, default=4, help="Number of training epochs.")
    parser.add_argument("--learning-rate", type=float, default=1e-3, help="AdamW learning rate.")
    parser.add_argument("--weight-decay", type=float, default=1e-5, help="AdamW weight decay.")
    parser.add_argument("--accumulator-size", type=int, default=128, help="HalfKP accumulator width.")
    parser.add_argument("--hidden-size", type=int, default=32, help="Post-accumulator hidden width.")
    parser.add_argument(
        "--output-scale",
        type=float,
        default=None,
        help="Centipawn scale associated with the network output. Defaults to the clip-cp value.",
    )
    parser.add_argument("--device", default="auto", help="Training device: auto, cpu, or cuda.")
    parser.add_argument("--seed", type=int, default=1337, help="Random seed for shuffling and splits.")
    parser.add_argument(
        "--output-checkpoint",
        type=Path,
        default=Path(__file__).resolve().parent / "checkpoints" / "deadfish_nnue.pt",
        help="Checkpoint output path.",
    )
    args = parser.parse_args()

    if hasattr(torch, "set_float32_matmul_precision"):
        torch.set_float32_matmul_precision("high")

    random.seed(args.seed)
    torch.manual_seed(args.seed)

    input_path = args.input.resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Training dataset not found: {input_path}")

    load_stats = LoadStats()
    all_records = load_jsonl_records(input_path, max_positions=args.max_positions, clip_cp=args.clip_cp, stats=load_stats)
    rng = random.Random(args.seed)
    rng.shuffle(all_records)

    if args.validation:
        validation_records = load_jsonl_records(args.validation.resolve(), clip_cp=args.clip_cp)
        training_records = all_records
    else:
        if len(all_records) < 2:
            raise ValueError("Automatic validation splitting requires at least two records.")
        validation_size = int(len(all_records) * args.validation_split)
        validation_size = min(max(validation_size, 1), max(1, len(all_records) - 1))
        validation_records = all_records[:validation_size]
        training_records = all_records[validation_size:]

    device = resolve_device(args.device)
    effective_output_scale = args.output_scale if args.output_scale is not None else args.clip_cp
    config = NetworkConfig(
        accumulator_size=args.accumulator_size,
        hidden_size=args.hidden_size,
        output_scale=effective_output_scale,
    )
    model = DeadFishNNUE(config).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)

    train_loader = build_loader(training_records, args.batch_size, shuffle=True)
    validation_loader = build_loader(validation_records, args.batch_size, shuffle=False)

    best_validation = float("inf")
    best_state = None
    train_loss = float("inf")
    for epoch in range(1, args.epochs + 1):
        model.train()
        total_loss = 0.0
        total_batches = 0
        for batch in train_loader:
            batch = move_batch_to_device(batch, device)
            optimizer.zero_grad(set_to_none=True)
            loss = batch_loss(model, batch)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            total_loss += float(loss.item())
            total_batches += 1

        train_loss = total_loss / max(1, total_batches)
        validation_loss = evaluate(model, validation_loader, device)
        print(
            f"epoch {epoch}: train_loss={train_loss:.6f} "
            f"validation_loss={validation_loss:.6f} "
            f"train_records={len(training_records)} validation_records={len(validation_records)}"
        )
        if validation_loss <= best_validation:
            best_validation = validation_loss
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}

    checkpoint = {
        "config": asdict(config),
        "state_dict": best_state if best_state is not None else model.state_dict(),
        "train_loss": train_loss,
        "validation_loss": best_validation,
        "record_count": len(all_records),
        "load_stats": load_stats.to_dict(),
        "training_args": checkpoint_safe_value(vars(args)),
    }
    output_checkpoint = args.output_checkpoint.resolve()
    output_checkpoint.parent.mkdir(parents=True, exist_ok=True)
    torch.save(checkpoint, output_checkpoint)
    print(
        json.dumps(
            {
                "checkpoint": str(output_checkpoint),
                "validation_loss": best_validation,
                "train_loss": train_loss,
                "output_scale": effective_output_scale,
                "record_count": len(all_records),
                "load_stats": load_stats.to_dict(),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
