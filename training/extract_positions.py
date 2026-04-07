from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    import chess.pgn
except ImportError as exc:  # pragma: no cover - runtime dependency guard
    raise SystemExit(
        "python-chess is required for the DeadFish NNUE training tools. "
        "Install training/requirements.txt first."
    ) from exc


def result_to_outcome(result: str) -> float | None:
    if result == "1-0":
        return 1.0
    if result == "0-1":
        return -1.0
    if result == "1/2-1/2":
        return 0.0
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract sampled NNUE training positions from PGN games.")
    parser.add_argument("--input-pgn", type=Path, required=True, help="Input PGN path.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "output" / "positions.jsonl",
        help="Output JSONL path.",
    )
    parser.add_argument("--sample-every", type=int, default=2, help="Sample every N plies after the opening skip.")
    parser.add_argument("--skip-opening-plies", type=int, default=8, help="Ignore the first N plies of each game.")
    parser.add_argument("--max-games", type=int, default=0, help="Optional maximum number of games to process.")
    parser.add_argument("--max-positions", type=int, default=0, help="Optional maximum number of positions to write.")
    args = parser.parse_args()

    input_pgn = args.input_pgn.resolve()
    if not input_pgn.exists():
        raise FileNotFoundError(f"PGN not found: {input_pgn}")

    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    processed_games = 0
    with input_pgn.open("r", encoding="utf-8", errors="replace") as handle, output_path.open(
        "w", encoding="utf-8"
    ) as out_handle:
        while True:
            game = chess.pgn.read_game(handle)
            if game is None:
                break
            if args.max_games > 0 and processed_games >= args.max_games:
                break
            processed_games += 1
            outcome = result_to_outcome(game.headers.get("Result", "*"))
            if outcome is None:
                continue

            board = game.board()
            for ply, move in enumerate(game.mainline_moves(), start=1):
                board.push(move)
                if ply <= args.skip_opening_plies:
                    continue
                if args.sample_every > 1 and (ply - args.skip_opening_plies) % args.sample_every != 0:
                    continue
                if board.is_game_over(claim_draw=True):
                    continue

                record = {
                    "fen": board.fen(en_passant="fen"),
                    "outcome": outcome,
                    "ply": ply,
                    "game_index": processed_games,
                    "result": game.headers.get("Result", "*"),
                }
                out_handle.write(json.dumps(record) + "\n")
                written += 1
                if args.max_positions > 0 and written >= args.max_positions:
                    print(f"Wrote {written} positions from {processed_games} games to {output_path}")
                    return 0

    print(f"Wrote {written} positions from {processed_games} games to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
