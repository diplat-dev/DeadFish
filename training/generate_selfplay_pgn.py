from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def default_engine_path() -> Path:
    return Path(__file__).resolve().parents[1] / "build" / "deadfish.exe"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate DeadFish self-play PGNs through cutechess-cli.")
    parser.add_argument("--cutechess", default="cutechess-cli", help="cutechess-cli executable.")
    parser.add_argument("--engine", type=Path, default=default_engine_path(), help="Path to the DeadFish executable.")
    parser.add_argument(
        "--output-pgn",
        type=Path,
        default=Path(__file__).resolve().parent / "output" / "selfplay.pgn",
        help="PGN output path.",
    )
    parser.add_argument("--games", type=int, default=50, help="Number of games to generate.")
    parser.add_argument("--concurrency", type=int, default=1, help="Cutechess concurrency.")
    parser.add_argument("--tc", default="8+0.08", help="Cutechess time control.")
    parser.add_argument("--hash", type=int, default=64, help="DeadFish hash size in MB.")
    parser.add_argument("--opening-file", type=Path, help="Optional PGN or EPD opening file.")
    parser.add_argument("--opening-format", default="pgn", help="Opening file format.")
    parser.add_argument("--opening-order", default="random", help="Opening order.")
    parser.add_argument("--opening-plies", type=int, default=8, help="Opening plies.")
    args = parser.parse_args()

    engine_path = args.engine.resolve()
    if not engine_path.exists():
        raise FileNotFoundError(f"Engine executable not found: {engine_path}")

    output_pgn = args.output_pgn.resolve()
    output_pgn.parent.mkdir(parents=True, exist_ok=True)

    command = [
        args.cutechess,
        "-engine",
        "name=DeadFish-A",
        f"cmd={engine_path}",
        f"option.Hash={args.hash}",
        "option.OwnBook=false",
        "-engine",
        "name=DeadFish-B",
        f"cmd={engine_path}",
        f"option.Hash={args.hash}",
        "option.OwnBook=false",
        "-each",
        "proto=uci",
        f"tc={args.tc}",
        "-games",
        str(args.games),
        "-repeat",
        "-recover",
        "-concurrency",
        str(args.concurrency),
        "-pgnout",
        str(output_pgn),
    ]

    if args.opening_file:
        opening_file = args.opening_file.resolve()
        if not opening_file.exists():
            raise FileNotFoundError(f"Opening file not found: {opening_file}")
        command.extend(
            [
                "-openings",
                f"file={opening_file}",
                f"format={args.opening_format}",
                f"order={args.opening_order}",
                f"plies={args.opening_plies}",
            ]
        )

    subprocess.run(command, check=True)
    print(f"Wrote self-play PGN to {output_pgn}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
