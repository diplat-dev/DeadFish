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

    from deadfish_nnue import (
        DeadFishNNUE,
        JsonlPositionDataset,
        LoadStats,
        NetworkConfig,
        collate_records,
        evaluate_backbone_fen,
        export_model,
        load_jsonl_records,
    )
    from deadfish_nnue.export import read_export
    from train_nnue import split_records_by_game

    sample_records = [
        {"fen": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1", "score_kind": "cp", "score_cp": 30, "game_index": 1},
        {"fen": "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2", "score_kind": "cp", "score_cp": 10, "game_index": 1},
        {"fen": "4k3/8/8/8/3r4/4Q3/8/4K3 w - - 0 1", "score_kind": "cp", "score_cp": 850, "game_index": 2},
        {"fen": "7k/P7/8/8/8/8/8/K7 w - - 0 1", "score_kind": "cp", "score_cp": 900, "game_index": 3},
        {"fen": "Q7/7R/8/8/2K2k2/8/8/8 w - - 1 74", "score_kind": "mate", "score_value": 3, "score_cp": None, "game_index": 4},
        {"fen": "8/8/8/8/8/8/5k2/6K1 w - - 0 1", "outcome": 0.0, "game_index": 5},
    ]

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        dataset_path = tmp_path / "sample.jsonl"
        dataset_path.write_text("".join(json.dumps(record) + "\n" for record in sample_records), encoding="utf-8")

        stats = LoadStats()
        records = load_jsonl_records(dataset_path, clip_cp=1200.0, target_mode="teacher-cp", stats=stats)
        assert len(records) == 4
        assert stats.score_cp_records == 4
        assert stats.mate_records == 1
        assert stats.skipped_mate_records == 1
        assert stats.non_cp_records == 1
        assert stats.skipped_non_cp_records == 1
        training_records, validation_records = split_records_by_game(records, validation_split=0.25, seed=1337)
        training_games = {record.game_index for record in training_records}
        validation_games = {record.game_index for record in validation_records}
        assert training_games.isdisjoint(validation_games)
        assert training_games
        assert validation_games

        residual_stats = LoadStats()
        residual_records = load_jsonl_records(dataset_path, clip_cp=1200.0, target_mode="classical-residual", stats=residual_stats)
        assert len(residual_records) == 4
        expected_residual = (30.0 - evaluate_backbone_fen(sample_records[0]["fen"])) / 1200.0
        assert abs(residual_records[0].target - expected_residual) < 1e-6

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
