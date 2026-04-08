from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


def default_engine_path() -> Path:
    return Path(__file__).resolve().parents[1] / "build" / "deadfish.exe"


def resolve_concurrency(requested: int, games: int) -> int:
    if requested > 0:
        return min(requested, max(1, games))
    cpu_count = os.cpu_count() or 1
    # Each concurrent game runs two single-threaded engine processes.
    auto = max(1, cpu_count // 2)
    return min(auto, max(1, games))


def count_games(path: Path) -> int:
    count = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith("[Event "):
                count += 1
    return count


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
    parser.add_argument(
        "--concurrency",
        type=int,
        default=0,
        help="Cutechess concurrency. Use 0 for auto based on CPU count.",
    )
    parser.add_argument("--tc", default="8+0.08", help="Cutechess time control.")
    parser.add_argument("--hash", type=int, default=64, help="DeadFish hash size in MB.")
    parser.add_argument("--opening-file", type=Path, help="Optional PGN or EPD opening file.")
    parser.add_argument("--opening-format", default="pgn", help="Opening file format.")
    parser.add_argument("--opening-order", default="random", help="Opening order.")
    parser.add_argument("--opening-plies", type=int, default=8, help="Opening plies.")
    parser.add_argument("--append", action="store_true", help="Append to an existing PGN instead of overwriting it.")
    parser.add_argument("--recover", action="store_true", help="Enable cutechess recovery/resume mode.")
    args = parser.parse_args()

    engine_path = args.engine.resolve()
    if not engine_path.exists():
        raise FileNotFoundError(f"Engine executable not found: {engine_path}")

    output_pgn = args.output_pgn.resolve()
    output_pgn.parent.mkdir(parents=True, exist_ok=True)
    if output_pgn.exists() and not args.append:
        output_pgn.unlink()
    concurrency = resolve_concurrency(args.concurrency, args.games)
    print(f"Using cutechess concurrency {concurrency} for {args.games} games.")

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
        "-concurrency",
        str(concurrency),
        "-pgnout",
        str(output_pgn),
    ]
    if args.recover:
        command.append("-recover")

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
    print(f"Self-play summary: games={count_games(output_pgn)} append={'yes' if args.append else 'no'} recover={'yes' if args.recover else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
