from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from _uci import default_engine_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DeadFish through a cutechess-based external gauntlet.")
    parser.add_argument("--engine", type=Path, default=default_engine_path(), help="Path to the engine under test.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "external_gauntlet.example.json",
        help="JSON config describing cutechess settings and opponents.",
    )
    args = parser.parse_args()

    engine_path = args.engine.resolve()
    config_path = args.config.resolve()
    if not engine_path.exists():
        raise FileNotFoundError(f"Engine executable not found: {engine_path}")
    if not config_path.exists():
        raise FileNotFoundError(f"Gauntlet config not found: {config_path}")

    config = json.loads(config_path.read_text(encoding="utf-8"))
    cutechess = config.get("cutechess", "cutechess-cli")
    games = int(config.get("games", 20))
    concurrency = int(config.get("concurrency", 1))
    tc = str(config.get("tc", "8+0.08"))
    engine_options = list(config.get("engine_options", []))
    opening_file = str(config.get("opening_file", "")).strip()
    opening_format = str(config.get("opening_format", "pgn"))
    opening_order = str(config.get("opening_order", "random"))
    opening_plies = int(config.get("opening_plies", 8))

    for opponent in config.get("opponents", []):
        opponent_name = opponent["name"]
        opponent_cmd = Path(opponent["cmd"]).expanduser()
        if not opponent_cmd.exists():
            raise FileNotFoundError(f"Opponent executable not found: {opponent_cmd}")

        command = [
            "python",
            str(Path(__file__).resolve().parent / "cutechess_match.py"),
            "--cutechess",
            cutechess,
            "--engine-a",
            str(engine_path),
            "--engine-b",
            str(opponent_cmd),
            "--name-a",
            "DeadFish",
            "--name-b",
            opponent_name,
            "--games",
            str(games),
            "--concurrency",
            str(concurrency),
            "--tc",
            tc,
        ]
        for option in engine_options:
            command.extend(["--option-a", option])
        for option in opponent.get("options", []):
            command.extend(["--option-b", option])
        if opening_file:
            command.extend(
                [
                    "--opening-file",
                    opening_file,
                    "--opening-format",
                    opening_format,
                    "--opening-order",
                    opening_order,
                    "--opening-plies",
                    str(opening_plies),
                ]
            )

        print(f"\n=== Versus {opponent_name} ===")
        subprocess.run(command, check=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
