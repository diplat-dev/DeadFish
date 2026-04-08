from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path


def has_dependency(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def main() -> int:
    if not has_dependency("torch") or not has_dependency("chess") or not has_dependency("numpy"):
        print("Skipping NNUE smoke test because torch, numpy, or python-chess is not installed.")
        return 0

    import torch
    from torch.utils.data import DataLoader

    from deadfish_nnue import DeadFishNNUE, JsonlPositionDataset, NetworkConfig, collate_records, export_model, load_jsonl_records
    from deadfish_nnue.export import read_export

    sample_records = [
        {"fen": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1", "score_cp": 30},
        {"fen": "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2", "score_cp": 10},
        {"fen": "4k3/8/8/8/3r4/4Q3/8/4K3 w - - 0 1", "score_cp": 850},
        {"fen": "7k/P7/8/8/8/8/8/K7 w - - 0 1", "score_cp": 900},
    ]

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        dataset_path = tmp_path / "sample.jsonl"
        dataset_path.write_text("".join(json.dumps(record) + "\n" for record in sample_records), encoding="utf-8")

        records = load_jsonl_records(dataset_path, clip_cp=1200.0)
        config = NetworkConfig(accumulator_size=16, hidden_size=8, output_scale=1200.0)
        model = DeadFishNNUE(config)
        loader = DataLoader(JsonlPositionDataset(records), batch_size=2, shuffle=False, collate_fn=collate_records)
        optimizer = torch.optim.AdamW(model.parameters(), lr=5e-3)

        model.train()
        for batch in loader:
            optimizer.zero_grad(set_to_none=True)
            predictions = model(
                batch["white_indices"],
                batch["white_offsets"],
                batch["black_indices"],
                batch["black_offsets"],
                batch["stm_is_white"],
            )
            loss = ((predictions - batch["targets"]) ** 2).mean()
            loss.backward()
            optimizer.step()

        export_path = tmp_path / "sample.nnue"
        metadata = export_model(export_path, model, config)
        read_back, tensors = read_export(export_path)
        assert metadata.feature_count == read_back.feature_count
        assert tensors["feature_weights"].shape == (config.feature_count, config.accumulator_size)
        assert tensors["hidden_weight"].shape == (config.hidden_size, config.accumulator_size * 2)
        assert tensors["output_weight"].shape == (1, config.hidden_size)

    print("NNUE smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
